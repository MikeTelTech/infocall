# services/asterisk_service.py - ENHANCED DEBUG VERSION
import logging
import socket
import threading
import time
import uuid
import subprocess
from config import AMI_HOST, AMI_PORT, AMI_USERNAME, AMI_SECRET

# Corrected Imports to resolve circular dependency
from app_state import active_calls, active_calls_lock, USER_LOCAL_TIMEZONE, UTC_TIMEZONE
from datetime import datetime, timedelta, timezone

# Global AMI client instance
ami_client_instance = None 
ami_lock = threading.Lock()

# Global handler registry to persist across reconnections
_registered_handlers = []
_handler_registry_lock = threading.Lock()

# ENHANCED DEBUG: AMI Connection tracking
ami_debug_log = []
ami_debug_lock = threading.Lock()

def log_ami_debug(action, details=""):
    """Enhanced debug logging for AMI operations"""
    with ami_debug_lock:
        timestamp = datetime.now(UTC_TIMEZONE)
        ami_debug_log.append({
            'timestamp': timestamp,
            'action': action,
            'details': details
        })
        
        # Keep only last 100 entries
        if len(ami_debug_log) > 100:
            ami_debug_log[:] = ami_debug_log[-100:]
    
    logging.info(f"🔌 AMI_DEBUG: {action} | {details}")

