import io
import logging
import os
from typing import Tuple, Optional
import replicate
import requests
from PIL import Image
from telebot.types import InputSticker
import telebot
import re
from app import app, db
from models import User, StickerGeneration

# ──────────────────── ЛОГИРОВАНИЕ ────────────────────
log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "sticker_generator.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(asctime)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file, encoding='utf-8')
    ]
)

# ──────────────────── КОНСТАНТЫ ───────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(BASE_DIR, "img")
BACKGROUND_PATH = os.path.join(IMG_DIR, "background.png")
SHILDIK_PATH = os.path.join(IMG_DIR, "shildik.png")
UPLOAD_DIR = "/opt/avito-bot/static/uploads"
AVITO_STICKERS_DIR = "/opt/avito-bot/static/stickers/AvitoTeam_Mint"

REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")
if not REPLICATE_API_TOKEN:
    raise RuntimeError("REPLICATE_API_TOKEN не задан в окружении")

replicate_client = replicate.Client(api_token=REPLICATE_API_TOKEN)

# ──────────────────── УТИЛИТЫ ─────────────────────────
def _grab_url(raw) -> Optional[str]:
    if not raw:
        return None
    if isinstance(raw, str) and raw.startswith("http"):
        return raw
    if isinstance(raw, (list, tuple)):
        for item in raw:
            url = _grab_url(item)
            if url:
                return url
    if isinstance(raw, dict):
        for key in ("image", "images", "url", "output"):
            if key in raw:
                url = _grab_url(raw[key])
                if url:
                    return url
    return None

def assemble_sticker(person_img: Image.Image) -> Optional[io.BytesIO]:
    try:
        if not os.path.exists(BACKGROUND_PATH) or not os.path.exists(SHILDIK_PATH):
            logging.warning("Нет background.png или shildik.png — пропускаем сборку")
            return None

        background = Image.open(BACKGROUND_PATH).convert("RGBA").resize((512, 512), Image.Resampling.LANCZOS)
        shildik = Image.open(SHILDIK_PATH).convert("RGBA").resize((512, 512), Image.Resampling.LANCZOS)
        if person_img.size != (320, 320):
            person_img = person_img.resize((320, 320), Image.Resampling.LANCZOS)

        x = (512 - person_img.width) // 2
        y = (512 - person_img.height) // 2 - 30

        result = background.copy()
        result.paste(person_img, (x, y), person_img)
        result.paste(shildik, (0, 0), shildik)

        buf = io.BytesIO()
        result.save(buf, format="PNG")
        buf.seek(0)
        return buf
    except Exception as err:
        logging.error(f"Ошибка сборки стикера: {err}")
        return None

def create_sticker_pack(bot: telebot.TeleBot, chat_id: int, user_sticker_path: str) -> Optional[str]:
    try:
        logging.info(f"[{chat_id}] Creating sticker pack")

        with app.app_context():
            user = User.query.filter_by(telegram_id=str(chat_id)).first()
            if not user:
                logging.error(f"[{chat_id}] Пользователь не найден")
                bot.send_message(chat_id, "⚠️ Пользователь не найден. Начните с /start.")
                return None

        # Создаем уникальное имя для стикерпака
        pack_name = f"AvitoTeamMint_{chat_id}"
        
        # Создаем короткое имя, соответствующее требованиям Telegram
        # Оно должно заканчиваться на "_by_<bot_username>" и быть не длиннее 64 символов
        bot_username = bot.get_me().username
        pack_short_name = f"avitoteammint_{chat_id}_by_{bot_username}"
        pack_short_name = re.sub(r'[^a-z0-9_]', '', pack_short_name.lower())[:64]
        
        # Создаем заголовок стикерпака
        pack_title = f"Avito Team Mint by User {chat_id}"

        # Создаем список стикеров, начиная с пользовательского стикера
        stickers = [InputSticker(open(user_sticker_path, "rb"), emoji_list=["😊"])]

        # Добавляем стикеры из папки /opt/avito-bot/static/stickers
        stickers_dir = "/opt/avito-bot/static/stickers"
        for i in range(1, 9):  # Предполагаем, что у нас есть стикеры с именами 1.png, 2.png, ..., 8.png
            sticker_path = os.path.join(stickers_dir, f"{i}.png")
            if os.path.exists(sticker_path):
                stickers.append(InputSticker(open(sticker_path, "rb"), emoji_list=["😊"]))

        # Создаем стикерпак
        bot.create_new_sticker_set(
            user_id=chat_id,
            name=pack_short_name,
            title=pack_title,
            stickers=stickers,
            sticker_format="static"
        )
        logging.info(f"[{chat_id}] Created sticker pack {pack_short_name}")

        pack_url = f"https://t.me/addstickers/{pack_short_name}"
        logging.info(f"[{chat_id}] Sticker pack created: {pack_url}")

        with app.app_context():
            user = User.query.filter_by(telegram_id=str(chat_id)).first()
            if user:
                user.sticker_pack_url = pack_url  # Это поле должно существовать в модели User
                sticker_gen = StickerGeneration(
                    user_id=user.id,
                    template_used="custom",
                    generated_file_id=f"sticker_{chat_id}.png",
                    sticker_set_name=pack_short_name,
                    sticker_set_link=pack_url,  # Используем sticker_set_link вместо pack_url
                    status="ok"
                )
                db.session.add(sticker_gen)
                db.session.commit()

        return pack_url

    except Exception as err:
        logging.error(f"[{chat_id}] Ошибка создания стикерпака: {err}")
        bot.send_message(chat_id, "⚠️ Ошибка при создании стикерпака.")
        return None

