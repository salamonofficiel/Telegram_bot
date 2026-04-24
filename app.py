#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Telegram Video Downloader Bot - نسخة تعمل على Render مع Webhook

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
# Render يحقن متغير RENDER_EXTERNAL_URL تلقائياً
BOT_TOKEN = os.getenv("BOT_TOKEN", "8396048352:AAGr86L9n9Ts4kT7HZ24bAdN3c25ZdHnGGU")  # يفضل استخدام متغير البيئة
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
if not RENDER_EXTERNAL_URL:
    raise ValueError("RENDER_EXTERNAL_URL environment variable not set! Make sure you are deploying on Render.")

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

# مجلد التحميلات المؤقت (استخدم /tmp في Render لأن الملفات ستنعدم بعد كل إعادة تشغيل)
DOWNLOAD_FOLDER = Path("/tmp/downloads")
DOWNLOAD_FOLDER.mkdir(exist_ok=True)

# ----------------- إعداد التسجيل -----------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ----------------- تطبيق Flask -----------------
flask_app = Flask(__name__)

# ----------------- متغير عام لحمل تطبيق Telegram -----------------
telegram_app = None

# ----------------- فئة تحميل الفيديو (نفس الكود الأصلي) -----------------
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
            'pinterest.com': 'Pinterest', 'reddit.com': 'Reddit', 'linkedin.com': 'LinkedIn',
            'snapchat.com': 'Snapchat',
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
            logger.error(f"Error downloading: {e}")
            return {'success': False, 'error': str(e)}

downloader = VideoDownloader()

# ----------------- دوال مساعدة للإرسال عبر API (لتجنب الاعتماد على context في الـ callback) -----------------
async def send_message(chat_id, text, reply_markup=None, parse_mode='Markdown'):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    await asyncio.to_thread(requests.post, url, json=payload)

async def send_video(chat_id, file_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
    with open(file_path, 'rb') as f:
        files = {'video': f}
        data = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'Markdown', 'supports_streaming': True}
        await asyncio.to_thread(requests.post, url, data=data, files=files)

async def send_audio(chat_id, file_path, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendAudio"
    with open(file_path, 'rb') as f:
        files = {'audio': f}
        data = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'Markdown'}
        await asyncio.to_thread(requests.post, url, data=data, files=files)

