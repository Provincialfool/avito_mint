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
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
AVITO_STICKERS_DIR = os.path.join(BASE_DIR, "static", "stickers")

# Создаем необходимые директории если их нет
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(AVITO_STICKERS_DIR, exist_ok=True)

# Функция для получения токена из базы данных или переменных окружения
def get_replicate_token():
    try:
        from models import SystemConfig
        with app.app_context():
            # Сначала проверяем базу данных
            db_token = SystemConfig.get_config('REPLICATE_API_TOKEN')
            if db_token:
                return db_token
    except Exception as e:
        logging.warning(f"Не удалось получить токен из базы данных: {e}")
    
    # Если в базе нет, берем из переменных окружения
    return os.getenv("REPLICATE_API_TOKEN")

REPLICATE_API_TOKEN = get_replicate_token()
replicate_client = None

if REPLICATE_API_TOKEN:
    replicate_client = replicate.Client(api_token=REPLICATE_API_TOKEN)
    logging.info("✅ REPLICATE_API_TOKEN настроен - функция генерации стикеров активна")
else:
    logging.warning("REPLICATE_API_TOKEN не задан - функция генерации стикеров отключена")

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
        bot_username = bot.get_me().username

        # Короткое и лаконичное имя: AvitoTeam_ID
        pack_short_name = f"avitoteam_{chat_id}_by_{bot_username}"
        pack_short_name = re.sub(r'[^a-z0-9_]', '', pack_short_name.lower())[:64]

        # Лаконичный заголовок
        pack_title = f"AvitoTeam_{chat_id}"

        pack_url = f"https://t.me/addstickers/{pack_short_name}"

        # Создаем список стикеров, начиная с пользовательского стикера
        stickers = [InputSticker(open(user_sticker_path, "rb"), emoji_list=["😊"])]

        # Добавляем стикеры из папки static/stickers
        stickers_dir = os.path.join(BASE_DIR, "static", "stickers")
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
                # Обновляем существующую запись или создаем новую
                existing_gen = StickerGeneration.query.filter_by(user_id=user.id).first()
                if existing_gen:
                    existing_gen.template_used = "avito_team"
                    existing_gen.generated_file_id = f"sticker_{chat_id}.png"
                    existing_gen.sticker_set_name = pack_short_name
                    existing_gen.sticker_set_link = pack_url
                    existing_gen.pack_url = pack_url
                    existing_gen.status = "ok"
                    logging.info(f"[{chat_id}] Updated existing sticker generation record")
                else:
                    sticker_gen = StickerGeneration(
                        user_id=user.id,
                        template_used="avito_team",
                        generated_file_id=f"sticker_{chat_id}.png",
                        sticker_set_name=pack_short_name,
                        sticker_set_link=pack_url,
                        pack_url=pack_url,
                        status="ok"
                    )
                    db.session.add(sticker_gen)
                    logging.info(f"[{chat_id}] Created new sticker generation record")
                db.session.commit()
                logging.info(f"[{chat_id}] Sticker generation record saved successfully")

        return pack_url

    except Exception as err:
        error_str = str(err).lower()
        logging.error(f"[{chat_id}] Ошибка создания стикерпака: {err}")

        # Если стикерпак уже существует - проверяем, действительно ли он существует
        if "already occupied" in error_str or "name is already" in error_str:
            logging.info(f"[{chat_id}] Sticker pack name occupied, verifying existence...")
            
            # Дополнительная проверка через API
            try:
                existing_set = bot.get_sticker_set(pack_short_name)
                if existing_set and hasattr(existing_set, 'stickers') and len(existing_set.stickers) > 0:
                    logging.info(f"[{chat_id}] Sticker pack confirmed exists, updating DB")
                    
                    # Обновляем запись в базе данных
                    with app.app_context():
                        user = User.query.filter_by(telegram_id=str(chat_id)).first()
                        if user:
                            existing_gen = StickerGeneration.query.filter_by(user_id=user.id).first()
                            if existing_gen:
                                existing_gen.sticker_set_name = pack_short_name
                                existing_gen.sticker_set_link = pack_url
                                existing_gen.pack_url = pack_url
                                existing_gen.status = "ok"
                                existing_gen.template_used = "avito_team"
                            else:
                                sticker_gen = StickerGeneration(
                                    user_id=user.id,
                                    template_used="avito_team",
                                    generated_file_id=f"sticker_{chat_id}.png",
                                    sticker_set_name=pack_short_name,
                                    sticker_set_link=pack_url,
                                    pack_url=pack_url,
                                    status="ok"
                                )
                                db.session.add(sticker_gen)
                            db.session.commit()
                    
                    return pack_url
                else:
                    logging.info(f"[{chat_id}] Sticker pack name occupied but pack is empty/deleted, continuing creation")
            except Exception as verify_error:
                if "not found" in str(verify_error).lower():
                    logging.info(f"[{chat_id}] Sticker pack name occupied but pack not found, continuing creation")
                else:
                    logging.warning(f"[{chat_id}] Could not verify sticker pack existence: {verify_error}")

        # Дополнительная проверка через URL только если API подтвердил существование
        # (убираем эту проверку, так как она может давать ложные срабатывания)

        try:
            bot.send_message(chat_id, "⚠️ Ошибка при создании стикерпака.")
        except Exception as send_error:
            logging.error(f"[{chat_id}] Failed to send error message: {send_error}")
        return None