class SocketAMIClient:
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls, force_new=False):
        log_ami_debug("GET_INSTANCE", f"force_new={force_new}")
        with cls._lock:
            if force_new or cls._instance is None:
                if cls._instance is not None:
                    try:
                        if hasattr(cls._instance, 'connection_id'):
                            log_ami_debug("DISCONNECTING_OLD", f"ID: {cls._instance.connection_id}")
                        cls._instance.disconnect()
                    except Exception as e:
                        log_ami_debug("DISCONNECT_OLD_ERROR", str(e))
                
                cls._instance = cls()
                log_ami_debug("NEW_INSTANCE_CREATED", f"ID: {cls._instance.connection_id}")
                # Re-register all previously registered handlers
                cls._instance._restore_handlers()
            return cls._instance

    def __init__(self, host=AMI_HOST, port=AMI_PORT, username=AMI_USERNAME, secret=AMI_SECRET):
        if not hasattr(self, '_initialized_once'):
            self.host = "127.0.0.1"
            self.port = port
            self.username = username
            self.secret = secret
            self.socket = None
            self.connected = False
            self.event_handlers = []
            self.listener_thread = None
            self.last_activity = time.time()
            self.connection_id = str(uuid.uuid4())[:8]
            self._initialized_once = True
            log_ami_debug("INSTANCE_INITIALIZED", f"ID: {self.connection_id}, Host: {self.host}:{self.port}, User: {self.username}")

    def connect(self, max_retries=3, initial_delay=1):
        log_ami_debug("CONNECT_START", f"ID: {self.connection_id}, Max retries: {max_retries}")
        
        for attempt in range(max_retries):
            try:
                log_ami_debug("CONNECT_ATTEMPT", f"ID: {self.connection_id}, Attempt: {attempt + 1}/{max_retries}")

                if self.connected:
                    log_ami_debug("ALREADY_CONNECTED", f"ID: {self.connection_id}")
                    return True

                # Close any existing socket
                if self.socket:
                    try:
                        self.socket.close()
                        log_ami_debug("OLD_SOCKET_CLOSED", f"ID: {self.connection_id}")
                    except Exception as e:
                        log_ami_debug("OLD_SOCKET_CLOSE_ERROR", f"ID: {self.connection_id}, Error: {e}")
                    finally:
                        self.socket = None

                log_ami_debug("CREATING_SOCKET", f"ID: {self.connection_id}, Target: {self.host}:{self.port}")
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket.settimeout(5)

                log_ami_debug("CONNECTING_SOCKET", f"ID: {self.connection_id}")
                self.socket.connect((self.host, self.port))
                log_ami_debug("SOCKET_CONNECTED", f"ID: {self.connection_id}")

                log_ami_debug("RECEIVING_GREETING", f"ID: {self.connection_id}")
                greeting_bytes = self.socket.recv(1024)
                greeting = greeting_bytes.decode('utf-8', errors='ignore')
                log_ami_debug("GREETING_RECEIVED", f"ID: {self.connection_id}, Greeting: '{greeting.strip()}'")
                
                if "Asterisk Call Manager" not in greeting:
                    log_ami_debug("UNEXPECTED_GREETING", f"ID: {self.connection_id}, Got: '{greeting.strip()}'")
                    raise ConnectionError("Unexpected AMI greeting")

                login_cmd = f"Action: Login\r\nUsername: {self.username}\r\nSecret: {self.secret}\r\nEvents: on\r\n\r\n"
                log_ami_debug("SENDING_LOGIN", f"ID: {self.connection_id}, User: {self.username}")
                self.socket.send(login_cmd.encode('utf-8'))

                resp_bytes = b""
                resp_buffer = ""
                log_ami_debug("WAITING_LOGIN_RESPONSE", f"ID: {self.connection_id}")
                
                while b"\r\n\r\n" not in resp_bytes:
                    chunk = self.socket.recv(4096)
                    if not chunk:
                        log_ami_debug("LOGIN_NO_RESPONSE", f"ID: {self.connection_id}")
                        raise ConnectionError("AMI connection closed during login response")
                    resp_bytes += chunk
                    resp_buffer += chunk.decode('utf-8', errors='ignore')
                
                log_ami_debug("LOGIN_RESPONSE_RECEIVED", f"ID: {self.connection_id}, Response: '{resp_buffer.strip()}'")

                if "Response: Success" in resp_buffer and "Authentication accepted" in resp_buffer:
                    log_ami_debug("LOGIN_SUCCESS", f"ID: {self.connection_id}")
                    self.connected = True
                    self.last_activity = time.time()
                    self.socket.settimeout(None)
                    
                    # Start listener thread
                    if self.listener_thread is None or not self.listener_thread.is_alive():
                        self.listener_thread = threading.Thread(target=self._event_listener, daemon=True)
                        self.listener_thread.start()
                        log_ami_debug("LISTENER_STARTED", f"ID: {self.connection_id}")
                    return True
                else:
                    log_ami_debug("LOGIN_FAILED", f"ID: {self.connection_id}, Response: '{resp_buffer.strip()}'")
                    raise ConnectionError("AMI login failed")

            except (socket.timeout, ConnectionRefusedError, OSError) as e:
                log_ami_debug("CONNECT_ERROR", f"ID: {self.connection_id}, Attempt: {attempt + 1}, Error: {type(e).__name__}: {e}")
                self.disconnect()
                if attempt < max_retries - 1:
                    sleep_time = initial_delay * (2 ** attempt)
                    log_ami_debug("CONNECT_RETRY_DELAY", f"ID: {self.connection_id}, Sleeping: {sleep_time}s")
                    time.sleep(sleep_time)
            except Exception as e:
                log_ami_debug("CONNECT_UNEXPECTED_ERROR", f"ID: {self.connection_id}, Attempt: {attempt + 1}, Error: {type(e).__name__}: {e}")
                self.disconnect()
                if attempt < max_retries - 1:
                    time.sleep(initial_delay * (2 ** attempt))
        
        log_ami_debug("CONNECT_FAILED_ALL_ATTEMPTS", f"ID: {self.connection_id}")
        return False

    def ensure_connected(self):
        log_ami_debug("ENSURE_CONNECTED", f"ID: {self.connection_id}, Current status: {self.connected}")
        if self.connected:
            return True
        
        log_ami_debug("NOT_CONNECTED_ATTEMPTING", f"ID: {self.connection_id}")
        if self.connect():
            # Restore handlers after successful connection
            self._restore_handlers()
            return True
        return False

    def add_event_handler(self, handler):
        log_ami_debug("ADD_EVENT_HANDLER", f"ID: {self.connection_id}, Handler: {handler.__name__}, Total handlers: {len(self.event_handlers) + 1}")
        if handler not in self.event_handlers:
            self.event_handlers.append(handler)
        
        # Add to global registry for persistence across reconnections
        with _handler_registry_lock:
            if handler not in _registered_handlers:
                _registered_handlers.append(handler)
                log_ami_debug("HANDLER_REGISTERED_GLOBALLY", f"Handler: {handler.__name__}, Total global: {len(_registered_handlers)}")
        
        return handler
    
    def _restore_handlers(self):
        """Restore all globally registered handlers to this instance"""
        with _handler_registry_lock:
            for handler in _registered_handlers:
                if handler not in self.event_handlers:
                    self.event_handlers.append(handler)
                    log_ami_debug("HANDLER_RESTORED", f"ID: {self.connection_id}, Handler: {handler.__name__}, Total: {len(self.event_handlers)}")

    def _event_listener(self):
        buffer = ""
        log_ami_debug("EVENT_LISTENER_START", f"ID: {self.connection_id}")
        self.socket.settimeout(1.0)

        while self.connected:
            try:
                data = self.socket.recv(4096).decode('utf-8', errors='ignore')
                if not data:
                    log_ami_debug("EVENT_LISTENER_NO_DATA", f"ID: {self.connection_id}")
                    break
                buffer += data

                while "\r\n\r\n" in buffer:
                    event_text, buffer = buffer.split("\r\n\r\n", 1)
                    event = {}
                    for line in event_text.split("\r\n"):
                        if line and ": " in line:
                            key, value = line.split(": ", 1)
                            event[key] = value
                    
                    if "Event" in event:
                        event['_local_id'] = str(uuid.uuid4())[:8]
                        self.last_activity = time.time()

                        # Log event processing
                        event_type = event.get('Event', 'UNKNOWN')
                        log_ami_debug("EVENT_RECEIVED", f"ID: {self.connection_id}, Type: {event_type}, Local ID: {event['_local_id']}")

                        # Log important events with more detail
                        if event_type in ['Newstate', 'Hangup', 'OriginateResponse', 'DTMFEnd']:
                            log_ami_debug("IMPORTANT_EVENT", f"ID: {self.connection_id}, Event: {event_type}, Details: {event}")

                        handlers = self.event_handlers.copy()
                        log_ami_debug("PROCESSING_HANDLERS", f"ID: {self.connection_id}, Event: {event_type}, Handlers: {len(handlers)}")
                        
                        for handler in handlers:
                            try:
                                handler(event)
                            except Exception as e:
                                log_ami_debug("HANDLER_ERROR", f"ID: {self.connection_id}, Handler: {handler.__name__}, Error: {e}")

            except socket.timeout:
                # Expected timeout, check connection status
                if not self.connected:
                    log_ami_debug("EVENT_LISTENER_DISCONNECTED", f"ID: {self.connection_id}")
                    break
                continue
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                log_ami_debug("EVENT_LISTENER_CONNECTION_ERROR", f"ID: {self.connection_id}, Error: {e}")
                self.connected = False
                break
            except Exception as e:
                log_ami_debug("EVENT_LISTENER_UNEXPECTED_ERROR", f"ID: {self.connection_id}, Error: {e}")
                self.connected = False
                break
        
        log_ami_debug("EVENT_LISTENER_TERMINATED", f"ID: {self.connection_id}")
        self.disconnect()

    def disconnect(self):
        if self.connected:
            try:
                log_ami_debug("DISCONNECT_START", f"ID: {self.connection_id}")
                self.connected = False
                if self.socket:
                    try:
                        self.socket.sendall("Action: Logoff\r\n\r\n".encode('utf-8'))
                        time.sleep(0.1)
                    except Exception as send_err:
                        log_ami_debug("LOGOFF_SEND_ERROR", f"ID: {self.connection_id}, Error: {send_err}")
                    finally:
                        self.socket.close()
                    self.socket = None
                
                if self.listener_thread and self.listener_thread.is_alive():
                    log_ami_debug("WAITING_LISTENER_TERMINATE", f"ID: {self.connection_id}")
                    self.listener_thread.join(timeout=2.0)
                    if self.listener_thread.is_alive():
                        log_ami_debug("LISTENER_TERMINATE_TIMEOUT", f"ID: {self.connection_id}")
                
                log_ami_debug("DISCONNECT_COMPLETE", f"ID: {self.connection_id}")
            except Exception as e:
                log_ami_debug("DISCONNECT_ERROR", f"ID: {self.connection_id}, Error: {e}")
                if self.socket:
                    try:
                        self.socket.close()
                    except:
                        pass
                    self.socket = None
        elif self.socket:
            try:
                self.socket.close()
                log_ami_debug("SOCKET_CLEANUP", f"ID: {self.connection_id}")
            except Exception as e:
                log_ami_debug("SOCKET_CLEANUP_ERROR", f"ID: {self.connection_id}, Error: {e}")
            finally:
                self.socket = None

    def send_action(self, action, **params):
        max_retries = 3
        initial_delay = 0.1

        for attempt in range(max_retries):
            try:
                log_ami_debug("SEND_ACTION_START", f"ID: {self.connection_id}, Action: {action}, Attempt: {attempt + 1}/{max_retries}")
                
                if not self.ensure_connected():
                    log_ami_debug("SEND_ACTION_NO_CONNECTION", f"ID: {self.connection_id}, Action: {action}, Attempt: {attempt + 1}")
                    if attempt == max_retries - 1:
                        return False
                    time.sleep(initial_delay * (2 ** attempt))
                    continue

                self.last_activity = time.time()
                action_str = f"Action: {action}\r\n"
                for key, value in params.items():
                    action_str += f"{key}: {value}\r\n"
                action_str += "\r\n"
                
                log_ami_debug("SENDING_ACTION", f"ID: {self.connection_id}, Action: {action}, Params: {params}")
                self.socket.sendall(action_str.encode('utf-8'))
                log_ami_debug("ACTION_SENT_SUCCESS", f"ID: {self.connection_id}, Action: {action}")
                return True

            except (OSError, BrokenPipeError, socket.error) as e:
                log_ami_debug("SEND_ACTION_SOCKET_ERROR", f"ID: {self.connection_id}, Action: {action}, Attempt: {attempt + 1}, Error: {e}")
                self.connected = False
                self.disconnect()
                if attempt == max_retries - 1:
                    log_ami_debug("SEND_ACTION_FAILED_ALL_ATTEMPTS", f"ID: {self.connection_id}, Action: {action}")
                    return False
                time.sleep(initial_delay * (2 ** attempt))
                continue

            except Exception as e:
                log_ami_debug("SEND_ACTION_UNEXPECTED_ERROR", f"ID: {self.connection_id}, Action: {action}, Attempt: {attempt + 1}, Error: {e}")
                if attempt == max_retries - 1:
                    return False
                time.sleep(initial_delay * (2 ** attempt))
                continue

        return False

    def heartbeat(self):
        if self.connected:
            if time.time() - self.last_activity > 45:
                log_ami_debug("HEARTBEAT_PING", f"ID: {self.connection_id}")
                if not self.send_action('Ping'):
                    log_ami_debug("HEARTBEAT_FAILED", f"ID: {self.connection_id}")
                    self.connected = False
                    return False
            return True
        return False

