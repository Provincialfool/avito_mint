
#!/usr/bin/env python3
import sys
from app import app
from models import SystemConfig

def switch_mode(mode, webhook_url=None):
    """Переключение режима бота"""
    with app.app_context():
        if mode == 'webhook':
            SystemConfig.set_config('BOT_MODE', 'webhook', 'text', 'Режим работы бота')
            if webhook_url:
                SystemConfig.set_config('WEBHOOK_DOMAIN', webhook_url, 'text', 'Домен для webhook')
                print(f"✅ Переключено на webhook режим с доменом: {webhook_url}")
            else:
                print("✅ Переключено на webhook режим")
                print("⚠️ Не забудьте установить WEBHOOK_DOMAIN в админ панели")
        elif mode == 'polling':
            SystemConfig.set_config('BOT_MODE', 'polling', 'text', 'Режим работы бота')
            print("✅ Переключено на polling режим")
        else:
            print("❌ Неверный режим. Используйте: webhook или polling")
            return
        
        print("🔄 Перезапустите бота для применения изменений")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование:")
        print("  python switch_mode.py polling")
        print("  python switch_mode.py webhook [URL]")
        print("Примеры:")
        print("  python switch_mode.py webhook https://mybot.example.com")
        print("  python switch_mode.py polling")
        sys.exit(1)
    
    mode = sys.argv[1]
    webhook_url = sys.argv[2] if len(sys.argv) > 2 else None
    switch_mode(mode, webhook_url)
