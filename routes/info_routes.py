from flask import Blueprint, render_template, current_app, send_from_directory
import os

info_bp = Blueprint('info', __name__)

@info_bp.route('/about')
def about():
    return render_template('about.html')

@info_bp.route('/license')
def license_page():
    try:
        # Assuming AGPL_notice.txt is in the root of the infocall directory
        # and we want to display its content directly in the template
        agpl_path = os.path.join(current_app.root_path, 'AGPL_notice.txt')
        with open(agpl_path, 'r') as f:
            license_content = f.read()
        return render_template('license.html', license_content=license_content)
    except FileNotFoundError:
        return "AGPL_notice.txt not found.", 404
    except Exception as e:
        current_app.logger.error(f"Error reading AGPL_notice.txt: {e}")
        return "Error loading license information.", 500
