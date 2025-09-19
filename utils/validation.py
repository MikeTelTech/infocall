## utils/validation.py
import logging

def validate_phone_number(phone_number):
    """
    Validates a phone number, checking for restricted numbers and length.
    Returns (True, None) for valid, (False, error_message) for invalid.
    """
    restricted_numbers = ['911', '411', '511']
    clean_number = ''.join(filter(str.isdigit, phone_number or ""))

    if not clean_number:
        return False, "Phone number cannot be empty."
    for restricted in restricted_numbers:
        if clean_number == restricted or clean_number.startswith(restricted):
            return False, f"Cannot use restricted number '{restricted}'."
    if not (len(clean_number) == 4 or len(clean_number) == 10):
        return False, "Phone number must be 4 or 10 digits."
    return True, None

def validate_caller_id_name(caller_id_name):
    """
    Validates the caller ID name.
    Allows letters, numbers, spaces, and periods. Limits length to 15 characters.
    """
    if caller_id_name is None:
        return "" # Default to empty string or suitable default if None
    
    # Remove any characters that are not letters, numbers, spaces, or periods
    cleaned_name = ''.join(c for c in caller_id_name if c.isalnum() or c.isspace() or c == '.')
    
    # Trim to max 15 characters
    return cleaned_name[:15]
