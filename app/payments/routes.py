"""
Payment Routes
All payment-related API endpoints.
"""

from flask import request, jsonify, current_app, render_template, redirect, url_for
from datetime import datetime
from flask_login import login_required, current_user
from app.payments import payment_bp
from app.extensions import db
from app.payments.services import PaymentService
from app.payments.exceptions import (
    PaymentException,
    PaymentValidationException,
    PaymentMethodNotSupportedException,
    PaymentGatewayNotConfiguredException
)
from app.payments.utils import format_payment_response
from app.payments.config import VALID_PAYMENT_METHODS, PAYMENT_METHOD_DISPLAY_NAMES
# Order model will be imported from app when needed to avoid circular imports


@payment_bp.route('/initiate', methods=['POST'])
@login_required
def initiate_payment():
    """
    Initiate a payment for an order.
    
    Expected JSON payload:
    {
        "order_id": 123,
        "amount": 100.00,
        "method": "wave",
        "customer_info": {
            "phone": "+2201234567",
            "email": "customer@example.com"
        }
    }
    """
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['order_id', 'amount', 'method', 'customer_info']
        if not all(field in data for field in required_fields):
            return jsonify(format_payment_response(
                success=False,
                message='Missing required fields',
                data={'required': required_fields}
            )), 400
        
        # Validate order (import here to avoid circular imports)
        from app import Order
        order = Order.query.get(data['order_id'])
        if not order:
            return jsonify(format_payment_response(
                success=False,
                message='Order not found'
            )), 404
        
        # Verify order belongs to user or user is admin
        if order.user_id != current_user.id and not current_user.is_admin:
            return jsonify(format_payment_response(
                success=False,
                message='Unauthorized access to this order'
            )), 403
        
        # Initiate payment
        result = PaymentService.initiate_payment(
            order_id=data['order_id'],
            amount=float(data['amount']),
            method=data['method'],
            customer_info=data['customer_info']
        )
        
        return jsonify(result), 200
        
    except PaymentValidationException as e:
        return jsonify(format_payment_response(
            success=False,
            message=str(e)
        )), 400
    except PaymentMethodNotSupportedException as e:
        return jsonify(format_payment_response(
            success=False,
            message=str(e)
        )), 400
    except PaymentGatewayNotConfiguredException as e:
        return jsonify(format_payment_response(
            success=False,
            message=str(e)
        )), 503
    except PaymentException as e:
        return jsonify(format_payment_response(
            success=False,
            message=str(e)
        )), 500
    except Exception as e:
        current_app.logger.error(f"Error in initiate_payment: {str(e)}")
        return jsonify(format_payment_response(
            success=False,
            message='An error occurred while initiating payment'
        )), 500


@payment_bp.route('/verify', methods=['POST'])
def verify_payment():
    """
    Verify a payment transaction (can be called by webhook or user).
    If payment is successful and linked to PendingPayment, converts it to Order.
    
    Expected JSON payload:
    {
        "payment_id": 123,
        "reference": "WAVE123456789",
        "transaction_id": "TXN123"
    }
    """
    try:
        data = request.get_json()
        
        payment_id = data.get('payment_id')
        reference = data.get('reference')
        transaction_id = data.get('transaction_id')
        
        if not payment_id and not reference and not transaction_id:
            return jsonify(format_payment_response(
                success=False,
                message='Either payment_id, reference, or transaction_id must be provided'
            )), 400
        
        # Find payment
        from app.payments.models import Payment
        payment = None
        if payment_id:
            payment = Payment.query.get(payment_id)
        elif reference:
            payment = Payment.query.filter_by(reference=reference).first()
        elif transaction_id:
            payment = Payment.query.filter_by(transaction_id=transaction_id).first()
        
        if not payment:
            return jsonify(format_payment_response(
                success=False,
                message='Payment not found'
            )), 404
        
        # Verify payment with gateway
        result = PaymentService.verify_payment(
            payment_id=payment.id,
            reference=payment.reference
        )
        
        # If payment is verified as successful and linked to PendingPayment, convert to Order
        if result.get('success') and payment.pending_payment_id:
            try:
                conversion_result = PaymentService.convert_pending_payment_to_order(payment.pending_payment_id)
                result['order_id'] = conversion_result.get('order_id')
                result['message'] = f"Payment verified and order created: Order #{conversion_result.get('order_id')}"
                current_app.logger.info(
                    f"✅ Payment verification: Converted PendingPayment {payment.pending_payment_id} to Order {conversion_result.get('order_id')}"
                )
            except Exception as e:
                current_app.logger.error(f"Error converting PendingPayment to Order during verification: {str(e)}")
                # Don't fail the verification, just log the error
        
        return jsonify(result), 200
        
    except PaymentValidationException as e:
        return jsonify(format_payment_response(
            success=False,
            message=str(e)
        )), 400
    except PaymentException as e:
        return jsonify(format_payment_response(
            success=False,
            message=str(e)
        )), 500
    except Exception as e:
        current_app.logger.error(f"Error in verify_payment: {str(e)}")
        return jsonify(format_payment_response(
            success=False,
            message='An error occurred while verifying payment'
        )), 500


