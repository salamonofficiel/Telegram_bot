#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Telegram Media Downloader Bot
# يدعم تحميل الفيديوهات والصوت من يوتيوب، إنستغرام، تيك توك، تويتر، فيسبوك وغيرها
# يعمل على Termux، مع حفظ التوكن تلقائياً

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

# إعداد نظام تسجيل الأخطاء
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# إعدادات المسارات
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)
TOKEN_FILE = Path.home() / ".bot_token"

# ----------------- دوال مساعدة -----------------
def is_valid_url(url: str) -> bool:
    """التحقق من صحة الرابط."""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False

async def cleanup_old_files(directory: Path, max_age_hours: int = 24):
    """حذف الملفات القديمة لتوفير المساحة."""
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

async def download_media(url: str, chat_id: int, is_audio: bool = False, update: Update = None) -> str:
    """
    تحميل الوسائط باستخدام yt-dlp وإرجاع مسار الملف.
    يتم عرض التقدم في حالة وجود كائن update.
    """
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
            'format': 'best[height<=720]/best',  # جودة تصل إلى 720p لضمان حجم معقول
        })

    # إضافة معالج التقدم في حال وجود كائن التحديث
    if update:
        last_progress = {'percent': 0}

        def progress_hook(d):
            if d['status'] == 'downloading':
                try:
                    percent_str = d.get('_percent_str', '0%').strip('%')
                    if '%' in percent_str:
                        percent_str = percent_str.replace('%', '')
                    try:
                        percent_float = float(percent_str)
                        if percent_float - last_progress['percent'] >= 10:
                            last_progress['percent'] = percent_float
                            asyncio.create_task(update.message.reply_text(f"🔄 جاري التحميل: {percent_float:.1f}%"))
                    except ValueError:
                        pass
                except:
                    pass
            elif d['status'] == 'finished':
                asyncio.create_task(update.message.reply_text("✅ تم التحميل! جاري المعالجة والإرسال..."))

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
        if update:
            await update.message.reply_text(f"❌ فشل التحميل: {str(e)[:100]}")
        return None

# ----------------- أوامر البوت -----------------
async def start(update: Update, context: CallbackContext) -> None:
    """رسالة الترحيب."""
    await update.message.reply_text(
        "🎬 **مرحباً بك في بوت تحميل الفيديوهات!**\n\n"
        "أرسل لي رابطاً من أي منصة (إنستغرام، تيك توك، يوتيوب، تويتر، فيسبوك، إلخ) وسأقوم بتحميل الفيديو لك.\n\n"
        "**الأوامر المتاحة:**\n"
        "/start - عرض هذه الرسالة\n"
        "/audio <رابط> - تحميل الصوت فقط بصيغة MP3\n"
        "/help - عرض المساعدة\n\n"
        "🔹 يمكنك أيضاً إرسال الرابط مباشرة لتحميل الفيديو.",
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: CallbackContext) -> None:
    """عرض تعليمات الاستخدام."""
    await update.message.reply_text(
        "📌 **كيفية الاستخدام:**\n"
        "1. أرسل رابط الفيديو (مثل: https://www.instagram.com/p/...)\n"
        "2. انتظر حتى يتم التحميل والإرسال\n"
        "3. استخدم /audio قبل الرابط لتحميل الصوت فقط\n\n"
        "**المنصات المدعومة:**\n"
        "✅ YouTube, Instagram, TikTok, Twitter/X, Facebook, Reddit, Twitch, Vimeo, SoundCloud وغيرها الكثير.\n\n"
        "**ملاحظات:**\n"
        "⚠️ حد تيليجرام الأقصى للملف هو 50MB\n"
        "⚠️ قد يستغرق التحميل بضع ثوانٍ حسب حجم الفيديو وسرعة الإنترنت",
        parse_mode='Markdown'
    )

