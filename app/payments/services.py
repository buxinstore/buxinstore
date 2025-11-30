"""
Payment Services
Business logic for payment processing.
"""

from datetime import datetime
from typing import Dict, Any, Optional
from flask import current_app, render_template
from app.extensions import db
from app.payments.models import Payment, PaymentTransaction, PendingPayment
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
    def start_modempay_payment(pending_payment_id: Optional[int] = None, order_id: Optional[int] = None, 
                              amount: float = None, phone: str = None, 
                              provider: str = 'modempay',
                              customer_name: Optional[str] = None,
                              customer_email: Optional[str] = None) -> Dict[str, Any]:
        """
        Start a ModemPay payment transaction.
        Works with either pending_payment_id (new flow) or order_id (legacy flow).
        
        Args:
            pending_payment_id: PendingPayment ID (new flow - preferred)
            order_id: Order ID (legacy flow - for backward compatibility)
            amount: Payment amount (required if pending_payment_id not provided)
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
        
        # Get pending payment if provided
        pending_payment = None
        reference_id = None
        payment_amount = amount
        
        if pending_payment_id:
            pending_payment = PendingPayment.query.get(pending_payment_id)
            if not pending_payment:
                raise PaymentValidationException(f"PendingPayment {pending_payment_id} not found")
            reference_id = pending_payment_id
            payment_amount = pending_payment.amount
            if not phone:
                phone = pending_payment.customer_phone or '+2200000000'
            if not customer_name:
                customer_name = pending_payment.customer_name
            if not customer_email:
                customer_email = pending_payment.customer_email
        elif order_id:
            from app import Order
            order = Order.query.get(order_id)
            if not order:
                raise PaymentValidationException(f"Order {order_id} not found")
            reference_id = order_id
            if not payment_amount:
                payment_amount = order.total
            if not phone:
                phone = getattr(order, 'customer_phone', None) or '+2200000000'
        else:
            if not payment_amount:
                raise PaymentValidationException("Either pending_payment_id, order_id, or amount must be provided")
        
        # Validate payment amount
        if not validate_payment_amount(payment_amount, 'modempay'):
            min_amount = 10.0  # ModemPay minimum
            raise PaymentValidationException(f"Payment amount must be at least D{min_amount}. Current amount: D{payment_amount:.2f}")
        
        # Check if ModemPay is configured
        from app.payments.config import PaymentConfig
        if not PaymentConfig.is_gateway_enabled('modempay'):
            raise PaymentGatewayNotConfiguredException(
                "ModemPay is not properly configured. Please check your environment variables."
            )
        
        try:
            # Generate payment reference
            reference = generate_payment_reference(reference_id, 'modempay')
            
            # Get ModemPay gateway
            gateway = get_gateway('modempay')
            
            # Prepare customer info with provider
            customer_info = {
                'phone': phone or '+2200000000',
                'provider': provider.lower(),
            }
            if customer_name:
                customer_info['name'] = customer_name
            if customer_email:
                customer_info['email'] = customer_email
            
            # Initiate payment with ModemPay
            gateway_response = gateway.initiate_payment(
                amount=payment_amount,
                reference=reference,
                order_id=reference_id,  # Can be pending_payment_id or order_id
                customer_info=customer_info
            )
            
            # Update pending payment with transaction ID if exists
            if pending_payment:
                pending_payment.modempay_transaction_id = gateway_response.get('transaction_id')
                db.session.add(pending_payment)
            
            # Create payment record (order_id will be set later after order creation)
            payment = Payment(
                order_id=order_id,  # Will be None for pending payments
                pending_payment_id=pending_payment_id,
                amount=payment_amount,
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
    def convert_pending_payment_to_order(pending_payment_id: int) -> Dict[str, Any]:
        """
        Convert a PendingPayment to an Order after successful payment confirmation.
        This should only be called after payment is verified as successful.
        
        Args:
            pending_payment_id: ID of the PendingPayment to convert
            
        Returns:
            Dictionary with order_id and success status
            
        Raises:
            PaymentValidationException: If pending payment not found or already converted
        """
        from app import Order, OrderItem, Product, CartItem
        # Import profit calculation function from app module
        from app import get_product_price_with_profit
        
        pending_payment = PendingPayment.query.get(pending_payment_id)
        if not pending_payment:
            raise PaymentValidationException(f"PendingPayment {pending_payment_id} not found")
        
        if pending_payment.status == 'completed':
            # Already converted - find the order
            payment = Payment.query.filter_by(pending_payment_id=pending_payment_id, status='completed').first()
            if payment and payment.order_id:
                return {
                    'success': True,
                    'order_id': payment.order_id,
                    'message': 'Order already exists for this pending payment'
                }
            raise PaymentValidationException(f"PendingPayment {pending_payment_id} already processed but order not found")
        
        try:
            import json
            
            # Parse cart items from JSON
            cart_items = json.loads(pending_payment.cart_items_json) if pending_payment.cart_items_json else []
            
            # Create Order
            order = Order(
                user_id=pending_payment.user_id,
                total=pending_payment.amount,
                payment_method=pending_payment.payment_method or 'modempay',
                delivery_address=pending_payment.delivery_address or '',
                status='paid',  # Order is created only after successful payment
                shipping_status='pending',
                shipping_price=pending_payment.shipping_price,
                shipping_price_gmd=pending_payment.shipping_price,  # Store GMD value
                total_cost=pending_payment.total_cost,
                customer_name=pending_payment.customer_name,
                customer_address=pending_payment.delivery_address,
                customer_phone=pending_payment.customer_phone,
                location=pending_payment.location or 'China',
                shipping_rule_id=pending_payment.shipping_rule_id,
                shipping_delivery_estimate=pending_payment.shipping_delivery_estimate,
                shipping_display_currency=pending_payment.shipping_display_currency
            )
            
            db.session.add(order)
            db.session.flush()  # Get order.id without committing
            
            # Calculate profit totals
            total_profit_gmd = 0.0
            total_revenue_gmd = 0.0
            
            # Add order items and update stock
            for item in cart_items:
                product = Product.query.get(item['id'])
                if not product:
                    current_app.logger.warning(f"Product {item['id']} not found for pending payment {pending_payment_id}")
                    continue
                
                # Check stock availability
                if product.stock is not None and product.stock < item['quantity']:
                    current_app.logger.warning(
                        f"Insufficient stock for product {product.id}. "
                        f"Required: {item['quantity']}, Available: {product.stock}"
                    )
                    # Still create order item but log the issue
                
                # Calculate profit for this product
                base_price = float(product.price)  # Base price in GMD
                final_price, profit_amount, profit_rule_id = get_product_price_with_profit(base_price)
                
                # Calculate totals for this item
                item_base_total = base_price * item['quantity']
                item_profit_total = profit_amount * item['quantity']
                item_revenue_total = final_price * item['quantity']
                
                total_profit_gmd += item_profit_total
                total_revenue_gmd += item_revenue_total
                
                # Create order item with profit information
                order_item = OrderItem(
                    order_id=order.id,
                    product_id=item['id'],
                    quantity=item['quantity'],
                    price=final_price,  # Final price (base + profit) per unit in GMD
                    base_price=base_price,  # Base price before profit
                    profit_amount=profit_amount,  # Profit per unit
                    profit_rule_id=profit_rule_id  # Which profit rule was applied
                )
                db.session.add(order_item)
                
                # Update product stock (only if payment confirmed)
                if product.stock is not None:
                    product.stock -= item['quantity']
            
            # Store profit totals in order
            order.total_profit_gmd = total_profit_gmd
            order.total_revenue_gmd = total_revenue_gmd
            
            # Update payment to link to order
            payment = Payment.query.filter_by(pending_payment_id=pending_payment_id).order_by(Payment.id.desc()).first()
            if payment:
                payment.order_id = order.id
            
            # Mark pending payment as completed
            pending_payment.status = 'completed'
            
            # Clear user's cart
            CartItem.query.filter_by(user_id=pending_payment.user_id).delete()
            
            db.session.commit()
            
            current_app.logger.info(f"✅ Converted PendingPayment {pending_payment_id} to Order {order.id}")
            
            return {
                'success': True,
                'order_id': order.id,
                'message': 'Order created successfully'
            }
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error converting PendingPayment {pending_payment_id} to Order: {str(e)}")
            raise PaymentException(f"Failed to convert pending payment to order: {str(e)}")
    
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
                
                # If payment is linked to a PendingPayment, convert it to Order
                if payment.pending_payment_id:
                    try:
                        result = PaymentService.convert_pending_payment_to_order(payment.pending_payment_id)
                        current_app.logger.info(
                            f"✅ Webhook: Converted PendingPayment {payment.pending_payment_id} to Order {result.get('order_id')}"
                        )
                        # Payment.order_id will be set by convert_pending_payment_to_order
                    except Exception as e:
                        current_app.logger.error(
                            f"❌ Webhook: Failed to convert PendingPayment {payment.pending_payment_id} to Order: {str(e)}"
                        )
                # Update order status if order exists (legacy flow)
                elif payment.order:
                    payment.order.status = 'paid'
                    
            elif webhook_status == 'failed' and payment.status != 'failed':
                payment.status = 'failed'
                payment.failure_reason = webhook_data.get('message', 'Payment failed')
                
                # Update pending payment status if exists
                if payment.pending_payment_id:
                    pending_payment = PendingPayment.query.get(payment.pending_payment_id)
                    if pending_payment:
                        pending_payment.status = 'failed'
                        db.session.add(pending_payment)
                
                # Update order status if needed (legacy flow)
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
            
            # Send receipt email on completed payments (best-effort, via Resend email queue)
            try:
                if webhook_status == 'completed' and payment and payment.order and payment.order.user_id:
                    from app.utils.email_queue import queue_single_email
                    from app.payments.models import Payment as PaymentModel

                    with current_app.app_context():
                        current_app.logger.info("Webhook email[BG]: preparing receipt email")
                        payment_obj = PaymentModel.query.get(payment.id)
                        if not payment_obj or not payment_obj.order:
                            current_app.logger.info("Webhook email[BG]: payment/order not found, aborting")
                        else:
                            customer = payment_obj.order.customer if hasattr(payment_obj.order, 'customer') else None
                            recipient_email = getattr(customer, 'email', None)
                            recipient_name = getattr(customer, 'username', 'Customer')
                            if not recipient_email:
                                current_app.logger.info("Webhook email[BG]: no recipient email, aborting")
                            else:
                                from app import _format_email_subject
                                subject = _format_email_subject(f"Payment Receipt - Order #{payment_obj.order.id}")
                                html_body = render_template(
                                    'emails/receipt_email.html',
                                    payment=payment_obj,
                                    order=payment_obj.order,
                                    order_items=getattr(payment_obj.order, 'items', []),
                                    customer_name=recipient_name
                                )

                                app_obj = current_app._get_current_object()
                                queue_single_email(app_obj, recipient_email, subject, html_body)
                                current_app.logger.info(
                                    f"✅ Receipt email queued to {recipient_email} (webhook background via email_queue/Resend)"
                                )
            except Exception as email_err:
                current_app.logger.error(f"Failed to queue webhook receipt email: {str(email_err)}")
            
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