@payment_bp.route('/webhook/<method>', methods=['POST'])
def process_webhook(method):
    """
    Process webhook notification from payment gateway.
    
    Args:
        method: Payment method (wave, qmoney, etc.)
    """
    try:
        if method.lower() not in VALID_PAYMENT_METHODS:
            return jsonify(format_payment_response(
                success=False,
                message=f'Invalid payment method: {method}'
            )), 400
        
        payload = request.get_json() or request.form.to_dict()
        signature = request.headers.get('X-Signature') or request.headers.get('Signature')
        
        # Process webhook
        result = PaymentService.process_webhook(
            method=method,
            payload=payload,
            signature=signature
        )
        
        return jsonify(result), 200
        
    except PaymentException as e:
        current_app.logger.error(f"Error processing webhook: {str(e)}")
        return jsonify(format_payment_response(
            success=False,
            message=str(e)
        )), 400
    except Exception as e:
        current_app.logger.error(f"Error in process_webhook: {str(e)}")
        return jsonify(format_payment_response(
            success=False,
            message='An error occurred while processing webhook'
        )), 500


@payment_bp.route('/methods', methods=['GET'])
def get_payment_methods():
    """
    Get list of available payment methods.
    """
    from app.payments.config import PaymentConfig
    
    methods = []
    for method in VALID_PAYMENT_METHODS:
        methods.append({
            'code': method,
            'name': PAYMENT_METHOD_DISPLAY_NAMES.get(method, method.upper()),
            'enabled': PaymentConfig.is_gateway_enabled(method)
        })
    
    return jsonify(format_payment_response(
        success=True,
        message='Payment methods retrieved successfully',
        data={'methods': methods}
    )), 200


@payment_bp.route('/status/<int:payment_id>', methods=['GET'])
@login_required
def get_payment_status(payment_id):
    """
    Get payment status by payment ID.
    """
    try:
        payment = PaymentService.get_payment(payment_id)
        
        # Verify user has access
        if payment.order.user_id != current_user.id and not current_user.is_admin:
            return jsonify(format_payment_response(
                success=False,
                message='Unauthorized access'
            )), 403
        
        return jsonify(format_payment_response(
            success=True,
            message='Payment status retrieved successfully',
            data={'payment': payment.to_dict()}
        )), 200
        
    except PaymentValidationException as e:
        return jsonify(format_payment_response(
            success=False,
            message=str(e)
        )), 404
    except Exception as e:
        current_app.logger.error(f"Error in get_payment_status: {str(e)}")
        return jsonify(format_payment_response(
            success=False,
            message='An error occurred while retrieving payment status'
        )), 500


