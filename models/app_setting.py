# models/app_setting.py
import logging
from utils.db import get_db_cursor

class AppSetting:
    def __init__(self, setting_name, setting_value):
        self.setting_name = setting_name
        self.setting_value = setting_value

    @classmethod
    def get(cls, setting_name):
        """Fetches a single application setting by name."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                cursor.execute("SELECT setting_value FROM app_settings WHERE setting_name = %s", (setting_name,))
                result = cursor.fetchone()
                return result['setting_value'] if result else None
        except Exception as e:
            logging.error(f"Error fetching app setting '{setting_name}': {e}", exc_info=True)
            return None

    @classmethod
    def set(cls, setting_name, setting_value):
        """Sets or updates an application setting."""
        try:
            with get_db_cursor() as (cursor, connection):
                update_query = """
                    INSERT INTO app_settings (setting_name, setting_value)
                    VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)
                """
                cursor.execute(update_query, (setting_name, setting_value))
                connection.commit()
                return True
        except Exception as e:
            logging.error(f"Error setting app setting '{setting_name}' to '{setting_value}': {e}", exc_info=True)
            raise
    
    @classmethod
    def get_setting(cls, setting_name):
        """Alias for get() method for compatibility."""
        return cls.get(setting_name)