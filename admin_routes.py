from flask import render_template, request, redirect, url_for, flash, jsonify, send_file, abort, session, make_response
from app import app, db
from models import User, Registration, QuestProgress, StickerGeneration, AdminLog, ScheduledMessage, SystemConfig, DanceSlot, AdminMessage, SurveyAnswer, UserFeedback
import pandas as pd
import io
import time
from datetime import datetime
import json
import hashlib
from functools import wraps
import json as json_lib
import os

# Простая авторизация (в production лучше использовать более безопасные методы)
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD_HASH = hashlib.sha256("festival2025".encode()).hexdigest()

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

# Добавляем фильтр для работы с JSON
@app.template_filter('from_json')
def from_json_filter(s):
    try:
        data = json_lib.loads(s or "[]")
        # Фильтруем только числовые значения для квеста
        if isinstance(data, list):
            return [item for item in data if isinstance(item, int)]
        return data
    except:
        return []

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        password_hash = hashlib.sha256(password.encode()).hexdigest()

        if username == ADMIN_USERNAME and password_hash == ADMIN_PASSWORD_HASH:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_home'))
        else:
            flash('Неверный логин или пароль', 'error')

    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))

@app.route('/admin')
@admin_required
def admin_home():
    """Главная страница админки"""
    # Собираем статистику
    stats = {
        'total_users': User.query.count(),
        'quest_completed': QuestProgress.query.filter_by(completed=True).count(),
        'dance_registered': db.session.query(Registration.user_id).filter_by(activity_type='dance').distinct().count(),
        'stickers_generated': StickerGeneration.query.filter_by(status='completed').count()
    }
    return render_template('admin_home.html', stats=stats)

@app.route('/')
@admin_required
def index():
    # Метрики
    total_users = User.query.count()
    total_vacancy = User.query.filter_by(interested_in_vacancies=True).count()
    total_quest_complete = QuestProgress.query.filter_by(completed=True).count()
    total_stickers = StickerGeneration.query.filter_by(status='ok').count()

    # Для квестов — последние, с пользователем
    recent_quest_winners = db.session.query(QuestProgress, User)\
        .join(User, QuestProgress.user_id == User.id)\
        .filter(QuestProgress.completed == True)\
        .order_by(QuestProgress.completed_at.desc())\
        .limit(20).all()

    # Для стикеров — последние, с пользователем
    recent_stickers = db.session.query(StickerGeneration, User)\
        .join(User, StickerGeneration.user_id == User.id)\
        .filter(StickerGeneration.status == 'ok')\
        .order_by(StickerGeneration.created_at.desc())\
        .limit(20).all()

    # Слоты танцев из базы данных
    dance_slots_data = []
    dance_slots = DanceSlot.query.filter_by(is_active=True).order_by(DanceSlot.day, DanceSlot.time_slot).all()

    for slot in dance_slots:
        regs = Registration.query.filter_by(
            activity_type='dance', 
            day=slot.day, 
            time_slot=slot.time_slot
        ).order_by(Registration.created_at).all()
        users = [User.query.get(reg.user_id) for reg in regs]
        dance_slots_data.append({
            'id': slot.id,
            'day': slot.day,
            'time_slot': slot.time_slot,
            'max_participants': slot.max_participants,
            'current_participants': len(users),
            'users': users
        })

    return render_template(
        'admin_dashboard.html',
        total_users=total_users,
        total_vacancy=total_vacancy,
        total_quest_complete=total_quest_complete,
        total_stickers=total_stickers,
        dance_slots=dance_slots_data,
        recent_quest_winners=recent_quest_winners,
        recent_stickers=recent_stickers
    )

