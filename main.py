import sys
import os
import eyed3
import requests
import json
import sqlite3
from io import BytesIO
#from functools import partial
from PySide6.QtWidgets import (QApplication, QMainWindow, QLabel, QLineEdit, QTextEdit, QVBoxLayout, QHBoxLayout, 
	QWidget, QListWidget, QListWidgetItem, QPushButton, QStyle, QDialog, QProgressBar, QFileDialog,
	QTableWidget, QTableWidgetItem, QMessageBox, QSplitter, QCompleter)
from PySide6.QtCore import (Qt, QUrl, QByteArray, QItemSelectionModel, QThread, Signal, QSettings, QCoreApplication, 
	QTimer, QStringListModel)
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QPixmap, QPalette, QColor, QPixmap, QPainter, QBrush
from mutagen import File
from mutagen.mp4 import MP4, MP4Cover
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TALB, TIT2, TPE1, TPE2, TCOM, TCON, TXXX, ID3NoHeaderError, error

from conv import merge_mp3_files

def get_file_path(file_name):
	if getattr(sys, 'frozen', False):  # Проверка, запущено ли приложение из исполняемого файла
		base_path = sys._MEIPASS  # Получение пути к папке с ресурсами
	else:
		base_path = os.path.abspath(os.path.dirname(__file__))  # Получение текущего пути к скрипту
		
	path = os.path.join(base_path, file_name)
	return path

db_path = get_file_path('db.sqlite')
tag_png_path = get_file_path('tag.png')
play_png_path = get_file_path('play.png')
close_png_path = get_file_path('close.png')

class CustomTextEdit(QTextEdit): # определени фокуса в поле Описание для сохраения данных в БД
	focusOut = Signal()  # Создаем новый сигнал
	
	def __init__(self, parent=None):
		super().__init__(parent)
		
	def focusOutEvent(self, event):
		super().focusOutEvent(event)
		self.focusOut.emit()  # Испускаем сигнал при потере фокуса

