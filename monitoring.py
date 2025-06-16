
import time
import psutil
import logging
from datetime import datetime, timedelta
from models import User, Registration, QuestProgress, StickerGeneration, SystemConfig
from app import db

class BotMonitoring:
    @staticmethod
    def get_system_stats():
        """Получить системные метрики"""
        return {
            'cpu_percent': psutil.cpu_percent(),
            'memory_percent': psutil.virtual_memory().percent,
            'disk_usage': psutil.disk_usage('/').percent,
            'uptime': time.time() - psutil.boot_time()
        }
    
    @staticmethod
    def get_bot_stats():
        """Получить статистику бота"""
        try:
            now = datetime.utcnow()
            day_ago = now - timedelta(days=1)
            hour_ago = now - timedelta(hours=1)
            
            return {
                'total_users': User.query.count(),
                'new_users_today': User.query.filter(User.created_at >= day_ago).count(),
                'new_users_hour': User.query.filter(User.created_at >= hour_ago).count(),
                'active_registrations': Registration.query.filter(Registration.created_at >= day_ago).count(),
                'quest_completions_today': QuestProgress.query.filter(
                    QuestProgress.completed == True, 
                    QuestProgress.completed_at >= day_ago
                ).count(),
                'stickers_generated_today': StickerGeneration.query.filter(
                    StickerGeneration.created_at >= day_ago,
                    StickerGeneration.status == 'ok'
                ).count(),
                'error_rate': SystemConfig.get_config('ERROR_COUNT_24H', 0)
            }
        except Exception as e:
            logging.error(f"Error getting bot stats: {e}")
            return {
                'total_users': 0,
                'new_users_today': 0,
                'new_users_hour': 0,
                'active_registrations': 0,
                'quest_completions_today': 0,
                'stickers_generated_today': 0,
                'error_rate': 0,
                'error': str(e)
            }
    
    @staticmethod
    def health_check():
        """Проверка здоровья системы"""
        checks = {
            'database': False,
            'bot_token': False,
            'replicate_api': False,
            'disk_space': False,
            'memory': False
        }
        
        try:
            # Проверка БД
            from sqlalchemy import text
            db.session.execute(text('SELECT 1'))
            db.session.commit()
            checks['database'] = True
        except Exception as e:
            logging.error(f"Database health check failed: {e}")
            checks['database'] = False
        
        # Проверка токенов
        checks['bot_token'] = bool(SystemConfig.get_config('BOT_TOKEN'))
        checks['replicate_api'] = bool(SystemConfig.get_config('REPLICATE_API_TOKEN'))
        
        # Проверка ресурсов
        checks['disk_space'] = psutil.disk_usage('/').percent < 90
        checks['memory'] = psutil.virtual_memory().percent < 85
        
        return checks, all(checks.values())
