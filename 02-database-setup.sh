#!/bin/bash
# 02-database-setup.sh
# Sets up the MariaDB database and user for InfoCall.

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

log_message "Step 2: Database Setup"

# Check for checkpoint to prevent re-running without confirmation
if [[ -f "${LOG_DIR}/.checkpoint_database_setup" ]]; then
    log_message "WARNING: Database setup already completed. To run again, remove ${LOG_DIR}/.checkpoint_database_setup file."
    read -p "Do you want to continue anyway? (y/n): " CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        log_message "Database setup skipped."
        exit 0
    fi
    log_message "Continuing with database setup."
fi

# --- 1. Prompt for Database Credentials ---
log_message "Prompting for database credentials..."

DEFAULT_DB_NAME="infocall_db"
read -p "Enter InfoCall database name [${DEFAULT_DB_NAME}]: " INPUT_DB_NAME
DB_NAME=${INPUT_DB_NAME:-$DEFAULT_DB_NAME}

# Loop until DB_USER is not empty
while true; do
    # Prompt for database username
    read -p "Enter InfoCall database username (e.g., infocall_user): " DB_USER

    # Validate DB_USER
    if [[ -z "$DB_USER" ]]; then
        echo "Database username cannot be empty. Please try again."
        # The loop will continue, prompting again
    else
        # DB_USER is not empty, break the loop
        break
    fi
done

# Prompt for database password securely with confirmation
DB_PASSWORD=""
while [ -z "$DB_PASSWORD" ]; do
    read -s -p "Enter InfoCall database password: " DB_PASSWORD
    echo # Add a newline after the secure password input
    if [ -z "$DB_PASSWORD" ]; then
        log_message "WARN: Database password cannot be empty."
        continue
    fi
    read -s -p "Confirm InfoCall database password: " DB_PASSWORD_CONFIRM
    echo # Add a newline after the secure password input
    if [ "$DB_PASSWORD" != "$DB_PASSWORD_CONFIRM" ]; then
        log_message "WARN: Passwords do not match."
        DB_PASSWORD=""
    fi
done

log_message "Database credentials received."

# --- 2. Create/Update config.py with ALL sections ---
log_message "Creating/Updating config.py with database, AMI, and Twilio configuration..."
DB_CONFIG_FILE="${APP_ROOT}/config.py"

cat << EOF > "${DB_CONFIG_FILE}"
# Database Configuration
DB_HOST = 'localhost'
DB_USER = '${DB_USER}'
DB_PASSWORD = '${DB_PASSWORD}'
DB_NAME = '${DB_NAME}'

# Asterisk Manager Interface configuration
# These values are set by 03-asterisk-setup.sh
AMI_HOST = '127.0.0.1'
AMI_PORT = 5038
AMI_USERNAME = 'infocall_ami' # Placeholder, will be set by 03-asterisk-setup.sh
AMI_SECRET = 'your_ami_secret_will_be_set_by_script' # Placeholder, will be set by 03-asterisk-setup.sh

# Twilio Configuration (Update these if using SMS/Call features)
TWILIO_ACCOUNT_SID = ''
TWILIO_AUTH_TOKEN = ''
TWILIO_PHONE_NUMBER = '' # Your Twilio phone number

# A2P 10DLC Registration information (Optional)
TWILIO_BRAND_SID = ''
TWILIO_TRUST_HUB_SID = ''
TWILIO_CUSTOMER_PROFILE_SID = ''
TWILIO_MESSAGING_SERVICE_SID = ''

# Application settings
DEBUG_MODE = False
MAX_CONCURRENT_CALLS = 4
MAX_SMS_PER_MINUTE = 10
EOF

log_message "config.py generated/updated with all default sections and database credentials."

# Ensure config.py has correct permissions (root owner for security, asterisk group for read)
chmod 640 "${DB_CONFIG_FILE}" || log_message "WARNING: Could not set permissions for ${DB_CONFIG_FILE}."
chown root:asterisk "${DB_CONFIG_FILE}" || log_message "WARNING: Could not set ownership for ${DB_CONFIG_FILE}."

log_message "Database connection configured."

# --- 3. Create Database and User in MariaDB ---
log_message "Creating MariaDB database and user..."

# SQL commands to create user, database, and grant privileges
TMP_SQL_FILE=$(mktemp)
cat << EOF > "${TMP_SQL_FILE}"
DROP USER IF EXISTS '${DB_USER}'@'localhost';
CREATE DATABASE IF NOT EXISTS ${DB_NAME};
CREATE USER '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
GRANT ALL PRIVILEGES ON ${DB_NAME}.* TO '${DB_USER}'@'localhost';
FLUSH PRIVILEGES;
EOF

# --- DEBUG: Log the SQL command for user creation ---
log_message "DEBUG: Executing SQL from ${TMP_SQL_FILE} to create user/DB."
# --- END DEBUG ---

# Loop until the MariaDB command successfully executes
while true; do
    log_message "MariaDB when prompted for password use passw0rd unless you have changed it... press [ENTER]:"
    # Attempt to execute SQL commands using the MariaDB root user.
    # The 'mysql' client will internally prompt for the password.
    # If the password is incorrect or the service is unavailable,
    # 'mysql' will exit with a non-zero status, triggering the 'else' block.
    if mysql -u root -p < "${TMP_SQL_FILE}"; then
        # If the command succeeded, break out of the loop
        log_message "MariaDB SQL commands executed successfully."
        break
    else
        # If the command failed (e.g., incorrect password, MariaDB service down),
        # print the requested re-prompt message.
        echo "Incorrect, try again:"
        # The loop will then reiterate, causing the 'mysql' command to run again
        # and prompt for the password once more.
    fi
done

# Clean up temporary SQL file after successful execution.
# This should happen outside the loop to ensure it's cleaned only once
# and only after a successful database operation.
rm -f "${TMP_SQL_FILE}"


log_message "MariaDB database and user created."

# --- NEW: Add a short delay to allow MariaDB to fully register new user ---
log_message "Pausing for 2 seconds to allow MariaDB to register new user..."
sleep 2 # Add a 2-second delay
# --- END NEW ---

# --- 4. Import Database Schema ---
log_message "Importing database schema from infocall_db_structure.sql..."
DB_SCHEMA_FILE="${INSTALL_DIR}/infocall_db_structure.sql"

if [[ ! -f "${DB_SCHEMA_FILE}" ]]; then
    error_exit "Database schema file not found: ${DB_SCHEMA_FILE}."
fi

# --- DEBUG: Log the exact schema import command ---
log_message "DEBUG: Executing schema import with: mysql -h ${DB_HOST} -u ${DB_USER} -p'${DB_PASSWORD}' ${DB_NAME} < ${DB_SCHEMA_FILE}"
# --- END DEBUG ---

mysql -h "${DB_HOST}" -u "${DB_USER}" -p"${DB_PASSWORD}" "${DB_NAME}" < "${DB_SCHEMA_FILE}" || error_exit "Failed to import database schema."
log_message "Database schema imported successfully."

# --- Create Checkpoint ---
log_message "Database setup complete!"
touch "${LOG_DIR}/.checkpoint_database_setup"

log_message "Step 2: Database Setup Complete"