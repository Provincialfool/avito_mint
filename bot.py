import os
import io
import time
import json
import threading
import queue

from datetime import datetime
from uuid import uuid4
from typing import Optional

from PIL import Image
from flask import request

import telebot
from telebot import types

from app import app, db
from models import User, Registration, QuestProgress, StickerGeneration, ScheduledMessage, SurveyAnswer
from text import (
    CONSENT_TEXT, SURVEY_QUESTIONS, MAIN_MENU_TEXT, MAIN_MENU_TEXT_NO_THANKS,
    DANCE_INTRO, DANCE_CHOOSE_SLOT, DANCE_CONFIRMATION, DANCE_FULL_MESSAGE, DANCE_ALL_FULL,
    WORKSHOP_TEXT, FOREST_TEXT_1, FOREST_TEXT_2, STICKER_START_MESSAGE, CAREER_MESSAGE,
    SCHEDULE_MESSAGE, MAP_TEXT, IMG_DIR, MAP_PATH, MAP_SENT_PATH, MAP_FOREST_PATH,
    MASTER_PATH, QUEST_PATH, DANCE_PATH, FOREST_PATH, DANCE_SLOTS, MAX_SLOTS_PER_DANCE,
    QUEST_STEPS, QUEST_TOTAL_STEPS
)
from sticker_generator import generate_sticker_from_user_photo

# ──────────────────── TELEGRAM ──────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not defined in environment variables")
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

user_states: dict[int, str] = {}          # chat_id -> state
callback_to_handler: dict[str, callable] = {}

# Очередь для обработки генерации стикеров
sticker_queue = queue.Queue()

# ──────────────────── UTILS ──────────────────────
def get_user_id(chat_id: int) -> Optional[int]:
    with app.app_context():
        user = User.query.filter_by(telegram_id=str(chat_id)).first()
        if not user:
            user = User(telegram_id=str(chat_id), consent_given=False)
            db.session.add(user)
            db.session.commit()
        return user.id

def send_img_scaled(chat_id: int, path: str, caption: str = "", kb=None, max_px: int = 700) -> None:
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

def inline_main_menu() -> types.InlineKeyboardMarkup:
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
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⬅️ Главное меню", callback_data="main"))
    return kb

def inline_map_and_menu() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("⬅️ Главное меню", callback_data="main"),
        types.InlineKeyboardButton("🗺️ Карта", callback_data="map")
    )
    return kb

def register_callback(name: str):
    def wrapper(fn):
        callback_to_handler[name] = fn
        return fn
    return wrapper
    

# ──────────────────── QUEST UTILS ──────────────────────
def get_quest_hint(step: int) -> tuple[Optional[str], Optional[str]]:
    if 0 <= step < len(QUEST_STEPS):
        return QUEST_STEPS[step]["hint"], QUEST_STEPS[step]["sticker_path"]
    return None, None

def get_quest_progress(user_id: int) -> str:
    with app.app_context():
        progress = db.session.query(QuestProgress).filter_by(user_id=user_id).first()
        if not progress:
            progress = QuestProgress(user_id=user_id, completed_steps="[]")
            db.session.add(progress)
            db.session.commit()
        count = len(json.loads(progress.completed_steps or "[]"))
        return f"Стикеров найдено: {count} из {QUEST_TOTAL_STEPS}"

def register_quest_step(user_id: int, step: int) -> None:
    with app.app_context():
        progress = QuestProgress.query.filter_by(user_id=user_id).first()
        if not progress:
            progress = QuestProgress(user_id=user_id, completed_steps="[]")
            db.session.add(progress)
        steps = json.loads(progress.completed_steps or "[]")
        if step not in steps and 1 <= step <= QUEST_TOTAL_STEPS:
            steps.append(step)
            progress.completed_steps = json.dumps(steps)
            if len(steps) == QUEST_TOTAL_STEPS:
                progress.completed = True
                progress.completed_at = datetime.utcnow()
            db.session.commit()

def user_quest_completed_steps(user_id: int) -> list:
    with app.app_context():
        progress = QuestProgress.query.filter_by(user_id=user_id).first()
        return json.loads(progress.completed_steps or "[]") if progress else []

