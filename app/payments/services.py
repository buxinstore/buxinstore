"""
Payment Services
Business logic for payment processing.
"""

from datetime import datetime
from typing import Dict, Any, Optional
from flask import current_app, render_template
from app.extensions import db
from app.payments.models import Payment, PaymentTransaction
from app.payments.gateways import get_gateway
from app.payments.utils import (
    generate_payment_reference,
    generate_transaction_id,
    validate_payment_amount,
    format_payment_response
)
from app.payments.exceptions import (
    PaymentException,
    PaymentValidationException,
    PaymentAmountMismatchException,
    PaymentMethodNotSupportedException,
    PaymentGatewayNotConfiguredException
)
from app.payments.config import VALID_PAYMENT_METHODS


class PaymentService:
    """Service class for handling payment operations."""
    
    @staticmethod
    def initiate_payment(order_id: int, amount: float, method: str, 
                         customer_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Initiate a payment for an order.
        
        Args:
            order_id: Order ID
            amount: Payment amount
            method: Payment method (wave, qmoney, etc.)
            customer_info: Customer information (phone, email, etc.)
            
        Returns:
            Payment initiation response
            
        Raises:
            PaymentValidationException: If payment data is invalid
            PaymentMethodNotSupportedException: If payment method is not supported
            PaymentGatewayNotConfiguredException: If gateway is not configured
        """
        # Validate payment method
        if method.lower() not in VALID_PAYMENT_METHODS:
            raise PaymentMethodNotSupportedException(f"Payment method '{method}' is not supported")
        
        # Validate payment amount
        if not validate_payment_amount(amount, method):
            raise PaymentValidationException(f"Invalid payment amount: {amount}")
        
        # Check if gateway is configured
        from app.payments.config import PaymentConfig
        if not PaymentConfig.is_gateway_enabled(method):
            raise PaymentGatewayNotConfiguredException(
                f"Payment gateway '{method}' is not properly configured"
            )
        
        try:
            # Generate payment reference
            reference = generate_payment_reference(order_id, method)
            
            # Get payment gateway
            gateway = get_gateway(method)
            
            # Initiate payment with gateway
            gateway_response = gateway.initiate_payment(
                amount=amount,
                reference=reference,
                order_id=order_id,
                customer_info=customer_info
            )
            
            # Create payment record
            payment = Payment(
                order_id=order_id,
                amount=amount,
                method=method,
                reference=reference,
                status='pending',
                transaction_id=gateway_response.get('transaction_id'),
                payment_provider_response=str(gateway_response)
            )
            
            db.session.add(payment)
            
            # Create transaction log
            transaction = PaymentTransaction(
                payment=payment,
                action='initiate',
                status='success' if gateway_response.get('success') else 'failed',
                request_data=str(customer_info),
                response_data=str(gateway_response)
            )
            
            db.session.add(transaction)
            db.session.commit()
            
            return format_payment_response(
                success=True,
                message='Payment initiated successfully',
                data={
                    'payment_id': payment.id,
                    'reference': reference,
                    'transaction_id': gateway_response.get('transaction_id'),
                    'payment_url': gateway_response.get('payment_url'),
                    'gateway_response': gateway_response
                }
            )
            
        except PaymentException:
            raise
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error initiating payment: {str(e)}")
            raise PaymentException(f"Failed to initiate payment: {str(e)}")
    
    @staticmethod
    def verify_payment(payment_id: Optional[int] = None, 
                      reference: Optional[str] = None) -> Dict[str, Any]:
        """
        Verify a payment transaction.
        
        Args:
            payment_id: Payment ID (optional)
            reference: Payment reference (optional)
            
        Returns:
            Payment verification response
            
        Raises:
            PaymentValidationException: If payment data is invalid
        """
        if not payment_id and not reference:
            raise PaymentValidationException("Either payment_id or reference must be provided")
        
        # Get payment record
        if payment_id:
            payment = Payment.query.get(payment_id)
        else:
            payment = Payment.query.filter_by(reference=reference).first()
        
        if not payment:
            raise PaymentValidationException("Payment not found")
        
        try:
            # Get payment gateway
            gateway = get_gateway(payment.method)
            
            # Verify payment with gateway
            verification_response = gateway.verify_payment(
                reference=payment.reference,
                transaction_id=payment.transaction_id
            )
            
            # Update payment status if verified
            if verification_response.get('success') and payment.status != 'completed':
                payment.status = 'completed'
                payment.paid_at = datetime.utcnow()
                payment.transaction_id = verification_response.get('transaction_id') or payment.transaction_id
                
                # Update order status
                if payment.order:
                    payment.order.status = 'paid'
            
            # Create transaction log
            transaction = PaymentTransaction(
                payment=payment,
                action='verify',
                status='success' if verification_response.get('success') else 'failed',
                request_data=str({'reference': payment.reference}),
                response_data=str(verification_response)
            )
            
            db.session.add(transaction)
            db.session.commit()
            
            return format_payment_response(
                success=verification_response.get('success', False),
                message='Payment verified',
                data={
                    'payment': payment.to_dict(),
                    'verification': verification_response
                }
            )
            
        except PaymentException:
            raise
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error verifying payment: {str(e)}")
            raise PaymentException(f"Failed to verify payment: {str(e)}")
    
    @staticmethod
    def process_webhook(method: str, payload: Dict[str, Any], 
                       signature: Optional[str] = None) -> Dict[str, Any]:
        """
        Process webhook notification from payment gateway.
        
        Args:
            method: Payment method
            payload: Webhook payload
            signature: Webhook signature
            
        Returns:
            Webhook processing response
        """
        try:
            # Get payment gateway
            gateway = get_gateway(method)
            
            # Process webhook
            webhook_data = gateway.process_webhook(payload, signature)
            
            # Find payment by reference
            reference = webhook_data.get('reference')
            if not reference:
                raise PaymentValidationException("Payment reference not found in webhook")
            
            payment = Payment.query.filter_by(reference=reference).first()
            if not payment:
                raise PaymentValidationException(f"Payment with reference {reference} not found")
            
            # Update payment status
            if webhook_data.get('status') == 'completed' and payment.status != 'completed':
                payment.status = 'completed'
                payment.paid_at = datetime.utcnow()
                payment.transaction_id = webhook_data.get('transaction_id') or payment.transaction_id
                
                # Update order status
                if payment.order:
                    payment.order.status = 'paid'
            
            # Create transaction log
            transaction = PaymentTransaction(
                payment=payment,
                action='webhook',
                status='success',
                request_data=str(payload),
                response_data=str(webhook_data)
            )
            
            db.session.add(transaction)
            db.session.commit()
            
            return format_payment_response(
                success=True,
                message='Webhook processed successfully',
                data={'payment': payment.to_dict()}
            )
            
        except PaymentException:
            raise
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error processing webhook: {str(e)}")
            raise PaymentException(f"Failed to process webhook: {str(e)}")
    
    @staticmethod
    def get_payment(payment_id: int) -> Payment:
        """
        Get payment by ID.
        
        Args:
            payment_id: Payment ID
            
        Returns:
            Payment object
        """
        payment = Payment.query.get(payment_id)
        if not payment:
            raise PaymentValidationException("Payment not found")
        return payment
    
    @staticmethod
    def get_payment_by_reference(reference: str) -> Payment:
        """
        Get payment by reference.
        
        Args:
            reference: Payment reference
            
        Returns:
            Payment object
        """
        payment = Payment.query.filter_by(reference=reference).first()
        if not payment:
            raise PaymentValidationException("Payment not found")
        return payment
    
    @staticmethod
    def start_modempay_payment(order_id: int, amount: float, phone: str, 
                              provider: str = 'wave',
                              customer_name: Optional[str] = None,
                              customer_email: Optional[str] = None) -> Dict[str, Any]:
        """
        Start a ModemPay payment transaction.
        Convenience method specifically for ModemPay unified gateway.
        
        Args:
            order_id: Order ID
            amount: Payment amount
            phone: Customer phone number
            provider: Payment provider (wave, qmoney, afrimoney, ecobank, card)
            
        Returns:
            Payment initiation response
            
        Raises:
            PaymentValidationException: If payment data is invalid
            PaymentMethodNotSupportedException: If provider is not supported
            PaymentGatewayNotConfiguredException: If ModemPay is not configured
        """
        # Validate provider
        valid_providers = ['wave', 'qmoney', 'afrimoney', 'ecobank', 'card', 'modempay']
        if provider.lower() not in valid_providers:
            raise PaymentMethodNotSupportedException(
                f"Invalid provider '{provider}'. Valid providers: {', '.join(valid_providers)}"
            )
        
        # Validate payment amount
        if not validate_payment_amount(amount, 'modempay'):
            raise PaymentValidationException(f"Invalid payment amount: {amount}")
        
        # Check if ModemPay is configured
        from app.payments.config import PaymentConfig
        if not PaymentConfig.is_gateway_enabled('modempay'):
            raise PaymentGatewayNotConfiguredException(
                "ModemPay is not properly configured. Please check your environment variables."
            )
        
        try:
            # Generate payment reference
            reference = generate_payment_reference(order_id, 'modempay')
            
            # Get ModemPay gateway
            gateway = get_gateway('modempay')
            
            # Prepare customer info with provider
            customer_info = {
                'phone': phone,
                'provider': provider.lower(),
            }
            if customer_name:
                customer_info['name'] = customer_name
            if customer_email:
                customer_info['email'] = customer_email
            
            # Initiate payment with ModemPay
            gateway_response = gateway.initiate_payment(
                amount=amount,
                reference=reference,
                order_id=order_id,
                customer_info=customer_info
            )
            
            # Create payment record
            payment = Payment(
                order_id=order_id,
                amount=amount,
                method='modempay',
                reference=reference,
                status='pending',
                transaction_id=gateway_response.get('transaction_id'),
                payment_provider_response=str(gateway_response)
            )
            
            db.session.add(payment)
            
            # Create transaction log
            transaction = PaymentTransaction(
                payment=payment,
                action='initiate',
                status='success' if gateway_response.get('success') else 'failed',
                request_data=str(customer_info),
                response_data=str(gateway_response)
            )
            
            db.session.add(transaction)
            db.session.commit()
            
            return format_payment_response(
                success=True,
                message='ModemPay payment initiated successfully',
                data={
                    'payment_id': payment.id,
                    'reference': reference,
                    'transaction_id': gateway_response.get('transaction_id'),
                    'payment_url': gateway_response.get('payment_url'),
                    'provider': provider,
                    'gateway_response': gateway_response
                }
            )
            
        except PaymentException:
            raise
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error starting ModemPay payment: {str(e)}")
            raise PaymentException(f"Failed to start ModemPay payment: {str(e)}")
    
    @staticmethod
    def handle_modempay_webhook(payload: Dict[str, Any], 
                                signature: Optional[str] = None) -> Dict[str, Any]:
        """
        Handle ModemPay webhook notification.
        Convenience method specifically for ModemPay webhooks.
        
        Args:
            payload: Webhook payload from ModemPay
            signature: Webhook signature for verification
            
        Returns:
            Webhook processing response
            
        Raises:
            PaymentException: If webhook processing fails
        """
        try:
            # Get ModemPay gateway
            gateway = get_gateway('modempay')
            
            # Process webhook
            webhook_data = gateway.process_webhook(payload, signature)
            
            # Find payment by reference or transaction_id
            reference = webhook_data.get('reference')
            transaction_id = webhook_data.get('transaction_id')
            
            payment = None
            if reference:
                payment = Payment.query.filter_by(reference=reference).first()
            elif transaction_id:
                payment = Payment.query.filter_by(transaction_id=transaction_id).first()
            
            if not payment:
                # If payment not found, log and return error
                current_app.logger.warning(
                    f"ModemPay webhook received for unknown payment: "
                    f"reference={reference}, transaction_id={transaction_id}"
                )
                raise PaymentValidationException(
                    f"Payment not found for reference: {reference or 'N/A'}"
                )
            
            # Update payment status based on webhook data
            webhook_status = webhook_data.get('status', '').lower()
            
            if webhook_status == 'completed' and payment.status != 'completed':
                payment.status = 'completed'
                payment.paid_at = datetime.utcnow()
                payment.transaction_id = transaction_id or payment.transaction_id
                
                # Update order status
                if payment.order:
                    payment.order.status = 'paid'
                    
            elif webhook_status == 'failed' and payment.status != 'failed':
                payment.status = 'failed'
                payment.failure_reason = webhook_data.get('message', 'Payment failed')
                
                # Update order status if needed
                if payment.order and payment.order.status not in ['cancelled', 'failed']:
                    payment.order.status = 'failed'
            
            # Create transaction log
            transaction = PaymentTransaction(
                payment=payment,
                action='webhook',
                status='success',
                request_data=str(payload),
                response_data=str(webhook_data)
            )
            
            db.session.add(transaction)
            db.session.commit()
            
            # Send receipt email on completed payments (best-effort, non-blocking error handling)
            try:
                if webhook_status == 'completed' and payment and payment.order and payment.order.user_id:
                    from app.extensions import mail
                    from flask_mail import Message
                    # Resolve customer email and name
                    customer = payment.order.customer if hasattr(payment.order, 'customer') else None
                    recipient_email = getattr(customer, 'email', None)
                    recipient_name = getattr(customer, 'username', 'Customer')
                    if recipient_email:
                        subject = f"Payment Receipt - Order #{payment.order.id}"
                        html_body = render_template(
                            'emails/receipt_email.html',
                            payment=payment,
                            order=payment.order,
                            order_items=getattr(payment.order, 'items', []),
                            customer_name=recipient_name
                        )
                        msg = Message(
                            subject=subject, 
                            recipients=[recipient_email],
                            sender=current_app.config.get('MAIL_DEFAULT_SENDER', current_app.config.get('MAIL_USERNAME'))
                        )
                        msg.html = html_body
                        mail.send(msg)
                        current_app.logger.info(f"✅ Receipt email sent to {recipient_email}")
            except Exception as email_err:
                current_app.logger.error(f"Failed to send receipt email: {str(email_err)}")
            
            # Send WhatsApp message on completed payments (best-effort, only in live mode)
            try:
                if webhook_status == 'completed' and payment and payment.order:
                    from app.payments.whatsapp import send_whatsapp_message
                    customer = payment.order.customer if hasattr(payment.order, 'customer') else None
                    if customer:
                        # Get customer name
                        customer_name = getattr(customer, 'display_name', None) or getattr(customer, 'username', 'Customer')
                        
                        # Get customer phone number (try order.customer_phone first, then profile)
                        customer_phone = None
                        if payment.order and hasattr(payment.order, 'customer_phone') and payment.order.customer_phone:
                            customer_phone = payment.order.customer_phone
                        elif customer and hasattr(customer, 'profile') and customer.profile:
                            customer_phone = getattr(customer.profile, 'phone_number', None)
                        
                        if customer_phone:
                            # Get payment reference
                            payment_reference = payment.reference or payment.transaction_id or str(payment.id)
                            
                            # Send WhatsApp message
                            success, error = send_whatsapp_message(
                                to=customer_phone,
                                customer_name=customer_name,
                                amount=payment.amount,
                                reference=payment_reference
                            )
                            if not success:
                                current_app.logger.warning(f"Failed to send WhatsApp message via webhook: {error}")
                        else:
                            current_app.logger.info("WhatsApp message skipped via webhook: No customer phone number available")
            except Exception as whatsapp_err:
                current_app.logger.error(f"Failed to send WhatsApp message via webhook: {str(whatsapp_err)}")
            
            return format_payment_response(
                success=True,
                message='ModemPay webhook processed successfully',
                data={
                    'payment': payment.to_dict(),
                    'webhook_status': webhook_status
                }
            )
            
        except PaymentException:
            raise
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error handling ModemPay webhook: {str(e)}")
            raise PaymentException(f"Failed to process ModemPay webhook: {str(e)}")
