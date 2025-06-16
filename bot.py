import os
import io
import time
import json
import threading
import queue
import logging

from datetime import datetime
from uuid import uuid4
from typing import Optional

from PIL import Image
import telebot
from telebot import types

from app import app, db
from models import User, Registration, QuestProgress, StickerGeneration, DanceSlot, SurveyAnswer, SystemConfig, UserFeedback, ScheduledMessage
from text import (
    CONSENT_TEXT, SURVEY_QUESTIONS, MAIN_MENU_TEXT, MAIN_MENU_TEXT_NO_THANKS,
    DANCE_INTRO, DANCE_CHOOSE_SLOT, DANCE_CONFIRMATION, DANCE_FULL_MESSAGE, DANCE_ALL_FULL,
    WORKSHOP_TEXT, FOREST_TEXT_1, FOREST_TEXT_2, STICKER_START_MESSAGE, CAREER_MESSAGE,
    SCHEDULE_MESSAGE, MAP_TEXT, IMG_DIR, MAP_PATH, MAP_SENT_PATH, MAP_FOREST_PATH,
    MASTER_PATH, QUEST_PATH, DANCE_PATH, FOREST_PATH
)
from text_cache import text_cache
from sticker_generator import generate_sticker_from_user_photo

# ═══════════════════════════════════════════════════════════════════════════════
#                                ИНИЦИАЛИЗАЦИЯ
# ═══════════════════════════════════════════════════════════════════════════════

def get_bot_token():
    """Получить токен бота из конфигурации или переменных окружения"""
    try:
        from models import SystemConfig
        from app import app
        with app.app_context():
            # Сначала проверяем токен из базы данных
            db_token = SystemConfig.get_config('BOT_TOKEN')
            if db_token:
                return db_token
    except:
        pass
    
    # Fallback на переменные окружения
    env_token = os.getenv("BOT_TOKEN")
    if not env_token:
        raise ValueError("BOT_TOKEN is not defined in environment variables or database")
    return env_token

BOT_TOKEN = get_bot_token()
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# Глобальные переменные для состояний
user_states: dict[int, str] = {}
sticker_queue = queue.Queue(maxsize=50)

# Защита от дублирования callback_query
last_callback_query: dict[int, float] = {}
CALLBACK_COOLDOWN = 1.0  # 1 секунда

def is_duplicate_callback(user_id: int) -> bool:
    """Проверить, не является ли callback дублированным"""
    now = time.time()
    if user_id in last_callback_query:
        if now - last_callback_query[user_id] < CALLBACK_COOLDOWN:
            return True
    last_callback_query[user_id] = now
    return False

# ═══════════════════════════════════════════════════════════════════════════════
#                                КЕШИРОВАНИЕ
# ═══════════════════════════════════════════════════════════════════════════════

cache = {}
CACHE_TTL = 300  # 5 минут

def get_cached_or_compute(key, compute_func, ttl=CACHE_TTL):
    """Получить данные из кеша или вычислить"""
    now = time.time()
    if key in cache:
        value, timestamp = cache[key]
        if now - timestamp < ttl:
            return value

    # Вычисляем и кешируем
    value = compute_func()
    cache[key] = (value, now)
    return value

def clear_old_cache():
    """Очистка устаревших записей кеша"""
    now = time.time()
    to_remove = []
    for key, (value, timestamp) in cache.items():
        if now - timestamp > CACHE_TTL:
            to_remove.append(key)
    for key in to_remove:
        del cache[key]

def get_cached_text(key: str, default: str = "") -> str:
    """Получает текст из кеша, если нет - из базы, если нет - возвращает default."""
    cache_key = f"text_{key}"
    def compute_text():
        config_text = get_text_from_config(key, default)
        return config_text if config_text else default
    return get_cached_or_compute(cache_key, compute_text)

# Очистка кеша каждые 10 минут
def cache_cleaner():
    while True:
        time.sleep(600)  # 10 минут
        clear_old_cache()

threading.Thread(target=cache_cleaner, daemon=True).start()

# ═══════════════════════════════════════════════════════════════════════════════
#                                УТИЛИТЫ
# ═══════════════════════════════════════════════════════════════════════════════

def get_text_from_config(key, default_fallback=None):
    """Получить текст из конфигурации с fallback на text.py"""
    with app.app_context():
        config_text = SystemConfig.get_config(key)
        if config_text:
            return config_text

        # Fallback на text.py
        if default_fallback:
            return default_fallback

        try:
            import text
            return getattr(text, key, '')
        except:
            return ''

def get_dance_slots_from_cache():
    """Получить слоты танцев из кеша"""
    return text_cache.get_dance_slots()

def get_user_id(chat_id: int) -> Optional[int]:
    """Получить ID пользователя с кешированием"""
    cache_key = f"user_id_{chat_id}"

    def compute_user_id():
        with app.app_context():
            user = User.query.filter_by(telegram_id=str(chat_id)).first()
            if not user:
                user = User(telegram_id=str(chat_id), consent_given=False)
                db.session.add(user)
                try:
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    print(f"Error creating user: {e}")
                    # Повторная попытка получения пользователя
                    user = User.query.filter_by(telegram_id=str(chat_id)).first()
                    if not user:
                        raise e
            return user.id

    return get_cached_or_compute(cache_key, compute_user_id, ttl=1800)  # Увеличиваем TTL до 30 минут

def send_img_scaled(chat_id: int, path: str, caption: str = "", kb=None, max_px: int = 700) -> None:
    """Отправка изображения с масштабированием"""
    if not os.path.exists(path):
        bot.send_message(chat_id, "⚠️ Изображение недоступно.")
        return
    try:
        with Image.open(path) as img:
            w, h = img.size
            scale = max(w, h) / max_px
            if scale > 1:
                img = img.resize((int(w / scale), int(h / scale)), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            bot.send_photo(chat_id, buf, caption=caption, reply_markup=kb)
    except Exception as e:
        print(f"Error in send_img_scaled: {e}")
        bot.send_message(chat_id, "⚠️ Не удалось загрузить изображение.")

# ═══════════════════════════════════════════════════════════════════════════════
#                                КЛАВИАТУРЫ
# ═══════════════════════════════════════════════════════════════════════════════

def inline_main_menu() -> types.InlineKeyboardMarkup:
    """Главное меню"""
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🗺️ Карта Дикой Мяты", callback_data="map"),
        types.InlineKeyboardButton("🌳 Лес", callback_data="forest"),
        types.InlineKeyboardButton("🎨 Мастер-класс", callback_data="workshop"),
        types.InlineKeyboardButton("💃 Танцы", callback_data="dance"),
        types.InlineKeyboardButton("🧩 Квест", callback_data="quest"),
        types.InlineKeyboardButton("🖼️ Стикерпак", callback_data="sticker"),
        types.InlineKeyboardButton("🚀 Карьера в Avito", callback_data="career"),
        types.InlineKeyboardButton("⏰ Расписание", callback_data="schedule"),
    )
    return kb

def inline_back_to_menu() -> types.InlineKeyboardMarkup:
    """Кнопка возврата в главное меню"""
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⬅️ Главное меню", callback_data="main"))
    return kb

def inline_map_and_menu() -> types.InlineKeyboardMarkup:
    """Карта и главное меню"""
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("⬅️ Главное меню", callback_data="main"),
        types.InlineKeyboardButton("🗺️ Карта", callback_data="map")
    )
    return kb

