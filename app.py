import os
import logging
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
import telebot

from extensions import db

# ───── ЛОГГИРОВАНИЕ ─────
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
log_path = os.path.join(BASE_DIR, "app.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler()
    ]
)

# ───── FLASK ─────
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "festival-bot-2025-secret-key-very-long-and-secure")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# ───── SQLite ─────
db_dir = os.path.join(BASE_DIR, "instance")
os.makedirs(db_dir, exist_ok=True)

db_path = os.path.join(db_dir, "festival_bot.db")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False  # Убирает warning

db.init_app(app)

# ───── DASHBOARD ROUTES ─────
from admin_routes import *

# ───── ИНИЦИАЛИЗАЦИЯ БД ─────
with app.app_context():
    import models
    db.create_all()
    logging.info(f"✅ База данных инициализирована: {db_path}")
