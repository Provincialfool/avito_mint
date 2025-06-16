
import os
import logging
from app import app
from bot import start_bot

# ───── НАСТРОЙКА ЛОГОВ ─────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

if __name__ == "__main__":
    with app.app_context():
        logging.info("🤖 Запуск Telegram бота...")
        start_bot()