# ═══════════════════════════════════════════════════════════════════════════════
#                                КВЕСТ УТИЛИТЫ
# ═══════════════════════════════════════════════════════════════════════════════

def get_quest_hint(step: int) -> tuple[Optional[str], Optional[str]]:
    """Получить подсказку квеста из кеша"""
    quest_step = text_cache.get_quest_step(step)
    if quest_step and quest_step.get("hint"):
        return quest_step.get("hint"), quest_step.get("image")
    return None, None

def get_quest_total_steps() -> int:
    """Получить общее количество шагов квеста"""
    return text_cache.get_quest_total_steps()

def get_quest_progress(user_id: int) -> str:
    """Получить прогресс квеста пользователя"""
    quest_total = get_quest_total_steps()

    with app.app_context():
        progress = db.session.query(QuestProgress).filter_by(user_id=user_id).first()
        if not progress:
            progress = QuestProgress(user_id=user_id, completed_steps="[]")
            db.session.add(progress)
            db.session.commit()

        try:
            steps_data = json.loads(progress.completed_steps or "[]")
            if isinstance(steps_data, list):
                valid_steps = [step for step in steps_data if isinstance(step, int) and 1 <= step <= quest_total]
                count = len(valid_steps)
            else:
                count = 0
        except (json.JSONDecodeError, TypeError):
            count = 0

        return f"Стикеров найдено: {count} из {quest_total}"

def register_quest_step(user_id: int, step: int) -> None:
    """Регистрация шага квеста"""
    quest_total = get_quest_total_steps()

    with app.app_context():
        progress = QuestProgress.query.filter_by(user_id=user_id).first()
        if not progress:
            progress = QuestProgress(user_id=user_id, completed_steps="[]")
            db.session.add(progress)

        try:
            steps = json.loads(progress.completed_steps or "[]")
            if not isinstance(steps, list):
                steps = []

            valid_steps = [s for s in steps if isinstance(s, int) and 1 <= s <= quest_total]

            if step not in valid_steps and 1 <= step <= quest_total:
                valid_steps.append(step)
                valid_steps.sort()
                progress.completed_steps = json.dumps(valid_steps)

                if len(valid_steps) == quest_total:
                    progress.completed = True
                    progress.completed_at = datetime.utcnow()

                db.session.commit()
                print(f"Quest step {step} registered for user {user_id}. Total steps: {len(valid_steps)}")
        except (json.JSONDecodeError, TypeError) as e:
            print(f"Error processing quest steps for user {user_id}: {e}")
            progress.completed_steps = json.dumps([step])
            db.session.commit()

def user_quest_completed_steps(user_id: int) -> list:
    """Получить завершенные шаги квеста"""
    quest_total = get_quest_total_steps()

    with app.app_context():
        progress = QuestProgress.query.filter_by(user_id=user_id).first()
        if not progress:
            return []
        try:
            steps = json.loads(progress.completed_steps or "[]")
            return [s for s in steps if isinstance(s, int) and 1 <= s <= quest_total]
        except (json.JSONDecodeError, TypeError):
            return []

def next_quest_step_for_user(user_id: int) -> Optional[int]:
    """Получить следующий шаг квеста для пользователя"""
    quest_total = get_quest_total_steps()
    found = user_quest_completed_steps(user_id)
    for step in range(1, quest_total + 1):
        if step not in found:
            return step
    return None

def format_quest_text(text: str) -> str:
    """Улучшить форматирование текста квеста"""
    key_phrases = [
        "сердце Дикой Мяты", "энергия — выше крыши", "сделанного с душой",
        "мягких подушек", "дух соперничества", "бусин и проволоки",
        "мотор рычит", "Маяк рядом", "живого символа"
    ]

    formatted_text = text
    for phrase in key_phrases:
        if phrase in formatted_text:
            formatted_text = formatted_text.replace(phrase, f"<b>{phrase}</b>")

    return formatted_text

def handle_quest_qr(chat_id: int, user_id: int, step: int) -> None:
    """Обработка QR-кода квеста"""
    try:
        quest_total = get_quest_total_steps()

        if step < 1 or step > quest_total:
            bot.send_message(chat_id, "⚠️ Неверный шаг квеста.")
            return

        # Проверяем, не найден ли уже этот шаг
        completed_steps = user_quest_completed_steps(user_id)
        if step in completed_steps:
            bot.send_message(chat_id, f"✅ Этот стикер уже найден! {get_quest_progress(user_id)}")
            return

        # Регистрируем шаг
        register_quest_step(user_id, step)

        # Получаем изображение для найденного шага
        _, sticker_path = get_quest_hint(step)
        progress_text = get_quest_progress(user_id)

        # Отправляем изображение найденного стикера
        if sticker_path and os.path.exists(sticker_path):
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("🔍 К подсказкам", callback_data="quest_hints"),
                types.InlineKeyboardButton("⬅️ Главное меню", callback_data="main")
            )
            send_img_scaled(chat_id, sticker_path, caption=f"✅ Стикер найден!\n\n{progress_text}", kb=kb)
        else:
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("🔍 К подсказкам", callback_data="quest_hints"),
                types.InlineKeyboardButton("⬅️ Главное меню", callback_data="main")
            )
            bot.send_message(chat_id, f"✅ Стикер найден!\n\n{progress_text}", reply_markup=kb)

        # Проверяем, завершен ли квест
        new_completed_steps = user_quest_completed_steps(user_id)
        if len(new_completed_steps) == quest_total:
            bot.send_message(
                chat_id, 
                f"🎉 Поздравляем! Ты нашел все стикеры квеста!\n\n"
                f"Приходи в лаунж Avito Team за призом! 🏆",
                reply_markup=inline_back_to_menu()
            )

    except Exception as e:
        print(f"Error in handle_quest_qr: {e}")
        bot.send_message(chat_id, "⚠️ Ошибка при обработке QR-кода.")

# ═══════════════════════════════════════════════════════════════════════════════
#                                ОТЛОЖЕННЫЕ СООБЩЕНИЯ
# ═══════════════════════════════════════════════════════════════════════════════

def scheduled_sender() -> None:
    """Отправка отложенных сообщений"""
    while True:
        try:
            with app.app_context():
                now = datetime.utcnow()
                pending = ScheduledMessage.query.filter(
                    ScheduledMessage.scheduled_time <= now,
                    ScheduledMessage.sent.is_(False),
                ).all()

                for msg in pending:
                    try:
                        message_text = msg.text
                        if not message_text:
                            continue

                        if msg.photo_url:
                            photo_path = msg.photo_url.lstrip('/')
                            if os.path.exists(photo_path):
                                with open(photo_path, 'rb') as photo:
                                    bot.send_photo(msg.chat_id, photo, caption=message_text)
                            else:
                                bot.send_message(msg.chat_id, message_text)
                        else:
                            bot.send_message(msg.chat_id, message_text)

                        msg.sent = True
                        db.session.commit()

                    except Exception as e:
                        print(f"Error sending message to {msg.chat_id}: {e}")
                        msg.sent = True
                        db.session.commit()
        except Exception as e:
            print(f"Error in scheduled_sender: {e}")

        time.sleep(30)

threading.Thread(target=scheduled_sender, daemon=True).start()

