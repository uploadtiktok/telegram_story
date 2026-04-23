import os
import re
import asyncio
import configparser
import requests
import isodate
import yt_dlp
import base64
from googleapiclient.discovery import build
from datetime import datetime, timedelta
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.stories import SendStoryRequest
from telethon.tl.types import InputMediaUploadedDocument, DocumentAttributeFilename, DocumentAttributeVideo, InputPrivacyValueAllowAll

# --- جلب الإعدادات من البيئة (Secrets) ---
# في GitHub Actions يتم ضبط هذه القيم في قسم Secrets
# في Termux يمكنك ضبطها عبر ملف .env أو تصديرها كـ export
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME = os.getenv("GH_REPO") # مثال: username/repo
TELEGRAM_API_ID = os.getenv("TG_API_ID")
TELEGRAM_API_HASH = os.getenv("TG_API_HASH")
TELEGRAM_SESSION = os.getenv("TG_SESSION")

VIDEO_URL_REF = "https://youtube.com/shorts/oG3WdBgRgxo?si=sMYhFhPMkB8DtYyF"
DEFAULT_START_DATE = "2026-03-11T00:00:00Z"
FILE_PATH = "youtube_shorts_links.txt"
BRANCH = "main"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_VIDEO = os.path.join(BASE_DIR, "temp_video.mp4")
COOKIES_FILE = os.path.join(BASE_DIR, "cookies.txt")

# --- وظائف GitHub ---

def get_github_file():
    url = f"https://api.github.com/repos/{REPO_NAME}/contents/{FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    res = requests.get(url, headers=headers)
    if res.status_code == 200:
        data = res.json()
        content = base64.b64decode(data['content']).decode('utf-8')
        return content, data['sha']
    return "", None

def update_github_file(new_content, sha, message):
    url = f"https://api.github.com/repos/{REPO_NAME}/contents/{FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    encoded_content = base64.b64encode(new_content.encode('utf-8')).decode('utf-8')
    data = {"message": message, "content": encoded_content, "sha": sha, "branch": BRANCH}
    res = requests.put(url, headers=headers, json=data)
    return res.status_code == 200

# --- وظائف اليوتيوب ---

def get_video_details(youtube, video_id):
    try:
        res = youtube.videos().list(part="snippet", id=video_id).execute()
        if res['items']:
            return res['items'][0]['snippet']['publishedAt'], res['items'][0]['snippet']['channelId']
    except: pass
    return None, None

def update_list_logic(youtube, current_content):
    lines = [l.strip() for l in current_content.split('\n') if l.strip()]
    start_date = DEFAULT_START_DATE
    channel_id = None

    if lines:
        match = re.search(r"(?:v=|shorts/|be/)([^/?&]+)", lines[-1])
        if match:
            last_id = match.group(1)
            pub_date, channel_id = get_video_details(youtube, last_id)
            if pub_date:
                dt = datetime.strptime(pub_date, "%Y-%m-%dT%H:%M:%SZ") + timedelta(seconds=1)
                start_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    if not channel_id:
        ref_id = re.search(r"(?:v=|shorts/|be/)([^/?&]+)", VIDEO_URL_REF).group(1)
        _, channel_id = get_video_details(youtube, ref_id)

    new_links = []
    next_page = None
    while True:
        res = youtube.search().list(part="id", channelId=channel_id, maxResults=50, type="video", 
                                    publishedAfter=start_date, order="date", pageToken=next_page).execute()
        v_ids = [item['id']['videoId'] for item in res.get('items', [])]
        if v_ids:
            details = youtube.videos().list(part="contentDetails", id=",".join(v_ids)).execute()
            for item in details.get('items', []):
                dur = isodate.parse_duration(item['contentDetails']['duration']).total_seconds()
                if dur < 60:
                    new_links.append(f"https://www.youtube.com/watch?v={item['id']}")
        next_page = res.get('nextPageToken')
        if not next_page: break

    new_links.reverse()
    updated_lines = lines + new_links
    return "\n".join(updated_lines)

# --- التحميل والنشر ---

def download_with_cookies(url):
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': TEMP_VIDEO,
        'cookiefile': COOKIES_FILE,
        'quiet': True,
        'no_warnings': True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            ydl.download([url])
            return True
        except Exception as e:
            print(f"Error downloading: {e}")
            return False

async def main_process():
    if not all([YOUTUBE_API_KEY, GITHUB_TOKEN, REPO_NAME, TELEGRAM_SESSION]):
        print("Error: Missing Environment Variables (Secrets).")
        return

    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    
    print("Fetching file from GitHub...")
    content, sha = get_github_file()
    updated_content = update_list_logic(youtube, content)
    
    lines = [l.strip() for l in updated_content.split('\n') if l.strip()]
    if not lines:
        print("No videos found.")
        return

    target_url = lines[0]
    if download_with_cookies(target_url):
        print(f"Video {target_url} downloaded. Posting to Telegram Story...")
        
        client = TelegramClient(StringSession(TELEGRAM_SESSION), 
                                int(TELEGRAM_API_ID), 
                                TELEGRAM_API_HASH)
        
        await client.start()
        file = await client.upload_file(TEMP_VIDEO, file_name='video.mp4')
        await client(SendStoryRequest(
            peer='me',
            media=InputMediaUploadedDocument(
                file=file, mime_type='video/mp4',
                attributes=[DocumentAttributeFilename('video.mp4'), DocumentAttributeVideo(duration=0, w=0, h=0)]
            ),
            caption='', privacy_rules=[InputPrivacyValueAllowAll()]
        ))
        await client.disconnect()
        
        # تحديث GitHub بحذف الرابط الأول
        final_content = "\n".join(lines[1:])
        if update_github_file(final_content, sha, "Update: Processed 1 video & synced links"):
            print("GitHub updated and processed link removed.")
        
        if os.path.exists(TEMP_VIDEO): os.remove(TEMP_VIDEO)
    else:
        print("Download failed.")

if __name__ == '__main__':
    asyncio.run(main_process())
