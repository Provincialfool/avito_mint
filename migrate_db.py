
import sqlite3
import os
from app import app

def migrate_database():
    """Миграция базы данных для добавления недостающих колонок"""
    db_path = os.path.join(app.instance_path, 'festival_bot.db')
    
    if not os.path.exists(db_path):
        print("База данных не найдена")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Проверяем существует ли колонка photo_url в scheduled_messages
        cursor.execute("PRAGMA table_info(scheduled_messages)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'photo_url' not in columns:
            print("Добавляем колонку photo_url в scheduled_messages...")
            cursor.execute("ALTER TABLE scheduled_messages ADD COLUMN photo_url VARCHAR(255)")
            print("✅ Колонка photo_url добавлена")
        else:
            print("Колонка photo_url уже существует")
        
        # Проверяем другие потенциальные недостающие колонки
        cursor.execute("PRAGMA table_info(scheduled_messages)")
        columns = [column[1] for column in cursor.fetchall()]
        
        required_columns = ['message_text', 'chat_id']
        for col in required_columns:
            if col not in columns:
                if col == 'message_text':
                    print("Добавляем колонку message_text...")
                    cursor.execute("ALTER TABLE scheduled_messages ADD COLUMN message_text TEXT")
                elif col == 'chat_id':
                    print("Добавляем колонку chat_id...")
                    cursor.execute("ALTER TABLE scheduled_messages ADD COLUMN chat_id INTEGER")
        
        conn.commit()
        print("✅ Миграция завершена успешно")
        
    except Exception as e:
        print(f"❌ Ошибка миграции: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    with app.app_context():
        migrate_database()