# ═══════════════════════════════════════════════════════════════════════════════
#                                КОМАНДА START
# ═══════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=["webhook_status"])
def handle_webhook_status(message: types.Message) -> None:
    """Команда для проверки статуса webhook (только для администраторов)"""
    chat_id = message.chat.id
    
    # Проверяем, что пользователь - администратор
    admin_ids = ["233223379"]  # Замените на ваш chat_id
    
    if str(chat_id) not in admin_ids:
        bot.send_message(chat_id, "❌ У вас нет прав для выполнения этой команды.")
        return
    
    try:
        with app.app_context():
            from models import SystemConfig
            bot_mode = SystemConfig.get_config('BOT_MODE', 'polling')
            webhook_domain = SystemConfig.get_config('WEBHOOK_DOMAIN', '')
        
        # Получаем информацию о webhook
        webhook_info = bot.get_webhook_info()
        
        status_text = f"""📊 <b>Статус Webhook:</b>

🤖 <b>Режим бота:</b> {bot_mode}
🌐 <b>Домен:</b> {webhook_domain or 'Не настроен'}
🔗 <b>URL в Telegram:</b> {webhook_info.url or 'Не установлен'}
⏳ <b>Ожидающих обновлений:</b> {webhook_info.pending_update_count}
❌ <b>Последняя ошибка:</b> {webhook_info.last_error_message or 'Нет ошибок'}

{'✅ Webhook работает' if webhook_info.url else '❌ Webhook не установлен'}"""

        bot.send_message(chat_id, status_text)
        
    except Exception as e:
        bot.send_message(chat_id, f"❌ Ошибка при проверке webhook: {e}")

@bot.message_handler(commands=["start"])
def handle_start(message: types.Message) -> None:
    """Обработка команды /start"""
    chat_id = message.chat.id
    payload = message.text.split(maxsplit=1)[1].strip().lower() if len(message.text.split()) > 1 else None
    print(f"[start] chat_id={chat_id}, payload={payload}")

    with app.app_context():
        user = User.query.filter_by(telegram_id=str(chat_id)).first()
        
        # Для существующих пользователей с согласием и завершенным опросом
        if user and user.consent_given and user.survey_completed:
            print(f"[start] Существующий пользователь с завершенными этапами: {chat_id}")
            
            # Обработка QR-кода квеста для авторизованных пользователей
            if payload and payload.startswith("q") and payload[1:].isdigit():
                try:
                    step = int(payload[1:])
                    handle_quest_qr(chat_id, user.id, step)
                    return
                except ValueError:
                    bot.send_message(chat_id, "⚠️ Ошибка обработки QR-кода.")
                    return
            
            # Отправка только видео и главного меню
            try:
                with open("circle.mp4", "rb") as f:
                    bot.send_video_note(chat_id, f)
            except Exception as e:
                print(f"[start] Ошибка отправки видео: {e}")
            
            # Сразу показываем главное меню
            show_main_menu(chat_id, first_time=False)
            return
        
        # Для новых пользователей или незавершивших этапы
        if not user:
            user = User(
                telegram_id=str(chat_id),
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name,
                consent_given=False
            )
            db.session.add(user)
            db.session.commit()
            print(f"[start] Новый пользователь: {chat_id}")

        # Обработка QR-кода квеста для неавторизованных пользователей
        if user.consent_given and payload and payload.startswith("q") and payload[1:].isdigit():
            try:
                step = int(payload[1:])
                handle_quest_qr(chat_id, user.id, step)
                return
            except ValueError:
                bot.send_message(chat_id, "⚠️ Ошибка обработки QR-кода.")
                return

        if payload and payload.startswith("q") and payload[1:].isdigit():
            user_states[chat_id] = f"qr_step|{payload[1:]}"
        else:
            user_states.pop(chat_id, None)

    # Отправка приветственного видео для новых пользователей
    try:
        with open("circle.mp4", "rb") as f:
            bot.send_video_note(chat_id, f)
    except Exception as e:
        print(f"[start] Ошибка отправки видео: {e}")
        bot.send_message(chat_id, "⚠️ Видео не найдено, продолжаем...")

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➡️ Далее", callback_data="start_continue"))
    bot.send_message(chat_id, "Жми <b>Далее</b>, чтобы продолжить 👇", reply_markup=kb, disable_web_page_preview=True)

# ═══════════════════════════════════════════════════════════════════════════════
#                                СОГЛАСИЕ
# ═══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(func=lambda c: c.data == "start_continue")
def handle_start_continue(call: types.CallbackQuery) -> None:
    """Продолжение после приветствия"""
    chat_id = call.message.chat.id
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Согласен", callback_data="consent_and_start_survey"))
    consent_text = get_text_from_config('CONSENT_TEXT', CONSENT_TEXT)
    bot.send_message(chat_id, consent_text, reply_markup=kb, disable_web_page_preview=True)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "consent_and_start_survey")
def handle_consent_and_start_survey(call: types.CallbackQuery) -> None:
    """Обработка согласия и запуск опроса"""
    chat_id = call.message.chat.id
    with app.app_context():
        user = User.query.filter_by(telegram_id=str(chat_id)).first()
        if not user:
            bot.send_message(chat_id, "⚠️ Ошибка: пользователь не найден.")
            return

        try:
            user.consent_given = True
            db.session.commit()
            bot.answer_callback_query(call.id, "Согласие принято!")

            # Проверяем, включен ли опрос
            survey_enabled = get_text_from_config('SURVEY_ENABLED', 'true').lower() in ('true', '1', 'yes', 'on')

            if survey_enabled:
                start_survey(chat_id, user.id)
            else:
                # Если опрос отключен, помечаем как завершенный и переходим к главному меню
                user.survey_completed = True
                db.session.commit()
                show_main_menu(chat_id, first_time=True)

        except Exception as e:
            print(f"[handle_consent_and_start_survey] Ошибка: {e}")
            bot.send_message(chat_id, "⚠️ Ошибка при сохранении согласия.")

# ═══════════════════════════════════════════════════════════════════════════════
#                                ОПРОС
# ═══════════════════════════════════════════════════════════════════════════════

def start_survey(chat_id: int, user_id: int) -> None:
    """Запуск опроса"""
    try:
        with app.app_context():
            user = User.query.filter_by(telegram_id=str(chat_id)).first()
            if user and user.survey_completed:
                print(f"[start_survey] Опрос уже завершен для chat_id: {chat_id}")
                show_main_menu(chat_id, first_time=True)
                return

        if chat_id in user_states and user_states[chat_id].startswith("survey|"):
            print(f"[start_survey] Опрос уже активен для chat_id: {chat_id}")
            return

        user_states[chat_id] = "survey|0"
        send_survey_question(chat_id, user_id, 0)
        print(f"[start_survey] Опрос начат для chat_id: {chat_id}, user_id: {user_id}")

    except Exception as e:
        print(f"[start_survey] Ошибка: {e}")
        bot.send_message(chat_id, "⚠️ Ошибка при запуске опроса. Попробуйте снова: /start")
        user_states.pop(chat_id, None)