@payment_bp.route('/success', methods=['GET'])
def payment_success():
    """
    Payment success callback page.
    """
    reference = request.args.get('reference')
    # ModemPay can send different param names; accept all common ones
    transaction_id = request.args.get('transaction_id') or request.args.get('intent') or request.args.get('id')
    order_id_param = request.args.get('order_id', type=int)
    
    # Variable to store order_id for redirect
    order_id = None
    
    # Eagerly mark payment completed on success redirect when we can identify it.
    # This ensures the app shows 'Completed' immediately, even if webhook is delayed.
    try:
        if reference or transaction_id or order_id_param:
            from app.payments.models import Payment
            payment = None
            if reference:
                payment = Payment.query.filter_by(reference=reference).first()
            if not payment and transaction_id:
                payment = Payment.query.filter_by(transaction_id=transaction_id).first()
            if not payment and order_id_param:
                payment = Payment.query.filter_by(order_id=order_id_param).order_by(Payment.id.desc()).first()
            if payment and payment.status != 'completed':
                payment.status = 'completed'
                payment.paid_at = datetime.utcnow()
                
                # If payment is linked to a PendingPayment, convert it to Order
                if payment.pending_payment_id and not payment.order_id:
                    try:
                        result = PaymentService.convert_pending_payment_to_order(payment.pending_payment_id)
                        converted_order_id = result.get('order_id')
                        current_app.logger.info(
                            f"✅ Payment Success Callback: Converted PendingPayment {payment.pending_payment_id} to Order {converted_order_id}"
                        )
                        # Refresh payment to get updated order_id
                        db.session.refresh(payment)
                        # Store order_id from conversion result
                        if converted_order_id:
                            order_id = converted_order_id
                    except Exception as e:
                        current_app.logger.error(
                            f"❌ Payment Success Callback: Failed to convert PendingPayment {payment.pending_payment_id} to Order: {str(e)}"
                        )
                
                # Update order status if order exists
                if payment.order:
                    payment.order.status = 'paid'
                    # Get order_id from order if not already set
                    if not order_id:
                        order_id = payment.order.id
                
                # Get order_id from payment if not already set
                if not order_id and payment.order_id:
                    order_id = payment.order_id
                
                db.session.commit()
                # Send receipt email (best effort, via Resend email queue)
                try:
                    from app.utils.email_queue import queue_single_email
                    from app.payments.models import Payment as PaymentModel

                    with current_app.app_context():
                        payment_obj = PaymentModel.query.get(payment.id)
                        if payment_obj and payment_obj.order and payment_obj.order.customer:
                            customer = payment_obj.order.customer
                            recipient_email = getattr(customer, 'email', None)
                            recipient_name = getattr(customer, 'username', 'Customer')
                            if recipient_email:
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
                                    f"✅ Receipt email queued to {recipient_email} (background via email_queue/Resend)"
                                )
                except Exception as email_err:
                    current_app.logger.error(f"Failed to queue receipt email on success redirect: {str(email_err)}")
                
                # Send WhatsApp message (best effort, only in live mode)
                try:
                    from app.payments.whatsapp import send_whatsapp_message
                    customer = payment.order.customer if payment.order else None
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
                                current_app.logger.warning(f"Failed to send WhatsApp message: {error}")
                        else:
                            current_app.logger.info("WhatsApp message skipped: No customer phone number available")
                except Exception as whatsapp_err:
                    current_app.logger.error(f"Failed to send WhatsApp message on success redirect: {str(whatsapp_err)}")
    except Exception as _e:
        current_app.logger.error(f"Error eager-updating payment on success redirect: {_e}")
    
    # Use order_id_param as fallback if order_id wasn't set from payment
    if not order_id:
        order_id = order_id_param
    
    # Redirect to order confirmation page if we have an order_id
    if order_id:
        # Redirect to order confirmation page (route is in main app, not blueprint)
        return redirect(f'/order-confirmation/{order_id}')
    else:
        # Fallback: redirect to receipt page if we can't determine order_id
        current_app.logger.warning("Could not determine order_id for redirect, falling back to receipt page")
        return redirect(url_for('payments.payment_receipt', reference=reference, transaction_id=transaction_id, order_id=order_id_param))


