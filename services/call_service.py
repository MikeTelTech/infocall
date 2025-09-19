# services/call_service.py - ENHANCED DEBUG VERSION WITH SOLUTION 1
import os
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

# Models
from models.call import Call
from models.announcement import Announcement
from models.member import Member

# Corrected Imports to resolve circular dependency
from app_state import active_calls, active_calls_lock, USER_LOCAL_TIMEZONE, UTC_TIMEZONE

# Asterisk service components
import services.asterisk_service as asterisk_service

# DB utilities
from utils.db import get_db_cursor

# ENHANCED DEBUG: Add a call tracking dictionary for debugging
call_debug_tracker = {}
call_debug_lock = threading.Lock()

# SOLUTION 1: Pre-correlation Storage
pending_correlations = {}
pending_correlations_lock = threading.Lock()

def register_pending_call(phone_number, campaign_id, action_id):
    """Register a call before originating to enable early correlation"""
    with pending_correlations_lock:
        pending_correlations[phone_number] = {
            'campaign_id': campaign_id,
            'action_id': action_id,
            'timestamp': datetime.now(UTC_TIMEZONE)
        }
        debug_log_call_state(campaign_id, phone_number, "PENDING_REGISTERED", f"ActionID: {action_id}")

def get_pending_campaign(phone_number):
    """Get campaign ID for a pending call by phone number"""
    with pending_correlations_lock:
        pending = pending_correlations.get(phone_number)
        if pending:
            # Clean up old entries (older than 2 minutes)
            if (datetime.now(UTC_TIMEZONE) - pending['timestamp']).total_seconds() > 120:
                del pending_correlations[phone_number]
                return None
            return pending['campaign_id']
        return None

def clear_pending_call(phone_number):
    """Remove pending call after successful correlation"""
    with pending_correlations_lock:
        if phone_number in pending_correlations:
            del pending_correlations[phone_number]

def debug_log_call_state(campaign_id, phone_number, action, details=""):
    """Enhanced debug logging for call state tracking"""
    with call_debug_lock:
        key = f"{campaign_id}_{phone_number}"
        if key not in call_debug_tracker:
            call_debug_tracker[key] = []
        
        timestamp = datetime.now(UTC_TIMEZONE)
        call_debug_tracker[key].append({
            'timestamp': timestamp,
            'action': action,
            'details': details
        })
        
        # Keep only last 50 entries per call to prevent memory bloat
        if len(call_debug_tracker[key]) > 50:
            call_debug_tracker[key] = call_debug_tracker[key][-50:]
    
    logging.info(f"🔍 CALL_DEBUG C:{campaign_id} P:{phone_number} | {action} | {details}")

def get_call_debug_history(campaign_id, phone_number):
    """Get debug history for a specific call"""
    with call_debug_lock:
        key = f"{campaign_id}_{phone_number}"
        return call_debug_tracker.get(key, []).copy()

# Helper function to find actual campaign ID for events
def find_actual_campaign_id(phone_number):
    debug_log_call_state("UNKNOWN", phone_number, "LOOKUP_CAMPAIGN", "Starting campaign lookup")
    
    # SOLUTION 1: First check pending correlations
    pending_campaign = get_pending_campaign(phone_number)
    if pending_campaign:
        debug_log_call_state(pending_campaign, phone_number, "FOUND_IN_PENDING", "From pending correlations")
        return pending_campaign
    
    try:
        with active_calls_lock:
            for campaign_id, campaign_calls in active_calls.items():
                if campaign_id != 'default' and phone_number in campaign_calls:
                    status = campaign_calls[phone_number].get('status')
                    if status in ['dialing', 'ringing', 'answered']:
                        debug_log_call_state(campaign_id, phone_number, "FOUND_IN_MEMORY", f"Status: {status}")
                        return campaign_id
            
            # Fallback to database lookup
            with get_db_cursor(dictionary=True) as (cursor, connection):
                query = """
                    SELECT sc.id FROM scheduled_calls sc JOIN members m ON m.phone_number = %s 
                    LEFT JOIN member_groups mg ON m.id = mg.member_id
                    WHERE sc.status IN ('in_progress', 'ready') 
                    AND (sc.group_filter IS NULL OR sc.group_filter = mg.group_id)
                    ORDER BY CASE WHEN sc.status = 'in_progress' THEN 1 WHEN sc.status = 'ready' THEN 2 ELSE 3 END, 
                    sc.scheduled_datetime DESC LIMIT 1
                """
                cursor.execute(query, (phone_number,))
                result = cursor.fetchone()
                if result:
                    campaign_id = str(result['id'])
                    debug_log_call_state(campaign_id, phone_number, "FOUND_IN_DB", f"Status: in_progress/ready")
                    return campaign_id

    except Exception as e:
        debug_log_call_state("ERROR", phone_number, "LOOKUP_FAILED", str(e))
        
    debug_log_call_state("DEFAULT", phone_number, "NO_CAMPAIGN_FOUND", "Defaulting to 'default'")
    return 'default'