def handle_feedback_answer(message: types.Message, user: User, state: str) -> None:
    """Обработка ответов на фидбек"""
    chat_id = message.chat.id
    answer = message.text.strip()

    question_id = state.split("|")[1]

    try:
        with app.app_context():
            if question_id == "activity_rating":
                # Проверяем что ответ - число от 1 до 10
                try:
                    rating = int(answer)
                    if 1 <= rating <= 10:
                        # Сохраняем ответ
                        feedback = UserFeedback(user_id=user.id, question_id=question_id, answer=str(rating))
                        db.session.add(feedback)
                        db.session.commit()

                        # Переходим к следующему вопросу
                        user_states[chat_id] = "feedback|team_knowledge"

                        kb = types.InlineKeyboardMarkup(row_width=1)
                        kb.add(
                            types.InlineKeyboardButton("Да", callback_data="feedback_team_yes"),
                            types.InlineKeyboardButton("Нет", callback_data="feedback_team_no"),
                            types.InlineKeyboardButton("Узнал(а) на Дикой Мяте", callback_data="feedback_team_learned")
                        )

                        bot.send_message(chat_id, "Знаешь ли ты что-то про команду Авито?", reply_markup=kb)
                    else:
                        bot.send_message(chat_id, "Пожалуйста, отправь оценку от 1 до 10")
                except ValueError:
                    bot.send_message(chat_id, "Пожалуйста, отправь число от 1 до 10")

            elif question_id == "recommend_work":
                try:
                    rating = int(answer)
                    if 1 <= rating <= 10:
                        # Сохраняем ответ
                        feedback = UserFeedback(user_id=user.id, question_id=question_id, answer=str(rating))
                        db.session.add(feedback)
                        db.session.commit()

                        # Переходим к следующему вопросу
                        user_states[chat_id] = "feedback|self_apply"
                        bot.send_message(chat_id, "А с какой вероятность откликнулся(-ась) бы сам(а)?\nОтправь в ответ оценку от 1 до 10, где 10 — это 100% да")
                    else:
                        bot.send_message(chat_id, "Пожалуйста, отправь оценку от 1 до 10")
                except ValueError:
                    bot.send_message(chat_id, "Пожалуйста, отправь число от 1 до 10")

            elif question_id == "self_apply":
                try:
                    rating = int(answer)
                    if 1 <= rating <= 10:
                        # Сохраняем ответ
                        feedback = UserFeedback(user_id=user.id, question_id=question_id, answer=str(rating))
                        db.session.add(feedback)
                        db.session.commit()

                        # Завершаем фидбек
                        user_states.pop(chat_id, None)

                        final_text = """Спасибо! 
Будем рады видеть тебя на нашем карьерном сайте и среди сотрудников Авито! 
https://career.avito.com/"""

                        bot.send_message(chat_id, final_text, reply_markup=inline_back_to_menu())
                    else:
                        bot.send_message(chat_id, "Пожалуйста, отправь оценку от 1 до 10")
                except ValueError:
                    bot.send_message(chat_id, "Пожалуйста, отправь число от 1 до 10")

    except Exception as e:
        print(f"Error in handle_feedback_answer: {e}")
        bot.send_message(chat_id, "⚠️ Ошибка при обработке ответа.")
        user_states.pop(chat_id, None)

@bot.callback_query_handler(func=lambda c: c.data.startswith("feedback_team_"))
def handle_feedback_team_knowledge(call: types.CallbackQuery) -> None:
    """Обработка ответа на вопрос о знании команды"""
    chat_id = call.message.chat.id

    with app.app_context():
        user = User.query.filter_by(telegram_id=str(chat_id)).first()
        if not user:
            bot.answer_callback_query(call.id, "Ошибка: пользователь не найден.")
            return

        answer_map = {
            "feedback_team_yes": "Да",
            "feedback_team_no": "Нет", 
            "feedback_team_learned": "Узнал(а) на Дикой Мяте"
        }

        answer = answer_map.get(call.data, "Неизвестно")

        # Сохраняем ответ
        feedback = UserFeedback(user_id=user.id, question_id="team_knowledge", answer=answer)
        db.session.add(feedback)
        db.session.commit()

        # Переходим к следующему вопросу
        user_states[chat_id] = "feedback|recommend_work"
        bot.send_message(chat_id, "С какой вероятностью ты бы порекомендовал(а) работу в Авито своим друзьям и знакомым?\nОтправь в ответ оценку от 1 до 10, где 10 — это 100% рекомендовал(а) бы")

        bot.answer_callback_query(call.id)

def send_survey_question(chat_id: int, user_id: int, question_index: int) -> None:
    """Отправка вопроса опроса"""
    try:
        print(f"[send_survey_question] Отправляем вопрос {question_index} для chat_id: {chat_id}")
        
        if question_index < len(SURVEY_QUESTIONS):
            if question_index == 4:  # Вопрос о вакансиях
                kb = types.InlineKeyboardMarkup()
                kb.add(
                    types.InlineKeyboardButton("Маякую", callback_data="vacancy_yes"),
                    types.InlineKeyboardButton("Пока не готов(а)", callback_data="vacancy_no")
                )
                bot.send_message(chat_id, SURVEY_QUESTIONS[question_index], reply_markup=kb)
            else:
                bot.send_message(chat_id, SURVEY_QUESTIONS[question_index])
            print(f"[send_survey_question] Вопрос {question_index} отправлен")
        else:
            print(f"[send_survey_question] Опрос завершен для chat_id: {chat_id}")
            complete_survey(chat_id)
    except Exception as e:
        print(f"[send_survey_question] Ошибка: {e}")
        bot.send_message(chat_id, "⚠️ Ошибка при отправке вопроса. Попробуйте снова: /start")
        user_states.pop(chat_id, None)

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, "").startswith("survey|"))
def handle_survey_response(message: types.Message) -> None:
    """Обработка ответов на опрос"""
    chat_id = message.chat.id
    state = user_states.get(chat_id)
    if not state:
        return

    # Дополнительная защита от одновременных запросов
    if is_duplicate_callback(message.from_user.id):
        return

    try:
        step = int(state.split("|")[1])
        response = (message.text or "").strip()

        if not response:
            bot.send_message(chat_id, "Пожалуйста, напиши текстовый ответ.")
            return

        if len(response) > 200:
            bot.send_message(chat_id, "Ответ слишком длинный. Пожалуйста, сократи до 200 символов.")
            return

        with app.app_context():
            user = User.query.filter_by(telegram_id=str(chat_id)).first()
            if not user:
                bot.send_message(chat_id, "⚠️ Ошибка: пользователь не найден.")
                user_states.pop(chat_id, None)
                return

            # Сохраняем ответ в таблицу ответов
            existing_answer = SurveyAnswer.query.filter_by(user_id=user.id, step_num=str(step)).first()
            if existing_answer:
                existing_answer.answer_text = response
            else:
                answer = SurveyAnswer(user_id=user.id, step_num=str(step), answer_text=response)
                db.session.add(answer)

            # Сохраняем данные в соответствующие поля пользователя
            if step == 0:  # ФИО
                user.full_name = response
            elif step == 1:  # Город
                user.city = response
            elif step == 2:  # Профессиональная роль
                user.professional_role = response
            elif step == 3:  # Компания
                user.company = response

            # Также сохраняем в JSON для полной совместимости
            survey_data = user.get_survey_data()
            survey_data[f'step_{step}'] = response
            if step == 0:
                survey_data['full_name'] = response
            elif step == 1:
                survey_data['city'] = response
            elif step == 2:
                survey_data['professional_role'] = response
            elif step == 3:
                survey_data['company'] = response
            user.set_survey_data(survey_data)

            db.session.commit()
            print(f"[handle_survey_response] Ответ сохранен для шага {step}, переходим к шагу {step + 1}")

            next_step = step + 1
            user_states[chat_id] = f"survey|{next_step}"
            send_survey_question(chat_id, user.id, next_step)

    except Exception as e:
        print(f"[handle_survey_response] Ошибка: {e}")
        try:
            with app.app_context():
                db.session.rollback()
        except:
            pass
        bot.send_message(chat_id, "⚠️ Ошибка в опросе. Начни заново: /start")
        user_states.pop(chat_id, None)@bot.message_handler(func=lambda m: user_states.get(m.chat.id, "").startswith(("feedback|", "qr_step|")))
