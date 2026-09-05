
import asyncio
import os
import tempfile
import time
import uuid

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile
from aiogram.filters import CommandStart, Command
from aiogram.exceptions import TelegramAPIError

from config import (
    BOT_TOKEN,
    WATERMARK_POSITION,
    WATERMARK_MODE,
    MAX_VIDEO_DURATION_SECONDS,
    USER_COOLDOWN_SECONDS,
    GENERIC_URL_RE,
    extract_url,
    detect_platform_name,
    MENU_INSTAGRAM,
    MENU_TIKTOK,
    MENU_MUSIC,
    MENU_GALLERY,
    MENU_SETTINGS,
    FOLLOWUP_DELAY_SECONDS,
    logger,
)
from keyboards import main_menu_keyboard
from watermark import download_video, remove_watermark
from music import search_and_download_music
from notifications import schedule_followup

# ==================================================================
# BOT OB'EKTLARI VA HOLAT
# ==================================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

user_mode: dict[int, str | None] = {}
user_last_request: dict[int, float] = {}


def check_cooldown(chat_id: int) -> float:
    """
    Agar foydalanuvchi juda tez-tez so'rov yuborayotgan bo'lsa,
    kutish kerak bo'lgan soniyalar sonini qaytaradi (0 bo'lsa — bemalol).
    """
    now = time.monotonic()
    last = user_last_request.get(chat_id, 0.0)
    elapsed = now - last
    if elapsed < USER_COOLDOWN_SECONDS:
        return round(USER_COOLDOWN_SECONDS - elapsed, 1)
    user_last_request[chat_id] = now
    return 0.0


# ==================================================================
# BUYRUQLAR
# ==================================================================

@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user_mode[message.chat.id] = None
    logger.info("Foydalanuvchi %s /start bosdi", message.chat.id)
    await message.answer(
        "Salom! 👋 Men ko'p funksiyali media botman.\n\n"
        "Quyidagi bo'limlardan birini tanlang:\n"
        f"{MENU_INSTAGRAM} — post/reels video yuklab, watermarkni tozalayman\n"
        f"{MENU_TIKTOK} — TikTok video yuklab, watermarkni tozalayman\n"
        f"{MENU_MUSIC} — qo'shiq nomini yozing, topib beraman\n"
        f"{MENU_GALLERY} — o'zingiz yuborgan videodagi belgini tozalayman\n"
        f"{MENU_SETTINGS} — sozlamalar haqida ma'lumot\n\n"
        "💡 Shuningdek, YouTube, Facebook, Twitter/X va boshqa video "
        "tarmoqlaridan ham link yuborsangiz, avtomatik ishlov beraman.\n\n"
        "Yordam kerak bo'lsa /help yozing.",
        reply_markup=main_menu_keyboard(),
    )


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "🆘 Yordam\n\n"
        "• Instagram, TikTok, YouTube, Facebook, Twitter/X va boshqa "
        "video tarmoqlaridan link yuborsangiz, bot uni avtomatik aniqlaydi.\n"
        "• Musiqa uchun 🎵 tugmasini bosib, qo'shiq nomini yozing.\n"
        "• Video faylni to'g'ridan-to'g'ri yuborsangiz, watermark tozalanadi.\n"
        f"• Video davomiyligi {MAX_VIDEO_DURATION_SECONDS // 60} daqiqadan oshmasligi kerak.\n"
        "• Joriy amalni bekor qilish uchun /cancel yozing."
    )


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message) -> None:
    user_mode[message.chat.id] = None
    await message.answer("❌ Bekor qilindi. Bosh menyuga qaytdingiz.", reply_markup=main_menu_keyboard())


@dp.message(F.text == MENU_INSTAGRAM)
async def menu_instagram(message: Message) -> None:
    user_mode[message.chat.id] = "instagram"
    await message.answer("📥 Instagram post yoki reels linkini yuboring.")


@dp.message(F.text == MENU_TIKTOK)
async def menu_tiktok(message: Message) -> None:
    user_mode[message.chat.id] = "tiktok"
    await message.answer("🎬 TikTok video linkini yuboring.")


@dp.message(F.text == MENU_MUSIC)
async def menu_music(message: Message) -> None:
    user_mode[message.chat.id] = "music"
    await message.answer("🎵 Qo'shiq nomini (va ijrochisini) yozing, masalan: 'Ummon guruhi - Ohangim'.")


@dp.message(F.text == MENU_GALLERY)
async def menu_gallery(message: Message) -> None:
    user_mode[message.chat.id] = "gallery"
    await message.answer("🖼 Endi telefon/kompyuteringizdagi videoni shu yerga yuboring (fayl sifatida).")


