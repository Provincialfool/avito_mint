import threading
import time
import json
from typing import Dict, Any, Optional

class TextCache:
    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._last_update = 0
        self._cache_ttl = 300  # 5 минут
        self._lock = threading.RLock()
        self._initialized = False

    def get_text(self, key: str, default: Any = None) -> Any:
        """Получить текст из кеша"""
        with self._lock:
            if not self._initialized or self._should_update():
                self._update_cache()
            return self._cache.get(key, default)

    def _should_update(self) -> bool:
        """Проверить, нужно ли обновить кеш"""
        return time.time() - self._last_update > self._cache_ttl

    def _update_cache(self):
        """Обновить кеш из базы данных"""
        try:
            from app import app
            from models import SystemConfig

            with app.app_context():
                # Получаем все настройки из базы
                configs = SystemConfig.query.all()

                # Обновляем кеш
                for config in configs:
                    self._cache[config.config_key] = config.get_value()

                # Добавляем fallback значения из text.py
                self._add_fallback_values()

                self._last_update = time.time()
                self._initialized = True

        except Exception as e:
            print(f"Error updating text cache: {e}")
            # Если ошибка, используем fallback из text.py
            if not self._initialized:
                self._add_fallback_values()
                self._initialized = True

    def _add_fallback_values(self):
        """Добавить fallback значения из text.py"""
        try:
            import text

            # Список всех текстовых констант из text.py
            text_constants = [
                'CONSENT_TEXT', 'MAIN_MENU_TEXT', 'MAIN_MENU_TEXT_NO_THANKS',
                'DANCE_INTRO', 'DANCE_CHOOSE_SLOT', 'DANCE_CONFIRMATION', 
                'DANCE_FULL_MESSAGE', 'DANCE_ALL_FULL', 'WORKSHOP_TEXT',
                'FOREST_TEXT_1', 'FOREST_TEXT_2', 'STICKER_START_MESSAGE',
                'CAREER_MESSAGE', 'SCHEDULE_MESSAGE', 'MAP_TEXT',
                'SURVEY_QUESTIONS'
            ]

            for const_name in text_constants:
                if const_name not in self._cache:
                    value = getattr(text, const_name, None)
                    if value is not None:
                        self._cache[const_name] = value

            # Добавляем шаги квеста
            if hasattr(text, 'QUEST_STEPS'):
                for i, step in enumerate(text.QUEST_STEPS):
                    key = f'QUEST_STEP_{i}_HINT'
                    if key not in self._cache:
                        self._cache[key] = step.get('hint', '')

            # Добавляем слоты танцев
            if hasattr(text, 'DANCE_SLOTS'):
                self._cache['DANCE_SLOTS'] = text.DANCE_SLOTS

        except Exception as e:
            print(f"Error adding fallback values: {e}")

    def force_update(self):
        """Принудительное обновление всех кешей"""
        with self._lock:
            self._last_update = 0
            self._update_cache()

    def get_survey_question(self, index: int) -> str:
        """Получить вопрос опроса по индексу"""
        questions = self.get_text('SURVEY_QUESTIONS', [])
        if isinstance(questions, list) and index < len(questions):
            return questions[index]

        # Fallback на отдельные вопросы
        question_key = f'SURVEY_QUESTION_{index}'
        return self.get_text(question_key, '')

    def get_quest_step(self, step: int) -> dict:
        """Получить шаг квеста"""
        hint_key = f'QUEST_STEP_{step}_HINT'
        image_key = f'QUEST_STEP_{step}_IMAGE'
        
        hint = self.get_text(hint_key)
        image = self.get_text(image_key)
        
        if hint:
            return {"hint": hint, "image": image}
        
        # Fallback на quest.py
        try:
            import quest
            if hasattr(quest, 'QUEST_STEPS') and step < len(quest.QUEST_STEPS):
                quest_step = quest.QUEST_STEPS[step]
                return {
                    "hint": quest_step.get('hint', ''),
                    "image": quest_step.get('sticker_path', '')
                }
        except:
            pass

        return {"hint": None, "image": None}

    def get_quest_total_steps(self) -> int:
        """Получить общее количество шагов квеста"""
        total = self.get_text('QUEST_TOTAL_STEPS')
        if total:
            return int(total)
        
        # Fallback на quest.py
        try:
            import quest
            if hasattr(quest, 'QUEST_TOTAL_STEPS'):
                return quest.QUEST_TOTAL_STEPS
            elif hasattr(quest, 'QUEST_STEPS'):
                return len(quest.QUEST_STEPS) - 1  # -1 для стартового сообщения
        except:
            pass
        
        return 9  # По умолчанию

    def get_dance_slots(self) -> list:
        """Получить слоты танцев из базы данных"""
        cache_key = 'dance_slots_from_db'

        def compute_dance_slots():
            try:
                from app import app
                from models import DanceSlot

                with app.app_context():
                    slots = DanceSlot.query.filter_by(is_active=True).order_by(DanceSlot.day, DanceSlot.time_slot).all()
                    return [
                        {
                            "day": slot.day,
                            "time_slot": slot.time_slot,
                            "activity_type": "dance",
                            "max_participants": slot.max_participants
                        }
                        for slot in slots
                    ]
            except Exception as e:
                print(f"Error loading dance slots from DB: {e}")
                return []

        # Кешируем на 5 минут для слотов танцев
        if cache_key in self._cache:
            value, timestamp = self._cache[cache_key]
            if time.time() - timestamp < 300:  # 5 минут
                return value

        slots = compute_dance_slots()
        self._cache[cache_key] = (slots, time.time())
        return slots

# Глобальный экземпляр кеша
text_cache = TextCache()

def get_cached_text(key: str, default: Any = None) -> Any:
    """Удобная функция для получения текста"""
    return text_cache.get_text(key, default)