@app.route('/participants')
@admin_required
def participants():
    page = request.args.get('page', 1, type=int)
    per_page = 20

    search = request.args.get('search', '')
    filter_consent = request.args.get('consent', '')
    filter_survey = request.args.get('survey', '')
    filter_career = request.args.get('career', '')
    filter_dance = request.args.get('dance', '')
    filter_quest = request.args.get('quest', '')
    filter_stickers = request.args.get('stickers', '')

    query = User.query

    if search:
        query = query.filter(
            db.or_(
                User.first_name.ilike(f'%{search}%'),
                User.last_name.ilike(f'%{search}%'),
                User.username.ilike(f'%{search}%'),
                User.telegram_id.ilike(f'%{search}%')
            )
        )

    if filter_consent:
        query = query.filter(User.consent_given == (filter_consent == 'true'))

    if filter_survey:
        query = query.filter(User.survey_completed == (filter_survey == 'true'))

    if filter_career:
        query = query.filter(User.interested_in_vacancies == (filter_career == 'true'))

    if filter_dance:
        if filter_dance == 'true':
            query = query.join(Registration).filter(Registration.activity_type == 'dance')
        elif filter_dance == 'false':
            query = query.outerjoin(Registration).filter(
                db.or_(Registration.id.is_(None), Registration.activity_type != 'dance')
            )

    if filter_quest:
        if filter_quest == 'true':
            query = query.join(QuestProgress).filter(QuestProgress.completed == True)
        elif filter_quest == 'false':
            query = query.outerjoin(QuestProgress).filter(
                db.or_(QuestProgress.id.is_(None), QuestProgress.completed != True)
            )

    if filter_stickers:
        if filter_stickers == 'true':
            query = query.join(StickerGeneration).filter(StickerGeneration.status == 'ok')
        elif filter_stickers == 'false':
            query = query.outerjoin(StickerGeneration).filter(
                db.or_(StickerGeneration.id.is_(None), StickerGeneration.status != 'ok')
            )

    users = query.distinct().order_by(User.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template('participants.html', users=users)

@app.route('/export_csv')
@admin_required
def export_csv():
    """Экспорт пользователей в CSV"""
    users = User.query.all()

    output = io.BytesIO()

    # Заголовки
    headers = ['ID', 'Telegram ID', 'Имя пользователя', 'Имя', 'Фамилия', 'ФИО', 'Город', 'Роль', 'Компания', 
               'Согласие', 'Опрос завершен', 'Интерес к вакансиям', 'Дата регистрации']

    # Записываем BOM для корректного отображения в Excel
    output.write('\ufeff'.encode('utf-8'))

    # Записываем заголовки
    output.write(','.join(headers).encode('utf-8'))
    output.write('\n'.encode('utf-8'))

    # Данные
    for user in users:
        row = [
            str(user.id),
            str(user.telegram_id),
            user.username or '',
            user.first_name or '',
            user.last_name or '',
            user.full_name or '',
            user.city or '',
            user.professional_role or '',
            user.company or '',
            'Да' if user.consent_given else 'Нет',
            'Да' if user.survey_completed else 'Нет',
            'Да' if user.interested_in_vacancies else 'Нет',
            user.created_at.strftime('%Y-%m-%d %H:%M:%S') if user.created_at else ''
        ]

        # Экранируем запятые и кавычки в данных
        escaped_row = []
        for field in row:
            field = str(field).replace('"', '""')
            if ',' in field or '"' in field or '\n' in field:
                field = f'"{field}"'
            escaped_row.append(field)

        output.write(','.join(escaped_row).encode('utf-8'))
        output.write('\n'.encode('utf-8'))

    output.seek(0)

    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=participants.csv"
    response.headers["Content-type"] = "text/csv; charset=utf-8"

    return response

@app.route('/export_filtered_csv')
@admin_required
def export_filtered_csv():
    """Export filtered participants data as CSV"""
    import csv
    import io
    from flask import make_response

    # Используем те же фильтры, что и в participants()
    query = User.query

    # Применяем фильтры из URL параметров
    search = request.args.get('search', '').strip()
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            db.or_(
                User.first_name.ilike(search_pattern),
                User.last_name.ilike(search_pattern),
                User.username.ilike(search_pattern),
                User.full_name.ilike(search_pattern)
            )
        )

    # Применяем остальные фильтры
    consent = request.args.get('consent')
    if consent == 'true':
        query = query.filter(User.consent_given == True)
    elif consent == 'false':
        query = query.filter(User.consent_given == False)

    survey = request.args.get('survey')
    if survey == 'true':
        query = query.filter(User.survey_completed == True)
    elif survey == 'false':
        query = query.filter(User.survey_completed == False)

    dance = request.args.get('dance')
    if dance == 'true':
        query = query.join(Registration).filter(Registration.activity_type == 'dance')
    elif dance == 'false':
        registered_users = db.session.query(Registration.user_id).filter_by(activity_type='dance').subquery()
        query = query.filter(~User.id.in_(registered_users))

    quest = request.args.get('quest')
    if quest == 'true':
        query = query.join(QuestProgress).filter(QuestProgress.completed == True)
    elif quest == 'false':
        completed_users = db.session.query(QuestProgress.user_id).filter_by(completed=True).subquery()
        query = query.filter(~User.id.in_(completed_users))

    career = request.args.get('career')
    if career == 'true':
        query = query.filter(User.interested_in_vacancies == True)
    elif career == 'false':
        query = query.filter(User.interested_in_vacancies == False)

    stickers = request.args.get('stickers')
    if stickers == 'true':
        query = query.join(StickerGeneration).filter(StickerGeneration.status == 'ok')
    elif stickers == 'false':
        generated_users = db.session.query(StickerGeneration.user_id).filter_by(status='ok').subquery()
        query = query.filter(~User.id.in_(generated_users))

    users = query.distinct().order_by(User.created_at.desc()).all()

    if not users:
        flash('Нет данных для экспорта с текущими фильтрами', 'warning')
        return redirect(url_for('participants'))

    # Создаем CSV
    output = io.BytesIO()
    string_output = io.StringIO()
    writer = csv.writer(string_output)

    # Заголовки (те же что и в полном экспорте)
    writer.writerow([
        'ID', 'Telegram ID', 'Username', 'Имя', 'Фамилия', 'ФИО полное',
        'Город', 'Проф. роль', 'Грейд', 'Компания', 'Дата регистрации',
        'Согласие ПД', 'Опрос завершен', 'Интерес к вакансиям', 
        'Записи на танцы', 'Квест завершен', 'Найдено стикеров квеста',
        'Стикерпак создан', 'Ссылка на стикерпак'
    ])

    for user in users:
        # Данные о регистрациях
        dance_registrations = Registration.query.filter_by(
            user_id=user.id, activity_type='dance'
        ).all()
        dance_info = '; '.join([f"{r.day} {r.time_slot}" for r in dance_registrations])

        # Данные о квесте
        quest = QuestProgress.query.filter_by(user_id=user.id).first()
        quest_completed = 'Да' if quest and quest.completed else 'Нет'
        quest_steps = len(json.loads(quest.completed_steps or "[]")) if quest else 0

        # Данные о стикерах
        sticker = StickerGeneration.query.filter_by(
            user_id=user.id, status='ok'
        ).first()
        sticker_created = 'Да' if sticker else 'Нет'
        sticker_url = sticker.pack_url or sticker.sticker_set_link if sticker else ''

        # Данные опроса
        survey_data = user.get_survey_data()

        writer.writerow([
            user.id,
            user.telegram_id,
            user.username or '',
            user.first_name or '',
            user.last_name or '',
            survey_data.get('full_name', '') or user.full_name or '',
            survey_data.get('city', '') or user.city or '',
            survey_data.get('professional_role', '') or user.professional_role or '',
            user.grade or '',
            survey_data.get('company', '') or user.company or '',
            user.created_at.strftime('%d.%m.%Y %H:%M'),
            'Да' if user.consent_given else 'Нет',
            'Да' if user.survey_completed else 'Нет',
            'Да' if user.interested_in_vacancies else 'Нет',
            dance_info,
            quest_completed,
            quest_steps,
            sticker_created,
            sticker_url
        ])

    # Конвертируем в байты с BOM для правильного отображения в Excel
    csv_content = string_output.getvalue()
    output.write('\ufeff'.encode('utf-8-sig'))  # BOM для UTF-8
    output.write(csv_content.encode('utf-8'))
    output.seek(0)

    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8-sig'
    response.headers['Content-Disposition'] = f'attachment; filename=participants_filtered_{datetime.now().strftime("%Y%m%d_%H%M")}.csv'
    return response

