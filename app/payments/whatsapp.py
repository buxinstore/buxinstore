"""
WhatsApp Integration Module
Handles sending WhatsApp messages via Meta WhatsApp Cloud API.
"""

import os
import requests
from flask import current_app
from typing import Optional, Tuple


def is_live_mode() -> bool:
    """
    Check if the app is running in live mode (production).
    
    Returns:
        True if in live mode, False otherwise
    """
    modempay_public_key = os.environ.get('MODEMPAY_PUBLIC_KEY', '')
    # Check if using live ModemPay keys (pk_live_ prefix)
    return modempay_public_key.startswith('pk_live_')


def send_whatsapp_message(
    to: str,
    customer_name: str,
    amount: float,
    reference: str,
    business_name: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """
    Send a WhatsApp message via Meta WhatsApp Cloud API.
    
    Args:
        to: Customer phone number (with country code, e.g., +2201234567)
        customer_name: Customer's name
        amount: Payment amount
        reference: Payment reference/transaction ID
        business_name: Business name (defaults to BUSINESS_NAME env var)
    
    Returns:
        Tuple of (success: bool, error_message: Optional[str])
    """
    # Only send in live mode
    if not is_live_mode():
        current_app.logger.info("WhatsApp message skipped: Not in live mode (test payment)")
        return False, "Not in live mode"
    
    # Get credentials dynamically (DB first, then .env)
    from app.utils.whatsapp_token import get_whatsapp_token
    access_token, phone_number_id = get_whatsapp_token()
    business_name = business_name or os.environ.get('BUSINESS_NAME', 'Our Store')
    
    # Validate required credentials
    if not access_token:
        error_msg = "WHATSAPP_ACCESS_TOKEN not configured"
        current_app.logger.error(error_msg)
        return False, error_msg
    
    if not phone_number_id:
        error_msg = "WHATSAPP_PHONE_NUMBER_ID not configured"
        current_app.logger.error(error_msg)
        return False, error_msg
    
    # Validate phone number format (should start with +)
    if not to.startswith('+'):
        # Try to add + if missing
        if to.startswith('220'):  # Gambia country code
            to = '+' + to
        else:
            error_msg = f"Invalid phone number format: {to}. Must start with +"
            current_app.logger.error(error_msg)
            return False, error_msg
    
    # Construct API URL
    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    
    # Prepare headers
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    # Prepare payload
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {
            "body": f"Hello {customer_name}, thank you for your payment of GMD {amount:.2f} to {business_name}. Your order reference is {reference}."
        }
    }
    
    try:
        # Send request to WhatsApp API
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        
        # Log success
        current_app.logger.info(f"✅ WhatsApp message sent successfully to {to} for payment {reference}")
        return True, None
        
    except requests.exceptions.HTTPError as e:
        # Check for token expiration with detailed error info
        from app.utils.whatsapp_token import check_token_expiration_from_error
        
        error_data = {}
        try:
            error_data = e.response.json()
        except (ValueError, KeyError):
            pass
        
        error_info = error_data.get('error', {}) if isinstance(error_data, dict) else {}
        error_response_dict = {
            'status_code': e.response.status_code,
            'error_code': error_info.get('code'),
            'error_subcode': error_info.get('error_subcode'),
            'message': error_info.get('message', e.response.text[:200]),
            'error_type': error_info.get('type', 'UnknownError')
        }
        
        if check_token_expiration_from_error(error_response_dict):
            error_msg = (
                "WhatsApp access token has expired. Please generate a new token from "
                "Meta Developer Console (https://developers.facebook.com/apps → WhatsApp → API Setup) "
                "and update it in Settings."
            )
            current_app.logger.error(f"❌ {error_msg} Error: {error_info.get('message', 'Token expired')}")
            return False, error_msg
        
        error_msg = f"HTTP error sending WhatsApp message: {e.response.status_code} - {error_info.get('message', e.response.text[:200])}"
        current_app.logger.error(error_msg)
        return False, error_msg
        
    except requests.exceptions.RequestException as e:
        error_msg = f"Failed to send WhatsApp message: {str(e)}"
        current_app.logger.error(error_msg)
        return False, error_msg
        
    except Exception as e:
        error_msg = f"Unexpected error sending WhatsApp message: {str(e)}"
        current_app.logger.error(error_msg)
        return False, error_msg