def initialize_ami_client(event_handler_function=None):
    global ami_client_instance
    log_ami_debug("INITIALIZE_START", f"Handler: {event_handler_function.__name__ if event_handler_function else 'None'}")
    
    with ami_lock:
        ami_client_instance = SocketAMIClient.get_instance(force_new=True)

        if ami_client_instance is None:
            log_ami_debug("INITIALIZE_FAILED", "Could not create instance")
            return False

        if event_handler_function:
            ami_client_instance.add_event_handler(event_handler_function)
        
        # Ensure connection and start listener
        if ami_client_instance.ensure_connected():
            log_ami_debug("INITIALIZE_SUCCESS", f"ID: {ami_client_instance.connection_id}, Handlers: {len(ami_client_instance.event_handlers)}")
            return True
        else:
            log_ami_debug("INITIALIZE_FAILED", "Could not establish connection")
            return False

def update_call_status(campaign_id, phone_number, status, details=None, action_id=None, uniqueid=None):
    """Enhanced debug version of update_call_status"""
    log_ami_debug("UPDATE_CALL_STATUS", f"C:{campaign_id} P:{phone_number} Status:{status} Details:{details} ActionID:{action_id} UniqueID:{uniqueid}")
    
    with active_calls_lock:
        campaign_id_str = str(campaign_id)
        if campaign_id_str not in active_calls:
            active_calls[campaign_id_str] = {}
            log_ami_debug("CREATED_CAMPAIGN_DICT", f"C:{campaign_id_str}")
        
        timestamp_utc = datetime.now(UTC_TIMEZONE)
        current_data = active_calls[campaign_id_str].get(phone_number, {})
        current_status = current_data.get('status', '')
        
        log_ami_debug("CURRENT_CALL_DATA", f"C:{campaign_id_str} P:{phone_number} Current:{current_status} Data:{current_data}")
        
        is_already_finalized = current_data.get('finalized_in_memory', False)
        
        status_hierarchy = {
            'unknown': 0, 'pending': 1, 'waiting': 2, 'dialing': 10, 'ringing': 20, 
            'answered': 50, 'dtmf_received': 60, 'completed': 70, 'opted_out': 80,
            'noanswer': 90, 'busy': 90, 'rejected': 90, 'aborted': 90
        }
        current_significance = status_hierarchy.get(current_status, 0)
        new_significance = status_hierarchy.get(status, 0)

        log_ami_debug("STATUS_SIGNIFICANCE", f"C:{campaign_id_str} P:{phone_number} Current:{current_status}({current_significance}) New:{status}({new_significance}) Finalized:{is_already_finalized}")

        # Handle waiting status (manual reset)
        if status == 'waiting':
            log_ami_debug("STATUS_RESET_WAITING", f"C:{campaign_id_str} P:{phone_number}")
            active_calls[campaign_id_str][phone_number] = {
                'status': status,
                'details': details or 'Status manually reset',
                'timestamp': timestamp_utc,
                'action_id': action_id if action_id is not None else current_data.get('action_id'),
                'uniqueid': uniqueid if uniqueid is not None else current_data.get('uniqueid'),
                'finalized_in_memory': False
            }
            return
        
        # Initial status setting
        if not current_status:
            log_ami_debug("STATUS_INITIAL_SET", f"C:{campaign_id_str} P:{phone_number} -> {status}")
            active_calls[campaign_id_str][phone_number] = {
                'status': status,
                'details': details,
                'timestamp': timestamp_utc,
                'action_id': action_id,
                'uniqueid': uniqueid,
                'finalized_in_memory': False
            }
            if status in ['completed', 'noanswer', 'busy', 'rejected', 'aborted', 'opted_out']:
                active_calls[campaign_id_str][phone_number]['finalized_in_memory'] = True
                log_ami_debug("STATUS_FINALIZED_INITIAL", f"C:{campaign_id_str} P:{phone_number} Status:{status}")
            return

        # Determine if update should be allowed
        allow_update = False
        update_reason = ""
        
        if new_significance > current_significance:
            allow_update = True
            update_reason = "Higher significance"
        elif status == current_status:
            allow_update = True
            update_reason = "Same status, updating details"
        elif current_status in ['dialing', 'ringing'] and status not in ['pending', 'waiting', 'unknown']:
            allow_update = True
            update_reason = "From transitional to non-transitional"
        elif is_already_finalized and status in ['completed', 'opted_out', 'aborted'] and new_significance < current_significance:
            allow_update = True
            update_reason = "Definitive final state override"
        elif is_already_finalized and status in ['noanswer', 'busy', 'rejected'] and current_status in ['dialing', 'ringing']:
            allow_update = True
            update_reason = "AMI final status override stuck status"

        log_ami_debug("UPDATE_DECISION", f"C:{campaign_id_str} P:{phone_number} Allow:{allow_update} Reason:{update_reason}")

        if is_already_finalized and not allow_update:
            log_ami_debug("UPDATE_SKIPPED_FINALIZED", f"C:{campaign_id_str} P:{phone_number} Current:{current_status} New:{status}")
            return

        if allow_update:
            # Update the call data
            active_calls[campaign_id_str][phone_number].update({
                'status': status,
                'details': details,
                'timestamp': timestamp_utc,
                'action_id': action_id if action_id is not None else current_data.get('action_id'),
                'uniqueid': uniqueid if uniqueid is not None else current_data.get('uniqueid')
            })
            
            # Mark as finalized if it's a final state
            if status in ['completed', 'noanswer', 'busy', 'rejected', 'aborted', 'opted_out']:
                active_calls[campaign_id_str][phone_number]['finalized_in_memory'] = True
                log_ami_debug("STATUS_FINALIZED_UPDATE", f"C:{campaign_id_str} P:{phone_number} Status:{status}")
            else:
                active_calls[campaign_id_str][phone_number]['finalized_in_memory'] = False

            log_ami_debug("STATUS_UPDATED", f"C:{campaign_id_str} P:{phone_number} {current_status} -> {status} {details or ''}")
        else:
            log_ami_debug("UPDATE_SKIPPED_CONDITIONS", f"C:{campaign_id_str} P:{phone_number} Current:{current_status}({current_significance}) New:{status}({new_significance})")

