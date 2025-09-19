#!/bin/bash
# 04-apache-setup.sh - Apache web server setup for InfoCall

# Determine the directory where this script is located
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"

# Source the environment setup script to get logging functions and variables
# This must be at the very beginning of each script that uses log_message
if [[ -f "${INSTALL_DIR}/00-setup-environment.sh" ]]; then
    source "${INSTALL_DIR}/00-setup-environment.sh"
else
    echo "ERROR: 00-setup-environment.sh not found in ${INSTALL_DIR}. Exiting." >&2
    exit 1
fi

log_message "Step 4: Apache Setup"

# Check if running as root (already done in 00-setup-environment.sh, but harmless here)
check_root

# Check for checkpoint to prevent re-running without confirmation
if [[ -f "${LOG_DIR}/.checkpoint_apache_setup" ]]; then # Check for this script's checkpoint
    log_message "WARNING: Apache setup already completed. To run again, remove ${LOG_DIR}/.checkpoint_apache_setup file."
    read -p "Do you want to continue anyway? (y/n): " CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        log_message "Apache setup skipped."
        exit 0
    fi
    log_message "Continuing with Apache setup."
fi

# Verify Apache is installed (using command -v instead of check_requirement)
log_message "Verifying Apache installation..."
if ! command -v apache2 &> /dev/null; then
    log_message "ERROR: Apache (apache2) not found. Please install apache2 package."
    exit 1
fi
log_message "Apache found."

# Ask for server name (less critical if using IP/port, but good practice)
SERVER_NAME="infocall.local"
read -p "Enter server name for InfoCall [${SERVER_NAME}]: " INPUT_SERVER_NAME
SERVER_NAME=${INPUT_SERVER_NAME:-$SERVER_NAME}

# Create WSGI entry point file (wsgi.py)
log_message "Creating WSGI entry point (${APP_ROOT}/wsgi.py)..." # Use APP_ROOT
# Check if wsgi.py exists, create/overwrite if needed (idempotent)
cat > "${APP_ROOT}/wsgi.py" << 'EOF'
import sys
import logging
import os

# Ensure the project directory is in the Python path
project_home = '/var/www/html/infocall' # Make sure this matches APP_ROOT
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Configure logging to stderr for WSGI/Apache logs
logging.basicConfig(stream=sys.stderr, level=logging.INFO) # Use INFO level for production
log = logging.getLogger(__name__)
log.info("WSGI script started.")

try:
    # Import the Flask app instance
    log.info("Attempting to import Flask app from app.py...")
    from app import app as application
    log.info("Flask app imported successfully as 'application'.")

except ImportError as e:
    log.critical(f"Error importing Flask application: {e}", exc_info=True)
    raise
except Exception as e:
    log.critical(f"Unexpected error during WSGI initialization: {e}", exc_info=True)
    raise
EOF
# Ownership will be set globally in script 05, ensure readable by asterisk group initially if needed
# Or rely on script 05 to set final permissions before Apache restart.

# Verify WSGI file creation
if [ ! -f "${APP_ROOT}/wsgi.py" ]; then # Use APP_ROOT
    log_message "ERROR: Failed to create WSGI entry point file."
    exit 1
fi
log_message "WSGI entry point created successfully."

# Create Apache configuration (infocall.conf)
log_message "Creating Apache configuration file (/etc/apache2/sites-available/infocall.conf)..."
# Add Listen directive for the new port if not already present globally
if ! grep -qE "^\s*Listen\s+5000\s*$" /etc/apache2/ports.conf; then
  log_message "Adding 'Listen 5000' to /etc/apache2/ports.conf"
  if [ -f /etc/apache2/ports.conf ]; then
    if ! grep -qE "^\s*Listen\s+5000\s*$" /etc/apache2/ports.conf; then
        echo "" >> /etc/apache2/ports.conf
        echo "# Port for InfoCall application" >> /etc/apache2/ports.conf
        echo "Listen 5000" >> /etc/apache2/ports.conf
    fi
  else
    log_message "WARN: /etc/apache2/ports.conf not found. Cannot add Listen 5000 directive automatically."
  fi
fi

cat > /tmp/infocall.conf << EOF
# Virtual Host for InfoCall on port 5000
<VirtualHost *:5000>
    ServerName ${SERVER_NAME}
    ServerAdmin webmaster@localhost

    # Define the WSGI daemon process - Run as asterisk user/group
    WSGIDaemonProcess infocall-5000 user=asterisk group=asterisk threads=5 python-home=${APP_ROOT}/venv python-path=${APP_ROOT} display-name=%{GROUP}
    WSGIProcessGroup infocall-5000
    WSGIScriptAlias / ${APP_ROOT}/wsgi.py process-group=infocall-5000 application-group=%{GLOBAL}

    <Directory ${APP_ROOT}>
        Require all granted
        # WSGI specific settings inside Directory
        <IfModule mod_authz_core.c>
            Require all granted
        </IfModule>
        <IfModule !mod_authz_core.c>
            Order allow,deny
            Allow from all
        </IfModule>
        # WSGIScriptReloading: On (helpful for development), Off (recommended for production)
        WSGIScriptReloading On
    </Directory>

    # Alias for static files
    Alias /static ${APP_ROOT}/static
    <Directory ${APP_ROOT}/static>
       Require all granted
    </Directory>

    # Logging
    ErrorLog \${APACHE_LOG_DIR}/infocall_error_5000.log
    CustomLog \${APACHE_LOG_DIR}/infocall_access_5000.log combined

</VirtualHost>
EOF

# Install the Apache configuration
if [ -f /etc/apache2/sites-available/infocall.conf ]; then
    log_message "INFO: Backing up existing infocall.conf..."
    cp /etc/apache2/sites-available/infocall.conf /etc/apache2/sites-available/infocall.conf.bak.$(date +%Y%m%d_%H%M%S)
fi
mv /tmp/infocall.conf /etc/apache2/sites-available/infocall.conf

# Verify Apache config file creation
if [ ! -f /etc/apache2/sites-available/infocall.conf ]; then
    log_message "ERROR: Failed to create Apache configuration file."
    exit 1
fi
log_message "Apache configuration file created/updated for port 5000."

# Enable required modules and site
log_message "Ensuring Apache modules (wsgi) and site (infocall) are handled..."
a2enmod wsgi 2>&1 | tee -a "${LOG_FILE}"
# Disable site first in case it was enabled with bad config
a2dissite infocall 2>&1 | tee -a "${LOG_FILE}" || true # Ignore error if not enabled
# Enable the new site configuration
a2ensite infocall 2>&1 | tee -a "${LOG_FILE}"

# Verify Apache config syntax
log_message "Checking Apache configuration syntax..."
if ! apache2ctl configtest 2>&1 | tee -a "${LOG_FILE}"; then
    log_message "ERROR: Apache configuration test failed. Please check the configuration and logs (/var/log/apache2/error.log and infocall_error_5000.log)."
    a2dissite infocall 2>&1 | tee -a "${LOG_FILE}" || true
    exit 1
fi
log_message "Apache configuration syntax OK."

# Create checkpoint
touch "${LOG_DIR}/.checkpoint_apache_setup" # Manual checkpoint creation

log_message "Apache setup for InfoCall on Port 5000 complete! Apache will be restarted in the final step."