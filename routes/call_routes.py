import os
import subprocess
import math
from datetime import datetime, timedelta
from flask import render_template, request, redirect, url_for, flash, session, jsonify, abort
from werkzeug.utils import secure_filename
import logging
import uuid
import time # Added for sleep in retry logic
from pydub import AudioSegment

from . import call_bp
from models.announcement import Announcement
from models.group import Group
from models.call import Call
from models.member import Member
# MODIFIED: ami_client_instance is now initialized globally in app.py,
# and we will rely on its ensure_connected method.
# REMOVED is_call_complete and run_asterisk_command from direct import.
# run_asterisk_command will be accessed via the services.asterisk_service module.
from services.asterisk_service import ami_client_instance, update_call_status 
# Import the entire asterisk_service module to access run_asterisk_command
import services.asterisk_service as asterisk_service_module # Renamed for clarity
from utils.security import login_required
from utils.validation import validate_caller_id_name

# Corrected Imports to resolve circular dependency
from app_state import active_calls, active_calls_lock, USER_LOCAL_TIMEZONE, UTC_TIMEZONE # type: ignore
from config import MAX_CONCURRENT_CALLS # type: ignore


@call_bp.route("/ann_upload", methods=["GET", "POST"])
@login_required
def ann_upload():
    page = request.args.get('page', 1, type=int)
    per_page = 10
    upload_dir = '/var/www/html/infocall/uploads'
    temp_dir = os.path.join(upload_dir, 'temp')

    try:
        os.makedirs(temp_dir, exist_ok=True)
        os.makedirs(upload_dir, exist_ok=True)
    except OSError as e:
        logging.error(f"Error creating directories: {e}")
        flash("Server configuration error: Cannot create upload directories.", "error")
        return redirect(url_for('auth.main_menu'))

    if request.method == "POST":
        if "file" not in request.files:
            flash("No file part", "error")
            return redirect(url_for("call.ann_upload", page=page))

        uploaded_file = request.files['file']
        if uploaded_file.filename == '':
            flash("No file selected", "error")
            return redirect(url_for("call.ann_upload", page=page))

        from utils.file_utils import allowed_file, is_audio_file
        if uploaded_file and allowed_file(uploaded_file.filename):
            original_filename = secure_filename(uploaded_file.filename)
            temp_file_path = os.path.join(temp_dir, original_filename)

            unique_prefix = str(uuid.uuid4())[:8]
            base_filename, _ = os.path.splitext(original_filename)
            final_filename_wav = f"{unique_prefix}_{base_filename}.wav"
            final_file_path = os.path.join(upload_dir, final_filename_wav)

            try:
                uploaded_file.save(temp_file_path)
                logging.info(f"Starting ffmpeg transcoding: {temp_file_path} -> {final_file_path}")
                ffmpeg_command = [
                    'ffmpeg',
                    '-i', temp_file_path,
                    '-ar', '8000',
                    '-ac', '1',
                    '-acodec', 'pcm_s16le',
                    final_file_path
                ]

                process = subprocess.run(ffmpeg_command, check=True, capture_output=True, text=True)
                logging.info(f"ffmpeg output for {original_filename}:\nSTDOUT: {process.stdout}\nSTDERR: {process.stderr}")
                logging.info(f"Transcoding successful for {original_filename} to {final_filename_wav}")

                try:
                    logging.info(f"Adding 3 seconds of leading silence to {final_file_path}")
                    audio = AudioSegment.from_wav(final_file_path) # This should be 8000Hz from ffmpeg

                    # Ensure silence is also 8000Hz (or matches the audio's frame rate)
                    # audio.frame_rate should be 8000 here
                    three_seconds_of_silence = AudioSegment.silent(duration=3000, frame_rate=audio.frame_rate)
                    
                    audio_with_silence = three_seconds_of_silence + audio

                    # Export, explicitly setting parameters to ensure the frame rate.
                    # Parameters like "-ar 8000" ensure ffmpeg (used by pydub for export) uses the correct sample rate.
                    audio_with_silence.export(final_file_path, format="wav", parameters=["-ar", str(audio.frame_rate)])
                    
                    logging.info(f"Successfully added 3s silence to {final_filename_wav} at {audio.frame_rate}Hz")
                except Exception as audio_processing_err:
                    logging.error(f"Error adding silence to {final_filename_wav} using pydub: {audio_processing_err}", exc_info=True)
                    flash(f"File '{original_filename}' converted, but failed to add leading silence. Audio will play immediately.", "warning")

                Announcement.create(final_filename_wav)
                flash(f"File '{original_filename}' uploaded, converted, and silence added successfully.", "success")
                logging.info(f"DB record created for {final_filename_wav} by user {session.get('user_email')}")

            except subprocess.CalledProcessError as e:
                logging.error(f"ffmpeg transcoding failed for {original_filename}: {e}", exc_info=True)
                logging.error(f"ffmpeg failed output:\nSTDOUT: {e.stdout}\nSTDERR: {e.stderr}")
                flash(f"Failed to transcode '{original_filename}'. Check logs for details. Ensure ffmpeg is installed and the file format is supported.", "error")
            except ValueError as ve:
                 if "Duplicate target filename" not in str(ve):
                     flash(f"Error processing '{original_filename}': {str(ve)}", "error")
                 logging.error(f"ValueError processing {original_filename}: {str(ve)}")
            except Exception as e:
                logging.error(f"Unexpected error processing file {original_filename}: {e}", exc_info=True)
                flash("An unexpected error occurred during file processing.", "error")
            finally:
                try:
                    if os.path.exists(temp_file_path):
                        os.remove(temp_file_path)
                        logging.info(f"Temporary file deleted: {temp_file_path}")
                except OSError as del_err:
                    logging.error(f"Error deleting temporary file {temp_file_path}: {del_err}")

            return redirect(url_for('call.ann_upload', page=page))
        else:
            flash("Invalid file type. Allowed types: wav, mp3, m4a, aac.", "error")
            return redirect(url_for("call.ann_upload", page=page))

    paged_announcements = []
    total_announcements = 0
    total_pages = 1
    next_page = False
    prev_page = False
    page = max(1, page)

    try:
        total_announcements = Announcement.get_count()
        logging.info(f"Found {total_announcements} total announcements in database (GET request)")

        if total_announcements > 0:
            total_pages = math.ceil(total_announcements / per_page)
            page = max(1, min(page, total_pages))
            next_page = page < total_pages
            prev_page = page > 1
            start = (page - 1) * per_page
            paged_announcements = Announcement.get_all_paged(per_page, start)
            logging.info(f"Fetched {len(paged_announcements)} announcements for page {page}/{total_pages}")
            for ann in paged_announcements:
                if 'upload_date' in ann and ann['upload_date']:
                    ann['upload_date_str'] = ann['upload_date'].strftime('%Y-%m-%d %H:%M:%S')
        else:
            page = 1
            total_pages = 1
            next_page = False
            prev_page = False
            paged_announcements = []
    except Exception as e:
        logging.error(f"Unexpected error loading announcements (GET): {e}", exc_info=True)
        flash("An unexpected error occurred loading announcements.", "error")
        paged_announcements = []
        page = 1
        total_pages = 1
        next_page = False
        prev_page = False

    return render_template(
        "ann_upload.html",
        announcements=paged_announcements,
        page=page,
        total_pages=total_pages,
        next_page=next_page,
        prev_page=prev_page
    )

