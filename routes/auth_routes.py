# routes/auth_routes.py
from flask import render_template, request, redirect, url_for, flash, session
import logging
from . import auth_bp
from models.user import User
from models.app_setting import AppSetting  # Import AppSetting model
from utils.security import login_required


@auth_bp.route("/", methods=["GET", "POST"])
def index():
    logging.debug("Entering index route")
    if 'user_id' in session:
        return redirect(url_for("auth.main_menu"))  # Redirect to blueprint-aware endpoint

    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        logging.debug(f"Login attempt: Email: {email}")
        try:
            user = User.get_by_email(email)

            if user:
                if user.check_password(password):
                    session["user_id"] = user.id
                    session["user_email"] = user.email
                    session["role"] = user.role  # Store user role in session
                    logging.info(f"User {user.email} logged in successfully")
                    return redirect(url_for("auth.main_menu"))  # Redirect to blueprint-aware endpoint
                else:
                    flash("Incorrect password")
                    logging.warning(f"Incorrect password attempt for user {email}")
            else:
                flash("Email not found")
                logging.warning(f"Email {email} not found")

        except Exception as e:
            logging.error(f"Unexpected error during login for {email}: {e}", exc_info=True)
            flash("An unexpected error occurred. Please try again later.")

    return render_template("login.html")


@auth_bp.route("/main_menu")
@login_required
def main_menu():
    logging.debug(f"User {session.get('user_email')} accessed main menu")
    return render_template("main_menu.html")


@auth_bp.route("/logout")
def logout():
    logging.info(f"User {session.get('user_email')} logged out")
    session.pop("user_id", None)
    session.pop("user_email", None)
    session.pop("role", None)  # Clear role on logout
    return redirect(url_for("auth.index"))


@auth_bp.route("/create_account", methods=["GET", "POST"])
def create_account():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        confirm_password = request.form["confirm_password"]
        ivr_passcode = request.form.get("ivr_passcode", "")
        confirm_ivr_passcode = request.form.get("confirm_ivr_passcode", "")
        phone_number = request.form["phone_number"]

        # --- Validation ---
        error = False
        if password != confirm_password:
            flash("Login passwords do not match.", "error")
            error = True

        if ivr_passcode != confirm_ivr_passcode:
            flash("IVR Passcodes do not match.", "error")
            error = True

        if not ivr_passcode or len(ivr_passcode) != 6 or not ivr_passcode.isdigit():
            flash("IVR Passcode must be exactly 6 digits.", "error")
            error = True

        if error:
            return render_template("create_account.html")
        # --- End Validation ---

        try:
            if User.get_by_email(email):
                flash("Email already exists. Please login.", "warning")
                return redirect(url_for("auth.index"))
            else:
                User.create(email, password, phone_number, ivr_passcode, 'admin')  # Set role to admin
                flash("Account created successfully. Please login.", "success")
                logging.info(f"Admin account created for email: {email}")
                return redirect(url_for("auth.index"))

        except Exception as e:
            logging.error(f"Unexpected error during account creation for {email}: {e}", exc_info=True)
            flash("An unexpected error occurred. Please try again later.", "error")

    return render_template("create_account.html")


@auth_bp.route("/admin_settings", methods=["GET", "POST"])
@login_required
def admin_settings():
    # Only allow admin role to access this page
    if session.get('role') != 'admin':
        flash("Access denied. Admin privileges required.", "error")
        return redirect(url_for('auth.main_menu'))

    if request.method == "POST":
        # Checkbox values from HTML can be tricky. If unchecked, it might not be in request.form.
        enable_auto_schedule = 'enable_auto_schedule' in request.form

        try:
            # Save the setting to the database
            AppSetting.set('ivr_auto_schedule_enabled', '1' if enable_auto_schedule else '0')
            flash("Settings saved successfully.", "success")
        except Exception as e:
            logging.error(f"Error saving admin settings: {e}", exc_info=True)
            flash("An error occurred while saving settings.", "error")

        return redirect(url_for('auth.admin_settings'))

    # GET request: Load current settings
    ivr_auto_schedule_enabled = False
    try:
        setting_value = AppSetting.get('ivr_auto_schedule_enabled')
        if setting_value == '1':
            ivr_auto_schedule_enabled = True
    except Exception as e:
        logging.error(f"Error loading admin settings: {e}", exc_info=True)
        flash("An error occurred while loading settings.", "error")

    return render_template("admin_settings.html", ivr_auto_schedule_enabled=ivr_auto_schedule_enabled)