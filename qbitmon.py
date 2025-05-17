import requests
import time
import os
import shutil
import difflib
import logging
from pathlib import Path
import re
from imdb import Cinemagoer
import smtplib
from email.message import EmailMessage

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# SMS configuration
CARRIER_GATEWAY = 'vtext.com'
SENDER_EMAIL = os.getenv('GMAIL_EMAIL_ADDRESS')  # Use environment variable
SENDER_PASSWORD = os.getenv('GMAIL_APP_PASSWORD')  # Use environment variable
SMS_CELL_NUMBER = '2674244233'

# Supported video extensions
VIDEO_EXTENSIONS = ['.mkv', '.mp4', '.avi']
year_pattern = re.compile(r'(19[0-9]{2}|20[0-5][0-9]|206[0-6])')
resolutions = ['720p', '1080p']
ia = Cinemagoer()

# qBittorrent Configuration
QB_URL = "http://localhost:8080"
USERNAME = "admin"
PASSWORD = "Hansol47$"

def login(session):
    response = session.post(f"{QB_URL}/api/v2/auth/login", data={
        'username': USERNAME,
        'password': PASSWORD
    })
    return response.text == 'Ok.'

def get_completed_torrents(session):
    response = session.get(f"{QB_URL}/api/v2/torrents/info", params={"filter": "completed"})
    return response.json()

def find_matching_directory(base_path, expected_name):
    full_path = Path(base_path) / expected_name
    if full_path.exists():
        return full_path

    try:
        candidates = [d for d in Path(base_path).iterdir() if d.is_dir()]
        match = difflib.get_close_matches(expected_name, [d.name for d in candidates], n=1)
        if match:
            corrected_path = Path(base_path) / match[0]
            logging.info(f"Corrected directory name: {expected_name} → {match[0]}")
            return corrected_path
    except Exception as e:
        logging.warning(f"Error scanning directories: {e}")
    return None

def move_video_files_from_dir(torrent_name, full_path):
    moved = False
    for root, _, files in os.walk(full_path):
        for file in files:
            if Path(file).suffix.lower() in VIDEO_EXTENSIONS:
                src = Path(root) / file
                dst = Path.cwd() / file
                logging.info(f"Moving movie {os.path.basename(src)} → {os.path.dirname(dst)}")
                shutil.move(str(src), str(dst))
                file_path = rename_movie_file(str(dst))
                if file_path:
                    if move_file_to_plex_movies(file_path):
                        send_sms_via_email(SMS_CELL_NUMBER, CARRIER_GATEWAY,
                                           f"Imported to Plex server: {os.path.basename(file_path)}",
                                           SENDER_EMAIL, SENDER_PASSWORD)
                        moved = True
                else:
                    send_sms_via_email(SMS_CELL_NUMBER, CARRIER_GATEWAY,
                        f"Could NOT rename and import to Plex server: {os.path.basename(src)}",
                        SENDER_EMAIL, SENDER_PASSWORD)
                    logging.warning(f"Skipping file due to failed rename: {dst}")

    logging.info(f"Removing directory: {full_path}")
    shutil.rmtree(full_path)
    return moved

def remove_torrent(session, torrent_hash):
    response = session.post(f"{QB_URL}/api/v2/torrents/delete", data={
        'hashes': torrent_hash,
        'deleteFiles': 'false'
    })
    if response.status_code == 200:
        logging.info(f"Removed torrent from qBittorrent: {torrent_hash}")
    else:
        logging.error(f"Failed to remove torrent {torrent_hash}: {response.text}")