@call_bp.route('/delete/<filename>')
@login_required
def delete_file(filename):
    safe_filename = secure_filename(filename)
    if safe_filename != filename:
        logging.error(f"Attempted delete with potentially unsafe filename: {filename}")
        flash("Invalid filename for deletion.", "error")
        return redirect(url_for('call.ann_upload'))

    file_path = os.path.join('/var/www/html/infocall/uploads', safe_filename)
    logging.info(f"Attempting to delete file: {file_path}")

    file_existed_on_fs = False
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            file_existed_on_fs = True
            logging.info(f"File {safe_filename} deleted from filesystem.")
        except OSError as e:
            logging.error(f"OS error deleting file {safe_filename}: {e}")
            flash(f"Error deleting file from system: {e}", "error")
    else:
        logging.warning(f"Attempted delete non-existent file: {file_path}")

    try:
        # The Announcement.delete method now handles associated calls
        if Announcement.delete(safe_filename):
            flash(f"Announcement '{safe_filename}' and any associated scheduled calls deleted successfully.", "success")
            logging.info(f"Announcement {safe_filename} and associated calls deleted by user {session.get('user_email')}")
        elif not file_existed_on_fs:
            flash(f"Announcement '{safe_filename}' not found in database or on filesystem.", "warning")
            logging.warning(f"Delete attempted for non-existent announcement and file: {safe_filename}")
        else: # DB record not found, BUT file WAS on FS (and now deleted)
            flash(f"File '{safe_filename}' deleted from system, but no matching database record was found.", "warning")
            logging.info(f"File {safe_filename} deleted from system, but DB record was already gone.")
    except Exception as e:
        logging.error(f"Unexpected error during announcement deletion for {safe_filename}: {e}", exc_info=True)
        flash(f"An unexpected error occurred during announcement deletion: {str(e)}", "error")

    return redirect(url_for('call.ann_upload', page=request.args.get('page', 1)))


@call_bp.route("/sync_announcements")
@login_required
def sync_announcements():
    deleted_records = 0
    added_records = 0
    upload_dir = '/var/www/html/infocall/uploads'

    try:
        db_announcements_dict = Announcement.get_all_filenames()
        db_filenames = set(db_announcements_dict.keys())

        actual_files = set()
        if os.path.isdir(upload_dir):
             from utils.file_utils import allowed_file
             for f in os.listdir(upload_dir):
                 file_path = os.path.join(upload_dir, f)
                 if os.path.isfile(file_path) and allowed_file(f):
                     actual_files.add(f)
        else:
             logging.error(f"Upload directory not found: {upload_dir}")
             flash(f"Error: Upload directory not found. Cannot sync.", "error")
             return redirect(url_for('call.ann_upload'))

        files_to_delete_from_db = db_filenames - actual_files
        if files_to_delete_from_db:
            logging.warning(f"Found orphaned DB records (no matching file): {files_to_delete_from_db}")
            for filename in files_to_delete_from_db:
                announcement_id_to_delete = db_announcements_dict.get(filename)
                if announcement_id_to_delete:
                    try:
                        if Announcement.delete_by_id(announcement_id_to_delete): # delete_by_id also handles scheduled_calls
                            deleted_records += 1
                            logging.info(f"Deleted orphaned DB record and associated calls: {filename} (ID: {announcement_id_to_delete})")
                        else:
                            logging.error(f"Error deleting orphaned DB record {filename} (ID: {announcement_id_to_delete}) from database (delete_by_id returned False).")
                    except Exception as del_err:
                        logging.error(f"Exception deleting orphaned DB record {filename} (ID: {announcement_id_to_delete}): {del_err}", exc_info=True)
                else:
                    logging.error(f"Could not find ID for orphaned filename {filename} for deletion.")

        files_to_add_to_db = actual_files - db_filenames
        if files_to_add_to_db:
            logging.info(f"Found files on disk not in DB: {files_to_add_to_db}")
            for filename in files_to_add_to_db:
                try:
                    if Announcement.create(filename):
                        added_records += 1
                        logging.info(f"Added missing DB record for file: {filename}")
                    else:
                        logging.error(f"DB record creation returned falsy for {filename} without raising error.")
                except Exception as create_err:
                    logging.error(f"Error adding DB record for file {filename}: {create_err}")

        flash_messages = []
        if deleted_records > 0:
            flash_messages.append(f"Records removed from DB: {deleted_records}")
        if added_records > 0:
            flash_messages.append(f"Records added to DB: {added_records}")

        if not flash_messages:
            flash("Announcements are already synchronized. No changes made.", "info")
        else:
            flash(f"Announcements synchronized. {'. '.join(flash_messages)}.", "success")

        logging.info(f"Sync complete. Removed from DB: {deleted_records}, Added to DB: {added_records}")

    except Exception as e:
        logging.error(f"Error synchronizing announcements: {e}", exc_info=True)
        flash(f"Error synchronizing announcements: {str(e)}", "error")

    return redirect(url_for('call.ann_upload'))

@call_bp.route("/call_mem", methods=["GET", "POST"])
@login_required
def call_mem():
    if request.method != "POST":
        announcements = []
        groups = []
        try:
            announcements = Announcement.get_all_paged(1000, 0)
            groups = Group.get_all_simple()
            logging.debug("Fetched data for call scheduling form.")
        except Exception as e:
            logging.error(f"Error fetching data for call_mem GET: {e}", exc_info=True)
            flash("An error occurred loading form data.", "error")
            announcements = []
            groups = []

        today_local = datetime.now(USER_LOCAL_TIMEZONE).strftime("%Y-%m-%d")
        return render_template("call_mem.html", announcements=announcements, groups=groups, today=today_local)

    announcement_id = request.form.get("announcement_id")
    scheduled_date = request.form.get("scheduled_date")
    scheduled_time = request.form.get("scheduled_time")
    group = request.form.get("group", "all")
    caller_id_name_raw = request.form.get("caller_id_name", "InfoCall")
    user_id = session.get("user_id")

    logging.info(f"Scheduling call POST: AnnounceID={announcement_id}, Date={scheduled_date}, Time={scheduled_time}, Group={group}, User={user_id}, CID Name Raw='{caller_id_name_raw}'")
    caller_id_name = validate_caller_id_name(caller_id_name_raw)
    logging.info(f"Validated Caller ID Name: '{caller_id_name}'")

    def fetch_form_data_for_error_render():
        announcements_form, groups_form = [], []
        try:
            announcements_form = Announcement.get_all_paged(1000, 0)
            groups_form = Group.get_all_simple()
        except Exception as e_fetch:
            logging.error(f"Error re-fetching form data after validation error: {e_fetch}")
        today_local_err = datetime.now(USER_LOCAL_TIMEZONE).strftime("%Y-%m-%d")
        return announcements_form, groups_form, today_local_err

    if not all([announcement_id, scheduled_date, scheduled_time, user_id]):
        flash("Missing required fields (announcement, date, time).", "warning")
        logging.warning("Call schedule failed: Missing required fields.")
        ann, grp, today_err = fetch_form_data_for_error_render()
        return render_template("call_mem.html", announcements=ann, groups=grp, today=today_err)

    try: # This try block was causing the SyntaxError due to improper closure
        scheduled_datetime_str = f"{scheduled_date} {scheduled_time}"
        naive_datetime_obj = datetime.strptime(scheduled_datetime_str, "%Y-%m-%d %H:%M")

        # Corrected Timezone Localization for zoneinfo:
        # Assume naive_datetime_obj is in the user's local time, make it aware in that zone.
        local_dt_aware = naive_datetime_obj.replace(tzinfo=USER_LOCAL_TIMEZONE) # CRUCIAL FIX

        now_utc = datetime.now(UTC_TIMEZONE)
        scheduled_dt_utc = local_dt_aware.astimezone(UTC_TIMEZONE)

        logging.info(f"Scheduling check: User Input Local Time={local_dt_aware}, Scheduled UTC Time={scheduled_dt_utc}, Current UTC Time={now_utc}")

        if scheduled_dt_utc < (now_utc - timedelta(seconds=1)):
            flash("Cannot schedule calls in the past.", "warning")
            logging.warning(f"Call schedule failed: Time in past. Scheduled UTC: {scheduled_dt_utc}, Now UTC: {now_utc}")
            ann, grp, today_err = fetch_form_data_for_error_render()
            return render_template("call_mem.html", announcements=ann, groups=grp, today=today_err)

        group_id = group if group != "all" else None
        last_row_id = Call.create(announcement_id, scheduled_dt_utc, group_id, user_id, caller_id_name)
        logging.info(f"Scheduled call inserted with ID: {last_row_id} for UTC time {scheduled_dt_utc}")

        # Format local time for display in flash message
        flash(f"Call scheduled successfully for {local_dt_aware.strftime('%Y-%m-%d %I:%M %p %Z')} (ID: {last_row_id}). Status: PENDING.", "success")
        return redirect(url_for("call.view_scheduled_calls"))

    except ValueError as ve:
        logging.error(f"Invalid date/time format submitted: '{scheduled_datetime_str}' - {ve}")
        flash("Invalid date or time format selected. Please use YYYY-MM-DD and HH:MM.", "error")
        ann, grp, today_err = fetch_form_data_for_error_render()
        return render_template("call_mem.html", announcements=ann, groups=grp, today=today_err)
    except Exception as e:
        logging.error(f"Unexpected error scheduling call: {e}", exc_info=True)
        flash("An unexpected error occurred while scheduling the call.", "error")
        return redirect(url_for("call.call_mem"))