class AudioBooksBuilder(QMainWindow):
	def __init__(self, default_tag_status='', parent=None):
		super().__init__(parent)
		
		self.setWindowTitle("AudioBooksBuilder")
		# Установка ключа для сохранения состояния окна
		self.settings = QSettings("YCompany", "AudioBooksBuilder")
		
		# Загрузка предыдущей позиции окна
		window_geometry = self.settings.value("window_geometry")
		if window_geometry is not None:
			self.restoreGeometry(window_geometry)
		else:
			self.setGeometry(100, 100, 1000, 500)
		
		self.default_tag_status = default_tag_status # Статус обработки книг
		self.workers = []  # Инициализация списка для хранения потоков
		
		
		# Содение базы данных 
		self.connection = sqlite3.connect(db_path)
		self.cursor = self.connection.cursor()
		self.cursor.execute("PRAGMA auto_vacuum = FULL;")
		self.connection.commit()
		
		# Создание таблицы, если она не существует
		self.cursor.execute('''CREATE TABLE IF NOT EXISTS books
								("id" integer PRIMARY KEY AUTOINCREMENT, status text, name text, artist text, album text, composer text, genre text, series text, series_num integer, track_num integer, description text, dir_name text, input_folder text, output_folder text, cover blob)''')
		self.cursor.execute('''CREATE TABLE IF NOT EXISTS "authors" ("name" text NOT NULL)''')
		self.cursor.execute('''CREATE TABLE IF NOT EXISTS "composer" ("name" text NOT NULL)''')
		self.connection.commit()
		
		# Главный компоновщик
		main_layout = QHBoxLayout()
		
		# Создаем QSplitter с горизонтальным расположением
		splitter = QSplitter(Qt.Horizontal)
		
		# Левая часть компоновки (теперь будет добавляться в splitter)
		left_widget = QWidget() # Создаем виджет для левой части
		left_layout = QVBoxLayout(left_widget)
		
		left_widget.setMinimumWidth(373)  # Устанавливает минимальную ширину
		left_widget.setMaximumWidth(373)  # Устанавливает максимальную ширину
		
		# Метка для отображения обложки аудиокниги
		self.cover_label = QLabel('Обложка')
		self.cover_label.setFixedSize(200, 200)  # Настроить размер для отображения обложки
		self.cover_label.setFixedSize(355, 355)
		self.cover_label.setStyleSheet("border: 1px solid black;")
		self.cover_label.setAlignment(Qt.AlignCenter)
		left_layout.addWidget(self.cover_label)
		
		#выделение обложки
		self.cover_label.mousePressEvent = self.on_cover_label_clicked
		self.cover_label.setFocusPolicy(Qt.ClickFocus)
		self.cover_label.keyPressEvent = self.on_cover_label_key_pressed
		
		# Компоновщик для формы с метаданными
		form_layout = QVBoxLayout()
		labels_texts = ['Название', 'Автор', 'Книга', 'Читает', 'Жанр', 'Цикл', 'Книга в цикле']
		#self.line_edits = {text: QLineEdit() for text in labels_texts + ['Книга в цикле', 'Трек №']}
		self.line_edits = {text: QLineEdit() for text in labels_texts}
		
		# Автоподстановка автора и чтеца из базы 
		authors = self.get_authors_from_db()
		composer = self.get_composer_from_db()
		
		# Создаем модель для хранения списка имен
		self.model_authors = QStringListModel(authors)
		self.model_composer = QStringListModel(composer)
		
		# Создаем комплитер с настройкой фильтрации авторов
		self.completer_authors = QCompleter()
		self.completer_authors.setModel(self.model_authors)
		self.completer_authors.setFilterMode(Qt.MatchContains)  # Фильтр для поиска по содержанию
		self.completer_authors.setCompletionMode(QCompleter.PopupCompletion)  # Режим автодополнения
		
		# Создаем комплитер с настройкой фильтрации чтецов
		self.completer_composer = QCompleter()
		self.completer_composer.setModel(self.model_composer)
		self.completer_composer.setFilterMode(Qt.MatchContains)  # Фильтр для поиска по содержанию
		self.completer_composer.setCompletionMode(QCompleter.PopupCompletion)  # Режим автодополнения
		
		# Добавление полей, которые не требуют специального размещения
		for label_text in labels_texts:
			row_layout = QHBoxLayout()
			label = QLabel(label_text)
			line_edit = self.line_edits[label_text]
			if label_text == "Автор":
				line_edit.setCompleter(self.completer_authors)
			if label_text == "Читает":
				line_edit.setCompleter(self.completer_composer)
			#line_edit.setMaximumWidth(250)  # Задаем максимальную ширину для QLineEdit
			row_layout.addWidget(label)
			row_layout.addWidget(line_edit)
			form_layout.addLayout(row_layout)
			line_edit.editingFinished.connect(lambda le=line_edit, lt=label_text: self.update_mp3_tag_value(lt, le.text()))
			
		# Специальное размещение для 'Диска №' и 'Трека №'
			#special_row_layout = QHBoxLayout()
			#disk_number_label = QLabel('Книга в цикле')
			#track_number_label = QLabel('Трек №')
			#disk_number_edit = self.line_edits['Книга в цикле']
			#track_number_edit = self.line_edits['Трек №']
			#disk_number_edit.setFixedWidth(50)
			#track_number_edit.setFixedWidth(85)
		
		# Добавление виджетов в специальный компоновщик
			#special_row_layout.addWidget(disk_number_label)
			#special_row_layout.addWidget(disk_number_edit)
			#special_row_layout.addWidget(track_number_label)
			#special_row_layout.addWidget(track_number_edit)
		
		# Добавление специального компоновщика в основную форму
			#form_layout.addLayout(special_row_layout)
	
		# Описание
		description_label = QLabel('Описание')
		self.description_edit = CustomTextEdit()
		
		left_layout.addLayout(form_layout)
		left_layout.addWidget(description_label)
		
		left_layout.addWidget(self.description_edit)
		self.description_edit.setMaximumWidth(355)
		self.description_edit.focusOut.connect(lambda: self.update_mp3_tag_value('Описание', self.description_edit.toPlainText()))

		
		# Правая часть компоновки
		right_widget = QWidget() # Создаем виджет для правой части
		right_layout = QVBoxLayout(right_widget)
		
		# Правая часть компоновки - список и область для перетаскивания
		self.file_list = QListWidget()
		self.file_list.setAcceptDrops(True)  # Разрешаем перетаскивание файлов
		right_layout.addWidget(self.file_list)
		self.file_list.itemClicked.connect(self.on_file_selected) # выбор названия выбделенного файла 
		
		# Компоновщик для кнопок в нижней части
		buttons_layout = QHBoxLayout()
		
		# Кнопка "Запустить"
		start_button = QPushButton('Запустить')
		start_button.setIcon(QIcon(play_png_path))
		start_button.clicked.connect(self.start_action)  # Слот для обработки нажатия кнопки "Запустить"
		
		# Добавление кнопок в компоновщик кнопок
		buttons_layout.addWidget(start_button)
		
		# Добавление компоновщика кнопок в правую часть компоновки
		right_layout.addLayout(buttons_layout)
		
		# Добавляем левую и правую часть в splitter
		splitter.addWidget(left_widget)
		splitter.addWidget(right_widget)

		# Устанавливаем splitter как центральный виджет
		self.setCentralWidget(splitter)
				
		self.added_folders = set()  # имена ранее добавленных папок

# АВТОПОДСТАНОВКА АВТОРА ИЗ БАЗЫ
	def get_authors_from_db(self):
		self.connection = sqlite3.connect(db_path)
		self.cursor = self.connection.cursor()
		
		# запрос для получения списка авторов
		self.cursor.execute("SELECT name FROM authors")
		
		# Получение всех результатов
		self.authors = self.cursor.fetchall()
		
		# Преобразование списка кортежей в список строк
		authors_list = [author[0] for author in self.authors]
		return authors_list

# АВТОПОДСТАНОВКА ЧТЕЦА ИЗ БАЗЫ
	def get_composer_from_db(self):
		self.connection = sqlite3.connect(db_path)
		self.cursor = self.connection.cursor()
		
		# запрос для получения списка авторов
		self.cursor.execute("SELECT name FROM composer")
		
		# Получение всех результатов
		self.composer = self.cursor.fetchall()
		
		# Преобразование списка кортежей в список строк
		composer_list = [composer[0] for composer in self.composer]
		return composer_list
		
	def dragEnterEvent(self, event: QDragEnterEvent):
		if event.mimeData().hasUrls():
			event.acceptProposedAction()

