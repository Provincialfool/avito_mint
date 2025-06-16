
import os
import logging
from flask import Flask, request, abort
import telebot
from telebot.types import Update

from app import app, db
from bot import bot, BOT_TOKEN

# Настройка логирования
handlers = [logging.StreamHandler()]

# Используем локальную директорию для логов в Replit
log_path = os.path.join(os.path.dirname(__file__), "webhook_bot.log")

try:
    # Проверяем возможность записи
    test_handler = logging.FileHandler(log_path)
    test_handler.close()
    handlers.append(logging.FileHandler(log_path))
except (PermissionError, OSError):
    # Если нет прав - логируем только в консоль (systemd journal)
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=handlers
)

# URL для webhook - приоритет имеет настройка из базы данных
def get_webhook_url():
    """Получает URL для webhook из конфигурации"""
    try:
        from models import SystemConfig
        from app import app
        
        with app.app_context():
            # Сначала проверяем настройку из базы данных
            webhook_domain = SystemConfig.get_config('WEBHOOK_DOMAIN')
            if webhook_domain:
                logging.info(f"Using webhook domain from DB: {webhook_domain}")
                return webhook_domain.rstrip('/')
            
            # Для Deployment используем REPL_URL (приоритет)
            repl_url = os.environ.get('REPL_URL', '')
            if repl_url:
                logging.info(f"Using REPL_URL for webhook: {repl_url}")
                return repl_url.rstrip('/')
            
            # Для VPS используем переменную WEBHOOK_URL
            webhook_url = os.environ.get('WEBHOOK_URL', '')
            if webhook_url:
                logging.info(f"Using WEBHOOK_URL for external VPS: {webhook_url}")
                return webhook_url.rstrip('/')
            
            # Fallback на стандартный Replit URL (для development)
            repl_slug = os.environ.get('REPL_SLUG', 'workspace')
            repl_owner = os.environ.get('REPL_OWNER', 'user')
            fallback_url = f"https://{repl_slug}-{repl_owner}.replit.app"
            logging.info(f"Using fallback URL: {fallback_url}")
            return fallback_url
    except Exception as e:
        logging.error(f"Error getting webhook URL: {e}")
        # В случае ошибки возвращаем deployment URL или fallback
        fallback = os.environ.get('REPL_URL') or os.environ.get('WEBHOOK_URL') or f"https://{os.environ.get('REPL_SLUG', 'workspace')}-{os.environ.get('REPL_OWNER', 'user')}.replit.app"
        logging.error(f"Using emergency fallback: {fallback}")
        return fallback

WEBHOOK_URL_PATH = f"/{BOT_TOKEN}/"

def setup_webhook():
    """Настройка webhook для Telegram бота"""
    try:
        webhook_url = get_webhook_url() + WEBHOOK_URL_PATH
        logging.info(f"🔗 Setting webhook URL: {webhook_url}")
        
        from bot import bot
        bot.remove_webhook()
        import time
        time.sleep(1)  # Небольшая задержка
        
        success = bot.set_webhook(
            url=webhook_url,
            max_connections=10,
            allowed_updates=["message", "callback_query"]
        )
        
        if success:
            logging.info(f"✅ Webhook установлен: {webhook_url}")
            return True
        else:
            logging.error("❌ Не удалось установить webhook")
            return False
            
    except Exception as e:
        logging.error(f"❌ Ошибка установки webhook: {e}")
        return False



@app.route('/webhook', methods=['POST'])
def webhook_telegram():
    """Основной endpoint для Telegram webhook"""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'OK'
    else:
        abort(403)

@app.route('/webhook-status', methods=['GET'])
def webhook_status():
    """Проверка статуса webhook"""
    try:
        from models import SystemConfig
        
        with app.app_context():
            bot_mode = SystemConfig.get_config('BOT_MODE', 'polling')
            webhook_domain = SystemConfig.get_config('WEBHOOK_DOMAIN', '')
            
        webhook_url = get_webhook_url() + WEBHOOK_URL_PATH
        
        # Проверяем статус webhook через API
        webhook_info = bot.get_webhook_info()
        
        status = {
            'bot_mode': bot_mode,
            'webhook_domain': webhook_domain,
            'webhook_url': webhook_url,
            'telegram_webhook_url': webhook_info.url,
            'pending_updates': webhook_info.pending_update_count,
            'last_error': webhook_info.last_error_message,
            'is_webhook_set': bool(webhook_info.url),
            'webhook_matches': webhook_info.url == webhook_url
        }
        
        return status, 200
        
    except Exception as e:
        return {'error': str(e)}, 500

@app.route(WEBHOOK_URL_PATH, methods=['POST'])
def webhook_handler():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    else:
        abort(403)

def set_webhook():
    """Устанавливает webhook для бота"""
    try:
        # Проверяем настройки из базы данных
        from models import SystemConfig
        from app import app
        
        with app.app_context():
            bot_mode = SystemConfig.get_config('BOT_MODE', 'polling')
            webhook_domain = SystemConfig.get_config('WEBHOOK_DOMAIN')
            
            if bot_mode != 'webhook':
                logging.info("Режим работы не webhook, пропускаем установку")
                return False
                
            if not webhook_domain:
                logging.error("WEBHOOK_DOMAIN не настроен в конфигурации")
                logging.info("Перейдите в админ панель и настройте домен для webhook")
                return False
        
        # Получаем актуальный URL
        webhook_base = get_webhook_url()
        webhook_url = webhook_base + WEBHOOK_URL_PATH
        
        logging.info(f"Устанавливаем webhook на: {webhook_url}")
        
        # Удаляем существующий webhook
        bot.remove_webhook()
        
        # Устанавливаем новый webhook
        success = bot.set_webhook(url=webhook_url)
        
        if success:
            logging.info("✅ Webhook установлен успешно")
            # Проверяем статус webhook
            webhook_info = bot.get_webhook_info()
            logging.info(f"Webhook info: URL={webhook_info.url}, pending_updates={webhook_info.pending_update_count}")
            return True
        else:
            logging.error("❌ Не удалось установить webhook")
            return False
            
    except Exception as e:
        logging.error(f"Ошибка при установке webhook: {e}")
        return False

def start_webhook_server():
    """Запускает Flask сервер для webhook"""
    try:
        logging.info("🌐 Запуск webhook сервера...")
        
        # Проверяем режим из базы данных
        from models import SystemConfig
        with app.app_context():
            bot_mode = SystemConfig.get_config('BOT_MODE', 'polling')
            webhook_domain = SystemConfig.get_config('WEBHOOK_DOMAIN', '')
            
        if bot_mode == 'webhook' and webhook_domain:
            # Пытаемся установить webhook
            if set_webhook():
                logging.info("🚀 Webhook установлен, сервер готов к работе")
                return True
            else:
                logging.error("❌ Не удалось настроить webhook")
                return False
        else:
            logging.info("Webhook режим не настроен, используем polling")
            return False
            
    except Exception as e:
        logging.error(f"Ошибка webhook сервера: {e}")
        return False

if __name__ == "__main__":
    with app.app_context():
        start_webhook_server()