@payment_bp.route('/receipt', methods=['GET'])
def payment_receipt():
    """
    Render a printable receipt page for the customer.
    Accepts ?reference=... or ?transaction_id=... (intent).
    """
    try:
        from sqlalchemy.orm import joinedload
        from app.payments.models import Payment
        # Import Order and OrderItem from main app module
        from app import Order, OrderItem
        
        reference = request.args.get('reference')
        transaction_id = request.args.get('transaction_id')
        order_id_param = request.args.get('order_id', type=int)

        payment = None

        # Eagerly load relationships to avoid N+1 queries
        if reference:
            payment = Payment.query.options(
                joinedload(Payment.order).joinedload(Order.items).joinedload(OrderItem.product),
                joinedload(Payment.order).joinedload(Order.customer)
            ).filter_by(reference=reference).first()
        if not payment and transaction_id:
            payment = Payment.query.options(
                joinedload(Payment.order).joinedload(Order.items).joinedload(OrderItem.product),
                joinedload(Payment.order).joinedload(Order.customer)
            ).filter_by(transaction_id=transaction_id).first()
        if not payment and order_id_param:
            payment = Payment.query.options(
                joinedload(Payment.order).joinedload(Order.items).joinedload(OrderItem.product),
                joinedload(Payment.order).joinedload(Order.customer)
            ).filter_by(order_id=order_id_param).order_by(Payment.id.desc()).first()

        order = getattr(payment, 'order', None) if payment else None
        order_items = getattr(order, 'items', []) if order else []

        # Get shipping method info (including inactive methods for historical orders)
        shipping_mode = None
        if order and order.shipping_mode_key:
            from app.shipping.models import ShippingMode
            shipping_mode = ShippingMode.query.filter_by(key=order.shipping_mode_key).first()
        
        return render_template('receipt.html',
                               payment=payment,
                               order=order,
                               order_items=order_items,
                               reference=reference,
                               transaction_id=transaction_id)
    except Exception as e:
        current_app.logger.error(f"Error rendering receipt: {str(e)}")
        # Fall back to a minimal JSON error to avoid blank pages
        return jsonify({'success': False, 'message': 'Failed to render receipt'}), 500


@payment_bp.route('/failure', methods=['GET'])
def payment_failure():
    """
    Payment failure callback page.
    """
    reference = request.args.get('reference')
    error = request.args.get('error', 'Payment failed')
    
    return jsonify(format_payment_response(
        success=False,
        message=error,
        data={'reference': reference}
    )), 400


@payment_bp.route('/cancel', methods=['GET'])
def payment_cancel():
    """
    Payment cancellation callback page.
    """
    reference = request.args.get('reference')
    order_id = request.args.get('order_id', type=int)
    
    # Mark payment as cancelled if we can find it
    try:
        if reference or order_id:
            from app.payments.models import Payment
            payment = None
            if reference:
                payment = Payment.query.filter_by(reference=reference).first()
            if not payment and order_id:
                payment = Payment.query.filter_by(order_id=order_id).order_by(Payment.id.desc()).first()
            if payment and payment.status not in ['completed', 'cancelled']:
                payment.status = 'cancelled'
                if payment.order:
                    payment.order.status = 'cancelled'
                db.session.commit()
    except Exception as e:
        current_app.logger.error(f"Error updating payment status on cancel: {str(e)}")
    
    return jsonify(format_payment_response(
        success=False,
        message='Payment was cancelled',
        data={'reference': reference, 'order_id': order_id}
    )), 200


