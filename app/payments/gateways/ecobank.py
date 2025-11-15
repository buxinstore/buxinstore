"""
ECOBANK Mobile Payment Gateway Integration
"""

from typing import Dict, Any, Optional
from .base import BasePaymentGateway
from app.payments.exceptions import PaymentGatewayException
from app.payments.config import PaymentConfig


class EcoBankGateway(BasePaymentGateway):
    """ECOBANK Mobile payment gateway implementation."""
    
    def get_method_name(self) -> str:
        return 'ecobank'
    
    def initiate_payment(self, amount: float, reference: str, order_id: int,
                        customer_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Initiate ECOBANK Mobile payment.
        
        Args:
            amount: Payment amount
            reference: Payment reference
            order_id: Order ID
            customer_info: Customer information (must include 'phone')
            
        Returns:
            Payment initiation response
        """
        if 'phone' not in customer_info:
            raise PaymentGatewayException("Phone number is required for ECOBANK Mobile payments")
        
        # Prepare payment request
        payment_data = {
            'amount': amount,
            'currency': PaymentConfig.DEFAULT_CURRENCY,
            'reference': reference,
            'merchant_id': self.config['merchant_id'],
            'customer_phone': customer_info['phone'],
            'callback_url': PaymentConfig.PAYMENT_CALLBACK_URL,
            'return_url': PaymentConfig.PAYMENT_SUCCESS_URL
        }
        
        try:
            # Make API request to ECOBANK
            response = self._make_request('payments/initiate', 'POST', payment_data)
            
            return {
                'success': True,
                'transaction_id': response.get('transaction_id'),
                'payment_url': response.get('payment_url'),
                'reference': reference,
                'message': 'Payment initiated successfully'
            }
        except PaymentGatewayException:
            raise
        except Exception as e:
            raise PaymentGatewayException(f"Failed to initiate ECOBANK Mobile payment: {str(e)}")
    
    def verify_payment(self, reference: str, transaction_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Verify ECOBANK Mobile payment status.
        
        Args:
            reference: Payment reference
            transaction_id: Optional transaction ID
            
        Returns:
            Payment verification response
        """
        try:
            endpoint = f'payments/verify/{reference}'
            if transaction_id:
                endpoint += f'?transaction_id={transaction_id}'
            
            response = self._make_request(endpoint, 'GET')
            
            return {
                'success': response.get('status') == 'completed',
                'status': response.get('status'),
                'transaction_id': response.get('transaction_id'),
                'amount': response.get('amount'),
                'reference': reference,
                'paid_at': response.get('paid_at')
            }
        except PaymentGatewayException:
            raise
        except Exception as e:
            raise PaymentGatewayException(f"Failed to verify ECOBANK Mobile payment: {str(e)}")
    
    def process_webhook(self, payload: Dict[str, Any], signature: Optional[str] = None) -> Dict[str, Any]:
        """
        Process ECOBANK Mobile webhook notification.
        
        Args:
            payload: Webhook payload
            signature: Webhook signature
            
        Returns:
            Processed webhook data
        """
        # Verify webhook signature if provided
        if signature and PaymentConfig.PAYMENT_WEBHOOK_SECRET:
            from app.payments.utils import verify_webhook_signature
            import json
            if not verify_webhook_signature(json.dumps(payload), signature, PaymentConfig.PAYMENT_WEBHOOK_SECRET):
                raise PaymentGatewayException("Invalid webhook signature")
        
        return {
            'reference': payload.get('reference'),
            'transaction_id': payload.get('transaction_id'),
            'status': payload.get('status'),
            'amount': payload.get('amount'),
            'paid_at': payload.get('paid_at')
        }