def generate_sticker_from_user_photo(
    photo_url: str,
    chat_id: int,
    bot: telebot.TeleBot,
    copy_base_pack: bool = False,
) -> Tuple[Optional[io.BytesIO], Optional[str], Optional[str], None]:
    try:
        logging.info(f"[{chat_id}] Starting sticker generation, photo_url={photo_url}")
        if not photo_url.startswith("https://"):
            logging.error(f"[{chat_id}] Invalid photo URL: {photo_url}")
            return None, None, None, None

        flux_raw = replicate_client.run(
            "black-forest-labs/flux-kontext-pro",
            input={
                "seed": 1224737784,
                "prompt": (
                    "Make a cute cartoon avatar in simple flat vector style with bold outlines, "
                    "clean shapes, and no shading. Remove all text, logos, or watermarks. "
                    "Solid or transparent background."
                ),
                "input_image": photo_url,
                "aspect_ratio": "1:1",
                "output_format": "png",
                "safety_tolerance": 2,
            },
        )
        cartoon_url = _grab_url(flux_raw)
        if not cartoon_url:
            logging.error(f"[{chat_id}] Flux did not return a valid URL")
            return None, None, None, None

        bg_raw = replicate_client.run(
            "851-labs/background-remover:a029dff38972b5fda4ec5d75d7d1cd25aeff621d2cf4946a41055d7db66b80bc",
            input={"image": cartoon_url, "format": "png", "background_type": "rgba"},
        )
        clean_url = _grab_url(bg_raw)
        if not clean_url:
            logging.error(f"[{chat_id}] Background Remover did not return a valid URL")
            return None, None, None, None

        resp = requests.get(clean_url, timeout=20)
        resp.raise_for_status()
        person_img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        if person_img.size[0] < 100 or person_img.size[1] < 100:
            logging.error(f"[{chat_id}] Image too small: {person_img.size}")
            return None, None, None, None
        person_img = person_img.resize((320, 320), Image.Resampling.LANCZOS)

        sticker_buf = assemble_sticker(person_img)
        if not sticker_buf:
            fallback_buf = io.BytesIO(resp.content)
            fallback_buf.seek(0)
            sent = bot.send_photo(chat_id, fallback_buf, caption="Ваш стикер (без фона и шильдика).")
            file_id = sent.photo[-1].file_id if sent and sent.photo else None
            return fallback_buf, file_id, None, None

        save_path = os.path.join(UPLOAD_DIR, f"sticker_{chat_id}.png")
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        sticker_buf.seek(0)
        Image.open(sticker_buf).convert("RGBA").save(save_path, format="PNG")

        pack_url = create_sticker_pack(bot, chat_id, save_path)
        if not pack_url:
            return sticker_buf, None, None, None

        sticker_buf.seek(0)
        sent = bot.send_photo(chat_id, sticker_buf)
        file_id = sent.photo[-1].file_id if sent and sent.photo else None
        return sticker_buf, file_id, pack_url, None

    except Exception as err:
        logging.error(f"[{chat_id}] Sticker generation failed: {err}")
        try:
            bot.send_message(chat_id, "⚠️ Ошибка при генерации стикера.")
        except Exception:
            pass
        return None, None, None, None