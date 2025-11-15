"""
Payment System Utilities
Helper functions for payment processing.
"""

import hashlib
import hmac
import json
from datetime import datetime
from typing import Dict, Any, Optional
from .config import PaymentConfig


def generate_payment_reference(order_id: int, method: str) -> str:
    """
    Generate a unique payment reference for an order.
    
    Args:
        order_id: The order ID
        method: Payment method (wave, qmoney, etc.)
        
    Returns:
        Unique payment reference string
    """
    timestamp = int(datetime.utcnow().timestamp())
    reference = f"{method.upper()}{order_id}{timestamp}"
    return reference


def generate_transaction_id() -> str:
    """
    Generate a unique transaction ID.
    
    Returns:
        Unique transaction ID string
    """
    timestamp = int(datetime.utcnow().timestamp() * 1000)  # milliseconds
    random_part = hashlib.md5(str(timestamp).encode()).hexdigest()[:8]
    return f"TXN{timestamp}{random_part}".upper()


def calculate_payment_fee(amount: float, method: str) -> float:
    """
    Calculate payment processing fee for a given amount and method.
    
    Args:
        amount: Payment amount
        method: Payment method
        
    Returns:
        Calculated fee amount
    """
    # This is a placeholder - implement actual fee calculation logic
    # based on your payment gateway fee structure
    fee_percentage = 0.0  # 0% by default
    fee_fixed = 0.0  # No fixed fee by default
    
    # Example: Different fees for different methods
    if method == 'wave':
        fee_percentage = 0.015  # 1.5%
    elif method == 'qmoney':
        fee_percentage = 0.02  # 2%
    elif method == 'afrimoney':
        fee_percentage = 0.015  # 1.5%
    elif method == 'ecobank':
        fee_percentage = 0.025  # 2.5%
    
    fee = (amount * fee_percentage) + fee_fixed
    return round(fee, 2)


def validate_payment_amount(amount: float, method: str) -> bool:
    """
    Validate if payment amount is within acceptable limits for the method.
    
    Args:
        amount: Payment amount
        method: Payment method
        
    Returns:
        True if amount is valid, False otherwise
    """
    if amount <= 0:
        return False
    
    # Define minimum and maximum amounts per method
    limits = {
        'wave': {'min': 1.0, 'max': 100000.0},
        'qmoney': {'min': 1.0, 'max': 100000.0},
        'afrimoney': {'min': 1.0, 'max': 100000.0},
        'ecobank': {'min': 1.0, 'max': 100000.0},
    }
    
    method_limits = limits.get(method.lower(), {'min': 1.0, 'max': 100000.0})
    return method_limits['min'] <= amount <= method_limits['max']


def format_payment_response(success: bool, message: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Format a standardized payment API response.
    
    Args:
        success: Whether the operation was successful
        message: Response message
        data: Additional response data
        
    Returns:
        Formatted response dictionary
    """
    response = {
        'success': success,
        'message': message,
        'timestamp': datetime.utcnow().isoformat()
    }
    
    if data:
        response.update(data)
    
    return response


def verify_webhook_signature(payload: str, signature: str, secret: str) -> bool:
    """
    Verify webhook signature from payment gateway.
    
    Args:
        payload: Webhook payload (raw string)
        signature: Signature from webhook headers
        secret: Secret key for verification
        
    Returns:
        True if signature is valid, False otherwise
    """
    try:
        expected_signature = hmac.new(
            secret.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(expected_signature, signature)
    except Exception:
        return False


def mask_payment_reference(reference: str) -> str:
    """
    Mask payment reference for display (show only first and last few characters).
    
    Args:
        reference: Payment reference
        
    Returns:
        Masked reference string
    """
    if len(reference) <= 8:
        return reference
    
    return f"{reference[:4]}****{reference[-4:]}"


def parse_payment_response(response_data: Any) -> Dict[str, Any]:
    """
    Parse payment gateway response into a standardized format.
    
    Args:
        response_data: Raw response from payment gateway
        
    Returns:
        Parsed response dictionary
    """
    if isinstance(response_data, str):
        try:
            response_data = json.loads(response_data)
        except json.JSONDecodeError:
            return {'raw': response_data}
    
    if isinstance(response_data, dict):
        return response_data
    
    return {'raw': str(response_data)}


def get_payment_method_display_name(method: str) -> str:
    """
    Get display name for a payment method.
    
    Args:
        method: Payment method code
        
    Returns:
        Display name for the payment method
    """
    from .config import PAYMENT_METHOD_DISPLAY_NAMES
    return PAYMENT_METHOD_DISPLAY_NAMES.get(method.lower(), method.upper())