def next_quest_step_for_user(user_id: int) -> Optional[int]:
    found = user_quest_completed_steps(user_id)
    for step in range(1, QUEST_TOTAL_STEPS + 1):
        if step not in found:
            return step
    return None

# ──────────────────── SCHEDULED MESSAGES ──────────────────────
def scheduled_sender() -> None:
    while True:
        with app.app_context():
            now = datetime.utcnow()
            pending = ScheduledMessage.query.filter(
                ScheduledMessage.scheduled_time <= now,
                ScheduledMessage.sent.is_(False),
            ).all()
            for msg in pending:
                try:
                    bot.send_message(msg.chat_id, msg.message_text)
                    msg.sent = True
                    db.session.commit()
                except Exception as e:
                    print(f"Error in scheduled_sender: {e}")
        time.sleep(30)

threading.Thread(target=scheduled_sender, daemon=True).start()

@bot.callback_query_handler(func=lambda c: c.data == "main")
def handle_main_menu(call: types.CallbackQuery) -> None:
    try:
        chat_id = call.message.chat.id
        print(f"Handling main menu callback for chat_id: {chat_id}")
        show_main_menu(chat_id, first_time=False)
        bot.answer_callback_query(call.id)
        print(f"Main menu sent to chat_id: {chat_id}")
    except Exception as e:
        print(f"Error in handle_main_menu for chat_id: {chat_id}, error: {str(e)}")
        try:
            bot.send_message(chat_id, "⚠️ Ошибка при возврате в главное меню. Попробуйте снова.")
        except Exception as send_error:
            print(f"Failed to send error message to chat_id: {chat_id}, error: {str(send_error)}")

# ──────────────────── /START ──────────────────────
@bot.message_handler(commands=["start"])
def handle_start(message: types.Message) -> None:
    chat_id = message.chat.id
    payload = message.text.split(maxsplit=1)[1].strip().lower() if len(message.text.split()) > 1 else None
    print(f"[start] chat_id={chat_id}, payload={payload}")

    with app.app_context():
        user = User.query.filter_by(telegram_id=str(chat_id)).first()
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

    try:
        with open("circle.mp4", "rb") as f:
            bot.send_video_note(chat_id, f)
    except Exception as e:
        print(f"[start] Ошибка отправки видео: {e}")
        bot.send_message(chat_id, "⚠️ Видео не найдено, продолжаем...")

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➡️ Далее", callback_data="start_continue"))
    bot.send_message(chat_id, "Жми <b>Далее</b>, чтобы продолжить 👇", reply_markup=kb, disable_web_page_preview=True)


@bot.callback_query_handler(func=lambda c: c.data == "start_continue")
def handle_start_continue(call: types.CallbackQuery) -> None:
    chat_id = call.message.chat.id
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Согласен", callback_data="consent_and_start_survey"))
    bot.send_message(chat_id, CONSENT_TEXT, reply_markup=kb, disable_web_page_preview=True)
    bot.answer_callback_query(call.id)