# Global dictionary to store DTMF state for each active call's phone number
_dtmf_state_buffer = {}
_dtmf_state_lock = threading.Lock()
DTMF_TIMEOUT_SECONDS = 2.0

# Helper functions for forced correlation

def find_call_by_action_id(action_id):
    """Find campaign and phone by ActionID - more aggressive search"""
    with active_calls_lock:
        for campaign_id, calls in active_calls.items():
            for phone_number, call_data in calls.items():
                if call_data.get('action_id') == action_id:
                    return campaign_id, phone_number
    return None, None

def process_originate_response(event, campaign_id, phone_number):
    """Process OriginateResponse event directly"""
    response = event.get('Response')
    channel = event.get('Channel')
    reason = event.get('Reason')
    originate_uniqueid = event.get('Uniqueid')
    
    debug_log_call_state(campaign_id, phone_number, "FORCED_ORIGINATE_RESPONSE", 
                       f"Response: {response}, Channel: {channel}")
    
    if response == 'Success' and originate_uniqueid:
        with active_calls_lock:
            if campaign_id in active_calls and phone_number in active_calls[campaign_id]:
                active_calls[campaign_id][phone_number]['uniqueid'] = originate_uniqueid
                debug_log_call_state(campaign_id, phone_number, "FORCED_UNIQUEID_STORED", f"UniqueID: {originate_uniqueid}")
        
        clear_pending_call(phone_number)
        asterisk_service.update_call_status(campaign_id, phone_number, 'answered', 
                                          f"Call connected: {channel}", 
                                          uniqueid=originate_uniqueid, action_id=event.get('ActionID'))
    elif response == 'Failure':
        clear_pending_call(phone_number)
        asterisk_service.update_call_status(campaign_id, phone_number, 'rejected', 
                                          f"Originate failed: {reason}", 
                                          uniqueid=originate_uniqueid, action_id=event.get('ActionID'))

# Replace the correlation logic in direct_event_handler_with_optout() function

