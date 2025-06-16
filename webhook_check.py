
#!/usr/bin/env python3
import os
import logging
from app import app
from models import SystemConfig
from webhook_bot import get_webhook_url, WEBHOOK_URL_PATH

def check_webhook_configuration():
    """Проверка конфигурации webhook"""
    print("🔍 Проверка конфигурации webhook...")
    print("=" * 50)
    
    with app.app_context():
        # Проверяем переменные окружения
        print("📋 Переменные окружения:")
        print(f"  REPL_URL: {os.environ.get('REPL_URL', 'НЕ УСТАНОВЛЕНА')}")
        print(f"  REPL_DEPLOYMENT: {os.environ.get('REPL_DEPLOYMENT', 'НЕ УСТАНОВЛЕНА')}")
        print(f"  WEBHOOK_URL: {os.environ.get('WEBHOOK_URL', 'НЕ УСТАНОВЛЕНА')}")
        print(f"  REPL_SLUG: {os.environ.get('REPL_SLUG', 'НЕ УСТАНОВЛЕНА')}")
        print(f"  REPL_OWNER: {os.environ.get('REPL_OWNER', 'НЕ УСТАНОВЛЕНА')}")
        
        print("\n⚙️ Настройки из базы данных:")
        bot_mode = SystemConfig.get_config('BOT_MODE', 'polling')
        webhook_domain = SystemConfig.get_config('WEBHOOK_DOMAIN', '')
        bot_token = SystemConfig.get_config('BOT_TOKEN', '')
        
        print(f"  BOT_MODE: {bot_mode}")
        print(f"  WEBHOOK_DOMAIN: {webhook_domain or 'НЕ УСТАНОВЛЕН'}")
        print(f"  BOT_TOKEN: {'УСТАНОВЛЕН' if bot_token else 'НЕ УСТАНОВЛЕН'}")
        
        print("\n🔗 Webhook URL:")
        webhook_url = get_webhook_url()
        full_webhook_url = webhook_url + WEBHOOK_URL_PATH
        print(f"  Base URL: {webhook_url}")
        print(f"  Full webhook URL: {full_webhook_url}")
        
        print("\n📊 Статус:")
        if bot_mode == 'webhook':
            if webhook_domain:
                print("  ✅ Режим webhook активен")
                print("  ✅ Домен настроен")
            else:
                print("  ⚠️ Режим webhook активен, но домен не настроен")
        else:
            print("  ℹ️ Режим polling (разработка)")
        
        print("\n🚀 Рекомендации для deployment:")
        if not os.environ.get('REPL_URL') and not os.environ.get('WEBHOOK_URL'):
            print("  • При deployment на Replit переменная REPL_URL будет установлена автоматически")
            print("  • При развертывании на VPS установите переменную WEBHOOK_URL")
        
        print("  • Убедитесь, что порт 5000 открыт")
        print("  • Flask должен быть запущен на 0.0.0.0:5000")

if __name__ == "__main__":
    check_webhook_configuration()