@app.route('/broadcast', methods=['GET', 'POST'])
@admin_required
def broadcast():
    import time
    import os
    from flask import flash, redirect, url_for, request
    from werkzeug.utils import secure_filename

    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

    def allowed_file(filename):
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

    if request.method == 'POST':
        # Обработка отправки сообщения
        message_text = request.form.get('message_text', '').strip()
        if not message_text:
            # Пробуем получить из другого поля
            message_text = request.form.get('message', '').strip()

        photo_file = request.files.get('photo')
        send_to = request.form.get('send_to', 'all')
        specific_user = request.form.get('specific_user', '').strip()

        if not message_text:
            flash('Сообщение не может быть пустым', 'error')
            return redirect(url_for('broadcast'))

        # Сохраняем фото если загружено
        photo_url = None
        if photo_file and photo_file.filename and allowed_file(photo_file.filename):
            filename = secure_filename(photo_file.filename)
            timestamp = int(time.time())
            filename = f"{timestamp}_{filename}"
            upload_dir = os.path.join('static', 'uploads')
            os.makedirs(upload_dir, exist_ok=True)
            file_path = os.path.join(upload_dir, filename)
            photo_file.save(file_path)
            photo_url = file_path

        # Получаем список пользователей для отправки
        users_query = User.query.filter_by(consent_given=True)

        if specific_user:
            # Отправка конкретному пользователю
            users = users_query.filter_by(telegram_id=specific_user).all()
            if not users:
                flash('Пользователь не найден или не дал согласие на обработку данных', 'error')
                return redirect(url_for('broadcast'))
        elif send_to == 'all':
            users = users_query.all()
        elif send_to == 'survey_completed':
            users = users_query.filter_by(survey_completed=True).all()
        elif send_to == 'no_survey':
            users = users_query.filter_by(survey_completed=False).all()
        elif send_to == 'dance_registered':
            registered_user_ids = db.session.query(Registration.user_id).filter_by(activity_type='dance').distinct().all()
            user_ids = [uid[0] for uid in registered_user_ids]
            users = users_query.filter(User.id.in_(user_ids)).all()
        elif send_to == 'sticker_generated':
            generated_user_ids = db.session.query(StickerGeneration.user_id).filter(
                StickerGeneration.pack_url.isnot(None)
            ).distinct().all()
            user_ids = [uid[0] for uid in generated_user_ids]
            users = users_query.filter(User.id.in_(user_ids)).all()
        elif send_to == 'quest_completed':
            completed_user_ids = db.session.query(QuestProgress.user_id).filter_by(completed=True).distinct().all()
            user_ids = [uid[0] for uid in completed_user_ids]
            users = users_query.filter(User.id.in_(user_ids)).all()
        else:
            users = []

        # Импортируем бота
        try:
            from bot import bot
        except ImportError:
            flash('Ошибка: не удалось импортировать бота', 'error')
            return redirect(url_for('broadcast'))

        # Отправляем сообщения
        success_count = 0
        error_count = 0

        for user in users:
            try:
                if photo_url and os.path.exists(photo_url):
                    with open(photo_url, 'rb') as photo:
                        bot.send_photo(int(user.telegram_id), photo, caption=message_text, parse_mode='HTML')
                else:
                    bot.send_message(int(user.telegram_id), message_text, parse_mode='HTML')
                success_count += 1
                time.sleep(0.1)  # Небольшая задержка между сообщениями
            except Exception as e:
                print(f"Error sending to user {user.telegram_id}: {e}")
                error_count += 1

        flash(f'Сообщение отправлено {success_count} пользователям. Ошибок: {error_count}', 'success')
        return redirect(url_for('broadcast'))

    scheduled_messages = ScheduledMessage.query.order_by(ScheduledMessage.scheduled_time).all()

    # Статистика фидбека
    feedback_stats = {
        'total': UserFeedback.query.count(),
        'avg_activity': db.session.query(db.func.avg(db.cast(UserFeedback.answer, db.Float))).filter_by(question_id='activity_rating').scalar(),
        'avg_recommend': db.session.query(db.func.avg(db.cast(UserFeedback.answer, db.Float))).filter_by(question_id='recommend_work').scalar()
    }

    return render_template('broadcast.html', scheduled_messages=scheduled_messages, feedback_stats=feedback_stats)

@app.route('/send_feedback_survey', methods=['POST'])
@admin_required
def send_feedback_survey():
    """Запуск опроса фидбека"""
    send_to = request.form.get('send_to', 'all')

    # Получаем список пользователей
    users_query = User.query.filter_by(consent_given=True)

    if send_to == 'all':
        users = users_query.all()
    elif send_to == 'survey_completed':
        users = users_query.filter_by(survey_completed=True).all()
    elif send_to == 'dance_registered':
        registered_user_ids = db.session.query(Registration.user_id).filter_by(activity_type='dance').distinct().all()
        user_ids = [uid[0] for uid in registered_user_ids]
        users = users_query.filter(User.id.in_(user_ids)).all()
    elif send_to == 'quest_completed':
        completed_user_ids = db.session.query(QuestProgress.user_id).filter_by(completed=True).distinct().all()
        user_ids = [uid[0] for uid in completed_user_ids]
        users = users_query.filter(User.id.in_(user_ids)).all()
    else:
        users = []

    feedback_text = """🌟 **Фидбэк в последний день фестиваля**

Чтобы наши мероприятия становились лучше, нам очень нужна твоя обратная связь 😉
Ответь, пожалуйста, на несколько вопросов — так мы сможем понять, что на Дикой Мяте получилось круто, а что нужно доработать в следующий раз.

Как тебе зона активностей Avito Team? 
Отправь в ответ оценку от 1 до 10, где 10 — это супер."""

    # Импортируем бота
    try:
        from bot import bot
    except ImportError:
        flash('Ошибка: не удалось импортировать бота', 'error')
        return redirect(url_for('broadcast'))

    # Отправляем сообщения
    success_count = 0
    error_count = 0

    for user in users:
        try:
            # Устанавливаем состояние фидбека для пользователя
            from bot import user_states
            user_states[int(user.telegram_id)] = "feedback|activity_rating"

            bot.send_message(int(user.telegram_id), feedback_text, parse_mode='Markdown')
            success_count += 1
            time.sleep(0.1)
        except Exception as e:
            print(f"Error sending feedback to user {user.telegram_id}: {e}")
            error_count += 1

    flash(f'Опрос фидбека отправлен {success_count} пользователям. Ошибок: {error_count}', 'success')
    return redirect(url_for('broadcast'))

@app.route('/dance_slots')
@admin_required
def dance_slots():
    """Управление слотами танцев"""
    slots = DanceSlot.query.order_by(DanceSlot.day, DanceSlot.time_slot).all()
    return render_template('dance_slots.html', slots=slots)

@app.route('/dance_slots/add', methods=['POST'])
def add_dance_slot():
    """Добавление нового слота"""
    day = request.form.get('day')
    time_slot = request.form.get('time_slot')
    max_participants = request.form.get('max_participants', 10, type=int)

    if not day or not time_slot:
        flash('День и время обязательны', 'danger')
        return redirect(url_for('dance_slots'))

    # Проверяем дубликат
    existing = DanceSlot.query.filter_by(day=day, time_slot=time_slot).first()
    if existing:
        flash('Такой слот уже существует', 'warning')
        return redirect(url_for('dance_slots'))

    slot = DanceSlot(day=day, time_slot=time_slot, max_participants=max_participants)
    db.session.add(slot)
    db.session.commit()
    flash('Слот добавлен', 'success')
    return redirect(url_for('dance_slots'))

