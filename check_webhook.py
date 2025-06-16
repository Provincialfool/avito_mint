
import requests
import os
from models import SystemConfig
from app import app

def check_webhook_status():
    """Проверка статуса webhook через Telegram API"""
    
    with app.app_context():
        # Получаем токен из базы данных или переменных окружения
        bot_token = SystemConfig.get_config('BOT_TOKEN') or os.getenv('BOT_TOKEN')
        
        if not bot_token:
            print("❌ BOT_TOKEN не найден")
            return
        
        # Проверяем статус webhook
        url = f"https://api.telegram.org/bot{bot_token}/getWebhookInfo"
        
        try:
            response = requests.get(url)
            data = response.json()
            
            if data.get('ok'):
                webhook_info = data.get('result', {})
                
                print("📊 Статус Webhook:")
                print(f"URL: {webhook_info.get('url', 'Не установлен')}")
                print(f"Ожидающих обновлений: {webhook_info.get('pending_update_count', 0)}")
                print(f"Последняя ошибка: {webhook_info.get('last_error_message', 'Нет ошибок')}")
                print(f"Время последней ошибки: {webhook_info.get('last_error_date', 'Нет')}")
                
                if webhook_info.get('url'):
                    print("✅ Webhook установлен")
                else:
                    print("❌ Webhook не установлен")
                    
            else:
                print(f"❌ Ошибка API: {data.get('description')}")
                
        except Exception as e:
            print(f"❌ Ошибка при проверке: {e}")

if __name__ == "__main__":
    check_webhook_status()
