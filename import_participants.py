
import csv
import os
from datetime import datetime
from app import app, db
from models import User

def import_participants_from_csv(csv_file_path):
    """Импорт участников из CSV файла"""
    
    if not os.path.exists(csv_file_path):
        print(f"❌ Файл {csv_file_path} не найден")
        return
    
    with app.app_context():
        imported_count = 0
        updated_count = 0
        error_count = 0
        
        print(f"📥 Начинаем импорт из {csv_file_path}")
        
        with open(csv_file_path, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            
            for row in reader:
                try:
                    telegram_id = row['telegram_id'].strip()
                    
                    if not telegram_id:
                        print(f"⚠️  Пропускаем строку без telegram_id")
                        continue
                    
                    # Проверяем, существует ли пользователь
                    existing_user = User.query.filter_by(telegram_id=telegram_id).first()
                    
                    if existing_user:
                        # Обновляем существующего пользователя
                        if row['username'].strip():
                            existing_user.username = row['username'].strip()
                        if row['first_name'].strip():
                            existing_user.first_name = row['first_name'].strip()
                        if row['last_name'].strip():
                            existing_user.last_name = row['last_name'].strip()
                        if row['company'].strip():
                            existing_user.company = row['company'].strip()
                        
                        # Парсим дату создания из CSV
                        if row['created_at'].strip():
                            try:
                                created_at = datetime.strptime(row['created_at'].strip(), '%Y-%m-%d %H:%M:%S')
                                existing_user.created_at = created_at
                            except ValueError:
                                pass
                        
                        updated_count += 1
                        print(f"🔄 Обновлен пользователь: {telegram_id} ({existing_user.first_name} {existing_user.last_name})")
                        
                    else:
                        # Создаем нового пользователя
                        new_user = User(
                            telegram_id=telegram_id,
                            username=row['username'].strip() if row['username'].strip() else None,
                            first_name=row['first_name'].strip() if row['first_name'].strip() else None,
                            last_name=row['last_name'].strip() if row['last_name'].strip() else None,
                            company=row['company'].strip() if row['company'].strip() else None,
                            consent_given=False,  # По умолчанию согласие не дано
                            survey_completed=False,
                            interested_in_vacancies=False
                        )
                        
                        # Парсим дату создания из CSV
                        if row['created_at'].strip():
                            try:
                                created_at = datetime.strptime(row['created_at'].strip(), '%Y-%m-%d %H:%M:%S')
                                new_user.created_at = created_at
                            except ValueError:
                                new_user.created_at = datetime.utcnow()
                        else:
                            new_user.created_at = datetime.utcnow()
                        
                        db.session.add(new_user)
                        imported_count += 1
                        print(f"✅ Добавлен новый пользователь: {telegram_id} ({new_user.first_name} {new_user.last_name})")
                
                except Exception as e:
                    error_count += 1
                    print(f"❌ Ошибка при обработке строки {row}: {e}")
                    continue
        
        # Сохраняем изменения
        try:
            db.session.commit()
            print(f"\n🎉 Импорт завершен!")
            print(f"📊 Статистика:")
            print(f"   • Новых пользователей: {imported_count}")
            print(f"   • Обновленных пользователей: {updated_count}")
            print(f"   • Ошибок: {error_count}")
            print(f"   • Всего обработано: {imported_count + updated_count}")
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Ошибка при сохранении в базу данных: {e}")

if __name__ == "__main__":
    # Путь к CSV файлу
    csv_file = "attached_assets/participants_20250615_073459_1749982796493.csv"
    
    print("🚀 Импорт участников из CSV")
    print("=" * 50)
    
    import_participants_from_csv(csv_file)
    
    print("\n✨ Готово!")
