# services/sms_service.py
import os
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

# Models
from models.sms import SMS
from models.member import Member
from models.app_setting import AppSetting

# Corrected Imports to resolve circular dependency
from app_state import active_sms, active_sms_lock, USER_LOCAL_TIMEZONE, UTC_TIMEZONE # type: ignore
from config import MAX_SMS_PER_MINUTE # type: ignore
from services.twilio_service import send_twilio_sms

# Global variable for current SMS session details (if any)
current_sms_session = {
    'campaign_id': None,
    'total_members': 0,
    'members_sent': 0,
    'start_time': None,
    'status': 'idle' # idle, in_progress, completed, cancelled
}
sms_session_lock = threading.Lock()

def update_sms_status(campaign_id, phone_number, status, details=None):
    with active_sms_lock:
        if campaign_id not in active_sms: active_sms[campaign_id] = {}
        # Always store timestamp as timezone-aware UTC datetime object
        timestamp_utc = datetime.now(UTC_TIMEZONE)
        
        current_data = active_sms[campaign_id].get(phone_number, {})
        current_status = current_data.get('status', '')

        status_hierarchy = {
            'unknown': 0, 'pending': 1, 'sending': 10, 'sent': 50,
            'delivered': 60, 'failed': 40, 'queued': 5, 'receiving': 70,
            'received': 80, 'opted_out': 90
        }
        current_significance = status_hierarchy.get(current_status, 0)
        new_significance = status_hierarchy.get(status, 0)

        if status == 'waiting': # Used for manual reset in API
            logging.info(f"Resetting SMS status for {phone_number} C:{campaign_id} from {current_status} to waiting")
            active_sms[campaign_id][phone_number] = {'status': status, 'details': details or 'Status reset', 'timestamp': timestamp_utc}
            return

        if not current_status:
            logging.info(f"Initial SMS status for {phone_number} C:{campaign_id}: -> {status} {details or ''}")
            active_sms[campaign_id][phone_number] = {'status': status, 'details': details, 'timestamp': timestamp_utc}
            return

        allow_update = False
        if new_significance > current_significance:
            allow_update = True
        elif new_significance == current_significance and status in ['sending', 'queued']:
            # Allow updates to same significance if it's about initial states being set
            allow_update = True
        elif status == 'failed' and current_status not in ['sent', 'delivered', 'opted_out']:
            # Allow failed to override most non-final states
            allow_update = True
        else:
            logging.info(f"SMS status update {phone_number} C:{campaign_id}: {current_status} -> {status} (Not updating to less significant or non-transitional status)")

        if allow_update:
            active_sms[campaign_id][phone_number] = {'status': status, 'details': details, 'timestamp': timestamp_utc}
            logging.info(f"Updated SMS status {phone_number} C:{campaign_id}: {current_status} -> {status} {details or ''}")


def is_sms_complete(phone, campaign_id):
    with active_sms_lock:
        if campaign_id not in active_sms or phone not in active_sms[campaign_id]:
            return True
        status = active_sms[campaign_id][phone].get('status', '')
        return status in ['sent', 'delivered', 'failed', 'opted_out']

# Background task for auto-sending scheduled SMS
def scheduled_sms_checker():
    logging.info("Starting scheduled SMS checker thread...")
    while True:
        logging.debug("Scheduled SMS checker running check...")
        ready_sms = []
        try:
            now_utc = datetime.now(UTC_TIMEZONE)
            logging.info(f"Checking SMS scheduled at or before UTC: {now_utc.isoformat()}")

            ready_sms = SMS.get_pending_sms_for_scheduling(now_utc)

            if ready_sms:
                logging.info(f"Found {len(ready_sms)} SMS ready to be marked for execution.")

                for sms_campaign in ready_sms:
                    sms_id_str = str(sms_campaign['id'])
                    logging.info(f"Processing SMS campaign ID {sms_id_str} to mark as 'ready'.")
                    try:
                        if SMS.update_status(sms_id_str, 'ready', 'Ready for execution'):
                            logging.info(f"SMS campaign {sms_id_str} successfully marked 'ready' in DB.")
                            thread = threading.Thread(
                                target=auto_execute_sms,
                                args=(
                                    sms_id_str,
                                    sms_campaign['message_content'],
                                    sms_campaign['group_filter'],
                                    sms_campaign['source_phone_number'],
                                    sms_campaign['webhook_url'] # Pass webhook URL
                                ),
                                daemon=True
                            )
                            thread.name = f"AutoSMSExec-{sms_id_str}"
                            thread.start()
                        else:
                            logging.warning(f"SMS campaign {sms_id_str} was not 'pending' or failed to update when trying to mark 'ready'. Skipping.")
                    except Exception as exec_err:
                        logging.error(f"Error starting execution thread for SMS {sms_id_str}: {exec_err}", exc_info=True)
            else:
                logging.debug("No pending SMS found ready for execution this cycle.")

        except Exception as e_main:
            logging.error(f"Unexpected error in SMS checker loop: {e_main}", exc_info=True)

        time.sleep(60) # Check every 60 seconds