# ──────────────────── survey ──────────────────────
'''
def start_survey(chat_id: int, user_id: int) -> None:
    try:
        # Проверяем, не начат ли опрос
        if chat_id in user_states and user_states[chat_id].startswith("survey|"):
            print(f"[start_survey] Опрос уже активен для chat_id: {chat_id}, state: {user_states[chat_id]}")
            return

        # Устанавливаем начальное состояние опроса
        user_states[chat_id] = "survey|0"
        send_survey_question(chat_id, 0)
        print(f"[start_survey] Опрос начат для chat_id: {chat_id}, user_id: {user_id}")

    except Exception as e:
        print(f"[start_survey] Ошибка при запуске опроса для chat_id: {chat_id}, error: {str(e)}")
        bot.send_message(chat_id, "⚠️ Ошибка при запуске опроса. Попробуйте снова: /start")
        user_states.pop(chat_id, None)

def send_survey_question(chat_id: int, step: int) -> None:
    if step < len(SURVEY_QUESTIONS):
        if step == 4:  # Вопрос о вакансиях
            kb = types.InlineKeyboardMarkup()
            kb.add(
                types.InlineKeyboardButton("Маякую", callback_data="vacancy_yes"),
                types.InlineKeyboardButton("Пока не готов(а)", callback_data="vacancy_no")
            )
            bot.send_message(chat_id, SURVEY_QUESTIONS[step], reply_markup=kb)
        else:
            bot.send_message(chat_id, SURVEY_QUESTIONS[step])
    else:
        complete_survey(chat_id)

def complete_survey(chat_id: int) -> None:
    with app.app_context():
        user = User.query.filter_by(telegram_id=str(chat_id)).first()
        if user:
            user.survey_completed = True
            db.session.commit()
    
    user_states.pop(chat_id, None)
    bot.send_message(chat_id, "Спасибо за ответы! 🎉")
    show_main_menu(chat_id, first_time=True)

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, "").startswith("survey|"))
def handle_survey_response(message: types.Message) -> None:
    chat_id = message.chat.id
    state = user_states.get(chat_id)
    if not state:
        return

    try:
        step = int(state.split("|")[1])
        response = (message.text or "").strip()

        if not response:
            bot.send_message(chat_id, "Пожалуйста, напиши текстовый ответ.")
            return

        with app.app_context():
            user = User.query.filter_by(telegram_id=str(chat_id)).first()
            if not user:
                bot.send_message(chat_id, "⚠️ Ошибка: пользователь не найден.")
                user_states.pop(chat_id, None)
                return

            answer = SurveyAnswer(user_id=user.id, step_num=str(step), answer_text=response)
            db.session.add(answer)
            db.session.commit()

        next_step = step + 1
        user_states[chat_id] = f"survey|{next_step}"
        send_survey_question(chat_id, next_step)

    except Exception as e:
        print(f"[handle_survey_response] Unexpected error: {e}")
        bot.send_message(chat_id, "⚠️ Ошибка в опросе. Начни заново: /start")
        user_states.pop(chat_id, None)

@bot.callback_query_handler(func=lambda c: c.data.startswith("vacancy_"))
def handle_vacancy_response(call: types.CallbackQuery) -> None:
    chat_id = call.message.chat.id
    with app.app_context():
        user = User.query.filter_by(telegram_id=str(chat_id)).first()
        if not user:
            bot.send_message(chat_id, "⚠️ Ошибка: пользователь не найден.")
            user_states.pop(chat_id, None)
            return

        try:
            answer = SurveyAnswer(user_id=user.id, step_num="4", answer_text=call.data)
            db.session.add(answer)
            user.interested_in_vacancies = call.data == "vacancy_yes"
            db.session.commit()
            bot.answer_callback_query(call.id)

            if call.data == "vacancy_yes":
                bot.send_message(chat_id, "Круто! После фестиваля мы свяжемся с тобой :)")
            else:
                bot.send_message(chat_id, "В любом случае не прощаемся! Будем рады видеть тебя на карьерном сайте и среди сотрудников Авито!")
            
            complete_survey(chat_id)
        except Exception as e:
            print(f"[handle_vacancy_response] Ошибка при сохранении ответа: {e}")
            bot.send_message(chat_id, "⚠️ Ошибка при сохранении ответа.")
            user_states.pop(chat_id, None)
'''

# Исправляем handler для consent_ok, чтобы опрос запускался сразу
@bot.callback_query_handler(func=lambda c: c.data == "consent_and_start_survey")
def handle_consent_and_start_survey(call: types.CallbackQuery) -> None:
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
            
            # Закомментированный код опроса
            # start_survey(chat_id, user.id)
            
            # Сразу показываем благодарность и главное меню
            bot.send_message(chat_id, "Спасибо! 🎉")
            show_main_menu(chat_id, first_time=True)
        except Exception as e:
            print(f"[handle_consent_and_start_survey] Ошибка: {e}")
            bot.send_message(chat_id, "⚠️ Ошибка при сохранении согласия.")


# ──────────────────── MAIN MENU ──────────────────────
def show_main_menu(chat_id: int, first_time: bool = False) -> None:
    try:
        text = MAIN_MENU_TEXT if first_time else MAIN_MENU_TEXT_NO_THANKS
        print(f"Sending main menu to chat_id: {chat_id}, first_time: {first_time}, text: {text[:50]}...")
        bot.send_message(chat_id, text, reply_markup=inline_main_menu())
        print(f"Main menu sent to chat_id: {chat_id}")
    except Exception as e:
        print(f"Error in show_main_menu for chat_id: {chat_id}, error: {str(e)}")
        try:
            bot.send_message(chat_id, "⚠️ Ошибка при отображении меню. Попробуйте снова.")
        except Exception as send_error:
            print(f"Failed to send error message in show_main_menu for chat_id: {chat_id}, error: {str(send_error)}")

