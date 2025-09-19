# routes/member_routes.py
from flask import render_template, request, redirect, url_for, flash, session, send_file, jsonify
import logging
import csv
from io import StringIO, BytesIO
from werkzeug.utils import secure_filename
import os
from . import member_bp
from models.member import Member
from models.group import Group
from utils.security import login_required
from utils.validation import validate_phone_number # Assuming you create this helper

# Function to re-fetch members for rendering the directory
def _get_members_for_render():
    try:
        members_data = Member.get_all_with_groups()
        return members_data
    except Exception as e:
        logging.error(f"Error fetching members for rendering: {e}", exc_info=True)
        flash("Error loading member list.", "error")
        return []

@member_bp.route("/manage_groups", methods=["GET", "POST"])
@login_required
def manage_groups():
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "add":
                name = request.form.get("name")
                description = request.form.get("description")
                Group.add(name, description)
                flash("Group added successfully.")

            elif action == "delete":
                group_id = request.form.get("group_id")
                Group.delete(group_id)
                flash("Group deleted successfully.")

        except Exception as e:
            logging.error(f"Unexpected error managing group (action: {action}): {e}", exc_info=True)
            flash(f"An unexpected error occurred while {'adding' if action == 'add' else 'deleting'} the group.", "error")
        return redirect(url_for('member.manage_groups'))

    # GET request: Fetch groups with member counts
    groups = []
    try:
        groups = Group.get_all_with_member_count()
    except Exception as e:
        logging.error(f"Unexpected error fetching groups: {e}", exc_info=True)
        flash("An unexpected error occurred while loading groups.", "error")

    return render_template("group_management.html", groups=groups)


@member_bp.route("/member_dir", methods=["GET", "POST"])
@login_required
def member_dir():
    editing = False
    member = None
    member_groups = []
    duplicate_message = None
    all_groups = Group.get_all_simple()
    members = [] # Initialize members list

    # Handle GET request for editing a member
    if request.method == "GET" and request.args.get('action') == 'edit':
        member_id = request.args.get('member_id')
        if member_id:
            try:
                member_obj = Member.get_by_id(member_id)
                if member_obj:
                    member = member_obj.__dict__ # Convert object to dict for template if needed
                    member_groups = Member.get_member_groups(member_id)
                    editing = True
                else:
                    flash("Member not found.")
            except Exception as e:
                logging.error(f"Unexpected error fetching member for edit: {e}", exc_info=True)
                flash("An unexpected error occurred.", "error")
                return redirect(url_for("member.member_dir"))

    if request.method == "POST":
        action = request.form.get("action")
        logging.info(f"Processing member_dir POST action: {action}")
        try:
            if action == "add":
                first_name = request.form["first_name"][:35]
                last_name = request.form["last_name"][:35]
                phone_number_raw = request.form["phone_number"] # MODIFIED: No slicing
                groups_selected = request.form.getlist("groups[]")
                logging.debug(f"Add action details: FName='{first_name}', LName='{last_name}', Phone='{phone_number_raw}', Groups={groups_selected}")

                is_valid, error_message = validate_phone_number(phone_number_raw)
                if not is_valid:
                    flash(error_message, "warning")
                    return render_template(
                        "member_dir.html", members=_get_members_for_render(), all_groups=all_groups,
                        editing=False, member=None, member_groups=[],
                        duplicate_message=error_message
                    )

                clean_phone_number = ''.join(filter(str.isdigit, phone_number_raw or ""))
                if Member.exists_by_phone_number(clean_phone_number):
                    duplicate_message = f"Member with phone {clean_phone_number} already exists."
                    flash(duplicate_message, "warning")
                    logging.warning(duplicate_message)
                else:
                    Member.add(first_name, last_name, clean_phone_number, groups_selected)
                    flash("Member added successfully.")
                    return redirect(url_for('member.member_dir'))

            elif action == "edit":
                member_id = request.form["member_id"]
                first_name = request.form["first_name"][:35]
                last_name = request.form["last_name"][:35]
                phone_number_raw = request.form["phone_number"]
                groups_selected = request.form.getlist("groups[]")

                is_valid, error_message = validate_phone_number(phone_number_raw)
                if not is_valid:
                    flash(error_message, "warning")
                    return redirect(url_for("member.member_dir", action='edit', member_id=member_id))

                clean_phone_number = ''.join(filter(str.isdigit, phone_number_raw or ""))
                if Member.exists_by_phone_number(clean_phone_number, exclude_member_id=member_id):
                    flash(f"Another member with phone {clean_phone_number} already exists.", "warning")
                    return redirect(url_for('member.member_dir', action='edit', member_id=member_id))
                else:
                    Member.update(member_id, first_name, last_name, clean_phone_number, groups_selected)
                    flash("Member updated successfully.")
                return redirect(url_for('member.member_dir'))

            elif action == "delete":
                member_id = request.form["member_id"]
                logging.debug(f"Delete action: MemberID={member_id}")
                if Member.delete(member_id):
                    flash("Member deleted successfully.")
                    logging.info(f"Member {member_id} deleted.")
                else:
                    flash("Member not found or already deleted.", "warning")
                    logging.warning(f"Delete failed for member {member_id}, not found.")
                return redirect(url_for('member.member_dir'))

        except Exception as e:
             logging.error(f"Unexpected error in member_dir POST (action: {action}): {e}", exc_info=True)
             flash("An unexpected error occurred.", "error")
             return redirect(url_for('member.member_dir'))

    # Common GET or fallthrough from POST (e.g., duplicate found on add)
    members = _get_members_for_render() # Re-fetch members for display

    return render_template(
        "member_dir.html",
        members=members,
        all_groups=all_groups,
        editing=editing,
        member=member,
        member_groups=member_groups,
        duplicate_message=duplicate_message
    )

