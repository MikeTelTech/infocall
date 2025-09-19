# models/member.py
import logging
from utils.db import get_db_cursor

class Member:
    def __init__(self, id, first_name, last_name, phone_number, remove_from_call=False):
        self.id = id
        self.first_name = first_name
        self.last_name = last_name
        self.phone_number = phone_number
        self.remove_from_call = remove_from_call

    @classmethod
    def get_all_with_groups(cls):
        """Fetches all members with their associated groups."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                query = """
                    SELECT m.id, m.last_name, m.first_name, m.phone_number, m.remove_from_call,
                           GROUP_CONCAT(g.id ORDER BY g.name SEPARATOR ',') as group_ids,
                           GROUP_CONCAT(g.name ORDER BY g.name SEPARATOR ',') as group_names
                    FROM members m
                    LEFT JOIN member_groups mg ON m.id = mg.member_id
                    LEFT JOIN groups g ON mg.group_id = g.id
                    GROUP BY m.id ORDER BY m.last_name, m.first_name
                """
                cursor.execute(query)
                members_data = cursor.fetchall()
                for member_data in members_data:
                    member_data['groups'] = []
                    if member_data.get('group_ids'):
                        group_ids = member_data['group_ids'].split(',')
                        group_names = member_data.get('group_names', '').split(',') if member_data.get('group_names') else []
                        member_data['groups'] = [{'id': gid, 'name': name} for gid, name in zip(group_ids, group_names)]
                return members_data
        except Exception as e:
            logging.error(f"Error fetching all members with groups: {e}", exc_info=True)
            return []

    @classmethod
    def get_by_id(cls, member_id):
        """Fetches a single member by ID."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                cursor.execute("SELECT id, first_name, last_name, phone_number, remove_from_call FROM members WHERE id = %s", (member_id,))
                member_data = cursor.fetchone()
                if member_data:
                    return cls(**member_data)
            return None
        except Exception as e:
            logging.error(f"Error fetching member by ID {member_id}: {e}", exc_info=True)
            return None

    @classmethod
    def get_member_groups(cls, member_id):
        """Fetches groups associated with a member."""
        try:
            with get_db_cursor(dictionary=True) as (cursor, connection):
                cursor.execute("SELECT group_id FROM member_groups WHERE member_id = %s", (member_id,))
                return [str(row['group_id']) for row in cursor.fetchall()]
        except Exception as e:
            logging.error(f"Error fetching groups for member {member_id}: {e}", exc_info=True)
            return []

    @classmethod
    def exists_by_phone_number(cls, phone_number, exclude_member_id=None):
        """Checks if a phone number already exists, optionally excluding a specific member ID."""
        try:
            with get_db_cursor() as (cursor, connection):
                if exclude_member_id:
                    cursor.execute("SELECT COUNT(*) FROM members WHERE phone_number = %s AND id != %s", (phone_number, exclude_member_id))
                else:
                    cursor.execute("SELECT COUNT(*) FROM members WHERE phone_number = %s", (phone_number,))
                return cursor.fetchone()[0] > 0
        except Exception as e:
            logging.error(f"Error checking phone number existence for {phone_number}: {e}", exc_info=True)
            raise

    @classmethod
    def add(cls, first_name, last_name, phone_number, groups_selected):
        """Adds a new member and associates them with groups."""
        try:
            with get_db_cursor() as (cursor, connection):
                query_insert = "INSERT INTO members (first_name, last_name, phone_number) VALUES (%s, %s, %s)"
                cursor.execute(query_insert, (first_name, last_name, phone_number))
                member_id = cursor.lastrowid
                if member_id and groups_selected:
                    group_values = [(member_id, group_id) for group_id in groups_selected]
                    cursor.executemany("INSERT INTO member_groups (member_id, group_id) VALUES (%s, %s)", group_values)
                connection.commit()
                return member_id
        except Exception as e:
            logging.error(f"Error adding member {phone_number}: {e}", exc_info=True)
            raise

    @classmethod
    def update(cls, member_id, first_name, last_name, phone_number, groups_selected):
        """Updates an existing member's details and group associations."""
        try:
            with get_db_cursor() as (cursor, connection):
                query_update = "UPDATE members SET first_name=%s, last_name=%s, phone_number=%s WHERE id=%s"
                cursor.execute(query_update, (first_name, last_name, phone_number, member_id))

                cursor.execute("DELETE FROM member_groups WHERE member_id = %s", (member_id,))
                if groups_selected:
                    group_values = [(member_id, group_id) for group_id in groups_selected]
                    cursor.executemany("INSERT INTO member_groups (member_id, group_id) VALUES (%s, %s)", group_values)
                connection.commit()
                return True
        except Exception as e:
            logging.error(f"Error updating member {member_id}: {e}", exc_info=True)
            raise

    @classmethod
    def delete(cls, member_id):
        """Deletes a member."""
        try:
            with get_db_cursor() as (cursor, connection):
                query_delete = "DELETE FROM members WHERE id=%s"
                cursor.execute(query_delete, (member_id,))
                rows_deleted = cursor.rowcount
                connection.commit()
                return rows_deleted > 0
        except Exception as e:
            logging.error(f"Error deleting member {member_id}: {e}", exc_info=True)
            raise

    @classmethod
    def update_remove_from_call_status(cls, member_id, status=1):
        """Updates the remove_from_call status for a member."""
        try:
            with get_db_cursor() as (cursor, connection):
                cursor.execute("UPDATE members SET remove_from_call = %s WHERE id = %s", (status, member_id))
                connection.commit()
                return True
        except Exception as e:
            logging.error(f"Error updating remove_from_call status for member {member_id}: {e}", exc_info=True)
            raise

    @classmethod
    def get_members_for_call(cls, group_filter, is_completed_call=False):
        """Fetches members eligible for a call, applying opt-out filter unless the call is completed."""
        try:
            member_params = []
            if group_filter:
                base_member_query = """
                    SELECT DISTINCT m.id, m.last_name, m.first_name, m.phone_number
                    FROM members m
                    JOIN member_groups mg ON m.id = mg.member_id
                    WHERE mg.group_id = %s
                """
                member_params.append(group_filter)
            else:
                base_member_query = """
                    SELECT m.id, m.last_name, m.first_name, m.phone_number
                    FROM members m
                """

            if not is_completed_call:
                where_clause = " WHERE (m.remove_from_call = 0 OR m.remove_from_call IS NULL)" if not group_filter else " AND (m.remove_from_call = 0 OR m.remove_from_call IS NULL)"
                member_query = base_member_query + where_clause + " ORDER BY m.last_name, m.first_name"
            else:
                member_query = base_member_query + " ORDER BY m.last_name, m.first_name"

            with get_db_cursor(dictionary=True) as (cursor, connection):
                cursor.execute(member_query, tuple(member_params))
                return cursor.fetchall()
        except Exception as e:
            logging.error(f"Error getting members for call (group_filter: {group_filter}, is_completed: {is_completed_call}): {e}", exc_info=True)
            return []

    @classmethod
    def get_members_for_sms(cls, group_filter, is_completed_sms=False):
        """Fetches members eligible for an SMS, applying opt-out filter unless the SMS is completed."""
        # This is almost identical to get_members_for_call, consider refactoring to a single private method if logic truly converges.
        try:
            member_params = []
            if group_filter:
                base_member_query = "SELECT m.id, m.phone_number FROM members m JOIN member_groups mg ON m.id = mg.member_id WHERE mg.group_id = %s"
                member_params.append(group_filter)
            else:
                base_member_query = "SELECT m.id, m.phone_number FROM members m"

            filter_clause = " AND (m.remove_from_call = 0 OR m.remove_from_call IS NULL)" if group_filter else " WHERE (m.remove_from_call = 0 OR m.remove_from_call IS NULL)"
            full_query = base_member_query + (filter_clause if not is_completed_sms else "") + " ORDER BY m.id"

            with get_db_cursor(dictionary=True) as (cursor, connection):
                cursor.execute(full_query, tuple(member_params))
                return cursor.fetchall()
        except Exception as e:
            logging.error(f"Error getting members for SMS (group_filter: {group_filter}, is_completed: {is_completed_sms}): {e}", exc_info=True)
            return []