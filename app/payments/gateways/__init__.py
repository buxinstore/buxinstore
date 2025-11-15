"""
Payment Gateway Integrations
All payment gateway implementations are in this package.
"""

from .base import BasePaymentGateway
from .wave import WaveGateway
from .qmoney import QMoneyGateway
from .afrimoney import AfriMoneyGateway
from .ecobank import EcoBankGateway
from .modempay import ModemPayGateway

__all__ = [
    'BasePaymentGateway',
    'WaveGateway',
    'QMoneyGateway',
    'AfriMoneyGateway',
    'EcoBankGateway',
    'ModemPayGateway',
]


def get_gateway(method: str) -> BasePaymentGateway:
    """
    Get the appropriate payment gateway instance for a given method.
    
    Args:
        method: Payment method name (wave, qmoney, etc.)
        
    Returns:
        Payment gateway instance
    """
    gateways = {
        'wave': WaveGateway,
        'qmoney': QMoneyGateway,
        'afrimoney': AfriMoneyGateway,
        'ecobank': EcoBankGateway,
        'modempay': ModemPayGateway,
    }
    
    gateway_class = gateways.get(method.lower())
    if not gateway_class:
        raise ValueError(f"Unsupported payment method: {method}")
    
    return gateway_class()

