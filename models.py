import json

from datetime import datetime
from sqlalchemy import (
    Integer, String, Text, Boolean, DateTime,
    Index, UniqueConstraint
)
from extensions import db

# ─────────────── Отложенные сообщения ───────────────
class ScheduledMessage(db.Model):
    __tablename__ = 'scheduled_messages'

    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.Text, nullable=True)  # Старое поле для совместимости
    message_text = db.Column(db.Text, nullable=True)  # Новое поле
    chat_id = db.Column(db.Integer, nullable=True)  # Для конкретного пользователя
    scheduled_time = db.Column(db.DateTime, nullable=False)
    sent = db.Column(db.Boolean, default=False)
    photo_url = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def text(self):
        """Возвращает текст сообщения из любого доступного поля"""
        return self.message_text or self.message or ""

    def is_pending(self):
        return not self.sent and self.scheduled_time > datetime.utcnow()


# ─────────────── Пользователи ───────────────
class User(db.Model):
    __tablename__ = "users"

    id = db.Column(Integer, primary_key=True)
    telegram_id = db.Column(String(20), unique=True, nullable=False, index=True)
    username = db.Column(String(100))
    first_name = db.Column(String(100))
    last_name = db.Column(String(100))
    full_name = db.Column(String(200))
    city = db.Column(String(100))
    professional_role = db.Column(String(100))
    grade = db.Column(String(50))
    company = db.Column(String(100))

    interested_in_vacancies = db.Column(Boolean, default=False)
    survey_completed = db.Column(Boolean, default=False)
    consent_given = db.Column(Boolean, default=False)
    is_admin = db.Column(Boolean, default=False)

    # 🔽 JSON-хранилище для всех опросов, анкет, свободных ответов
    survey_data = db.Column(Text)

    created_at = db.Column(DateTime, default=datetime.utcnow)
    updated_at = db.Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def get_survey_data(self):
        """Получить данные опроса как словарь"""
        if self.survey_data:
            try:
                import json
                return json.loads(self.survey_data)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    def set_survey_data(self, data):
        """Установить данные опроса"""
        import json
        self.survey_data = json.dumps(data, ensure_ascii=False)

    def update_survey_field(self, field, value):
        """Обновить конкретное поле в опросе"""
        data = self.get_survey_data()
        data[field] = value
        self.set_survey_data(data)

    # 🔽 связи
    registrations = db.relationship("Registration", backref="user", lazy=True)
    quest_progress = db.relationship("QuestProgress", backref="user", lazy=True)
    sticker_generations = db.relationship("StickerGeneration", backref="user", lazy=True)

    # 🔽 свойства для проверки активностей
    @property
    def has_dance_registration(self):
        """Проверить, есть ли запись на танцы"""
        return Registration.query.filter_by(user_id=self.id, activity_type='dance').first() is not None

    @property
    def has_completed_quest(self):
        """Проверить, завершен ли квест"""
        quest = QuestProgress.query.filter_by(user_id=self.id).first()
        return quest and quest.completed

    @property
    def has_sticker(self):
        """Проверить, создан ли стикер"""
        return StickerGeneration.query.filter_by(user_id=self.id, status='ok').first() is not None

    def to_dict(self):
        data = {
            "id": self.id,
            "telegram_id": self.telegram_id,
            "username": self.username,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "full_name": self.full_name,
            "city": self.city,
            "professional_role": self.professional_role,
            "grade": self.grade,
            "company": self.company,
            "survey_completed": self.survey_completed,
            "consent_given": self.consent_given,
        }

        # Добавляем информацию о стикерпаке, если он сгенерирован
        successful_sticker = StickerGeneration.query.filter_by(
            user_id=self.id, status='ok'
        ).first()
        if successful_sticker:
            data["sticker_pack_url"] = successful_sticker.sticker_set_link

        return data


# ─────────────── Регистрация на активности ───────────────
class Registration(db.Model):
    __tablename__ = "registrations"

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, db.ForeignKey("users.id"), nullable=False)
    activity_type = db.Column(String(50), nullable=False)
    time_slot = db.Column(String(10), nullable=False)
    day = db.Column(String(20), nullable=False)
    created_at = db.Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_reg_user", "user_id"),
        UniqueConstraint("user_id", "activity_type", "day", "time_slot", name="uq_reg_unique"),
    )


# ─────────────── Прогресс квеста (QR) ───────────────
class QuestProgress(db.Model):
    __tablename__ = "quest_progress"

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, db.ForeignKey("users.id"), nullable=False)
    completed_steps = db.Column(Text)
    completed = db.Column(Boolean, default=False)
    completed_at = db.Column(DateTime)
    created_at = db.Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "user_id": self.user_id,
            "completed": self.completed,
            "completed_steps": self.completed_steps,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None
        }


