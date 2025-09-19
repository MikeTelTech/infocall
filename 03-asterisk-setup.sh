#!/bin/bash
# 03-asterisk-setup.sh
# Configures Asterisk integration for InfoCall, including AMI access.

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

log_message "Step 3: Asterisk Configuration"

# Check for checkpoint to prevent re-running without confirmation
if [[ -f "${LOG_DIR}/.checkpoint_asterisk_setup" ]]; then
    log_message "WARNING: Asterisk setup already completed. To run again, remove ${LOG_DIR}/.checkpoint_asterisk_setup file."
    read -p "Do you want to continue anyway? (y/n): " CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        log_message "Asterisk setup skipped."
        exit 0
    fi
    log_message "Continuing with Asterisk setup."
fi

# --- 1. Prompt for Asterisk AMI Credentials ---
log_message "Prompting for Asterisk AMI credentials..."

# Prompt for AMI username, ensure it's not blank
AMI_USER=""
while [ -z "$AMI_USER" ]; do
    read -p "Enter Asterisk AMI username: " AMI_USER
    if [ -z "$AMI_USER" ]; then
        log_message "WARN: AMI username cannot be empty. Please try again."
    fi
done

# Prompt for AMI password securely with confirmation
AMI_SECRET=""
while [ -z "$AMI_SECRET" ]; do
    read -s -p "Enter Asterisk AMI secret (password): " AMI_SECRET
    echo # Add a newline after the secure password input
    if [ -z "$AMI_SECRET" ]; then
        log_message "WARN: AMI secret cannot be empty."
        continue
    fi
    read -s -p "Confirm Asterisk AMI secret: " AMI_SECRET_CONFIRM
    echo # Add a newline after the secure password input
    if [ "$AMI_SECRET" != "$AMI_SECRET_CONFIRM" ]; then
        log_message "WARN: Secrets do not match. Please try again."
        AMI_SECRET=""
    fi
done

log_message "Asterisk AMI credentials received."

# AMI configuration file
MANAGER_CONF="/etc/asterisk/manager.conf"

# Backup original manager.conf
if [[ ! -f "${MANAGER_CONF}.bak" ]]; then
    cp "${MANAGER_CONF}" "${MANAGER_CONF}.bak" || log_message "WARNING: Could not back up ${MANAGER_CONF}."
fi

# Define the full AMI config block including markers for robust replacement
# Use a temporary file for the block content
AMI_CONFIG_BLOCK_CONTENT="
[${AMI_USER}]
secret = ${AMI_SECRET}
deny=0.0.0.0/0.0.0.0
permit=127.0.0.1/255.255.255.0
read = system,call,log,verbose,command,agent,user,config,command,dtmf,reporting,cdr,dialplan,originate,message
write = system,call,log,verbose,command,agent,user,config,command,dtmf,reporting,cdr,dialplan,originate,message
"
AMI_CONFIG_START_MARKER="; --- BEGIN INFOCALL AMI USER CONFIG ---"
AMI_CONFIG_END_MARKER="; --- END INFOCALL AMI USER CONFIG ---"

FULL_AMI_CONFIG_BLOCK="${AMI_CONFIG_START_MARKER}\n${AMI_CONFIG_BLOCK_CONTENT}\n${AMI_CONFIG_END_MARKER}"

# Write the block to a temporary file for atomic insertion
TMP_AMI_BLOCK_FILE=$(mktemp)
echo -e "${FULL_AMI_CONFIG_BLOCK}" > "${TMP_AMI_BLOCK_FILE}"

# Use sed to delete the old block if it exists, then re-append the new one
if grep -qF "${AMI_CONFIG_START_MARKER}" "${MANAGER_CONF}"; then
    log_message "Existing AMI user '${AMI_USER}' configuration block found. Deleting old block..."
    sed -i "/${AMI_CONFIG_START_MARKER}/,/${AMI_CONFIG_END_MARKER}/d" "${MANAGER_CONF}" || error_exit "Failed to delete old AMI user block."
fi

log_message "Appending/re-appending AMI user '${AMI_USER}' configuration to ${MANAGER_CONF}..."
cat "${TMP_AMI_BLOCK_FILE}" >> "${MANAGER_CONF}" || error_exit "Failed to append AMI user to ${MANAGER_CONF}."
rm -f "${TMP_AMI_BLOCK_FILE}" # Clean up temp file (corrected variable name)

log_message "Asterisk AMI user configured."

# --- 2. Update config.py with AMI Credentials ---
log_message "Updating AMI credentials in ${APP_ROOT}/config.py..."
DB_CONFIG_FILE="${APP_ROOT}/config.py" # config.py also holds AMI credentials

# Use robust sed regex to capture AMI_USERNAME/AMI_SECRET based on their current quotes
sed -i "s/^\(AMI_USERNAME = \)['\"][^'\"]*['\"]/\1'${AMI_USER}'/" "${DB_CONFIG_FILE}"
sed -i "s/^\(AMI_SECRET = \)['\"][^'\"]*['\"]/\1'${AMI_SECRET}'/" "${DB_CONFIG_FILE}"
log_message "AMI credentials updated in ${DB_CONFIG_FILE}."

# --- 3. Reload Asterisk AMI ---
log_message "Reloading Asterisk AMI to apply changes..."
sudo asterisk -rx "manager reload" || log_message "WARNING: Failed to reload Asterisk manager. Check Asterisk status."
log_message "Asterisk AMI reloaded."

# --- Create Checkpoint ---
log_message "Asterisk configuration complete!"
touch "${LOG_DIR}/.checkpoint_asterisk_setup"

log_message "Step 3: Asterisk Configuration Complete"