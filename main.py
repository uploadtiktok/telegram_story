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

# --- الإعدادات ---
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

def sync_youtube_links(youtube, current_content):
    lines = [l.strip() for l in current_content.split('\n') if l.strip()]
    # تاريخ افتراضي للبحث إذا كان الملف فارغاً
    start_date = "2026-03-11T00:00:00Z"
    channel_id = None
    
    try:
        # تحديد معرف القناة من الفيديو المرجعي
        ref_id = re.search(r"(?:v=|shorts/|be/)([^/?&]+)", VIDEO_URL_REF).group(1)
        ref_res = youtube.videos().list(part="snippet", id=ref_id).execute()
        channel_id = ref_res['items'][0]['snippet']['channelId']

        # إذا كان هناك روابط قديمة، نبدأ البحث من تاريخ آخر فيديو موجود
        if lines:
            last_url = lines[-1]
            last_id = re.search(r"(?:v=|shorts/|be/)([^/?&]+)", last_url).group(1)
            res = youtube.videos().list(part="snippet", id=last_id).execute()
            if res['items']:
                pub_date = res['items'][0]['snippet']['publishedAt']
                dt = datetime.strptime(pub_date, "%Y-%m-%dT%H:%M:%SZ") + timedelta(seconds=1)
                start_date = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except:
        pass

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
                    if dur < 60: # فقط فيديوهات أقل من دقيقة (Shorts)
                        new_links.append(f"https://www.youtube.com/watch?v={item['id']}")
            next_page = res.get('nextPageToken')
            if not next_page: break
        except: break
        
    new_links.reverse() # لترتيبها من الأقدم للأحدث في القائمة
    return lines + new_links

def download_video(url):
    command = [
        "yt-dlp", "--cookies", COOKIES_FILE, "--js-runtime", "node",
        "--remote-components", "ejs:github", "-f", 
        "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "--merge-output-format", "mp4", "-o", TEMP_VIDEO, url
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
        return result.returncode == 0 and os.path.exists(TEMP_VIDEO)
    except: return False

async def main():
    if not create_cookies_file(): return

    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    content, sha = get_github_file()
    
    # الخطوة الأهم: جلب الروابط الجديدة ودمجها مع القديمة
    print("🔄 Syncing with YouTube channel...")
    updated_links = sync_youtube_links(youtube, content)
    
    if not updated_links:
        print("ℹ️ No links to process.")
        return

    target_url = updated_links[0]
    print(f"🎬 Current target: {target_url}")

    if download_video(target_url):
        try:
            client = TelegramClient(StringSession(TELEGRAM_SESSION), int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
            await client.start()
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
            
            # تحديث الملف بحذف الرابط الذي تم رفعه وحفظ الروابط الجديدة المكتشفة
            new_content = "\n".join(updated_links[1:])
            update_github_file(new_content, sha, "Auto-sync and posted 1 story")
            print("✅ Process completed successfully.")
        except Exception as e:
            print(f"❌ Telegram Error: {e}")
    
    if os.path.exists(TEMP_VIDEO): os.remove(TEMP_VIDEO)
    if os.path.exists(COOKIES_FILE): os.remove(COOKIES_FILE)

if __name__ == '__main__':
    asyncio.run(main())
