#!/bin/bash

# install_ivr.sh
# This script installs/updates the Infocall Dial-in IVR feature.
# It handles Asterisk dialplan modifications, AGI script deployment,
# and sound file placement, ensuring correct permissions.
# This script should be run from: /var/www/html/infocall/dialin_ivr_install/

# --- Configuration Variables ---
# Determine the directory where this script is located
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"

# Standard Asterisk directories
ASTERISK_ETC_DIR="/etc/asterisk"
ASTERISK_AGI_BIN_DIR="/var/lib/asterisk/agi-bin"
ASTERISK_SOUNDS_CUSTOM_DIR="/var/lib/asterisk/sounds/custom"

# Specific file paths
IVR_EXTENSIONS_CONF="${ASTERISK_ETC_DIR}/extensions_custom.conf"

# Source directory for sound files (assumed to be a subfolder named 'Sound_files')
SOUND_FILES_SOURCE_DIR="${INSTALL_DIR}/Sound_files"

# Asterisk user and group for file permissions
ASTERISK_USER="asterisk"
ASTERISK_GROUP="asterisk"

# Markers for the IVR context in extensions_custom.conf for idempotent updates
IVR_CONTEXT_START_MARKER="; --- BEGIN INFOCALL IVR RECORDING CONTEXT ---"
IVR_CONTEXT_END_MARKER="; --- END INFOCALL IVR RECORDING CONTEXT ---"
OLD_IVR_CONTEXT_START_MARKER="# --- BEGIN INFOCALL DIAL-IN IVR CONTEXT ---" # Marker from previous script
OLD_IVR_CONTEXT_END_MARKER="# --- END INFOCALL DIAL-IN IVR CONTEXT ---" # Marker from previous script

# Default IVR extension
DEFAULT_IVR_EXTENSION="732"
IVR_EXTENSION=""

# --- Helper Functions ---

# Function to log messages with a timestamp
log_message() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1"
}

# Function to log an error message and exit
error_exit() {
    log_message "ERROR: $1" >&2
    exit 1
}

# Function to check if the script is run as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        error_exit "This script must be run as root. Please use sudo."
    fi
}

# Function to prompt the user for the IVR extension number with validation
prompt_for_extension() {
    log_message "Prompting for IVR extension number..."
    while true; do
        read -p "Enter the desired IVR extension number (3 to 4 digits, default: ${DEFAULT_IVR_EXTENSION}): " IVR_EXTENSION_INPUT
        IVR_EXTENSION_INPUT=${IVR_EXTENSION_INPUT:-$DEFAULT_IVR_EXTENSION} # Set default if empty

        # Validate that the input is purely numeric and between 3 and 4 digits long
        if [[ "$IVR_EXTENSION_INPUT" =~ ^[0-9]{3,4}$ ]]; then
            IVR_EXTENSION="$IVR_EXTENSION_INPUT"
            log_message "Using IVR Extension: ${IVR_EXTENSION}"
            break
        else
            log_message "Invalid input. The extension must be a numeric value between 3 and 4 digits."
        fi
    done
}