def is_call_complete(phone, campaign_id):
    """Enhanced debug version of is_call_complete"""
    with active_calls_lock:
        campaign_id_str = str(campaign_id)
        if campaign_id_str not in active_calls or phone not in active_calls[campaign_id_str]:
            log_ami_debug("CALL_COMPLETE_NOT_IN_MEMORY", f"C:{campaign_id_str} P:{phone}")
            return True
        
        status = active_calls[campaign_id_str][phone].get('status', '')
        finalized = active_calls[campaign_id_str][phone].get('finalized_in_memory', False)
        is_complete = status in ['completed', 'noanswer', 'busy', 'rejected', 'aborted', 'opted_out'] or finalized
        
        log_ami_debug("CALL_COMPLETE_CHECK", f"C:{campaign_id_str} P:{phone} Status:{status} Finalized:{finalized} Complete:{is_complete}")
        return is_complete

def run_asterisk_command(cmd):
    """Enhanced debug version of run_asterisk_command"""
    log_ami_debug("ASTERISK_CMD_START", f"Command: {cmd}")
    
    try:
        import os
        if os.path.exists('/usr/sbin/asterisk'): 
            asterisk_path = '/usr/sbin/asterisk'
        elif os.path.exists('/usr/bin/asterisk'): 
            asterisk_path = '/usr/bin/asterisk'
        else: 
            asterisk_path = 'asterisk'
        
        full_cmd = [asterisk_path, '-rx', cmd]
        log_ami_debug("ASTERISK_CMD_EXEC", f"Full command: {' '.join(full_cmd)}")
        
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=10, check=False)

        if result.returncode == 0:
            log_ami_debug("ASTERISK_CMD_SUCCESS", f"Command: {cmd}, Output length: {len(result.stdout)} chars")
            return True, result.stdout.strip()
        else:
            log_ami_debug("ASTERISK_CMD_FAILED", f"Command: {cmd}, Return code: {result.returncode}, STDERR: {result.stderr.strip()}")
            return False, result.stderr.strip() or result.stdout.strip()
            
    except FileNotFoundError:
        log_ami_debug("ASTERISK_CMD_NOT_FOUND", f"Command: {cmd}")
        return False, "Asterisk executable not found."
    except subprocess.TimeoutExpired:
        log_ami_debug("ASTERISK_CMD_TIMEOUT", f"Command: {cmd}")
        return False, "Timeout executing Asterisk command."
    except Exception as e:
        log_ami_debug("ASTERISK_CMD_ERROR", f"Command: {cmd}, Error: {e}")
        return False, str(e)