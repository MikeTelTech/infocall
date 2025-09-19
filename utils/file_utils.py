# utils/file_utils.py
import os
import logging
from pydub import AudioSegment # Moved from app.py

def allowed_file(filename):
    """
    Checks if a filename has an allowed audio extension.
    """
    ALLOWED_EXTENSIONS = {'wav', 'mp3', 'm4a', 'aac'}
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def is_audio_file(filepath):
    """
    Attempts to load an audio file to validate its format.
    """
    if not os.path.exists(filepath):
        logging.error(f"Audio file not found at path: {filepath}")
        return False
    try:
        audio = AudioSegment.from_file(filepath)
        logging.debug(f"Validated audio file: {filepath}")
        return True
    except Exception as e:
        logging.error(f"Error validating audio file {filepath}: {e}", exc_info=True)
        return False