@app.route('/dance_slots/delete/<int:slot_id>')
def delete_dance_slot(slot_id):
    """Удаление слота"""
    slot = DanceSlot.query.get_or_404(slot_id)

    # Проверяем, есть ли записи
    registrations = Registration.query.filter_by(
        activity_type='dance',
        day=slot.day,
        time_slot=slot.time_slot
    ).count()

    if registrations > 0:
        flash(f'Нельзя удалить слот с {registrations} записями', 'danger')
        return redirect(url_for('dance_slots'))

    db.session.delete(slot)
    db.session.commit()
    flash('Слот удален', 'success')
    return redirect(url_for('dance_slots'))

@app.route('/dance_slots/toggle/<int:slot_id>')
def toggle_dance_slot(slot_id):
    """Активация/деактивация слота"""
    slot = DanceSlot.query.get_or_404(slot_id)
    slot.is_active = not slot.is_active
    db.session.commit()
    status = 'активирован' if slot.is_active else 'деактивирован'
    flash(f'Слот {status}', 'success')
    return redirect(url_for('dance_slots'))

@app.route('/config')
@admin_required
def config():
    """Управление конфигурацией"""
    from datetime import datetime
    import os

    configs = SystemConfig.query.order_by(SystemConfig.config_key).all()

    # Получаем текущие настройки бота (приоритет: база данных, потом переменные окружения)
    bot_config = {
        'BOT_TOKEN': SystemConfig.get_config('BOT_TOKEN', os.environ.get('BOT_TOKEN', '')),
        'REPLICATE_API_TOKEN': SystemConfig.get_config('REPLICATE_API_TOKEN', os.environ.get('REPLICATE_API_TOKEN', '')),
        'BOT_MODE': SystemConfig.get_config('BOT_MODE', 'polling'),
        'WEBHOOK_DOMAIN': SystemConfig.get_config('WEBHOOK_DOMAIN', ''),
    }

    return render_template('config.html', 
                         configs=configs, 
                         bot_config=bot_config,
                         current_time=datetime.now())

@app.route('/config/edit/<int:config_id>', methods=['GET', 'POST'])
def edit_config(config_id):
    """Редактирование конфигурации"""
    config = SystemConfig.query.get_or_404(config_id)

    if request.method == 'POST':
        config.config_value = request.form.get('config_value', '')
        config.description = request.form.get('description', '')
        db.session.commit()
        flash('Конфигурация обновлена', 'success')
        return redirect(url_for('config'))

    return render_template('edit_config.html', config=config)

@app.route('/reinit_replicate', methods=['POST'])
@admin_required
def reinit_replicate():
    """Переинициализация Replicate клиента"""
    try:
        from sticker_generator import reinitialize_replicate_client
        success = reinitialize_replicate_client()
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Replicate клиент успешно переинициализирован'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Не удалось переинициализировать - токен не найден'
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/config/bot', methods=['POST'])
def update_bot_config():
    """Обновление конфигурации бота"""
    try:
        # Сохраняем настройки
        bot_token = request.form.get('bot_token', '').strip()
        replicate_token = request.form.get('replicate_token', '').strip()
        bot_mode = request.form.get('bot_mode', 'polling')
        webhook_domain = request.form.get('webhook_domain', '').strip()

        old_token = SystemConfig.get_config('BOT_TOKEN')
        token_changed = False

        if bot_token:
            SystemConfig.set_config('BOT_TOKEN', bot_token, 'bot', 'Токен Telegram бота')
            # Обновляем переменную окружения
            os.environ['BOT_TOKEN'] = bot_token
            if old_token != bot_token:
                token_changed = True

        if replicate_token:
            SystemConfig.set_config('REPLICATE_API_TOKEN', replicate_token, 'bot', 'Токен Replicate API')
            os.environ['REPLICATE_API_TOKEN'] = replicate_token

        SystemConfig.set_config('BOT_MODE', bot_mode, 'bot', 'Режим работы бота (polling/webhook)')

        if webhook_domain:
            SystemConfig.set_config('WEBHOOK_DOMAIN', webhook_domain, 'bot', 'Домен для webhook')

        if token_changed:
            flash('Настройки бота обновлены. Требуется перезапуск для применения нового токена!', 'warning')
        else:
            flash('Настройки бота обновлены', 'success')

    except Exception as e:
        flash(f'Ошибка при сохранении настроек: {str(e)}', 'danger')

    return redirect(url_for('config'))

@app.route('/config/add', methods=['POST'])
def add_config():
    """Добавление новой настройки"""
    key = request.form.get('config_key', '').strip()
    value = request.form.get('config_value', '').strip()
    config_type = request.form.get('config_type', 'text')
    description = request.form.get('description', '').strip()

    if not key or not value:
        flash('Ключ и значение обязательны', 'danger')
        return redirect(url_for('config'))

    # Проверяем дубликат
    existing = SystemConfig.query.filter_by(config_key=key).first()
    if existing:
        flash('Настройка с таким ключом уже существует', 'warning')
        return redirect(url_for('config'))

    SystemConfig.set_config(key, value, config_type, description)
    flash('Настройка добавлена', 'success')
    return redirect(url_for('config'))

