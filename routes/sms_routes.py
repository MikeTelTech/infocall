import os
import subprocess
import math
from datetime import datetime, timedelta
from flask import render_template, request, redirect, url_for, flash, session, jsonify, abort
from werkzeug.utils import secure_filename
import logging
import uuid
from pydub import AudioSegment

from . import sms_bp
from models.group import Group
from models.member import Member
from models.sms import SMS # Ensure SMS model is imported
from services.sms_service import update_sms_status, is_sms_complete, current_sms_session, sms_session_lock # Import from new SMS service
from utils.security import login_required
from utils.validation import validate_phone_number

# Corrected Imports to resolve circular dependency
from app_state import active_sms, active_sms_lock, USER_LOCAL_TIMEZONE, UTC_TIMEZONE # type: ignore
from config import MAX_SMS_PER_MINUTE # type: ignore


@sms_bp.route("/sms_mem", methods=["GET", "POST"])
@login_required
def sms_mem():
    if request.method != "POST":
        groups = []
        try:
            groups = Group.get_all_simple()
            logging.debug("Fetched data for SMS scheduling form (GET).")
        except Exception as e:
            logging.error(f"Error fetching data for sms_mem GET: {e}", exc_info=True)
            flash("An error occurred loading form data.", "error")
            groups = []

        today_local = datetime.now(USER_LOCAL_TIMEZONE).strftime("%Y-%m-%d")
        return render_template("sms_mem.html", groups=groups, today=today_local)

    # POST request for scheduling SMS
    message_content = request.form.get("message_content")
    source_phone_number = request.form.get("source_phone_number")
    scheduled_date = request.form.get("scheduled_date")
    scheduled_time = request.form.get("scheduled_time")
    group = request.form.get("group", "all")
    user_id = session.get("user_id")

    logging.info(f"Scheduling SMS POST: Message='{message_content[:50]}...', Source={source_phone_number}, Date={scheduled_date}, Time={scheduled_time}, Group={group}, User={user_id}")

    def fetch_form_data_for_error_render():
        groups_form = []
        try:
            groups_form = Group.get_all_simple()
        except Exception as e_fetch:
            logging.error(f"Error re-fetching form data after validation error: {e_fetch}")
        today_local_err = datetime.now(USER_LOCAL_TIMEZONE).strftime("%Y-%m-%d")
        return groups_form, today_local_err

    if not all([message_content, source_phone_number, scheduled_date, scheduled_time, user_id]):
        flash("Missing required fields (message, source number, date, time).", "warning")
        logging.warning("SMS schedule failed: Missing required fields.")
        grp, today_err = fetch_form_data_for_error_render()
        return render_template("sms_mem.html", groups=grp, today=today_err)

    if not validate_phone_number(source_phone_number):
        flash("Invalid source phone number format.", "warning")
        logging.warning(f"SMS schedule failed: Invalid source phone number {source_phone_number}.")
        grp, today_err = fetch_form_data_for_error_render()
        return render_template("sms_mem.html", groups=grp, today=today_err)

    try:
        scheduled_datetime_str = f"{scheduled_date} {scheduled_time}"
        naive_datetime_obj = datetime.strptime(scheduled_datetime_str, "%Y-%m-%d %H:%M")

        # Corrected Timezone Localization for zoneinfo:
        # Assume naive_datetime_obj is in the user's local time, make it aware in that zone.
        local_dt_aware = naive_datetime_obj.replace(tzinfo=USER_LOCAL_TIMEZONE) # CRUCIAL FIX

        now_utc = datetime.now(UTC_TIMEZONE)
        scheduled_dt_utc = local_dt_aware.astimezone(UTC_TIMEZONE)

        logging.info(f"Scheduling check: User Input Local Time={local_dt_aware}, Scheduled UTC Time={scheduled_dt_utc}, Current UTC Time={now_utc}")

        if scheduled_dt_utc < (now_utc - timedelta(seconds=1)):
            flash("Cannot schedule SMS in the past.", "warning")
            logging.warning(f"SMS schedule failed: Time in past. Scheduled UTC: {scheduled_dt_utc}, Now UTC: {now_utc}")
            grp, today_err = fetch_form_data_for_error_render()
            return render_template("sms_mem.html", groups=grp, today=today_err)

        group_id = group if group != "all" else None
        
        # Determine webhook URL based on current environment or config
        # For a production environment, this should be a stable, publicly accessible URL
        # For development, you might use ngrok or similar.
        # This is a placeholder; you might need to make this configurable.
        webhook_base_url = request.url_root.replace('http://', 'https://') if request.is_secure else request.url_root
        status_callback_webhook_url = f"{webhook_base_url}api/sms_status_callback"
        logging.info(f"Using SMS status callback webhook URL: {status_callback_webhook_url}")

        last_row_id = SMS.create(message_content, source_phone_number, scheduled_dt_utc, group_id, user_id, status_callback_webhook_url)
        logging.info(f"Scheduled SMS inserted with ID: {last_row_id} for UTC time {scheduled_dt_utc}")

        # Format local time for display in flash message
        flash(f"SMS campaign scheduled successfully for {local_dt_aware.strftime('%Y-%m-%d %I:%M %p %Z')} (ID: {last_row_id}). Status: PENDING.", "success")
        return redirect(url_for("sms.view_scheduled_sms"))

    except ValueError as ve:
        logging.error(f"Invalid date/time format submitted: '{scheduled_datetime_str}' - {ve}")
        flash("Invalid date or time format selected. Please use appropriate date-MM-DD and HH:MM.", "error")
        grp, today_err = fetch_form_data_for_error_render()
        return render_template("sms_mem.html", groups=grp, today=today_err)
    except Exception as e:
        logging.error(f"Unexpected error scheduling SMS: {e}", exc_info=True)
        flash("An unexpected error occurred while scheduling the SMS campaign.", "error")
        return redirect(url_for("sms.sms_mem"))


