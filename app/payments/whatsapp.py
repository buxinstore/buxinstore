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
    
    # Get credentials from environment variables
    access_token = os.environ.get('WHATSAPP_ACCESS_TOKEN')
    phone_number_id = os.environ.get('WHATSAPP_PHONE_NUMBER_ID')
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
        # Check for token expiration (401 error with specific error code)
        if e.response.status_code == 401:
            try:
                error_data = e.response.json()
                if error_data.get('error', {}).get('code') == 190:
                    error_msg = "WhatsApp access token has expired. Please refresh your token in Meta Developer Console and update WHATSAPP_ACCESS_TOKEN in your .env file. See WHATSAPP_TOKEN_REFRESH.md for instructions."
                    current_app.logger.error(error_msg)
                    return False, error_msg
            except (ValueError, KeyError):
                pass
        
        error_msg = f"HTTP error sending WhatsApp message: {e.response.status_code} - {e.response.text}"
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