def rename_movie_file(filename):
    name, ext = os.path.splitext(filename)
    if ext.lower() not in VIDEO_EXTENSIONS:
        logging.info(f"Skipped: {filename} (unsupported extension)")
        return None

    final_name_pattern = re.compile(r'.+_\(\d{4}\)' + re.escape(ext) + r'$')
    if final_name_pattern.match(filename):
        logging.info(f"Skipped: {filename} (already in Title_(Year) format)")
        new_path = Path(filename).parent / filename
        return new_path

    clean_name = name.replace('.', '_').replace(' ', '_')
    parts = clean_name.split('_')

    year = None
    year_index = None
    for i in reversed(range(len(parts))):
        if year_pattern.fullmatch(parts[i]):
            year = parts[i]
            year_index = i
            break

    if year:
        title = '_'.join(parts[:year_index])
    else:
        res_index = None
        for res in resolutions:
            if res in parts:
                res_index = parts.index(res)
                break
        title = '_'.join(parts[:res_index]) if res_index is not None else clean_name

        search_query = title.replace('_', ' ')
        try:
            logging.info(f"IMDb searching for title '{search_query}'")
            results = [m for m in ia.search_movie(search_query) if m.get('kind') == 'movie']
            if results:
                movie = results[0]
                ia.update(movie)
                imdb_year = str(movie.get('year', '')).strip()
                if year_pattern.fullmatch(imdb_year):
                    year = imdb_year
                    title = title.rstrip('_')
                else:
                    logging.warning(f"Skipping {filename} (IMDb year not valid)")
                    return None
            else:
                logging.warning(f"Skipping {filename} (IMDb search returned no results)")
                return None
        except Exception as e:
            logging.warning(f"Skipping {filename} (IMDb lookup failed: {e})")
            return None

    if year:
        title = title.rstrip('_')
        new_name = f"{title}_({year}){ext}"
    else:
        new_name = f"{title}{ext}"

    new_name = re.sub(r'_+', '_', new_name).strip('_')
    new_path = Path(filename).parent / new_name

    os.rename(filename, new_path)
    logging.info(f"Renaming {os.path.basename(filename)} -> {os.path.basename(new_name)}")
    return str(new_path)

def move_file_to_plex_movies(file_path):
    source = Path(file_path).resolve()
    destination_dir = Path("/Volumes/Plex Server/Movies")

    if not destination_dir.exists():
        logging.warning(f"Destination folder '{destination_dir}' does not exist. File not moved.")
        return False

    if not source.exists():
        logging.error(f"Source file {source} does not exist.")
        return False

    destination = destination_dir / source.name

    if destination.exists():
        logging.warning(f"File already exists at destination: {destination}")
        return False

    try:
        logging.info(f"Moving {os.path.basename(source)} to Plex server...")
        shutil.copy2(source, destination)
        os.remove(source)
        logging.info(f"File moved to {destination}")
        return True
    except Exception as e:
        logging.error(f"Failed to move file: {e}")
        return False

def send_sms_via_email(phone_number: str, carrier_gateway: str, message: str,
                       sender_email: str, sender_password: str):
    sms_email = f"{phone_number}@{carrier_gateway}"

    msg = EmailMessage()
    msg.set_content(message)
    msg["Subject"] = ""
    msg["From"] = sender_email
    msg["To"] = sms_email

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender_email, sender_password)
            smtp.send_message(msg)
            logging.info(f"Sent message to {sms_email}")
    except Exception as e:
        logging.error(f"Failed to send SMS via email: {e}")

def main():
    session = requests.Session()
    if not login(session):
        logging.error("Failed to log in to qBittorrent.")
        return

    print()
    logging.info("Monitoring for completed downloads... Press Ctrl+C to stop.")
    known_completed_hashes = set()

    try:
        while True:
            completed = get_completed_torrents(session)
            for torrent in completed:
                torrent_hash = torrent['hash']
                if torrent_hash not in known_completed_hashes:
                    name = torrent['name']
                    save_path = torrent['save_path']

                    logging.info('-'*100)
                    logging.info(f"Detected completed torrent: {name}")
                    full_path = find_matching_directory(save_path, name)

                    if full_path is None:
                        logging.warning(f"Could not find matching directory for: {name}")
                        continue

                    move_video_files_from_dir(name, full_path)
                    remove_torrent(session, torrent_hash)
                    known_completed_hashes.add(torrent_hash)

            time.sleep(10)
    except KeyboardInterrupt:
        logging.info("Stopped monitoring.")

if __name__ == "__main__":
    main()