# ─────────────── Генерация стикеров ───────────────
class StickerGeneration(db.Model):
    __tablename__ = "sticker_generations"

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, db.ForeignKey('users.id'), nullable=False)
    pack_url = db.Column(String(255))
    created_at = db.Column(DateTime, default=datetime.utcnow)
    template_used = db.Column(String(255), nullable=True)
    generated_file_id = db.Column(String(255), nullable=True)
    sticker_set_name = db.Column(String(255), nullable=True)
    sticker_set_link = db.Column(String(255), nullable=True)
    status = db.Column(String(50), default='pending')

    @property
    def is_generated(self):
        return self.status == "ok"

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "template_used": self.template_used,
            "generated_file_id": self.generated_file_id,
            "sticker_set_name": self.sticker_set_name,
            "sticker_set_link": self.sticker_set_link,
            "pack_url": self.pack_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "status": self.status,
        }


# ─────────────── Логи админов ───────────────
class AdminLog(db.Model):
    __tablename__ = "admin_logs"

    id = db.Column(Integer, primary_key=True)
    action = db.Column(String(100), nullable=False)
    admin_telegram_id = db.Column(String(20), nullable=False, index=True)
    target_user_id = db.Column(Integer)
    details = db.Column(Text)
    created_at = db.Column(DateTime, default=datetime.utcnow)

# ─────────────── Опрос ───────────────
class SurveyAnswer(db.Model):
    __tablename__ = "survey_answers"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    step_num = db.Column(db.String(10), nullable=False)  # например: "0", "3.1"
    answer_text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ─────────────── Конфигурация системы ───────────────
class SystemConfig(db.Model):
    __tablename__ = "system_config"

    id = db.Column(Integer, primary_key=True)
    config_key = db.Column(String(100), unique=True, nullable=False)
    config_value = db.Column(Text, nullable=False)
    config_type = db.Column(String(20), default='text')  # text, json, int, bool
    description = db.Column(Text)
    created_at = db.Column(DateTime, default=datetime.utcnow)
    updated_at = db.Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def get_value(self):
        """Получить значение с приведением типа"""
        if self.config_type == 'json':
            try:
                return json.loads(self.config_value)
            except:
                return {}
        elif self.config_type == 'int':
            try:
                return int(self.config_value)
            except:
                return 0
        elif self.config_type == 'bool':
            return self.config_value.lower() in ('true', '1', 'yes', 'on')
        return self.config_value

    @staticmethod
    def get_config(key, default=None):
        """Получить значение конфигурации"""
        config = SystemConfig.query.filter_by(config_key=key).first()
        return config.get_value() if config else default

    @staticmethod
    def set_config(key, value, config_type='text', description=None):
        """Установить значение конфигурации"""
        config = SystemConfig.query.filter_by(config_key=key).first()
        if not config:
            config = SystemConfig(config_key=key, config_type=config_type, description=description)
            db.session.add(config)

        if config_type == 'json':
            config.config_value = json.dumps(value, ensure_ascii=False)
        else:
            config.config_value = str(value)

        config.config_type = config_type
        if description:
            config.description = description

        db.session.commit()
        return config

# ─────────────── Слоты для танцев ───────────────
class DanceSlot(db.Model):
    __tablename__ = "dance_slots"

    id = db.Column(Integer, primary_key=True)
    day = db.Column(String(50), nullable=False)
    time_slot = db.Column(String(10), nullable=False)
    max_participants = db.Column(Integer, default=10)
    is_active = db.Column(Boolean, default=True)
    created_at = db.Column(DateTime, default=datetime.utcnow)

    @property
    def current_participants(self):
        return Registration.query.filter_by(
            activity_type='dance',
            day=self.day,
            time_slot=self.time_slot
        ).count()

    @property
    def is_full(self):
        return self.current_participants >= self.max_participants

    def to_dict(self):
        return {
            "id": self.id,
            "day": self.day,
            "time_slot": self.time_slot,
            "max_participants": self.max_participants,
            "current_participants": self.current_participants,
            "is_full": self.is_full,
            "is_active": self.is_active
        }

# ─────────────── Сообщения админов пользователям ───────────────
class AdminMessage(db.Model):
    __tablename__ = "admin_messages"

    id = db.Column(Integer, primary_key=True)
    admin_telegram_id = db.Column(String(20), nullable=False)
    user_telegram_id = db.Column(String(20), nullable=False)
    message_text = db.Column(Text, nullable=False)
    reply_to_message_id = db.Column(Integer, nullable=True)
    sent = db.Column(Boolean, default=False)
    created_at = db.Column(DateTime, default=datetime.utcnow)
    sent_at = db.Column(DateTime, nullable=True)

# ─────────────── Фидбек пользователей ───────────────
class UserFeedback(db.Model):
    __tablename__ = "user_feedback"

    id = db.Column(Integer, primary_key=True)
    user_id = db.Column(Integer, db.ForeignKey('users.id'), nullable=False)
    question_id = db.Column(String(50), nullable=False)  # 'activity_rating', 'team_knowledge', 'recommend_work', 'self_apply'
    answer = db.Column(Text, nullable=False)
    created_at = db.Column(DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='feedback_answers')