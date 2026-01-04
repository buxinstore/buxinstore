"""
Payment Details Configuration
Stores payment details for manual payment methods (Bank Transfer, Western Union, Ria, Wave, MoneyGram)
"""

PAYMENT_DETAILS = {
    'bank_transfer': {
        'name': 'Bank Transfer',
        'display_name': 'Bank Transfer',
        'icon': '🏦',
        'details': {
            'account_holder_name': 'ABDOUKADIR JABBI',
            'bank_name': 'State Bank of India (SBI)',
            'branch_name': 'Surajpur Greater Noida',
            'account_number': '60541424234',
            'ifsc_code': 'SBIN0014022',
            'note': 'VERY IMPORTANT: Include IFSC Code when making transfer'
        },
        'instructions': 'After payment, upload your bank transfer receipt/screenshot here.'
    },
    'western_union': {
        'name': 'Western Union',
        'display_name': 'Western Union',
        'icon': '🌍',
        'details': {
            'receiver_name': 'Abdoukadir Jabbi',
            'country': 'India',
            'phone': '+91 93190 38312'
        },
        'instructions': 'Send payment via Western Union and upload your receipt here.'
    },
    'moneygram': {
        'name': 'MoneyGram',
        'display_name': 'MoneyGram',
        'icon': '💸',
        'details': {
            'receiver_name': 'Abdoukadir Jabbi',
            'country': 'India',
            'phone': '+91 93190 38312'
        },
        'instructions': 'Send payment via MoneyGram and upload your receipt here.'
    },
    'ria': {
        'name': 'Ria Money Transfer',
        'display_name': 'Ria Money Transfer',
        'icon': '💳',
        'details': {
            'receiver_name': 'Abdoukadir Jabbi',
            'country': 'India',
            'phone': '+91 93190 38312'
        },
        'instructions': 'Send payment via Ria Money Transfer and upload your receipt here.'
    },
    'wave': {
        'name': 'Wave',
        'display_name': 'Wave',
        'icon': '📱',
        'details': {
            'receiver_name': 'Foday M J',
            'wave_number': '5427090',
            'note': 'Send payment to this Wave number and upload the screenshot.'
        },
        'instructions': 'Send payment to the Wave number above and upload your payment screenshot here.'
    }
}


def get_payment_details(payment_method: str) -> dict:
    """
    Get payment details for a specific payment method.
    
    Args:
        payment_method: Payment method key (bank_transfer, western_union, etc.)
    
    Returns:
        Dictionary with payment details, or None if not found
    """
    return PAYMENT_DETAILS.get(payment_method.lower())


def get_all_manual_payment_methods() -> list:
    """
    Get all available manual payment methods.
    
    Returns:
        List of payment method dictionaries
    """
    return [
        {
            'key': key,
            'name': details['name'],
            'display_name': details['display_name'],
            'icon': details.get('icon', '💳')
        }
        for key, details in PAYMENT_DETAILS.items()
    ]


def is_manual_payment_method(payment_method: str) -> bool:
    """
    Check if a payment method is a manual payment method (requires receipt upload).
    
    Args:
        payment_method: Payment method key
    
    Returns:
        True if manual payment method, False otherwise
    """
    return payment_method.lower() in PAYMENT_DETAILS.keys()

