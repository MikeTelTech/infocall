# models/group.py
import logging
from utils.db import get_db_cursor

class Group:
    def __init__(self, id, name, description):
        self.id = id
        self.name = name
        self.description = description

    @classmethod
    def get_all_with_member_count(cls):
        """Fetches all groups with their member counts."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                query = """
                    SELECT g.*, COUNT(mg.member_id) as member_count
                    FROM groups g
                    LEFT JOIN member_groups mg ON g.id = mg.group_id
                    GROUP BY g.id
                    ORDER BY g.name
                """
                cursor.execute(query)
                return cursor.fetchall()
        except Exception as e:
            logging.error(f"Error fetching groups with member count: {e}", exc_info=True)
            return []

    @classmethod
    def get_all_simple(cls):
        """Fetches all groups (ID and Name only)."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                cursor.execute("SELECT id, name FROM groups ORDER BY name")
                return cursor.fetchall()
        except Exception as e:
            logging.error(f"Error fetching simple group list: {e}", exc_info=True)
            return []

    @classmethod
    def add(cls, name, description):
        """Adds a new group."""
        try:
            with get_db_cursor() as (cursor, connection):
                query = "INSERT INTO groups (name, description) VALUES (%s, %s)"
                cursor.execute(query, (name, description))
                connection.commit()
                return cursor.lastrowid
        except Exception as e:
            logging.error(f"Error adding group {name}: {e}", exc_info=True)
            raise

    @classmethod
    def delete(cls, group_id):
        """Deletes a group."""
        try:
            with get_db_cursor() as (cursor, connection):
                query = "DELETE FROM groups WHERE id = %s"
                cursor.execute(query, (group_id,))
                rows_affected = cursor.rowcount
                connection.commit()
                return rows_affected > 0
        except Exception as e:
            logging.error(f"Error deleting group {group_id}: {e}", exc_info=True)
            raise

    @classmethod
    def get_by_name(cls, group_name):
        """Fetches a group by name, creating it if it doesn't exist."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                cursor.execute("SELECT id FROM groups WHERE name = %s", (group_name,))
                group_result = cursor.fetchone()
                group_id = group_result['id'] if group_result else None
                if not group_id:
                    cursor.execute("INSERT INTO groups (name) VALUES (%s)", (group_name,))
                    group_id = cursor.lastrowid
                    connection.commit() # Commit the new group creation
                return group_id
        except Exception as e:
            logging.error(f"Error getting/creating group by name {group_name}: {e}", exc_info=True)
            raise