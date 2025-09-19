# models/announcement.py
import logging
from utils.db import get_db_cursor

class Announcement:
    def __init__(self, id, filename, upload_date):
        self.id = id
        self.filename = filename
        self.upload_date = upload_date

    @classmethod
    def get_all_paged(cls, per_page, offset):
        """Fetches announcements with pagination."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                query = "SELECT id, filename, upload_date FROM announcements ORDER BY upload_date DESC LIMIT %s OFFSET %s"
                cursor.execute(query, (per_page, offset))
                return cursor.fetchall()
        except Exception as e:
            logging.error(f"Error fetching paged announcements: {e}", exc_info=True)
            return []

    @classmethod
    def get_count(cls):
        """Fetches the total count of announcements."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                cursor.execute("SELECT COUNT(*) as count FROM announcements")
                result = cursor.fetchone()
                return result['count'] if result else 0
        except Exception as e:
            logging.error(f"Error fetching announcement count: {e}", exc_info=True)
            return 0

    @classmethod
    def get_by_filename(cls, filename):
        """Checks if an announcement with a given filename exists."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                cursor.execute("SELECT id, filename, upload_date FROM announcements WHERE filename = %s", (filename,))
                data = cursor.fetchone()
                if data:
                    return cls(**data)
                return None
        except Exception as e:
            logging.error(f"Error checking announcement filename {filename}: {e}", exc_info=True)
            raise

    @classmethod
    def create(cls, filename):
        """Creates a new announcement record."""
        try:
            with get_db_cursor() as (cursor, connection):
                cursor.execute("INSERT INTO announcements (filename) VALUES (%s)", (filename,))
                connection.commit()
                return cursor.lastrowid
        except Exception as e:
            logging.error(f"Error creating announcement record for {filename}: {e}", exc_info=True)
            raise

    @classmethod
    def delete(cls, filename):
        """Deletes an announcement record by filename and its associated scheduled calls."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                # First, get the ID of the announcement
                cursor.execute("SELECT id FROM announcements WHERE filename = %s", (filename,))
                announcement_data = cursor.fetchone()
                if not announcement_data:
                    logging.warning(f"Attempted to delete non-existent announcement by filename: {filename}")
                    return False

                announcement_id = announcement_data['id']

                # Now, delete associated scheduled calls first (foreign key constraint)
                logging.info(f"Deleting scheduled calls associated with announcement ID {announcement_id} (filename: {filename})")
                cursor.execute("DELETE FROM scheduled_calls WHERE announcement_id = %s", (announcement_id,))
                
                # Then delete the announcement record
                cursor.execute("DELETE FROM announcements WHERE id = %s", (announcement_id,))
                rows_affected = cursor.rowcount
                connection.commit()
                logging.info(f"Successfully deleted announcement ID {announcement_id} (filename: {filename}) and its associated calls.")
                return rows_affected > 0
        except Exception as e:
            logging.error(f"Error deleting announcement (and associated calls) by filename {filename}: {e}", exc_info=True)
            # Connection rollback is handled by DBConnectionManager context exit
            raise # Re-raise to be handled by the route

    @classmethod
    def get_all_filenames(cls):
        """Retrieves all filenames from the database mapped to their IDs."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                cursor.execute("SELECT id, filename FROM announcements")
                return {row['filename']: row['id'] for row in cursor.fetchall()}
        except Exception as e:
            logging.error(f"Error fetching all announcement filenames: {e}", exc_info=True)
            return {}

    @classmethod
    def delete_by_id(cls, announcement_id):
        """Deletes an announcement record by ID, and associated scheduled calls."""
        try:
            with get_db_cursor() as (cursor, connection):
                logging.info(f"Deleting scheduled calls associated with announcement ID {announcement_id}")
                cursor.execute("DELETE FROM scheduled_calls WHERE announcement_id = %s", (announcement_id,))
                
                logging.info(f"Deleting announcement record for ID {announcement_id}")
                cursor.execute("DELETE FROM announcements WHERE id = %s", (announcement_id,))
                rows_affected = cursor.rowcount
                connection.commit()
                logging.info(f"Successfully deleted announcement ID {announcement_id} and its associated calls. Rows affected for announcement: {rows_affected}")
                return rows_affected > 0
        except Exception as e:
            logging.error(f"Error deleting announcement by ID {announcement_id}: {e}", exc_info=True)
            raise

    @classmethod
    def get_filename_by_id(cls, announcement_id):
        """Fetches the filename for a given announcement ID."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                cursor.execute("SELECT filename FROM announcements WHERE id = %s", (announcement_id,))
                result = cursor.fetchone()
                return result['filename'] if result else None
        except Exception as e:
            logging.error(f"Error fetching filename for announcement ID {announcement_id}: {e}", exc_info=True)
            return None