# ──────────────────── INLINE ROUTING ──────────────────────
@bot.callback_query_handler(
    func=lambda c: c.data in callback_to_handler
)
def handle_inline_router(call: types.CallbackQuery) -> None:
    try:
        chat_id = call.message.chat.id
        print(f"Handling callback for chat_id: {chat_id}, data: {call.data}")
        bot.answer_callback_query(call.id)

        handler = callback_to_handler.get(call.data)
        if handler:
            print(f"Calling registered handler for callback: {call.data}")
            handler(call.message)
        else:
            print(f"No handler registered for callback: {call.data}")
            bot.send_message(chat_id, "⚠️ Неизвестная команда.")
    except Exception as e:
        print(f"Error in handle_inline_router for chat_id: {chat_id}, callback: {call.data}, error: {str(e)}")
        try:
            bot.send_message(chat_id, "⚠️ Ошибка при обработке команды. Попробуйте снова.")
        except Exception as send_error:
            print(f"Failed to send error message to chat_id: {chat_id}, error: {str(send_error)}")

# ──────────────────── MAP ──────────────────────
@register_callback("map")
def handle_map(msg: types.Message) -> None:
    chat_id = msg.chat.id
    try:
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("Карта в PDF", url="https://disk.yandex.ru/i/Z5n3QI9aH8WNWA"),
            types.InlineKeyboardButton("⬅️ Главное меню", callback_data="main")
        )
        send_img_scaled(chat_id, MAP_PATH, caption=MAP_TEXT, kb=kb)
        print(f"Map sent to chat_id: {chat_id}")
    except Exception as e:
        bot.send_message(chat_id, "⚠️ Ошибка при загрузке карты.")
        print(f"Error in handle_map: {e}")

# ──────────────────── FOREST ──────────────────────
@register_callback("forest")
def handle_forest(msg: types.Message) -> None:
    chat_id = msg.chat.id
    try:
        send_img_scaled(chat_id, FOREST_PATH, caption=FOREST_TEXT_1)
        time.sleep(1)
        bot.send_message(chat_id, FOREST_TEXT_2, reply_markup=inline_map_and_menu())
        print(f"Forest sent to chat_id: {chat_id}")
    except Exception as e:
        bot.send_message(chat_id, "⚠️ Ошибка при загрузке раздела леса.")
        print(f"Error in handle_forest: {e}")

# ──────────────────── WORKSHOP ──────────────────────
@register_callback("workshop")
def handle_workshop(msg: types.Message) -> None:
    chat_id = msg.chat.id
    try:
        send_img_scaled(chat_id, MASTER_PATH, caption=WORKSHOP_TEXT, kb=inline_map_and_menu())
        print(f"Workshop sent to chat_id: {chat_id}")
    except Exception as e:
        bot.send_message(chat_id, "⚠️ Ошибка при загрузке мастер-класса.")
        print(f"Error in handle_workshop: {e}")

# ──────────────────── QUEST ──────────────────────
@register_callback("quest")
def handle_quest(msg: types.Message) -> None:
    chat_id = msg.chat.id
    user_id = get_user_id(chat_id)
    if not user_id:
        bot.send_message(chat_id, "⚠ Ошибка: пользователь не найден. Попробуйте /start.")
        print("User not found in handle_quest")
        return
    try:
        progress = get_quest_progress(user_id)
        if not os.path.exists(QUEST_STEPS[0]["sticker_path"]):
            bot.send_message(chat_id, "⚠ Ошибка загрузки квеста.")
            print("Quest image not found")
            return
        send_img_scaled(
            chat_id,
            QUEST_STEPS[0]["sticker_path"],
            caption=f"{QUEST_STEPS[0]['hint']}\n\n<b>{progress}</b>",
            kb=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("🚀 К ПОДСКАЗКАМ", callback_data="hint|1"),
                types.InlineKeyboardButton("⬅ Главное меню", callback_data="main")
            )
        )
        print(f"Quest started for chat_id: {chat_id}")
    except Exception as e:
        bot.send_message(chat_id, "⚠ Ошибка при загрузке квеста.")
        print(f"Error in handle_quest: {e}")