@app.route('/text_messages')
@admin_required
def text_messages():
    """Редактирование текстовых сообщений"""

    # Основные сообщения
    main_messages = [
        {
            'key': 'CONSENT_TEXT',
            'title': 'Текст согласия на обработку данных',
            'text': SystemConfig.get_config('CONSENT_TEXT', get_default_text('CONSENT_TEXT')),
            'description': 'Сообщение с согласием на обработку персональных данных'
        },
        {
            'key': 'MAIN_MENU_TEXT',
            'title': 'Главное меню (первый раз)',
            'text': SystemConfig.get_config('MAIN_MENU_TEXT', get_default_text('MAIN_MENU_TEXT')),
            'description': 'Текст главного меню при первом входе'
        },
        {
            'key': 'MAIN_MENU_TEXT_NO_THANKS',
            'title': 'Главное меню (обычное)',
            'text': SystemConfig.get_config('MAIN_MENU_TEXT_NO_THANKS', get_default_text('MAIN_MENU_TEXT_NO_THANKS')),
            'description': 'Текст главного меню при повторных входах'
        }
    ]

    # Активности
    activity_messages = [
        {
            'key': 'DANCE_INTRO',
            'title': 'Танцы - вступление',
            'text': SystemConfig.get_config('DANCE_INTRO', get_default_text('DANCE_INTRO')),
            'description': 'Текст при входе в раздел танцев'
        },
        {
            'key': 'WORKSHOP_TEXT',
            'title': 'Мастер-класс',
            'text': SystemConfig.get_config('WORKSHOP_TEXT', get_default_text('WORKSHOP_TEXT')),
            'description': 'Описание мастер-класса'
        },
        {
            'key': 'CAREER_MESSAGE',
            'title': 'Карьера в Avito',
            'text': SystemConfig.get_config('CAREER_MESSAGE', get_default_text('CAREER_MESSAGE')),
            'description': 'Сообщение о карьерных возможностях'
        },
        {
            'key': 'STICKER_START_MESSAGE',
            'title': 'Стикерпак - начало',
            'text': SystemConfig.get_config('STICKER_START_MESSAGE', get_default_text('STICKER_START_MESSAGE')),
            'description': 'Сообщение при входе в раздел стикеров'
        },
        {
            'key': 'SCHEDULE_MESSAGE',
            'title': 'Расписание',
            'text': SystemConfig.get_config('SCHEDULE_MESSAGE', get_default_text('SCHEDULE_MESSAGE')),
            'description': 'Расписание всех мероприятий'
        },
        {
            'key': 'MAP_TEXT',
            'title': 'Карта',
            'text': SystemConfig.get_config('MAP_TEXT', get_default_text('MAP_TEXT')),
            'description': 'Описание карты'
        },
        {
            'key': 'FOREST_TEXT_1',
            'title': 'Лес - часть 1',
            'text': SystemConfig.get_config('FOREST_TEXT_1', get_default_text('FOREST_TEXT_1')),
            'description': 'Первая часть описания леса'
        },
        {
            'key': 'FOREST_TEXT_2',
            'title': 'Лес - часть 2',
            'text': SystemConfig.get_config('FOREST_TEXT_2', get_default_text('FOREST_TEXT_2')),
            'description': 'Вторая часть описания леса'
        }
    ]

    # Получаем вопросы опроса
    survey_questions = []
    for i in range(5):  # У нас 5 вопросов
        question = SystemConfig.get_config(f'SURVEY_QUESTION_{i}', get_default_survey_question(i))
        survey_questions.append(question)

    # Проверяем, включен ли опрос
    survey_enabled = SystemConfig.get_config('SURVEY_ENABLED', 'true').lower() in ('true', '1', 'yes', 'on')

    # Получаем шаги квеста
    quest_steps = []
    quest_total = SystemConfig.get_config('QUEST_TOTAL_STEPS', 9)
    for i in range(quest_total + 1):  # +1 для стартового сообщения
        hint = SystemConfig.get_config(f'QUEST_STEP_{i}_HINT', get_default_quest_step(i))
        image = SystemConfig.get_config(f'QUEST_STEP_{i}_IMAGE', f'img/step{i}.png' if i > 0 else 'img/quest.jpeg')
        quest_steps.append({
            'step': i,
            'hint': hint,
            'image': image,
            'title': 'Стартовое сообщение' if i == 0 else f'Подсказка {i}'
        })

    return render_template('text_messages.html',
                         main_messages=main_messages,
                         activity_messages=activity_messages,
                         survey_questions=survey_questions,
                         quest_steps=quest_steps,
                         survey_enabled=survey_enabled)

@app.route('/text_messages/update', methods=['POST'])
def update_text_message():
    """Обновление текстового сообщения"""
    message_key = request.form.get('message_key')
    message_text = request.form.get('message_text', '').strip()

    if not message_key or not message_text:
        flash('Ключ и текст сообщения обязательны', 'danger')
        return redirect(url_for('text_messages'))

    SystemConfig.set_config(message_key, message_text, 'text', f'Текстовое сообщение: {message_key}')

    # Принудительно обновляем кеш
    try:
        from text_cache import text_cache
        text_cache.force_update()
    except:
        pass

    flash('Сообщение обновлено', 'success')
    return redirect(url_for('text_messages'))

@app.route('/text_messages/survey', methods=['POST'])
def update_survey_questions():
    """Обновление вопросов опроса"""
    questions = []
    for i in range(5):
        question_text = request.form.get(f'question_{i}', '').strip()
        if question_text:
            SystemConfig.set_config(f'SURVEY_QUESTION_{i}', question_text, 'text', f'Вопрос опроса {i+1}')
            questions.append(question_text)

    # Также сохраняем как массив для совместимости
    if questions:
        SystemConfig.set_config('SURVEY_QUESTIONS', questions, 'json', 'Вопросы опроса')

    # Принудительно обновляем кеш
    try:
        from text_cache import text_cache
        text_cache.force_update()
    except:
        pass

    flash('Вопросы опроса обновлены', 'success')
    return redirect(url_for('text_messages'))

@app.route('/text_messages/quest', methods=['POST'])
def update_quest_steps():
    """Обновление шагов квеста"""
    try:
        quest_total = SystemConfig.get_config('QUEST_TOTAL_STEPS', 9)

        for i in range(quest_total + 1):  # +1 для стартового сообщения
            hint_text = request.form.get(f'quest_step_{i}_hint', '').strip()
            image_path = request.form.get(f'quest_step_{i}_image', '').strip()

            if hint_text:
                SystemConfig.set_config(f'QUEST_STEP_{i}_HINT', hint_text, 'text', f'Подсказка квеста {i}')

            if image_path:
                SystemConfig.set_config(f'QUEST_STEP_{i}_IMAGE', image_path, 'text', f'Изображение квеста {i}')

        # Принудительно обновляем кеш
        try:
            from text_cache import text_cache
            text_cache.force_update()
        except:
            pass

        flash('Шаги квеста обновлены', 'success')
    except Exception as e:
        flash(f'Ошибка при обновлении квеста: {str(e)}', 'error')

    return redirect(url_for('text_messages'))

@app.route('/restart_bot', methods=['POST'])
def restart_bot():
    """Перезапуск бота"""
    try:
        # Проверяем режим работы
        bot_mode = SystemConfig.get_config('BOT_MODE', 'polling')

        if bot_mode == 'webhook':
            # Для webhook режима пытаемся переустановить webhook
            success = setup_webhook_from_admin()
            if success:
                return jsonify({'success': True, 'message': 'Webhook переустановлен'})
            else:
                return jsonify({'success': False, 'error': 'Не удалось установить webhook'})
        else:
            # Для polling режима просто сообщаем о необходимости перезапуска
            return jsonify({'success': True, 'message': 'Для применения изменений необходимо перезапустить приложение'})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