def handle_special_states(message: types.Message) -> None:
    """Обработка специальных состояний (фидбек и квест QR)"""
    chat_id = message.chat.id
    state = user_states.get(chat_id)

    with app.app_context():
        user = User.query.filter_by(telegram_id=str(chat_id)).first()

        if not user:
            bot.send_message(chat_id, "⚠️ Ошибка: пользователь не найден.")
            user_states.pop(chat_id, None)
            return

        # Обработка состояний квеста
        if state.startswith("qr_step|"):
            step = int(state.split("|")[1])
            handle_quest_qr(chat_id, user.id, step)
            return

        # Обработка фидбека
        if state.startswith("feedback|"):
            handle_feedback_answer(message, user, state)
            return

@bot.callback_query_handler(func=lambda c: c.data.startswith("vacancy_"))
def handle_vacancy_response(call: types.CallbackQuery) -> None:
    """Обработка ответа о вакансиях"""
    chat_id = call.message.chat.id
    
    # Защита от дублирования
    if is_duplicate_callback(call.from_user.id):
        bot.answer_callback_query(call.id)
        return
    
    try:
        with app.app_context():
            user = User.query.filter_by(telegram_id=str(chat_id)).first()
            if not user:
                bot.send_message(chat_id, "⚠️ Ошибка: пользователь не найден.")
                user_states.pop(chat_id, None)
                return

            existing_answer = SurveyAnswer.query.filter_by(user_id=user.id, step_num="4").first()
            if existing_answer:
                existing_answer.answer_text = call.data
            else:
                answer = SurveyAnswer(user_id=user.id, step_num="4", answer_text=call.data)
                db.session.add(answer)

            user.interested_in_vacancies = call.data == "vacancy_yes"
            
            # Сохраняем и в JSON
            survey_data = user.get_survey_data()
            survey_data['vacancy_interest'] = "Да" if call.data == "vacancy_yes" else "Нет"
            user.set_survey_data(survey_data)
            
            db.session.commit()
            bot.answer_callback_query(call.id)

            if call.data == "vacancy_yes":
                bot.send_message(chat_id, "Круто! После фестиваля мы свяжемся с тобой :)")
            else:
                bot.send_message(chat_id, "В любом случае не прощаемся! Будем рады видеть тебя на карьерном сайте и среди сотрудников Авито!")

            complete_survey(chat_id)
    except Exception as e:
        print(f"[handle_vacancy_response] Ошибка: {e}")
        bot.send_message(chat_id, "⚠️ Ошибка при сохранении ответа.")
        user_states.pop(chat_id, None)

# ═══════════════════════════════════════════════════════════════════════════════
#                                ГЛАВНОЕ МЕНЮ
# ═══════════════════════════════════════════════════════════════════════════════

def show_main_menu(chat_id: int, first_time: bool = False) -> None:
    """Отображение главного меню"""
    try:
        if first_time:
            text = get_text_from_config('MAIN_MENU_TEXT', MAIN_MENU_TEXT)
        else:
            text = get_text_from_config('MAIN_MENU_TEXT_NO_THANKS', MAIN_MENU_TEXT_NO_THANKS)
        print(f"Sending main menu to chat_id: {chat_id}, first_time: {first_time}")
        bot.send_message(chat_id, text, reply_markup=inline_main_menu())
        print(f"Main menu sent to chat_id: {chat_id}")
    except Exception as e:
        print(f"Error in show_main_menu for chat_id: {chat_id}, error: {str(e)}")
        try:
            bot.send_message(chat_id, "⚠️ Ошибка при отображении меню. Попробуйте снова.")
        except Exception as send_error:
            print(f"Failed to send error message in show_main_menu for chat_id: {chat_id}, error: {str(send_error)}")

# ═══════════════════════════════════════════════════════════════════════════════
#                                ОБРАБОТЧИКИ ГЛАВНОГО МЕНЮ
# ═══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(func=lambda c: c.data == "main")
def handle_main_menu(call: types.CallbackQuery) -> None:
    """Возврат в главное меню"""
    chat_id = call.message.chat.id
    
    # Защита от дублирования
    if is_duplicate_callback(call.from_user.id):
        bot.answer_callback_query(call.id)
        return
    
    try:
        bot.answer_callback_query(call.id)
        print(f"Handling main menu callback for chat_id: {chat_id}")
        show_main_menu(chat_id, first_time=False)
        print(f"Main menu sent to chat_id: {chat_id}")
    except Exception as e:
        print(f"Error in handle_main_menu for chat_id: {chat_id}, error: {str(e)}")
        bot.answer_callback_query(call.id)
        try:
            bot.send_message(chat_id, "⚠️ Ошибка при возврате в главное меню. Попробуйте снова.")
        except Exception as send_error:
            print(f"Failed to send error message to chat_id: {chat_id}, error: {str(send_error)}")

@bot.callback_query_handler(func=lambda c: c.data == "workshop")
def handle_workshop(call: types.CallbackQuery) -> None:
    """Обработка мастер-класса"""
    chat_id = call.message.chat.id
    
    if is_duplicate_callback(call.from_user.id):
        bot.answer_callback_query(call.id)
        return
        
    bot.answer_callback_query(call.id)
    try:
        workshop_text = get_cached_text('WORKSHOP_TEXT', WORKSHOP_TEXT)
        send_img_scaled(chat_id, MASTER_PATH, caption=workshop_text, kb=inline_map_and_menu())
        print(f"Workshop sent to chat_id: {chat_id}")
    except Exception as e:
        bot.send_message(chat_id, "⚠️ Ошибка при загрузке мастер-класса.")
        print(f"Error in handle_workshop: {e}")

@bot.callback_query_handler(func=lambda c: c.data == "forest")
def handle_forest(call: types.CallbackQuery) -> None:
    """Обработка раздела леса"""
    chat_id = call.message.chat.id
    
    if is_duplicate_callback(call.from_user.id):
        bot.answer_callback_query(call.id)
        return
        
    bot.answer_callback_query(call.id)
    try:
        forest_text_1 = get_cached_text('FOREST_TEXT_1', FOREST_TEXT_1)
        forest_text_2 = get_cached_text('FOREST_TEXT_2', FOREST_TEXT_2)
        send_img_scaled(chat_id, FOREST_PATH, caption=forest_text_1)
        time.sleep(0.5)  # Уменьшаем задержку
        bot.send_message(chat_id, forest_text_2, reply_markup=inline_map_and_menu())
        print(f"Forest sent to chat_id: {chat_id}")
    except Exception as e:
        bot.send_message(chat_id, "⚠️ Ошибка при загрузке раздела леса.")
        print(f"Error in handle_forest: {e}")