def direct_event_handler_with_optout(event):
    event_type = event.get('Event', 'UNKNOWN')
    event_uniqueid = event.get('Uniqueid')
    event_actionid = event.get('ActionID')
    
    # Log ALL events for debugging
    logging.info(f"🎯 AMI_EVENT: {event_type} | UniqueID: {event_uniqueid or 'N/A'} | ActionID: {event_actionid or 'N/A'}")
    
    # CRITICAL FIX: Special handling for OriginateResponse
    if event_type == 'OriginateResponse' and event_actionid:
        asterisk_service.log_ami_debug("ORIGINATE_RESPONSE_RECEIVED", f"ActionID: {event_actionid}")
        
        # Force correlation for OriginateResponse
        campaign_id, phone_number = find_call_by_action_id(event_actionid)
        if campaign_id and phone_number:
            asterisk_service.log_ami_debug("FORCED_CORRELATION_SUCCESS", f"C:{campaign_id} P:{phone_number}")
            # Process the OriginateResponse immediately
            process_originate_response(event, campaign_id, phone_number)
            return
        else:
            asterisk_service.log_ami_debug("FORCED_CORRELATION_FAILED", f"ActionID: {event_actionid}")
    
    campaign_id = None
    phone_number = None

    # Step 1: For OriginateResponse events, ALWAYS use ActionID first (most reliable)
    if event_type == 'OriginateResponse' and event_actionid:
        with active_calls_lock:
            for cid, calls in active_calls.items():
                for pnum, call_data in calls.items():
                    if call_data.get('action_id') == event_actionid:
                        campaign_id = cid
                        phone_number = pnum
                        debug_log_call_state(campaign_id, phone_number, "CORR_BY_ACTIONID_ORIGINATE", f"Event: {event_type}")
                        break
                if campaign_id: break

    # Step 2: For other events, try UniqueID correlation (but only for active campaigns)
    elif event_uniqueid and not campaign_id:
        with active_calls_lock:
            for cid, calls in active_calls.items():
                # Skip campaigns that are not currently active
                try:
                    call_info = Call.get_by_id(int(cid))
                    if not call_info or call_info.get('status') not in ['in_progress', 'ready']:
                        continue
                except:
                    continue
                    
                for pnum, call_data in calls.items():
                    if (call_data.get('uniqueid') == event_uniqueid and 
                        call_data.get('status') in ['dialing', 'ringing', 'answered']):
                        campaign_id = cid
                        phone_number = pnum
                        debug_log_call_state(campaign_id, phone_number, "CORR_BY_UNIQUEID_ACTIVE", f"Event: {event_type}")
                        break
                if campaign_id: break

    # Step 3: Try ActionID correlation for non-OriginateResponse events
    if not campaign_id and event_actionid:
        with active_calls_lock:
            for cid, calls in active_calls.items():
                for pnum, call_data in calls.items():
                    if (call_data.get('action_id') == event_actionid and 
                        call_data.get('status') in ['dialing', 'ringing', 'pending']):
                        campaign_id = cid
                        phone_number = pnum
                        debug_log_call_state(campaign_id, phone_number, "CORR_BY_ACTIONID", f"Event: {event_type}")
                        break
                if campaign_id: break
    
    # Step 4: Extract phone number from event if not found
    if not phone_number:
        if 'CallerIDNum' in event and event['CallerIDNum'].isdigit(): 
            phone_number = event['CallerIDNum']
            debug_log_call_state(campaign_id or "UNKNOWN", phone_number, "PHONE_FROM_CALLERIDNUM", f"Event: {event_type}")
        elif 'ConnectedLineNum' in event and event['ConnectedLineNum'].isdigit(): 
            phone_number = event['ConnectedLineNum']
            debug_log_call_state(campaign_id or "UNKNOWN", phone_number, "PHONE_FROM_CONNECTEDLINENUM", f"Event: {event_type}")
        elif 'Exten' in event and event['Exten'].isdigit(): 
            phone_number = event['Exten']
            debug_log_call_state(campaign_id or "UNKNOWN", phone_number, "PHONE_FROM_EXTEN", f"Event: {event_type}")
        elif 'Channel' in event and 'Local/' in event['Channel']: 
            phone_number = event['Channel'].split('Local/', 1)[1].split('@', 1)[0]
            debug_log_call_state(campaign_id or "UNKNOWN", phone_number, "PHONE_FROM_CHANNEL", f"Event: {event_type}")

    # Step 5: Extract campaign from event variables (fallback)
    if not campaign_id:
        for key in event:
            if key == 'CAMPAIGN_ID': 
                campaign_id = event[key]
                debug_log_call_state(campaign_id, phone_number or "UNKNOWN", "CORR_BY_VAR_DIRECT", f"Event: {event_type}")
                break
            elif key in ['Variable', 'ChanVariable']:
                variables = event[key].split(',')
                for var in variables:
                    if var.startswith('CAMPAIGN_ID='): 
                        campaign_id = var.split('=', 1)[1].strip()
                        debug_log_call_state(campaign_id, phone_number or "UNKNOWN", "CORR_BY_VAR_PARSED", f"Event: {event_type}")
                        break
                if campaign_id: break
            elif key == 'UserField' and not campaign_id and event[key].isdigit(): 
                campaign_id = event[key]
                debug_log_call_state(campaign_id, phone_number or "UNKNOWN", "CORR_BY_USERFIELD", f"Event: {event_type}")

    # If still no phone_number or campaign_id, skip processing
    if not phone_number or not campaign_id:
        if event_type not in ['RTCPReceived', 'RTCPSent', 'ExtensionStatus', 'AGIExec', 'VarSet', 'Bridge']:
            logging.debug(f"🚫 SKIPPING_EVENT: {event_type} | Missing phone/campaign | Phone: {phone_number or 'N/A'} | Campaign: {campaign_id or 'N/A'}")
        return

    # Final validation - make sure this is an active campaign
    if campaign_id != 'default':
        try:
            call_info = Call.get_by_id(int(campaign_id))
            if not call_info or call_info.get('status') not in ['in_progress', 'ready', 'pending']:
                debug_log_call_state(campaign_id, phone_number, "INACTIVE_CAMPAIGN_SKIP", f"Status: {call_info.get('status') if call_info else 'NOT_FOUND'}")
                return
        except Exception as e:
            debug_log_call_state(campaign_id, phone_number, "CAMPAIGN_CHECK_ERROR", str(e))
            return

    # Final fallback for campaign_id
    if campaign_id == 'default' or not campaign_id.isdigit():
        original_campaign_id = campaign_id
        campaign_id_from_fn = find_actual_campaign_id(phone_number)
        if campaign_id_from_fn != 'default':
            campaign_id = campaign_id_from_fn
            debug_log_call_state(campaign_id, phone_number, "CAMPAIGN_RESOLVED", f"From: {original_campaign_id} To: {campaign_id}")
        else:
            debug_log_call_state(campaign_id, phone_number, "UNMANAGED_CALL", f"Event: {event_type}")
            return

    # Log current active_calls state for this campaign/phone
    with active_calls_lock:
        current_state = active_calls.get(campaign_id, {}).get(phone_number, {})
        debug_log_call_state(campaign_id, phone_number, "CURRENT_STATE", 
                           f"Status: {current_state.get('status', 'N/A')}, "
                           f"UniqueID: {current_state.get('uniqueid', 'N/A')}, "
                           f"ActionID: {current_state.get('action_id', 'N/A')}")

    # Process event based on type
    if event_type == 'Newstate':
        state = event.get('ChannelStateDesc')
        debug_log_call_state(campaign_id, phone_number, "NEWSTATE_EVENT", f"State: {state}")
        
        if state == 'Ringing': 
            asterisk_service.update_call_status(campaign_id, phone_number, 'ringing', "Phone is ringing", 
                                              uniqueid=event_uniqueid, action_id=event_actionid)
        elif state == 'Up': 
            asterisk_service.update_call_status(campaign_id, phone_number, 'answered', "Call answered", 
                                              uniqueid=event_uniqueid, action_id=event_actionid)
    
    elif event_type == 'OriginateResponse':
        response = event.get('Response')
        channel = event.get('Channel')
        reason = event.get('Reason')
        originate_uniqueid = event.get('Uniqueid')
        
        debug_log_call_state(campaign_id, phone_number, "ORIGINATE_RESPONSE", 
                           f"Response: {response}, Channel: {channel}, Reason: {reason}, UniqueID: {originate_uniqueid}")
        
        if response == 'Success' and originate_uniqueid:
            # Store the Uniqueid when OriginateResponse is successful
            with active_calls_lock:
                if campaign_id in active_calls and phone_number in active_calls[campaign_id]:
                    active_calls[campaign_id][phone_number]['uniqueid'] = originate_uniqueid
                    debug_log_call_state(campaign_id, phone_number, "UNIQUEID_STORED", f"UniqueID: {originate_uniqueid}")
                else:
                    debug_log_call_state(campaign_id, phone_number, "UNIQUEID_STORE_FAILED", "Call not in active_calls")
            
            # SOLUTION 1: Clear pending correlation after successful OriginateResponse
            clear_pending_call(phone_number)
            
            current_status = active_calls.get(campaign_id, {}).get(phone_number, {}).get('status')
            if current_status == 'dialing' or current_status == 'pending':
                asterisk_service.update_call_status(campaign_id, phone_number, 'dialing', 
                                                  f"Originate successful, channel: {channel}", 
                                                  uniqueid=originate_uniqueid, action_id=event_actionid)
        elif response == 'Failure':
            # SOLUTION 1: Clear pending correlation on failure too
            clear_pending_call(phone_number)
            asterisk_service.update_call_status(campaign_id, phone_number, 'rejected', 
                                              f"Originate failed: {reason}", 
                                              uniqueid=originate_uniqueid, action_id=event_actionid)

    elif event_type == 'Hangup':
        cause = event.get('Cause-txt', 'Unknown')
        debug_log_call_state(campaign_id, phone_number, "HANGUP_EVENT", f"Cause: {cause}")
        
        # Check current status from active_calls
        current_status_in_memory = active_calls.get(campaign_id, {}).get(phone_number, {}).get('status')
        
        final_status = None
        details = None
        
        if current_status_in_memory == 'opted_out':
            final_status = 'opted_out'
            details = "Member opted out (0# pressed)"
        elif current_status_in_memory == 'aborted':
            final_status = 'aborted'
            details = "Call aborted by admin"
        elif 'user busy' in cause.lower():
            final_status = 'busy'
            details = f"Line busy: {cause}"
        elif 'no answer' in cause.lower() or 'timeout' in cause.lower():
            final_status = 'noanswer'
            details = f"No answer/timeout: {cause}"
        elif 'rejected' in cause.lower() or 'congestion' in cause.lower() or 'unallocated' in cause.lower():
            final_status = 'rejected'
            details = f"Call rejected/failed: {cause}"
        else:
            final_status = 'completed'
            details = f"Call completed: {cause}"
        
        debug_log_call_state(campaign_id, phone_number, "HANGUP_STATUS_DECISION", 
                           f"Final: {final_status}, Current: {current_status_in_memory}")
        
        if final_status:
            asterisk_service.update_call_status(campaign_id, phone_number, final_status, details, 
                                              uniqueid=event_uniqueid, action_id=event_actionid)

    elif event_type == 'DTMFEnd':
        digit = event.get('Digit')
        dtmf_timestamp = datetime.now(UTC_TIMEZONE)
        
        debug_log_call_state(campaign_id, phone_number, "DTMF_EVENT", f"Digit: {digit}")
        asterisk_service.update_call_status(campaign_id, phone_number, 'dtmf_received', f"Pressed {digit}", 
                                          uniqueid=event_uniqueid, action_id=event_actionid)

        with _dtmf_state_lock:
            current_dtmf_state = _dtmf_state_buffer.get(phone_number, {'buffer': '', 'timestamp': None})
            
            if not current_dtmf_state['timestamp'] or (dtmf_timestamp - current_dtmf_state['timestamp']).total_seconds() > DTMF_TIMEOUT_SECONDS:
                current_dtmf_state['buffer'] = digit
                current_dtmf_state['timestamp'] = dtmf_timestamp
                debug_log_call_state(campaign_id, phone_number, "DTMF_BUFFER_RESET", f"Buffer: {digit}")
            else:
                current_dtmf_state['buffer'] += digit
                current_dtmf_state['timestamp'] = dtmf_timestamp
                debug_log_call_state(campaign_id, phone_number, "DTMF_BUFFER_APPEND", f"Buffer: {current_dtmf_state['buffer']}")

            _dtmf_state_buffer[phone_number] = current_dtmf_state

            # Check for opt-out sequence (0#)
            if current_dtmf_state['buffer'] == '0#':
                debug_log_call_state(campaign_id, phone_number, "OPTOUT_DETECTED", "Sequence: 0#")
                try:
                    member_id = None
                    from utils.db import get_db_cursor
                    with get_db_cursor(dictionary=False) as (cursor, connection):
                        cursor.execute("SELECT id FROM members WHERE phone_number = %s", (phone_number,))
                        member_result = cursor.fetchone()
                        if member_result:
                            member_id = member_result[0]

                    if member_id:
                        from models.member import Member
                        Member.update_remove_from_call_status(member_id, 1)
                        asterisk_service.update_call_status(campaign_id, phone_number, 'opted_out', 
                                                          "Member pressed 0# to opt out", 
                                                          uniqueid=event_uniqueid, action_id=event_actionid)
                        debug_log_call_state(campaign_id, phone_number, "OPTOUT_PROCESSED", f"Member ID: {member_id}")
                        
                        current_dtmf_state['buffer'] = ''
                        _dtmf_state_buffer[phone_number] = current_dtmf_state
                    else:
                        debug_log_call_state(campaign_id, phone_number, "OPTOUT_FAILED", "Member not found")
                except Exception as e:
                    debug_log_call_state(campaign_id, phone_number, "OPTOUT_ERROR", str(e))

