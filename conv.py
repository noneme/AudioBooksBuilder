import os
import sys
import subprocess
import shutil
import time
import re
from mutagen import File
from mutagen.mp4 import MP4, MP4Cover
from mutagen.mp3 import MP3

def get_file_path(file_name):
	if getattr(sys, 'frozen', False):  # Проверка, запущено ли приложение из исполняемого файла
		base_path = sys._MEIPASS  # Получение пути к папке с ресурсами
	else:
		base_path = os.path.abspath(os.path.dirname(__file__))  # Получение текущего пути к скрипту
		
	path = os.path.join(base_path, file_name)
	return path

# получение данных об обрабатываеой книге из программы + worker - состояние кнопки "Отмена"
def merge_mp3_files(results, worker):
	start = len(results)
	
	for i, row in enumerate(results, 1):
		input_folder = row[0]
		output_file = row[1]
		artist = row[2]
		album = row[3]
		composer = row[4]
		genre = row[5]
		series = row[6]
		series_num = row[7]
		description = row[8]
		cover = row[9]
		
		bild_m4b(input_folder, output_file, artist, album, composer, genre, series, series_num, description, cover, worker)
		
		worker.progress_updated.emit(i)  # Обновление прогресса
		
# Функция для определения формата изображения по BLOB
def get_image_format(blob_data):
	if blob_data.startswith(b'\xff\xd8'):
		return MP4Cover.FORMAT_JPEG
	elif blob_data.startswith(b'\x89PNG\r\n\x1a\n'):
		return MP4Cover.FORMAT_PNG
	
def bild_m4b(mp3_folder, output_file, artist, album, composer, genre, series, series_num, description, cover, worker):
	
	if worker.is_running: # если отмена не нажата
		abbinder_path = get_file_path('abbinder')
	
		files = sorted([f for f in os.listdir(mp3_folder) if f.endswith('.mp3') or f.endswith('.MP3')])
		# Создаем список пар: номер главы и путь к файлу
		files_with_chapters = []
		for index, f in enumerate(files):
			chapter = f"@Глава {index+1}@"
			path = os.path.join(mp3_folder, f)
			files_with_chapters.extend([chapter, path])
			
		files_list = files_with_chapters # Список файлов + Список глав
	
		audio = MP3(path)
		bitrate = audio.info.bitrate / 1000 # определяем битрейт последнего в папке mp3 файла 
	
		if bitrate < 65:
			bitrate = str(64)
		elif bitrate < 75:
			bitrate = str(72)
		elif bitrate <= 80:
			bitrate = str(80)
		elif bitrate <= 96:
			bitrate = str(96)
		elif bitrate <= 112:
			bitrate = str(112)
		elif bitrate <= 128:
			bitrate = str(128)
		elif bitrate <= 144:
			bitrate = str(144)
		elif bitrate <= 160:
			bitrate = str(160)
		elif bitrate <= 192:
			bitrate = str(192)
		elif bitrate <= 244:
			bitrate = str(244)
		elif bitrate <= 256:
			bitrate = str(256)
		elif bitrate <= 288:
			bitrate = str(288)
		else:
			bitrate = str(320)
			
		total_duration = 0.0    # Длительность книги в секундах 
		for file in os.listdir(mp3_folder):
			if file.endswith(".mp3"):
				# Составляем полный путь к файлу
				file_path = os.path.join(mp3_folder, file)
				audio = MP3(file_path)
				total_duration += audio.info.length
				
		# если книга бльше 11 часов - разбиваем ее не части
		if total_duration > 54000:
			duration_book = 15
		else:
			duration_book = 0
			
		output_file_m4b = os.path.splitext(output_file)[0] + '/' + album + '.m4b'  # Путь к mp4 файлу + Имя файла
	
		# СБОРКА
		command = [abbinder_path, '-o', output_file_m4b, '-b', bitrate, '-s', '-q', '-l', str(duration_book)] + files_list
		#subprocess.run(command)
	
		mp3_to_m4b = subprocess.Popen(command)
	
		# Периодическая проверка флага is_running, пока процесс не завершится
		while True:
			if not worker.is_running:
				mp3_to_m4b.terminate() # Останавливаем
				if os.path.exists(output_file_m4b): # Чистим временные файлы
					os.remove(output_file_m4b)
				break
			if mp3_to_m4b.poll() is not None: # нормалоьная работа - переходим к следующей книге 
				break
	
		if not worker.is_running: # Выход из конвертирования
			return

	time.sleep(0.1)
	add_tag_to_m4b(output_file, artist, album, composer, genre, series, series_num, description, cover)
	
def add_tag_to_m4b(output_file, artist, album, composer, genre, series, series_num, description, cover):
	
	# Получаем список всех файлов с расширением .m4b в корне папки
	m4b_files = [os.path.join(output_file, f) for f in os.listdir(output_file) if f.endswith('.m4b') or f.endswith('.M4B')]
	
	# Выводим полные пути к файлам
	for file_path in m4b_files:
		audio = MP4(file_path)
		audio["\xa9nam"] = album
		audio["\xa9ART"] = artist
		audio["\xa9alb"] = album
		audio["\xa9wrt"] = composer
		audio["\xa9gen"] = genre
		audio["----:com.apple.iTunes:series"] = series.encode("utf-8")
		audio["----:com.apple.iTunes:series-part"] = str(series_num).encode("utf-8")
		audio["desc"] = description
		
		if cover != None and cover != '':
		# проверяем формат обложки
			cover_format = get_image_format(cover)
			cover_art = MP4Cover(cover, imageformat=cover_format)
			audio['covr'] = [cover_art]
		audio.save()
		
	time.sleep(0.1)
	
	# ПЕРЕМЕЩАЕМ ФАЙЛ В ЦЕЛЕВУЮ ПАПКУ
	# Полный путь к папке artist
	artist_folder = os.path.join(output_file, f"{artist}")
	# Полный путь к папке album
	album_folder = os.path.join(artist_folder, f"{album}")
	# Создание папки album, если она еще не существует
	os.makedirs(album_folder, exist_ok=True)
	
	# Получение списка всех файлов m4b в исходной папке
	m4b_files = [f for f in os.listdir(output_file) if f.endswith('.m4b') or f.endswith('.M4B')]
	
	for file_name in m4b_files:
		file_path = os.path.join(output_file, file_name)
		new_output_path_m4b = os.path.join(album_folder, file_name)
		
		# Перемещение файла в папку album
		shutil.move(file_path, new_output_path_m4b)
		
	time.sleep(0.1)
	