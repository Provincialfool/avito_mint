
import os
import json
import shutil
from datetime import datetime
from models import SystemConfig, User, Registration, QuestProgress, StickerGeneration
from app import db

class BackupManager:
    def __init__(self, backup_dir="backups"):
        self.backup_dir = backup_dir
        os.makedirs(backup_dir, exist_ok=True)
    
    def create_full_backup(self):
        """Создать полную резервную копию"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(self.backup_dir, f"backup_{timestamp}")
        os.makedirs(backup_path, exist_ok=True)
        
        # Копируем базу данных
        db_path = "instance/festival_bot.db"
        if os.path.exists(db_path):
            shutil.copy2(db_path, os.path.join(backup_path, "database.db"))
        
        # Экспортируем конфигурацию
        config_data = []
        configs = SystemConfig.query.all()
        for config in configs:
            config_data.append({
                'key': config.config_key,
                'value': config.config_value,
                'type': config.config_type,
                'description': config.description
            })
        
        with open(os.path.join(backup_path, "config.json"), 'w', encoding='utf-8') as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
        
        # Копируем статические файлы
        static_dirs = ["img", "static"]
        for dir_name in static_dirs:
            if os.path.exists(dir_name):
                shutil.copytree(dir_name, os.path.join(backup_path, dir_name))
        
        return backup_path
    
    def restore_from_backup(self, backup_path):
        """Восстановить из резервной копии"""
        if not os.path.exists(backup_path):
            raise ValueError("Backup path does not exist")
        
        # Восстанавливаем конфигурацию
        config_file = os.path.join(backup_path, "config.json")
        if os.path.exists(config_file):
            with open(config_file, 'r', encoding='utf-8') as f:
                configs = json.load(f)
            
            for config in configs:
                SystemConfig.set_config(
                    config['key'], 
                    config['value'], 
                    config['type'], 
                    config['description']
                )
        
        return True
    
    def list_backups(self):
        """Список доступных резервных копий"""
        backups = []
        for item in os.listdir(self.backup_dir):
            item_path = os.path.join(self.backup_dir, item)
            if os.path.isdir(item_path) and item.startswith("backup_"):
                backups.append({
                    'name': item,
                    'path': item_path,
                    'created': datetime.fromtimestamp(os.path.getctime(item_path))
                })
        return sorted(backups, key=lambda x: x['created'], reverse=True)