@bot.callback_query_handler(func=lambda c: c.data.startswith("hint|"))
def handle_hint_callback(call: types.CallbackQuery) -> None:
    chat_id = call.message.chat.id
    try:
        step = int(call.data.split("|")[1])
    except (IndexError, ValueError):
        step = 1
    user_id = get_user_id(chat_id)
    if not user_id:
        bot.send_message(chat_id, "⚠ Ошибка: пользователь не найден.")
        bot.answer_callback_query(call.id)
        print("User not found in handle_hint_callback")
        return
    try:
        send_quest_hint(chat_id, user_id, step)
        bot.answer_callback_query(call.id)
        print(f"Hint sent for step: {step}")
    except Exception as e:
        bot.send_message(chat_id, "⚠ Ошибка при отправке подсказки.")
        print(f"Error in handle_hint_callback: {e}")

def send_quest_hint(chat_id: int, user_id: int, step: int) -> None:
    hint, _ = get_quest_hint(step)
    if not hint:
        bot.send_message(chat_id, "⚠ Подсказка не найдена.")
        return

    next_step = step + 1 if step < QUEST_TOTAL_STEPS else 1
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("Ещё подсказка", callback_data=f"hint|{next_step}"))
    kb.add(
        types.InlineKeyboardButton("⬅ Назад", callback_data="quest"),
        types.InlineKeyboardButton("⬅ Главное меню", callback_data="main"),
    )

    # только текстовая подсказка
    bot.send_message(chat_id, hint, reply_markup=kb)

def handle_quest_qr(chat_id: int, user_id: int, step: int) -> None:
    """Обработка QR-кода: фиксируем шаг и шлём сам стикер + навигацию."""
    if step < 1 or step > QUEST_TOTAL_STEPS:
        bot.send_message(chat_id, "⚠ Некорректный шаг квеста.")
        return

    try:
        register_quest_step(user_id, step)
        _, sticker_path = get_quest_hint(step)
        if not sticker_path or not os.path.exists(sticker_path):
            bot.send_message(chat_id, "⚠ Изображение стикера не найдено.")
            return

        # Получаем прогресс
        progress = get_quest_progress(user_id)

        # ── клавиатура ────────────────────────────────────────────────
        next_step = step + 1 if step < QUEST_TOTAL_STEPS else 1
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("🚀 К подсказкам", callback_data=f"hint|{next_step}"))
        kb.add(types.InlineKeyboardButton("⬅ Назад", callback_data="quest"),
               types.InlineKeyboardButton("⬅ Главное меню", callback_data="main"))

        # ── отправляем сам стикер ─────────────────────────────────────
        send_img_scaled(chat_id, sticker_path, caption=f"<b>{progress}</b>", kb=kb)

        # ── финальное поздравление, если собраны все ─────────────────
        if len(user_quest_completed_steps(user_id)) == QUEST_TOTAL_STEPS:
            bot.send_message(
                chat_id,
                "🏆 Поздравляем! Ты нашёл все стикеры!\n"
                "Забегай в лаунж Avito Team, чтобы получить приз! 🥳",
                reply_markup=inline_back_to_menu(),
            )
    except Exception as e:
        bot.send_message(chat_id, "⚠️ Ошибка при обработке шага квеста. Попробуйте снова.")
        print(f"Error in handle_quest_qr: {e}")



# ────────────────────  DANCE  ───────────────────────
@register_callback("dance")
def handle_dance(msg: types.Message) -> None:
    """Стартовый экран раздела танцев."""
    chat_id = msg.chat.id
    try:
        if not os.path.exists(DANCE_PATH):
            bot.send_message(chat_id, "⚠ Ошибка загрузки раздела танцев.")
            return

        send_img_scaled(
            chat_id,
            DANCE_PATH,
            caption=DANCE_INTRO,
            kb=(types.InlineKeyboardMarkup()
                .add(types.InlineKeyboardButton("📆 Записаться", callback_data="dance_slots"))
                .add(types.InlineKeyboardButton("⬅️ Главное меню", callback_data="main")))
        )
    except Exception as e:
        bot.send_message(chat_id, "⚠ Ошибка при загрузке раздела танцев.")
        print(f"[dance] {e}")