@member_bp.route("/api/update_member_status", methods=["POST"])
@login_required
def api_update_member_status():
    data = request.json
    member_id = data.get('member_id')
    status = data.get('status')
    if not member_id or not status:
        return jsonify({"success": False, "message": "Missing parameters"}), 400

    if status == 'opted_out':
        try:
            Member.update_remove_from_call_status(member_id, 1)
            logging.info(f"Member {member_id} marked as opted out via API")
            return jsonify({"success": True, "message": "Member opted out of future calls"})
        except Exception as e:
            logging.error(f"Unexpected error updating member status: {e}", exc_info=True)
            return jsonify({"success": False, "message": "An unexpected error occurred"}), 500
    else:
        return jsonify({"success": True, "message": "Status not 'opted_out', no action taken"})

@member_bp.route("/upload_csv", methods=["POST"])
@login_required
def upload_csv():
    if "file" not in request.files:
        flash("No file part", "error")
        return redirect(url_for("member.member_dir"))
    file = request.files["file"]
    if file.filename == "":
        flash("No selected file", "error")
        return redirect(url_for("member.member_dir"))

    if file and file.filename.endswith(".csv"):
        filename = secure_filename(file.filename)
        try:
            file_content = file.read().decode("utf-8")
            csv_input = StringIO(file_content)
            reader = csv.reader(csv_input)

            header = next(reader)  # Skip header
            added_count, duplicate_count, invalid_count = 0, 0, 0
            row_num = 1 # Initialize for error reporting

            from utils.db import get_db_cursor # Need to import here for specific transaction

            with get_db_cursor() as (cursor, connection):
                try:
                    for row_num, row in enumerate(reader, start=2):
                        if len(row) < 3:
                            logging.warning(f"CSV {filename} Row {row_num}: Insufficient columns.")
                            invalid_count += 1
                            continue

                        first_name = row[0][:35]
                        last_name = row[1][:35]
                        phone_number_raw = row[2]
                        groups_str = row[3] if len(row) >= 4 else ""

                        is_valid, phone_error = validate_phone_number(phone_number_raw)
                        if not is_valid:
                            logging.warning(f"CSV {filename} Row {row_num}: Invalid phone '{phone_number_raw}' ({phone_error}).")
                            invalid_count += 1
                            continue

                        clean_phone_number = ''.join(filter(str.isdigit, phone_number_raw or ""))

                        if Member.exists_by_phone_number(clean_phone_number):
                            logging.warning(f"CSV {filename} Row {row_num}: Duplicate phone '{clean_phone_number}'.")
                            duplicate_count += 1
                            continue

                        Member.add(first_name, last_name, clean_phone_number, []) # Add member first without groups
                        member_id = cursor.lastrowid # Get ID from the transaction's cursor
                        added_count += 1

                        if groups_str:
                            group_names = [g.strip() for g in groups_str.split(',') if g.strip()]
                            group_ids_to_assign = []
                            for group_name in group_names:
                                group_id = Group.get_by_name(group_name) # This will create the group if it doesn't exist
                                if group_id:
                                    group_ids_to_assign.append(group_id)
                            if group_ids_to_assign:
                                group_values = [(member_id, gid) for gid in group_ids_to_assign]
                                cursor.executemany("INSERT INTO member_groups (member_id, group_id) VALUES (%s, %s)", group_values)

                    connection.commit()
                    message_parts = []
                    if added_count > 0: message_parts.append(f"{added_count} members added")
                    if duplicate_count > 0: message_parts.append(f"{duplicate_count} duplicates skipped")
                    if invalid_count > 0: message_parts.append(f"{invalid_count} invalid records skipped")
                    if not message_parts: flash("CSV processed, but no new valid records found.", "warning")
                    else: flash(f"CSV Processed: {', '.join(message_parts)}.", "info" if added_count > 0 else "warning")

                except Exception as inner_err: # Catch errors during processing
                    connection.rollback() # Rollback changes from this CSV on error
                    logging.error(f"Error processing CSV {filename} at row {row_num}: {inner_err}", exc_info=True)
                    flash(f"An error occurred processing the CSV at row {row_num}. Changes rolled back.", "error")

        except Exception as e:
            logging.error(f"Error processing CSV file {filename}: {e}", exc_info=True)
            flash("An error occurred processing the CSV file.", "error")
    else:
        flash("Invalid file type. Please upload a CSV file.", "error")

    return redirect(url_for("member.member_dir"))

