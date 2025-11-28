"""
WhatsApp Token Management Utility
Handles dynamic token retrieval, validation, and .env file updates.
"""

import os
import re
import requests
from typing import Optional, Tuple, Dict, Any
from flask import current_app
from dotenv import load_dotenv, set_key, find_dotenv


def get_whatsapp_token() -> Tuple[Optional[str], Optional[str]]:
    """
    Get WhatsApp access token and phone number ID dynamically.
    Priority: Database settings > Environment variables
    
    Returns:
        Tuple of (access_token, phone_number_id)
    """
    try:
        from app import AppSettings, db
        settings = AppSettings.query.first()
        
        # Try database first
        if settings:
            access_token = settings.whatsapp_access_token
            phone_number_id = settings.whatsapp_phone_number_id
            
            if access_token and phone_number_id:
                return access_token.strip(), phone_number_id.strip()
    except Exception as e:
        current_app.logger.warning(f"Could not load WhatsApp token from database: {e}")
    
    # Fallback to environment variables
    load_dotenv(override=True)
    access_token = os.getenv('WHATSAPP_ACCESS_TOKEN', '').strip()
    phone_number_id = os.getenv('WHATSAPP_PHONE_NUMBER_ID', '').strip()
    
    return access_token if access_token else None, phone_number_id if phone_number_id else None


def validate_whatsapp_token(access_token: str, phone_number_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Validate WhatsApp access token by making a lightweight API call to get phone number info.
    
    Args:
        access_token: WhatsApp access token
        phone_number_id: WhatsApp phone number ID
    
    Returns:
        Tuple of (is_valid: bool, error_info: Optional[Dict])
        error_info contains: status_code, error_code, error_subcode, message, error_type
    """
    if not access_token or not phone_number_id:
        return False, {
            'status_code': None,
            'error_code': None,
            'error_subcode': None,
            'message': 'Access token or phone number ID is missing',
            'error_type': 'ConfigurationError'
        }
    
    # Use a lightweight API call to validate - get phone number info
    # This endpoint requires minimal permissions and validates the token
    url = f"https://graph.facebook.com/v22.0/{phone_number_id}"
    headers = {
        "Authorization": f"Bearer {access_token}"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response_data = response.json() if response.headers.get('content-type', '').startswith('application/json') else {}
        
        if response.status_code == 200:
            # Token is valid if we can access the phone number info
            return True, None
        else:
            # Parse error response
            error_info = response_data.get('error', {}) if isinstance(response_data, dict) else {}
            return False, {
                'status_code': response.status_code,
                'error_code': error_info.get('code'),
                'error_subcode': error_info.get('error_subcode'),
                'message': error_info.get('message', f'HTTP {response.status_code} error'),
                'error_type': error_info.get('type', 'UnknownError')
            }
    except requests.exceptions.RequestException as e:
        return False, {
            'status_code': None,
            'error_code': None,
            'error_subcode': None,
            'message': f'Network error: {str(e)}',
            'error_type': 'NetworkError'
        }
    except Exception as e:
        return False, {
            'status_code': None,
            'error_code': None,
            'error_subcode': None,
            'message': f'Unexpected error: {str(e)}',
            'error_type': 'UnexpectedError'
        }


def check_token_expiration_from_error(error_response: Dict[str, Any]) -> bool:
    """
    Check if an error response indicates token expiration.
    
    Args:
        error_response: Error response from Meta API
    
    Returns:
        True if token is expired, False otherwise
    """
    if not error_response:
        return False
    
    # Check for error code 190 (OAuthException) which indicates expired token
    error_code = error_response.get('error_code')
    error_subcode = error_response.get('error_subcode')
    message = error_response.get('message', '').lower()
    
    # Error code 190 = OAuthException (expired/invalid token)
    # Error subcode 463 = Session expired
    if error_code == 190 or error_subcode == 463 or 'expired' in message or 'session has expired' in message:
        return True
    
    return False


def update_env_file(key: str, value: str) -> bool:
    """
    Update a key-value pair in the .env file.
    
    Args:
        key: Environment variable key
        value: Environment variable value
    
    Returns:
        True if successful, False otherwise
    """
    try:
        env_path = find_dotenv()
        if not env_path:
            # Try to find .env in common locations
            env_path = os.path.join(os.getcwd(), '.env')
            if not os.path.exists(env_path):
                current_app.logger.warning(f".env file not found at {env_path}")
                return False
        
        # Update the .env file
        set_key(env_path, key, value)
        
        # Reload environment variables
        load_dotenv(override=True)
        
        # Update os.environ immediately
        os.environ[key] = value
        
        current_app.logger.info(f"✅ Updated {key} in .env file and reloaded environment")
        return True
    except Exception as e:
        current_app.logger.error(f"❌ Failed to update .env file: {str(e)}")
        return False


def save_whatsapp_token_to_env(access_token: str, phone_number_id: str) -> bool:
    """
    Save WhatsApp credentials to .env file and reload environment.
    
    Args:
        access_token: WhatsApp access token
        phone_number_id: WhatsApp phone number ID
    
    Returns:
        True if successful, False otherwise
    """
    success = True
    if access_token:
        success = update_env_file('WHATSAPP_ACCESS_TOKEN', access_token) and success
    if phone_number_id:
        success = update_env_file('WHATSAPP_PHONE_NUMBER_ID', phone_number_id) and success
    
    return success


def get_token_status() -> Dict[str, Any]:
    """
    Get current WhatsApp token status including validation.
    
    Returns:
        Dict with status information:
        - configured: bool
        - token_exists: bool
        - phone_id_exists: bool
        - is_valid: bool
        - is_expired: bool
        - error_info: Optional[Dict]
    """
    access_token, phone_number_id = get_whatsapp_token()
    
    status = {
        'configured': bool(access_token and phone_number_id),
        'token_exists': bool(access_token),
        'phone_id_exists': bool(phone_number_id),
        'is_valid': False,
        'is_expired': False,
        'error_info': None
    }
    
    if not status['configured']:
        return status
    
    # Validate the token
    is_valid, error_info = validate_whatsapp_token(access_token, phone_number_id)
    status['is_valid'] = is_valid
    
    if error_info:
        status['error_info'] = error_info
        status['is_expired'] = check_token_expiration_from_error(error_info)
    
    return status