@bot.callback_query_handler(func=lambda c: c.data == "map")
def handle_map(call: types.CallbackQuery) -> None:
    """Обработка карты"""
    chat_id = call.message.chat.id
    
    if is_duplicate_callback(call.from_user.id):
        bot.answer_callback_query(call.id)
        return
        
    bot.answer_callback_query(call.id)
    try:
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("Карта в PDF", url="https://disk.yandex.ru/i/Z5n3QI9aH8WNWA"),
            types.InlineKeyboardButton("⬅️ Главное меню", callback_data="main")
        )
        map_text = get_cached_text('MAP_TEXT', MAP_TEXT)
        send_img_scaled(chat_id, MAP_PATH, caption=map_text, kb=kb)
        print(f"Map sent to chat_id: {chat_id}")
    except Exception as e:
        bot.send_message(chat_id, "⚠️ Ошибка при загрузке карты.")
        print(f"Error in handle_map: {e}")

@bot.callback_query_handler(func=lambda c: c.data == "career")
def handle_career(call: types.CallbackQuery) -> None:
    """Обработка карьеры"""
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id)
    try:
        career_text = get_cached_text('CAREER_MESSAGE', CAREER_MESSAGE)
        bot.send_message(chat_id, career_text, reply_markup=inline_map_and_menu())
        print(f"Career message sent to chat_id: {chat_id}")
    except Exception as e:
        bot.send_message(chat_id, "⚠️ Ошибка при отправке сообщения о карьере.")
        print(f"Error in handle_career: {e}")

@bot.callback_query_handler(func=lambda c: c.data == "schedule")
def handle_schedule(call: types.CallbackQuery) -> None:
    """Обработка расписания"""
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id)
    try:
        schedule_text = get_cached_text('SCHEDULE_MESSAGE', SCHEDULE_MESSAGE)
        bot.send_message(chat_id, schedule_text, reply_markup=inline_map_and_menu())
        print(f"Schedule sent to chat_id: {chat_id}")
    except Exception as e:
        bot.send_message(chat_id, "⚠️ Ошибка при отправке расписания.")
        print(f"Error in handle_schedule: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
#                                ТАНЦЫ
# ═══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(func=lambda c: c.data == "dance")
def handle_dance(call: types.CallbackQuery) -> None:
    """Обработка танцев - показать описание"""
    chat_id = call.message.chat.id
    
    if is_duplicate_callback(call.from_user.id):
        bot.answer_callback_query(call.id)
        return
        
    bot.answer_callback_query(call.id)

    try:
        dance_intro = get_cached_text('DANCE_INTRO', DANCE_INTRO)

        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton("💃 Записаться", callback_data="dance_show_slots"),
            types.InlineKeyboardButton("⬅️ Главное меню", callback_data="main")
        )

        send_img_scaled(chat_id, DANCE_PATH, caption=dance_intro, kb=kb)
        print(f"Dance intro sent to chat_id: {chat_id}")
    except Exception as e:
        bot.send_message(chat_id, "⚠️ Ошибка при загрузке танцев.")
        print(f"Error in handle_dance: {e}")

@bot.callback_query_handler(func=lambda c: c.data == "dance_show_slots")
def handle_dance_show_slots(call: types.CallbackQuery) -> None:
    """Показать слоты для записи на танцы"""
    chat_id = call.message.chat.id
    try:
        dance_slots = get_dance_slots_from_cache()

        if not dance_slots:
            bot.send_message(chat_id, "❌ Слоты для танцев пока не настроены.", reply_markup=inline_back_to_menu())
            bot.answer_callback_query(call.id)
            return

        dance_choose_slot = get_cached_text('DANCE_CHOOSE_SLOT', DANCE_CHOOSE_SLOT)

        # Получаем все регистрации одним запросом
        with app.app_context():
            registrations_count = {}
            all_registrations = Registration.query.filter_by(activity_type='dance').all()
            for reg in all_registrations:
                key = f"{reg.day}|{reg.time_slot}"
                registrations_count[key] = registrations_count.get(key, 0) + 1

        kb = types.InlineKeyboardMarkup(row_width=1)
        for slot in dance_slots:
            key = f"{slot['day']}|{slot['time_slot']}"
            current_registrations = registrations_count.get(key, 0)
            max_participants = slot.get('max_participants', 10)
            available_spots = max_participants - current_registrations

            if available_spots > 0:
                button_text = f"{slot['day']} {slot['time_slot']} (свободно: {available_spots})"
                callback_data = f"dance_register|{slot['day']}|{slot['time_slot']}"
            else:
                button_text = f"{slot['day']} {slot['time_slot']} (мест нет)"
                callback_data = f"dance_full|{slot['day']}|{slot['time_slot']}"

            kb.add(types.InlineKeyboardButton(button_text, callback_data=callback_data))

        kb.add(
            types.InlineKeyboardButton("⬅️ Назад", callback_data="dance"),
            types.InlineKeyboardButton("🏠 Главное меню", callback_data="main")
        )

        bot.send_message(chat_id, dance_choose_slot, reply_markup=kb)
        bot.answer_callback_query(call.id)
        print(f"Dance slots sent to chat_id: {chat_id}")
    except Exception as e:
        bot.send_message(chat_id, "⚠️ Ошибка при загрузке слотов.")
        bot.answer_callback_query(call.id)
        print(f"Error in handle_dance_show_slots: {e}")

@bot.callback_query_handler(func=lambda c: c.data.startswith("dance_register|"))
def handle_dance_registration(call: types.CallbackQuery) -> None:
    """Регистрация на танцы"""
    chat_id = call.message.chat.id
    try:
        _, day, time_slot = call.data.split("|")
        user_id = get_user_id(chat_id)

        with app.app_context():
            existing = Registration.query.filter_by(
                user_id=user_id,
                activity_type='dance',
                day=day,
                time_slot=time_slot
            ).first()

            if existing:
                bot.answer_callback_query(call.id, "Вы уже записаны на этот слот!")
                return

            current_count = Registration.query.filter_by(
                activity_type='dance',
                day=day,
                time_slot=time_slot
            ).count()

            from models import DanceSlot
            slot = DanceSlot.query.filter_by(day=day, time_slot=time_slot).first()
            max_participants = slot.max_participants if slot else 10

            if current_count >= max_participants:
                bot.answer_callback_query(call.id, "Места закончились!")
                return

            registration = Registration(
                user_id=user_id,
                activity_type='dance',
                day=day,
                time_slot=time_slot
            )
            db.session.add(registration)
            db.session.commit()

            confirmation_text = get_cached_text('DANCE_CONFIRMATION', DANCE_CONFIRMATION).format(slot=f"{day} {time_slot}")
            bot.answer_callback_query(call.id, "Успешно записаны!")
            bot.send_message(chat_id, confirmation_text, reply_markup=inline_back_to_menu())

    except Exception as e:
        print(f"Error in dance registration: {e}")
        bot.answer_callback_query(call.id, "Ошибка регистрации")

