#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Telegram Media Downloader Bot - نسخة تعمل على Render مع Webhook

import os
import re
import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime

import yt_dlp
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from flask import Flask, request, jsonify
import requests

# إعداد نظام تسجيل الأخطاء
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# إعدادات المسارات
DOWNLOAD_DIR = Path("/tmp/downloads")  # استخدم /tmp في Render
DOWNLOAD_DIR.mkdir(exist_ok=True)

# متغيرات البيئة
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set!")

RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
if not RENDER_EXTERNAL_URL:
    raise ValueError("RENDER_EXTERNAL_URL not set! Make sure you're deploying on Render.")

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

# تطبيق Flask لاستقبال Webhook
flask_app = Flask(__name__)

# تطبيق Telegram (سيتم تهيئته لاحقاً)
telegram_app = None

# ----------------- دوال مساعدة -----------------
def is_valid_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False

async def cleanup_old_files(directory: Path, max_age_hours: int = 24):
    now = datetime.now()
    for file_path in directory.glob("*"):
        if file_path.is_file():
            file_age = now - datetime.fromtimestamp(file_path.stat().st_mtime)
            if file_age.total_seconds() > max_age_hours * 3600:
                try:
                    file_path.unlink()
                    logger.info(f"Deleted old file: {file_path}")
                except Exception as e:
                    logger.error(f"Failed to delete {file_path}: {e}")

async def download_media(url: str, chat_id: int, is_audio: bool = False, progress_callback=None) -> str:
    output_template = "%(title)s.%(ext)s"
    download_path = DOWNLOAD_DIR / f"{chat_id}"
    download_path.mkdir(exist_ok=True)

    ydl_opts = {
        'outtmpl': str(download_path / output_template),
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'extract_flat': False,
    }

    if is_audio:
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    else:
        ydl_opts.update({
            'format': 'best[height<=720]/best',
        })

    if progress_callback:
        def progress_hook(d):
            if d['status'] == 'downloading':
                try:
                    percent_str = d.get('_percent_str', '0%').strip('%')
                    if '%' in percent_str:
                        percent_str = percent_str.replace('%', '')
                    try:
                        percent = float(percent_str)
                        if percent % 10 < 1:  # كل 10%
                            asyncio.create_task(progress_callback(f"🔄 جاري التحميل: {percent:.1f}%"))
                    except:
                        pass
                except:
                    pass
            elif d['status'] == 'finished':
                asyncio.create_task(progress_callback("✅ تم التحميل! جاري المعالجة..."))
        ydl_opts['progress_hooks'] = [progress_hook]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                return None
            filename = ydl.prepare_filename(info)
            if is_audio:
                filename = filename.rsplit('.', 1)[0] + '.mp3'
            return filename
    except Exception as e:
        logger.error(f"Error downloading {url}: {e}")
        return None

async def send_message(chat_id: int, text: str):
    """إرسال رسالة عبر API مباشرة (بدون context)."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        await asyncio.to_thread(requests.post, url, json=payload)
    except Exception as e:
        logger.error(f"Failed to send message: {e}")

async def send_video(chat_id: int, file_path: str, caption: str = ""):
    """إرسال فيديو عبر API مباشرة."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
    with open(file_path, 'rb') as f:
        files = {'video': f}
        data = {'chat_id': chat_id, 'caption': caption, 'supports_streaming': True}
        await asyncio.to_thread(requests.post, url, data=data, files=files)