@call_bp.route("/view_scheduled_calls")
@login_required
def view_scheduled_calls():
    scheduled_calls = []
    try:
        scheduled_calls_raw = Call.get_all_scheduled()
        for call_data in scheduled_calls_raw:
            db_datetime_utc = call_data.get('scheduled_datetime')
            if isinstance(db_datetime_utc, datetime):
                # Ensure it's treated as UTC from DB and convert to local for display
                if db_datetime_utc.tzinfo is None:
                    # If naive, assume it's UTC from DB and make it aware
                    db_datetime_utc = db_datetime_utc.replace(tzinfo=UTC_TIMEZONE)
                # Now convert to local timezone for display
                dt_local = db_datetime_utc.astimezone(USER_LOCAL_TIMEZONE)
                call_data['formatted_datetime'] = dt_local.strftime('%Y-%m-%d %I:%M %p %Z')
            elif isinstance(db_datetime_utc, str):
                try:
                    # Attempt to parse string from DB, assume UTC if naive, then convert
                    naive_dt = datetime.fromisoformat(db_datetime_utc)
                    aware_utc_dt = naive_dt.replace(tzinfo=UTC_TIMEZONE)
                    dt_local = aware_utc_dt.astimezone(USER_LOCAL_TIMEZONE)
                    call_data['formatted_datetime'] = dt_local.strftime('%Y-%m-%d %I:%M %p %Z')
                except ValueError:
                    call_data['formatted_datetime'] = db_datetime_utc + " (Unparseable UTC string)"
                    logging.warning(f"Could not parse string datetime for call {call_data.get('id')}: {db_datetime_utc}")
            else:
                call_data['formatted_datetime'] = 'N/A'
                logging.warning(f"Unexpected type or missing scheduled_datetime for call {call_data.get('id')}: {db_datetime_utc}")

            call_data['group_filter'] = call_data.get('group_filter_name', 'all')
            scheduled_calls.append(call_data)
    except Exception as e:
        logging.error(f"Unexpected error fetching scheduled calls: {e}", exc_info=True)
        flash("An unexpected error occurred while loading calls.", "error")
        scheduled_calls = []
    return render_template("view_scheduled_calls.html", scheduled_calls=scheduled_calls)

@call_bp.route("/remove_scheduled_call/<int:call_id>")
@login_required
def remove_scheduled_call(call_id):
    try:
        call_info = Call.get_by_id(call_id)
        if not call_info:
            flash("Call not found or already removed.", "warning")
            return redirect(url_for("call.view_scheduled_calls"))
        call_status = call_info.get('status')
        if call_status == 'in_progress':
            flash("Cannot remove calls currently in progress. Please abort the call first if needed.", "warning")
            return redirect(url_for("call.view_scheduled_calls"))
        if Call.delete(call_id):
            flash(f"Scheduled call (ID: {call_id}) has been removed successfully.", "success")
            logging.info(f"Removed scheduled call ID {call_id} by user {session.get('user_email')}")
        else:
            flash(f"Call record (ID: {call_id}) could not be removed (possibly already deleted).", "warning")
            logging.warning(f"Attempted to remove call {call_id}, but no rows affected in DB (user: {session.get('user_email')}).")
    except Exception as e:
        logging.error(f"Error removing scheduled call {call_id}: {e}", exc_info=True)
        flash(f"An unexpected error occurred while trying to remove the call.", "error")
    return redirect(url_for("call.view_scheduled_calls"))

@call_bp.route("/execute_call/<int:call_id>")
@login_required
def execute_call(call_id):
    call_info = None
    members = []
    call_stats = {'total': 0, 'called': 0, 'completed': 0, 'opted_out': 0, 'answered': 0, 'pending': 0, 'progress_percent': 0}
    is_completed_or_cancelled = False

    try:
        call_info = Call.get_by_id(call_id)
        if not call_info:
            flash("Scheduled call not found.", "warning")
            return redirect(url_for("call.view_scheduled_calls"))

        logging.info(f"Call info retrieved for campaign {call_id}: {call_info}")
        is_completed_or_cancelled = call_info.get('status') in ['completed', 'cancelled']
        members = Member.get_members_for_call(call_info.get('group_filter'), is_completed_call=is_completed_or_cancelled)
        logging.info(f"Members found for campaign {call_id} (is_completed_or_cancelled={is_completed_or_cancelled}): {len(members)}")

        if call_info.get('status') == 'ready':
            if Call.update_status(call_id, 'in_progress', 'Execution page viewed'):
                call_info['status'] = 'in_progress'
                logging.info(f"Updated call {call_id} status to 'in_progress' in DB.")
            else:
                logging.error(f"Failed to update call {call_id} status. It might have been processed by another instance.", "error")

        # Format scheduled_datetime for display
        if 'scheduled_datetime' in call_info and isinstance(call_info['scheduled_datetime'], datetime):
            if call_info['scheduled_datetime'].tzinfo is None:
                # Assume it's UTC if naive from DB
                call_info['scheduled_datetime'] = call_info['scheduled_datetime'].replace(tzinfo=UTC_TIMEZONE)
            call_info['formatted_scheduled_datetime'] = call_info['scheduled_datetime'].astimezone(USER_LOCAL_TIMEZONE).strftime('%Y-%m-%d %I:%M %p %Z')
        else:
            call_info['formatted_scheduled_datetime'] = 'N/A'


        if not members:
            flash("No eligible members found for this call campaign.", "warning")

        total_members = len(members)
        called_count = 0
        completed_count = 0
        opted_out_count = 0
        answered_count = 0
        
        campaign_calls_data = active_calls.get(str(call_id), {})
        for member in members:
            phone = member['phone_number']
            status_data = campaign_calls_data.get(phone, {})
            member['call_status'] = status_data.get('status', 'pending')
            member['call_details'] = status_data.get('details', '')
            
            # Format timestamp for display only here
            timestamp_utc = status_data.get('timestamp')
            if isinstance(timestamp_utc, datetime):
                member['call_timestamp'] = timestamp_utc.astimezone(USER_LOCAL_TIMEZONE).strftime('%I:%M:%S %p')
            else:
                member['call_timestamp'] = '-' # Or original string if conversion fails


            current_member_status = member['call_status']
            if current_member_status and current_member_status not in ['pending', 'waiting', 'unknown']:
                called_count +=1
                if current_member_status == 'completed': completed_count +=1
                elif current_member_status == 'opted_out': opted_out_count +=1
                elif current_member_status == 'answered': answered_count +=1

        call_stats = {
            'total': total_members,
            'called': called_count,
            'completed': completed_count,
            'opted_out': opted_out_count,
            'answered': answered_count,
            'pending': total_members - called_count,
            'progress_percent': round((called_count / total_members) * 100) if total_members > 0 else 0
        }
    except Exception as e:
        logging.error(f"Error in execute_call route for call {call_id}: {e}", exc_info=True)
        flash(f"An unexpected error occurred: {str(e)}", "error")
        return redirect(url_for("call.view_scheduled_calls"))

    return render_template(
        "execute_call.html",
        call_info=call_info,
        members=members,
        announcement_file=call_info.get('filename', 'N/A') if call_info else 'N/A',
        call_stats=call_stats,
        call_id=call_id,
        is_completed=is_completed_or_cancelled
    )

