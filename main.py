import os
import logging
import threading
from flask import Flask
from app import app
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

def run_flask():
    """Запуск Flask админки"""
    logging.info("🌐 Запуск Flask админки на порту 5000...")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

def run_bot_polling():
    """Запуск Telegram бота через polling или webhook"""
    try:
        # Проверяем режим работы из базы данных
        from models import SystemConfig
        
        with app.app_context():
            # Определяем режим работы на основе окружения
            deployment_detected = False
            webhook_domain = None
            
            # 1. Replit Deployment
            if os.environ.get('REPL_DEPLOYMENT') or os.environ.get('REPL_URL'):
                deployment_detected = True
                webhook_domain = os.environ.get('REPL_URL', f"https://{os.environ.get('REPL_SLUG', 'workspace')}-{os.environ.get('REPL_OWNER', 'user')}.replit.app")
                logging.info(f"🚀 Replit Deployment detected: {webhook_domain}")
            
            # 2. Внешний VPS/сервер
            elif os.environ.get('WEBHOOK_URL'):
                deployment_detected = True
                webhook_domain = os.environ.get('WEBHOOK_URL')
                logging.info(f"🌐 External server detected: {webhook_domain}")
            
            # 3. Явное указание режима webhook в конфигурации
            elif SystemConfig.get_config('BOT_MODE') == 'webhook':
                deployment_detected = True
                webhook_domain = SystemConfig.get_config('WEBHOOK_DOMAIN')
                logging.info(f"⚙️ Webhook mode configured manually: {webhook_domain}")
            
            if deployment_detected:
                bot_mode = 'webhook'
                SystemConfig.set_config('BOT_MODE', 'webhook', 'text', 'Режим работы бота')
                
                # Автоматически устанавливаем домен для webhook
                current_domain = SystemConfig.get_config('WEBHOOK_DOMAIN')
                
                if webhook_domain and (not current_domain or current_domain != webhook_domain):
                    SystemConfig.set_config('WEBHOOK_DOMAIN', webhook_domain, 'text', 'Домен для webhook')
                    logging.info(f"🔄 Webhook domain updated: {webhook_domain}")
                
                # Сохраняем информацию о deployment
                from datetime import datetime
                SystemConfig.set_config('DEPLOYMENT_START_TIME', datetime.now().isoformat(), 'text', 'Время запуска deployment')
                SystemConfig.set_config('DEPLOYMENT_URL', webhook_domain, 'text', 'URL deployment')
                
                logging.info(f"✅ Auto-switched to webhook mode with domain: {webhook_domain}")
            else:
                bot_mode = SystemConfig.get_config('BOT_MODE', 'polling')
                logging.info("🔄 Using polling mode (development)")
            # Обновляем токен из базы данных в переменные окружения
            db_token = SystemConfig.get_config('BOT_TOKEN')
            if db_token:
                os.environ['BOT_TOKEN'] = db_token
                logging.info("✅ Токен бота обновлен из базы данных")
            
        if bot_mode == 'webhook':
            logging.info("🌐 Запуск Telegram бота через webhook...")
            
            # В deployment режиме настраиваем webhook автоматически
            if os.environ.get('REPL_DEPLOYMENT'):
                from webhook_bot import setup_webhook
                webhook_setup = setup_webhook()
                if not webhook_setup:
                    logging.error("❌ Не удалось настроить webhook в deployment")
            
            from webhook_bot import start_webhook_server
            webhook_started = start_webhook_server()
            if webhook_started:
                logging.info("✅ Webhook режим активен, Flask сервер обрабатывает запросы")
                # В webhook режиме Flask уже запущен в основном потоке, просто ждем
                import time
                while True:
                    time.sleep(60)  # Просто держим поток alive
            else:
                logging.error("❌ Не удалось запустить webhook, переключаемся на polling")
                # Принудительно удаляем webhook перед переключением на polling
                try:
                    from bot import bot
                    bot.remove_webhook()
                    logging.info("✅ Webhook удален перед переключением на polling")
                except Exception as e:
                    logging.error(f"Ошибка при удалении webhook: {e}")
                
                from bot import start_bot_polling_only
                start_bot_polling_only()
        else:
            logging.info("🤖 Запуск Telegram бота через polling...")
            # Принудительно удаляем webhook при запуске в polling режиме
            try:
                from bot import bot
                bot.remove_webhook()
                logging.info("✅ Webhook удален для запуска в polling режиме")
            except Exception as e:
                logging.error(f"Ошибка при удалении webhook: {e}")
            
            from bot import start_bot_polling_only
            start_bot_polling_only()
    except KeyboardInterrupt:
        logging.info("👋 Бот остановлен пользователем")
    except Exception as e:
        logging.exception("❌ Ошибка при запуске Telegram бота")

if __name__ == "__main__":
    # Инициализация базы данных
    with app.app_context():
        try:
            db.create_all()
            logging.info(f"✅ База данных инициализирована: {app.config['SQLALCHEMY_DATABASE_URI']}")
        except Exception as e:
            logging.exception("❌ Ошибка инициализации базы данных")
            exit(1)

    try:
        # Проверяем режим работы
        from models import SystemConfig
        with app.app_context():
            bot_mode = SystemConfig.get_config('BOT_MODE', 'polling')

        if bot_mode == 'webhook':
            # В webhook режиме Flask запускается в главном потоке
            logging.info("🌐 Запуск в webhook режиме...")
            
            # Инициализируем webhook в отдельном потоке
            def init_webhook():
                import time
                time.sleep(2)  # Даем Flask время запуститься
                from webhook_bot import start_webhook_server
                start_webhook_server()
            
            webhook_thread = threading.Thread(target=init_webhook, daemon=True)
            webhook_thread.start()
            
            # Flask запускается в главном потоке
            logging.info("🌐 Запуск Flask сервера для webhook...")
            app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
        else:
            # В polling режиме Flask в отдельном потоке, бот в главном
            logging.info("🤖 Запуск в polling режиме...")
            
            flask_thread = threading.Thread(target=run_flask, daemon=True)
            flask_thread.start()
            logging.info("✅ Flask админка запущена в отдельном потоке")

            # Даем Flask время запуститься
            import time
            time.sleep(2)

            # Запускаем бота в основном потоке
            run_bot_polling()

    except KeyboardInterrupt:
        logging.info("👋 Приложение остановлено пользователем")
    except Exception as e:
        logging.exception("💥 Критическая ошибка приложения")