# ═══════════════════════════════════════════════════════════════════════════════
#                                СТИКЕРЫ
# ═══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(func=lambda c: c.data == "sticker")
def handle_sticker(call: types.CallbackQuery) -> None:
    """Обработка стикеров"""
    chat_id = call.message.chat.id
    user_id = get_user_id(chat_id)

    with app.app_context():
        # Ищем любой успешный стикерпак пользователя
        sticker_gen = StickerGeneration.query.filter_by(user_id=user_id, status='ok').first()

        if sticker_gen:
            # Если есть URL стикерпака - показываем его
            if sticker_gen.pack_url:
                logging.info(f"[{chat_id}] Found existing sticker pack: {sticker_gen.pack_url}")
                
                kb = types.InlineKeyboardMarkup(row_width=1)
                kb.add(
                    types.InlineKeyboardButton("📦 Открыть стикерпак", url=sticker_gen.pack_url),
                    types.InlineKeyboardButton("⬅️ Главное меню", callback_data="main")
                )
                
                bot.send_message(
                    chat_id,
                    f"✅ Твой стикерпак уже готов!\n\n"
                    f"Ссылка: {sticker_gen.pack_url}\n\n"
                    f"Добавь его в Telegram и пользуйся! 🎉",
                    reply_markup=kb
                )
                bot.answer_callback_query(call.id)
                return
            
            # Если нет URL, но статус 'ok' - пытаемся восстановить ссылку
            elif sticker_gen.sticker_set_name:
                pack_url = f"https://t.me/addstickers/{sticker_gen.sticker_set_name}"
                
                # Проверяем, существует ли стикерпак
                try:
                    bot.get_sticker_set(sticker_gen.sticker_set_name)
                    # Стикерпак существует - обновляем URL и показываем
                    sticker_gen.pack_url = pack_url
                    sticker_gen.sticker_set_link = pack_url
                    db.session.commit()
                    
                    logging.info(f"[{chat_id}] Restored sticker pack URL: {pack_url}")
                    
                    kb = types.InlineKeyboardMarkup(row_width=1)
                    kb.add(
                        types.InlineKeyboardButton("📦 Открыть стикерпак", url=pack_url),
                        types.InlineKeyboardButton("⬅️ Главное меню", callback_data="main")
                    )
                    
                    bot.send_message(
                        chat_id,
                        f"✅ Твой стикерпак уже готов!\n\n"
                        f"Ссылка: {pack_url}\n\n"
                        f"Добавь его в Telegram и пользуйся! 🎉",
                        reply_markup=kb
                    )
                    bot.answer_callback_query(call.id)
                    return
                except Exception as e:
                    # Стикерпак не существует - удаляем запись
                    logging.info(f"[{chat_id}] Sticker pack in DB but doesn't exist: {e}")
                    db.session.delete(sticker_gen)
                    db.session.commit()

        # Проверяем статус pending
        pending_gen = StickerGeneration.query.filter_by(user_id=user_id, status='pending').first()
        if pending_gen:
            bot.send_message(
                chat_id,
                "⏳ Твой стикерпак уже в процессе генерации. Это может занять несколько минут.",
                reply_markup=inline_back_to_menu()
            )
        else:
            sticker_text = get_cached_text('STICKER_START_MESSAGE', STICKER_START_MESSAGE)
            bot.send_message(chat_id, sticker_text, reply_markup=inline_back_to_menu())
            user_states[chat_id] = "waiting_for_sticker_photo"

    bot.answer_callback_query(call.id)

@bot.message_handler(content_types=['photo'], func=lambda m: user_states.get(m.chat.id) == "waiting_for_sticker_photo")
def handle_sticker_photo(message: types.Message) -> None:
    """Обработка фото для генерации стикера"""
    chat_id = message.chat.id
    user_id = get_user_id(chat_id)

    try:
        user_states.pop(chat_id, None)

        with app.app_context():
            existing_gen = StickerGeneration.query.filter_by(user_id=user_id).first()
            if existing_gen and existing_gen.status == 'pending':
                bot.send_message(chat_id, "⏳ У тебя уже есть стикерпак в процессе генерации.")
                return
            elif existing_gen and existing_gen.pack_url:
                bot.send_message(chat_id, f"✅ У тебя уже есть готовый стикерпак: {existing_gen.pack_url}")
                return

        file_info = bot.get_file(message.photo[-1].file_id)
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"

        bot.send_message(chat_id, "⏳ Отлично! Начинаю генерацию твоего стикерпака. Это займет около 2-3 минут...")

        with app.app_context():
            sticker_gen = StickerGeneration(
                user_id=user_id,
                status='pending'
            )
            db.session.add(sticker_gen)
            db.session.commit()

        def generate_async():
            try:
                sticker_buf, file_id, pack_url, error_msg = generate_sticker_from_user_photo(file_url, chat_id, bot)

                if error_msg:
                    kb = types.InlineKeyboardMarkup()
                    if pack_url:
                        kb.add(types.InlineKeyboardButton("📦 Открыть стикерпак", url=pack_url))
                    kb.add(types.InlineKeyboardButton("⬅️ Главное меню", callback_data="main"))
                    bot.send_message(chat_id, error_msg, reply_markup=kb)
                elif pack_url:
                    kb = types.InlineKeyboardMarkup(row_width=1)
                    kb.add(
                        types.InlineKeyboardButton("📦 Открыть стикерпак", url=pack_url),
                        types.InlineKeyboardButton("⬅️ Главное меню", callback_data="main")
                    )
                    bot.send_message(
                        chat_id,
                        f"🎉 Твой стикерпак готов!\n\n"
                        f"Ссылка: {pack_url}\n\n"
                        f"Добавь его в Telegram и пользуйся!",
                        reply_markup=kb
                    )
                else:
                    bot.send_message(chat_id, "⚠️ Не удалось создать стикерпак. Попробуй еще раз с другим фото.")
            except Exception as e:
                print(f"Error in sticker generation: {e}")
                bot.send_message(chat_id, "⚠️ Произошла ошибка при генерации стикера.")

        threading.Thread(target=generate_async, daemon=True).start()

    except Exception as e:
        print(f"Error processing sticker photo: {e}")
        bot.send_message(chat_id, "⚠️ Ошибка при обработке фото.")
        user_states.pop(chat_id, None)

# ═══════════════════════════════════════════════════════════════════════════════
#                                КВЕСТ
# ═══════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(func=lambda c: c.data == "quest")
def handle_quest(call: types.CallbackQuery) -> None:
    """Обработка квеста"""
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id)

    if is_duplicate_callback(call.from_user.id):
        return

    user_id = get_user_id(chat_id)

    try:
        quest_total = get_quest_total_steps()
        completed_steps = user_quest_completed_steps(user_id)
        progress_text = get_quest_progress(user_id)

        if len(completed_steps) == quest_total:
            bot.send_message(
                chat_id,
                f"🎉 Поздравляем! Ты уже завершил квест!\n\n"
                f"{progress_text}\n\n"
                f"Если ты еще не получил приз, приходи в лаунж Avito Team! 🏆",
                reply_markup=inline_back_to_menu()
            )
            return

        hint, sticker_path = get_quest_hint(0)

        if hint:
            formatted_hint = format_quest_text(hint)
            message_text = f"{formatted_hint}\n\n{progress_text}"

            kb = types.InlineKeyboardMarkup(row_width=1)
            kb.add(
                types.InlineKeyboardButton("🔍 К подсказкам", callback_data="quest_hints"),
                types.InlineKeyboardButton("⬅️ Главное меню", callback_data="main")
            )

            if sticker_path and os.path.exists(sticker_path):
                send_img_scaled(chat_id, sticker_path, caption=message_text, kb=kb)
            else:
                bot.send_message(chat_id, message_text, reply_markup=kb)
        else:
            bot.send_message(
                chat_id,
                f"⚠️ Ошибка загрузки квеста.\n\n{progress_text}",
                reply_markup=inline_back_to_menu()
            )

        print(f"Quest start sent to chat_id: {chat_id}")

    except Exception as e:
        bot.send_message(chat_id, "⚠️ Ошибка при загрузке квеста.")
        print(f"Error in handle_quest: {e}")