@call_bp.route("/api/originate_call", methods=["POST"])
@login_required
def originate_call():
    active_call_count = 0
    with active_calls_lock:
        for campaign_id_key in active_calls:
            for phone_status_data in active_calls[campaign_id_key].values():
                if phone_status_data.get('status') in ['ringing', 'dialing', 'answered']:
                    active_call_count += 1
    if active_call_count >= MAX_CONCURRENT_CALLS: # Used MAX_CONCURRENT_CALLS from config.py
        logging.warning(f"Max concurrent call limit ({MAX_CONCURRENT_CALLS}) reached. Call request rejected.")
        return jsonify({"success": False, "message": f"Max concurrent call limit ({MAX_CONCURRENT_CALLS}) reached. Please wait.", "call_limit_reached": True}), 429

    data = request.json
    phone_number = data.get("phone_number", "").strip()
    announcement_file = data.get("announcement_file", "")
    member_id = data.get("member_id")
    campaign_id = str(data.get("campaign_id"))

    logging.info(f"API originate call request: Phone={phone_number}, AnnounceFile={announcement_file}, MemberID={member_id}, CampaignID={campaign_id}")

    if not all([phone_number, announcement_file, member_id is not None, campaign_id]):
        logging.error(f"API originate_call: Missing required parameters. PN:{phone_number}, AF:{announcement_file}, MID:{member_id}, CID:{campaign_id}")
        return jsonify({"success": False, "message": "Missing required parameters (phone, announcement, member_id, campaign_id)"}), 400

    caller_id_name = 'InfoCall'
    try:
        call_campaign_info = Call.get_by_id(int(campaign_id))
        if call_campaign_info and call_campaign_info.get('caller_id_name'):
             caller_id_name = call_campaign_info['caller_id_name']
    except Exception as e_cid:
         logging.error(f"Error fetching caller ID name for campaign {campaign_id}: {e_cid}")

    logging.debug(f"DEBUG: Entering originate_call function. CampaignID: {campaign_id}.")
    # Log config values for AMI connection details
    from config import AMI_HOST, AMI_PORT, AMI_USERNAME # Import here to ensure they are available
    logging.debug(f"DEBUG: AMI_HOST={AMI_HOST}, AMI_PORT={AMI_PORT}, AMI_USERNAME={AMI_USERNAME} (from config).")


    ami_connection_successful = False
    ami_client_to_use = None

    # Attempt 1: Try to use the existing global instance and ensure it's connected
    if ami_client_instance:
        logging.debug(f"DEBUG: Global ami_client_instance exists (ID: {ami_client_instance.connection_id if hasattr(ami_client_instance, 'connection_id') else 'N/A'}). Attempting to ensure connection.")
        if ami_client_instance.ensure_connected():
            logging.info(f"AMI client instance (ID: {ami_client_instance.connection_id}) successfully connected/re-connected.")
            ami_client_to_use = ami_client_instance
            ami_connection_successful = True
        else:
            logging.warning(f"AMI client instance (ID: {ami_client_instance.connection_id if hasattr(ami_client_instance, 'connection_id') else 'N/A'}) failed to ensure connection. Will try re-initialization.")
    else:
        logging.warning("DEBUG: Global ami_client_instance is None. Attempting full re-initialization.")

    # Attempt 2: If existing instance failed or was None, try a full re-initialization (force_new)
    if not ami_connection_successful:
        from services.call_service import direct_event_handler_with_optout
        from services.asterisk_service import initialize_ami_client, SocketAMIClient # Import SocketAMIClient directly
        
        logging.info("Attempting to re-initialize global AMI client instance with force_new.")
        # Call initialize_ami_client which uses SocketAMIClient.get_instance(force_new=True)
        initialize_ami_client(direct_event_handler_with_optout) 
        
        # After re-initialization, ami_client_instance should now point to a new instance
        if ami_client_instance:
            logging.debug(f"DEBUG: New ami_client_instance created (ID: {ami_client_instance.connection_id if hasattr(ami_client_instance, 'connection_id') else 'N/A'}). Attempting to ensure connection.")
            if ami_client_instance.ensure_connected():
                logging.info(f"AMI client instance (ID: {ami_client_instance.connection_id}) successfully re-initialized and connected.")
                ami_client_to_use = ami_client_instance
                ami_connection_successful = True
            else:
                logging.error(f"Failed to connect new AMI client instance (ID: {ami_client_instance.connection_id if hasattr(ami_client_instance, 'connection_id') else 'N/A'}) after re-initialization.")
        else:
            logging.error("Failed to get AMI client instance even after re-initialization attempt.")


    if not ami_connection_successful:
        logging.error("AMI connection unavailable after all retry attempts for originate_call.")
        update_call_status(campaign_id, phone_number, 'rejected', 'AMI connection unavailable')
        return jsonify({"success": False, "message": "AMI connection not available"}), 500

    try:
        upload_dir = '/var/www/html/infocall/uploads'
        # Get the sound path without the .wav extension, as Application=Playback expects it this way
        sound_path_for_playback = os.path.join(upload_dir, os.path.splitext(announcement_file)[0])
        
        logging.info(f"Using sound path for Playback: {sound_path_for_playback}, Caller ID Name: {caller_id_name}")
        update_call_status(campaign_id, phone_number, 'dialing', 'Call initiated via API')

        variables_to_set = [
            f"CAMPAIGN_ID={campaign_id}",
            f"CALL_ID={campaign_id}",
            f"DIAL_NUMBER={phone_number}",
            f"MEMBER_ID={member_id}",
            f"FORCE_CALLER_ID={caller_id_name}"
        ]
        
        # MODIFIED: Change action_params to use Application and Data for direct playback
        action_params = {
            'Channel': f'Local/{phone_number}@from-internal',
            'Application': 'Playback', # <--- Use Playback application directly
            'Data': sound_path_for_playback, # <--- Pass the sound path directly
            'CallerID': f'"{caller_id_name}" <{phone_number}>',
            'Async': 'true',
            'Timeout': '45000', # 45 seconds timeout for the call
            'UserField': campaign_id, # Custom field to associate events with campaign
            'Variable': ','.join(variables_to_set) # Variables are still useful
        }
        
        logging.info(f"Sending AMI action: Originate with params: {action_params}") # Added action_params to log
        
        # MODIFIED: Use ami_client_to_use for sending the action
        action_sent_successfuly = False
        send_action_retries = 2 # Retries after initial attempt
        for attempt in range(send_action_retries + 1):
            logging.debug(f"DEBUG: send_action attempt {attempt + 1}/{send_action_retries + 1} for AMI action 'Originate'.")
            if ami_client_to_use and ami_client_to_use.connected: # Use ami_client_to_use
                action_sent_successfuly = ami_client_to_use.send_action('Originate', **action_params) # Use ami_client_to_use
                if action_sent_successfuly:
                    break # Success, exit retry loop
            else:
                logging.warning(f"DEBUG: AMI client disconnected before sending Originate action (Attempt {attempt + 1}). Attempting to re-ensure connection.")
                # This block should ideally not be hit if ami_client_to_use is already connected.
                # However, if it does, it needs to re-establish connection for ami_client_to_use.
                if not ami_client_to_use or not ami_client_to_use.ensure_connected(): # Ensure ami_client_to_use is connected
                    logging.error(f"Failed to re-ensure AMI connection for sending Originate action (Attempt {attempt + 1}).")
                    if attempt == send_action_retries: # If last attempt failed
                        update_call_status(campaign_id, phone_number, 'rejected', 'AMI client unavailable after multiple re-init attempts')
                        return jsonify({"success": False, "message": "AMI connection lost and could not be re-established"}), 500
                else:
                    logging.info(f"DEBUG: AMI connection re-established (ID: {ami_client_to_use.connection_id}). Retrying send_action.") # Use ami_client_to_use
            time.sleep(0.5) # Small delay between send action retries

        if action_sent_successfuly:
            logging.info(f"AMI Originate action sent successfully for {phone_number}, campaign {campaign_id}.")
            try:
                # Update DB status for the campaign if it's the first call
                # This might be redundant if scheduled_call_checker already sets it
                # but ensures it's 'in_progress' if manually triggered.
                call_db_info = Call.get_by_id(int(campaign_id))
                if call_db_info and call_db_info.get('status') == 'ready':
                    Call.update_status(int(campaign_id), 'in_progress')
            except Exception as db_err:
                logging.error(f"DB error updating call {campaign_id} status to in_progress: {db_err}")
            return jsonify({"success": True, "message": "Call originated", "status": "dialing"})
        else:
            logging.error(f"Failed to send AMI Originate action for {phone_number}, campaign {campaign_id} after retries.")
            update_call_status(campaign_id, phone_number, 'rejected', 'Failed to send AMI originate action after retries')
            return jsonify({"success": False, "message": "Failed to send AMI originate action"}), 500
    except Exception as e:
        logging.error(f"Unexpected error originating call to {phone_number} (Campaign: {campaign_id}): {e}", exc_info=True)
        update_call_status(campaign_id, phone_number, 'rejected', f'Error: {str(e)}')
        return jsonify({"success": False, "message": f"An unexpected error occurred: {str(e)}"}), 500