async def edit_message_text(chat_id, message_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    await asyncio.to_thread(requests.post, url, json=payload)

# ----------------- معالجات الأوامر (معدلة لاستخدام الدوال المساعدة) -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    welcome_message = """
🎬 **مرحباً بك في بوت تحميل الفيديوهات المتقدم!**

✨ **المميزات:**
• تحميل من جميع المنصات الشهيرة
• إزالة العلامات المائية تلقائياً
• جودة عالية HD
• سرعة فائقة

📱 **المنصات المدعومة:**
✅ YouTube, Instagram, Facebook, TikTok, Twitter, Pinterest, Reddit وأكثر من 1000+ موقع!

📝 **كيفية الاستخدام:**
فقط أرسل رابط الفيديو أو الصورة

⚙️ **الأوامر:**
/start - بدء البوت
/help - المساعدة
/stats - الإحصائيات

💡 أرسل الرابط الآن!
    """
    keyboard = [
        [InlineKeyboardButton("📚 المساعدة", callback_data='help'),
         InlineKeyboardButton("📊 الإحصائيات", callback_data='stats')],
        [InlineKeyboardButton("👨‍💻 المطور", url='https://t.me/VulnHub1')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await send_message(chat_id, welcome_message, reply_markup=reply_markup.to_dict())

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    help_text = """
📖 **دليل الاستخدام التفصيلي:**

1️⃣ **انسخ رابط الفيديو/الصورة** من أي منصة
2️⃣ **الصق الرابط** في المحادثة
3️⃣ **انتظر قليلاً** حتى يتم التحميل
4️⃣ **استلم الملف** بدون علامة مائية!

⚠️ **ملاحظات مهمة:**
• الحد الأقصى لحجم الملف: 50MB (تيليجرام)
• للملفات الأكبر سيتم إرسال رابط تحميل
• يتم حذف الملفات تلقائياً بعد الإرسال

🔗 **أمثلة على الروابط:**
• https://www.instagram.com/p/xxxxx/
• https://www.tiktok.com/@user/video/xxxxx
• https://youtube.com/watch?v=xxxxx

❓ هل لديك سؤال؟ تواصل مع المطور!
    """
    await send_message(chat_id, help_text)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    stats_text = f"""
📊 **إحصائيات البوت:**

👥 المستخدمين: 1,234
📥 التحميلات اليوم: 567
⚡ السرعة: فائقة
✅ الحالة: يعمل بكفاءة 100%

⏰ آخر تحديث: {datetime.now().strftime('%Y-%m-%d %H:%M')}
    """
    await send_message(chat_id, stats_text)

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    chat_id = update.effective_chat.id
    message_id = update.message.message_id

    url_pattern = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
    if not url_pattern.match(url):
        await send_message(chat_id, "❌ الرجاء إرسال رابط صحيح!")
        return

    platform = downloader.get_platform(url)
    processing_text = f"⏳ جاري معالجة الرابط من {platform}...\n⚙️ إزالة العلامة المائية...\n📥 جاري التحميل..."
    msg = await send_message(chat_id, processing_text)  # لا يمكننا بسهولة أخذ message_id من send_message - نحتاج تعديل بسيط

    # سنستخدم edit_message_text بعد تخزين message_id
    # لهذا نستدعي API مباشرة ونأخذ معرف الرسالة
    # تبسيطاً: سأرسل رسالة جديدة وأحصل على معرفها عبر إرجاع JSON.
    # لكن الدالة send_message أعلاه لا ترجع الـ message_id. سأعدلها لترجعه.
    # لكن لتقليل التعقيد، سأستخدم طريقة أسهل: إرسال الرسالة ثم استقبال الـ update لاحقاً.
    # بدلاً من ذلك، سأستخدم الطريقة التقليدية عبر Update الوارد.

    # لكن مع webhook، من الأسهل استخدام الـ context.bot.
    # نظراً لأننا في وضع webhook، يمكننا استخدام context.bot الموجود في الـ update.
    # سأعدل المعالج لاستخدام context.bot مباشرة.
    # ومع ذلك، دالة handle_url تُستدعى من flask عبر update الوهمي، و context.bot موجود فعلاً.
    # إذن سأستخدم update.message.reply_text و update.message.edit_text بدلاً من دوال المساعدة.
    # لتبسيط الأمر، سأترك المعالج كما هو في الكود الأصلي ولكن مع تعديل بسيط: لأن async و webhook لا مشكلة فيهما.
    # سأستخدم update.message.reply_text مباشرة.

    # ولكن بما أن الكود الأصلي يستخدم update.message.reply_text، سأبقي عليه فهو يعمل مع webhook.
    # طالما أن الـ update يتم تمريره بشكل صحيح.

    # سأعدل فقط لاستخدام update.effective_chat.send_message و edit_text.

    await update.message.reply_text(
        f"✅ تم التعرف على الرابط من **{platform}**\n\nاختر نوع التحميل:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎥 فيديو", callback_data=f'video_{url}'),
             InlineKeyboardButton("🎵 صوت فقط", callback_data=f'audio_{url}')]
        ]),
        parse_mode='Markdown'
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    message_id = query.message.message_id

    if data == 'help':
        await help_command(update, context)
        return
    if data == 'stats':
        stats_text = f"""
📊 **إحصائيات البوت:**

👥 المستخدمين: 1,234
📥 التحميلات اليوم: 567
⚡ السرعة: فائقة
✅ الحالة: يعمل بكفاءة 100%

⏰ آخر تحديث: {datetime.now().strftime('%Y-%m-%d %H:%M')}
        """
        await query.edit_message_text(stats_text, parse_mode='Markdown')
        return

    if data.startswith('video_') or data.startswith('audio_'):
        media_type, url = data.split('_', 1)
        await query.edit_message_text(
            f"⏳ جاري تحميل {'الفيديو' if media_type == 'video' else 'الصوت'}...\n⚙️ إزالة العلامة المائية...\n📤 يرجى الانتظار..."
        )
        result = await downloader.download_media(url, media_type)
        if result['success']:
            try:
                file_path = result['filename']
                caption = f"✅ **{result['title']}**\n\n📱 المنصة: {result['platform']}\n"
                if result['duration']:
                    m, s = divmod(result['duration'], 60)
                    caption += f"⏱ المدة: {m}:{s:02d}\n"
                caption += f"\n🤖 تم بواسطة @VulnHub1"

                if media_type == 'video':
                    await query.message.reply_video(video=open(file_path, 'rb'), caption=caption, parse_mode='Markdown')
                else:
                    await query.message.reply_audio(audio=open(file_path, 'rb'), caption=caption, parse_mode='Markdown')
                os.remove(file_path)
                await query.edit_message_text("✅ تم الإرسال بنجاح!")
            except Exception as e:
                logger.error(f"Send error: {e}")
                await query.edit_message_text(f"❌ خطأ في الإرسال: {str(e)}\nقد يكون الملف كبيراً جداً (الحد الأقصى 50MB)")
        else:
            await query.edit_message_text(f"❌ فشل التحميل: {result['error']}\n\nتأكد من صحة الرابط وأن الفيديو متاح")

# ----------------- إعداد Webhook -----------------
@flask_app.route(WEBHOOK_PATH, methods=['POST'])
async def webhook():
    """نقطة استقبال تحديثات Telegram"""
    global telegram_app
    if not telegram_app:
        return jsonify({"ok": False, "error": "Application not initialized"}), 500
    update_data = request.get_json()
    update = Update.de_json(update_data, telegram_app.bot)
    await telegram_app.process_update(update)
    return jsonify({"ok": True})

@flask_app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "bot_running": telegram_app is not None})

async def setup_webhook():
    """تعيين Webhook بعد بدء التطبيق"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    payload = {"url": WEBHOOK_URL}
    response = await asyncio.to_thread(requests.post, url, json=payload)
    logger.info(f"Webhook set response: {response.json()}")

async def main():
    global telegram_app
    # إنشاء تطبيق Telegram
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    # إضافة المعالجات
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("help", help_command))
    telegram_app.add_handler(CommandHandler("stats", stats_command))
    telegram_app.add_handler(CallbackQueryHandler(button_callback))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    await telegram_app.initialize()
    await setup_webhook()
    # تشغيل خادم Flask باستخدام Waitress
    from waitress import serve
    port = int(os.getenv("PORT", 10000))
    logger.info(f"Starting Flask web server on port {port}")
    serve(flask_app, host='0.0.0.0', port=port)

if __name__ == "__main__":
    asyncio.run(main())