def auto_execute_sms(sms_id, message_content, group_filter, source_phone_number, webhook_url):
    campaign_id = str(sms_id)
    logging.info(f"Auto-executing SMS campaign ID {campaign_id}")
    members = []
    
    with sms_session_lock:
        current_sms_session['campaign_id'] = campaign_id
        current_sms_session['total_members'] = 0 # Will update after fetching members
        current_sms_session['members_sent'] = 0
        current_sms_session['start_time'] = datetime.now(UTC_TIMEZONE)
        current_sms_session['status'] = 'in_progress'

    try:
        members = Member.get_members_for_sms(group_filter)

        if not members:
            logging.warning(f"No eligible members for SMS campaign {campaign_id}")
            SMS.update_status(campaign_id, 'completed', 'No eligible members')
            with sms_session_lock:
                current_sms_session['status'] = 'completed'
            return
        
        with sms_session_lock:
            current_sms_session['total_members'] = len(members)

        # Update campaign status
        if not SMS.update_status(campaign_id, 'in_progress', 'Execution started'):
            logging.warning(f"Failed to update SMS {campaign_id} status to in_progress at start of auto_execute_sms, or already in a final state.")
            sms_campaign_info = SMS.get_by_id(campaign_id)
            if sms_campaign_info and sms_campaign_info['status'] not in ['pending', 'ready']:
                logging.info(f"SMS {campaign_id} is already in status '{sms_campaign_info['status']}', aborting auto-execution.")
                with sms_session_lock:
                    current_sms_session['status'] = 'cancelled' # Or existing status
                return


        logging.info(f"Starting SMS campaign {campaign_id} to {len(members)} members")

        # Fetch Twilio settings
        twilio_account_sid = AppSetting.get_setting('twilio_account_sid')
        twilio_auth_token = AppSetting.get_setting('twilio_auth_token')
        
        if not twilio_account_sid or not twilio_auth_token:
            logging.error("Twilio credentials not found in settings. Cannot send SMS.")
            SMS.update_status(campaign_id, 'failed', 'Twilio credentials missing')
            with sms_session_lock:
                current_sms_session['status'] = 'failed'
            return

        for member in members:
            try:
                sms_campaign_info = SMS.get_by_id(campaign_id)
                current_campaign_status = sms_campaign_info.get('status') if sms_campaign_info else 'unknown'
                logging.debug(f"Campaign {campaign_id} current status before sending SMS to member {member.get('phone_number')}: {current_campaign_status}")

                if current_campaign_status == 'cancelled':
                    logging.info(f"SMS campaign {campaign_id} cancelled, stopping further SMS."); break
                if current_campaign_status != 'in_progress':
                    logging.warning(f"SMS campaign {campaign_id} is not 'in_progress' (status: {current_campaign_status}), stopping further SMS."); break

                phone_number = member['phone_number']
                member_id = member['id']

                logging.info(f"Sending SMS to {phone_number} C:{campaign_id}")
                update_sms_status(campaign_id, phone_number, 'sending', 'Initiating SMS send')

                # Pass campaign_id, member_id, and webhook_url to Twilio
                send_success, twilio_sid = send_twilio_sms(
                    to_number=phone_number,
                    from_number=source_phone_number,
                    message_body=message_content,
                    campaign_id=campaign_id, # Pass campaign_id as a parameter
                    member_id=member_id, # Pass member_id as a parameter
                    webhook_url=webhook_url # Pass the webhook URL
                )

                if send_success:
                    update_sms_status(campaign_id, phone_number, 'sent', f'SMS sent, Twilio SID: {twilio_sid}')
                    logging.info(f"SMS sent successfully to {phone_number} C:{campaign_id}. Twilio SID: {twilio_sid}")
                else:
                    update_sms_status(campaign_id, phone_number, 'failed', f'Failed to send SMS via Twilio. No Twilio SID received.')
                    logging.error(f"Failed to send SMS to {phone_number} C:{campaign_id}. No Twilio SID received.")
                
                with sms_session_lock:
                    current_sms_session['members_sent'] += 1

                # Use MAX_SMS_PER_MINUTE from config.py for rate limiting
                # Ensure MAX_SMS_PER_MINUTE is not zero to avoid division by zero
                if MAX_SMS_PER_MINUTE > 0:
                    time.sleep(60.0 / MAX_SMS_PER_MINUTE) 
                else:
                    time.sleep(1.0) # Default if no limit is set or invalid

            except Exception as sms_send_err:
                logging.error(f"Error sending SMS to {member.get('phone_number', 'N/A')}: {sms_send_err}", exc_info=True)
                if member.get('phone_number'):
                    update_sms_status(campaign_id, member['phone_number'], 'failed', f'Error: {sms_send_err}')

        # Start completion monitor only if SMS were attempted
        if members:
            threading.Thread(target=monitor_auto_sms_completion, args=(campaign_id, [m['phone_number'] for m in members]), daemon=True).start()

    except Exception as e:
        logging.error(f"Critical error in auto_execute_sms setup for campaign {campaign_id}: {e}", exc_info=True)
        SMS.update_status(campaign_id, 'cancelled', f'Execution setup error: {e}')
        with sms_session_lock:
            current_sms_session['status'] = 'failed'
    finally:
        with sms_session_lock:
            if current_sms_session['status'] == 'in_progress': # If not already set to completed/cancelled/failed
                current_sms_session['status'] = 'completed'


