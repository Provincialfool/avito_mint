import logging
import threading

from app import app
from bot import start_bot

# ───── НАСТРОЙКА ЛОГОВ ─────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)

def bot_with_context() -> None:
    with app.app_context():
        try:
            logging.info("🔄 Инициализация Telegram-бота (в фоне)...")
            start_bot()
            logging.info("✅ Telegram-бот успешно запущен")
        except Exception as e:
            logging.exception("❌ Ошибка при запуске Telegram-бота")

if __name__ == "__main__":
    try:
        logging.info("🚀 Flask-приложение запускается")
        threading.Thread(target=bot_with_context, daemon=True).start()
        # Flask запускается Gunicorn, так что просто держим контекст
    except Exception as e:
        logging.exception("❌ Ошибка запуска main.py")