@payment_bp.route('/modempay/pay', methods=['POST'])
@login_required
def modempay_pay():
    """
    Initiate a ModemPay payment.
    
    Expected JSON payload (new flow - preferred):
    {
        "pending_payment_id": 123,
        "provider": "wave"  // optional
    }
    
    Or legacy flow (backward compatibility):
    {
        "order_id": 123,
        "amount": 100.00,
        "phone": "+2201234567",
        "provider": "wave"
    }
    """
    try:
        data = request.get_json()
        
        # Normalize provider - always use ModemPay (unified gateway)
        provider_value = (data.get('provider') or 'modempay').lower()
        
        # Check for pending_payment_id (new flow)
        pending_payment_id = data.get('pending_payment_id')
        
        if pending_payment_id:
            # New flow: work with PendingPayment
            from app.payments.models import PendingPayment
            pending_payment = PendingPayment.query.get(pending_payment_id)
            
            if not pending_payment:
                return jsonify(format_payment_response(
                    success=False,
                    message=f'PendingPayment {pending_payment_id} not found'
                )), 404
            
            # Verify pending payment belongs to user or user is admin
            if pending_payment.user_id != current_user.id and not current_user.is_admin:
                return jsonify(format_payment_response(
                    success=False,
                    message='Unauthorized access to this pending payment'
                )), 403
            
            # Start ModemPay payment with pending_payment_id
            result = PaymentService.start_modempay_payment(
                pending_payment_id=pending_payment.id,
                provider=provider_value
            )
        else:
            # Legacy flow: work with order_id (backward compatibility)
            order_id = data.get('order_id')
            amount = data.get('amount')
            
            if not order_id or not amount:
                return jsonify(format_payment_response(
                    success=False,
                    message='Missing required fields. Provide either pending_payment_id or (order_id and amount)',
                    data={'required': ['pending_payment_id']}
                )), 400
            
            # Validate order (import here to avoid circular imports)
            from app import Order
            order = Order.query.get(order_id)
            
            if not order:
                return jsonify(format_payment_response(
                    success=False,
                    message=f'Order {order_id} not found'
                )), 404
            
            # Verify order belongs to user or user is admin
            if order.user_id != current_user.id and not current_user.is_admin:
                return jsonify(format_payment_response(
                    success=False,
                    message='Unauthorized access to this order'
                )), 403
            
            # Start ModemPay payment with order_id (legacy)
            result = PaymentService.start_modempay_payment(
                order_id=order.id,
                amount=float(amount),
                phone=(data.get('phone') or getattr(current_user, 'phone', None) or '+2200000000'),
                provider=provider_value,
                customer_name=(current_user.username if hasattr(current_user, 'username') else None),
                customer_email=(current_user.email if hasattr(current_user, 'email') else None)
            )
        
        # Extract payment_url directly from result (format_payment_response merges data into top level)
        success = bool(result.get('success'))
        payment_url = result.get('payment_url')  # Read directly from result, not from result['data']
        
        if success and payment_url:
            # Return JSON with payment_url for frontend to redirect
            return jsonify({'success': True, 'payment_url': payment_url}), 200
        
        # Log error if payment_url is missing
        current_app.logger.error(f"ModemPay returned success={success} but payment_url={payment_url}. Full result: {result}")
        return jsonify({'success': False, 'message': result.get('message', 'Payment initiation failed')}), 500
        
    except PaymentValidationException as e:
        current_app.logger.error(f"Error in modempay_pay (validation): {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400
    except PaymentMethodNotSupportedException as e:
        current_app.logger.error(f"Error in modempay_pay (provider): {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 400
    except PaymentGatewayNotConfiguredException as e:
        current_app.logger.error(f"Error in modempay_pay (config): {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 503
    except PaymentException as e:
        current_app.logger.error(f"Error in modempay_pay (payment): {str(e)}")
        # Extract error message from exception
        error_message = str(e)
        if hasattr(e, 'gateway_response') and isinstance(e.gateway_response, dict):
            gateway_msg = e.gateway_response.get('message') or e.gateway_response.get('body', '')
            if gateway_msg:
                error_message = gateway_msg
        return jsonify({'success': False, 'message': error_message}), 400
    except Exception as e:
        current_app.logger.error(f"Error in modempay_pay: {str(e)}")
        return jsonify({'success': False, 'message': 'An error occurred while initiating payment'}), 500


@payment_bp.route('/modempay/webhook', methods=['POST'])
def modempay_webhook():
    """
    ModemPay Webhook Receiver.
    - Logs all payloads for debugging
    - Returns HTTP 200 immediately
    - Future: Add SDK signature verification and process payload.
    - It now processes the webhook to update payment and order status.
    """
    try:
        # Get payload as JSON, with fallbacks for form data or empty payloads
        payload = request.get_json(silent=True) or request.form.to_dict() or {}
        # Log the webhook payload for debugging purposes
        current_app.logger.info(f"ModemPay Webhook Received: {payload}")

        # Process the webhook using the payment service
        # The signature can be added later for security
        signature = request.headers.get('X-ModemPay-Signature')
        PaymentService.handle_modempay_webhook(payload, signature)

    except Exception as e:
        # Log any errors but still return 200 to ModemPay
        current_app.logger.error(f"Error processing ModemPay webhook: {str(e)}")

    # Always return a 200 OK response to acknowledge receipt of the webhook
    return "", 200


@payment_bp.route('/modempay/test-auth', methods=['POST'])
def test_modempay_auth():
    """
    Test endpoint to try different ModemPay authentication methods.
    This helps identify which authentication method works with ModemPay API.
    """
    try:
        from app.payments.gateways.modempay import ModemPayGateway
        from app.payments.config import PaymentConfig
        import requests
        
        # Get ModemPay configuration
        config = PaymentConfig.get_gateway_config('modempay')
        public_key = config.get('public_key')
        secret_key = config.get('secret_key')
        api_url = config.get('api_url', 'https://api.modempay.com/v1')
        
        if not public_key or not secret_key:
            return jsonify({
                'success': False,
                'message': 'ModemPay API keys not configured'
            }), 400
        
        # Test data
        test_data = {
            'amount': 100.00,
            'currency': 'GMD',
            'phone': '+2201234567',
            'provider': 'wave'
        }
        
        url = f"{api_url}/transactions"
        results = []
        
        # Test Method 1: Custom headers (X-Public-Key, X-Secret-Key)
        headers1 = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-Public-Key': public_key,
            'X-Secret-Key': secret_key,
        }
        try:
            response1 = requests.post(url, json=test_data, headers=headers1, timeout=10)
            results.append({
                'method': 'Custom Headers (X-Public-Key, X-Secret-Key)',
                'status_code': response1.status_code,
                'success': response1.status_code == 200,
                'response': response1.text[:200]
            })
        except Exception as e:
            results.append({
                'method': 'Custom Headers (X-Public-Key, X-Secret-Key)',
                'status_code': None,
                'success': False,
                'error': str(e)
            })
        
        # Test Method 2: Bearer token with secret key only (CORRECT METHOD per ModemPay docs)
        headers2 = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': f'Bearer {secret_key}',
        }
        try:
            response2 = requests.post(url, json=test_data, headers=headers2, timeout=10)
            results.append({
                'method': 'Bearer Token (Secret Key Only) - RECOMMENDED',
                'status_code': response2.status_code,
                'success': response2.status_code == 200,
                'response': response2.text[:200]
            })
        except Exception as e:
            results.append({
                'method': 'Bearer Token (Secret Key Only) - RECOMMENDED',
                'status_code': None,
                'success': False,
                'error': str(e)
            })
        
        # Test Method 2b: Bearer token with secret key + public key in header
        headers2b = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': f'Bearer {secret_key}',
            'X-API-Key': public_key,
        }
        try:
            response2b = requests.post(url, json=test_data, headers=headers2b, timeout=10)
            results.append({
                'method': 'Bearer Token (Secret Key) + X-API-Key (Public Key)',
                'status_code': response2b.status_code,
                'success': response2b.status_code == 200,
                'response': response2b.text[:200]
            })
        except Exception as e:
            results.append({
                'method': 'Bearer Token (Secret Key) + X-API-Key (Public Key)',
                'status_code': None,
                'success': False,
                'error': str(e)
            })
        
        # Test Method 3: Bearer token with public key
        headers3 = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': f'Bearer {public_key}',
            'X-Secret-Key': secret_key,
        }
        try:
            response3 = requests.post(url, json=test_data, headers=headers3, timeout=10)
            results.append({
                'method': 'Bearer Token (Public Key) + X-Secret-Key',
                'status_code': response3.status_code,
                'success': response3.status_code == 200,
                'response': response3.text[:200]
            })
        except Exception as e:
            results.append({
                'method': 'Bearer Token (Public Key) + X-Secret-Key',
                'status_code': None,
                'success': False,
                'error': str(e)
            })
        
        # Test Method 4: Basic Auth
        import base64
        auth_string = base64.b64encode(f'{public_key}:{secret_key}'.encode()).decode()
        headers4 = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': f'Basic {auth_string}',
        }
        try:
            response4 = requests.post(url, json=test_data, headers=headers4, timeout=10)
            results.append({
                'method': 'Basic Authentication',
                'status_code': response4.status_code,
                'success': response4.status_code == 200,
                'response': response4.text[:200]
            })
        except Exception as e:
            results.append({
                'method': 'Basic Authentication',
                'status_code': None,
                'success': False,
                'error': str(e)
            })
        
        # Find which method works
        working_method = None
        for result in results:
            if result.get('success') or result.get('status_code') not in [403, 401]:
                working_method = result['method']
                break
        
        return jsonify({
            'success': True,
            'message': 'Authentication test completed',
            'results': results,
            'working_method': working_method,
            'recommendation': working_method if working_method else 'None of the methods worked. Check API keys and documentation.'
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Error testing ModemPay auth: {str(e)}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500
