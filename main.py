import os
import re
import asyncio
import base64
import requests
import isodate
import yt_dlp
from googleapiclient.discovery import build
from datetime import datetime, timedelta
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.stories import SendStoryRequest
from telethon.tl.types import InputMediaUploadedDocument, DocumentAttributeFilename, DocumentAttributeVideo, InputPrivacyValueAllowAll

# --- الإعدادات المسحوبة من Secrets ---
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
GH_TOKEN = os.getenv("GH_TOKEN")
TELEGRAM_API_ID = os.getenv("TG_API_ID")
TELEGRAM_API_HASH = os.getenv("TG_API_HASH")
TELEGRAM_SESSION = os.getenv("TG_SESSION")
COOKIES_B64 = os.getenv("COOKIES_B64") # الملف المشفر

REPO_NAME = "uploadtiktok/telegram_story"
VIDEO_URL_REF = "https://youtube.com/shorts/oG3WdBgRgxo?si=sMYhFhPMkB8DtYyF"
DEFAULT_START_DATE = "2026-03-11T00:00:00Z"
FILE_PATH = "youtube_shorts_links.txt"
BRANCH = "main"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_VIDEO = os.path.join(BASE_DIR, "temp_video.mp4")
COOKIES_FILE = os.path.join(BASE_DIR, "cookies.txt")

# --- وظيفة إعادة بناء ملف الكوكيز ---
def create_cookies_file():
    if COOKIES_B64:
        try:
            with open(COOKIES_FILE, "wb") as f:
                f.write(base64.b64decode(COOKIES_B64))
            print("Cookies file created from Secret.")
            return True
        except Exception as e:
            print(f"Error decoding cookies: {e}")
    return False

# --- وظائف GitHub API ---
def get_github_file():
    url = f"https://api.github.com/repos/{REPO_NAME}/contents/{FILE_PATH}"
    headers = {"Authorization": f"token {GH_TOKEN}"}
    res = requests.get(url, headers=headers)
    if res.status_code == 200:
        data = res.json()
        content = base64.b64decode(data['content']).decode('utf-8')
        return content, data['sha']
    return "", None

def update_github_file(new_content, sha, message):
    url = f"https://api.github.com/repos/{REPO_NAME}/contents/{FILE_PATH}"
    headers = {"Authorization": f"token {GH_TOKEN}"}
    encoded_content = base64.b64encode(new_content.encode('utf-8')).decode('utf-8')
    data = {"message": message, "content": encoded_content, "sha": sha, "branch": BRANCH}
    res = requests.put(url, headers=headers, json=data)
    return res.status_code in [200, 201]

# --- وظائف اليوتيوب ---
def sync_youtube_links(youtube, current_content):
    lines = [l.strip() for l in current_content.split('\n') if l.strip()]
    start_date = DEFAULT_START_DATE
    channel_id = None
    
    try:
        if lines:
            last_id = re.search(r"(?:v=|shorts/|be/)([^/?&]+)", lines[-1]).group(1)
            res = youtube.videos().list(part="snippet", id=last_id).execute()
            if res['items']:
                pub_date = res['items'][0]['snippet']['publishedAt']
                channel_id = res['items'][0]['snippet']['channelId']
                dt = datetime.strptime(pub_date, "%Y-%m-%dT%H:%M:%SZ") + timedelta(seconds=1)
                start_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        if not channel_id:
            ref_id = re.search(r"(?:v=|shorts/|be/)([^/?&]+)", VIDEO_URL_REF).group(1)
            res = youtube.videos().list(part="snippet", id=ref_id).execute()
            channel_id = res['items'][0]['snippet']['channelId']
    except:
        return lines

    new_links = []
    next_page = None
    while True:
        try:
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
        except: break
        
    new_links.reverse()
    return lines + new_links

# --- التحميل والرفع ---
def download_video(url):
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': TEMP_VIDEO,
        'cookiefile': COOKIES_FILE if os.path.exists(COOKIES_FILE) else None,
        'quiet': True,
        'no_warnings': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            ydl.download([url])
            return os.path.exists(TEMP_VIDEO)
        except Exception as e:
            print(f"Download Error: {e}")
            return False

async def main():
    if not all([YOUTUBE_API_KEY, GH_TOKEN, TELEGRAM_SESSION]):
        print("Missing core Secrets.")
        return

    # 1. إنشاء ملف الكوكيز مؤقتاً
    create_cookies_file()

    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    content, sha = get_github_file()
    all_links = sync_youtube_links(youtube, content)
    
    if all_links:
        target_url = all_links[0]
        print(f"Processing: {target_url}")

        if download_video(target_url):
            try:
                client = TelegramClient(StringSession(TELEGRAM_SESSION), int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
                await client.start()
                file_to_upload = await client.upload_file(TEMP_VIDEO, file_name='video.mp4')
                await client(SendStoryRequest(
                    peer='me',
                    media=InputMediaUploadedDocument(
                        file=file_to_upload, mime_type='video/mp4',
                        attributes=[DocumentAttributeFilename('video.mp4'), DocumentAttributeVideo(duration=0, w=0, h=0)]
                    ),
                    caption='', privacy_rules=[InputPrivacyValueAllowAll()]
                ))
                await client.disconnect()
                
                update_github_file("\n".join(all_links[1:]), sha, "Auto-update: Processed 1 story")
                print("Success!")
            except Exception as e:
                print(f"Update Error: {e}")
        
        if os.path.exists(TEMP_VIDEO): os.remove(TEMP_VIDEO)
    
    # 2. حذف ملف الكوكيز بعد الانتهاء
    if os.path.exists(COOKIES_FILE): os.remove(COOKIES_FILE)

if __name__ == '__main__':
    asyncio.run(main())
