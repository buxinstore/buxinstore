# ModemPay 403 Forbidden - Troubleshooting Guide

## Current Error
```
403 Client Error: Forbidden for url: https://api.modempay.com/v1/transactions
```

## Current Authentication Method

The code now uses:
- `Authorization: Bearer {public_key}`
- `X-Secret-Key: {secret_key}`

## Quick Fix Options

### Option 1: Try Secret Key as Bearer Token

Edit `app/payments/gateways/modempay.py` around line 88-94:

**Change from:**
```python
if public_key:
    headers['Authorization'] = f'Bearer {public_key}'
if secret_key:
    headers['X-Secret-Key'] = secret_key
```

**Change to:**
```python
if secret_key:
    headers['Authorization'] = f'Bearer {secret_key}'
if public_key:
    headers['X-API-Key'] = public_key
```

### Option 2: Try Both Keys in Custom Headers

**Change to:**
```python
if public_key:
    headers['X-Public-Key'] = public_key
if secret_key:
    headers['X-Secret-Key'] = secret_key
# Remove Authorization header
```

### Option 3: Try Public Key Only

**Change to:**
```python
if public_key:
    headers['Authorization'] = f'Bearer {public_key}'
# Don't include secret key
```

### Option 4: Try Basic Authentication

**Change to:**
```python
import base64
if public_key and secret_key:
    auth_string = base64.b64encode(f'{public_key}:{secret_key}'.encode()).decode()
    headers['Authorization'] = f'Basic {auth_string}'
```

## Verify Your API Keys

1. **Check if keys are correct**:
   - Public key should start with `pk_`
   - Secret key should start with `sk_`
   - Test keys should have `_test_` in them

2. **Verify keys in .env**:
   ```bash
   # Check your .env file
   cat .env | grep MODEMPAY
   ```

3. **Test keys are active**:
   - Log into ModemPay dashboard
   - Verify keys are not expired or revoked
   - Check if keys have required permissions

## Check API Endpoint

Verify the API URL is correct:
- Test environment: `https://api.modempay.com/v1` or `https://api-sandbox.modempay.com/v1`
- Production: `https://api.modempay.com/v1`

## Test with curl

Test the API directly to see what works:

```bash
# Method 1: Public key as Bearer
curl -X POST https://api.modempay.com/v1/transactions \
  -H "Authorization: Bearer pk_test_..." \
  -H "X-Secret-Key: sk_test_..." \
  -H "Content-Type: application/json" \
  -d '{"amount": 100, "phone": "+2201234567", "provider": "wave"}'

# Method 2: Secret key as Bearer
curl -X POST https://api.modempay.com/v1/transactions \
  -H "Authorization: Bearer sk_test_..." \
  -H "X-API-Key: pk_test_..." \
  -H "Content-Type: application/json" \
  -d '{"amount": 100, "phone": "+2201234567", "provider": "wave"}'

# Method 3: Custom headers
curl -X POST https://api.modempay.com/v1/transactions \
  -H "X-Public-Key: pk_test_..." \
  -H "X-Secret-Key: sk_test_..." \
  -H "Content-Type: application/json" \
  -d '{"amount": 100, "phone": "+2201234567", "provider": "wave"}'
```

## Check Request Format

The request body should match ModemPay's expected format. Current format:
```json
{
  "amount": 100.00,
  "currency": "GMD",
  "reference": "MODEMPAY123456789",
  "order_id": "123",
  "phone": "+2201234567",
  "provider": "wave",
  "callback_url": "http://localhost:5000/payments/modempay/webhook",
  "return_url": "/payments/success",
  "cancel_url": "/payments/failure"
}
```

## Next Steps

1. **Check ModemPay Documentation**: Look for exact authentication requirements
2. **Try different auth methods**: Test the options above one by one
3. **Contact ModemPay Support**: If none work, they can provide exact auth format
4. **Check API Response**: The error handler now shows detailed error messages

## Current Status

The code has been updated to:
- ✅ Use public key as Bearer token
- ✅ Include secret key in X-Secret-Key header
- ✅ Better error handling for 403 errors
- ✅ Detailed error messages
- ✅ Request/response logging for debugging

Try the payment again and check the error message for more details.