async def send_audio(chat_id: int, file_path: str, title: str):
    """إرسال صوت عبر API مباشرة."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendAudio"
    with open(file_path, 'rb') as f:
        files = {'audio': f}
        data = {'chat_id': chat_id, 'title': title, 'performer': "Media Bot"}
        await asyncio.to_thread(requests.post, url, data=data, files=files)

# ----------------- معالجات الأوامر -----------------
async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "🎬 **مرحباً بك في بوت تحميل الفيديوهات!**\n\n"
        "أرسل لي رابطاً من أي منصة وسأقوم بتحميل الفيديو لك.\n\n"
        "**الأوامر المتاحة:**\n"
        "/start - عرض هذه الرسالة\n"
        "/audio <رابط> - تحميل الصوت فقط بصيغة MP3\n"
        "/help - عرض المساعدة",
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "📌 **كيفية الاستخدام:**\n"
        "1. أرسل رابط الفيديو\n"
        "2. استخدم /audio قبل الرابط لتحميل الصوت فقط\n\n"
        "**المنصات المدعومة:**\n"
        "YouTube, Instagram, TikTok, Twitter, Facebook, Reddit, Twitch, Vimeo, SoundCloud",
        parse_mode='Markdown'
    )

async def handle_audio(update: Update, context: CallbackContext) -> None:
    if not context.args:
        await update.message.reply_text("⚠️ يرجى إرسال رابط الفيديو بعد الأمر /audio")
        return
    url = context.args[0]
    if not is_valid_url(url):
        await update.message.reply_text("❌ الرابط غير صالح.")
        return

    chat_id = update.effective_chat.id
    await update.message.reply_text("🎵 جاري تحميل الصوت...")
    
    async def progress(msg):
        await send_message(chat_id, msg)
    
    file_path = await download_media(url, chat_id, is_audio=True, progress_callback=progress)
    if file_path and Path(file_path).exists():
        try:
            await send_audio(chat_id, file_path, Path(file_path).stem)
            await send_message(chat_id, "✅ تم إرسال الصوت بنجاح!")
        except Exception as e:
            await send_message(chat_id, f"❌ خطأ: {str(e)[:100]}")
        finally:
            try:
                Path(file_path).unlink()
            except:
                pass
    else:
        await send_message(chat_id, "❌ فشل تحميل الصوت.")

async def handle_message(update: Update, context: CallbackContext) -> None:
    text = update.message.text.strip()
    if not is_valid_url(text):
        await update.message.reply_text("❌ يرجى إرسال رابط صحيح.")
        return

    chat_id = update.effective_chat.id
    await update.message.reply_text("🎬 جاري تحميل الفيديو...")
    
    async def progress(msg):
        await send_message(chat_id, msg)
    
    file_path = await download_media(text, chat_id, is_audio=False, progress_callback=progress)
    if file_path and Path(file_path).exists():
        try:
            await send_video(chat_id, file_path)
            await send_message(chat_id, "✅ تم إرسال الفيديو بنجاح!")
        except Exception as e:
            await send_message(chat_id, f"❌ خطأ: {str(e)[:100]}")
        finally:
            try:
                Path(file_path).unlink()
            except:
                pass
    else:
        await send_message(chat_id, "❌ فشل تحميل الفيديو.")

# ----------------- إعداد Webhook -----------------
@flask_app.route(WEBHOOK_PATH, methods=["POST"])
async def webhook():
    """استقبال التحديثات من Telegram."""
    if not telegram_app:
        return jsonify({"ok": False, "error": "App not initialized"}), 500
    
    update_data = request.get_json()
    update = Update.de_json(update_data, telegram_app.bot)
    await telegram_app.process_update(update)
    return jsonify({"ok": True})

@flask_app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "bot_running": telegram_app is not None})

async def setup_webhook():
    """تعيين Webhook عند بدء التشغيل."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    payload = {"url": WEBHOOK_URL}
    response = await asyncio.to_thread(requests.post, url, json=payload)
    logger.info(f"Webhook set response: {response.json()}")

# ----------------- التشغيل الرئيسي -----------------
async def main():
    global telegram_app
    
    # إنشاء تطبيق Telegram
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    
    # إضافة معالجات الأوامر
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("help", help_command))
    telegram_app.add_handler(CommandHandler("audio", handle_audio))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    await telegram_app.initialize()
    await setup_webhook()
    
    # تشغيل Flask باستخدام waiter (مناسب لـ Render)
    from waitress import serve
    logger.info(f"Starting web server on port {os.getenv('PORT', 10000)}")
    serve(flask_app, host='0.0.0.0', port=int(os.getenv('PORT', 10000)))

if __name__ == "__main__":
    asyncio.run(main())
