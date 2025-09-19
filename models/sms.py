# models/sms.py
import logging
from utils.db import get_db_cursor
from datetime import datetime

class SMS:
    def __init__(self, id, message_text, scheduled_datetime, group_filter, created_by, status):
        self.id = id
        self.message_text = message_text
        self.scheduled_datetime = scheduled_datetime
        self.group_filter = group_filter
        self.created_by = created_by
        self.status = status

    @classmethod
    def get_all_scheduled(cls):
        """Fetches all scheduled SMS with associated group names."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                query = """
                    SELECT ss.id, ss.message_text, ss.scheduled_datetime,
                           COALESCE(g.name, 'all') as group_filter_name, ss.status
                    FROM scheduled_sms ss
                    LEFT JOIN groups g ON ss.group_filter = g.id
                    ORDER BY ss.scheduled_datetime DESC
                """
                cursor.execute(query)
                return cursor.fetchall()
        except Exception as e:
            logging.error(f"Error fetching all scheduled SMS: {e}", exc_info=True)
            return []

    @classmethod
    def get_by_id(cls, sms_id):
        """Fetches a single scheduled SMS by ID with associated details."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                query = """
                    SELECT ss.id, ss.message_text, ss.scheduled_datetime, ss.group_filter, ss.status,
                           COALESCE(g.name, 'all') as group_name
                    FROM scheduled_sms ss LEFT JOIN groups g ON ss.group_filter = g.id
                    WHERE ss.id = %s
                """
                cursor.execute(query, (sms_id,))
                return cursor.fetchone()
        except Exception as e:
            logging.error(f"Error fetching SMS by ID {sms_id}: {e}", exc_info=True)
            return None

    @classmethod
    def create(cls, message_text, scheduled_datetime, group_id, user_id, status='pending'):
        """Creates a new scheduled SMS record."""
        try:
            with get_db_cursor() as (cursor, connection):
                query = "INSERT INTO scheduled_sms (message_text, scheduled_datetime, group_filter, created_by, status) VALUES (%s, %s, %s, %s, %s)"
                cursor.execute(query, (message_text, scheduled_datetime, group_id, user_id, status))
                last_row_id = cursor.lastrowid
                connection.commit()
                return last_row_id
        except Exception as e:
            logging.error(f"Error creating scheduled SMS: {e}", exc_info=True)
            raise

    @classmethod
    def delete(cls, sms_id):
        """Deletes a scheduled SMS by ID."""
        try:
            with get_db_cursor() as (cursor, connection):
                delete_query = "DELETE FROM scheduled_sms WHERE id = %s"
                cursor.execute(delete_query, (sms_id,))
                rows_affected = cursor.rowcount
                connection.commit()
                return rows_affected > 0
        except Exception as e:
            logging.error(f"Error deleting scheduled SMS {sms_id}: {e}", exc_info=True)
            raise

    @classmethod
    def update_status(cls, sms_id, status, details=None):
        """Updates the status of a scheduled SMS campaign."""
        try:
            with get_db_cursor() as (cursor, connection):
                if details:
                    cursor.execute("UPDATE scheduled_sms SET status = %s, details = %s WHERE id = %s", (status, details, sms_id))
                else:
                    cursor.execute("UPDATE scheduled_sms SET status = %s WHERE id = %s", (status, sms_id))
                rows_updated = cursor.rowcount
                connection.commit()
                return rows_updated > 0
        except Exception as e:
            logging.error(f"Error updating status for SMS {sms_id} to {status}: {e}", exc_info=True)
            raise

    @classmethod
    def get_pending_sms_for_scheduling(cls, now):
        """Fetches pending SMS whose scheduled_datetime has passed."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                query = "SELECT id, message_text, group_filter FROM scheduled_sms WHERE scheduled_datetime <= %s AND status = 'pending'"
                cursor.execute(query, (now,))
                return cursor.fetchall()
        except Exception as e:
            logging.error(f"Error fetching pending SMS for scheduling: {e}", exc_info=True)
            return []

class SMSSessionStatus:
    def __init__(self, id, scheduled_sms_id, member_id, phone_number, status, details, twilio_sid=None, created_at=None, updated_at=None):
        self.id = id
        self.scheduled_sms_id = scheduled_sms_id
        self.member_id = member_id
        self.phone_number = phone_number
        self.status = status
        self.details = details
        self.twilio_sid = twilio_sid
        self.created_at = created_at
        self.updated_at = updated_at

    @classmethod
    def create_or_update(cls, scheduled_sms_id, member_id, phone_number, status, details, twilio_sid=None):
        """Inserts or updates the status of an individual SMS send."""
        try:
            with get_db_cursor() as (cursor, connection):
                if twilio_sid:
                    query = """
                        INSERT INTO sms_status (scheduled_sms_id, member_id, phone_number, status, details, twilio_sid)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE status=%s, details=%s, updated_at=NOW()
                    """
                    cursor.execute(query, (scheduled_sms_id, member_id, phone_number, status, details, twilio_sid, status, details))
                else:
                    # For cases where Twilio SID might not be immediately available or for initial 'pending' status
                    query = """
                        INSERT INTO sms_status (scheduled_sms_id, member_id, phone_number, status, details)
                        VALUES (%s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE status=%s, details=%s, updated_at=NOW()
                    """
                    cursor.execute(query, (scheduled_sms_id, member_id, phone_number, status, details, status, details))
                connection.commit()
                return cursor.lastrowid
        except Exception as e:
            logging.error(f"Error creating/updating SMS status for {phone_number} (SMS ID: {scheduled_sms_id}): {e}", exc_info=True)
            raise

    @classmethod
    def get_by_twilio_sid(cls, twilio_sid):
        """Fetches SMS status details by Twilio SID."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                query = "SELECT scheduled_sms_id, member_id, phone_number FROM sms_status WHERE twilio_sid = %s LIMIT 1"
                cursor.execute(query, (twilio_sid,))
                return cursor.fetchone()
        except Exception as e:
            logging.error(f"Error fetching SMS status by Twilio SID {twilio_sid}: {e}", exc_info=True)
            return None

    @classmethod
    def update_status_by_twilio_sid(cls, twilio_sid, status, details):
        """Updates the status of an SMS based on its Twilio SID."""
        try:
            with get_db_cursor() as (cursor, connection):
                update_query = "UPDATE sms_status SET status = %s, details = %s, updated_at = NOW() WHERE twilio_sid = %s"
                cursor.execute(update_query, (status, details, twilio_sid))
                connection.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logging.error(f"Error updating SMS status by Twilio SID {twilio_sid}: {e}", exc_info=True)
            raise

    @classmethod
    def update_pending_to_failed(cls, sms_id):
        """Marks all pending SMS statuses for a given SMS campaign as failed."""
        try:
            with get_db_cursor() as (cursor, connection):
                cursor.execute("UPDATE sms_status SET status = 'failed', details = 'Aborted by admin' WHERE scheduled_sms_id = %s AND status = 'pending'", (sms_id,))
                rows_affected = cursor.rowcount
                connection.commit()
                return rows_affected
        except Exception as e:
            logging.error(f"Error updating pending SMS status to failed for SMS {sms_id}: {e}", exc_info=True)
            raise