@sms_bp.route("/view_scheduled_sms")
@login_required
def view_scheduled_sms():
    scheduled_sms_campaigns = []
    try:
        scheduled_sms_raw = SMS.get_all_scheduled()
        for sms_data in scheduled_sms_raw:
            db_datetime_utc = sms_data.get('scheduled_datetime')
            if isinstance(db_datetime_utc, datetime):
                # Ensure it's treated as UTC from DB and convert to local for display
                if db_datetime_utc.tzinfo is None:
                    db_datetime_utc = db_datetime_utc.replace(tzinfo=UTC_TIMEZONE)
                dt_local = db_datetime_utc.astimezone(USER_LOCAL_TIMEZONE)
                sms_data['formatted_datetime'] = dt_local.strftime('%Y-%m-%d %I:%M %p %Z')
            elif isinstance(db_datetime_utc, str):
                try:
                    naive_dt = datetime.fromisoformat(db_datetime_utc)
                    aware_utc_dt = naive_dt.replace(tzinfo=UTC_TIMEZONE)
                    dt_local = aware_utc_dt.astimezone(USER_LOCAL_TIMEZONE)
                    sms_data['formatted_datetime'] = dt_local.strftime('%Y-%m-%d %I:%M %p %Z')
                except ValueError:
                    sms_data['formatted_datetime'] = db_datetime_utc + " (Unparseable UTC string)"
                    logging.warning(f"Could not parse string datetime for SMS {sms_data.get('id')}: {db_datetime_utc}")
            else:
                sms_data['formatted_datetime'] = 'N/A'
                logging.warning(f"Unexpected type or missing scheduled_datetime for SMS {sms_data.get('id')}: {db_data_utc}")

            sms_data['group_filter'] = sms_data.get('group_filter_name', 'all')
            scheduled_sms_campaigns.append(sms_data)
    except Exception as e:
        logging.error(f"Unexpected error fetching scheduled SMS: {e}", exc_info=True)
        flash("An unexpected error occurred while loading SMS campaigns.", "error")
        scheduled_sms_campaigns = []
    return render_template("view_scheduled_sms.html", scheduled_sms_campaigns=scheduled_sms_campaigns)

