"""
Payment System Configuration
All payment-related configuration settings.
"""

import os
from typing import Dict, Any


class PaymentConfig:
    """
    Payment system configuration.
    All payment gateway credentials and settings should be stored here.
    """
    
    # Payment gateway API endpoints
    WAVE_API_URL = os.environ.get('WAVE_API_URL', 'https://api.wave.com/v1')
    QMONEY_API_URL = os.environ.get('QMONEY_API_URL', 'https://api.qmoney.com/v1')
    AFRIMONEY_API_URL = os.environ.get('AFRIMONEY_API_URL', 'https://api.afrimoney.com/v1')
    ECOBANK_API_URL = os.environ.get('ECOBANK_API_URL', 'https://api.ecobank.com/v1')
    
    # API Keys (should be stored in environment variables)
    WAVE_API_KEY = os.environ.get('WAVE_API_KEY', '')
    WAVE_SECRET_KEY = os.environ.get('WAVE_SECRET_KEY', '')
    WAVE_MERCHANT_ID = os.environ.get('WAVE_MERCHANT_ID', '')
    
    QMONEY_API_KEY = os.environ.get('QMONEY_API_KEY', '')
    QMONEY_SECRET_KEY = os.environ.get('QMONEY_SECRET_KEY', '')
    QMONEY_MERCHANT_ID = os.environ.get('QMONEY_MERCHANT_ID', '')
    
    AFRIMONEY_API_KEY = os.environ.get('AFRIMONEY_API_KEY', '')
    AFRIMONEY_SECRET_KEY = os.environ.get('AFRIMONEY_SECRET_KEY', '')
    AFRIMONEY_MERCHANT_ID = os.environ.get('AFRIMONEY_MERCHANT_ID', '')
    
    ECOBANK_API_KEY = os.environ.get('ECOBANK_API_KEY', '')
    ECOBANK_SECRET_KEY = os.environ.get('ECOBANK_SECRET_KEY', '')
    ECOBANK_MERCHANT_ID = os.environ.get('ECOBANK_MERCHANT_ID', '')
    
    # ModemPay Configuration (Unified Gambian Payment Gateway)
    MODEMPAY_API_URL = os.environ.get('MODEMPAY_API_URL', 'https://api.modempay.com/v1/checkout')
    # Updated to use SDK env var names
    MODEMPAY_PUBLIC_KEY = os.environ.get('MODEMPAY_PUBLIC_KEY') or os.environ.get('MODEM_PAY_PUBLIC_KEY')
    MODEMPAY_SECRET_KEY = os.environ.get('MODEMPAY_SECRET_KEY') or os.environ.get('MODEM_PAY_API_KEY')
    MODEMPAY_WEBHOOK_SECRET = os.environ.get('MODEMPAY_WEBHOOK_SECRET', '')
    MODEMPAY_CALLBACK_URL = os.environ.get('MODEMPAY_CALLBACK_URL', '')
    
    # Ngrok Configuration (for local development with public URLs)
    # Set NGROK_URL to your Ngrok forwarding URL for live payment callbacks
    # Example: NGROK_URL=https://carissa-prosodemic-gratifyingly-ngrok-free.dev
    NGROK_URL = os.environ.get('NGROK_URL', '')
    
    # Payment settings
    PAYMENT_TIMEOUT = int(os.environ.get('PAYMENT_TIMEOUT', 300))  # 5 minutes
    PAYMENT_RETRY_ATTEMPTS = int(os.environ.get('PAYMENT_RETRY_ATTEMPTS', 3))
    PAYMENT_WEBHOOK_SECRET = os.environ.get('PAYMENT_WEBHOOK_SECRET', '')
    
    # Currency settings
    DEFAULT_CURRENCY = os.environ.get('DEFAULT_CURRENCY', 'GMD')
    
    # Payment callback URLs
    PAYMENT_SUCCESS_URL = os.environ.get('PAYMENT_SUCCESS_URL', '/payments/success')
    PAYMENT_FAILURE_URL = os.environ.get('PAYMENT_FAILURE_URL', '/payments/failure')
    PAYMENT_CALLBACK_URL = os.environ.get('PAYMENT_CALLBACK_URL', '/payments/callback')
    
    @classmethod
    def get_gateway_config(cls, gateway_name: str) -> Dict[str, Any]:
        """
        Get configuration for a specific payment gateway.
        
        Args:
            gateway_name: Name of the payment gateway (wave, qmoney, etc.)
            
        Returns:
            Dictionary containing gateway configuration
        """
        gateway_name = gateway_name.lower()
        
        configs = {
            'wave': {
                'api_url': cls.WAVE_API_URL,
                'api_key': cls.WAVE_API_KEY,
                'secret_key': cls.WAVE_SECRET_KEY,
                'merchant_id': cls.WAVE_MERCHANT_ID,
            },
            'qmoney': {
                'api_url': cls.QMONEY_API_URL,
                'api_key': cls.QMONEY_API_KEY,
                'secret_key': cls.QMONEY_SECRET_KEY,
                'merchant_id': cls.QMONEY_MERCHANT_ID,
            },
            'afrimoney': {
                'api_url': cls.AFRIMONEY_API_URL,
                'api_key': cls.AFRIMONEY_API_KEY,
                'secret_key': cls.AFRIMONEY_SECRET_KEY,
                'merchant_id': cls.AFRIMONEY_MERCHANT_ID,
            },
            'ecobank': {
                'api_url': cls.ECOBANK_API_URL,
                'api_key': cls.ECOBANK_API_KEY,
                'secret_key': cls.ECOBANK_SECRET_KEY,
                'merchant_id': cls.ECOBANK_MERCHANT_ID,
            },
            'modempay': {
                'api_url': cls.MODEMPAY_API_URL,
                'public_key': cls.MODEMPAY_PUBLIC_KEY,
                'secret_key': cls.MODEMPAY_SECRET_KEY,
                'webhook_secret': cls.MODEMPAY_WEBHOOK_SECRET,
            },
        }
        
        return configs.get(gateway_name, {})
    
    @classmethod
    def is_gateway_enabled(cls, gateway_name: str) -> bool:
        """
        Check if a payment gateway is properly configured and enabled.
        
        Args:
            gateway_name: Name of the payment gateway
            
        Returns:
            True if gateway is enabled, False otherwise
        """
        config = cls.get_gateway_config(gateway_name)
        
        # ModemPay uses public_key and secret_key, others use api_key and secret_key
        if gateway_name.lower() == 'modempay':
            return bool(config.get('public_key') and config.get('secret_key'))
        
        return bool(config.get('api_key') and config.get('secret_key'))


# Payment method display names
PAYMENT_METHOD_DISPLAY_NAMES = {
    'wave': 'Wave Money',
    'qmoney': 'QMoney',
    'afrimoney': 'AfriMoney',
    'ecobank': 'ECOBANK Mobile',
    'modempay': 'ModemPay (Unified Gateway)'
}

# Valid payment methods
VALID_PAYMENT_METHODS = list(PAYMENT_METHOD_DISPLAY_NAMES.keys())