@register_callback("dance_slots")
def choose_slot(msg: types.Message) -> None:
    chat_id = msg.chat.id
    try:
        kb = types.InlineKeyboardMarkup(row_width=2)

        with app.app_context():
            for slot in DANCE_SLOTS:                       # ← без sorted()
                total = Registration.query.filter_by(
                    activity_type="dance",
                    day=slot["day"],
                    time_slot=slot["time_slot"]
                ).count()

                text = f"{slot['day']} — {slot['time_slot']}"
                cb   = f"slot|{slot['day']}|{slot['time_slot']}"

                if total < MAX_SLOTS_PER_DANCE:
                    kb.add(types.InlineKeyboardButton(text, callback_data=cb))
                else:
                    kb.add(types.InlineKeyboardButton(f"{text} (заполнен)", callback_data="slot_full"))

        kb.add(
            types.InlineKeyboardButton("⬅️ Назад", callback_data="dance"),
            types.InlineKeyboardButton("⬅️ Главное меню", callback_data="main")
        )

        bot.send_message(chat_id, DANCE_CHOOSE_SLOT, reply_markup=kb)
    except Exception as e:
        bot.send_message(chat_id, "⚠ Ошибка при выборе слота.")
        print(f"[dance_slots] {e}")


# --- ключевой хендлер: ловим все callback_data, начинающиеся на 'slot|' ---
@bot.callback_query_handler(func=lambda c: c.data.startswith("slot|"))
def register_slot(c: types.CallbackQuery) -> None:
    """Регистрируем пользователя на выбранный слот."""
    chat_id = c.message.chat.id

    # 1) Парсим callback_data
    try:
        _, day, time_slot = c.data.split("|", 2)
    except ValueError:
        bot.answer_callback_query(c.id, "⚠️ Неверный формат слота")
        return

    user_id = get_user_id(chat_id)
    if not user_id:
        bot.answer_callback_query(c.id, "⚠️ Пользователь не найден")
        return

    # 2) Работа с базой
    with app.app_context():
        # Дубликат?
        if Registration.query.filter_by(
            user_id=user_id, activity_type="dance",
            day=day, time_slot=time_slot
        ).first():
            bot.answer_callback_query(c.id, "Ты уже на этом слоте 😉")
            return

        # Переполнен?
        total = Registration.query.filter_by(
            activity_type="dance",
            day=day, time_slot=time_slot
        ).count()
        if total >= MAX_SLOTS_PER_DANCE:
            bot.answer_callback_query(c.id, "Увы, слот уже заполнен 😕")
            bot.send_message(chat_id, DANCE_FULL_MESSAGE,
                             reply_markup=choice_slot_keyboard())
            return

        # Записываем
        db.session.add(Registration(
            user_id=user_id,
            activity_type="dance",
            day=day,
            time_slot=time_slot
        ))
        db.session.commit()

    # 3) Ответ пользователю
    bot.answer_callback_query(c.id, "✅ Записал!")

    if os.path.exists(MAP_SENT_PATH):
        send_img_scaled(
            chat_id,
            MAP_SENT_PATH,
            caption=DANCE_CONFIRMATION.format(slot=f"{day} — {time_slot}"),
            kb=inline_map_and_menu()
        )
    else:
        bot.send_message(chat_id, DANCE_CONFIRMATION.format(slot=f"{day} — {time_slot}"),
                         reply_markup=inline_map_and_menu())


@bot.callback_query_handler(func=lambda c: c.data == "slot_full")
def handle_slot_full(c: types.CallbackQuery) -> None:
    """Если пользователь нажал на «полный» слот."""
    chat_id = c.message.chat.id
    bot.answer_callback_query(c.id)
    bot.send_message(chat_id, DANCE_FULL_MESSAGE, reply_markup=choice_slot_keyboard())