def reinitialize_replicate_client():
    """Переинициализация клиента Replicate с новым токеном"""
    global replicate_client, REPLICATE_API_TOKEN
    
    REPLICATE_API_TOKEN = get_replicate_token()
    
    if REPLICATE_API_TOKEN:
        replicate_client = replicate.Client(api_token=REPLICATE_API_TOKEN)
        logging.info("✅ Replicate клиент переинициализирован с новым токеном")
        return True
    else:
        replicate_client = None
        logging.warning("❌ Не удалось переинициализировать Replicate клиент - токен не найден")
        return False

def generate_sticker_from_user_photo(file_url: str, chat_id: int, bot) -> Tuple[Optional[io.BytesIO], Optional[str], Optional[str], Optional[str]]:
    """Generate sticker from user photo - limit one successful generation per user"""

    # Проверяем существующий стикерпак по имени в Telegram API
    bot_username = bot.get_me().username
    pack_short_name = f"avitoteam_{chat_id}_by_{bot_username}"
    pack_short_name = re.sub(r'[^a-z0-9_]', '', pack_short_name.lower())[:64]
    pack_url = f"https://t.me/addstickers/{pack_short_name}"

    def update_db_and_return_existing():
        """Обновляет БД и возвращает существующий стикерпак"""
        with app.app_context():
            user = User.query.filter_by(telegram_id=str(chat_id)).first()
            if user:
                existing_gen = StickerGeneration.query.filter_by(user_id=user.id).first()
                if existing_gen:
                    existing_gen.sticker_set_name = pack_short_name
                    existing_gen.sticker_set_link = pack_url
                    existing_gen.pack_url = pack_url
                    existing_gen.status = "ok"
                    existing_gen.template_used = "avito_team"
                else:
                    sticker_gen = StickerGeneration(
                        user_id=user.id,
                        template_used="avito_team",
                        generated_file_id=f"sticker_{chat_id}.png",
                        sticker_set_name=pack_short_name,
                        sticker_set_link=pack_url,
                        pack_url=pack_url,
                        status="ok"
                    )
                    db.session.add(sticker_gen)
                db.session.commit()
        return None, None, pack_url, f"У вас уже есть готовый стикерпак!\n\nСсылка: {pack_url}\n\nДобавьте его в Telegram и пользуйтесь!"

    # 1. Проверяем в базе данных
    try:
        with app.app_context():
            user = User.query.filter_by(telegram_id=str(chat_id)).first()
            if user:
                existing_gen = StickerGeneration.query.filter_by(
                    user_id=user.id, 
                    status='ok'
                ).filter(
                    StickerGeneration.pack_url.isnot(None)
                ).first()

                if existing_gen and existing_gen.pack_url:
                    logging.info(f"[{chat_id}] Found existing sticker pack in DB: {existing_gen.pack_url}")
                    return None, None, existing_gen.pack_url, f"У вас уже есть готовый стикерпак!\n\nСсылка: {existing_gen.pack_url}\n\nДобавьте его в Telegram и пользуйтесь!"
    except Exception as db_error:
        logging.warning(f"[{chat_id}] DB check error: {db_error}")

    # 2. Проверяем через Telegram API
    try:
        sticker_set = bot.get_sticker_set(pack_short_name)
        if sticker_set and hasattr(sticker_set, 'stickers') and len(sticker_set.stickers) > 0:
            logging.info(f"[{chat_id}] Found existing sticker pack in Telegram: {pack_url}")
            return update_db_and_return_existing()
    except Exception as e:
        # Если ошибка содержит "not found" - стикерпак точно не существует
        if "not found" in str(e).lower() or "sticker set not found" in str(e).lower():
            logging.info(f"[{chat_id}] Sticker pack confirmed not found: {e}")
        else:
            logging.info(f"[{chat_id}] Sticker pack check in Telegram failed: {e}")

    # 3. Проверяем через URL (более строгая проверка)
    try:
        import requests
        response = requests.get(pack_url, timeout=10)
        if response.status_code == 200:
            # Проверяем, что страница содержит реальный стикерпак, а не ошибку
            text_lower = response.text.lower()
            if ("addstickers" in text_lower or "stickerset" in text_lower) and "not found" not in text_lower and "404" not in text_lower:
                logging.info(f"[{chat_id}] Sticker pack exists via URL check: {pack_url}")
                return update_db_and_return_existing()
            else:
                logging.info(f"[{chat_id}] URL check shows sticker pack does not exist")
        else:
            logging.info(f"[{chat_id}] URL check failed with status: {response.status_code}")
    except Exception as e:
        logging.info(f"[{chat_id}] URL check failed: {e}")

    # Если дошли до сюда - стикерпак не существует, продолжаем с генерацией
    logging.info(f"[{chat_id}] No existing sticker pack found, proceeding with generation")

    try:
        if not replicate_client:
            # Пытаемся переинициализировать клиент на случай, если токен был добавлен через админку
            logging.info(f"[{chat_id}] Replicate клиент не инициализирован, пытаемся переинициализировать...")
            if not reinitialize_replicate_client():
                logging.error(f"[{chat_id}] REPLICATE_API_TOKEN не настроен")
                bot.send_message(chat_id, "⚠️ Генерация стикеров временно недоступна. Обратитесь к администратору.")
                return None, None, None, None

        logging.info(f"[{chat_id}] Starting sticker generation, photo_url={file_url}")
        if not file_url.startswith("https://"):
            logging.error(f"[{chat_id}] Invalid photo URL: {file_url}")
            return None, None, None, None

        with app.app_context():
            # Проверяем лимит генераций для пользователя
            user = User.query.filter_by(telegram_id=str(chat_id)).first()
            if not user:
                logging.error(f"User not found for chat_id: {chat_id}")
                return None, None, None, None

            # Дополнительная проверка на всякий случай
            existing_generation = StickerGeneration.query.filter_by(
                user_id=user.id
            ).filter(
                StickerGeneration.status.in_(['ok', 'completed'])
            ).first()
            if existing_generation and existing_generation.pack_url:
                logging.info(f"User {user.id} already has a completed sticker generation")
                return None, None, existing_generation.pack_url, "У вас уже есть сгенерированный стикерпак"

        flux_raw = replicate_client.run(
            "black-forest-labs/flux-kontext-pro",
            input={
                "seed": 1224737784,
                "prompt": (
                    "Make a cute cartoon avatar in simple flat vector style with bold outlines, "
                    "clean shapes, and no shading. Remove all text, logos, or watermarks. "
                    "Solid or transparent background."
                ),
                "input_image": file_url,
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