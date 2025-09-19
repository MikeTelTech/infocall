# services/twilio_service.py
import logging
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, TWILIO_MESSAGING_SERVICE_SID

def send_twilio_sms(to_phone_number, message_text, status_callback_url=None):
    """
    Sends an SMS message via Twilio.

    Args:
        to_phone_number (str): The recipient's phone number. Will be formatted to E.164.
        message_text (str): The text content of the SMS.
        status_callback_url (str, optional): URL for Twilio to send status updates.

    Returns:
        str: The Twilio Message SID if successful.

    Raises:
        TwilioRestException: If Twilio API call fails.
        Exception: For other unexpected errors.
    """
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    # Format phone number to E.164
    formatted_phone = to_phone_number
    if not formatted_phone.startswith('+'):
        formatted_phone = '+1' + formatted_phone # Assuming +1 for US numbers

    try:
        message_params = {
            'body': message_text,
            'to': formatted_phone,
            'status_callback': status_callback_url
        }

        # Use Messaging Service SID if configured, otherwise use individual Twilio Phone Number
        if TWILIO_MESSAGING_SERVICE_SID:
            message_params['messaging_service_sid'] = TWILIO_MESSAGING_SERVICE_SID
        else:
            message_params['from_'] = TWILIO_PHONE_NUMBER

        message = client.messages.create(**message_params)
        logging.info(f"Twilio SMS sent to {formatted_phone} (SID: {message.sid})")
        return message.sid
    except TwilioRestException as e:
        logging.error(f"Twilio API error sending SMS to {to_phone_number}: {e}", exc_info=True)
        raise e
    except Exception as e:
        logging.error(f"Unexpected error sending SMS to {to_phone_number}: {e}", exc_info=True)
        raise e