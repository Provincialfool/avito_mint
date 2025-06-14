from datetime import datetime
from sqlalchemy import (
    Integer, String, Text, Boolean, DateTime,
    Index, UniqueConstraint
)
from extensions import db

# ─────────────── Отложенные сообщения ───────────────
class ScheduledMessage(db.Model):
    __tablename__ = "scheduled_messages"

    id = db.Column(Integer, primary_key=True)
    message = db.Column(Text, nullable=False)
    scheduled_time = db.Column(DateTime, nullable=False)
    sent = db.Column(Boolean, default=False)
    created_at = db.Column(DateTime, default=datetime.utcnow())

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

    # 🔽 связи
    registrations = db.relationship("Registration", backref="user", lazy=True)
    quest_progress = db.relationship("QuestProgress", backref="user", lazy=True)
    sticker_generation = db.relationship("StickerGeneration", backref="user", uselist=False)

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
        if self.sticker_generation and self.sticker_generation.is_generated:
            data["sticker_pack_url"] = self.sticker_generation.sticker_set_link
        
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
    # Удалите или сделайте nullable=True для полей, которые не используются:
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
            "user_id": self.user_id,
            "template": self.template_used,
            "file_id": self.generated_file_id,
            "sticker_link": self.sticker_set_link,
            "status": self.status,
            "created_at": self.created_at.isoformat()
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