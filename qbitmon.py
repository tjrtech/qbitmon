import requests  # type: ignore
import time
import os
import sys
import shutil
import difflib
import logging
from pathlib import Path
import re
from imdb import Cinemagoer  # type: ignore
import smtplib
from email.message import EmailMessage
from difflib import SequenceMatcher

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
PASSWORD = os.getenv('GQBIT_PASSWORD')

def login(session):
    response = session.post(f"{QB_URL}/api/v2/auth/login", data={
        'username': USERNAME,
        'password': PASSWORD
    })
    return response.text == 'Ok.'

def get_completed_torrents(session):
    try:
        response = session.get(f"{QB_URL}/api/v2/torrents/info", params={"filter": "completed"}, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError as e:
        logging.error(f"Connection error while accessing qBittorrent API: {e}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Request to qBittorrent API failed: {e}")
    return []

def normalize(name):
    name = re.sub(r'\[.*?\]', '', name)
    return re.sub(r'\s+', ' ', name).strip().lower()

def find_matching_directory(base_path, expected_name):
    full_path = Path(base_path) / expected_name
    if full_path.exists():
        logging.info(f"Found source directory {expected_name} thus no fuzzy match required")
        return full_path

    try:
        expected_norm = normalize(expected_name)
        best_match = None
        best_score = 0

        for d in Path(base_path).iterdir():
            if d.is_dir():
                candidate_norm = normalize(d.name)
                score = SequenceMatcher(None, expected_norm, candidate_norm).ratio()
                if score > best_score:
                    best_score = score
                    best_match = d

        if best_score >= 0.8:
            logging.info(f"Corrected source directory name to match {best_match.name} (score: {best_score:.2f})")
            return best_match
        else:
            logging.warning(f"No close match found for '{expected_name}' (best score: {best_score:.2f})")
    except Exception as e:
        logging.warning(f"Error scanning directories: {e}")
    return None

def move_video_files_from_torrent_dir(torrent_name, full_path):
    moved = False
    for root, _, files in os.walk(full_path):
        for file in files:
            if Path(file).suffix.lower() in VIDEO_EXTENSIONS:
                src = Path(root) / file
                renamed_path = rename_movie_file(str(src))

                if renamed_path:
                    if move_file_to_plex_movies(renamed_path):
                        send_email("Imported to Plex", f"{os.path.basename(renamed_path)}",
                                   SENDER_EMAIL, SENDER_PASSWORD)
                        moved = True
                    else:
                        logging.warning(f"Failed to move renamed file: {renamed_path}")
                        send_email("Plex Import Failed", f"Could NOT move to Plex: {os.path.basename(renamed_path)}",
                                   SENDER_EMAIL, SENDER_PASSWORD)
                else:
                    logging.warning(f"Failed to rename file: {src}")
                    send_email("Plex Import Failed", f"Could NOT rename: {os.path.basename(src)}",
                               SENDER_EMAIL, SENDER_PASSWORD)

    if not moved:
        logging.info(f"No video files found in {full_path}")

    logging.info(f"Removing directory {full_path}")
    shutil.rmtree(full_path)
    return moved

def rename_movie_file(filename):
    if not os.path.exists(filename):
        logging.error(f"Source file does not exist: {filename}")
        return None

    name, ext = os.path.splitext(filename)
    if ext.lower() not in VIDEO_EXTENSIONS:
        logging.info(f"Skipping {filename} (unsupported extension)")
        return None

    final_name_pattern = re.compile(r'.+_\(\d{4}\)' + re.escape(ext) + r'$')
    if final_name_pattern.match(filename):
        logging.info(f"Skipping {filename} (already in Title_(Year) format)")
        return filename

    clean_name = os.path.basename(name).replace('.', '_').replace(' ', '_')
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
    parent_dir = os.path.dirname(filename)
    new_path = os.path.join(parent_dir, new_name)

    os.rename(filename, new_path)
    logging.info(f"Renaming movie to {os.path.basename(new_name)}")
    return new_path

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
        # Use rename instead of copy+remove for speed on same volume
        source.rename(destination)
        logging.info(f"Moved {os.path.basename(source)} to {destination_dir}")
        return True
    except Exception as e:
        logging.error(f"Failed to move file: {e}")
        return False

def send_email(subject: str, body: str, sender_email: str, sender_password: str):
    recipient_email = sender_email  
    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = sender_email

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender_email, sender_password)
            smtp.send_message(msg)
            logging.info(f"Email sent to {recipient_email}")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")

def remove_torrent(session, torrent_hash):
    response = session.post(f"{QB_URL}/api/v2/torrents/delete", data={
        'hashes': torrent_hash,
        'deleteFiles': 'false'
    })
    if response.status_code == 200:
        logging.info(f"Removed torrent from qBittorrent {torrent_hash}")
    else:
        logging.error(f"Failed to remove torrent {torrent_hash}: {response.text}")

def main():
    session = requests.Session()
    try:
        if not login(session):
            logging.error("Failed to log in to qBittorrent. Is qBittorrent running?")
            sys.exit(1)
    except requests.exceptions.ConnectionError:
        logging.error(f"Could not connect to qBittorrent at {QB_URL}. Is qBittorrent running?")
        sys.exit(1)

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

                    logging.info('-' * 100)
                    logging.info(f"Detected completed torrent {name}")
                    full_path = find_matching_directory(save_path, name)

                    if full_path is None:
                        logging.warning(f"Could not find matching directory for: {name}")
                        continue

                    move_video_files_from_torrent_dir(name, full_path)
                    remove_torrent(session, torrent_hash)
                    known_completed_hashes.add(torrent_hash)

            time.sleep(10)
    except KeyboardInterrupt:
        logging.info("Stopped monitoring.")

if __name__ == "__main__":
    main()