# Формируем правую часть программы 	
	def add_to_list(self, dir_name):
		# Создаем QListWidgetItem
		item = QListWidgetItem(self.file_list)
		item.setData(Qt.UserRole, dir_name)
		
		# Создаем контейнерный виджет
		widget = QWidget()
		# Горизонтальный компоновщик для контейнера
		layout = QHBoxLayout()
		
		# Метка с именем файла
		label_file = QLabel(dir_name)
		# Установка стилей в зависимости от темы оформления
		label_file.setAlignment(Qt.AlignLeft)
		self.set_label_style(label_file)
	
		# Кнопка "Tag"
		button_tags = QPushButton()
		button_tags.setIcon(QIcon(tag_png_path))  # Укажите путь к файлу иконки
		button_tags.clicked.connect(lambda: self.tags_clicked(dir_name))
		# Задаем фиксированный размер кнопки
		button_tags.setFixedSize(45, 30)
		
		layout.addWidget(label_file)
		layout.addWidget(button_tags)
		
		# Установка компоновщика на контейнерный виджет
		widget.setLayout(layout)
		
		# Добавление контейнера в QListWidgetItem
		self.file_list.setItemWidget(item, widget)
		item.setSizeHint(widget.sizeHint())  # Установка размера элемента списка в соответствии с контейнером
			
	def set_label_style(self, label):
		# Получаем цвет фона из палитры приложения
		background_color = self.palette().color(QPalette.Window)
		# Проверяем яркость цвета фона
		if background_color.lightness() < 128:  # Темный фон
			label.setStyleSheet("color: white;")
		else:  # Светлый фон
			label.setStyleSheet("color: black;")
		
# ДОБАВЛЯЕМ ФАЙЛЫ В ПРОГУ
	def dropEvent(self, event: QDropEvent):
		for url in event.mimeData().urls():
			if url.isLocalFile():
				input_folder = url.toLocalFile().rstrip('/')   # input_folder
				# Проверяем, является ли путь директорией
				if os.path.isdir(input_folder):
					all_files_mp3 = True  # Предполагаем, что все файлы - mp3
					mp3_found = False  # Флаг наличия хотя бы одного mp3 файла
					
					for root, dirs, files in os.walk(input_folder):
						for file in files:
							# Пропускаем скрытые файлы
							if file.startswith('.'):
								continue
							if not file.endswith('.mp3') and not file.endswith('.MP3'):
								all_files_mp3 = False  # Найден файл с другим расширением
								break
							mp3_found = True
							
						if not all_files_mp3:
							break
						
					last_folder_or_filename = os.path.basename(input_folder)
					# Проверяем, была ли уже добавлена папка с таким именем
					if all_files_mp3 and mp3_found and last_folder_or_filename not in self.added_folders:
						self.add_to_list(last_folder_or_filename)
						self.added_folders.add(last_folder_or_filename)  # Добавляем имя папки в набор
						
						self.tags_mp3_files(input_folder)  # Извлечение тегов из первого mp3 файла и запись в db

					else:
						print(f"Папка '{last_folder_or_filename}' уже добавлена или не может быть добавлена.")
					
					# автоматический выбор самой первой книги в списке
					if self.file_list.count() != 0:
						self.file_list.setCurrentRow(0, QItemSelectionModel.SelectCurrent)
						self.on_file_selected()
					event.acceptProposedAction()

# ДОБАВЛЯЕМ ОБЛОЖКУ К ВЫБРАННОМУ ФАЙЛ
			cover_file_path = url.toLocalFile()
			if cover_file_path.endswith(('.jpg', '.jpeg', '.png')):
				
				selected_items = self.file_list.currentItem()
				if selected_items is not None:
					dir_name = selected_items.data(Qt.UserRole)  # Получаем данные, сохраненные в элементе списка
					
					# Обработка изображений
					image = QPixmap(cover_file_path).scaled(350, 350)
					self.cover_label.setPixmap(image)
								
					# Получение полного пути к файлу из базы данных
					self.cursor.execute("SELECT input_folder FROM books WHERE dir_name = ?", (dir_name,))
					result = self.cursor.fetchone()
					if result is not None:
						input_folder = result[0]
						files = sorted([f for f in os.listdir(input_folder) if f.endswith(".mp3") or f.endswith(".MP3")])
						# Выбираем первый файл
						first_file_path = os.path.join(input_folder, files[0])
					
						audio = MP3(first_file_path, ID3=ID3)
					
						if 'APIC:' in audio.tags:
							del audio.tags['APIC:']
						audio.save()
	
						with open(cover_file_path, 'rb') as albumart:
						# Читаем данные изображения
							albumart_data = albumart.read()
									
						# Добавляем APIC фрейм с обложкой альбома
						audio.tags.add(APIC(
							encoding=3,  # 3 означает UTF-8
							mime='image/jpeg',  # или 'image/png'
							type=3,  # 3 означает обложка альбома (front cover)
							desc='Cover',
							data=albumart_data
						))
						audio.save()  # Сохраняем изменения
				
						# Обновление значения тега "covr" в базе данных
						self.cursor.execute("UPDATE books SET cover = ? WHERE dir_name = ?", (albumart_data, dir_name))
					
					# Сохранение изменений в базе данных
					self.connection.commit()
	
