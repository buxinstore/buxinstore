# Payment System

This folder contains all payment-related functionality for the application. Everything related to payments is organized here for easy management and maintenance.

## Folder Structure

```
payments/
├── __init__.py          # Package initialization and blueprint setup
├── models.py            # Payment database models
├── config.py           # Payment configuration and settings
├── services.py         # Payment business logic
├── routes.py           # Payment API endpoints
├── utils.py            # Payment utility functions
├── exceptions.py       # Custom payment exceptions
├── gateways/           # Payment gateway integrations
│   ├── __init__.py
│   ├── base.py         # Base gateway class
│   ├── wave.py         # Wave Money integration
│   ├── qmoney.py       # QMoney integration
│   ├── afrimoney.py    # AfriMoney integration
│   ├── ecobank.py      # ECOBANK Mobile integration
│   └── modempay.py     # ModemPay unified gateway integration
└── README.md           # This file
```

## Setup Instructions

### 1. Environment Variables

Add the following environment variables to your `.env` file or system environment:

```env
# Wave Money
WAVE_API_URL=https://api.wave.com/v1
WAVE_API_KEY=your_wave_api_key
WAVE_SECRET_KEY=your_wave_secret_key
WAVE_MERCHANT_ID=your_wave_merchant_id

# QMoney
QMONEY_API_URL=https://api.qmoney.com/v1
QMONEY_API_KEY=your_qmoney_api_key
QMONEY_SECRET_KEY=your_qmoney_secret_key
QMONEY_MERCHANT_ID=your_qmoney_merchant_id

# AfriMoney
AFRIMONEY_API_URL=https://api.afrimoney.com/v1
AFRIMONEY_API_KEY=your_afrimoney_api_key
AFRIMONEY_SECRET_KEY=your_afrimoney_secret_key
AFRIMONEY_MERCHANT_ID=your_afrimoney_merchant_id

# ECOBANK Mobile
ECOBANK_API_URL=https://api.ecobank.com/v1
ECOBANK_API_KEY=your_ecobank_api_key
ECOBANK_SECRET_KEY=your_ecobank_secret_key
ECOBANK_MERCHANT_ID=your_ecobank_merchant_id

# ModemPay (Unified Gambian Payment Gateway)
MODEMPAY_API_URL=https://api.modempay.com/v1
MODEMPAY_PUBLIC_KEY=your_modempay_public_key
MODEMPAY_SECRET_KEY=your_modempay_secret_key
MODEMPAY_WEBHOOK_SECRET=your_modempay_webhook_secret
MODEMPAY_CALLBACK_URL=https://yourdomain.com/payments/modempay/webhook

# Payment Settings
PAYMENT_WEBHOOK_SECRET=your_webhook_secret
PAYMENT_SUCCESS_URL=/payments/success
PAYMENT_FAILURE_URL=/payments/failure
PAYMENT_CALLBACK_URL=/payments/callback
DEFAULT_CURRENCY=GMD
```

### 2. Initialize Payment System

In your main application file (`app/__init__.py`), add:

```python
from app.payments import init_payment_system

# After creating the app
app = create_app()

# Initialize payment system
init_payment_system(app)
```

### 3. Database Migration

Run database migrations to create payment tables:

```bash
flask db migrate -m "Add payment tables"
flask db upgrade
```

## Usage

### Initiating a Payment

```python
from app.payments.services import PaymentService

# Initiate payment
result = PaymentService.initiate_payment(
    order_id=123,
    amount=100.00,
    method='wave',
    customer_info={
        'phone': '+2201234567',
        'email': 'customer@example.com'
    }
)
```

### Verifying a Payment

```python
# Verify by payment ID
result = PaymentService.verify_payment(payment_id=123)

# Or verify by reference
result = PaymentService.verify_payment(reference='WAVE123456789')
```

### API Endpoints

- `POST /payments/initiate` - Initiate a payment
- `POST /payments/verify` - Verify a payment
- `POST /payments/webhook/<method>` - Process webhook notifications
- `GET /payments/methods` - Get available payment methods
- `GET /payments/status/<payment_id>` - Get payment status
- `GET /payments/success` - Payment success callback
- `GET /payments/failure` - Payment failure callback

### ModemPay Endpoints (Unified Gateway)

- `POST /payments/modempay/pay` - Initiate ModemPay payment
- `POST /payments/modempay/webhook` - Process ModemPay webhook notifications

## Payment Models

### Payment
- Tracks payment transactions
- Fields: order_id, amount, method, reference, status, transaction_id, etc.

### PaymentMethod
- Configuration for payment methods
- Fields: name, display_name, is_active, fees, etc.

### PaymentTransaction
- Detailed transaction logs
- Fields: payment_id, action, status, request_data, response_data, etc.

## Adding a New Payment Gateway

1. Create a new file in `gateways/` (e.g., `newgateway.py`)
2. Inherit from `BasePaymentGateway`
3. Implement required methods:
   - `get_method_name()`
   - `initiate_payment()`
   - `verify_payment()`
   - `process_webhook()`
4. Add configuration in `config.py`
5. Register in `gateways/__init__.py`

## ModemPay Integration (Unified Gateway)

ModemPay is a unified payment gateway that supports multiple Gambian payment providers through a single API.

### Supported Providers
- **wave** - Wave Money
- **qmoney** - QMoney
- **afrimoney** - AfriMoney
- **ecobank** - ECOBANK Mobile
- **card** - Card payments

### Using ModemPay

```python
from app.payments.services import PaymentService

# Start a ModemPay payment
result = PaymentService.start_modempay_payment(
    order_id=123,
    amount=100.00,
    phone='+2201234567',
    provider='wave'  # or 'qmoney', 'afrimoney', 'ecobank', 'card'
)
```

### ModemPay API Example

```bash
# Initiate ModemPay payment
curl -X POST http://localhost:5000/payments/modempay/pay \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "order_id": 123,
    "amount": 100.00,
    "phone": "+2201234567",
    "provider": "wave"
  }'
```

### ModemPay Webhook Configuration

1. Set `MODEMPAY_WEBHOOK_SECRET` in your environment variables
2. Configure webhook URL in ModemPay dashboard: `https://yourdomain.com/payments/modempay/webhook`
3. Webhook signature is automatically verified for security

## Testing

Test payment endpoints using curl or Postman:

```bash
# Initiate payment (traditional gateway)
curl -X POST http://localhost:5000/payments/initiate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "order_id": 123,
    "amount": 100.00,
    "method": "wave",
    "customer_info": {
      "phone": "+2201234567",
      "email": "customer@example.com"
    }
  }'

# Initiate ModemPay payment
curl -X POST http://localhost:5000/payments/modempay/pay \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "order_id": 123,
    "amount": 100.00,
    "phone": "+2201234567",
    "provider": "wave"
  }'
```

## Notes

- All payment gateway credentials should be stored in environment variables
- Webhook signatures are verified for security
- Payment transactions are logged for audit purposes
- Payment status is automatically synced with order status
- ModemPay provides a unified interface for multiple payment providers

