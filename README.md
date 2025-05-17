# qbitmon

## Overview
qbitmon is a Python script designed to monitor completed downloads in qBittorrent. It automates the process of moving video files to a specified directory, renaming them based on IMDb data, and sending SMS notifications via email when files are imported into a Plex server.

## Features
- Monitors completed downloads in qBittorrent.
- Moves video files to a designated directory.
- Renames files according to IMDb data.
- Sends SMS notifications via email upon successful file import.

## Requirements
To run this project, you need to install the following dependencies:

- requests
- imdbpy
- other necessary libraries

## Setup Instructions
1. Clone the repository:
   ```
   git clone <repository-url>
   cd qbitmon
   ```

2. Install the required packages:
   ```
   pip install -r requirements.txt
   ```

3. Set up your environment variables for email notifications:
   - `GMAIL_EMAIL_ADDRESS`: Your Gmail email address.
   - `GMAIL_APP_PASSWORD`: Your Gmail app password.

4. Configure the qBittorrent settings in `qbitmon.py`:
   - Update the `USERNAME` and `PASSWORD` variables with your qBittorrent credentials.

## Usage
Run the script using Python:
```
python qbitmon.py
```
The script will start monitoring for completed downloads. You can stop it by pressing `Ctrl+C`.

## License
This project is licensed under the MIT License.