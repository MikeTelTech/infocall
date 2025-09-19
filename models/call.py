# models/call.py
import logging
from utils.db import get_db_cursor
from datetime import datetime

class Call:
    def __init__(self, id, announcement_id, scheduled_datetime, group_filter, created_by, caller_id_name, status, details=None):
        self.id = id
        self.announcement_id = announcement_id
        self.scheduled_datetime = scheduled_datetime
        self.group_filter = group_filter
        self.created_by = created_by
        self.caller_id_name = caller_id_name
        self.status = status
        self.details = details

    @classmethod
    def get_all_scheduled(cls):
        """Fetches all scheduled calls with associated announcement and group names."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                query = """
                    SELECT sc.id, a.filename, sc.scheduled_datetime, sc.caller_id_name,
                           COALESCE(g.name, 'all') as group_filter_name, sc.status
                    FROM scheduled_calls sc
                    JOIN announcements a ON sc.announcement_id = a.id
                    LEFT JOIN groups g ON sc.group_filter = g.id
                    ORDER BY sc.scheduled_datetime DESC
                """
                cursor.execute(query)
                return cursor.fetchall()
        except Exception as e:
            logging.error(f"Error fetching all scheduled calls: {e}", exc_info=True)
            return []

    @classmethod
    def get_by_id(cls, call_id):
        """Fetches a single scheduled call by ID with associated details."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                query = """
                    SELECT sc.id, a.id as announcement_id, a.filename,
                           sc.scheduled_datetime, sc.group_filter, sc.caller_id_name, sc.status,
                           COALESCE(g.name, 'all') as group_name
                    FROM scheduled_calls sc
                    JOIN announcements a ON sc.announcement_id = a.id
                    LEFT JOIN groups g ON sc.group_filter = g.id
                    WHERE sc.id = %s
                """
                cursor.execute(query, (call_id,))
                return cursor.fetchone()
        except Exception as e:
            logging.error(f"Error fetching call by ID {call_id}: {e}", exc_info=True)
            return None

    @classmethod
    def create(cls, announcement_id, scheduled_dt_utc, group_id, user_id, caller_id_name, status='pending'):
        """Creates a new scheduled call record."""
        try:
            with get_db_cursor() as (cursor, connection):
                sql_query = """
                    INSERT INTO scheduled_calls (announcement_id, scheduled_datetime, group_filter, created_by, caller_id_name, status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """
                sql_params = (announcement_id, scheduled_dt_utc, group_id, user_id, caller_id_name, status)
                cursor.execute(sql_query, sql_params)
                last_row_id = cursor.lastrowid
                connection.commit()
                return last_row_id
        except Exception as e:
            logging.error(f"Error creating scheduled call for announcement {announcement_id}: {e}", exc_info=True)
            raise

    @classmethod
    def delete(cls, call_id):
        """Deletes a scheduled call by ID."""
        try:
            with get_db_cursor() as (cursor, connection):
                cursor.execute("DELETE FROM scheduled_calls WHERE id = %s", (call_id,))
                rows_affected = cursor.rowcount
                connection.commit()
                return rows_affected > 0
        except Exception as e:
            logging.error(f"Error deleting scheduled call {call_id}: {e}", exc_info=True)
            raise

    @classmethod
    def update_status(cls, call_id, status, details=None):
        """Updates the status of a scheduled call."""
        try:
            with get_db_cursor() as (cursor, connection):
                if details:
                    cursor.execute("UPDATE scheduled_calls SET status = %s, details = %s WHERE id = %s", (status, details, call_id))
                else:
                    cursor.execute("UPDATE scheduled_calls SET status = %s WHERE id = %s", (status, call_id))
                rows_updated = cursor.rowcount
                connection.commit()
                return rows_updated > 0
        except Exception as e:
            logging.error(f"Error updating status for call {call_id} to {status}: {e}", exc_info=True)
            raise

    @classmethod
    def get_pending_calls_for_scheduling(cls, now_utc):
        """Fetches pending calls whose scheduled_datetime (UTC) has passed."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                query = """
                    SELECT id, announcement_id, group_filter, caller_id_name
                    FROM scheduled_calls
                    WHERE scheduled_datetime <= %s AND status = 'pending'
                    ORDER BY scheduled_datetime ASC
                """
                cursor.execute(query, (now_utc,))
                return cursor.fetchall()
        except Exception as e:
            logging.error(f"Error fetching pending calls for scheduling: {e}", exc_info=True)
            return []

    @classmethod
    def get_active_campaign_ids(cls, campaign_ids):
        """Fetches active campaign IDs from the database."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                format_strings = ','.join(['%s'] * len(campaign_ids))
                cursor.execute(f"SELECT id, status FROM scheduled_calls WHERE id IN ({format_strings})", tuple(campaign_ids))
                return {str(row['id']) for row in cursor.fetchall() if row['status'] in ['in_progress', 'ready']}
        except Exception as e:
            logging.error(f"Error fetching active DB campaigns: {e}", exc_info=True)
            return set()