# utils/security.py
from flask import session, flash, redirect, url_for
from functools import wraps
import logging

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('You need to be logged in to access this page.')
            return redirect(url_for('auth.index'))
        return f(*args, **kwargs)
    return decorated_function