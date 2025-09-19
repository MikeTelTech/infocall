INFOCALL Flask Application Installation Guide https://img.shields.io/github/downloads/MikeTelTech/infocall/total


This guide provides instructions for installing the INFOCALL Flask application on an Ubuntu 22.04 server with Incredible PBX 2027-U. INFOCALL is designed for automated communication campaigns,
leveraging Incredible PBX 2027-u  https://nerdvittles.com/happy-new-year-its-incredible-pbx-2027-for-ubuntu-22-04/

Prerequisites

Before proceeding with the installation, ensure your server meets the following requirements:

    Operating System: Ubuntu 22.04 LTS.
    PBX System: Incredible PBX 2027-U is already up and running. (Untested but likely will run - Asterisk 18+ installation with FreePBX )
    User Privileges: You must have sudo or root access to execute the installation scripts and modify system configurations.
    Internet Connectivity: The server requires an active internet connection to download necessary packages and dependencies during the installation process.
    Firewall: Be aware that Incredible PBX typically uses a locked-down firewall (e.g., Travelin' Man 3). Ensure your server's IP address (or the IP address from
    which you'll access the web interface) is whitelisted.
    Create a new Asterisk Manager (From the console select Settings -> Asterisk Manager Users   Create a new user with password you will need it during installation)

Directory Structure and Path Requirements

After unzipping the infocall.zip file, you will have a folder named infocall. You must copy the entire contents of this infocall folder into your web root directory,
specifically naming the destination folder infocall.

The recommended path for the application is /var/www/html/infocall/.
Recommended *** Save or copy the zipped file to the path /var/www/html/ and unzip the file there will result in the files and subfolder in the correct path /var/www/html/infocall/   ***

Your /var/www/html/infocall/ directory structure should look like this (essential files and folders listed):

/var/www/html/infocall/
├── app.py
├── app_state.py
├── .env                  (**Future and highly recommended** for environment variables, but currently in config.py ***DO NOT RUN PUBLIC FACING*** )
├── install-infocall.sh   (Main installation script)
├── infocall_db_structure.sql
├── requirements.txt
├── wsgi.py
├── 00-setup-environment.sh
├── 01-system-setup.sh
├── 02-database-setup.sh
├── 03-asterisk-setup.sh
├── 04-apache-setup.sh
├── 05-finalize-setup.sh
├── dialin_ivr_install/   (Contains install_ivr.sh, dialin_ivr.agi, and Sound_files/)
│   ├── install_ivr.sh
│   ├── dialin_ivr.agi
│   └── Sound_files/      (Place your IVR audio files here, e.g., welcome_ivr.wav)
├── models/               (Contains Python database models)
│   ├── __init__.py
│   ├── announcement.py
│   ├── app_setting.py
│   ├── call.py
│   ├── group.py
│   ├── member.py
│   ├── sms.py
│   └── user.py
├── routes/               (Contains Flask route definitions)
│   ├── __init__.py
│   ├── auth_routes.py
│   ├── call_routes.py
│   ├── member_routes.py
│   └── sms_routes.py
├── services/             (Contains business logic and external integrations)
│   ├── asterisk_service.py
│   ├── call_service.py
│   ├── sms_service.py
│   └── twilio_service.py
├── static/               (Contains CSS, JavaScript, and other static assets)
│   ├── emergency.css
│   └── styles.css
├── templates/            (Contains HTML Jinja2 templates)
│   ├── admin_settings.html
│   ├── ann_upload.html
│   ├── base.html
│   ├── call_mem.html
│   ├── create_account.html
│   ├── dashboard.html
│   ├── execute_call.html
│   ├── execute_sms.html
│   ├── group_management.html
│   ├── login.html
│   ├── main_menu.html
│   ├── member_dir.html
│   ├── sms_mem.html
│   ├── view_scheduled_calls.html
│   └── view_scheduled_sms.html
└── utils/                (Contains utility functions)
    ├── db.py
    ├── file_utils.py
    ├── security.py
    └── validation.py

Installation Steps
    
Navigate to Application Directory:
Change your current directory to the INFOCALL application root:

cd /var/www/html/infocall/

Make Scripts Executable:
chmod +x *.sh

Make Scripts Executable:
sudo chmod +x dialin_ivr_install/*.sh

Run the Installation Script:
Execute the main installer script with root privileges:
Bash

    sudo ./install-infocall.sh

    The install-infocall.sh script will then sequentially execute several sub-scripts (00-setup-environment.sh through 05-finalize-setup.sh) to:
        Set up the environment.
        Install system dependencies (e.g. Python libraries).
        Configure the MariaDB database and import the application schema.
        Integrate with Asterisk by copying AGI scripts, modifying extensions_custom.conf, and copying IVR sound files from dialin_ivr_install/Sound_files/.
        You will be prompted to enter a desired IVR extension number during this phase.
        Configure Apache web server to serve the Flask application via WSGI.
        Set up necessary file permissions and finalize the installation.

Post-Installation

    Access the Application: Once the script completes, the INFOCALL application will be accessible via your server's IP address or hostname in a web browser on Port 5000.
    Example: http://[Your_Server_IP_Address_or_Hostname]:5000
    Create User Account: The application typically requires you to create an administrator account upon the first access.
    Firewall Configuration: Double-check your server's firewall (e.g., Travelin' Man 3 for Incredible PBX) to ensure that HTTP/HTTPS traffic to the web server is allowed from your client IP address.
    Logging: Application logs will be available at /var/log/infocall/ for troubleshooting. Asterisk logs are typically in /var/log/asterisk/.
    IVR Setup: If you intend to use the Dial-in IVR, ensure that your main Asterisk inbound context (e.g., from-internal) includes from-internal-custom to route calls to the IVR extension you configured.
     cd  /var/www/html/infocall/dialin_ivr_install   SEE "read.me" file in the folder!! 
...
Considerations for Port 5000 Access ***(This has been added to the script left for reference)***

If you have a specific requirement to access the application on http://[Your_Server_IP_Address_or_Hostname]:5000/, this would involve deviating from the default Apache configuration set up by the provided scripts.

Option 1: Modify Apache to Listen on Port 5000 (*** I believe I have this fixed in latest script and the app works under port 5000 leaving port 80 for the admin login to Incredible PBX)
          This should be working post-installation http://[Your_Server_IP_Address_or_Hostname]:5000
          

      -------------------- Below remains for reference ----------------------------------------
          
    N/A This involves changing the 04-apache-setup.sh script before running install-infocall.sh, and potentially modifying Apache's global ports.conf.

    N/A Edit 04-apache-setup.sh: Change VirtualHost *:80 to VirtualHost *:5000 within the Apache configuration template.
    N/A Edit /etc/apache2/ports.conf: Ensure Apache is configured to Listen 5000. You might need to add Listen 5000 if it's not present.
    N/A Firewall: You will also need to open port 5000 in your server's firewall (e.g., Travelin' Man 3) for external access.

N/A Option 2: Run Flask Directly (Advanced) (*** This should not be necessary!) 
N/A This involves bypassing the Apache/WSGI setup entirely and running the Flask application with a production-ready WSGI server (like Gunicorn or uWSGI)
N/A configured to listen on port 5000, and managing it with a process manager like Systemd. This approach is outside the scope of the provided installation
N/A scripts and requires significant manual configuration.