@sms_bp.route("/remove_scheduled_sms/<int:sms_id>")
@login_required
def remove_scheduled_sms(sms_id):
    try:
        sms_info = SMS.get_by_id(sms_id)
        if not sms_info:
            flash("SMS campaign not found or already removed.", "warning")
            return redirect(url_for("sms.view_scheduled_sms"))
        
        sms_status = sms_info.get('status')
        if sms_status == 'in_progress':
            flash("Cannot remove SMS campaigns currently in progress. Please abort the campaign first if needed.", "warning")
            return redirect(url_for("sms.view_scheduled_sms"))

        if SMS.delete(sms_id):
            flash(f"Scheduled SMS campaign (ID: {sms_id}) has been removed successfully.", "success")
            logging.info(f"Removed scheduled SMS campaign ID {sms_id} by user {session.get('user_email')}")
        else:
            flash(f"SMS campaign record (ID: {sms_id}) could not be removed (possibly already deleted).", "warning")
            logging.warning(f"Attempted to remove SMS campaign {sms_id}, but no rows affected in DB (user: {session.get('user_email')}).")
    except Exception as e:
        logging.error(f"Error removing scheduled SMS campaign {sms_id}: {e}", exc_info=True)
        flash("An unexpected error occurred while trying to remove the SMS campaign.", "error")
    return redirect(url_for("sms.view_scheduled_sms"))

@sms_bp.route("/execute_sms/<int:sms_id>")
@login_required
def execute_sms(sms_id):
    sms_info = None
    members = []
    sms_stats = {'total': 0, 'sent': 0, 'delivered': 0, 'failed': 0, 'opted_out': 0, 'pending': 0, 'progress_percent': 0}
    is_completed_or_cancelled = False
    
    with sms_session_lock:
        session_data = current_sms_session.copy()

    try:
        sms_info = SMS.get_by_id(sms_id)
        if not sms_info:
            flash("Scheduled SMS campaign not found.", "warning")
            return redirect(url_for("sms.view_scheduled_sms"))

        logging.info(f"SMS info retrieved for campaign {sms_id}: {sms_info}")
        is_completed_or_cancelled = sms_info.get('status') in ['completed', 'cancelled', 'failed', 'completed_with_errors']
        members = Member.get_members_for_sms(sms_info.get('group_filter'), is_completed_sms=is_completed_or_cancelled)
        logging.info(f"Members found for campaign {sms_id} (is_completed_or_cancelled={is_completed_or_cancelled}): {len(members)}")

        if sms_info.get('status') == 'ready':
            if SMS.update_status(sms_id, 'in_progress', 'Execution page viewed'):
                sms_info['status'] = 'in_progress'
                logging.info(f"Updated SMS {sms_id} status to 'in_progress' in DB.")
            else:
                logging.error(f"Failed to update SMS {sms_id} status. It might have been processed by another instance.", "error")

        # Format scheduled_datetime for display
        if 'scheduled_datetime' in sms_info and isinstance(sms_info['scheduled_datetime'], datetime):
            if sms_info['scheduled_datetime'].tzinfo is None:
                sms_info['scheduled_datetime'] = sms_info['scheduled_datetime'].replace(tzinfo=UTC_TIMEZONE)
            sms_info['formatted_scheduled_datetime'] = sms_info['scheduled_datetime'].astimezone(USER_LOCAL_TIMEZONE).strftime('%Y-%m-%d %I:%M %p %Z')
        else:
            sms_info['formatted_scheduled_datetime'] = 'N/A'

        if not members:
            flash("No eligible members found for this SMS campaign.", "warning")
            sms_info['status'] = 'completed' # Mark as completed if no members
            SMS.update_status(sms_id, 'completed', 'No eligible members found') # Update DB if not already completed

        total_members = len(members)
        sent_count = 0
        delivered_count = 0
        failed_count = 0
        opted_out_count = 0
        
        campaign_sms_data = active_sms.get(str(sms_id), {})
        for member in members:
            phone = member['phone_number']
            status_data = campaign_sms_data.get(phone, {})
            member['sms_status'] = status_data.get('status', 'pending')
            member['sms_details'] = status_data.get('details', '')
            
            # Format timestamp for display only here
            timestamp_utc = status_data.get('timestamp')
            if isinstance(timestamp_utc, datetime):
                member['sms_timestamp'] = timestamp_utc.astimezone(USER_LOCAL_TIMEZONE).strftime('%I:%M:%S %p')
            else:
                member['sms_timestamp'] = '-'

            current_member_status = member['sms_status']
            if current_member_status and current_member_status not in ['pending', 'waiting', 'unknown', 'queued']:
                sent_count +=1 # Treat anything beyond pending/waiting as 'sent' for progress
                if current_member_status == 'sent': sent_count +=1
                elif current_member_status == 'delivered': delivered_count +=1
                elif current_member_status == 'failed': failed_count +=1
                elif current_member_status == 'opted_out': opted_out_count +=1
        
        sms_stats = {
            'total': total_members,
            'sent': sent_count,
            'delivered': delivered_count,
            'failed': failed_count,
            'opted_out': opted_out_count,
            'pending': total_members - (sent_count + delivered_count + failed_count + opted_out_count), # More accurate pending
            'progress_percent': round((sent_count + delivered_count + failed_count + opted_out_count / total_members) * 100) if total_members > 0 else 0
        }

    except Exception as e:
        logging.error(f"Error in execute_sms route for SMS {sms_id}: {e}", exc_info=True)
        flash(f"An unexpected error occurred: {str(e)}", "error")
        return redirect(url_for("sms.view_scheduled_sms"))

    return render_template(
        "execute_sms.html",
        sms_info=sms_info,
        members=members,
        sms_stats=sms_stats,
        sms_id=sms_id,
        is_completed=is_completed_or_cancelled,
        current_sms_session=session_data
    )

