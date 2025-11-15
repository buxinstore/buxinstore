"""
Wave Money Payment Gateway Integration
"""

from typing import Dict, Any, Optional
from .base import BasePaymentGateway
from app.payments.exceptions import PaymentGatewayException
from app.payments.config import PaymentConfig


class WaveGateway(BasePaymentGateway):
    """Wave Money payment gateway implementation."""
    
    def get_method_name(self) -> str:
        return 'wave'
    
    def initiate_payment(self, amount: float, reference: str, order_id: int,
                        customer_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Initiate Wave Money payment.
        
        Args:
            amount: Payment amount
            reference: Payment reference
            order_id: Order ID
            customer_info: Customer information (must include 'phone')
            
        Returns:
            Payment initiation response
        """
        if 'phone' not in customer_info:
            raise PaymentGatewayException("Phone number is required for Wave Money payments")
        
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
            # Make API request to Wave Money
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
            raise PaymentGatewayException(f"Failed to initiate Wave Money payment: {str(e)}")
    
    def verify_payment(self, reference: str, transaction_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Verify Wave Money payment status.
        
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
            raise PaymentGatewayException(f"Failed to verify Wave Money payment: {str(e)}")
    
    def process_webhook(self, payload: Dict[str, Any], signature: Optional[str] = None) -> Dict[str, Any]:
        """
        Process Wave Money webhook notification.
        
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
    
    def refund_payment(self, reference: str, amount: Optional[float] = None) -> Dict[str, Any]:
        """
        Process Wave Money refund.
        
        Args:
            reference: Original payment reference
            amount: Refund amount (if None, full refund)
            
        Returns:
            Refund response
        """
        refund_data = {
            'reference': reference,
            'amount': amount  # If None, full refund
        }
        
        try:
            response = self._make_request('payments/refund', 'POST', refund_data)
            return {
                'success': True,
                'refund_id': response.get('refund_id'),
                'amount': response.get('amount'),
                'message': 'Refund processed successfully'
            }
        except Exception as e:
            raise PaymentGatewayException(f"Failed to process Wave Money refund: {str(e)}")

