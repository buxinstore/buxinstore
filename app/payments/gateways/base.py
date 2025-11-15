"""
Base Payment Gateway
Abstract base class for all payment gateway implementations.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from app.payments.config import PaymentConfig
from app.payments.exceptions import PaymentGatewayException


class BasePaymentGateway(ABC):
    """
    Abstract base class for payment gateway implementations.
    All payment gateways must inherit from this class and implement its methods.
    """
    
    def __init__(self):
        """Initialize the payment gateway with configuration."""
        self.config = PaymentConfig.get_gateway_config(self.get_method_name())
        self._validate_config()
    
    @abstractmethod
    def get_method_name(self) -> str:
        """
        Return the payment method name (e.g., 'wave', 'qmoney').
        
        Returns:
            Payment method name
        """
        pass
    
    @abstractmethod
    def initiate_payment(self, amount: float, reference: str, order_id: int, 
                        customer_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Initiate a payment transaction.
        
        Args:
            amount: Payment amount
            reference: Payment reference
            order_id: Order ID
            customer_info: Customer information (phone, email, etc.)
            
        Returns:
            Dictionary containing payment initiation response
            
        Raises:
            PaymentGatewayException: If payment initiation fails
        """
        pass
    
    @abstractmethod
    def verify_payment(self, reference: str, transaction_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Verify a payment transaction.
        
        Args:
            reference: Payment reference
            transaction_id: Optional transaction ID
            
        Returns:
            Dictionary containing payment verification response
            
        Raises:
            PaymentGatewayException: If payment verification fails
        """
        pass
    
    @abstractmethod
    def process_webhook(self, payload: Dict[str, Any], signature: Optional[str] = None) -> Dict[str, Any]:
        """
        Process webhook notification from payment gateway.
        
        Args:
            payload: Webhook payload
            signature: Webhook signature for verification
            
        Returns:
            Dictionary containing processed webhook data
            
        Raises:
            PaymentGatewayException: If webhook processing fails
        """
        pass
    
    def refund_payment(self, reference: str, amount: Optional[float] = None) -> Dict[str, Any]:
        """
        Process a payment refund.
        
        Args:
            reference: Original payment reference
            amount: Refund amount (if None, full refund)
            
        Returns:
            Dictionary containing refund response
            
        Raises:
            PaymentGatewayException: If refund fails
        """
        raise NotImplementedError("Refund not supported for this payment method")
    
    def _validate_config(self):
        """Validate that gateway configuration is complete."""
        method_name = self.get_method_name()
        
        # ModemPay uses public_key, others use api_key
        if method_name == 'modempay':
            if not self.config.get('public_key'):
                raise PaymentGatewayException(
                    f"Payment gateway {method_name} is not properly configured. "
                    "Missing public_key."
                )
        else:
            if not self.config.get('api_key'):
                raise PaymentGatewayException(
                    f"Payment gateway {method_name} is not properly configured. "
                    "Missing API key."
                )
    
    def _make_request(self, endpoint: str, method: str = 'POST', 
                     data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Make HTTP request to payment gateway API.
        
        Args:
            endpoint: API endpoint
            method: HTTP method (GET, POST, etc.)
            data: Request data
            
        Returns:
            API response as dictionary
            
        Raises:
            PaymentGatewayException: If request fails
        """
        # This is a base implementation - subclasses should override
        # with their specific API request logic
        import requests
        
        url = f"{self.config['api_url']}/{endpoint}"
        headers = {
            'Authorization': f"Bearer {self.config['api_key']}",
            'Content-Type': 'application/json'
        }
        
        try:
            if method.upper() == 'POST':
                response = requests.post(url, json=data, headers=headers, timeout=30)
            else:
                response = requests.get(url, params=data, headers=headers, timeout=30)
            
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise PaymentGatewayException(
                f"Payment gateway request failed: {str(e)}",
                gateway_response={'error': str(e)}
            )