# Извлечение тегов из первого mp3 файла и запись в db
	def tags_mp3_files(self, input_folder):
		# Получаем список mp3 файлов в папке и сортируем их
		files = sorted([f for f in os.listdir(input_folder) if f.endswith(".mp3") or f.endswith(".MP3")])
		# Извлекаем теги из первого файла
		first_file_path = os.path.join(input_folder, files[0])
		audio = MP3(first_file_path, ID3=ID3)
		
		try:
			album = str(audio['TALB']) # album = название книги
		except Exception as e:
			album = ''
				
		try:
			name = str(audio['TIT2']) # название
		except Exception as e:
			name = ''
		
		try:
			artist = str(audio['TPE1']) # artist = автор
		except Exception as e:
			artist = ''
		
		try:
			TPE2 = str(audio['TPE2']) # album artist = читает 
		except Exception as e:
			TPE2 = ''
			print(TPE2)
			
		try:
			genre = str(audio['TCON']) # жанр
		except Exception as e:
			genre = ''
			
		try:
			COMM = str(audio['COMM::eng']) # комментарии = читает 
		except Exception as e:
			COMM = ''
		
		if TPE2 != '':
			composer = TPE2
		else:
			composer = COMM
	
		def extract_cover(first_file_path):
			audiofile = eyed3.load(first_file_path)
			if audiofile.tag is None:
				return None
			cover = None
			for image in audiofile.tag.images:
				if image.picture_type == 3:  # Front cover
					cover = image.image_data
					break
				
			return cover
		
		cover = extract_cover(first_file_path)
		dir_name = os.path.basename(input_folder)  # имя папки с mp3 файлами

		file_data = ('', name, artist, album, composer, genre, '', '', '', '', dir_name, input_folder, '', cover)
		self.cursor.execute('''INSERT INTO books (status, name, artist, album, composer, genre, series, series_num, track_num, description, dir_name, input_folder, output_folder, cover) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', file_data)
		self.connection.commit()
	
#ИЗВЛЕКАЕМ ДАННЫЕ ПРИ ВЫДЕЛЕНИИ КНИГИ
	def on_file_selected(self):
		# Получаем текущий выбранный элемент
		selected_items = self.file_list.currentItem()
		
		if selected_items:
			dir_name = selected_items.data(Qt.UserRole)  # Получаем данные, сохраненные в элементе списка
			if dir_name:
				# Заполнение полей формы данными из базы данных для dir_name
				file_data = self.get_file_data(dir_name)
				if file_data:
					self.display_selected_files(file_data)

# ПОЛУЧЕНИЕ ТЕГОВ ИЗ БД 
	def get_file_data(self, input_folder):
		self.cursor.execute('''SELECT status, name, artist, album, composer, genre, series, series_num, track_num, description, dir_name, input_folder, output_folder, cover FROM books WHERE dir_name = ?''', (input_folder,))
		row = self.cursor.fetchone()
	
		if row:
			status, name, artist, album, composer, genre, series, series_num, track_num, description, dir_name, input_folder, output_folder, cover = row
			result = {'Название': name, 'Автор': artist, 'Книга': album, 'Читает': composer, 'Жанр': genre, 'Цикл': series, 'Книга в цикле': series_num, 'Трек №': track_num, 'Описание': description, 'Обложка': cover}
			return result
		else:
			return {}

# ОТОБРАЖЕНИЕ ТЕГОВ И ОБЛОЖКИ В ПРОГРАММЕ 
	def display_selected_files(self, file_data):
		# Обновление текстовых полей интерфейса данными
		for label_text, line_edit in self.line_edits.items():
			if label_text in file_data and 'Описание' in file_data and label_text != 'Обложка':  # Исключаем поле обложки для специальной обработки
				line_edit.setText(str(file_data[label_text]))
				self.description_edit.setText((file_data['Описание']))
		
		# Отдельная обработка для поля обложки
		if 'Обложка' in file_data and file_data['Обложка']:
			cover_data = file_data['Обложка']
			pixmap = QPixmap()
			pixmap.loadFromData(cover_data)
			self.cover_label.setPixmap(pixmap.scaled(350, 350, Qt.KeepAspectRatio))
		else:
			self.cover_label.setText("Обложка не найдена")

# ОЧИСТКА ПОЛЕЙ ТЕГОВ 
	def clear_fields(self, dir_name):
		for label_text, line_edit in self.line_edits.items():
			self.line_edits[label_text].clear()
			self.description_edit.clear()
			self.cover_label.clear()
		
# УДАЛЕНИЕ ФАЙЛОВ ИЗ СПИСКА
	def delete_selected_files(self):
		selected_items = self.file_list.currentItem()
			
		# Удаляем из БД
		dir_name = selected_items.data(Qt.UserRole)  # Получаем данные, сохраненные в элементе списка
		self.cursor.execute("DELETE FROM books WHERE dir_name = ?", (dir_name,))
			
		# Удаление файла из списка
		self.file_list.takeItem(self.file_list.row(selected_items))
		self.clear_fields(dir_name)
		self.cover_label.setStyleSheet("border: 1px solid black;")
		
		# Сохранение изменений в базе данных
		self.connection.commit()
		
		# Удаляем имя папки из набора added_folders
		if dir_name in self.added_folders:
			self.added_folders.remove(dir_name)

# ВЫДЕЛЕНИЕ ОЛОЖКИ
	def on_cover_label_clicked(self, event):
			if event.button() == Qt.LeftButton:
				self.cover_label.setStyleSheet("border: 2px solid red;")
	
# УДАЛЕНИЕ ОБЛОЖКИ
	def on_cover_label_key_pressed(self, event):
		if event.key() == Qt.Key_Minus:
			selected_items = self.file_list.currentItem()
			dir_name = selected_items.data(Qt.UserRole)  # Получаем данные, сохраненные в элементе списка
				
			# Получение полного пути к файлу из базы данных
			self.cursor.execute("SELECT input_folder FROM books WHERE dir_name = ?", (dir_name,))
			result = self.cursor.fetchone()
			if result is not None:
				input_folder = result[0]
				files = sorted([f for f in os.listdir(input_folder) if f.endswith(".mp3") or f.endswith(".MP3")])
				# Удалем обложку из первого файла
				first_file_path = os.path.join(input_folder, files[0])
				
				audiofile = eyed3.load(first_file_path)
				# Проверяем, существует ли тег в файле
				if audiofile.tag is None:
					return None
				
				# Находим обложку и удаляем её
				for image in audiofile.tag.images:
					if image.picture_type == 3:  # Front cover
						audiofile.tag.images.remove(image.description)  # Удаляем обложку
						break
				audiofile.tag.save()
					
				# Удаление значения тега "cover" из базы данных
				self.cursor.execute("UPDATE books SET cover = NULL WHERE dir_name = ?", (dir_name,))
					
				self.cover_label.clear()
				self.cover_label.setText("Обложка не найдена")
				self.cover_label.setStyleSheet("border: 1px solid black;")
				self.cover_label.clearFocus()
					
			# Сохранение изменений в базе данных
			self.connection.commit()

# ОБНОВЛЕНИЕ НАЗВНИЯ КНИГИ И АВТОРА В ТЕГАХ	
	def update_mp3_tag_value(self, tag, value):
		selected_items = self.file_list.currentItem()
		dir_name = selected_items.data(Qt.UserRole)  # Получаем данные, сохраненные в элементе списка
				
		# Получение полного пути к файлу из базы данных
		self.cursor.execute("SELECT input_folder FROM books WHERE dir_name = ?", (dir_name,))
		result = self.cursor.fetchone()
		if result is not None:
			input_folder = result[0]
			files = sorted([f for f in os.listdir(input_folder) if f.endswith(".mp3") or f.endswith(".MP3")])
			# Выбираем первый файл
			first_file_path = os.path.join(input_folder, files[0])
		
			tag_mapping = {
					"Название":"name",
					"Книга": "album",
					"Автор": "artist",
					"Читает": "composer",
					"Жанр": "genre",
					"Описание": "description",
					"Цикл": "series",
					"Книга в цикле": "series_num"
				}
				
			tag_en = tag_mapping.get(tag, tag)
												
									
			# Обновление значения тега в файле
			if tag_en == "name" or tag_en == "album" or tag_en == "artist" or tag_en == "composer" or tag_en == "genre" or tag_en == "description" or tag_en == "series" or tag_en == "series_num":
				audio = MP3(first_file_path, ID3=ID3)
				if audio.tags is None:
					audio.add_tags()
				try:
					if tag_en == 'name':
						# Устанавливаем название 
						audio.tags.add(TIT2(encoding=3, text=value))
					elif tag_en == 'album':
						# Устанавливаем название 
						audio.tags.add(TALB(encoding=3, text=value))
					elif tag_en == 'artist':
						# Устанавливаем автора 
						audio.tags.add(TPE1(encoding=3, text=value))
					elif tag_en == 'composer':
						# Устанавливаем чтеца 
						audio.tags.add(TCOM(encoding=3, text=value))
					elif tag_en == 'genre':
						# Устанавливаем жанр 
						audio.tags.add(TCON(encoding=3, text=value))
					elif tag_en == 'description':
						audio.tags.add(TXXX(encoding=3, desc="DESCRIPTION", text=value))
					
					# Сохраняем изменения
					audio.save()
										
				except error as e:
					print(f"Ошибка при обновлении тега: {e}")
				
				# Обновление значения тега в базе данных
				self.cursor.execute(f"UPDATE books SET {tag_en} = ? WHERE dir_name = ?", (value, dir_name))
				self.connection.commit()
			
			# Добавляем нового автора в БД
			if tag_en == "artist":
				if value != '' and value != ' ':
					self.cursor.execute("SELECT * FROM authors WHERE name = ?", (value,))
					existing_author = self.cursor.fetchone()
			
					if existing_author:
						pass
					else:
						# Если значение не существует, добавляем его в таблицу
						self.cursor.execute("INSERT INTO authors (name) VALUES (?)", (value,))
						self.connection.commit()
			
			# Добавляем нового чтеца в БД
			if tag_en == "composer":
				if value != '' and value != ' ':
					self.cursor.execute("SELECT * FROM composer WHERE name = ?", (value,))
					existing_composer = self.cursor.fetchone()
					
					if existing_composer:
						pass
					else:
						# Если значение не существует, добавляем его в таблицу
						self.cursor.execute("INSERT INTO composer (name) VALUES (?)", (value,))
						self.connection.commit()

# ОБРАБОТКА ПОИСКА ТЕГОВ
	# Слот, который вызывается при нажатии кнопки "Теги"
	def tags_clicked(self, dir_name):
		self.cursor.execute("SELECT artist, album FROM books WHERE dir_name = ?", (dir_name,))
		result = self.cursor.fetchone()
		
		if result:
			artist, album = result
			# Создаём экземпляр SearchWindow с передачей artist и album
			self.search_window = SearchWindow(default_artist=artist, default_album=album, default_dir_name=dir_name, parent=self)
		else:
			# Если результатов нет, создаём экземпляр SearchWindow без предзаполнения данных
			self.search_window = SearchWindow(parent=self)
			
		#self.search_window.exec()
		if self.search_window.exec():
			# Код для обработки после закрытия окна поиска, если нужно
			pass
		
	# Слот для кнопки "Отмена"
	def cancel_action(self):
		for worker in self.workers:
			worker.stop()  # Останавливаем каждый поток
	
	def select_folder(self):
		# Открытие диалогового окна для выбора папки
		select_folder_path = QFileDialog.getExistingDirectory(self, "Выберите папку")
		return select_folder_path
	
# Слот для кнопки "Запустить"
	def start_action(self):
		# Задаем папку для сохраенения конвертированных в m4и книг
		self.cursor.execute("SELECT output_folder FROM books")
		results = self.cursor.fetchall()
		
		# Проверка, есть ли в results хотя бы одно значение, равное ""
		select_folder_path = self.select_folder()
		if select_folder_path:  # Проверка, что пользователь выбрал папку
			new_output_folder = os.path.join(select_folder_path)#, "out.mp3")
					
			# Обновление записей в базе данных
			self.cursor.execute("UPDATE books SET output_folder = ? WHERE output_folder = ''", (new_output_folder,))
			self.connection.commit()
					
			# Повторное получение путки к выходной (mp3) и выхоной (m4b) папки
			self.cursor.execute("SELECT input_folder, output_folder, artist, album, composer, genre, series, series_num, description, cover FROM books")
			results = self.cursor.fetchall()
								
			if results:
				worker = Worker(results)
					
				total_books = len(results)
				self.progress_dialog = ProgressDialog(total_books)
				self.progress_dialog.cancel_clicked.connect(worker.stop)
				worker.progress_updated.connect(self.progress_dialog.update_progress)
				worker.finished.connect(self.progress_dialog.accept)  # Закрыть диалог по завершении
				self.workers.append(worker)  # Сохраняем ссылку на поток
				worker.finished.connect(lambda: self.workers.remove(worker))  # Удаляем из списка по завершении
				worker.start()  # Запуск потока	
				self.progress_dialog.exec()  # Отображение диалога

	# Удаление по Delete
	def keyPressEvent(self, event):
		if event.key() == Qt.Key_Delete:
			self.delete_selected_files()
		else:
			super().keyPressEvent(event)
	
	# Очистка таблицы books от данных
	def closeEvent(self, event):
		cursor = self.connection.cursor()
		cursor.execute("DELETE FROM books")
		self.connection.commit()
		self.connection.close()  # Закрытие соединения с базой данных при закрытии приложения
		self.settings.setValue("window_geometry", self.saveGeometry())
		super().closeEvent(event)
	
	def run(self):
		self.show()

###################
class AnimatedProgressBar(QProgressBar):
	def __init__(self, parent=None):
		super().__init__(parent)
		self._active = True
		self._animation_position = 0
		self._timer = QTimer(self)
		self._timer.timeout.connect(self.update_animation)
		self._timer.start(30)  # Обновляем анимацию каждые 30 мс
		
	def update_animation(self):
		self._animation_position = (self._animation_position + 1) % self.width()
		self.update()
		
	def paintEvent(self, event):
		super().paintEvent(event)  # Вызываем базовый метод отрисовки
		painter = QPainter(self)
		rect = self.rect()
		
		# Цвет фона и выделения
		background_color = self.palette().color(QPalette.Window)
		highlight_color = self.palette().color(QPalette.Highlight)
		
		# Рисуем фон
		painter.fillRect(rect, background_color)
		
		# Рисуем заполненную часть прогресс-бара
		processed_width = rect.width() * self.value() / self.maximum() # 
		painter.fillRect(0, 0, processed_width, rect.height(), highlight_color)
		
		# Если прогресс полный, не отображаем анимацию
		if self.value() == self.maximum():
			return
		
		# Рисуем анимированную часть
		brush = QBrush(highlight_color)
		#brush = QBrush(QColor('red'))
		brush.setStyle(Qt.DiagCrossPattern) # Выбираем стиль штриховки
		
		# Смещаем штриховку в зависимости от позиции анимации
		painter.translate(self._animation_position - rect.width(), 0)
		painter.fillRect(processed_width, 0, rect.width(), rect.height(), brush)
		painter.translate(-(self._animation_position - rect.width()), 0)  # Возвращаем обратно

class ProgressDialog(QDialog):
	cancel_clicked = Signal()  # Сигнал для обработки нажатия кнопки "Отмена"
	def __init__(self, total_books, parent=None):
		super().__init__(parent)
		self.total_books = total_books
		self.current_book = 0
		
		self.layout = QVBoxLayout(self)
		
		self.message_label = QLabel()  # Создаём QLabel без текста
		self.update_message_label()  # Теперь вызываем для инициализации текста сообщения
		
		self.progress_bar = AnimatedProgressBar(self)
		self.progress_bar.setMaximum(self.total_books)
		
		self.start_progress()
		
		self.cancel_button = QPushButton("Отмена")
		self.cancel_button.clicked.connect(self.cancel_clicked.emit)
		
		# Добавление виджетов в макет
		self.layout.addWidget(self.message_label)
		self.layout.addWidget(self.progress_bar)
		self.layout.addWidget(self.cancel_button)
	
	# Обработка текста с колличеством книг
	@staticmethod
	def get_books_word(number):
		if 11 <= number % 100 <= 14:
			return "книг"
		elif number % 10 == 1:
			return "книгу"
		elif 2 <= number % 10 <= 4:
			return "книги"
		else:
			return "книг"
		
	def update_message_label(self):
		# Обновляем текст сообщения с правильным окончанием слова "книг"
		remaining_books = self.total_books - self.current_book
		word = self.get_books_word(remaining_books)
		self.message_label.setText(f"Осталось обработать {remaining_books} {word}")
		
	def start_progress(self):
		self.current_book = 0
		self.progress_bar.setValue(0)
		
	def update_progress(self, current_book):
		self.current_book = current_book
		self.progress_bar.setValue(self.current_book)
		
		self.update_message_label()

class Worker(QThread):
	progress_updated = Signal(int)  # Сигнал для обновления прогресса
	def __init__(self, results, parent=None):
		super().__init__(parent)
		self.results = results
		self.is_running = True  # Флаг для контроля выполнения
		
	def stop(self):
		self.is_running = False  # Метод для остановки выполнения
		
	def run(self):
		merge_mp3_files(self.results, self)

# ОКНО ПОИСКА ТЕГОВ ПО API
class SearchWindow(QDialog):
	def __init__(self, default_artist='', default_album='', default_dir_name='', parent=None):
		super().__init__(parent)
		self.setWindowTitle("Поиск информации о книге")
		
		# Установка ключа для сохранения состояния окна
		self.settings = QSettings("Поиск информации о книге")
		
		# Загрузка предыдущей позиции окна
		window_geometry = self.settings.value("window_geometry")
		if window_geometry is not None:
			self.restoreGeometry(window_geometry)
		else:
			self.setGeometry(100, 100, 1350, 600)
		
		self.default_artist = default_artist
		self.default_album = default_album
		self.default_dir_name = default_dir_name
		
		# Добавление словаря для хранения изображений в формате BLOB
		self.image_data = {}
		
		self.setup_ui()
		
	def setup_ui(self):
		layout = QVBoxLayout(self)
		
		self.book_title_edit = QLineEdit()
		self.book_title_edit.setPlaceholderText('Название книги')
		self.book_title_edit.setText(self.default_album)
		self.author_edit = QLineEdit()
		self.author_edit.setPlaceholderText('Автор')
		self.author_edit.setText(self.default_artist)
		
		self.book_title_edit.editingFinished.connect(lambda be=self.book_title_edit: self.set_text_find_book(be.text()))
		self.author_edit.editingFinished.connect(lambda be=self.author_edit: self.set_text_find_book(be.text()))
		
		self.results_table = QTableWidget(0, 7)
		self.results_table.setHorizontalHeaderLabels(['cover', 'album', 'artist', 'description', 'genre', 'series', 'series_num'])
		self.results_table.setSelectionBehavior(QTableWidget.SelectRows)
		self.results_table.setColumnWidth(3, 500)  # Установка ширины столбца 'description'
		
		okay_button = QPushButton('Окей')
		okay_button.clicked.connect(self.accept)
		
		layout.addWidget(self.book_title_edit)
		layout.addWidget(self.author_edit)
		layout.addWidget(self.results_table)
		layout.addWidget(okay_button)
		
		# передаем в запрос значения Название книги + Автор указанные в тегах mp3 файла
		results = self.tag_find()
		if results:
			self.fill_results_table(results)
	
	# Изменение названия и автора книги для поиска
	def set_text_find_book(self, tag_value):
		results = self.tag_find()
		if results:
			self.fill_results_table(results)

# ПОИСК ТЕГОВ КНИГИ
	# Загрузка найденных обложек книги
	def set_image_in_table(self, row, col, image_url):
		if image_url is not None:
			response = requests.get(image_url)
			if response.status_code == 200:
				image_data = BytesIO(response.content).read()
				# Сохранение изображения в формате BLOB в словарь
				self.image_data[row] = image_data
					
				image_pixmap = QPixmap()
				image_pixmap.loadFromData(image_data)
				image_pixmap = image_pixmap.scaledToWidth(100, Qt.SmoothTransformation)
				label = QLabel()
				label.setPixmap(image_pixmap)
				label.setAlignment(Qt.AlignCenter)
				self.results_table.setCellWidget(row, col, label)
	
	# настройка ободражения найденных описаний книги
	def set_text_with_word_wrap(self, row, col, text):
		label = QLabel(text)
		label.setWordWrap(True) # перенос по строкам длинного текста
		self.results_table.setCellWidget(row, col, label)
	
	# получение и отображение найденных данных о книге 
	def fill_results_table(self, results):
		# Очистка содержимого таблицы перед заполнением новыми данными
		self.results_table.clearContents()
		
		# Установка количества строк в таблице
		self.results_table.setRowCount(len(results))
		
		for row_index, result in enumerate(results):
			self.set_image_in_table(row_index, 0, result['cover'])
			self.results_table.setItem(row_index, 1, QTableWidgetItem(result['album']))
			self.results_table.setItem(row_index, 2, QTableWidgetItem(result['artist']))
			self.set_text_with_word_wrap(row_index, 3, result['description'])
			self.results_table.setItem(row_index, 4, QTableWidgetItem(result['genre']))
			self.results_table.setItem(row_index, 5, QTableWidgetItem(result.get('series', '')))
			self.results_table.setItem(row_index, 6, QTableWidgetItem(str(result.get('series_num', ''))))
		
		# Автоматическое изменение размеров столбцов и строк под содержимое	
		self.results_table.resizeColumnsToContents()
		self.results_table.resizeRowsToContents()
		
# ПОИСК ТЕГОВ
	def tag_find(self):
		
		query_name = self.book_title_edit.text()
		query_artist= self.author_edit.text()

		api_url = 'https://api.fantlab.ru/search-txt?q=' + query_name + ' ' + query_artist
		response = requests.get(api_url)
		data = response.json()
		
		try:
			results = []
			for work in data['works']:
				author = work['creators']['authors'][0]['name']
				album = work['name']
				genre = work['name_type']
				description = work['description']
				img = work.get('image', None)
				if img != None:
					cover = 'https://fantlab.ru' + img
				else:
					cover = None
				series_id = work.get('saga', {}).get('id', '')
				series = work.get('saga', {}).get('name', '')
				work_id = work.get('id', None)
				
				series_num = ''
				
				if series_id:
					api_url_series = 'https://api.fantlab.ru/work/' + str(series_id) + '/extended'
					response_series = requests.get(api_url_series)
					data_series = response_series.json()
					
					# Поиск series_num
					for index, child in enumerate(data_series['children']):
						if str(child.get('work_id')) == str(work_id):
							series_num = index + 1
							break
						
				results.append({
					'artist': author,
					'album': album,
					'description': description,
					'genre': genre,
					'cover': cover,
					'series': series,
					'series_num': series_num
				})
				
		except Exception as e:
			print(f"Произошла ошибка: {e}")
		
		return results
		
	def accept(self):
		# Получение выделенных строк
		selected_rows = self.results_table.selectionModel().selectedRows()
		
		self.connection = sqlite3.connect(db_path)
		self.cursor = self.connection.cursor()
		
		if selected_rows:
			selected_row = selected_rows[0].row()
			book_data = {
				'cover': self.image_data.get(selected_row),  # Получение BLOB изображения
				'name': self.results_table.item(selected_row, 1).text(),
				'album': self.results_table.item(selected_row, 1).text(),
				'artist': self.results_table.item(selected_row, 2).text(),
				'description': self.results_table.cellWidget(selected_row, 3).text() if self.results_table.cellWidget(selected_row, 3) else "",
				#'genre': self.results_table.item(selected_row, 4).text(),
				'series': self.results_table.item(selected_row, 5).text(),
				'series_num': self.results_table.item(selected_row, 6).text()
			}
			
			# сообщение выбора обновлять или нет обложку
			msgBox = QMessageBox()
			msgBox.setText("Обновить обложку?")
			msgBox.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
			msgBox.setDefaultButton(QMessageBox.No)
			ret = msgBox.exec()
			
			if ret == QMessageBox.Yes:
				# Обновляем все поля, включая обложку
				for tag, value in book_data.items():
					self.cursor.execute(f"UPDATE books SET {tag} = ? WHERE dir_name = ?", (value, self.default_dir_name))

			else:
				# Обновляем все поля, кроме обложки
				for tag, value in book_data.items():
					if tag != 'cover':  # Пропускаем поле обложки
						self.cursor.execute(f"UPDATE books SET {tag} = ? WHERE dir_name = ?", (value, self.default_dir_name))
			
			self.connection.commit()
		self.settings.setValue("window_geometry", self.saveGeometry()) # Сохранение позиции окна поиска тегов
		super().accept()
		
if __name__ == '__main__':
	app = QApplication(sys.argv)
	main_window = AudioBooksBuilder()
	main_window.run()
	sys.exit(app.exec())