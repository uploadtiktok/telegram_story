import os
import re
import asyncio
import base64
import requests
import isodate
import subprocess
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
COOKIES_TEXT = os.getenv("COOKIES_TEXT") 

REPO_NAME = "uploadtiktok/telegram_story"
VIDEO_URL_REF = "https://youtube.com/shorts/oG3WdBgRgxo"
FILE_PATH = "youtube_shorts_links.txt"
BRANCH = "main"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_VIDEO = os.path.join(BASE_DIR, "temp_video.mp4")
COOKIES_FILE = os.path.join(BASE_DIR, "cookies.txt")

def create_cookies_file():
    if COOKIES_TEXT:
        with open(COOKIES_FILE, "w", encoding="utf-8") as f:
            f.write(COOKIES_TEXT)
        return True
    return False

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

def download_video(url):
    # استخدام نفس الأوامر الدقيقة التي طلبتها
    command = [
        "yt-dlp", 
        "--cookies", COOKIES_FILE, 
        "--js-runtime", "node",
        "--remote-components", "ejs:github", 
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "--merge-output-format", "mp4", 
        "-o", TEMP_VIDEO, 
        url
    ]
    try:
        # تشغيل الأمر عبر subprocess كما في الكود السابق
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0 and os.path.exists(TEMP_VIDEO):
            return True
        else:
            print(f"❌ Download Error: {result.stderr}")
            return False
    except Exception as e:
        print(f"❌ Subprocess Error: {e}")
        return False

async def main():
    if not create_cookies_file():
        print("❌ No cookies found!")
        return

    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    content, sha = get_github_file()
    
    # استخراج الروابط (نفس المنطق السابق)
    lines = [l.strip() for l in content.split('\n') if l.strip()]
    if not lines: return

    target_url = lines[0]
    print(f"🎬 Processing: {target_url}")

    if download_video(target_url):
        try:
            client = TelegramClient(StringSession(TELEGRAM_SESSION), int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
            await client.start()
            
            print("📤 Uploading...")
            file_to_upload = await client.upload_file(TEMP_VIDEO)
            
            await client(SendStoryRequest(
                peer='me',
                media=InputMediaUploadedDocument(
                    file=file_to_upload, mime_type='video/mp4',
                    attributes=[DocumentAttributeFilename('video.mp4'), DocumentAttributeVideo(duration=0, w=0, h=0)]
                ),
                caption='', privacy_rules=[InputPrivacyValueAllowAll()]
            ))
            await client.disconnect()
            
            print("✨ Success!")
            update_github_file("\n".join(lines[1:]), sha, "Processed 1 story")
        except Exception as e:
            print(f"❌ TG Error: {e}")
    
    if os.path.exists(TEMP_VIDEO): os.remove(TEMP_VIDEO)
    if os.path.exists(COOKIES_FILE): os.remove(COOKIES_FILE)

if __name__ == '__main__':
    asyncio.run(main())
