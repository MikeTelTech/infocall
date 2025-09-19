# models/user.py
import logging
import bcrypt
from utils.db import get_db_cursor

class User:
    def __init__(self, id, email, password_hash, phone_number=None, ivr_passcode_hash=None, role='user'):
        self.id = id
        self.email = email
        self.password_hash = password_hash
        self.phone_number = phone_number
        self.ivr_passcode_hash = ivr_passcode_hash
        self.role = role

    @classmethod
    def get_by_email(cls, email):
        """Fetches a user by email."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                query = "SELECT id, email, password, phone_number, ivr_passcode_hash, role FROM users WHERE email = %s"
                cursor.execute(query, (email,))
                user_data = cursor.fetchone()
                if user_data:
                    return cls(
                        id=user_data['id'],
                        email=user_data['email'],
                        password_hash=user_data['password'],
                        phone_number=user_data.get('phone_number'),
                        ivr_passcode_hash=user_data.get('ivr_passcode_hash'),
                        role=user_data.get('role')
                    )
            return None
        except Exception as e:
            logging.error(f"Error fetching user by email {email}: {e}", exc_info=True)
            return None

    @classmethod
    def create(cls, email, password, phone_number, ivr_passcode, role='user'):
        """Creates a new user account."""
        try:
            hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            hashed_ivr_passcode = bcrypt.hashpw(ivr_passcode.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

            with get_db_cursor() as (cursor, connection):
                query = """
                    INSERT INTO users (email, password, phone_number, ivr_passcode_hash, role)
                    VALUES (%s, %s, %s, %s, %s)
                """
                params = (email, hashed_password, phone_number, hashed_ivr_passcode, role)
                cursor.execute(query, params)
                connection.commit()
                return cursor.lastrowid
        except Exception as e:
            logging.error(f"Error creating user {email}: {e}", exc_info=True)
            raise # Re-raise to be handled by the route

    def check_password(self, password):
        """Checks if the provided password matches the stored hash."""
        return bcrypt.checkpw(password.encode('utf-8'), self.password_hash.encode('utf-8'))

    def check_ivr_passcode(self, passcode):
        """Checks if the provided IVR passcode matches the stored hash."""
        if not self.ivr_passcode_hash:
            return False # No IVR passcode set
        return bcrypt.checkpw(passcode.encode('utf-8'), self.ivr_passcode_hash.encode('utf-8'))