# Rest of the functions remain the same but with added debug logging...
# [Continue with existing functions but add debug_log_call_state calls at key points]

def auto_execute_call(call_id, announcement_id, group_filter, caller_id_name):
    campaign_id = str(call_id)
    debug_log_call_state(campaign_id, "ALL", "AUTO_EXECUTE_START", f"Announcement: {announcement_id}")
    
    members = []
    announcement_file = None
    try:
        announcement_file = Announcement.get_filename_by_id(announcement_id)
        if not announcement_file:
            debug_log_call_state(campaign_id, "ALL", "ANNOUNCEMENT_NOT_FOUND", f"ID: {announcement_id}")
            Call.update_status(campaign_id, 'cancelled', 'Announcement not found')
            return

        members = Member.get_members_for_call(group_filter)
        debug_log_call_state(campaign_id, "ALL", "MEMBERS_FOUND", f"Count: {len(members)}")

        if not Call.update_status(campaign_id, 'in_progress', 'Execution started'):
            debug_log_call_state(campaign_id, "ALL", "STATUS_UPDATE_FAILED", "Could not set in_progress")
            current_call_info = Call.get_by_id(campaign_id)
            if current_call_info and current_call_info['status'] not in ['pending', 'ready']:
                debug_log_call_state(campaign_id, "ALL", "STATUS_CONFLICT", f"Current: {current_call_info['status']}")
                return

        if not members:
            debug_log_call_state(campaign_id, "ALL", "NO_MEMBERS", "Campaign completed - no eligible members")
            Call.update_status(campaign_id, 'completed', 'No eligible members')
            return

        debug_log_call_state(campaign_id, "ALL", "STARTING_CALLS", f"To {len(members)} members")
        
        for member in members:
            try:
                phone_number = member['phone_number']
                member_id = member['id']
                
                debug_log_call_state(campaign_id, phone_number, "MEMBER_CALL_START", f"Member ID: {member_id}")
                
                # Check campaign status before each call
                call_campaign_info = Call.get_by_id(campaign_id)
                current_campaign_status = call_campaign_info.get('status') if call_campaign_info else 'unknown'
                
                if current_campaign_status == 'cancelled':
                    debug_log_call_state(campaign_id, phone_number, "CAMPAIGN_CANCELLED", "Stopping calls")
                    break
                if current_campaign_status != 'in_progress':
                    debug_log_call_state(campaign_id, phone_number, "CAMPAIGN_STATUS_CHANGED", f"Status: {current_campaign_status}")
                    break

                # Ensure AMI client
                if not asterisk_service.ami_client_instance or not asterisk_service.ami_client_instance.connected:
                    if not asterisk_service.initialize_ami_client():
                        debug_log_call_state(campaign_id, phone_number, "AMI_INIT_FAILED", "Cannot proceed")
                        break

                # Generate ActionID
                action_id = str(uuid.uuid4())
                debug_log_call_state(campaign_id, phone_number, "ACTION_ID_GENERATED", action_id)
                
                # SOLUTION 1: Register pending call before originate
                register_pending_call(phone_number, campaign_id, action_id)
                
                # Store in active_calls immediately
                with active_calls_lock:
                    if campaign_id not in active_calls:
                        active_calls[campaign_id] = {}
                    active_calls[campaign_id][phone_number] = {
                        'status': 'dialing',
                        'details': 'Auto-initiated',
                        'timestamp': datetime.now(UTC_TIMEZONE),
                        'action_id': action_id,
                        'uniqueid': None,
                        'finalized_in_memory': False
                    }

                debug_log_call_state(campaign_id, phone_number, "STORED_IN_ACTIVE_CALLS", f"ActionID: {action_id}")

                upload_dir = '/var/www/html/infocall/uploads'
                sound_path = f"{upload_dir}/{os.path.splitext(announcement_file)[0]}"
                variables = [
                    f"CAMPAIGN_ID={campaign_id}", 
                    f"DIAL_NUMBER={phone_number}", 
                    f"MEMBER_ID={member_id}", 
                    f"FORCE_CALLER_ID={caller_id_name}"
                ]

                debug_log_call_state(campaign_id, phone_number, "ORIGINATE_PARAMS", 
                                   f"Sound: {sound_path}, Variables: {','.join(variables)}")

                if asterisk_service.ami_client_instance:
                    action_success = asterisk_service.ami_client_instance.send_action(
                        'Originate',
                        Channel=f'Local/{phone_number}@from-internal',
                        Application='Playback', 
                        Data=sound_path,
                        CallerID=f'"{caller_id_name}" <{phone_number}>',
                        Async='true', 
                        Timeout='45000',
                        UserField=campaign_id, 
                        Variable=','.join(variables),
                        ActionID=action_id
                    )
                    
                    if action_success:
                        debug_log_call_state(campaign_id, phone_number, "ORIGINATE_SENT", "Success")
                    else:
                        debug_log_call_state(campaign_id, phone_number, "ORIGINATE_FAILED", "AMI send_action failed")
                        asterisk_service.update_call_status(campaign_id, phone_number, 'rejected', 'Failed AMI originate', action_id=action_id)
                else:
                    debug_log_call_state(campaign_id, phone_number, "AMI_CLIENT_NONE", "Cannot originate")
                    asterisk_service.update_call_status(campaign_id, phone_number, 'rejected', 'AMI client unavailable', action_id=action_id)

                time.sleep(5.0)  # Rate limiting
                
            except Exception as call_err:
                debug_log_call_state(campaign_id, member.get('phone_number', 'UNKNOWN'), "CALL_ERROR", str(call_err))
                if member.get('phone_number'):
                    asterisk_service.update_call_status(campaign_id, member['phone_number'], 'rejected', f'Error: {call_err}')

        # Start monitors
        if members:
            debug_log_call_state(campaign_id, "ALL", "STARTING_MONITORS", f"For {len(members)} members")
            threading.Thread(target=monitor_auto_call_completion, args=(campaign_id, [m['phone_number'] for m in members]), daemon=True).start()
            threading.Thread(target=auto_dial_watchdog, args=(campaign_id, 600), daemon=True).start()

    except Exception as e:
        debug_log_call_state(campaign_id, "ALL", "CRITICAL_ERROR", str(e))
        Call.update_status(campaign_id, 'cancelled', f'Execution setup error: {e}')

