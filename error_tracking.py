
import logging
import traceback
from datetime import datetime
from models import SystemConfig, db
from functools import wraps

class ErrorTracker:
    @staticmethod
    def log_error(error, context="", user_id=None, chat_id=None):
        """Логирование ошибки с контекстом"""
        error_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'error': str(error),
            'traceback': traceback.format_exc(),
            'context': context,
            'user_id': user_id,
            'chat_id': chat_id
        }
        
        # Логируем в файл
        logging.error(f"Bot Error: {error_data}")
        
        # Увеличиваем счетчик ошибок
        current_count = SystemConfig.get_config('ERROR_COUNT_24H', 0)
        SystemConfig.set_config('ERROR_COUNT_24H', current_count + 1, 'int')
        
        return error_data
    
    @staticmethod
    def track_user_action(action, user_id, details=None):
        """Отслеживание действий пользователей"""
        logging.info(f"User Action: {action} | User: {user_id} | Details: {details}")

def handle_bot_errors(f):
    """Декоратор для обработки ошибок в функциях бота"""
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            # Определяем chat_id из аргументов
            chat_id = None
            if args and isinstance(args[0], (int, str)):
                chat_id = args[0]
            
            ErrorTracker.log_error(e, f"Function: {f.__name__}", chat_id=chat_id)
            
            # Отправляем сообщение об ошибке пользователю
            if chat_id:
                try:
                    from bot import bot
                    bot.send_message(chat_id, "⚠️ Произошла ошибка. Попробуйте позже.")
                except:
                    pass
            
            raise e
    return decorated
