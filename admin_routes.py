from flask import render_template, request, redirect, url_for, flash, jsonify, send_file, abort
from app import app, db
from models import User, Registration, QuestProgress, StickerGeneration, AdminLog, ScheduledMessage
import pandas as pd
import io
from datetime import datetime

@app.route('/')
def index():
    # Метрики
    total_users = User.query.count()
    total_vacancy = User.query.filter_by(interested_in_vacancies=True).count()
    total_quest_complete = QuestProgress.query.filter_by(completed=True).count()
    total_stickers = StickerGeneration.query.count()

    # Для квестов — последние, с пользователем
    recent_quest_winners = db.session.query(QuestProgress, User)\
        .join(User, QuestProgress.user_id == User.id)\
        .filter(QuestProgress.completed == True)\
        .order_by(QuestProgress.completed_at.desc())\
        .limit(20).all()

    # Для стикеров — последние, с пользователем
    recent_stickers = db.session.query(StickerGeneration, User)\
        .join(User, StickerGeneration.user_id == User.id)\
        .order_by(StickerGeneration.created_at.desc())\
        .limit(20).all()

    # Слоты танцев — для каждого уникального (day, time_slot) показать всех записанных
    slot_query = db.session.query(Registration.day, Registration.time_slot)\
        .filter(Registration.activity_type == 'dance')\
        .distinct().order_by(Registration.day, Registration.time_slot).all()
    dance_slots = []
    for day, time_slot in slot_query:
        regs = Registration.query.filter_by(activity_type='dance', day=day, time_slot=time_slot)\
                .order_by(Registration.created_at).all()
        users = [User.query.get(reg.user_id) for reg in regs]
        dance_slots.append({
            'day': day,
            'time_slot': time_slot,
            'users': users
        })

    return render_template(
        'admin_dashboard.html',
        total_users=total_users,
        total_vacancy=total_vacancy,
        total_quest_complete=total_quest_complete,
        total_stickers=total_stickers,
        dance_slots=dance_slots,
        recent_quest_winners=recent_quest_winners,
        recent_stickers=recent_stickers
    )

@app.route('/participants')
def participants():
    """View all participants"""
    page = request.args.get('page', 1, type=int)
    per_page = 30

    users = User.query.order_by(User.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template('participants.html', users=users)

@app.route('/export_csv')
def export_csv():
    """Export participants data as CSV"""
    users = User.query.order_by(User.created_at.desc()).all()
    data = []
    for user in users:
        data.append({
            'telegram_id': user.telegram_id,
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'company': user.company,
            'created_at': user.created_at.strftime('%Y-%m-%d %H:%M:%S')
        })

    if not data:
        flash('Нет данных для экспорта', 'warning')
        return redirect(url_for('participants'))

    df = pd.DataFrame(data)
    output = io.StringIO()
    df.to_csv(output, index=False)
    output.seek(0)
    csv_bytes = io.BytesIO()
    csv_bytes.write(output.getvalue().encode('utf-8'))
    csv_bytes.seek(0)
    filename = f"participants_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    return send_file(
        csv_bytes,
        mimetype='text/csv',
        as_attachment=True,
        download_name=filename
    )

@app.route('/broadcast', methods=['GET', 'POST'])
def broadcast():
    if request.method == 'POST':
        message = request.form.get('message')
        send_date = request.form.get('send_date')
        send_time = request.form.get('send_time')

        if not message:
            flash('Текст сообщения не может быть пустым', 'danger')
            return redirect(url_for('broadcast'))

        # Если дата и время не выбраны — отправить сразу
        if not send_date or not send_time:
            scheduled_datetime = datetime.utcnow()
        else:
            try:
                scheduled_datetime = datetime.strptime(f"{send_date} {send_time}", "%Y-%m-%d %H:%M")
            except ValueError:
                flash('Некорректный формат даты или времени', 'danger')
                return redirect(url_for('broadcast'))

        if scheduled_datetime < datetime.utcnow():
            flash('Нельзя запланировать сообщение в прошлом', 'danger')
            return redirect(url_for('broadcast'))

        new_message = ScheduledMessage(message=message, scheduled_time=scheduled_datetime)
        db.session.add(new_message)
        db.session.commit()

        flash('Сообщение добавлено в очередь на отправку', 'success')
        return redirect(url_for('broadcast'))

    scheduled_messages = ScheduledMessage.query.order_by(ScheduledMessage.scheduled_time).all()
    return render_template('broadcast.html', scheduled_messages=scheduled_messages)

@app.route('/user/<telegram_id>')
def user_detail(telegram_id):
    """View specific user details"""
    user = User.query.filter_by(telegram_id=telegram_id).first_or_404()

    registrations = Registration.query.filter_by(user_id=user.id).order_by(Registration.created_at.desc()).all()
    quest_progress = QuestProgress.query.filter_by(user_id=user.id).first()
    stickers = StickerGeneration.query.filter_by(user_id=user.id).order_by(StickerGeneration.created_at.desc()).all()

    return render_template(
        'user_detail.html',
        user=user,
        registrations=registrations,
        quest_progress=quest_progress,
        stickers=stickers
    )

# Удаление пользователя (через AJAX, POST)
@app.route('/user/delete/<telegram_id>', methods=['POST'])
def delete_user(telegram_id):
    user = User.query.filter_by(telegram_id=telegram_id).first()
    if not user:
        return jsonify({'success': False, 'error': 'User not found'})

    # Удалить все, что связано с этим пользователем
    Registration.query.filter_by(user_id=user.id).delete()
    QuestProgress.query.filter_by(user_id=user.id).delete()
    StickerGeneration.query.filter_by(user_id=user.id).delete()
    db.session.delete(user)
    db.session.commit()
    return jsonify({'success': True})

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

        msg.message = new_text
        msg.scheduled_time = new_dt
        db.session.commit()
        flash("Сообщение обновлено", "success")
        return redirect(url_for('broadcast'))

    return render_template("edit_broadcast.html", msg=msg)