def monitor_auto_sms_completion(sms_id, phone_numbers):
    campaign_id = str(sms_id)
    try:
        logging.info(f"Starting SMS completion monitor C:{campaign_id} for {len(phone_numbers)} numbers.")
        max_wait_time, start_time, check_interval, consecutive_completed_checks, required_completed_checks = 1800, time.time(), 30, 0, 2
        time.sleep(check_interval)

        while time.time() - start_time < max_wait_time:
            sms_info = SMS.get_by_id(campaign_id)
            if sms_info and sms_info['status'] == 'cancelled':
                logging.info(f"Monitor C:{campaign_id} detected campaign cancelled, stopping monitor."); return
            if not sms_info or sms_info['status'] == 'completed' or sms_info['status'] == 'failed':
                logging.info(f"Monitor C:{campaign_id} detected campaign already completed/failed or not found, stopping monitor."); return

            active_sms_count = sum(1 for phone in phone_numbers if not is_sms_complete(phone, campaign_id))

            if active_sms_count == 0:
                consecutive_completed_checks += 1
                logging.info(f"Monitor C:{campaign_id}: No active SMS. Consecutive checks: {consecutive_completed_checks}/{required_completed_checks}")
                if consecutive_completed_checks >= required_completed_checks:
                    logging.info(f"Monitor C:{campaign_id}: All SMS appear completed. Marking campaign as completed in DB.")
                    final_sms_info = SMS.get_by_id(campaign_id)
                    if final_sms_info and final_sms_info['status'] not in ['completed', 'cancelled', 'failed']:
                        SMS.update_status(campaign_id, 'completed', 'All SMS processed')
                    else:
                        logging.info(f"Monitor C:{campaign_id}: Final check (timeout) found campaign already '{final_sms_info.get('status', 'N/A')}' or not found.")
                    return
            else:
                consecutive_completed_checks = 0
                logging.info(f"Monitor C:{campaign_id}: {active_sms_count} active SMS remain...")

            time.sleep(check_interval)

        logging.warning(f"Monitor C:{campaign_id}: Max wait time ({max_wait_time}s) reached. Marking campaign as completed due to monitor timeout.")
        final_sms_info_timeout = SMS.get_by_id(campaign_id)
        if final_sms_info_timeout and final_sms_info_timeout['status'] not in ['completed', 'cancelled', 'failed']:
            SMS.update_status(campaign_id, 'completed', 'Monitor timeout')
        else:
            logging.info(f"Monitor C:{campaign_id}: Final check (timeout) found campaign already '{final_sms_info_timeout.get('status', 'N/A')}' or not found.")
    except Exception as e:
        logging.error(f"Error in SMS completion monitor for C:{campaign_id}: {e}", exc_info=True)
        try:
            sms_info_on_error = SMS.get_by_id(campaign_id)
            if sms_info_on_error and sms_info_on_error['status'] == 'in_progress':
                SMS.update_status(campaign_id, 'completed_with_errors', f'Monitor error: {e}')
        except Exception as db_err:
            logging.error(f"Failed to update campaign {campaign_id} status after monitor error: {db_err}")