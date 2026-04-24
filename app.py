#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Telegram Video Downloader Bot - يعمل على Render مع Webhook + Gunicorn

import os
import re
import asyncio
import logging
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import yt_dlp
import requests

# ----------------- إعدادات بيئة التشغيل -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set!")

# تحديد رابط الخدمة: استخدم متغير Render إن وُجد، وإلا استخدم اسم الخدمة الافتراضي
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
if not RENDER_EXTERNAL_URL:
    SERVICE_NAME = "media-bot"   # غيّر هذا إلى اسم خدمتك على Render
    RENDER_EXTERNAL_URL = f"https://{SERVICE_NAME}.onrender.com"

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

# مجلد التحميلات المؤقت (سيُحذف تلقائياً بعد كل إعادة تشغيل)
DOWNLOAD_FOLDER = Path("/tmp/downloads")
DOWNLOAD_FOLDER.mkdir(exist_ok=True)

# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ----------------- تطبيق Flask -----------------
flask_app = Flask(__name__)

# ----------------- متغير عام لحمل تطبيق Telegram -----------------
telegram_app = None

# ----------------- فئة تحميل الفيديو -----------------
class VideoDownloader:
    def __init__(self):
        self.ydl_opts_video = {
            'format': 'best[ext=mp4]/best',
            'outtmpl': str(DOWNLOAD_FOLDER / '%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'geo_bypass': True,
            'nocheckcertificate': True,
        }
        self.ydl_opts_audio = {
            'format': 'bestaudio/best',
            'outtmpl': str(DOWNLOAD_FOLDER / '%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }

    def get_platform(self, url):
        platforms = {
            'youtube.com': 'YouTube', 'youtu.be': 'YouTube',
            'instagram.com': 'Instagram', 'facebook.com': 'Facebook', 'fb.watch': 'Facebook',
            'twitter.com': 'Twitter', 'x.com': 'Twitter',
            'tiktok.com': 'TikTok', 'vm.tiktok.com': 'TikTok', 'vt.tiktok.com': 'TikTok',
            'pinterest.com': 'Pinterest', 'reddit.com': 'Reddit',
        }
        for domain, platform in platforms.items():
            if domain in url:
                return platform
        return 'Unknown'

    async def download_media(self, url, media_type='video'):
        try:
            opts = self.ydl_opts_video if media_type == 'video' else self.ydl_opts_audio
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if media_type == 'video':
                    filename = ydl.prepare_filename(info)
                else:
                    filename = ydl.prepare_filename(info).rsplit('.', 1)[0] + '.mp3'
                return {
                    'success': True,
                    'filename': filename,
                    'title': info.get('title', 'Unknown'),
                    'duration': info.get('duration', 0),
                    'thumbnail': info.get('thumbnail', None),
                    'platform': self.get_platform(url)
                }
        except Exception as e:
            logger.error(f"Download error: {e}")
            return {'success': False, 'error': str(e)}

downloader = VideoDownloader()

# ----------------- دوال مساعدة للإرسال (اختيارية، غير مستخدمة مباشرة، لكن قد تفيد) -----------------
async def send_message(chat_id, text, reply_markup=None, parse_mode='Markdown'):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    await asyncio.to_thread(requests.post, url, json=payload)

# ----------------- معالجات الأوامر -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = """
🎬 **مرحباً بك في بوت تحميل الفيديوهات المتقدم!**

✨ **المميزات:**
• تحميل من جميع المنصات الشهيرة
• إزالة العلامات المائية تلقائياً
• جودة عالية HD

📱 **المنصات المدعومة:**
YouTube, Instagram, Facebook, TikTok, Twitter, Pinterest, Reddit

📝 **كيفية الاستخدام:**
فقط أرسل رابط الفيديو أو الصورة

⚙️ **الأوامر:**
/start - بدء البوت
/help - المساعدة
/stats - الإحصائيات
    """
    keyboard = [
        [InlineKeyboardButton("📚 المساعدة", callback_data='help'),
         InlineKeyboardButton("📊 الإحصائيات", callback_data='stats')],
        [InlineKeyboardButton("👨‍💻 المطور", url='https://t.me/VulnHub1')]
    ]
    await update.message.reply_text(welcome_message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
📖 **دليل الاستخدام:**

1️⃣ أرسل رابط الفيديو
2️⃣ اختر فيديو أو صوت
3️⃣ انتظر التحميل والإرسال

⚠️ **ملاحظات:**
• الحد الأقصى للحجم: 50 ميجابايت
• الملفات تُحذف بعد الإرسال
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats_text = f"""
📊 **إحصائيات البوت:**

✅ الحالة: يعمل
⏰ آخر تحديث: {datetime.now().strftime('%Y-%m-%d %H:%M')}
    """
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not re.match(r'https?://', url):
        await update.message.reply_text("❌ الرجاء إرسال رابط صحيح يبدأ بـ http:// أو https://")
        return

    platform = downloader.get_platform(url)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎥 فيديو", callback_data=f'video_{url}'),
         InlineKeyboardButton("🎵 صوت فقط", callback_data=f'audio_{url}')]
    ])
    await update.message.reply_text(
        f"✅ تم التعرف على الرابط من **{platform}**\nاختر نوع التحميل:",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == 'help':
        await help_command(update, context)
        return
    if data == 'stats':
        stats_text = f"📊 الإحصائيات:\n✅ يعمل\n🕒 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        await query.edit_message_text(stats_text, parse_mode='Markdown')
        return

    if data.startswith('video_') or data.startswith('audio_'):
        media_type, url = data.split('_', 1)
        await query.edit_message_text(f"⏳ جاري تحميل {'الفيديو' if media_type == 'video' else 'الصوت'}...")
        result = await downloader.download_media(url, media_type)
        if result['success']:
            try:
                file_path = result['filename']
                caption = f"✅ **{result['title']}**\n📱 المنصة: {result['platform']}\n🤖 تم بواسطة @VulnHub1"
                if media_type == 'video':
                    with open(file_path, 'rb') as f:
                        await query.message.reply_video(video=f, caption=caption, parse_mode='Markdown')
                else:
                    with open(file_path, 'rb') as f:
                        await query.message.reply_audio(audio=f, caption=caption, parse_mode='Markdown')
                os.remove(file_path)
                await query.edit_message_text("✅ تم الإرسال بنجاح!")
            except Exception as e:
                logger.error(f"Send error: {e}")
                await query.edit_message_text(f"❌ خطأ في الإرسال: قد يكون الملف أكبر من 50 ميجابايت")
        else:
            await query.edit_message_text(f"❌ فشل التحميل: {result.get('error', 'خطأ غير معروف')}")

# ----------------- إعداد Webhook -----------------
@flask_app.route(WEBHOOK_PATH, methods=['POST'])
async def webhook():
    if not telegram_app:
        return jsonify({"ok": False, "error": "App not initialized"}), 500
    update_data = request.get_json()
    update = Update.de_json(update_data, telegram_app.bot)
    await telegram_app.process_update(update)
    return jsonify({"ok": True})

@flask_app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "bot_running": telegram_app is not None})

async def setup_webhook():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    payload = {"url": WEBHOOK_URL}
    response = await asyncio.to_thread(requests.post, url, json=payload)
    logger.info(f"Webhook set response: {response.json()}")

async def init_bot():
    global telegram_app
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("help", help_command))
    telegram_app.add_handler(CommandHandler("stats", stats_command))
    telegram_app.add_handler(CallbackQueryHandler(button_callback))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    await telegram_app.initialize()
    await setup_webhook()
    logger.info("Bot initialized and webhook set.")

# ----------------- تشغيل الخادم -----------------
# عند استخدام Gunicorn، سيتم استدعاء هذا الملف، ولن نستخدم asyncio.run() بشكل مباشر.
# بدلاً من ذلك، نقوم بتهيئة البوت عند تحميل التطبيق.
import asyncio
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
loop.run_until_complete(init_bot())

# تطبيق Flask جاهز (Gunicorn سيتولى تشغيله)
# لا حاجة لاستدعاء flask_app.run()