def choice_slot_keyboard() -> types.InlineKeyboardMarkup:
    """Перепроводим юзера к свободным слотам (повторно строим клавиатуру)."""
    kb = types.InlineKeyboardMarkup(row_width=2)
    with app.app_context():
        for slot in sorted(DANCE_SLOTS, key=lambda x: (x["day"], x["time_slot"])):
            total = Registration.query.filter_by(
                activity_type="dance",
                day=slot["day"],
                time_slot=slot["time_slot"]
            ).count()

            text = f"{slot['day']} — {slot['time_slot']}"
            cb   = f"slot|{slot['day']}|{slot['time_slot']}"

            if total < MAX_SLOTS_PER_DANCE:
                kb.add(types.InlineKeyboardButton(text, callback_data=cb))
            else:
                kb.add(types.InlineKeyboardButton(f"{text} (заполнен)", callback_data="slot_full"))

    kb.add(
        types.InlineKeyboardButton("⬅️ Назад", callback_data="dance"),
        types.InlineKeyboardButton("⬅️ Главное меню", callback_data="main")
    )
    return kb


# ──────────────────── STICKERPACK ──────────────────────
@register_callback("sticker")
def handle_sticker(msg: types.Message) -> None:
    chat_id = msg.chat.id
    user_id = get_user_id(chat_id)
    
    with app.app_context():
        sticker_gen = StickerGeneration.query.filter_by(user_id=user_id).first()
        
        if sticker_gen:
            if sticker_gen.pack_url:
                # Пользователь уже сгенерировал стикерпак
                bot.send_message(
                    chat_id,
                    f"Твой сгенерированный стикерпак: {sticker_gen.pack_url}\n",
                    reply_markup=inline_back_to_menu()
                )
            elif sticker_gen.status == 'pending':
                # Стикерпак в процессе генерации
                bot.send_message(
                    chat_id,
                    "Твой стикерпак уже в процессе генерации).",
                    reply_markup=inline_back_to_menu()
                )
            else:
                # Что-то пошло не так при предыдущей попытке
                bot.send_message(
                    chat_id,
                    "Произошла ошибка при предыдущей попытке создания стикерпака. Попробуйте еще раз.",
                    reply_markup=inline_map_and_menu()
                )
                # Удаляем неудачную попытку
                db.session.delete(sticker_gen)
                db.session.commit()
        else:
            # Пользователь еще не генерировал стикерпак
            bot.send_message(
                chat_id,
                "Давай создадим что-то особенное вместе! Например, сгенерим твой собственный стикер с Avito Team. Просто поделись с нами своей фотографией.\n\n"
                "📸 <b>Важно:</b> загрузи фото с селфи на нейтральном фоне.\n\n"
                "После загрузки фото в течение пары минут ИИ подготовит твой уникальный стикер — и ты станешь частью эксклюзивного стикерпака от Авито!\n"
                "<i>Твоё фото нигде не хранится, после обработки мы сразу его удаляем.</i>",
                reply_markup=inline_map_and_menu()
            )

@bot.message_handler(content_types=["photo", "document"])
def process_photo(msg: types.Message) -> None:
    chat_id = msg.chat.id
    user_id = get_user_id(chat_id)

    with app.app_context():
        sticker_gen = StickerGeneration.query.filter_by(user_id=user_id).first()
        if sticker_gen:
            if sticker_gen.pack_url:
                bot.send_message(
                    chat_id,
                    f"Твой сгенерированный стикерпак: {sticker_gen.pack_url}\n",
                    reply_markup=inline_back_to_menu()
                )
            elif sticker_gen.status == 'pending':
                bot.send_message(
                    chat_id,
                    "Твой стикерпак уже в процессе генерации)",
                    reply_markup=inline_back_to_menu()
                )
            else:
                # Удаляем неудачную попытку и продолжаем с новой
                db.session.delete(sticker_gen)
                db.session.commit()
        
        if not sticker_gen or (sticker_gen and not sticker_gen.pack_url and sticker_gen.status != 'pending'):
            # Получаем file_id изображения
            file_id = (msg.photo[-1].file_id if msg.content_type == "photo"
                       else msg.document.file_id if msg.document and msg.document.mime_type.startswith("image/")
                       else None)
            if not file_id:
                bot.send_message(chat_id, "❌ Это не изображение.", reply_markup=inline_map_and_menu())
                return

            # Получаем URL файла
            try:
                file_info = bot.get_file(file_id)
                file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
            except Exception as e:
                bot.send_message(chat_id, f"⚠ Не удалось получить файл: {str(e)}.", reply_markup=inline_map_and_menu())
                return

            # Создаем новую запись о генерации стикера
            new_sticker_gen = StickerGeneration(user_id=user_id, status='pending')
            db.session.add(new_sticker_gen)
            db.session.commit()

            bot.send_message(chat_id, "🔄 Идет генерация стикера. Это может занять какое-то время")

            # Добавляем задачу в очередь
            sticker_queue.put((chat_id, user_id, file_url))

