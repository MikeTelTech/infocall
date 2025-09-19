# routes/__init__.py
from flask import Blueprint

# Create blueprints
auth_bp = Blueprint('auth', __name__)
member_bp = Blueprint('member', __name__, url_prefix='/') # Keep root for now if member_dir is root-level
call_bp = Blueprint('call', __name__)
sms_bp = Blueprint('sms', __name__)

# Import routes to register them with the blueprints
from . import auth_routes
from . import member_routes
from . import call_routes
from . import sms_routes