# Background task for auto-dial watchdog (keep existing)
def auto_dial_watchdog(call_id, timeout=300):
    time.sleep(timeout)
    try:
        call_info = Call.get_by_id(call_id)
        if call_info and call_info['status'] == 'in_progress':
            debug_log_call_state(str(call_id), "ALL", "WATCHDOG_TIMEOUT", f"After {timeout}s")
            Call.update_status(call_id, 'completed', 'Watchdog timeout')
    except Exception as e: 
        debug_log_call_state(str(call_id), "ALL", "WATCHDOG_ERROR", str(e))

# Keep existing detect_stuck_calls, scheduled_call_checker, and monitor_auto_call_completion functions
# but add debug_log_call_state calls at key points...

def detect_stuck_calls():
    cleanup_stale_active_calls() 
    with active_calls_lock:
        now_utc = datetime.now(UTC_TIMEZONE)
        campaign_ids_to_check = [cid for cid in active_calls.keys() if cid != 'default']
        active_db_campaigns = set()

        if campaign_ids_to_check:
            try:
                active_db_campaigns = Call.get_active_campaign_ids(campaign_ids_to_check)
            except Exception as e:
                logging.error(f"General error checking campaigns: {e}")
                return

        for campaign_id, calls in list(active_calls.items()):
            if campaign_id == 'default' or campaign_id not in active_db_campaigns: 
                continue
            for phone_number, status_data in list(calls.items()):
                status = status_data.get('status', '')
                timestamp_utc = status_data.get('timestamp')

                if status not in ['ringing', 'dialing'] or not isinstance(timestamp_utc, datetime): 
                    continue
                
                time_diff = (now_utc - timestamp_utc).total_seconds()

                if time_diff > 60:  # Stuck threshold
                    debug_log_call_state(campaign_id, phone_number, "STUCK_CALL_DETECTED", f"In {status} for {time_diff:.1f}s")

                    success, output = asterisk_service.run_asterisk_command('core show channels')
                    channel_exists = False
                    if success:
                        for line in output.splitlines():
                            channel_uniqueid = status_data.get('uniqueid')
                            if phone_number in line and ('Up' in line or 'Ringing' in line):
                                if channel_uniqueid and channel_uniqueid in line:
                                    channel_exists = True
                                    break
                                elif not channel_uniqueid:
                                    channel_exists = True
                                    break
                    
                    debug_log_call_state(campaign_id, phone_number, "CHANNEL_CHECK", f"Exists: {channel_exists}")

                    if not channel_exists:
                        asterisk_service.update_call_status(campaign_id, phone_number, 'noanswer', 
                                                          'Call timed out (channel gone or uniqueid mismatch)', 
                                                          uniqueid=status_data.get('uniqueid'), 
                                                          action_id=status_data.get('action_id'))
                        debug_log_call_state(campaign_id, phone_number, "STUCK_CALL_RESET", "Marked as noanswer")

