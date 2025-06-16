from app import app, db
from models import SystemConfig, DanceSlot
from text import DANCE_SLOTS

def init_system_config():
    """Инициализация системной конфигурации"""
    configs = [
        ('MAX_SLOTS_PER_DANCE', '10', 'int', 'Максимальное количество участников на слот танцев'),
        ('STICKER_GENERATION_ENABLED', 'true', 'bool', 'Включена ли генерация стикеров'),
        ('SURVEY_ENABLED', 'true', 'bool', 'Включен ли опрос'),
        ('BOT_MODE', 'polling', 'text', 'Режим работы бота: polling или webhook'),
        ('WEBHOOK_DOMAIN', '', 'text', 'Домен для webhook режима'),
        ('QUEST_TOTAL_STEPS', '9', 'int', 'Общее количество шагов квеста'),
    ]

    for key, value, config_type, description in configs:
        if not SystemConfig.query.filter_by(config_key=key).first():
            SystemConfig.set_config(key, value, config_type, description)

def init_dance_slots():
    """Инициализация слотов танцев из text.py"""
    from text import DANCE_SLOTS

    for slot_data in DANCE_SLOTS:
        existing = DanceSlot.query.filter_by(
            day=slot_data['day'],
            time_slot=slot_data['time_slot']
        ).first()

        if not existing:
            slot = DanceSlot(
                day=slot_data['day'],
                time_slot=slot_data['time_slot'],
                max_participants=10,
                is_active=True
            )
            db.session.add(slot)

    db.session.commit()

def init_text_messages():
    """Инициализация текстовых сообщений"""
    try:
        import text

        # Основные тексты
        text_configs = [
            ('CONSENT_TEXT', getattr(text, 'CONSENT_TEXT', ''), 'Согласие на обработку данных'),
            ('MAIN_MENU_TEXT', getattr(text, 'MAIN_MENU_TEXT', ''), 'Главное меню (первый раз)'),
            ('MAIN_MENU_TEXT_NO_THANKS', getattr(text, 'MAIN_MENU_TEXT_NO_THANKS', ''), 'Главное меню (обычное)'),
            ('DANCE_INTRO', getattr(text, 'DANCE_INTRO', ''), 'Вступление к танцам'),
            ('WORKSHOP_TEXT', getattr(text, 'WORKSHOP_TEXT', ''), 'Мастер-класс'),
            ('CAREER_MESSAGE', getattr(text, 'CAREER_MESSAGE', ''), 'Карьера в Avito'),
            ('STICKER_START_MESSAGE', getattr(text, 'STICKER_START_MESSAGE', ''), 'Начало генерации стикера'),
            ('SCHEDULE_MESSAGE', getattr(text, 'SCHEDULE_MESSAGE', ''), 'Расписание мероприятий'),
            ('MAP_TEXT', getattr(text, 'MAP_TEXT', ''), 'Описание карты'),
            ('FOREST_TEXT_1', getattr(text, 'FOREST_TEXT_1', ''), 'Лес - первая часть'),
            ('FOREST_TEXT_2', getattr(text, 'FOREST_TEXT_2', ''), 'Лес - вторая часть'),
        ]

        for key, value, description in text_configs:
            if value and not SystemConfig.query.filter_by(config_key=key).first():
                SystemConfig.set_config(key, value, 'text', description)

        # Вопросы опроса
        survey_questions = getattr(text, 'SURVEY_QUESTIONS', [])
        for i, question in enumerate(survey_questions):
            key = f'SURVEY_QUESTION_{i}'
            if not SystemConfig.query.filter_by(config_key=key).first():
                SystemConfig.set_config(key, question, 'text', f'Вопрос опроса {i+1}')

    except ImportError:
        print("Warning: text.py not found, skipping text initialization")

if __name__ == "__main__":
    with app.app_context():
        init_system_config()
        init_dance_slots()
        init_text_messages()
        print("✅ Система инициализирована!")():
            SystemConfig.set_config(key, value, config_type, description)
            print(f"Создана конфигурация: {key} = {value}")

def init_dance_slots():
    """Инициализация слотов танцев"""
    for slot_data in DANCE_SLOTS:
        existing = DanceSlot.query.filter_by(
            day=slot_data['day'], 
            time_slot=slot_data['time_slot']
        ).first()

        if not existing:
            slot = DanceSlot(
                day=slot_data['day'],
                time_slot=slot_data['time_slot'],
                max_participants=10
            )
            db.session.add(slot)
            print(f"Создан слот: {slot_data['day']} {slot_data['time_slot']}")

    db.session.commit()

if __name__ == "__main__":
    with app.app_context():
        print("Инициализация базовых данных...")
        init_system_config()
        init_dance_slots()
        print("Инициализация завершена!")