@bot.callback_query_handler(func=lambda c: c.data == "quest_hints")
def handle_quest_hints(call: types.CallbackQuery) -> None:
    """Показать подсказки квеста"""
    chat_id = call.message.chat.id
    user_id = get_user_id(chat_id)

    try:
        quest_total = get_quest_total_steps()
        completed_steps = user_quest_completed_steps(user_id)

        if len(completed_steps) == quest_total:
            bot.send_message(
                chat_id,
                f"🎉 Поздравляем! Ты уже завершил квест!\n\n"
                f"Если ты еще не получил приз, приходи в лаунж Avito Team! 🏆",
                reply_markup=inline_back_to_menu()
            )
            bot.answer_callback_query(call.id)
            return

        next_step = next_quest_step_for_user(user_id)
        if not next_step:
            next_step = 1

        hint, _ = get_quest_hint(next_step)
        progress_text = get_quest_progress(user_id)

        if hint:
            formatted_hint = format_quest_text(hint)
            message_text = f"🔍 <b>Подсказка {next_step}:</b>\n\n{formatted_hint}\n\n{progress_text}"

            kb = types.InlineKeyboardMarkup(row_width=1)
            if next_step < quest_total:
                kb.add(types.InlineKeyboardButton("🔍 Еще подсказка", callback_data=f"quest_next_hint|{next_step + 1}"))
            kb.add(
                types.InlineKeyboardButton("⬅️ Назад", callback_data="quest"),
                types.InlineKeyboardButton("🏠 Главное меню", callback_data="main")
            )

            bot.send_message(chat_id, message_text, reply_markup=kb)
        else:
            bot.send_message(
                chat_id,
                f"⚠️ Подсказка не найдена.\n\n{progress_text}",
                reply_markup=inline_back_to_menu()
            )

        bot.answer_callback_query(call.id)
        print(f"Quest hint {next_step} sent to chat_id: {chat_id}")

    except Exception as e:
        bot.send_message(chat_id, "⚠️ Ошибка при загрузке подсказки.")
        bot.answer_callback_query(call.id)
        print(f"Error in handle_quest_hints: {e}")

@bot.callback_query_handler(func=lambda c: c.data.startswith("quest_next_hint|"))
def handle_quest_next_hint(call: types.CallbackQuery) -> None:
    """Показать следующую подсказку"""
    chat_id = call.message.chat.id
    user_id = get_user_id(chat_id)

    try:
        step = int(call.data.split("|")[1])
        quest_total = get_quest_total_steps()

        if step > quest_total:
            bot.answer_callback_query(call.id, "Это была последняя подсказка!")
            return

        hint, _ = get_quest_hint(step)
        progress_text = get_quest_progress(user_id)

        if hint:
            formatted_hint = format_quest_text(hint)
            message_text = f"🔍 <b>Подсказка {step}:</b>\n\n{formatted_hint}\n\n{progress_text}"

            kb = types.InlineKeyboardMarkup(row_width=1)
            if step < quest_total:
                kb.add(types.InlineKeyboardButton("🔍 Еще подсказка", callback_data=f"quest_next_hint|{step + 1}"))
            kb.add(
                types.InlineKeyboardButton("⬅️ Назад", callback_data="quest"),
                types.InlineKeyboardButton("🏠 Главное меню", callback_data="main")
            )

            bot.edit_message_text(
                message_text,
                chat_id,
                call.message.message_id,
                reply_markup=kb
            )
            bot.answer_callback_query(call.id)
        else:
            bot.answer_callback_query(call.id, "Подсказка не найдена!")

    except Exception as e:
        bot.answer_callback_query(call.id, "Ошибка при загрузке подсказки")
        print(f"Error in handle_quest_next_hint: {e}")

def complete_survey(chat_id: int) -> None:
    """Завершение опроса"""
    try:
        with app.app_context():
            user = User.query.filter_by(telegram_id=str(chat_id)).first()
            if user:
                user.survey_completed = True
                db.session.commit()
                print(f"[complete_survey] Опрос завершен для chat_id: {chat_id}")

        user_states.pop(chat_id, None)
        show_main_menu(chat_id, first_time=True)
    except Exception as e:
        print(f"[complete_survey] Ошибка: {e}")
        with app.app_context():
            db.session.rollback()
        user_states.pop(chat_id, None)
        bot.send_message(chat_id, "⚠️ Ошибка при завершении опроса. Попробуйте снова: /start")

# ═══════════════════════════════════════════════════════════════════════════════
#                                ЗАПУСК БОТА
# ═══════════════════════════════════════════════════════════════════════════════

def start_bot_polling_only() -> None:
    """Запуск бота только в polling режиме"""
    print("🤖 Starting bot in polling mode...")
    try:
        # Принудительно удаляем webhook
        bot.remove_webhook()
        print("✅ Webhook removed successfully")
        import time
        time.sleep(1)  # Даем время Telegram API обработать удаление
    except Exception as e:
        print(f"Warning: Could not remove webhook: {e}")
    
    retry_count = 0
    max_retries = 5
    
    while retry_count < max_retries:
        try:
            print("✅ Bot started successfully in polling mode")
            bot.polling(none_stop=True, interval=1, timeout=20)
            break  # Если polling завершился нормально
        except Exception as e:
            error_str = str(e).lower()
            
            if "409" in error_str and "conflict" in error_str:
                retry_count += 1
                wait_time = min(30, 5 * retry_count)  # Увеличиваем время ожидания
                logging.error(f"❌ Конфликт экземпляров бота (409). Попытка {retry_count}/{max_retries}. Ожидание {wait_time} сек...")
                print(f"❌ Обнаружен другой экземпляр бота. Остановите все другие workflow и попробуйте снова.")
                
                if retry_count >= max_retries:
                    logging.error("❌ Превышено максимальное количество попыток. Остановите все другие экземпляры бота.")
                    break
                
                time.sleep(wait_time)
                
                # Пытаемся снова удалить webhook
                try:
                    bot.remove_webhook()
                    time.sleep(2)
                except:
                    pass
            else:
                logging.error(f"Error in polling: {e}")
                try:
                    while True:
                        try:
                            bot.polling(none_stop=False, interval=2, timeout=10)
                        except Exception as polling_error:
                            if "409" in str(polling_error).lower():
                                logging.error("❌ Конфликт экземпляров. Остановите другие workflow.")
                                time.sleep(10)
                            else:
                                logging.error(f"Polling error: {polling_error}")
                                time.sleep(5)
                except KeyboardInterrupt:
                    logging.info("Bot stopped by user")
                break

def start_bot() -> None:
    """Обратная совместимость - использует polling"""
    start_bot_polling_only()

if __name__ == "__main__":
    start_bot()