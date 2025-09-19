#!/bin/bash
# 00-setup-environment.sh
# Sets up common variables, logging, and checks for root privileges.
# This script is sourced by other installation scripts.

# --- Configuration Variables ---
APP_ROOT="/var/www/html/infocall"
LOG_DIR="${APP_ROOT}/logs"
LOG_FILE="${LOG_DIR}/infocall_install_$(date +%Y%m%d_%H%M%S).log"

# --- Functions ---

# Function to check for root privileges
check_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "ERROR: This script must be run as root. Please use sudo." >&2
        exit 1
    fi
}

# Function to log messages to console and file
log_message() {
    # Ensure LOG_FILE is defined before attempting to write
    if [[ -z "$LOG_FILE" ]]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >&2 # Fallback to stderr if log_file not set
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "${LOG_FILE}"
    fi
}

# Function to log an error message and exit
error_exit() {
    log_message "ERROR: $1" >&2
    exit 1
}

# --- Main Script Execution ---

# Ensure we are running as root
check_root

# Ensure APP_ROOT exists
mkdir -p "${APP_ROOT}" || error_exit "Failed to create application root directory: ${APP_ROOT}"

# Ensure LOG_DIR exists and set correct permissions
mkdir -p "${LOG_DIR}" || error_exit "Failed to create log directory: ${LOG_DIR}"
chown asterisk:asterisk "${LOG_DIR}" || error_exit "Failed to set ownership for log directory: ${LOG_DIR}"
chmod 775 "${LOG_DIR}" || error_exit "Failed to set permissions for log directory: ${LOG_DIR}"

# Setup logging to file
# Redirect all stdout/stderr to the log file from this point forward
# For sourcing, it's safer to use tee -a in log_message itself.
log_message "Environment setup complete. Installation logs will be saved to ${LOG_FILE}"

# Export APP_ROOT for other scripts that source this one
export APP_ROOT
export LOG_DIR
export LOG_FILE

# Check for checkpoint
if [[ -f "${LOG_DIR}/.checkpoint_environment_setup" ]]; then
    log_message "WARNING: Environment setup already completed. To run again, remove ${LOG_DIR}/.checkpoint_environment_setup file."
    read -p "Do you want to continue anyway? (y/n): " CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        log_message "Environment setup skipped."
        exit 0
    fi
    log_message "Continuing with environment setup."
fi

log_message "Environment setup complete!"
touch "${LOG_DIR}/.checkpoint_environment_setup" # Create checkpoint