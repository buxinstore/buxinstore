"""
Payment Routes
All payment-related API endpoints.
"""

from flask import request, jsonify, current_app, render_template, redirect, url_for
from datetime import datetime
from flask_mail import Message
from app.extensions import mail
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
@login_required
def verify_payment():
    """
    Verify a payment transaction.
    
    Expected JSON payload:
    {
        "payment_id": 123,
        "reference": "WAVE123456789"
    }
    """
    try:
        data = request.get_json()
        
        payment_id = data.get('payment_id')
        reference = data.get('reference')
        
        if not payment_id and not reference:
            return jsonify(format_payment_response(
                success=False,
                message='Either payment_id or reference must be provided'
            )), 400
        
        # Verify payment
        result = PaymentService.verify_payment(
            payment_id=payment_id,
            reference=reference
        )
        
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
                if payment.order:
                    payment.order.status = 'paid'
                db.session.commit()
                # Send receipt email (best effort)
                try:
                    customer = payment.order.customer if payment.order else None
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
                    current_app.logger.error(f"Failed to send receipt email on success redirect: {str(email_err)}")
                
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
    
    # Always redirect to receipt page with whatever identifiers we have
    return redirect(url_for('payments.payment_receipt', reference=reference, transaction_id=transaction_id, order_id=order_id_param))


@payment_bp.route('/receipt', methods=['GET'])
def payment_receipt():
    """
    Render a printable receipt page for the customer.
    Accepts ?reference=... or ?transaction_id=... (intent).
    """
    try:
        reference = request.args.get('reference')
        transaction_id = request.args.get('transaction_id')
        order_id_param = request.args.get('order_id', type=int)

        from app.payments.models import Payment
        payment = None

        if reference:
            payment = Payment.query.filter_by(reference=reference).first()
        if not payment and transaction_id:
            payment = Payment.query.filter_by(transaction_id=transaction_id).first()
        if not payment and order_id_param:
            payment = Payment.query.filter_by(order_id=order_id_param).order_by(Payment.id.desc()).first()

        order = getattr(payment, 'order', None) if payment else None
        order_items = getattr(order, 'items', []) if order else []

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


@payment_bp.route('/modempay/pay', methods=['POST'])
@login_required
def modempay_pay():
    """
    Initiate a ModemPay payment.
    
    Expected JSON payload:
    {
        "order_id": 123,
        "amount": 100.00,
        "phone": "+2201234567",
        "provider": "wave"  // wave, qmoney, afrimoney, ecobank, or card
    }
    """
    try:
        data = request.get_json()
        
        # Validate required fields (phone/provider optional; derived server-side)
        required_fields = ['order_id', 'amount']
        if not all(field in data for field in required_fields):
            return jsonify(format_payment_response(
                success=False,
                message='Missing required fields',
                data={'required': required_fields}
            )), 400
        
        # Normalize provider (not strictly required for ModemPay unified flow)
        provider_value = (data.get('provider') or 'modempay').lower()
        
        # Validate order (import here to avoid circular imports)
        from app import Order, OrderItem
        
        order_id = data.get('order_id')
        order = None

        if order_id:
            order = Order.query.get(order_id)

        if not order:
            current_app.logger.info(f"Order ID {order_id} not found or not provided. Creating a new order.")
            # Create a new order since one was not found/provided
            order = Order(
                user_id=current_user.id,
                total=float(data['amount']),
                payment_method='modempay',
                delivery_address="To be confirmed", # Placeholder address
                status='pending'
            )
            db.session.add(order)
            db.session.commit()  # get order.id

            # Populate order items from user's cart so receipt shows products
            try:
                from app import CartItem, Product, OrderItem as _OrderItem
                user_cart_items = CartItem.query.filter_by(user_id=current_user.id).all()
                order_total = 0.0
                for ci in user_cart_items:
                    product = Product.query.get(ci.product_id)
                    if not product or ci.quantity <= 0:
                        continue
                    # Create order item
                    oi = _OrderItem(
                        order_id=order.id,
                        product_id=product.id,
                        quantity=ci.quantity,
                        price=product.price
                    )
                    db.session.add(oi)
                    order_total += float(product.price) * int(ci.quantity)
                # Update order total if we built it from cart
                if order_total > 0:
                    order.total = order_total
                db.session.commit()
            except Exception as _e:
                current_app.logger.error(f"Failed to attach cart items to order {order.id}: {_e}")
            current_app.logger.info(f"New order created with ID: {order.id}")
        
        # Verify order belongs to user or user is admin
        if order.user_id != current_user.id and not current_user.is_admin:
            return jsonify(format_payment_response(
                success=False,
                message='Unauthorized access to this order'
            )), 403
        
        # Start ModemPay payment
        result = PaymentService.start_modempay_payment(
            order_id=order.id,
            amount=float(data['amount']),
            phone=(data.get('phone') or getattr(current_user, 'phone', None) or '+2200000000'),
            provider=provider_value,
            customer_name=(current_user.username if hasattr(current_user, 'username') else None),
            customer_email=(current_user.email if hasattr(current_user, 'email') else None)
        )
        
        # Normalize response to match required schema
        success = bool(result.get('success'))
        payment_url = result.get('payment_url')
        if success and payment_url:
            return jsonify({'success': True, 'payment_url': payment_url}), 200
        current_app.logger.error(f"ModemPay returned success={success} but payment_url={payment_url}")
        return jsonify({'success': False}), 500
        
    except PaymentValidationException as e:
        current_app.logger.error(f"Error in modempay_pay (validation): {str(e)}")
        return jsonify({'success': False}), 400
    except PaymentMethodNotSupportedException as e:
        current_app.logger.error(f"Error in modempay_pay (provider): {str(e)}")
        return jsonify({'success': False}), 400
    except PaymentGatewayNotConfiguredException as e:
        current_app.logger.error(f"Error in modempay_pay (config): {str(e)}")
        return jsonify({'success': False}), 503
    except PaymentException as e:
        current_app.logger.error(f"Error in modempay_pay (payment): {str(e)}")
        return jsonify({'success': False}), 500
    except Exception as e:
        current_app.logger.error(f"Error in modempay_pay: {str(e)}")
        return jsonify({'success': False}), 500


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