def scheduled_call_checker():
    logging.info("Starting scheduled call checker thread...")
    while True:
        logging.debug("Scheduled call checker running check...")
        ready_calls = []
        try:
            now_utc = datetime.now(UTC_TIMEZONE)
            logging.info(f"Checking calls scheduled at or before UTC: {now_utc.isoformat()}")

            ready_calls = Call.get_pending_calls_for_scheduling(now_utc)

            if ready_calls:
                logging.info(f"Found {len(ready_calls)} calls ready to be marked for execution.")

                for call in ready_calls:
                    call_id_str = str(call['id'])
                    debug_log_call_state(call_id_str, "ALL", "SCHEDULER_FOUND", "Marking as ready")
                    
                    try:
                        if Call.update_status(call_id_str, 'ready', 'Ready for execution'):
                            debug_log_call_state(call_id_str, "ALL", "MARKED_READY", "Starting execution thread")
                            thread = threading.Thread(
                                target=auto_execute_call,
                                args=(
                                    call_id_str,
                                    call['announcement_id'],
                                    call['group_filter'],
                                    call['caller_id_name']
                                ),
                                daemon=True
                            )
                            thread.name = f"AutoCallExec-{call_id_str}"
                            thread.start()
                        else:
                            debug_log_call_state(call_id_str, "ALL", "MARK_READY_FAILED", "Not pending or update failed")
                    except Exception as exec_err:
                        debug_log_call_state(call_id_str, "ALL", "EXEC_THREAD_ERROR", str(exec_err))
            else:
                logging.debug("No pending calls found ready for execution this cycle.")

            try:
                logging.debug("Running stuck call detection...")
                detect_stuck_calls()
            except Exception as stuck_err:
                logging.error(f"Error during periodic stuck call detection: {stuck_err}", exc_info=True)

        except Exception as e_main:
            logging.error(f"Unexpected error in call checker loop: {e_main}", exc_info=True)

        time.sleep(60)
        