@member_bp.route("/export_csv")
@login_required
def export_csv():
    output = StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_NONNUMERIC)
    writer.writerow(["Last Name", "First Name", "Phone Number", "Groups"])
    try:
        from utils.db import get_db_cursor # Need to import here for specific transaction
        with get_db_cursor(dictionary=True) as (cursor, connection):
            query = """
                SELECT m.last_name, m.first_name, m.phone_number,
                       GROUP_CONCAT(g.name ORDER BY g.name SEPARATOR ', ') as groups
                FROM members m LEFT JOIN member_groups mg ON m.id = mg.member_id LEFT JOIN groups g ON mg.group_id = g.id
                GROUP BY m.id ORDER BY m.last_name, m.first_name
            """
            cursor.execute(query)
            for row in cursor:
                writer.writerow([row['last_name'], row['first_name'], row['phone_number'], row['groups'] or ''])
    except Exception as e:
        logging.error(f"Unexpected error during CSV export: {e}", exc_info=True)
        flash("Unexpected error during export.", "error")
        return redirect(url_for('member.member_dir'))

    output_bytes = BytesIO()
    output_bytes.write(output.getvalue().encode('utf-8'))
    output_bytes.seek(0)
    output.close()
    return send_file(output_bytes, mimetype='text/csv', download_name='member_directory.csv', as_attachment=True)

@member_bp.route("/remove_all_data", methods=["POST"])
@login_required
def remove_all_data():
    calls_del, sms_stat_del, sms_del, assoc_del, mem_del, grp_del = 0, 0, 0, 0, 0, 0
    try:
        logging.warning(f"User {session.get('user_email')} initiated complete data removal")
        from utils.db import get_db_cursor # Import locally for this critical function
        with get_db_cursor() as (cursor, connection):
            try:
                cursor.execute("SET FOREIGN_KEY_CHECKS=0")
                cursor.execute("DELETE FROM scheduled_calls"); calls_del = cursor.rowcount
                cursor.execute("DELETE FROM sms_status"); sms_stat_del = cursor.rowcount
                cursor.execute("DELETE FROM scheduled_sms"); sms_del = cursor.rowcount
                cursor.execute("DELETE FROM member_groups"); assoc_del = cursor.rowcount
                cursor.execute("DELETE FROM members"); mem_del = cursor.rowcount
                cursor.execute("DELETE FROM groups"); grp_del = cursor.rowcount
                cursor.execute("SET FOREIGN_KEY_CHECKS=1")
                connection.commit()
                logging.warning(f"DB reset complete. Deleted: {calls_del} calls, {sms_del} SMS, {mem_del} members, {grp_del} groups")
                return jsonify({"success": True, "message": f"All data removed. Deleted: {calls_del} calls, {sms_del} SMS, {mem_del} members, {grp_del} groups",
                                "stats": {"calls_deleted": calls_del, "sms_status_deleted": sms_stat_del, "sms_deleted": sms_del, "associations_deleted": assoc_del, "members_deleted": mem_del, "groups_deleted": grp_del}})
            except Exception as err:
                 logging.error(f"DB error during data removal: {err}", exc_info=True)
                 connection.rollback()
                 try: cursor.execute("SET FOREIGN_KEY_CHECKS=1")
                 except: pass
                 return jsonify({"success": False, "message": f"Database error: {str(err)}"}), 500

    except Exception as e:
        logging.error(f"Unexpected error in remove_all_data: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"An unexpected error occurred: {str(e)}"}), 500