@call_bp.route("/api/abort_calls/<int:call_id>", methods=["POST"])
@login_required
def abort_calls(call_id):
    logging.info(f"Received request to abort calls for campaign ID: {call_id}")
    campaign_id_str = str(call_id)
    aborted_in_memory_count = 0
    hanged_up_on_asterisk_count = 0
    
    try:
        # Log the incoming JSON data
        data = request.json
        if data:
            logging.info(f"Abort calls request JSON data: {data}")
            # Ensure campaign_id from body matches URL parameter if needed, though URL param is primary
            if str(data.get('campaign_id')) != campaign_id_str:
                logging.warning(f"Campaign ID mismatch: URL {campaign_id_str} vs Body {data.get('campaign_id')}")
        else:
            logging.warning("Abort calls request received without JSON body.")

        if Call.update_status(call_id, 'cancelled', 'Aborted by admin'):
            logging.info(f"Marked call campaign {call_id} as 'cancelled' in DB by user {session.get('user_email')}.")
        else:
            call_info_check = Call.get_by_id(call_id)
            if call_info_check:
                 logging.info(f"Call campaign {call_id} already in status '{call_info_check.get('status', 'N/A')}' or failed to update.")
            else:
                 logging.warning(f"Call campaign {call_id} not found in DB during abort operation.")
        
        # Use the imported run_asterisk_command
        phones_to_hangup = []
        with active_calls_lock:
            if campaign_id_str in active_calls:
                for phone_number, status_data in list(active_calls[campaign_id_str].items()):
                    if status_data.get('status') in ['ringing', 'dialing', 'answered']:
                        phones_to_hangup.append(phone_number)
                        # Mark as aborted with current UTC time
                        update_call_status(campaign_id_str, phone_number, 'aborted', 'Aborted by admin') # Use the update_call_status function
                        aborted_in_memory_count += 1
                        logging.info(f"Marked call to {phone_number} for campaign {campaign_id_str} as 'aborted' in memory.")
        
        if not phones_to_hangup:
            logging.info(f"No active calls found in memory for campaign {campaign_id_str} to abort.")
        
        if phones_to_hangup:
            logging.info(f"Attempting to hang up channels for {len(phones_to_hangup)} phone(s) on Asterisk for campaign {campaign_id_str}...")
            # Access run_asterisk_command via the imported module
            success_cli, output_cli = asterisk_service_module.run_asterisk_command('core show channels concise')
            if success_cli:
                active_channels_info = output_cli.strip().split('\n')
                for line in active_channels_info:
                    if not line.strip(): continue
                    parts = line.split('!')
                    channel_name = parts[0]
                    dialed_number_from_channel = ""
                    # Extract UserField, typically it might be at a fixed position or identified by "UserField=" if not concise
                    channel_campaign_id_userfield = parts[11] if len(parts) > 12 else "" # Example for concise format
                    
                    # For Local channels like Local/7500@from-internal-00000000;1
                    if channel_name.startswith("Local/") and "@" in channel_name:
                        dialed_number_from_channel = channel_name.split('@')[0].split('/')[-1]
                    # For PJSIP or SIP channels, the dialed number might be in different parts
                    # This part needs careful checking against actual `core show channels concise` output
                    elif len(parts) > 6 and parts[2].isdigit(): # Often exten or dialed number
                         dialed_number_from_channel = parts[2]
                    elif len(parts) > 6 and parts[6].isdigit(): # Sometimes in context,exten,pri
                         dialed_number_from_channel = parts[6]


                    if dialed_number_from_channel in phones_to_hangup and \
                       (not channel_campaign_id_userfield or channel_campaign_id_userfield == campaign_id_str or campaign_id_str in channel_name): # Added check if campaign_id is in channel name
                        logging.info(f"Requesting hangup for Asterisk channel: {channel_name} (Num: {dialed_number_from_channel}, Campaign UserField: {channel_campaign_id_userfield})")
                        hangup_success, hangup_output = asterisk_service_module.run_asterisk_command(f'channel request hangup {channel_name}')
                        if hangup_success and "requested on" in hangup_output.lower():
                            hanged_up_on_asterisk_count +=1
                            logging.info(f"Hangup successfully requested for channel {channel_name}.")
                        else:
                            logging.warning(f"Failed to request hangup or confirm hangup for channel {channel_name}. Output: {hangup_output}")
            else:
                logging.error(f"Failed to get 'core show channels concise' from Asterisk for campaign {campaign_id_str}.")

        flash_msg = f"Abort requested for campaign {call_id}. {aborted_in_memory_count} call(s) marked 'aborted' in system."
        if hanged_up_on_asterisk_count > 0:
            flash_msg += f" {hanged_up_on_asterisk_count} channel(s) requested for hangup on Asterisk."
        logging.info(f"Abort calls response: {flash_msg}") # Added log for final response
        return jsonify({"success": True, "message": flash_msg, "aborted_count": aborted_in_memory_count, "hanged_up_on_asterisk": hanged_up_on_asterisk_count})
    except Exception as e:
        logging.error(f"Error aborting calls for campaign {call_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"An unexpected error occurred: {str(e)}"}), 500

@call_bp.route("/api/batch_call_status", methods=["POST"])
@login_required
def batch_call_status():
    data = request.json
    phone_numbers = data.get("phone_numbers", [])
    campaign_id_str = str(data.get("campaign_id", "default"))
    if not phone_numbers:
        return jsonify({"success": False, "message": "No phone numbers provided"}), 400
    results = {}
    with active_calls_lock:
        campaign_calls = active_calls.get(campaign_id_str, {})
        for phone in phone_numbers:
            clean_phone = phone.strip()
            status_data = campaign_calls.get(clean_phone, {}).copy()
            # If timestamp is a datetime object, convert it to string for JSON response
            if 'timestamp' in status_data and isinstance(status_data['timestamp'], datetime):
                status_data['timestamp'] = status_data['timestamp'].astimezone(USER_LOCAL_TIMEZONE).strftime('%I:%M:%S %p')
            else:
                status_data['timestamp'] = '-' # Default if not set or not a datetime object
            
            if 'status' not in status_data:
                status_data['status'] = 'unknown'
                status_data['details'] = None

            results[clean_phone] = status_data
    return jsonify({"success": True, "results": results})

# Add these routes to call_routes.py or create a separate debug_routes.py

from flask import jsonify, request
from datetime import datetime
import services.call_service as call_service
import services.asterisk_service as asterisk_service
from app_state import active_calls, active_calls_lock

@call_bp.route("/api/debug/call_history/<campaign_id>/<phone_number>", methods=["GET"])
@login_required
def get_call_debug_history(campaign_id, phone_number):
    """Get debug history for a specific call"""
    try:
        history = call_service.get_call_debug_history(campaign_id, phone_number)
        
        # Format timestamps for JSON
        formatted_history = []
        for entry in history:
            formatted_entry = entry.copy()
            formatted_entry['timestamp'] = entry['timestamp'].isoformat()
            formatted_history.append(formatted_entry)
        
        return jsonify({
            "success": True,
            "campaign_id": campaign_id,
            "phone_number": phone_number,
            "history": formatted_history
        })
    except Exception as e:
        logging.error(f"Error getting call debug history: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

@call_bp.route("/api/debug/ami_history", methods=["GET"])
@login_required
def get_ami_debug_history():
    """Get AMI debug history"""
    try:
        with asterisk_service.ami_debug_lock:
            history = asterisk_service.ami_debug_log.copy()
        
        # Format timestamps for JSON
        formatted_history = []
        for entry in history:
            formatted_entry = entry.copy()
            formatted_entry['timestamp'] = entry['timestamp'].isoformat()
            formatted_history.append(formatted_entry)
        
        return jsonify({
            "success": True,
            "history": formatted_history
        })
    except Exception as e:
        logging.error(f"Error getting AMI debug history: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

@call_bp.route("/api/debug/active_calls", methods=["GET"])
@login_required
def get_active_calls_debug():
    """Get current active_calls state for debugging"""
    try:
        with active_calls_lock:
            active_calls_copy = {}
            for campaign_id, calls in active_calls.items():
                active_calls_copy[campaign_id] = {}
                for phone, call_data in calls.items():
                    call_data_copy = call_data.copy()
                    # Convert timestamp to string for JSON
                    if 'timestamp' in call_data_copy and isinstance(call_data_copy['timestamp'], datetime):
                        call_data_copy['timestamp'] = call_data_copy['timestamp'].isoformat()
                    active_calls_copy[campaign_id][phone] = call_data_copy
        
        return jsonify({
            "success": True,
            "active_calls": active_calls_copy,
            "total_campaigns": len(active_calls_copy),
            "total_calls": sum(len(calls) for calls in active_calls_copy.values())
        })
    except Exception as e:
        logging.error(f"Error getting active calls debug: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

@call_bp.route("/api/debug/ami_status", methods=["GET"])
@login_required
def get_ami_status():
    """Get current AMI client status"""
    try:
        ami_status = {
            "instance_exists": asterisk_service.ami_client_instance is not None,
            "connected": False,
            "connection_id": None,
            "last_activity": None,
            "event_handlers_count": 0,
            "listener_thread_alive": False
        }
        
        if asterisk_service.ami_client_instance:
            client = asterisk_service.ami_client_instance
            ami_status.update({
                "connected": client.connected,
                "connection_id": getattr(client, 'connection_id', 'N/A'),
                "last_activity": client.last_activity if hasattr(client, 'last_activity') else None,
                "event_handlers_count": len(client.event_handlers) if hasattr(client, 'event_handlers') else 0,
                "listener_thread_alive": (client.listener_thread.is_alive() 
                                        if hasattr(client, 'listener_thread') and client.listener_thread 
                                        else False),
                "host": getattr(client, 'host', 'N/A'),
                "port": getattr(client, 'port', 'N/A'),
                "username": getattr(client, 'username', 'N/A')
            })
        
        return jsonify({
            "success": True,
            "ami_status": ami_status
        })
    except Exception as e:
        logging.error(f"Error getting AMI status: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

@call_bp.route("/api/debug/test_ami_connection", methods=["POST"])
@login_required
def test_ami_connection():
    """Test AMI connection and send a simple action"""
    try:
        if not asterisk_service.ami_client_instance:
            return jsonify({"success": False, "message": "No AMI client instance"}), 400
        
        client = asterisk_service.ami_client_instance
        
        # Test connection
        if not client.ensure_connected():
            return jsonify({"success": False, "message": "Failed to establish AMI connection"}), 500
        
        # Send a simple Ping action
        ping_success = client.send_action('Ping')
        
        return jsonify({
            "success": True,
            "connection_test": "passed",
            "ping_sent": ping_success,
            "connection_id": getattr(client, 'connection_id', 'N/A')
        })
        
    except Exception as e:
        logging.error(f"Error testing AMI connection: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

# Add this route to call_routes.py

@call_bp.route("/debug_dashboard")
@login_required  
def debug_dashboard():
    """Serve the debug dashboard page"""
    # Only allow admin users to access debug dashboard
    if session.get('role') != 'admin':
        flash("Access denied. Admin privileges required.", "error")
        return redirect(url_for('auth.main_menu'))
    
    # For now, return the HTML directly. In production, you'd use render_template
    return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>InfoCall Debug Dashboard</title>
    <style>
        body {
            font-family: 'Courier New', monospace;
            margin: 0;
            padding: 20px;
            background-color: #1a1a1a;
            color: #00ff00;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        .debug-section {
            margin: 20px 0;
            border: 1px solid #333;
            border-radius: 5px;
            background-color: #2a2a2a;
        }
        .debug-header {
            background-color: #333;
            padding: 10px 15px;
            font-weight: bold;
            border-bottom: 1px solid #444;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .debug-content {
            padding: 15px;
            max-height: 400px;
            overflow-y: auto;
        }
        .log-entry {
            margin: 5px 0;
            padding: 5px;
            border-left: 3px solid #555;
        }
        .log-ami { border-left-color: #00aaff; color: #00aaff; }
        .log-call { border-left-color: #ffaa00; color: #ffaa00; }
        .log-error { border-left-color: #ff4444; color: #ff4444; }
        .log-success { border-left-color: #44ff44; color: #44ff44; }
        
        .status-indicator {
            display: inline-block;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            margin-right: 8px;
        }
        .status-connected { background-color: #44ff44; }
        .status-disconnected { background-color: #ff4444; }
        .status-unknown { background-color: #ffaa00; }
        
        .button {
            background-color: #444;
            color: #fff;
            border: 1px solid #666;
            padding: 8px 16px;
            border-radius: 3px;
            cursor: pointer;
            margin: 5px;
        }
        .button:hover {
            background-color: #555;
        }
        
        .json-data {
            background-color: #111;
            padding: 10px;
            border-radius: 3px;
            white-space: pre-wrap;
            font-size: 12px;
            overflow-x: auto;
        }
        
        .auto-refresh {
            float: right;
        }
        
        .timestamp {
            color: #888;
            font-size: 11px;
        }
        
        .collapse {
            display: none;
        }
        
        .nav-link {
            color: #00ff00;
            text-decoration: none;
            margin-right: 20px;
        }
        .nav-link:hover {
            color: #44ff44;
        }
    </style>
</head>
<body>
    <div class="container">
        <div style="margin-bottom: 20px;">
            <a href="/main_menu" class="nav-link">← Back to Main Menu</a>
            <a href="/view_scheduled_calls" class="nav-link">View Scheduled Calls</a>
        </div>
        
        <h1>🔍 InfoCall Debug Dashboard</h1>
        
        <!-- AMI Status Section -->
        <div class="debug-section">
            <div class="debug-header" onclick="toggleSection('ami-status')">
                <span>🔌 AMI Connection Status</span>
                <div>
                    <label class="auto-refresh">
                        <input type="checkbox" id="auto-refresh-ami" checked> Auto-refresh (5s)
                    </label>
                    <button class="button" onclick="refreshAMIStatus()">Refresh</button>
                    <button class="button" onclick="testAMIConnection()">Test Connection</button>
                </div>
            </div>
            <div class="debug-content" id="ami-status">
                <div id="ami-status-content">Loading...</div>
            </div>
        </div>

        <!-- Active Calls Section -->
        <div class="debug-section">
            <div class="debug-header" onclick="toggleSection('active-calls')">
                <span>📞 Active Calls State</span>
                <div>
                    <label class="auto-refresh">
                        <input type="checkbox" id="auto-refresh-calls" checked> Auto-refresh (3s)
                    </label>
                    <button class="button" onclick="refreshActiveCalls()">Refresh</button>
                </div>
            </div>
            <div class="debug-content" id="active-calls">
                <div id="active-calls-content">Loading...</div>
            </div>
        </div>

        <!-- AMI Event Log Section -->
        <div class="debug-section">
            <div class="debug-header" onclick="toggleSection('ami-log')">
                <span>⚡ AMI Event Log</span>
                <div>
                    <label class="auto-refresh">
                        <input type="checkbox" id="auto-refresh-ami-log" checked> Auto-refresh (2s)
                    </label>
                    <button class="button" onclick="refreshAMILog()">Refresh</button>
                </div>
            </div>
            <div class="debug-content" id="ami-log">
                <div id="ami-log-content">Loading...</div>
            </div>
        </div>

        <!-- Call Debug History Section -->
        <div class="debug-section">
            <div class="debug-header" onclick="toggleSection('call-history')">
                <span>🎯 Call Debug History</span>
                <div>
                    <input type="text" id="campaign-id-input" placeholder="Campaign ID" style="margin-right: 5px; background: #333; color: #fff; border: 1px solid #666; padding: 5px;">
                    <input type="text" id="phone-number-input" placeholder="Phone Number" style="margin-right: 5px; background: #333; color: #fff; border: 1px solid #666; padding: 5px;">
                    <button class="button" onclick="loadCallHistory()">Load History</button>
                </div>
            </div>
            <div class="debug-content" id="call-history">
                <div id="call-history-content">Enter Campaign ID and Phone Number above to load debug history.</div>
            </div>
        </div>
    </div>

    <script>
        // Auto-refresh intervals
        let amiStatusInterval, activeCallsInterval, amiLogInterval;
        
        // Initialize page
        document.addEventListener('DOMContentLoaded', function() {
            refreshAMIStatus();
            refreshActiveCalls();
            refreshAMILog();
            
            // Set up auto-refresh
            setupAutoRefresh();
        });

        function setupAutoRefresh() {
            // AMI Status auto-refresh
            const amiCheckbox = document.getElementById('auto-refresh-ami');
            amiCheckbox.addEventListener('change', function() {
                if (this.checked) {
                    amiStatusInterval = setInterval(refreshAMIStatus, 5000);
                } else {
                    clearInterval(amiStatusInterval);
                }
            });
            if (amiCheckbox.checked) {
                amiStatusInterval = setInterval(refreshAMIStatus, 5000);
            }

            // Active Calls auto-refresh
            const callsCheckbox = document.getElementById('auto-refresh-calls');
            callsCheckbox.addEventListener('change', function() {
                if (this.checked) {
                    activeCallsInterval = setInterval(refreshActiveCalls, 3000);
                } else {
                    clearInterval(activeCallsInterval);
                }
            });
            if (callsCheckbox.checked) {
                activeCallsInterval = setInterval(refreshActiveCalls, 3000);
            }

            // AMI Log auto-refresh
            const logCheckbox = document.getElementById('auto-refresh-ami-log');
            logCheckbox.addEventListener('change', function() {
                if (this.checked) {
                    amiLogInterval = setInterval(refreshAMILog, 2000);
                } else {
                    clearInterval(amiLogInterval);
                }
            });
            if (logCheckbox.checked) {
                amiLogInterval = setInterval(refreshAMILog, 2000);
            }
        }

        function toggleSection(sectionId) {
            const content = document.getElementById(sectionId);
            content.classList.toggle('collapse');
        }

        async function refreshAMIStatus() {
            try {
                const response = await fetch('/api/debug/ami_status');
                const data = await response.json();
                
                if (data.success) {
                    const status = data.ami_status;
                    const indicator = status.connected ? 
                        '<span class="status-indicator status-connected"></span>Connected' :
                        '<span class="status-indicator status-disconnected"></span>Disconnected';
                    
                    document.getElementById('ami-status-content').innerHTML = `
                        <div><strong>Status:</strong> ${indicator}</div>
                        <div><strong>Connection ID:</strong> ${status.connection_id || 'N/A'}</div>
                        <div><strong>Host:</strong> ${status.host || 'N/A'}:${status.port || 'N/A'}</div>
                        <div><strong>Username:</strong> ${status.username || 'N/A'}</div>
                        <div><strong>Event Handlers:</strong> ${status.event_handlers_count}</div>
                        <div><strong>Listener Thread:</strong> ${status.listener_thread_alive ? 'Alive' : 'Dead'}</div>
                        <div><strong>Last Activity:</strong> ${status.last_activity ? new Date(status.last_activity * 1000).toLocaleString() : 'N/A'}</div>
                        <div class="json-data">${JSON.stringify(status, null, 2)}</div>
                    `;
                } else {
                    document.getElementById('ami-status-content').innerHTML = `<div class="log-error">Error: ${data.message}</div>`;
                }
            } catch (error) {
                document.getElementById('ami-status-content').innerHTML = `<div class="log-error">Fetch Error: ${error.message}</div>`;
            }
        }

        async function refreshActiveCalls() {
            try {
                const response = await fetch('/api/debug/active_calls');
                const data = await response.json();
                
                if (data.success) {
                    let html = `
                        <div><strong>Total Campaigns:</strong> ${data.total_campaigns}</div>
                        <div><strong>Total Active Calls:</strong> ${data.total_calls}</div>
                        <hr>
                    `;
                    
                    if (data.total_calls === 0) {
                        html += '<div>No active calls</div>';
                    } else {
                        for (const [campaignId, calls] of Object.entries(data.active_calls)) {
                            html += `<div><strong>Campaign ${campaignId}:</strong></div>`;
                            for (const [phone, callData] of Object.entries(calls)) {
                                const statusClass = getStatusClass(callData.status);
                                html += `
                                    <div class="log-entry ${statusClass}">
                                        📞 ${phone}: ${callData.status} 
                                        <span class="timestamp">${callData.timestamp ? new Date(callData.timestamp).toLocaleTimeString() : 'N/A'}</span>
                                        <br>&nbsp;&nbsp;&nbsp;&nbsp;${callData.details || 'No details'}
                                        <br>&nbsp;&nbsp;&nbsp;&nbsp;ActionID: ${callData.action_id || 'N/A'} | UniqueID: ${callData.uniqueid || 'N/A'}
                                    </div>
                                `;
                            }
                        }
                    }
                    
                    document.getElementById('active-calls-content').innerHTML = html;
                } else {
                    document.getElementById('active-calls-content').innerHTML = `<div class="log-error">Error: ${data.message}</div>`;
                }
            } catch (error) {
                document.getElementById('active-calls-content').innerHTML = `<div class="log-error">Fetch Error: ${error.message}</div>`;
            }
        }

        async function refreshAMILog() {
            try {
                const response = await fetch('/api/debug/ami_history');
                const data = await response.json();
                
                if (data.success) {
                    let html = '';
                    const recentEntries = data.history.slice(-50).reverse();
                    
                    for (const entry of recentEntries) {
                        const timestamp = new Date(entry.timestamp).toLocaleTimeString();
                        html += `
                            <div class="log-entry log-ami">
                                <span class="timestamp">${timestamp}</span> 
                                <strong>${entry.action}</strong> - ${entry.details}
                            </div>
                        `;
                    }
                    
                    if (html === '') {
                        html = '<div>No AMI events logged yet</div>';
                    }
                    
                    document.getElementById('ami-log-content').innerHTML = html;
                } else {
                    document.getElementById('ami-log-content').innerHTML = `<div class="log-error">Error: ${data.message}</div>`;
                }
            } catch (error) {
                document.getElementById('ami-log-content').innerHTML = `<div class="log-error">Fetch Error: ${error.message}</div>`;
            }
        }

        async function loadCallHistory() {
            const campaignId = document.getElementById('campaign-id-input').value.trim();
            const phoneNumber = document.getElementById('phone-number-input').value.trim();
            
            if (!campaignId || !phoneNumber) {
                document.getElementById('call-history-content').innerHTML = '<div class="log-error">Please enter both Campaign ID and Phone Number</div>';
                return;
            }
            
            try {
                const response = await fetch(`/api/debug/call_history/${campaignId}/${phoneNumber}`);
                const data = await response.json();
                
                if (data.success) {
                    let html = `<div><strong>Campaign:</strong> ${data.campaign_id} | <strong>Phone:</strong> ${data.phone_number}</div><hr>`;
                    
                    if (data.history.length === 0) {
                        html += '<div>No debug history found for this call</div>';
                    } else {
                        for (const entry of data.history.reverse()) {
                            const timestamp = new Date(entry.timestamp).toLocaleTimeString();
                            html += `
                                <div class="log-entry log-call">
                                    <span class="timestamp">${timestamp}</span> 
                                    <strong>${entry.action}</strong> - ${entry.details}
                                </div>
                            `;
                        }
                    }
                    
                    document.getElementById('call-history-content').innerHTML = html;
                } else {
                    document.getElementById('call-history-content').innerHTML = `<div class="log-error">Error: ${data.message}</div>`;
                }
            } catch (error) {
                document.getElementById('call-history-content').innerHTML = `<div class="log-error">Fetch Error: ${error.message}</div>`;
            }
        }

        async function testAMIConnection() {
            try {
                const response = await fetch('/api/debug/test_ami_connection', { method: 'POST' });
                const data = await response.json();
                
                if (data.success) {
                    alert(`AMI Connection Test: SUCCESS\\nConnection ID: ${data.connection_id}\\nPing Sent: ${data.ping_sent}`);
                } else {
                    alert(`AMI Connection Test: FAILED\\nError: ${data.message}`);
                }
            } catch (error) {
                alert(`AMI Connection Test: ERROR\\n${error.message}`);
            }
        }

        function getStatusClass(status) {
            if (['completed', 'answered'].includes(status)) return 'log-success';
            if (['failed', 'rejected', 'aborted'].includes(status)) return 'log-error';
            if (['dialing', 'ringing'].includes(status)) return 'log-ami';
            return 'log-call';
        }
    </script>
</body>
</html>'''        

@call_bp.route("/api/call_status/<phone_number>", methods=["GET"])
@login_required
def get_call_status(phone_number):
    clean_phone = phone_number.strip()
    campaign_id_str = str(request.args.get('campaign_id', 'default'))
    reset_status = request.args.get('reset', '0') == '1'
    logging.info(f"API Call Status Check: Campaign {campaign_id_str}, Phone {clean_phone}, Reset: {reset_status}")
    status_data_to_return = {}
    with active_calls_lock:
        if campaign_id_str not in active_calls:
            active_calls[campaign_id_str] = {}
        if reset_status:
            prev_status = active_calls[campaign_id_str].get(clean_phone, {}).get('status', 'unknown')
            logging.info(f"Resetting status for Campaign {campaign_id_str}, Phone {clean_phone} - Previous status was: {prev_status}")
            new_status_info = {
                'status': 'waiting',
                'details': 'Status manually reset by user',
                'timestamp': datetime.now(UTC_TIMEZONE) # Store as UTC datetime object
            }
            active_calls[campaign_id_str][clean_phone] = new_status_info
            status_data_to_return = new_status_info.copy()
        else:
            status_data_to_return = active_calls[campaign_id_str].get(
                clean_phone,
                {'status': 'unknown', 'details': None, 'timestamp': datetime.now(UTC_TIMEZONE)} # Default to UTC datetime
            ).copy()
    
    # Format timestamp for JSON response
    if 'timestamp' in status_data_to_return and isinstance(status_data_to_return['timestamp'], datetime):
        status_data_to_return['timestamp'] = status_data_to_return['timestamp'].astimezone(USER_LOCAL_TIMEZONE).strftime('%I:%M:%S %p')
    else:
        status_data_to_return['timestamp'] = '-' # Fallback if not a datetime object

    status_data_to_return['phone_number'] = clean_phone
    return jsonify(status_data_to_return)


@call_bp.route("/api/ivr_schedule_trigger", methods=["POST"])
def ivr_schedule_trigger():
    """
    API endpoint for the IVR AGI script to trigger the scheduling of a new call.
    This route is called by `ivr_handler.agi` after a successful announcement recording.
    It does not require login_required decorator as it's an internal server-to-server call.
    """
    if not request.is_json:
        logging.error("IVR Schedule Trigger: Request must be JSON.")
        return jsonify({"success": False, "message": "Request must be JSON"}), 400

    data = request.get_json()
    announcement_id = data.get('announcement_id')
    user_id = data.get('user_id')

    logging.info(f"IVR Schedule Trigger: Received request for Announcement ID: {announcement_id}, User ID: {user_id}")

    if announcement_id is None or user_id is None:
        logging.error("IVR Schedule Trigger: Missing required parameters (announcement_id or user_id).")
        return jsonify({"success": False, "message": "Missing required parameters: announcement_id or user_id"}), 400

    try:
        # Determine the current UTC time and schedule for 15 minutes in the future
        scheduled_dt_utc = datetime.now(UTC_TIMEZONE) + timedelta(minutes=15) # Schedule 15 mins from now

        # Default values for other parameters
        group_id = None  # Schedule for all members by default
        caller_id_name = "IVR Announcement"[:10] # Default caller ID name for IVR-triggered calls, ensure it fits in the column

        # Create a new scheduled call entry in the database
        last_row_id = Call.create(
            announcement_id=announcement_id,
            scheduled_dt_utc=scheduled_dt_utc,
            group_id=group_id,
            user_id=user_id,
            caller_id_name=caller_id_name,
            status='pending' # Set initial status to pending for background checker
        )

        if last_row_id:
            logging.info(f"IVR Schedule Trigger: Successfully scheduled call ID: {last_row_id} for announcement ID: {announcement_id} by user: {user_id}")
            return jsonify({"success": True, "message": f"Announcement scheduled successfully with Call ID: {last_row_id}"}), 200
        else:
            logging.error(f"IVR Schedule Trigger: Failed to create scheduled call record for announcement ID: {announcement_id} by user: {user_id}.")
            return jsonify({"success": False, "message": "Failed to create scheduled call record"}), 500

    except Exception as e:
        logging.error(f"IVR Schedule Trigger: Unexpected error scheduling call for announcement {announcement_id}, user {user_id}: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"An unexpected error occurred during scheduling: {str(e)}"}), 500
