#!/bin/bash
# 01-system-setup.sh
# Sets up the system environment for InfoCall, including Python, MariaDB, and Apache dependencies.

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

# --- NEW ADDITION - Display AGPL Notice START ---
if [[ -f "${INSTALL_DIR}/AGPL_notice.txt" ]]; then
    log_message "--- GNU Affero General Public License Notice ---"
    # Display the content of AGPL_notice.txt using log_message for consistent output
    while IFS= read -r line; do
        log_message "$line"
    done < "${INSTALL_DIR}/AGPL_notice.txt"
    log_message "--- End of AGPL Notice ---"
    read -p "Press Enter to acknowledge and continue with setup..."
else
    log_message "WARNING: AGPL_notice.txt not found in ${INSTALL_DIR}. Please ensure it is in the same directory as this script."
fi
# --- NEW ADDITION - Display AGPL Notice END ---

log_message "Step 1: System Setup"

# Check for checkpoint to prevent re-running without confirmation
if [[ -f "${LOG_DIR}/.checkpoint_system_setup" ]]; then
    log_message "WARNING: System setup already completed. To run again, remove ${LOG_DIR}/.checkpoint_system_setup file."
    read -p "Do you want to continue anyway? (y/n): " CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        log_message "System setup skipped."
        exit 0
    fi
    log_message "Continuing with system setup."
fi

# --- 1. Update System Packages ---
log_message "Updating system packages..."
sudo apt update -y || log_message "WARNING: apt update failed. Check internet connection and package sources."
# sudo apt upgrade -y # Optional: uncomment if you want to upgrade all system packages
log_message "System packages updated."

# --- 2. Install Required System Dependencies ---
log_message "Installing required system dependencies..."

sudo apt install -y \
    python3 \
    python3-venv \
    python3-pip \
    mariadb-server \
    mariadb-client \
    libmariadb-dev \
    libmariadb-dev-compat \
    apache2 \
    libapache2-mod-wsgi-py3 \
    python3-dev \
    libsndfile1-dev \
    ffmpeg \
    || error_exit "Failed to install required system dependencies."

log_message "Required system dependencies installed."

# --- 3. Set up Python Virtual Environment ---
log_message "Creating application directory: ${APP_ROOT}..."
mkdir -p "${APP_ROOT}" || error_exit "Failed to create application directory: ${APP_ROOT}."

log_message "Setting up Python virtual environment in ${APP_ROOT}/venv..."
python3 -m venv "${APP_ROOT}/venv" || error_exit "Failed to create Python virtual environment."

# Set ownership and permissions BEFORE pip install
log_message "Setting ownership of application directory and venv to asterisk:asterisk..."
chown -R asterisk:asterisk "${APP_ROOT}" || error_exit "Failed to set ownership for ${APP_ROOT} to asterisk:asterisk."
log_message "Ensuring pip executable within venv has execute permissions..."
chmod +x "${APP_ROOT}/venv/bin/pip" || log_message "WARNING: Failed to set execute permissions for venv pip."
# Also ensure other venv executables like 'python' are executable by owner
chmod +x "${APP_ROOT}/venv/bin/python" || log_message "WARNING: Failed to set execute permissions for venv python."
log_message "Ownership and permissions set for ${APP_ROOT}."

log_message "Creating requirements.txt file..."
# Create requirements.txt with necessary Python packages
cat << EOF > "${APP_ROOT}/requirements.txt"
Flask==2.2.3
mysql-connector-python==8.0.32
bcrypt==4.0.1
pydub==0.25.1
Werkzeug==2.2.3
twilio>=7.0,<8.0
pyst2==0.5.1
EOF

log_message "Installing Python dependencies from requirements.txt..."
# Activate venv and install requirements (use full path to pip within venv)
"${APP_ROOT}/venv/bin/pip" install -r "${APP_ROOT}/requirements.txt" || error_exit "Failed to install Python dependencies."
log_message "Python packages installed successfully."


# --- Create Checkpoint ---
log_message "System preparation complete!"
touch "${LOG_DIR}/.checkpoint_system_setup"

log_message "Step 1: System Setup Complete"