def sticker_worker():
    while True:
        try:
            chat_id, user_id, file_url = sticker_queue.get()
            
            # Генерируем стикер
            try:
                buf, doc_file_id, pack_url, _ = generate_sticker_from_user_photo(file_url, chat_id, bot)
                if not buf or not doc_file_id or not pack_url:
                    raise ValueError("Не удалось создать стикер или получить ссылку на стикерпак.")
                
                with app.app_context():
                    sticker_gen = StickerGeneration.query.filter_by(user_id=user_id, status='pending').first()
                    if sticker_gen:
                        sticker_gen.pack_url = pack_url
                        sticker_gen.generated_file_id = doc_file_id
                        sticker_gen.status = 'completed'
                        db.session.commit()
                    else:
                        print(f"Error: No pending sticker generation found for user {user_id}")
                
                # Отправляем ссылку на стикерпак
                bot.send_message(
                    chat_id,
                    f"Твой стикерпак готов!:  {pack_url}",
                    reply_markup=inline_back_to_menu()
                )
            except Exception as e:
                print(f"Error generating sticker: {e}")
                with app.app_context():
                    sticker_gen = StickerGeneration.query.filter_by(user_id=user_id, status='pending').first()
                    if sticker_gen:
                        db.session.delete(sticker_gen)
                        db.session.commit()
                bot.send_message(
                    chat_id,
                    f"⚠ Не удалось создать стикер. Попробуйте другое фото.",
                    reply_markup=inline_map_and_menu()
                )
        except Exception as e:
            print(f"Error in sticker_worker: {e}")
        finally:
            sticker_queue.task_done()

# Запускаем воркеры для обработки очереди стикеров
for _ in range(3):  # Например, 3 воркера
    threading.Thread(target=sticker_worker, daemon=True).start()

# ──────────────────── CAREER ──────────────────────
@register_callback("career")
def handle_career(msg: types.Message) -> None:
    chat_id = msg.chat.id
    try:
        bot.send_message(chat_id, CAREER_MESSAGE, reply_markup=inline_map_and_menu())
        print(f"Career message sent to chat_id: {chat_id}")
    except Exception as e:
        bot.send_message(chat_id, "⚠ Ошибка при отправке сообщения о карьере.")
        print(f"Error in handle_career: {e}")

# ──────────────────── SCHEDULE ──────────────────────
@register_callback("schedule")
def handle_schedule(msg: types.Message) -> None:
    chat_id = msg.chat.id
    try:
        bot.send_message(chat_id, SCHEDULE_MESSAGE, reply_markup=inline_map_and_menu())
        print(f"Schedule sent to chat_id: {chat_id}")
    except Exception as e:
        bot.send_message(chat_id, "⚠ Ошибка при отправке расписания.")
        print(f"Error in handle_schedule: {e}")

# ──────────────────── WEBHOOK ROUTES ──────────────────────
@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    try:
        if request.headers.get("content-type") == "application/json":
            json_str = request.get_data(as_text=True)
            update = telebot.types.Update.de_json(json.loads(json_str))
            bot.process_new_updates([update])
            print("Webhook update processed")
        return "ok", 200
    except Exception as e:
        print(f"Error in webhook: {e}")
        return "error", 500

# ──────────────────── START BOT ────────────────────────
def start_bot() -> None:
    bot.remove_webhook()
    bot.set_webhook(
        url="https://egor-fomin.fvds.ru/webhook",
        allowed_updates=["message", "callback_query"],
    )
    print("Bot webhook set")
    app.run(host="0.0.0.0", port=5000)

if __name__ == "__main__":
    start_bot()