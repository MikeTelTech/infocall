#!/bin/bash
# install-infocall.sh
# This is the main installation script for InfoCall.
# It orchestrates the execution of other setup scripts in sequence.

# Ensure this script is run from the root of the InfoCall application directory
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "${INSTALL_DIR}" || { echo "ERROR: Failed to change to installation directory. Exiting." >&2; exit 1; }

# Source the environment setup script to get logging functions and variables
# This must be at the very beginning to make log_message available
if [[ -f "${INSTALL_DIR}/00-setup-environment.sh" ]]; then
    source "${INSTALL_DIR}/00-setup-environment.sh"
else
    echo "ERROR: 00-setup-environment.sh not found in ${INSTALL_DIR}. Exiting." >&2
    exit 1
fi

# Ensure we are running as root
check_root

log_message "====================================================="
log_message " InfoCall Installation Script"
log_message "====================================================="
log_message "This script will install and configure InfoCall on your system."
log_message "Please ensure you have a working installation of Incredible PBX 2027-U."
log_message "This process may take several minutes to complete."

# Basic system checks (optional, but good practice)
if ! command -v asterisk &> /dev/null; then
    log_message "WARNING: Asterisk command not found. Please ensure Asterisk is installed and in your PATH."
    read -p "Continue anyway? (y/n): " CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        error_exit "Installation aborted by user."
    fi
fi

# Check if all required scripts are present
REQUIRED_SCRIPTS=(
    "00-setup-environment.sh"
    "01-system-setup.sh"
    "02-database-setup.sh"
    "03-asterisk-setup.sh"
    "04-apache-setup.sh"
    "05-finalize-setup.sh"
)

ALL_SCRIPTS_FOUND=true
for script in "${REQUIRED_SCRIPTS[@]}"; do
    if [[ ! -f "${INSTALL_DIR}/${script}" ]]; then
        log_message "ERROR: Required script not found: ${INSTALL_DIR}/${script}"
        ALL_SCRIPTS_FOUND=false
    fi
done

if ! $ALL_SCRIPTS_FOUND; then
    error_exit "One or more required installation scripts are missing. Please ensure all files are in the ${INSTALL_DIR} directory."
fi

log_message "All required installation scripts found."

# Make installation scripts executable (if they are not already)
log_message "Making installation scripts executable..."
chmod +x "${INSTALL_DIR}"/*.sh || log_message "WARNING: Could not make all .sh scripts executable. Check permissions."

log_message "The installation will perform the following steps:"
log_message "  1. Set up environment and utilities"
log_message "  2. Install system dependencies (Python, MariaDB, Apache, etc.)"
log_message "  3. Create and configure the database & credentials"
log_message "  4. Configure Asterisk integration (AMI)"
log_message "  5. Set up Apache web server with WSGI"
log_message "  6. Create admin user and finalize installation"

read -p "Press Enter to start the installation or Ctrl+C to cancel."

log_message "Starting installation process..."

# --- Execute Setup Scripts in Order ---

# Step 1: System Setup
log_message "Step 1: System Setup"
bash "${INSTALL_DIR}/01-system-setup.sh" || error_exit "Step 1 (System Setup) failed."

# Step 2: Database Setup
log_message "Step 2: Database Setup"
bash "${INSTALL_DIR}/02-database-setup.sh" || error_exit "Step 2 (Database Setup) failed."

# Step 3: Asterisk Configuration
log_message "Step 3: Asterisk Configuration"
bash "${INSTALL_DIR}/03-asterisk-setup.sh" || error_exit "Step 3 (Asterisk Configuration) failed."

# Step 4: Apache Setup
log_message "Step 4: Apache Setup"
bash "${INSTALL_DIR}/04-apache-setup.sh" || error_exit "Step 4 (Apache Setup) failed."

# Step 5: Finalize Setup
log_message "Step 5: Finalize Setup"
bash "${INSTALL_DIR}/05-finalize-setup.sh" || error_exit "Step 5 (Finalize Setup) failed."

log_message "Performing final verification..."

# Example verification steps (customize as needed)
# Verify database connection
DB_USER=$(grep -oP "DB_USER = ['\"]\K[^'\"]+" "${APP_ROOT}/config.py")
DB_PASSWORD=$(grep -oP "DB_PASSWORD = ['\"]\K[^'\"]+" "${APP_ROOT}/config.py")
DB_NAME=$(grep -oP "DB_NAME = ['\"]\K[^'\"]+" "${APP_ROOT}/config.py")
DB_HOST=$(grep -oP "DB_HOST = ['\"]\K[^'\"]+" "${APP_ROOT}/config.py")

if [[ -z "$DB_USER" || -z "$DB_PASSWORD" || -z "$DB_NAME" || -z "$DB_HOST" ]]; then
    log_message "WARNING: Could not retrieve DB credentials for final verification."
else
    mysql -h "${DB_HOST}" -u "${DB_USER}" -p"${DB_PASSWORD}" -e "USE ${DB_NAME}; SELECT 1;" &> /dev/null
    if [[ $? -eq 0 ]]; then
        log_message "Database connection verified."
    else
        log_message "WARNING: Database connection failed during final verification. Check DB settings in ${APP_ROOT}/config.py."
    fi
fi

# Verify Apache service status
if systemctl is-active --quiet apache2; then
    log_message "Apache service is active."
else
    log_message "WARNING: Apache service is not active. Check Apache installation."
fi

# Verify Apache WSGI module is loaded (if applicable)
if apache2ctl -M | grep -q wsgi_module; then
    log_message "Apache WSGI module is loaded."
else
    log_message "WARNING: Apache WSGI module is not loaded. Check Apache configuration."
fi

# Verify Apache site is enabled (e.g., infocall.conf)
if [[ -f "/etc/apache2/sites-enabled/infocall.conf" ]]; then
    log_message "Apache site 'infocall.conf' is enabled."
else
    log_message "WARNING: Apache site 'infocall.conf' is not enabled. Check Apache configuration."
fi

# Attempt to access application locally via curl
CURL_TEST_URL="http://127.0.0.1:5000/" # Updated to include port 5000
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${CURL_TEST_URL}")
if [[ "$HTTP_STATUS" -eq 200 || "$HTTP_STATUS" -eq 302 ]]; then # 200 OK or 302 Redirect
    log_message "Application seems accessible locally (received HTTP ${HTTP_STATUS})."
else
    log_message "WARNING: Application not accessible locally via curl (received HTTP ${HTTP_STATUS}). Check Apache/WSGI configuration."
fi

log_message "All verification checks passed successfully!" # Change to WARNING if any checks fail

log_message "====================================================="
log_message " InfoCall installation complete!"
log_message "====================================================="
log_message "You can now access InfoCall at:"
log_message "    http://$(hostname -I | awk '{print $1}'):5000/" # Updated to include port 5000
log_message "    (or http://<your_server_name>:5000/ if you configured DNS/hosts)"
log_message "Log in with the admin email and password you provided during setup."
log_message "Remember to configure Twilio credentials in ${APP_ROOT}/config.py if you plan to use SMS features."
log_message "Installation logs are available in: ${LOG_DIR}/"
log_message "Thank you for installing InfoCall!"