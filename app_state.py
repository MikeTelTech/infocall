# app_state.py (formerly globals.py)
import threading
from datetime import datetime, timedelta, timezone
import logging

try:
    from zoneinfo import ZoneInfo
except ImportError:
    logging.error("zoneinfo module not found. Timezone features require Python 3.9+ or the 'tzdata' package on some systems.")
    raise ImportError("Required timezone library (zoneinfo or pytz) not found.")

# --- Define Timezones ---
try:
    USER_LOCAL_TIMEZONE = ZoneInfo("America/New_York")
except Exception as tz_error:
    logging.error(f"Could not load timezone 'America/New_York'. Is 'tzdata' installed? Error: {tz_error}")
    USER_LOCAL_TIMEZONE = timezone.utc
UTC_TIMEZONE = timezone.utc

# Global variables for application state that require thread safety
# These variables will be modified during runtime by multiple threads.

# Dictionary to track active calls
# Format: {campaign_id: {phone_number: {'status': status, 'details': details, 'timestamp': time, 'action_id': uuid_str, 'uniqueid': unique_asterisk_id, 'finalized_in_memory': bool}}}
active_calls = {}
active_calls_lock = threading.Lock() # Lock to protect 'active_calls' dictionary

# Dictionary to track active SMS messages (e.g., for rate limiting)
active_sms = {}
active_sms_lock = threading.Lock() # Lock to protect 'active_sms' dictionary

# Note: concurrent_call_limit and concurrent_sms_limit have been moved to config.py