def cleanup_stale_active_calls():
    """Clean up stale entries in active_calls dictionary"""
    with active_calls_lock:
        campaigns_to_remove = []
        
        for campaign_id, calls in list(active_calls.items()):
            if campaign_id == 'default':
                continue
                
            try:
                # Check if campaign is still active in database
                call_info = Call.get_by_id(int(campaign_id))
                if not call_info or call_info.get('status') in ['completed', 'cancelled', 'failed']:
                    debug_log_call_state(campaign_id, "ALL", "CLEANUP_STALE_CAMPAIGN", f"DB Status: {call_info.get('status') if call_info else 'NOT_FOUND'}")
                    campaigns_to_remove.append(campaign_id)
                    continue
                    
                # Clean up individual calls that are finalized and old
                now_utc = datetime.now(UTC_TIMEZONE)
                phones_to_remove = []
                
                for phone_number, call_data in calls.items():
                    if call_data.get('finalized_in_memory', False):
                        timestamp = call_data.get('timestamp')
                        if isinstance(timestamp, datetime):
                            # Remove finalized calls older than 5 minutes
                            if (now_utc - timestamp).total_seconds() > 300:
                                phones_to_remove.append(phone_number)
                                debug_log_call_state(campaign_id, phone_number, "CLEANUP_OLD_FINALIZED", f"Age: {(now_utc - timestamp).total_seconds()}s")
                
                # Remove old finalized calls
                for phone in phones_to_remove:
                    del active_calls[campaign_id][phone]
                    
                # If campaign has no active calls, remove it
                if not active_calls[campaign_id]:
                    campaigns_to_remove.append(campaign_id)
                    debug_log_call_state(campaign_id, "ALL", "CLEANUP_EMPTY_CAMPAIGN", "No active calls remaining")
                    
            except Exception as e:
                debug_log_call_state(campaign_id, "ALL", "CLEANUP_ERROR", str(e))
                campaigns_to_remove.append(campaign_id)
        
        # Remove stale campaigns
        for campaign_id in campaigns_to_remove:
            if campaign_id in active_calls:
                del active_calls[campaign_id]
                logging.info(f"🧹 CLEANUP: Removed stale campaign {campaign_id} from active_calls")


        

