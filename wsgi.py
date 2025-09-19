import sys
import logging
import os
import atexit

# Ensure the project directory is in the Python path
project_home = '/var/www/html/infocall'  # Make sure this matches APP_ROOT
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Configure logging to stderr for WSGI/Apache logs
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
log = logging.getLogger(__name__)
log.info("WSGI script started.")

# Enable debug mode if environment variable is set
if os.environ.get("INFOCALL_DEBUG") == "1":
    log.setLevel(logging.DEBUG)
    log.debug("Debug mode activated via INFOCALL_DEBUG environment variable.")

# Ensure logs flush on exit
atexit.register(lambda: sys.stderr.flush())

# Trap top-level unhandled exceptions
sys.excepthook = lambda *args: log.critical("Unhandled top-level exception", exc_info=args)

try:
    log.info("Attempting to import Flask app from app.py...")
    from app import app as application
    log.info("Flask app imported successfully as 'application'.")
except ImportError as e:
    log.critical(f"Error importing Flask application: {e}", exc_info=True)
    raise
except Exception as e:
    log.critical(f"Unexpected error during WSGI initialization: {e}", exc_info=True)
    raise