# Function to copy AGI (Asterisk Gateway Interface) files and fix shebangs
copy_agi_files() {
    log_message "Copying AGI files..."
    local agi_source_files=("check_passcode.agi" "ivr_handler.agi" "lookup_user_for_ivr.agi")
    local agi_dest_names=("check_passcode.agi" "ivr_handler.agi" "lookup_user_for_ivr.agi")
    local correct_shebang_raw="#!/var/www/html/infocall/venv/bin/python3" # Define the correct shebang

    # Create the destination directory if it doesn't exist
    mkdir -p "${ASTERISK_AGI_BIN_DIR}" || error_exit "Failed to create AGI binary directory: ${ASTERISK_AGI_BIN_DIR}"

    for i in "${!agi_source_files[@]}"; do
        local source_file="${INSTALL_DIR}/${agi_source_files[$i]}"
        local dest_name="${agi_dest_names[$i]}"
        local dest_path="${ASTERISK_AGI_BIN_DIR}/${dest_name}"

        if [[ ! -f "$source_file" ]]; then
            error_exit "Required AGI source file not found: ${source_file}. Please ensure it exists."
        fi

        log_message "Copying ${source_file} to ${dest_path}..."
        cp -v "${source_file}" "${dest_path}" || error_exit "Failed to copy AGI file ${source_file} to ${dest_path}."

        # --- NEW AGGRESSIVE SHEBANG FIX using sed ---
        log_message "Aggressively correcting shebang for ${dest_path} to '${correct_shebang_raw}'..."
        # Replace the first line of the file with the correct shebang.
        # Use @ as delimiter for sed to avoid conflicts with / in the path.
        sed -i.bak "1s@^.*\$@${correct_shebang_raw}@" "${dest_path}" 

        if [ $? -ne 0 ]; then
            log_message "CRITICAL WARNING: Failed to aggressively correct shebang for ${dest_path} using sed. Manual intervention may be required."
            # Attempt to revert from backup if sed failed significantly
            [ -f "${dest_path}.bak" ] && mv "${dest_path}.bak" "${dest_path}"
            error_exit "Shebang correction failed for ${dest_path}."
        else
            # Remove the backup file created by sed -i.bak
            rm -f "${dest_path}.bak"
            log_message "Shebang corrected for ${dest_path}."
        fi
        # --- END NEW ---
    done

    log_message "AGI files copied and shebangs processed in ${ASTERISK_AGI_BIN_DIR}."
}