def monitor_auto_call_completion(call_id, phone_numbers):
    campaign_id = str(call_id)
    try:
        debug_log_call_state(campaign_id, "ALL", "MONITOR_START", f"For {len(phone_numbers)} numbers")
        max_wait_time, start_time, check_interval = 3600, time.time(), 15
        consecutive_completed_checks, required_completed_checks = 0, 2
        time.sleep(check_interval)

        while time.time() - start_time < max_wait_time:
            call_info = Call.get_by_id(campaign_id)
            if call_info and call_info['status'] == 'cancelled':
                debug_log_call_state(campaign_id, "ALL", "MONITOR_CANCELLED", "Campaign cancelled, stopping")
                return
            if not call_info or call_info['status'] == 'completed':
                debug_log_call_state(campaign_id, "ALL", "MONITOR_ALREADY_COMPLETE", "Stopping monitor")
                return

            active_call_count = sum(1 for phone in phone_numbers if not asterisk_service.is_call_complete(phone, campaign_id))
            
            debug_log_call_state(campaign_id, "ALL", "MONITOR_CHECK", f"Active calls: {active_call_count}")

            if active_call_count == 0:
                consecutive_completed_checks += 1
                debug_log_call_state(campaign_id, "ALL", "MONITOR_ALL_COMPLETE", f"Check {consecutive_completed_checks}/{required_completed_checks}")
                
                if consecutive_completed_checks >= required_completed_checks:
                    debug_log_call_state(campaign_id, "ALL", "MONITOR_MARKING_COMPLETE", "All calls processed")
                    final_call_info = Call.get_by_id(campaign_id)
                    if final_call_info and final_call_info['status'] not in ['completed', 'cancelled']:
                        Call.update_status(campaign_id, 'completed', 'All calls processed')
                        debug_log_call_state(campaign_id, "ALL", "MONITOR_COMPLETED", "DB status updated")
                    else:
                        debug_log_call_state(campaign_id, "ALL", "MONITOR_ALREADY_FINAL", f"Status: {final_call_info.get('status', 'N/A') if final_call_info else 'N/A'}")
                    return
            else:
                consecutive_completed_checks = 0

            time.sleep(check_interval)

        debug_log_call_state(campaign_id, "ALL", "MONITOR_TIMEOUT", f"After {max_wait_time}s")
        final_call_info_timeout = Call.get_by_id(campaign_id)
        if final_call_info_timeout and final_call_info_timeout['status'] not in ['completed', 'cancelled']:
            Call.update_status(campaign_id, 'failed', 'Monitor timeout or error')
            debug_log_call_state(campaign_id, "ALL", "MONITOR_TIMEOUT_FAILED", "Marked as failed")

    except Exception as e:
        debug_log_call_state(campaign_id, "ALL", "MONITOR_ERROR", str(e))
        try:
            call_info_on_error = Call.get_by_id(campaign_id)
            if call_info_on_error and call_info_on_error['status'] == 'in_progress':
                Call.update_status(campaign_id, 'failed', f'Monitor error: {e}')
        except Exception as db_err:
            debug_log_call_state(campaign_id, "ALL", "MONITOR_DB_ERROR", str(db_err))