@dp.message(F.text == MENU_SETTINGS)
async def menu_settings(message: Message) -> None:
    hours = FOLLOWUP_DELAY_SECONDS // 3600
    await message.answer(
        "⚙️ Sozlamalar\n\n"
        f"• Watermark yashirish usuli: {WATERMARK_MODE}\n"
        f"• Watermark joylashuvi: {WATERMARK_POSITION}\n"
        f"• Eslatma xabari: {hours} soatdan keyin\n"
        f"• Maksimal video davomiyligi: {MAX_VIDEO_DURATION_SECONDS // 60} daqiqa\n"
        f"• So'rovlar orasidagi minimal interval: {USER_COOLDOWN_SECONDS} soniya\n\n"
        "Bu qiymatlarni config.py faylidan o'zgartirishingiz mumkin."
    )


# ==================================================================
# VIDEO QAYTA ISHLASH (Instagram / TikTok / YouTube va h.k.)
# ==================================================================

async def process_video_link(message: Message, url: str, platform_name: str) -> None:
    chat_id = message.chat.id
    wait = check_cooldown(chat_id)
    if wait > 0:
        await message.answer(f"⏱ Iltimos, {wait} soniya kuting va qayta urining.")
        return

    logger.info("[%s] %s -> %s", chat_id, platform_name, url)
    await message.answer("✅ Video qabul qilindi! Ishlov berilmoqda...")
    status_msg = await message.answer("⏳ Video yuklanmoqda...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            raw_path = await asyncio.to_thread(download_video, url, tmp_dir)
        except ValueError as e:
            await status_msg.edit_text(f"⚠️ {e}")
            return
        except Exception as e:
            logger.exception("[%s] Yuklab olishda xatolik", chat_id)
            await status_msg.edit_text(
                "❌ Videoni yuklab bo'lmadi.\n"
                "Sabablari: video maxfiy akkauntdan, link noto'g'ri, "
                "yoki tarmoqda vaqtinchalik muammo bo'lishi mumkin.\n"
                f"Texnik tafsilot: {str(e)[:200]}"
            )
            return

        await status_msg.edit_text("🎬 Username/watermark tozalanmoqda...")
        processed_path = os.path.join(tmp_dir, f"{uuid.uuid4().hex}_clean.mp4")
        try:
            await asyncio.to_thread(
                remove_watermark, raw_path, processed_path, WATERMARK_POSITION, WATERMARK_MODE
            )
        except Exception:
            logger.exception("[%s] Watermarkni tozalashda xatolik", chat_id)
            processed_path = raw_path
            await status_msg.edit_text("⚠️ Watermarkni tozalab bo'lmadi, asl video yuborilmoqda...")

        await status_msg.edit_text("📤 Video yuborilmoqda...")
        try:
            await message.answer_video(FSInputFile(processed_path), caption=f"✅ Tayyor! ({platform_name})")
        except TelegramAPIError as e:
            logger.error("[%s] Video yuborishda xatolik: %s", chat_id, e)
            await status_msg.edit_text(
                "❌ Video juda katta yoki Telegram uni qabul qilmadi. "
                "(Telegram bot orqali maksimal 50 MB gacha fayl yuborish mumkin.)"
            )
            return
        await status_msg.delete()

    logger.info("[%s] %s video muvaffaqiyatli yuborildi", chat_id, platform_name)
    schedule_followup(bot, chat_id)


# ==================================================================
# GALEREYADAN YUBORILGAN VIDEO
# ==================================================================

@dp.message(F.video)
async def handle_uploaded_video(message: Message) -> None:
    chat_id = message.chat.id
    wait = check_cooldown(chat_id)
    if wait > 0:
        await message.answer(f"⏱ Iltimos, {wait} soniya kuting va qayta urining.")
        return

    if message.video.duration and message.video.duration > MAX_VIDEO_DURATION_SECONDS:
        await message.answer(
            f"⚠️ Video juda uzun. Maksimal ruxsat etilgan: "
            f"{MAX_VIDEO_DURATION_SECONDS // 60} daqiqa."
        )
        return

    logger.info("[%s] Galereyadan video qabul qilindi", chat_id)
    await message.answer("✅ Video qabul qilindi! Ishlov berilmoqda...")
    status_msg = await message.answer("⏳ Video yuklab olinmoqda...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        raw_path = os.path.join(tmp_dir, f"{uuid.uuid4().hex}.mp4")
        try:
            file_info = await bot.get_file(message.video.file_id)
            await bot.download_file(file_info.file_path, destination=raw_path)
        except Exception as e:
            logger.exception("[%s] Videoni yuklab olishda xatolik", chat_id)
            await status_msg.edit_text(f"❌ Videoni yuklab bo'lmadi: {str(e)[:200]}")
            return

        await status_msg.edit_text("🎬 Watermark/belgi tozalanmoqda...")
        processed_path = os.path.join(tmp_dir, f"{uuid.uuid4().hex}_clean.mp4")
        try:
            await asyncio.to_thread(
                remove_watermark, raw_path, processed_path, WATERMARK_POSITION, WATERMARK_MODE
            )
        except Exception:
            logger.exception("[%s] Watermarkni tozalashda xatolik", chat_id)
            processed_path = raw_path
            await status_msg.edit_text("⚠️ Tozalab bo'lmadi, asl video yuborilmoqda...")

        await status_msg.edit_text("📤 Video yuborilmoqda...")
        try:
            await message.answer_video(FSInputFile(processed_path), caption="✅ Tayyor!")
        except TelegramAPIError as e:
            logger.error("[%s] Video yuborishda xatolik: %s", chat_id, e)
            await status_msg.edit_text("❌ Video juda katta yoki yuborilmadi.")
            return
        await status_msg.delete()

    logger.info("[%s] Galereya videosi muvaffaqiyatli yuborildi", chat_id)
    schedule_followup(bot, chat_id)


# ==================================================================
# MUSIQA QIDIRISH
# ==================================================================

async def process_music_query(message: Message, query: str) -> None:
    chat_id = message.chat.id
    wait = check_cooldown(chat_id)
    if wait > 0:
        await message.answer(f"⏱ Iltimos, {wait} soniya kuting va qayta urining.")
        return

    logger.info("[%s] Musiqa qidiruvi: %s", chat_id, query)
    await message.answer("✅ So'rov qabul qilindi! Qidirilmoqda...")
    status_msg = await message.answer("🔎 Qidirilmoqda...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            title, filepath = await asyncio.to_thread(search_and_download_music, query, tmp_dir)
        except Exception as e:
            logger.exception("[%s] Musiqa qidirishda xatolik", chat_id)
            await status_msg.edit_text(
                f"❌ Topilmadi yoki yuklab bo'lmadi.\nTexnik tafsilot: {str(e)[:200]}"
            )
            return

        await status_msg.edit_text("📤 Yuborilmoqda...")
        try:
            await message.answer_audio(FSInputFile(filepath), title=title, caption=f"🎵 {title}")
        except TelegramAPIError as e:
            logger.error("[%s] Audio yuborishda xatolik: %s", chat_id, e)
            await status_msg.edit_text("❌ Audio faylni yuborib bo'lmadi.")
            return
        await status_msg.delete()

    logger.info("[%s] Musiqa muvaffaqiyatli yuborildi: %s", chat_id, title)
    schedule_followup(bot, chat_id)


# ==================================================================
# MATNLI XABARLARNI YO'NALTIRISH
# ==================================================================

@dp.message(F.text)
async def handle_text(message: Message) -> None:
    text = message.text or ""
    chat_id = message.chat.id
    mode = user_mode.get(chat_id)

    url = extract_url(text, GENERIC_URL_RE)

    if url:
        platform_name = detect_platform_name(url)
        await process_video_link(message, url, platform_name)
        return

    if mode == "music":
        await process_music_query(message, text)
        return

    if mode == "gallery":
        await message.answer("🖼 Iltimos, videoni matn emas, fayl sifatida yuboring.")
        return

    await message.answer(
        "Tushunmadim 🙂 Pastdagi menyudan bo'lim tanlang yoki to'g'ridan-to'g'ri "
        "video linkini (Instagram, TikTok, YouTube va h.k.) yuboring. "
        "Yordam uchun /help yozing.",
        reply_markup=main_menu_keyboard(),
    )


# ==================================================================
# XATOLIKLARNI GLOBAL USHLASH
# ==================================================================

@dp.errors()
async def global_error_handler(event) -> bool:
    logger.exception("Kutilmagan xatolik: %s", event.exception)
    return True


# ==================================================================
# ISHGA TUSHIRISH
# ==================================================================

async def main() -> None:
    logger.info("Bot ishga tushmoqda...")
    try:
        await dp.start_polling(bot)
    finally:
        logger.info("Bot to'xtatildi.")
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot foydalanuvchi tomonidan to'xtatildi (Ctrl+C).")