def setup_webhook_from_admin():
    """Установка webhook из админки"""
    try:
        from bot import bot
        
        # Получаем настройки
        webhook_domain = SystemConfig.get_config('WEBHOOK_DOMAIN', '')
        bot_token = SystemConfig.get_config('BOT_TOKEN', '')
        
        if not webhook_domain or not bot_token:
            return False
            
        # Формируем URL
        webhook_url = f"{webhook_domain.rstrip('/')}/webhook"
        
        # Удаляем старый webhook
        bot.remove_webhook()
        import time
        time.sleep(1)
        
        # Устанавливаем новый
        success = bot.set_webhook(
            url=webhook_url,
            max_connections=10,
            allowed_updates=["message", "callback_query"]
        )
        
        if success:
            logging.info(f"✅ Webhook установлен через админку: {webhook_url}")
            return True
        else:
            logging.error("❌ Не удалось установить webhook через админку")
            return False
            
    except Exception as e:
        logging.error(f"❌ Ошибка установки webhook через админку: {e}")
        return False

@app.route('/webhook/status')
@admin_required
def webhook_status():
    """Проверка статуса webhook"""
    try:
        from bot import bot
        webhook_info = bot.get_webhook_info()
        
        return jsonify({
            'success': True,
            'webhook_info': {
                'url': webhook_info.url,
                'has_custom_certificate': webhook_info.has_custom_certificate,
                'pending_update_count': webhook_info.pending_update_count,
                'last_error_date': webhook_info.last_error_date.isoformat() if webhook_info.last_error_date else None,
                'last_error_message': webhook_info.last_error_message,
                'max_connections': webhook_info.max_connections,
                'allowed_updates': webhook_info.allowed_updates
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/webhook/set', methods=['POST'])
@admin_required  
def set_webhook_route():
    """Установка webhook через API"""
    try:
        success = setup_webhook_from_admin()
        if success:
            return jsonify({'success': True, 'message': 'Webhook успешно установлен'})
        else:
            return jsonify({'success': False, 'error': 'Не удалось установить webhook'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/webhook/remove', methods=['POST'])
@admin_required
def remove_webhook_route():
    """Удаление webhook через API"""
    try:
        from bot import bot
        success = bot.remove_webhook()
        if success:
            return jsonify({'success': True, 'message': 'Webhook успешно удален'})
        else:
            return jsonify({'success': False, 'error': 'Не удалось удалить webhook'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/cleanup_bot', methods=['POST'])
@admin_required
def cleanup_bot():
    """Принудительная очистка состояния бота"""
    try:
        # Удаляем webhook
        from bot import bot
        bot.remove_webhook()
        
        # Очищаем состояния пользователей
        try:
            from bot import user_states
            user_states.clear()
        except:
            pass
        
        # Логируем действие
        logging.info("🧹 Выполнена принудительная очистка состояния бота")
        
        return jsonify({
            'success': True, 
            'message': 'Состояние бота очищено. Webhook удален. Теперь можно безопасно запустить новый экземпляр.'
        })
        
    except Exception as e:
        logging.error(f"Ошибка при очистке состояния бота: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/deployment/redeploy', methods=['POST'])
@admin_required
def redeploy_app():
    """Инициация повторного деплоя"""
    try:
        # Проверяем, что мы в deployment окружении
        if not os.environ.get('REPL_DEPLOYMENT'):
            return jsonify({'success': False, 'error': 'Функция доступна только в deployment'})
        
        # Логируем событие
        logging.info("🚀 Инициирован повторный деплой через админку")
        
        # Сохраняем информацию о деплое
        SystemConfig.set_config('LAST_REDEPLOY', datetime.now().isoformat(), 'text', 'Время последнего деплоя')
        
        return jsonify({
            'success': True, 
            'message': 'Деплой инициирован. Изменения будут применены в течение нескольких минут.'
        })
        
    except Exception as e:
        logging.error(f"Ошибка при инициации деплоя: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/deployment/status')
@admin_required
def deployment_status():
    """Получение статуса deployment"""
    try:
        status = {
            'is_deployment': bool(os.environ.get('REPL_DEPLOYMENT')),
            'repl_url': os.environ.get('REPL_URL', 'Не установлен'),
            'deployment_mode': 'Autoscale' if os.environ.get('REPL_DEPLOYMENT') else 'Development',
            'last_redeploy': SystemConfig.get_config('LAST_REDEPLOY', 'Никогда'),
            'bot_mode': SystemConfig.get_config('BOT_MODE', 'polling')
        }
        return jsonify(status)
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/check_bot_status')
@admin_required
def check_bot_status():
    """Проверка статуса бота"""
    try:
        from bot import bot
        me = bot.get_me()
        return jsonify({
            'success': True,
            'bot_info': {
                'username': me.username,
                'first_name': me.first_name,
                'id': me.id
            },
            'mode': SystemConfig.get_config('BOT_MODE', 'polling')
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/download_logs')
@admin_required  
def download_logs():
    """Скачать логи бота"""
    import os
    log_files = ['app.log', 'webhook_bot.log', 'logs/sticker_generator.log']

    for log_file in log_files:
        if os.path.exists(log_file):
            return send_file(log_file, as_attachment=True, download_name=f"bot_logs_{datetime.now().strftime('%Y%m%d_%H%M')}.log")

    # Если логов нет, создаем пустой файл
    import io
    output = io.StringIO()
    output.write("Логи не найдены\n")
    output.seek(0)

    bytes_output = io.BytesIO()
    bytes_output.write(output.getvalue().encode('utf-8'))
    bytes_output.seek(0)

    return send_file(bytes_output, as_attachment=True, download_name=f"no_logs_{datetime.now().strftime('%Y%m%d_%H%M')}.txt")

@app.route('/export_config')
def export_config():
    """Экспорт конфигурации"""
    configs = SystemConfig.query.all()
    data = []
    for config in configs:
        data.append({
            'key': config.config_key,
            'value': config.config_value,
            'type': config.config_type,
            'description': config.description
        })

    import json
    from flask import make_response
    response = make_response(json.dumps(data, ensure_ascii=False, indent=2))
    response.headers['Content-Type'] = 'application/json; charset=utf-8'
    response.headers['Content-Disposition'] = 'attachment; filename=bot_config.json'
    return response

def get_default_text(key):
    """Получить текст по умолчанию из text.py"""
    try:
        import text
        return getattr(text, key, '')
    except:
        return ''

def get_default_survey_question(index):
    """Получить вопрос опроса по умолчанию"""
    try:
        import text
        questions = getattr(text, 'SURVEY_QUESTIONS', [])
        return questions[index] if index < len(questions) else ''
    except:
        return ''

def get_default_quest_step(index):
    """Получить шаг квеста по умолчанию"""
    try:
        import quest
        steps = getattr(quest, 'QUEST_STEPS', [])
        if index < len(steps):
            return steps[index].get('hint', '')
        return ''
    except:
        return ''

@app.route('/text_messages/import_from_text', methods=['POST'])
@admin_required
def import_from_text_py():
    """Импорт всех текстов из text.py"""
    try:
        import text

        # Список всех текстовых констант из text.py
        text_constants = [
            'CONSENT_TEXT', 'MAIN_MENU_TEXT', 'MAIN_MENU_TEXT_NO_THANKS',
            'DANCE_INTRO', 'DANCE_CHOOSE_SLOT', 'DANCE_CONFIRMATION', 
            'DANCE_FULL_MESSAGE', 'DANCE_ALL_FULL', 'WORKSHOP_TEXT',
            'FOREST_TEXT_1', 'FOREST_TEXT_2', 'STICKER_START_MESSAGE',
            'CAREER_MESSAGE', 'SCHEDULE_MESSAGE', 'MAP_TEXT'
        ]

        imported_count = 0
        for const_name in text_constants:
            value = getattr(text, const_name, None)
            if value is not None:
                SystemConfig.set_config(const_name, value, 'text', f'Импортировано из text.py: {const_name}')
                imported_count += 1

        # Импорт вопросов опроса
        if hasattr(text, 'SURVEY_QUESTIONS'):
            questions = text.SURVEY_QUESTIONS
            for i, question in enumerate(questions):
                SystemConfig.set_config(f'SURVEY_QUESTION_{i}', question, 'text', f'Вопрос опроса {i+1}')
                imported_count += 1

        # Принудительно обновляем кеш
        try:
            from text_cache import text_cache
            text_cache.force_update()
        except:
            pass

        flash(f'Импортировано {imported_count} текстов из text.py', 'success')
    except Exception as e:
        flash(f'Ошибка импорта: {str(e)}', 'error')

    return redirect(url_for('text_messages'))

@app.route('/toggle_survey', methods=['POST'])
@admin_required
def toggle_survey():
    """Переключение включения/отключения опроса"""
    # Получаем текущее состояние
    current_state = SystemConfig.get_config('SURVEY_ENABLED', 'true').lower() in ('true', '1', 'yes', 'on')

    # Переключаем состояние
    new_state = 'false' if current_state else 'true'
    SystemConfig.set_config('SURVEY_ENABLED', new_state, 'text', 'Включение/отключение опроса для новых пользователей')

    # Принудительно обновляем кеш
    try:
        from text_cache import text_cache
        text_cache.force_update()
    except:
        pass

    status = 'включен' if new_state == 'true' else 'отключен'
    flash(f'Опрос {status}', 'success')
    return redirect(url_for('text_messages'))

@app.route('/text_messages/import_from_quest', methods=['POST'])
@admin_required
def import_from_quest_py():
    """Импорт всех текстов квеста из quest.py"""
    try:
        import quest

        imported_count = 0

        # Импорт шагов квеста
        if hasattr(quest, 'QUEST_STEPS'):
            steps = quest.QUEST_STEPS
            for i, step in enumerate(steps):
                hint = step.get('hint', '')
                if hint:
                    SystemConfig.set_config(f'QUEST_STEP_{i}_HINT', hint, 'text', f'Подсказка квеста {i}')
                    imported_count += 1

                sticker_path = step.get('sticker_path', '')
                if sticker_path:
                    SystemConfig.set_config(f'QUEST_STEP_{i}_IMAGE', sticker_path, 'text', f'Изображение квеста {i}')
                    imported_count += 1

        # Импорт общих настроек квеста
        if hasattr(quest, 'QUEST_TOTAL_STEPS'):
            SystemConfig.set_config('QUEST_TOTAL_STEPS', quest.QUEST_TOTAL_STEPS, 'int', 'Общее количество шагов квеста')
            imported_count += 1

        # Принудительно обновляем кеш
        try:
            from text_cache import text_cache
            text_cache.force_update()
        except:
            pass

        flash(f'Импортировано {imported_count} элементов квеста из quest.py', 'success')
    except Exception as e:
        flash(f'Ошибка импорта квеста: {str(e)}', 'error')

    return redirect(url_for('text_messages'))

@app.route('/upload_image', methods=['POST'])
@admin_required
def upload_image():
    """Загрузка нового изображения"""
    import os
    from werkzeug.utils import secure_filename

    image_type = request.form.get('image_type')
    image_file = request.files.get('image_file')

    if not image_type or not image_file:
        flash('Выберите тип изображения и файл', 'error')
        return redirect(url_for('text_messages'))

    # Определяем имя файла
    file_mapping = {
        'map': 'map.png',
        'workshop': 'workshop.jpeg',
        'dance': 'dance.jpeg',
        'forest': 'forest.jpeg',
        'quest': 'quest.jpeg',
        'background': 'background.png',
        'shildik': 'shildik.png'
    }

    if image_type not in file_mapping:
        flash('Неверный тип изображения', 'error')
        return redirect(url_for('text_messages'))

    filename = file_mapping[image_type]
    file_path = os.path.join('img', filename)

    try:
        # Создаем папку img если её нет
        os.makedirs('img', exist_ok=True)

        # Сохраняем файл
        image_file.save(file_path)
        flash(f'Изображение {filename} успешно загружено', 'success')
    except Exception as e:
        flash(f'Ошибка загрузки: {str(e)}', 'error')

    return redirect(url_for('text_messages'))

@app.route('/user/<telegram_id>')
def user_detail(telegram_id):
    """View specific user details"""
    user = User.query.filter_by(telegram_id=telegram_id).first_or_404()

    registrations = Registration.query.filter_by(user_id=user.id).order_by(Registration.created_at.desc()).all()
    quest_progress = QuestProgress.query.filter_by(user_id=user.id).first()
    stickers = StickerGeneration.query.filter_by(user_id=user.id).order_by(StickerGeneration.created_at.desc()).all()
    survey_answers = db.session.query(SurveyAnswer).filter_by(user_id=user.id).order_by(SurveyAnswer.step_num).all()

    # Получаем информацию о генерации стикеров
    sticker_generation = StickerGeneration.query.filter_by(user_id=user.id).first()
    sticker_info = None
    if sticker_generation:
        # Исправляем отображение ссылки на стикерпак
        pack_url = sticker_generation.pack_url or sticker_generation.sticker_set_link
        sticker_info = {
            'template': sticker_generation.template_used or 'avito_team',
            'status': sticker_generation.status or 'Unknown',
            'pack_url': pack_url,
            'sticker_set_link': pack_url,
            'created_at': sticker_generation.created_at,
            'count': 1 if pack_url else 0  # Правильный подсчет
        }

    return render_template(
        'user_detail.html',
        user=user,
        registrations=registrations,
        quest_progress=quest_progress,
        stickers=stickers,
        survey_answers=survey_answers,
        sticker_info=sticker_info
    )

@app.route('/user/delete/<telegram_id>', methods=['POST'])
def delete_user(telegram_id):
    user = User.query.filter_by(telegram_id=telegram_id).first()
    if not user:
        return jsonify({'success': False, 'error': 'User not found'})

    # Удалить все, что связано с этим пользователем
    Registration.query.filter_by(user_id=user.id).delete()
    QuestProgress.query.filter_by(user_id=user.id).delete()
    StickerGeneration.query.filter_by(user_id=user.id).delete()
    SurveyAnswer.query.filter_by(user_id=user.id).delete()
    db.session.delete(user)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/user/reset/<telegram_id>', methods=['POST'])
def reset_user(telegram_id):
    """Сброс всех данных пользователя (кроме базовой информации)"""
    user = User.query.filter_by(telegram_id=telegram_id).first()
    if not user:
        return jsonify({'success': False, 'error': 'User not found'})

    try:
        # Удаляем все активности пользователя
        Registration.query.filter_by(user_id=user.id).delete()
        QuestProgress.query.filter_by(user_id=user.id).delete()
        StickerGeneration.query.filter_by(user_id=user.id).delete()
        SurveyAnswer.query.filter_by(user_id=user.id).delete()

        # Сбрасываем флаги пользователя
        user.survey_completed = False
        user.interested_in_vacancies = False
        user.survey_data = None
        user.full_name = None
        user.city = None
        user.professional_role = None
        user.grade = None
        user.company = None

        # Сбрасываем состояние в боте
        try:
            from bot import user_states
            if int(telegram_id) in user_states:
                del user_states[int(telegram_id)]
        except:
            pass  # Бот может быть недоступен

        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/broadcast/delete/<int:message_id>')
def delete_scheduled_message(message_id):
    msg = ScheduledMessage.query.get_or_404(message_id)
    if msg.sent:
        flash("Нельзя удалить уже отправленное сообщение", "warning")
    else:
        db.session.delete(msg)
        db.session.commit()
        flash("Сообщение удалено", "success")
    return redirect(url_for('broadcast'))

@app.route('/api/users')
@admin_required
def api_users():
    """API для получения списка пользователей"""
    users = User.query.filter_by(consent_given=True).order_by(User.first_name).all()
    return jsonify([{
        'telegram_id': user.telegram_id,
        'first_name': user.first_name,
        'last_name': user.last_name or '',
        'username': user.username or ''
    } for user in users])

@app.route('/api/stats')
@admin_required
def api_stats():
    """API для получения статистики"""
    from monitoring import BotMonitoring
    return jsonify({
        'system': BotMonitoring.get_system_stats(),
        'bot': BotMonitoring.get_bot_stats(),
        'timestamp': datetime.utcnow().isoformat()
    })

@app.route('/api/health')
def api_health():
    """Публичная проверка здоровья системы"""
    from monitoring import BotMonitoring
    checks, is_healthy = BotMonitoring.health_check()
    
    return jsonify({
        'status': 'healthy' if is_healthy else 'unhealthy',
        'checks': checks,
        'timestamp': datetime.utcnow().isoformat()
    }), 200 if is_healthy else 503

@app.route('/api/errors')
@admin_required
def api_errors():
    """Получить последние ошибки"""
    # Это можно расширить, добавив модель для логирования ошибок
    return jsonify({
        'recent_errors': [],
        'error_count_24h': SystemConfig.get_config('ERROR_COUNT_24H', 0)
    })

@app.route('/broadcast/edit/<int:message_id>', methods=['GET', 'POST'])
def edit_scheduled_message(message_id):
    msg = ScheduledMessage.query.get_or_404(message_id)

    if msg.sent:
        flash("Сообщение уже отправлено, редактирование невозможно", "warning")
        return redirect(url_for('broadcast'))

    if request.method == 'POST':
        new_text = request.form.get("message")
        new_date = request.form.get("send_date")
        new_time = request.form.get("send_time")

        try:
            new_dt = datetime.strptime(f"{new_date} {new_time}", "%Y-%m-%d %H:%M")
        except:
            flash("Неверная дата или время", "danger")
            return redirect(url_for('edit_scheduled_message', message_id=message_id))

        msg.message_text = new_text
        msg.scheduled_time = new_dt
        db.session.commit()
        flash("Сообщение обновлено", "success")
        return redirect(url_for('broadcast'))

@app.route('/backup/create', methods=['POST'])
@admin_required
def create_backup():
    """Создать резервную копию"""
    try:
        from backup import BackupManager
        backup_manager = BackupManager()
        backup_path = backup_manager.create_full_backup()
        
        return jsonify({
            'success': True, 
            'message': 'Резервная копия создана',
            'backup_path': backup_path
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/backup/list')
@admin_required
def list_backups():
    """Список резервных копий"""
    try:
        from backup import BackupManager
        backup_manager = BackupManager()
        backups = backup_manager.list_backups()
        
        return jsonify({
            'success': True,
            'backups': [{
                'name': b['name'],
                'created': b['created'].isoformat(),
                'size': 'N/A'  # Можно добавить расчет размера
            } for b in backups]
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/config/update', methods=['POST'])
@admin_required
def update_config():
    """Обновление конфигурации"""
    try:
        for key in request.form:
            if key.startswith('config_'):
                config_key = key.replace('config_', '')
                value = request.form[key].strip()

                # Обновляем переменные окружения для токенов
                if config_key == 'BOT_TOKEN' and value:
                    os.environ['BOT_TOKEN'] = value
                    SystemConfig.set_config(config_key, value, 'password', f'Токен: {config_key}')
                elif config_key == 'REPLICATE_API_TOKEN' and value:
                    os.environ['REPLICATE_API_TOKEN'] = value
                    SystemConfig.set_config(config_key, value, 'password', f'Токен: {config_key}')
                elif config_key in ['QUEST_TOTAL_STEPS', 'MAX_SLOTS_PER_DANCE']:
                    # Для числовых значений
                    SystemConfig.set_config(config_key, value, 'int', f'Настройка: {config_key}')
                elif config_key in ['SURVEY_ENABLED', 'STICKER_GENERATION_ENABLED']:
                    # Для булевых значений
                    SystemConfig.set_config(config_key, value, 'bool', f'Настройка: {config_key}')
                else:
                    # Для текстовых значений
                    SystemConfig.set_config(config_key, value, 'text', f'Настройка: {config_key}')

        db.session.commit()

        # Принудительно обновляем кеш
        try:
            from text_cache import text_cache
            text_cache.force_update()
        except:
            pass

        flash('Конфигурация обновлена успешно! Изменения токенов вступят в силу после перезапуска.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка при обновлении конфигурации: {str(e)}', 'danger')

    return redirect(url_for('config'))