# Function to copy sound files to the Asterisk custom sounds directory
copy_sound_files() {
    log_message "Copying sound files..."

    # Create the destination directory if it doesn't exist
    mkdir -p "${ASTERISK_SOUNDS_CUSTOM_DIR}" || error_exit "Failed to create custom sounds directory: ${ASTERISK_SOUNDS_CUSTOM_DIR}"

    # Check if the source directory exists and has files
    if [[ ! -d "${SOUND_FILES_SOURCE_DIR}" ]]; then
        log_message "WARNING: Sound files source directory not found: ${SOUND_FILES_SOURCE_DIR}. Skipping sound file copy."
        return 0 # Exit function gracefully if no source files
    fi

    local sound_files=("$SOUND_FILES_SOURCE_DIR"/*)
    if (( ${#sound_files[@]} == 0 )) || [[ "${sound_files[0]}" == "${SOUND_FILES_SOURCE_DIR}/*" ]]; then
        log_message "No sound files found in ${SOUND_FILES_SOURCE_DIR}. Skipping sound file copy."
        return 0 # Exit function gracefully if no files found
    fi

    log_message "Copying files from ${SOUND_FILES_SOURCE_DIR} to ${ASTERISK_SOUNDS_CUSTOM_DIR}..."
    # Use cp -v for verbose output
    cp -v "${SOUND_FILES_SOURCE_DIR}"/* "${ASTERISK_SOUNDS_CUSTOM_DIR}"/ || error_exit "Failed to copy sound files."
    log_message "Sound files copied successfully."
}

# Function to insert or update the IVR context in extensions_custom.conf (FINAL REVISED - Simplest)
insert_or_update_extensions_conf() {
    log_message "Modifying ${IVR_EXTENSIONS_CONF} using a robust rebuild strategy..."

    # Define the path to the external template file
    local EXTERNAL_TEMPLATE_FILE="${INSTALL_DIR}/ivr_context_template.conf"

    # Read the content of the external template file into the variable
    if [ ! -f "${EXTERNAL_TEMPLATE_FILE}" ]; then
        log_message "CRITICAL ERROR: External IVR template file not found: ${EXTERNAL_TEMPLATE_FILE}"
        error_exit "Please ensure 'ivr_context_template.conf' exists in the script directory."
    fi
    local IVR_CONTEXT_TEMPLATE_READ=$(cat "${EXTERNAL_TEMPLATE_FILE}")


    local new_content_file=$(mktemp) # Temp file to build the new extensions_custom.conf
    local IVR_CONTEXT_FINAL=$(echo "${IVR_CONTEXT_TEMPLATE_READ}" | sed "s/__IVR_EXTENSION__/${IVR_EXTENSION}/g")

    # --- DIAGNOSTIC CHECK (removed) ---
    # These diagnostic checks are no longer needed now that template reading is stable.
    # --- END DIAGNOSTIC CHECK ---

    local in_infocall_block="false"
    local in_old_infocall_block="false"

    # Define markers for the [from-internal-custom] include section
    local INCLUDE_START_MARKER="; --- BEGIN INFOCALL FROM-INTERNAL-CUSTOM INCLUDE ---"
    local INCLUDE_END_MARKER="; --- END INFOCALL FROM-INTERNAL-CUSTOM INCLUDE ---"

    # Read existing extensions_custom.conf, filtering out old InfoCall sections and includes
    if [ -f "${IVR_EXTENSIONS_CONF}" ]; then
        while IFS= read -r line; do
            # Filter out the main IVR context blocks (new format)
            if [[ "$line" == *"${IVR_CONTEXT_START_MARKER}"* ]]; then in_infocall_block="true"; continue; fi
            if [[ "$line" == *"${IVR_CONTEXT_END_MARKER}"* ]]; then in_infocall_block="false"; continue; fi
            if [ "$in_infocall_block" == "true" ]; then continue; fi

            # Filter out old IVR context blocks
            if [[ "$line" == *"${OLD_IVR_CONTEXT_START_MARKER}"* ]]; then in_old_infocall_block="true"; continue; fi
            if [[ "$line" == *"${OLD_IVR_CONTEXT_END_MARKER}"* ]]; then in_old_infocall_block="false"; continue; fi
            if [ "$in_old_infocall_block" == "true" ]; then continue; fi

            # Filter out the specific include block markers and the include line itself (to re-add cleanly)
            if [[ "$line" == *"${INCLUDE_START_MARKER}"* ]]; then continue; fi
            if [[ "$line" == *"${INCLUDE_END_MARKER}"* ]]; then continue; fi
            if [[ "$line" == "include => infocall-ivr-recording" ]]; then continue; fi
            if [[ "$line" == "[from-internal-custom]" ]]; then continue; fi # Filter this out to re-add carefully

            # Keep other lines that are not part of InfoCall modifications
            echo "$line" >> "$new_content_file"
        done < "${IVR_EXTENSIONS_CONF}"
        log_message "Existing '${IVR_EXTENSIONS_CONF}' processed. Old InfoCall sections and includes filtered."
    else
        log_message "${IVR_EXTENSIONS_CONF} does not exist, creating new content from scratch."
    fi

    # Append the new IVR context block (after all existing non-infocall content)
    echo -e "${IVR_CONTEXT_FINAL}" >> "$new_content_file"
    log_message "New IVR context (with '${IVR_EXTENSION}' inserted) appended to temporary file."

    # Append the [from-internal-custom] context and its include.
    # This is appended at the very end to ensure it's always present and clean.
    echo -e "\n${INCLUDE_START_MARKER}" >> "$new_content_file"
    echo -e "[from-internal-custom]" >> "$new_content_file"
    echo -e "include => infocall-ivr-recording" >> "$new_content_file"
    echo -e "${INCLUDE_END_MARKER}\n" >> "$new_content_file"
    log_message "[from-internal-custom] and 'include => infocall-ivr-recording' appended."

    # Atomically replace the original file
    mv "$new_content_file" "${IVR_EXTENSIONS_CONF}" || error_exit "Failed to write updated ${IVR_EXTENSIONS_CONF}."
    log_message "${IVR_EXTENSIONS_CONF} updated successfully with new content."
}

# Function to set correct file ownership and permissions for Asterisk
set_permissions() {
    log_message "Setting file ownership and permissions..."

    # AGI files permissions (owned by asterisk, executable)
    # The shebang fix is now part of copy_agi_files, but permissions need re-application
    if ls "${ASTERISK_AGI_BIN_DIR}"/*.agi 1>/dev/null 2>&1; then # Check if any AGI files exist before trying to modify
        chown "${ASTERISK_USER}:${ASTERISK_GROUP}" "${ASTERISK_AGI_BIN_DIR}"/* || log_message "Warning: Could not set ownership for AGI files."
        chmod +x "${ASTERISK_AGI_BIN_DIR}"/* || log_message "Warning: Could not set executable permission for AGI files."
    else
        log_message "No AGI files found to set permissions on (this may be expected if none were copied)."
    fi

    # Sound files permissions (owned by asterisk, readable by group/others)
    if ls "${ASTERISK_SOUNDS_CUSTOM_DIR}"/*.slin 1>/dev/null 2>&1 || \
       ls "${ASTERISK_SOUNDS_CUSTOM_DIR}"/*.gsm 1>/dev/null 2>&1 || \
       ls "${ASTERISK_SOUNDS_CUSTOM_DIR}"/*.wav 1>/dev/null 2>&1 || \
       ls "${ASTERISK_SOUNDS_CUSTOM_DIR}"/*.mp3 1>/dev/null 2>&1 || \
       ls "${ASTERISK_SOUNDS_CUSTOM_DIR}"/*.ulaw 1>/dev/null 2>&1; then
        chown -R "${ASTERISK_USER}:${ASTERISK_GROUP}" "${ASTERISK_SOUNDS_CUSTOM_DIR}" || log_message "Warning: Could not set ownership for sound files."
        chmod -R u=rwX,go=rX "${ASTERISK_SOUNDS_CUSTOM_DIR}" || log_message "Warning: Could not set permissions for sound files."
    else
        log_message "No sound files found to set permissions on (this may be expected if none were copied)."
    fi

    # extensions_custom.conf permissions (owned by asterisk, readable/writable by owner/group)
    chown "${ASTERISK_USER}:${ASTERISK_GROUP}" "${IVR_EXTENSIONS_CONF}" || log_message "Warning: Could not set ownership for ${IVR_EXTENSIONS_CONF}."
    chmod 664 "${IVR_EXTENSIONS_CONF}" || log_message "Warning: Could not set permissions for ${IVR_EXTENSIONS_CONF}."

    log_message "File ownership and permissions set."
}

# Function to reload Asterisk dialplan
reload_asterisk_dialplan() {
    log_message "Reloading Asterisk dialplan..."
    # Check if the 'asterisk' command is available
    if command -v asterisk >/dev/null 2>&1; then
        asterisk -rx "dialplan reload" || error_exit "Failed to reload Asterisk dialplan. Please check Asterisk status and logs for errors."
        log_message "Asterisk dialplan reloaded successfully."
    else
        log_message "Asterisk command not found. Please reload dialplan manually by accessing Asterisk CLI: 'asterisk -rv' then 'dialplan reload'."
    fi
}

# --- Main Execution Flow ---

# Ensure the script is run with root privileges
check_root

log_message "Starting INFOCALL Dial-in IVR Installation Script..."

# Step 1: Prompt user for the IVR extension number
prompt_for_extension

# Step 2: Copy AGI files to the Asterisk AGI directory (includes shebang fix)
copy_agi_files

# Step 3: Copy sound files to the Asterisk custom sounds directory
copy_sound_files

# Step 4: Modify extensions_custom.conf to include the IVR context
insert_or_update_extensions_conf

# Step 5: Set correct file ownership and permissions for Asterisk
set_permissions

# Step 6: Reload Asterisk dialplan to apply changes
reload_asterisk_dialplan

log_message "INFOCALL Dial-in IVR Installation Complete for extension: ${IVR_EXTENSION}!"
log_message "----------------------------------------------------------------------"
log_message "IMPORTANT: For the IVR to be reachable, ensure your Asterisk dialplan "
log_message "includes the 'from-internal-custom' context from your primary "
log_message "inbound context (e.g., 'from-internal')."
log_message "Example line in your main context (e.g., in extensions.conf):"
log_message "  include => from-internal-custom"
log_message "----------------------------------------------------------------------"
