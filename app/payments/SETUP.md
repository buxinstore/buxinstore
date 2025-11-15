# Payment System Setup Guide

## Quick Start

### 1. Initialize Payment System in Your App

Add this to your `app/__init__.py` file (in the `create_app()` function, after creating the Flask app):

```python
from app.payments import init_payment_system

# After: app = Flask(__name__)
# Add:
init_payment_system(app)
```

### 2. Configure Environment Variables

Create or update your `.env` file with payment gateway credentials:

```env
# Wave Money Configuration
WAVE_API_KEY=your_wave_api_key_here
WAVE_SECRET_KEY=your_wave_secret_key_here
WAVE_MERCHANT_ID=your_wave_merchant_id_here
WAVE_API_URL=https://api.wave.com/v1

# QMoney Configuration
QMONEY_API_KEY=your_qmoney_api_key_here
QMONEY_SECRET_KEY=your_qmoney_secret_key_here
QMONEY_MERCHANT_ID=your_qmoney_merchant_id_here
QMONEY_API_URL=https://api.qmoney.com/v1

# AfriMoney Configuration
AFRIMONEY_API_KEY=your_afrimoney_api_key_here
AFRIMONEY_SECRET_KEY=your_afrimoney_secret_key_here
AFRIMONEY_MERCHANT_ID=your_afrimoney_merchant_id_here
AFRIMONEY_API_URL=https://api.afrimoney.com/v1

# ECOBANK Mobile Configuration
ECOBANK_API_KEY=your_ecobank_api_key_here
ECOBANK_SECRET_KEY=your_ecobank_secret_key_here
ECOBANK_MERCHANT_ID=your_ecobank_merchant_id_here
ECOBANK_API_URL=https://api.ecobank.com/v1

# ModemPay Configuration (Unified Gambian Payment Gateway)
# ModemPay supports: wave, qmoney, afrimoney, ecobank, and card payments
MODEMPAY_API_URL=https://api.modempay.com/v1
MODEMPAY_PUBLIC_KEY=your_modempay_public_key_here
MODEMPAY_SECRET_KEY=your_modempay_secret_key_here
MODEMPAY_WEBHOOK_SECRET=your_modempay_webhook_secret_here
MODEMPAY_CALLBACK_URL=https://yourdomain.com/payments/modempay/webhook

# Payment Settings
PAYMENT_WEBHOOK_SECRET=your_secure_webhook_secret_here
PAYMENT_SUCCESS_URL=/payments/success
PAYMENT_FAILURE_URL=/payments/failure
PAYMENT_CALLBACK_URL=/payments/callback
DEFAULT_CURRENCY=GMD
```

### 3. Run Database Migrations

Create and apply database migrations for payment tables:

```bash
flask db migrate -m "Add payment system tables"
flask db upgrade
```

### 4. Test the Payment System

The payment system is now ready! You can test it using the API endpoints:

- `POST /payments/initiate` - Start a payment
- `POST /payments/verify` - Verify a payment
- `GET /payments/methods` - List available payment methods
- `GET /payments/status/<payment_id>` - Get payment status

### 5. ModemPay Setup (Optional but Recommended)

ModemPay is a unified gateway that supports all Gambian payment providers. To use it:

1. **Get API Keys**: Sign up at ModemPay and get your `MODEMPAY_PUBLIC_KEY` and `MODEMPAY_SECRET_KEY`
2. **Configure Webhook**: Set `MODEMPAY_WEBHOOK_SECRET` and `MODEMPAY_CALLBACK_URL` in your `.env`
3. **Test Mode**: ModemPay may have a test/sandbox mode - check their documentation
4. **Webhook URL**: Configure in ModemPay dashboard: `https://yourdomain.com/payments/modempay/webhook`

**ModemPay Endpoints:**
- `POST /payments/modempay/pay` - Initiate payment with provider selection
- `POST /payments/modempay/webhook` - Receive payment status updates

## Integration Examples

### Traditional Gateway (Wave, QMoney, etc.)

```python
from app.payments.services import PaymentService

@app.route('/checkout', methods=['POST'])
@login_required
def checkout():
    # ... your checkout logic ...
    
    # After creating the order, initiate payment
    try:
        payment_result = PaymentService.initiate_payment(
            order_id=order.id,
            amount=order.total,
            method=form.payment_method.data,  # 'wave', 'qmoney', etc.
            customer_info={
                'phone': form.phone.data,
                'email': form.email.data
            }
        )
        
        if payment_result['success']:
            # Redirect to payment URL or show payment instructions
            return redirect(payment_result['data']['payment_url'])
    except Exception as e:
        flash(f'Payment initiation failed: {str(e)}', 'error')
        return redirect(url_for('cart'))
```

### ModemPay Integration (Recommended)

```python
from app.payments.services import PaymentService

@app.route('/checkout', methods=['POST'])
@login_required
def checkout():
    # ... your checkout logic ...
    
    # After creating the order, initiate ModemPay payment
    try:
        payment_result = PaymentService.start_modempay_payment(
            order_id=order.id,
            amount=order.total,
            phone=form.phone.data,
            provider=form.payment_provider.data  # 'wave', 'qmoney', 'afrimoney', 'ecobank', 'card'
        )
        
        if payment_result['success']:
            # Redirect to payment URL
            return redirect(payment_result['data']['payment_url'])
    except Exception as e:
        flash(f'Payment initiation failed: {str(e)}', 'error')
        return redirect(url_for('cart'))
```

## File Structure

All payment-related code is in the `app/payments/` folder:

- **models.py** - Database models (Payment, PaymentMethod, PaymentTransaction)
- **services.py** - Business logic for payments
- **routes.py** - API endpoints
- **config.py** - Configuration settings
- **utils.py** - Helper functions
- **exceptions.py** - Custom exceptions
- **gateways/** - Payment gateway integrations
  - **base.py** - Base gateway class
  - **wave.py** - Wave Money
  - **qmoney.py** - QMoney
  - **afrimoney.py** - AfriMoney
  - **ecobank.py** - ECOBANK Mobile
  - **modempay.py** - ModemPay (Unified Gateway)

## Notes

- All payment gateway credentials should be kept secure in environment variables
- Payment transactions are automatically logged for audit purposes
- Webhook signatures are verified for security
- Payment status automatically updates order status when payment is completed

