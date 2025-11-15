"""
ModemPay Payment Gateway Integration
Unified Gambian payment gateway supporting Wave, QMoney, AfriMoney, ECOBANK, and Card payments.
"""

from typing import Dict, Any, Optional
import requests
import json
import hmac
import hashlib
import os
import re
from .base import BasePaymentGateway
from app.payments.exceptions import PaymentGatewayException
from app.payments.config import PaymentConfig

_MODEM_PAY_API_KEY = os.getenv("MODEM_PAY_API_KEY") or os.getenv("MODEM_PAY_SECRET_KEY") or os.getenv("MODEM_PAY_APIKEY") or PaymentConfig.MODEMPAY_SECRET_KEY
_MODEM_PAY_PUBLIC_KEY = os.getenv("MODEM_PAY_PUBLIC_KEY") or os.getenv("MODEMPAY_PUBLIC_KEY") or PaymentConfig.MODEMPAY_PUBLIC_KEY


class ModemPayGateway(BasePaymentGateway):
    """
    ModemPay unified payment gateway implementation.
    Supports multiple payment providers: wave, qmoney, afrimoney, ecobank, card
    """
    
    # Valid ModemPay providers
    VALID_PROVIDERS = ['wave', 'qmoney', 'afrimoney', 'ecobank', 'card']
    
    def get_method_name(self) -> str:
        """Return the payment method name."""
        return 'modempay'
    
    def _validate_provider(self, provider: str):
        """
        Validate that the provider is supported by ModemPay.
        
        Args:
            provider: Payment provider name
            
        Raises:
            PaymentGatewayException: If provider is not valid
        """
        if provider.lower() not in self.VALID_PROVIDERS:
            raise PaymentGatewayException(
                f"Invalid provider '{provider}'. Valid providers: {', '.join(self.VALID_PROVIDERS)}"
            )
    
    def _make_modempay_request(self, endpoint: str, method: str = 'POST',
                               data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Deprecated for ModemPay in this app. We no longer call /v1 endpoints or send JSON.
        This remains only for non-checkout operations that might be unused.
        """
        raise PaymentGatewayException("Unsupported ModemPay request. Use form-data to live checkout endpoint only.")
    
    def initiate_payment(self, amount: float, reference: str, order_id: int, customer_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Initiate a payment using ModemPay live API:
        - POST https://checkout.modempay.com/api/pay
        - form-data only (no JSON)
        - payload fields exactly as required
        - parse HTML for intent + token and build payment URL
        """
        key = (_MODEM_PAY_PUBLIC_KEY or '').strip()
        if not key or key.lower().startswith('your_'):
            raise PaymentGatewayException("MODEM_PAY_PUBLIC_KEY is missing or invalid")

        # Generate absolute cancel/return URLs using Ngrok URL for live payments
        # Priority: NGROK_URL > APP_BASE_URL > Flask request host > fallback
        def _get_base_url() -> str:
            # First, check for Ngrok URL (for live payments with public callbacks)
            ngrok_url = os.getenv('NGROK_URL', '').strip().rstrip('/')
            if ngrok_url:
                # Ensure HTTPS for Ngrok URLs
                if not ngrok_url.startswith('http'):
                    ngrok_url = f"https://{ngrok_url}"
                return ngrok_url
            
            # Second, check for APP_BASE_URL
            app_base = os.getenv('APP_BASE_URL', '').strip().rstrip('/')
            if app_base:
                # Ensure HTTPS for live payments
                if not app_base.startswith('http'):
                    app_base = f"https://{app_base}"
                return app_base
            
            # Third, try Flask's request context
            try:
                from flask import request as _req
                base = (_req.host_url or '').rstrip('/')
                if base:
                    # Convert HTTP to HTTPS for live payments if needed
                    if base.startswith('http://') and 'localhost' not in base and '127.0.0.1' not in base:
                        base = base.replace('http://', 'https://')
                    return base
            except Exception:
                pass
            
            # Last resort fallback
            return "https://modempay.com"

        base_url = _get_base_url()
        
        # Construct callback URLs with proper paths
        cancel_url = f"{base_url}/payments/failure"
        return_url = f"{base_url}/payments/success"

        # Append identifying params so success callback can find the payment without waiting for webhook
        def _append_params(url_value: str, params: Dict[str, Any]) -> str:
            try:
                from urllib.parse import urlencode, urlparse, parse_qs, urlunparse
                parsed = urlparse(url_value)
                existing = parse_qs(parsed.query)
                for k, v in params.items():
                    if v is None or v == '':
                        continue
                    existing[k] = [str(v)]
                new_query = urlencode(existing, doseq=True)
                return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
            except Exception:
                # Fallback simple concatenation
                sep = '&' if ('?' in url_value) else '?'
                kv = '&'.join([f"{k}={v}" for k, v in params.items() if v not in [None, '']])
                return f"{url_value}{sep}{kv}" if kv else url_value

        # Exact payload as required by the working test script
        # Include identifiers on callback URLs
        return_url_with_ids = _append_params(return_url, {"reference": reference, "order_id": order_id})
        cancel_url_with_ids = _append_params(cancel_url, {"reference": reference, "order_id": order_id})

        base_payload: Dict[str, Any] = {
            "amount": int(float(amount)),
            "customer_name": customer_info.get('name') or "Test User",
            "customer_email": customer_info.get('email') or "usr@example.com",
            "customer_phone": customer_info.get('phone') or "+2200000000",
            "cancel_url": cancel_url_with_ids,
            "return_url": return_url_with_ids,
            "currency": "GMD",
            "metadata": {"source": "flask-app"}
        }

        # Build form-data with flattened metadata and required public_key
        form_payload: Dict[str, Any] = {
            "public_key": key,
            "amount": base_payload["amount"],
            "customer_name": base_payload["customer_name"],
            "customer_email": base_payload["customer_email"],
            "customer_phone": base_payload["customer_phone"],
            "cancel_url": base_payload["cancel_url"],
            "return_url": base_payload["return_url"],
            "currency": base_payload["currency"],
            "metadata[source]": base_payload["metadata"]["source"],
        }

        try:
            # Send ONLY form-data to the test checkout endpoint
            # Logger setup
            try:
                from flask import has_request_context
                if has_request_context():
                    from flask import current_app
                    _logger = current_app.logger
                else:
                    import logging as _logging
                    _logger = _logging.getLogger(__name__)
            except Exception:
                import logging as _logging
                _logger = _logging.getLogger(__name__)

            safe_payload = dict(form_payload)
            if 'public_key' in safe_payload and isinstance(safe_payload['public_key'], str):
                pk = safe_payload['public_key']
                safe_payload['public_key'] = pk[:6] + '...' + pk[-6:] if len(pk) > 12 else '***'
            _logger.info({"attempt": "form", "endpoint": "https://checkout.modempay.com/api/pay", "payload": safe_payload})

            resp = requests.post(
                "https://checkout.modempay.com/api/pay",
                data=form_payload,
                timeout=30,
            )
            text = resp.text or ""
            _logger.info({"status": resp.status_code, "response_has_next": "__NEXT_DATA__" in text, "response_preview": text[:200]})

            # Successful response returns HTML containing __NEXT_DATA__
            if resp.status_code == 200 and "__NEXT_DATA__" in text:
                m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', text, re.S)
                if m:
                    try:
                        next_data = json.loads(m.group(1))
                        q = (next_data.get("query") or {})
                        intent_id = q.get("intent") or (next_data.get("props", {}).get("pageProps", {}).get("intent"))
                        token = q.get("token") or (next_data.get("props", {}).get("pageProps", {}).get("token"))
                        if intent_id and token:
                            payment_link = f"https://checkout.modempay.com/{intent_id}?token={token}"
                            _logger.info({"parsed_intent": intent_id, "built_payment_url": payment_link})
                            return {
                                'success': True,
                                'transaction_id': intent_id,
                                'payment_url': payment_link,
                                'reference': reference,
                                'message': 'Payment link created',
                            }
                    except Exception:
                        pass

            # If HTML parsing failed, treat as error to match strict test behavior
            _logger.error({"error": "Failed to create ModemPay payment link", "status": resp.status_code, "response_preview": text[:500]})
            raise PaymentGatewayException(
                "Failed to create ModemPay payment link",
                gateway_response={'status_code': resp.status_code, 'body': text[:500]}
            )

        except requests.exceptions.RequestException as e:
            raise PaymentGatewayException(
                f"ModemPay request failed: {str(e)}",
                gateway_response={'error': str(e)}
            )
    
    def verify_payment(self, reference: str, transaction_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Verify a ModemPay payment transaction status.
        
        Args:
            reference: Payment reference
            transaction_id: ModemPay transaction ID (preferred for verification)
            
        Returns:
            Payment verification response with status
            
        Raises:
            PaymentGatewayException: If payment verification fails
        """
        try:
            # Use transaction_id if available, otherwise use reference
            identifier = transaction_id or reference
            
            if not identifier:
                raise PaymentGatewayException("Either transaction_id or reference must be provided")
            
            # ModemPay verification endpoint
            endpoint = f'transactions/{identifier}'
            
            response = self._make_modempay_request(endpoint, 'GET')
            
            # Parse response
            response_data = response.get('data', response) if isinstance(response, dict) else response
            
            # Determine payment status
            status = response_data.get('status', '').lower()
            is_completed = status in ['completed', 'success', 'paid', 'successful']
            is_failed = status in ['failed', 'cancelled', 'declined', 'error']
            is_pending = status in ['pending', 'processing', 'initiated']
            
            return {
                'success': is_completed,
                'status': status,
                'transaction_id': response_data.get('transaction_id') or response_data.get('id'),
                'amount': response_data.get('amount'),
                'reference': response_data.get('reference') or reference,
                'provider': response_data.get('provider'),
                'paid_at': response_data.get('paid_at') or response_data.get('completed_at'),
                'message': response_data.get('message', 'Payment verified')
            }
            
        except PaymentGatewayException:
            raise
        except Exception as e:
            raise PaymentGatewayException(
                f"Failed to verify ModemPay payment: {str(e)}",
                gateway_response={'error': str(e)}
            )
    
    def process_webhook(self, payload: Dict[str, Any], signature: Optional[str] = None) -> Dict[str, Any]:
        """
        Process ModemPay webhook notification.
        
        Args:
            payload: Webhook payload from ModemPay
            signature: Webhook signature for verification
            
        Returns:
            Processed webhook data
            
        Raises:
            PaymentGatewayException: If webhook processing fails
        """
        # Verify webhook signature if secret is configured
        webhook_secret = PaymentConfig.MODEMPAY_WEBHOOK_SECRET
        if signature and webhook_secret:
            from app.payments.utils import verify_webhook_signature
            payload_str = json.dumps(payload, sort_keys=True) if isinstance(payload, dict) else str(payload)
            
            if not verify_webhook_signature(payload_str, signature, webhook_secret):
                raise PaymentGatewayException("Invalid ModemPay webhook signature")
        
        # Extract webhook data
        # ModemPay webhook typically contains: transaction_id, reference, status, amount, etc.
        webhook_data = payload.get('data', payload) if isinstance(payload, dict) else payload
        
        # Map ModemPay status to our internal status
        modempay_status = webhook_data.get('status', '').lower()
        status_mapping = {
            'completed': 'completed',
            'success': 'completed',
            'paid': 'completed',
            'successful': 'completed',
            'failed': 'failed',
            'cancelled': 'failed',
            'declined': 'failed',
            'error': 'failed',
            'pending': 'pending',
            'processing': 'pending',
            'initiated': 'pending'
        }
        
        mapped_status = status_mapping.get(modempay_status, modempay_status)
        
        return {
            'reference': webhook_data.get('reference'),
            'transaction_id': webhook_data.get('transaction_id') or webhook_data.get('id'),
            'status': mapped_status,
            'amount': webhook_data.get('amount'),
            'provider': webhook_data.get('provider'),
            'paid_at': webhook_data.get('paid_at') or webhook_data.get('completed_at'),
            'raw_status': modempay_status  # Keep original status for reference
        }
    
    def refund_payment(self, reference: str, amount: Optional[float] = None) -> Dict[str, Any]:
        """
        Process a ModemPay payment refund.
        
        Args:
            reference: Original payment reference
            amount: Refund amount (if None, full refund)
            
        Returns:
            Refund response
            
        Raises:
            PaymentGatewayException: If refund fails
        """
        refund_data = {
            'reference': reference,
        }
        
        if amount is not None:
            refund_data['amount'] = float(amount)
        
        try:
            response = self._make_modempay_request('transactions/refund', 'POST', refund_data)
            
            response_data = response.get('data', response) if isinstance(response, dict) else response
            
            return {
                'success': response.get('success', False) or response_data.get('status') == 'refunded',
                'refund_id': response_data.get('refund_id') or response_data.get('id'),
                'amount': response_data.get('amount') or amount,
                'message': response.get('message', 'Refund processed successfully')
            }
        except Exception as e:
            raise PaymentGatewayException(
                f"Failed to process ModemPay refund: {str(e)}",
                gateway_response={'error': str(e)}
            )