@sms_bp.route("/api/batch_sms_status", methods=["POST"])
@login_required
def batch_sms_status():
    data = request.json
    phone_numbers = data.get("phone_numbers", [])
    campaign_id_str = str(data.get("campaign_id", "default"))
    if not phone_numbers:
        return jsonify({"success": False, "message": "No phone numbers provided"}), 400
    
    results = {}
    with active_sms_lock:
        campaign_sms = active_sms.get(campaign_id_str, {})
        for phone in phone_numbers:
            clean_phone = phone.strip()
            status_data = campaign_sms.get(clean_phone, {}).copy()
            # If timestamp is a datetime object, convert it to string for JSON response
            if 'timestamp' in status_data and isinstance(status_data['timestamp'], datetime):
                status_data['timestamp'] = status_data['timestamp'].astimezone(USER_LOCAL_TIMEZONE).strftime('%I:%M:%S %p')
            else:
                status_data['timestamp'] = '-' # Default if not set or not a datetime object
            
            if 'status' not in status_data:
                status_data['status'] = 'unknown'
                status_data['details'] = None

            results[clean_phone] = status_data
    
    with sms_session_lock:
        session_progress = current_sms_session.copy()
        if session_progress['campaign_id'] == campaign_id_str:
            session_progress['current_progress'] = f"{session_progress['members_sent']} of {session_progress['total_members']}"
        else:
            session_progress = {'status': 'inactive', 'current_progress': 'N/A'} # Clear if not current campaign

    return jsonify({"success": True, "results": results, "session_progress": session_progress})

