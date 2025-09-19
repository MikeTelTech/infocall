#!/bin/bash
# 05-finalize-setup.sh
# Finalizes the InfoCall installation, including user creation and Apache configuration.

# Determine the directory where this script is located
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"

# Source the environment setup script to get logging functions and variables
if [[ -f "${INSTALL_DIR}/00-setup-environment.sh" ]]; then
    source "${INSTALL_DIR}/00-setup-environment.sh"
else
    echo "ERROR: 00-setup-environment.sh not found in ${INSTALL_DIR}. Exiting." >&2
    exit 1
fi

log_message "Step 5: Finalize Setup"

# Check for checkpoint to prevent re-running without confirmation
if [[ -f "${LOG_DIR}/.checkpoint_finalize_setup" ]]; then
    log_message "WARNING: Finalize setup already completed. To run again, remove ${LOG_DIR}/.checkpoint_finalize_setup file."
    read -p "Do you want to continue anyway? (y/n): " CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        log_message "Finalize setup skipped."
        exit 0
    fi
    log_message "Continuing with finalize setup."
fi

# Get database password from config.py - Ensure config.py exists first
DB_CONFIG_FILE="${APP_ROOT}/config.py" # Use APP_ROOT

if [ ! -f "${DB_CONFIG_FILE}" ]; then
    log_message "ERROR: Config file ${DB_CONFIG_FILE} not found. Run 02-database-setup.sh first."
    exit 1
fi
log_message "Reading database credentials from config.py..."

# Use regex to capture content between single OR double quotes
DB_HOST=$(grep -oP "DB_HOST = ['\"]\K[^'\"]+" "${DB_CONFIG_FILE}")
DB_USER=$(grep -oP "DB_USER = ['\"]\K[^'\"]+" "${DB_CONFIG_FILE}")
DB_PASSWORD=$(grep -oP "DB_PASSWORD = ['\"]\K[^'\"]+" "${DB_CONFIG_FILE}")
DB_NAME=$(grep -oP "DB_NAME = ['\"]\K[^'\"]+" "${DB_CONFIG_FILE}")

if [[ -z "$DB_USER" || -z "$DB_PASSWORD" || -z "$DB_NAME" || -z "$DB_HOST" ]]; then
    log_message "ERROR: Could not retrieve all DB credentials from config.py. Check file format or content."
    log_message "DB_HOST: '$DB_HOST', DB_USER: '$DB_USER', DB_NAME: '$DB_NAME', DB_PASSWORD: '${DB_PASSWORD}'" # Added debug for values
    exit 1
fi
log_message "Database credentials read successfully."

# Verify database connection
log_message "Verifying database connection..."
if ! mysql -h "${DB_HOST}" -u "${DB_USER}" -p"${DB_PASSWORD}" -e "USE ${DB_NAME}; SELECT 1;" >/dev/null 2>&1; then
    log_message "ERROR: Cannot connect to database '${DB_NAME}' with user '${DB_USER}'. Check credentials in config.py and database setup."
    exit 1
fi
log_message "Database connection verified."


# Function to get password securely (kept for get_password_securely, but direct reads are used now)
get_password_securely() {
    local prompt_text="$1"
    local password_var="$2"
    while true; do
        read -rsp "${prompt_text}: " PASSWORD_INPUT
        echo
        read -rsp "Confirm ${prompt_text}: " PASSWORD_CONFIRM
        echo
        if [[ "$PASSWORD_INPUT" == "$PASSWORD_CONFIRM" && -n "$PASSWORD_INPUT" ]]; then
            eval "$password_var='$PASSWORD_INPUT'"
            break
        else
            log_message "Passwords do not match or are empty. Please try again."
        fi
    done
}


# Get admin credentials (these prompts are fine as is)
ADMIN_EMAIL=""
ADMIN_PASSWORD=""
ADMIN_PHONE=""
ADMIN_IVR_PASSCODE="" # New variable for IVR passcode

