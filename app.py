'''
 INFOCALL IS COMMUNICATION MANAGEMENT CALLING CAMPAIGN SOFTWARE. This was created for use by Churches and civic groups.
    Copyright (C) 2025  Mike Davis myke4416@gmail.com

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU Affero General Public License as
    published by the Free Software Foundation, either version 3 of the
    License, or (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU Affero General Public License for more details.

    You should have received a copy of the GNU Affero General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.
    '''
import sys
import logging
import os
import subprocess
import mysql.connector.pooling
import bcrypt
import csv
import uuid
import socket
import threading
import time
import math
import random
import logging.handlers
import io
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, redirect, url_for, session, render_template, send_from_directory, flash
from werkzeug.utils import secure_filename
from functools import wraps
from zoneinfo import ZoneInfo
from pydub import AudioSegment

# Application-specific imports
from config import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, AMI_HOST, AMI_PORT, AMI_USERNAME, AMI_SECRET
from app_state import active_calls, active_calls_lock, active_sms, active_sms_lock, USER_LOCAL_TIMEZONE, UTC_TIMEZONE


# --- Timezone Handling --- 
# Use zoneinfo (Python 3.9+)
try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Fallback or raise error if zoneinfo is not available
    logging.error("zoneinfo module not found. Timezone features require Python 3.9+ or the 'tzdata' package on some systems.") 
    raise ImportError("Required timezone library (zoneinfo or pytz) not found.")

# --- Define Timezones ---
# Define the user's local timezone and UTC 
try:
    USER_LOCAL_TIMEZONE = ZoneInfo("America/New_York") 
except Exception as tz_error:
    logging.error(f"Could not load timezone 'America/New_York'. Is 'tzdata' installed? Error: {tz_error}") 
    USER_LOCAL_TIMEZONE = timezone.utc 
UTC_TIMEZONE = timezone.utc 
# --------------------------------------------------

print(sys.executable)
print(sys.path)

# Define AMI Event Filter before configuring logging
class AMIEventFilter(logging.Filter):
    def filter(self, record): 
        # Filter out most AMI Variable events but keep critical ones
        if "AMI Event Variables" in record.getMessage() and record.levelno == logging.DEBUG:
            # Keep only specific variables you care about
            important_vars = ["CAMPAIGN_ID", "MEMBER_ID", "DIAL_NUMBER"] 
            for var in important_vars:
                if var in record.getMessage(): 
                    return True
            return False
        return True

# Configure logging with rotation
log_handler = logging.handlers.RotatingFileHandler(
    filename='/var/www/html/infocall/logs/infocall.log',
    maxBytes=10*1024*1024,  # 10MB per file
    backupCount=5,  # Keep 5 backup files
    encoding='utf-8' # <--- ADD THIS LINE
)
log_handler.setLevel(logging.INFO)
log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# Get the root logger and add the handler
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)  # Set overall level
root_logger.addHandler(log_handler)

# Add a StreamHandler to output logs to stderr as well, for WSGI servers (e.g., Apache/Nginx)
# This ensures logs appear in the web server's error logs.
stream_handler = logging.StreamHandler(sys.stderr)
stream_handler.setLevel(logging.DEBUG) # Set level for stderr output (e.g., INFO, DEBUG)
stream_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
# For environments where sys.stderr might not default to UTF-8, you can set an encoder:
# stream_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
# stream_handler.stream.reconfigure(encoding='utf-8') # Use if you face issues with sys.stderr encoding
root_logger.addHandler(stream_handler)

# Add the filter to reduce AMI event logging
root_logger.addFilter(AMIEventFilter())

# Continue with app initialization
app = Flask(__name__, template_folder='templates', static_folder='static', static_url_path='/static')
app.secret_key = os.urandom(24) # Use a random key

# --- Database Connection Pooling ---
db_pool = None
try:
    db_pool = mysql.connector.pooling.MySQLConnectionPool(
        pool_name="infocall_pool",
        pool_size=10, # Adjust size as needed
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        pool_reset_session=True # Helps ensure clean state 
    )
    logging.info("Database connection pool created successfully.") 
except mysql.connector.Error as err:
    logging.error(f"FATAL: Failed to create database connection pool: {err}") 
    db_pool = None # Ensure pool is None if creation fails
# -----------------------------------------------------------

# --->>> ADD THE CONTEXT PROCESSOR HERE <<<---
@app.context_processor
def inject_now():
  """Injects the current UTC date/time into the template context."""
  # Using aware object consistent with other parts of your new code
  return {'now': datetime.now(UTC_TIMEZONE)}
# --->>> END OF CONTEXT PROCESSOR <<<---

# Global variables
active_calls = {} # Format: {campaign_id: {phone_number: {'status': status, 'details': details, 'timestamp': time}}} 
concurrent_call_limit = 4  # Maximum number of concurrent calls allowed 
active_calls_lock = threading.Lock() 
ami_lock = threading.Lock() # Lock specifically for the initialize_ami_client function 
active_sms = {} # Initialize

# Import and register blueprints
from routes.auth_routes import auth_bp
from routes.member_routes import member_bp
from routes.call_routes import call_bp
from routes.sms_routes import sms_bp
from routes.info_routes import info_bp

app.register_blueprint(auth_bp)
app.register_blueprint(member_bp)
app.register_blueprint(call_bp)
app.register_blueprint(sms_bp)
app.register_blueprint(info_bp)

# Start the background threads
# MODIFIED IMPORT: Removed maintain_ami_connection from import list
from services.call_service import scheduled_call_checker, direct_event_handler_with_optout 
from services.sms_service import scheduled_sms_checker 
from services.asterisk_service import initialize_ami_client # MODIFIED LINE: Removed maintain_ami_connection


# Initialize AMI client globally upon app start
socket_ami_client = None
initialize_ami_client(direct_event_handler_with_optout) # MODIFIED LINE


# REMOVED: The ami_maintenance_thread is no longer needed as per the new AMI connection handling strategy.
# ami_maintenance_thread = threading.Thread(target=maintain_ami_connection, daemon=True)
# ami_maintenance_thread.start()

scheduled_sms_checker_thread = threading.Thread(target=scheduled_sms_checker, daemon=True)
scheduled_sms_checker_thread.start()

scheduled_call_checker_thread = threading.Thread(target=scheduled_call_checker, daemon=True)
scheduled_call_checker_thread.start()


if __name__ == "__main__":
    if db_pool is None: 
        logging.critical("DB pool not initialized. App cannot start.") 
    else:
        logging.info("Starting Flask application...") 
        app.run(debug=False, host='0.0.0.0', port=5000, use_reloader=False) # use_reloader=False recommended with threads 