@sms_bp.route("/api/sms_status/<phone_number>", methods=["GET"])
@login_required
def get_sms_status(phone_number):
    clean_phone = phone_number.strip()
    campaign_id_str = str(request.args.get('campaign_id', 'default'))
    reset_status = request.args.get('reset', '0') == '1'
    logging.info(f"API SMS Status Check: Campaign {campaign_id_str}, Phone {clean_phone}, Reset: {reset_status}")
    status_data_to_return = {}
    with active_sms_lock:
        if campaign_id_str not in active_sms:
            active_sms[campaign_id_str] = {}
        if reset_status:
            prev_status = active_sms[campaign_id_str].get(clean_phone, {}).get('status', 'unknown')
            logging.info(f"Resetting status for Campaign {campaign_id_str}, Phone {clean_phone} - Previous status was: {prev_status}")
            new_status_info = {
                'status': 'waiting',
                'details': 'Status manually reset by user',
                'timestamp': datetime.now(UTC_TIMEZONE) # Store as UTC datetime object
            }
            active_sms[campaign_id_str][clean_phone] = new_status_info
            status_data_to_return = new_status_info.copy()
        else:
            status_data_to_return = active_sms[campaign_id_str].get(
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

@sms_bp.route("/api/sms_status_callback", methods=["POST"])
def sms_status_callback():
    try:
        # Twilio sends form-encoded data, not JSON
        message_sid = request.form.get("SmsSid")
        message_status = request.form.get("SmsStatus") # e.g., 'queued', 'sending', 'sent', 'delivered', 'undelivered', 'failed'
        to_number = request.form.get("To")
        from_number = request.form.get("From")
        error_code = request.form.get("ErrorCode")

        # Extract custom parameters passed during origination
        # These are usually prefixed with 'X-' or similar if set in Twilio API call metadata
        # For simplicity, if we passed them as part of the 'Body' or as custom parameters in the URL,
        # we'd need to parse them from there. Assuming they are sent back as standard params if set on Message.
        # Twilio sends parameters in the webhook for `MessagingServiceSid`, `AccountSid`, `From`, `To`, `Body`, `SmsSid`, `SmsStatus`, `MessageStatus`, `ApiVersion` etc.
        # Custom parameters (like campaign_id, member_id) typically need to be put into the `StatusCallback` URL itself or in Twilio's `Parameters` feature.
        # In `services/twilio_service.py`, these were added as query parameters to the webhook URL.
        campaign_id = request.args.get("campaign_id") # Get from URL query parameters
        member_id = request.args.get("member_id") # Get from URL query parameters

        if not campaign_id or not to_number:
            logging.error(f"SMS Status Callback: Missing essential data (campaign_id: {campaign_id}, To: {to_number}). Form Data: {request.form}")
            return "Missing data", 400

        logging.info(f"SMS Status Callback received for SID: {message_sid}, Status: {message_status}, To: {to_number}, Campaign: {campaign_id}, Member: {member_id}")

        details = f"Twilio Status: {message_status}"
        if error_code:
            details += f", Error Code: {error_code}"

        # Map Twilio statuses to our internal statuses
        internal_status = 'unknown'
        if message_status in ['queued', 'sending']:
            internal_status = 'sending'
        elif message_status == 'sent':
            internal_status = 'sent'
        elif message_status == 'delivered':
            internal_status = 'delivered'
        elif message_status in ['undelivered', 'failed']:
            internal_status = 'failed'
        
        update_sms_status(campaign_id, to_number, internal_status, details)
        
        # If the status is 'failed' and an error code indicates opt-out (e.g., Twilio 21610 for 'Stopped due to unsubscribed keyword')
        if internal_status == 'failed' and error_code == '21610' and member_id:
            try:
                logging.info(f"Member {member_id} with phone {to_number} opted out via Twilio webhook (Error Code {error_code}). Updating DB.")
                Member.update_remove_from_sms_status(member_id, 1) # Mark member as opted out from SMS
                update_sms_status(campaign_id, to_number, 'opted_out', 'Opted out via Twilio')
            except Exception as opt_out_err:
                logging.error(f"Error processing SMS opt-out for member {member_id}: {opt_out_err}", exc_info=True)


        return "OK", 200 # Twilio expects a 200 OK response
    except Exception as e:
        logging.error(f"Error processing SMS status callback: {e}", exc_info=True)
        return "Error", 500

@sms_bp.route("/api/abort_sms/<int:sms_id>", methods=["POST"])
@login_required
def abort_sms(sms_id):
    campaign_id_str = str(sms_id)
    aborted_in_memory_count = 0
    try:
        if SMS.update_status(sms_id, 'cancelled', 'Aborted by admin'):
            logging.info(f"Marked SMS campaign {sms_id} as 'cancelled' in DB by user {session.get('user_email')}.")
        else:
            sms_info_check = SMS.get_by_id(sms_id)
            if sms_info_check:
                 logging.info(f"SMS campaign {sms_id} already in status '{sms_info_check.get('status', 'N/A')}' or failed to update.")
            else:
                 logging.warning(f"SMS campaign {sms_id} not found in DB during abort operation.")
        
        with active_sms_lock:
            if campaign_id_str in active_sms:
                for phone_number, status_data in list(active_sms[campaign_id_str].items()):
                    # Only update if the SMS is still in a pending/sending state
                    if status_data.get('status') in ['pending', 'sending', 'queued', 'unknown']:
                        active_sms[campaign_id_str][phone_number] = {
                            'status': 'aborted',
                            'details': 'Aborted by admin',
                            'timestamp': datetime.now(UTC_TIMEZONE) 
                        }
                        aborted_in_memory_count += 1
                        logging.info(f"Marked SMS to {phone_number} for campaign {campaign_id_str} as 'aborted' in memory.")
        
        flash_msg = f"Abort requested for SMS campaign {sms_id}. {aborted_in_memory_count} message(s) marked 'aborted' in system."
        flash(flash_msg, "info")
        return jsonify({"success": True, "message": flash_msg, "aborted_count": aborted_in_memory_count})
    except Exception as e:
        logging.error(f"Error aborting SMS for campaign {sms_id}: {e}", exc_info=True)
        flash(f"An unexpected error occurred while aborting SMS: {str(e)}", "error")
        return jsonify({"success": False, "message": f"Unexpected error: {str(e)}"}), 500