while [ -z "$ADMIN_EMAIL" ]; do read -p "Enter admin email: " ADMIN_EMAIL; if [ -z "$ADMIN_EMAIL" ]; then log_message "WARN: Admin email cannot be empty."; continue; fi; if ! echo "$ADMIN_EMAIL" | grep -E -q '^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'; then log_message "WARN: Invalid email format."; ADMIN_EMAIL=""; fi; done
while [ -z "$ADMIN_PASSWORD" ]; do read -s -p "Enter admin password (min 8 characters): " ADMIN_PASSWORD; echo; if [ -z "$ADMIN_PASSWORD" ]; then log_message "WARN: Admin password cannot be empty."; continue; fi; if [ ${#ADMIN_PASSWORD} -lt 8 ]; then log_message "WARN: Password must be at least 8 characters long."; ADMIN_PASSWORD=""; continue; fi; read -s -p "Confirm admin password: " ADMIN_PASSWORD_CONFIRM; echo; if [ "$ADMIN_PASSWORD" != "$ADMIN_PASSWORD_CONFIRM" ]; then log_message "WARN: Passwords do not match."; ADMIN_PASSWORD=""; fi; done
while [ -z "$ADMIN_IVR_PASSCODE" ]; do read -s -p "Enter 6-digit IVR passcode (numbers only): " ADMIN_IVR_PASSCODE; echo; if [ -z "$ADMIN_IVR_PASSCODE" ]; then log_message "WARN: IVR passcode cannot be empty."; continue; fi; if ! [[ "$ADMIN_IVR_PASSCODE" =~ ^[0-9]{6}$ ]]; then log_message "WARN: IVR passcode must be exactly 6 digits."; ADMIN_IVR_PASSCODE=""; continue; fi; read -s -p "Confirm 6-digit IVR passcode: " ADMIN_IVR_PASSCODE_CONFIRM; echo; if [ "$ADMIN_IVR_PASSCODE" != "$ADMIN_IVR_PASSCODE_CONFIRM" ]; then log_message "WARN: IVR passcodes do not match."; ADMIN_IVR_PASSCODE=""; fi; done
while [ -z "$ADMIN_PHONE" ]; do read -p "Enter admin phone number (digits only): " ADMIN_PHONE; if [ -z "$ADMIN_PHONE" ]; then log_message "WARN: Admin phone number cannot be empty."; continue; fi; if ! [[ "$ADMIN_PHONE" =~ ^[0-9]+$ ]]; then log_message "WARN: Phone number should contain only digits."; ADMIN_PHONE=""; fi; done


# Create admin user using Python script
log_message "Creating/Verifying admin user..."
# cd "${APP_ROOT}" # Already handled by APP_ROOT, no need to cd here

# Create Python script to add admin user (UPDATED CONTENT)
cat > /tmp/create_admin.py << EOF
import mysql.connector
import bcrypt
import sys
import os

# Ensure the project directory is in the Python path
project_home = '${APP_ROOT}' # Use APP_ROOT variable
if project_home not in sys.path: sys.path.insert(0, project_home)
try: import config
except ImportError: print(f"Error: config.py not found", file=sys.stderr); exit(1)

admin_email = sys.argv[1]
admin_password = sys.argv[2]
admin_ivr_passcode = sys.argv[3] # New argument for IVR passcode
admin_phone = sys.argv[4] # Updated index for phone number

try:
    mydb = mysql.connector.connect(host=config.DB_HOST, user=config.DB_USER, password=config.DB_PASSWORD, database=config.DB_NAME)
    cursor = mydb.cursor()

    cursor.execute('SELECT id FROM users WHERE email = %s', (admin_email,))
    existing_user = cursor.fetchone()

    if existing_user:
        print(f"Admin user {admin_email} exists. Updating password and IVR passcode hash.", file=sys.stdout) # Changed log to print
        hashed_password = bcrypt.hashpw(admin_password.encode('utf-8'), bcrypt.gensalt())
        hashed_ivr_passcode = bcrypt.hashpw(admin_ivr_passcode.encode('utf-8'), bcrypt.gensalt())
        update_query = 'UPDATE users SET password = %s, phone_number = %s, ivr_passcode_hash = %s, role = %s WHERE email = %s'
        update_params = (hashed_password.decode('utf-8'), admin_phone, hashed_ivr_passcode.decode('utf-8'), 'admin', admin_email)
        cursor.execute(update_query, update_params)
        mydb.commit()
        print(f'Admin user {admin_email} password and IVR passcode updated successfully!', file=sys.stdout) # Changed log to print
    else:
        print("Admin user does not exist. Creating new user.", file=sys.stdout) # Changed log to print
        hashed_password = bcrypt.hashpw(admin_password.encode('utf-8'), bcrypt.gensalt())
        hashed_ivr_passcode = bcrypt.hashpw(admin_ivr_passcode.encode('utf-8'), bcrypt.gensalt()) # Hash IVR passcode

        query = 'INSERT INTO users (email, password, phone_number, ivr_passcode_hash, role) VALUES (%s, %s, %s, %s, %s)'
        params = (admin_email, hashed_password.decode('utf-8'), admin_phone, hashed_ivr_passcode.decode('utf-8'), 'admin')
        cursor.execute(query, params)
        mydb.commit()
        print(f'Admin user {admin_email} created successfully!', file=sys.stdout) # Changed log to print

    cursor.close()
    mydb.close()
except mysql.connector.Error as err:
    print(f"Database Error: {err}", file=sys.stderr)
    exit(1)
except Exception as e:
    print(f'Error: {str(e)}', file=sys.stderr)
    exit(1)
EOF

# Run the script within the virtual environment (UPDATED ARGUMENTS)
log_message "Executing Python script to create admin user..."
# The python script will print its own messages, so we only tee the script's stdout/stderr to LOG_FILE
if ! "${APP_ROOT}/venv/bin/python3" /tmp/create_admin.py "$ADMIN_EMAIL" "$ADMIN_PASSWORD" "$ADMIN_IVR_PASSCODE" "$ADMIN_PHONE" 2>&1 | tee -a "${LOG_FILE}"; then
    log_message "ERROR: Failed to create admin user. Check logs."
    rm -f /tmp/create_admin.py
    exit 1
fi
rm -f /tmp/create_admin.py
log_message "Admin user creation script finished."

# Set proper permissions (Using asterisk:asterisk)
log_message "Setting final file permissions for user asterisk:asterisk..."
if ! getent group asterisk > /dev/null 2>&1; then log_message "WARN: Group 'asterisk' not found."; else if ! id -u asterisk > /dev/null 2>&1; then log_message "WARN: User 'asterisk' not found."; else
    chown -R asterisk:asterisk "${APP_ROOT}" 2>&1 | tee -a "${LOG_FILE}"
    find "${APP_ROOT}" -type d -exec chmod 775 {} \; 2>&1 | tee -a "${LOG_FILE}"
    find "${APP_ROOT}" -type f -exec chmod 664 {} \; 2>&1 | tee -a "${LOG_FILE}"
    # Specific permission for config.py for security
    chown root:asterisk "${APP_ROOT}/config.py" 2>&1 | tee -a "${LOG_FILE}"
    chmod 640 "${APP_ROOT}/config.py" 2>&1 | tee -a "${LOG_FILE}"
    # Specific permission for wsgi.py
    chmod 664 "${APP_ROOT}/wsgi.py" 2>&1 | tee -a "${LOG_FILE}"
    # Ensure venv executables are executable by owner
    chmod +x "${APP_ROOT}/venv/bin/activate"
    chmod +x "${APP_ROOT}/venv/bin/python"
    chmod +x "${APP_ROOT}/venv/bin/python3"
    # Ensure uploads and logs directories are writable by group
    chmod 775 "${APP_ROOT}/uploads" 2>&1 | tee -a "${LOG_FILE}"
    chmod 775 "${APP_ROOT}/logs" 2>&1 | tee -a "${LOG_FILE}"
fi; fi

# Restart Apache FIRST
log_message "Restarting Apache web server..."
if ! systemctl restart apache2 2>&1 | tee -a "${LOG_FILE}"; then
    log_message "ERROR: Failed to restart Apache. Check 'systemctl status apache2' and Apache error logs."
    apache2ctl configtest || true
    exit 1
fi
if ! systemctl is-active --quiet apache2; then log_message "ERROR: Apache service failed to start after restart."; exit 1; fi
log_message "Apache service is active."

# --- Configure firewall using Incredible PBX method ---
FIREWALL_CUSTOM_SCRIPT="/usr/local/sbin/iptables-custom"
FIREWALL_RESTART_SCRIPT="/usr/local/sbin/iptables-restart"
INFOCALL_PORT="5000"

log_message "Configuring firewall for InfoCall App Port ${INFOCALL_PORT} using ${FIREWALL_CUSTOM_SCRIPT}..."

if [ -f "${FIREWALL_CUSTOM_SCRIPT}" ] && [ -f "${FIREWALL_RESTART_SCRIPT}" ]; then
    # Check if the rule already exists in the custom script
    if ! grep -qE -- "--dport ${INFOCALL_PORT} -j ACCEPT" "${FIREWALL_CUSTOM_SCRIPT}"; then
        log_message "Port ${INFOCALL_PORT} rule not found in ${FIREWALL_CUSTOM_SCRIPT}. Adding rule..."
        # Create the rule line
        RULE_LINE="/sbin/iptables -A INPUT -p tcp --dport ${INFOCALL_PORT} -j ACCEPT"
        # Create a temporary file with the rule and a comment
        TMP_RULE_FILE=$(mktemp)
        echo "# Rule for InfoCall Application" > "${TMP_RULE_FILE}"
        echo "${RULE_LINE}" >> "${TMP_RULE_FILE}"
        echo "" >> "${TMP_RULE_FILE}" # Add a blank line after

        # Use sed to insert the rule file contents before a common end marker
        # Adjust marker if needed (e.g., final REJECT/DROP, or specific comment)
        MARKER_LINE="/# End of Trusted Provider Section/" # Using marker from add-ip script
        # Fallback marker if the first one isn't found
        FALLBACK_MARKER_LINE="/^### END OF CUSTOM RULES ###/" # A common pattern
        FALLBACK_MARKER_LINE2="/REJECT --reject-with icmp-host-prohibited/" # Another common end rule

        if grep -q "$MARKER_LINE" "$FIREWALL_CUSTOM_SCRIPT"; then
             log_message "Inserting rule before '$MARKER_LINE' in ${FIREWALL_CUSTOM_SCRIPT}"
             sed -i "$MARKER_LINE r ${TMP_RULE_FILE}" "$FIREWALL_CUSTOM_SCRIPT"
        elif grep -q "$FALLBACK_MARKER_LINE" "$FIREWALL_CUSTOM_SCRIPT"; then
             log_message "Inserting rule before '$FALLBACK_MARKER_LINE' in ${FIREWALL_CUSTOM_SCRIPT}"
             sed -i "$FALLBACK_MARKER_LINE r ${TMP_RULE_FILE}" "$FIREWALL_CUSTOM_SCRIPT"
        elif grep -q "$FALLBACK_MARKER_LINE2" "$FIREWALL_CUSTOM_SCRIPT"; then
             log_message "Inserting rule before '$FALLBACK_MARKER_LINE2' in ${FIREWALL_CUSTOM_SCRIPT}"
             sed -i "$FALLBACK_MARKER_LINE2 i # Inserting InfoCall Rule Before Final Reject" "$FIREWALL_CUSTOM_SCRIPT" # Add comment
             sed -i "$FALLBACK_MARKER_LINE2 r ${TMP_RULE_FILE}" "$FIREWALL_CUSTOM_SCRIPT"
        else
             log_message "WARN: Could not find standard marker in ${FIREWALL_CUSTOM_SCRIPT}. Appending rule. Please verify position manually."
             echo "# Appended InfoCall Rule - Verify Position" >> "$FIREWALL_CUSTOM_SCRIPT"
             cat "${TMP_RULE_FILE}" >> "$FIREWALL_CUSTOM_SCRIPT"
        fi
        rm -f "${TMP_RULE_FILE}"
        log_message "Rule added to ${FIREWALL_CUSTOM_SCRIPT}. Restarting firewall..."
        # Run the Incredible PBX firewall restart script
        if ! "${FIREWALL_RESTART_SCRIPT}" 2>&1 | tee -a "${LOG_FILE}"; then
            log_message "ERROR: Failed to restart firewall using ${FIREWALL_RESTART_SCRIPT}. Check firewall status manually."
            # Don't necessarily exit, but warn user
        else
             log_message "Firewall restarted successfully."
        fi
    else
        log_message "INFO: Firewall rule for port ${INFOCALL_PORT} already exists in ${FIREWALL_CUSTOM_SCRIPT}."
    fi
else
    log_message "WARN: Could not find Incredible PBX firewall scripts (${FIREWALL_CUSTOM_SCRIPT} or ${FIREWALL_RESTART_SCRIPT})."
    log_message "WARN: Firewall rule for port ${INFOCALL_PORT} has NOT been made persistent."
    log_message "WARN: Attempting to add temporary rule with iptables..."
    if command -v iptables >/dev/null; then
         if ! iptables -C INPUT -p tcp --dport ${INFOCALL_PORT} -j ACCEPT >/dev/null 2>&1; then
             iptables -I INPUT -p tcp --dport ${INFOCALL_PORT} -j ACCEPT 2>&1 | tee -a "${LOG_FILE}"
             log_message "WARN: Added TEMPORARY iptables rule. Use 'iptables-persistent' to save if needed."
         fi
    else
        log_message "ERROR: iptables command not found. Port ${INFOCALL_PORT} is likely blocked."
    fi
fi
# --- End of Firewall Section ---


# Create checkpoint (using touch)
touch "${LOG_DIR}/.checkpoint_finalize_setup"

SERVER_IP=$(hostname -I | awk '{print $1}')
log_message "====================================================="
log_message " InfoCall Final Setup Complete! "
log_message "====================================================="
log_message "You should now be able to access:"
log_message "  - FreePBX GUI at http://${SERVER_IP}/ (or https if configured)"
log_message "  - InfoCall App at http://${SERVER_IP}:5000/"
log_message ""
log_message "Login to InfoCall with email: ${ADMIN_EMAIL}, the password you provided, and your 6-digit IVR passcode."
log_message "Installation logs are available in: ${LOG_DIR}/"
log_message "Thank you for installing InfoCall!"