async def handle_audio(update: Update, context: CallbackContext) -> None:
    """معالجة أمر تحميل الصوت."""
    if not context.args:
        await update.message.reply_text("⚠️ يرجى إرسال رابط الفيديو بعد الأمر /audio\nمثال: /audio https://www.youtube.com/watch?v=...")
        return

    url = context.args[0]
    if not is_valid_url(url):
        await update.message.reply_text("❌ الرابط غير صالح. يرجى التأكد من الرابط وإعادة المحاولة.")
        return

    await update.message.reply_text("🎵 جاري تحميل الصوت... قد يستغرق هذا بضع ثوانٍ.")
    file_path = await download_media(url, update.effective_chat.id, is_audio=True, update=update)

    if file_path and Path(file_path).exists():
        try:
            with open(file_path, 'rb') as audio_file:
                await update.message.reply_audio(audio=audio_file, title=Path(file_path).stem, performer="Media Bot")
            await update.message.reply_text("✅ تم إرسال الصوت بنجاح!")
        except Exception as e:
            await update.message.reply_text(f"❌ حدث خطأ أثناء إرسال الملف: {str(e)[:100]}")
        finally:
            try:
                Path(file_path).unlink()
            except:
                pass
    else:
        await update.message.reply_text("❌ فشل تحميل الصوت. تأكد من أن الرابط صحيح ويدعمه البوت.")

async def handle_message(update: Update, context: CallbackContext) -> None:
    """معالجة الرسائل النصية (الروابط)."""
    text = update.message.text.strip()
    if not is_valid_url(text):
        await update.message.reply_text("❌ يرجى إرسال رابط صحيح يبدأ بـ http:// أو https://")
        return

    await update.message.reply_text("🎬 جاري تحميل الفيديو... قد يستغرق هذا بضع ثوانٍ.")
    file_path = await download_media(text, update.effective_chat.id, is_audio=False, update=update)

    if file_path and Path(file_path).exists():
        try:
            with open(file_path, 'rb') as video_file:
                await update.message.reply_video(video=video_file, supports_streaming=True)
            await update.message.reply_text("✅ تم إرسال الفيديو بنجاح!")
        except Exception as e:
            await update.message.reply_text(f"❌ حدث خطأ أثناء إرسال الملف: {str(e)[:100]}")
        finally:
            try:
                Path(file_path).unlink()
            except:
                pass
    else:
        await update.message.reply_text("❌ فشل تحميل الفيديو. قد يكون الرابط غير مدعوم أو الفيديو خاصاً.")

async def cleanup_job(context: CallbackContext):
    """تنظيف الملفات القديمة بشكل دوري."""
    await cleanup_old_files(DOWNLOAD_DIR)

# ----------------- الحصول على التوكن -----------------
def get_bot_token() -> str:
    """الحصول على التوكن من: متغير بيئة -> ملف -> إدخال يدوي وحفظ في الملف."""
    # 1. محاولة قراءة من متغير البيئة
    token = os.getenv("BOT_TOKEN")
    if token:
        return token

    # 2. محاولة قراءة من الملف
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text().strip()
        if token:
            return token

    # 3. طلب إدخال يدوي
    print("لم يتم العثور على توكن البوت. الرجاء إدخاله الآن:")
    token = input("التوكن: ").strip()
    if not token:
        raise ValueError("لا يمكن تشغيل البوت بدون توكن")

    # حفظ التوكن في الملف للمرة القادمة
    TOKEN_FILE.write_text(token)
    print(f"✅ تم حفظ التوكن في {TOKEN_FILE}")
    return token

# ----------------- التشغيل الرئيسي -----------------
def main():
    print("""
    ╔══════════════════════════════════════════════════════╗
    ║     Telegram Media Downloader Bot v2.0               ║
    ║     مع حفظ التوكن تلقائياً                           ║
    ╚══════════════════════════════════════════════════════╝
    """)

    try:
        TOKEN = get_bot_token()
    except ValueError as e:
        print(f"❌ {e}")
        return

    # إنشاء التطبيق
    application = Application.builder().token(TOKEN).build()

    # إضافة معالجات الأوامر
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("audio", handle_audio))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # جدولة مهمة التنظيف التلقائي كل 6 ساعات
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(cleanup_job, interval=21600, first=10)

    print("✅ البوت يعمل الآن... اضغط Ctrl+C للإيقاف")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
