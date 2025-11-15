# ModemPay 403 Forbidden Error - Comprehensive Troubleshooting Guide

## Current Status
- **Error**: 403 Forbidden (HTML response from nginx)
- **Authentication Method**: Currently using `Authorization: Bearer {secret_key}`
- **Endpoint**: `https://api.modempay.com/v1/transactions`

## Issue Analysis

The 403 error with HTML response from nginx indicates that the request is being blocked at the web server level, before it reaches the ModemPay API application. This suggests one of the following:

1. **Invalid API Keys**: The keys might be expired, revoked, or incorrect
2. **Wrong Endpoint**: The API endpoint URL might be incorrect
3. **IP Whitelisting**: Your IP address might not be whitelisted
4. **Authentication Method**: The authentication method might be incorrect
5. **API Account Issues**: Your API account might be suspended or inactive

## Authentication Methods to Try

The code now supports multiple authentication methods via the `MODEMPAY_AUTH_METHOD` environment variable:

### Method 1: Public Key as Bearer (Default)
```bash
MODEMPAY_AUTH_METHOD=public_bearer
```
- Uses: `Authorization: Bearer {public_key}`
- Also includes: `X-Secret-Key: {secret_key}`

### Method 2: Secret Key as Bearer
```bash
MODEMPAY_AUTH_METHOD=secret_bearer
```
- Uses: `Authorization: Bearer {secret_key}`

### Method 3: Custom Headers
```bash
MODEMPAY_AUTH_METHOD=both_headers
```
- Uses: `X-Public-Key: {public_key}`
- Uses: `X-Secret-Key: {secret_key}`

### Method 4: Basic Authentication
```bash
MODEMPAY_AUTH_METHOD=basic_auth
```
- Uses: `Authorization: Basic {base64(public_key:secret_key)}`

## Diagnostic Steps

### Step 1: Verify API Keys
1. Log into ModemPay dashboard
2. Verify your API keys are active
3. Check if keys have expired
4. Verify keys have required permissions
5. Confirm you're using test keys for test environment

### Step 2: Test Authentication Methods
Run the diagnostic tool:
```bash
python app/payments/gateways/modempay_diagnostic.py
```

This will test all authentication methods and show which one works.

### Step 3: Check API Endpoint
Verify the correct API endpoint:
- Test: `https://api.modempay.com/v1` or `https://api-sandbox.modempay.com/v1`
- Production: `https://api.modempay.com/v1`

### Step 4: Verify Request Format
Check if the request payload format is correct:
```json
{
  "amount": 100.00,
  "currency": "GMD",
  "reference": "REF123",
  "order_id": "123",
  "phone": "+2201234567",
  "provider": "wave",
  "callback_url": "http://localhost:5000/payments/modempay/webhook",
  "return_url": "/payments/success",
  "cancel_url": "/payments/failure"
}
```

### Step 5: Check IP Whitelisting
1. Contact ModemPay support to verify if IP whitelisting is required
2. If required, provide your server's IP address for whitelisting

### Step 6: Contact ModemPay Support
If all else fails, contact ModemPay support with:
- Your API keys (first and last few characters)
- Error message and status code
- Request headers (masked)
- Request payload
- Your server IP address

## Quick Fix: Try Different Authentication Method

Add to your `.env` file:
```bash
# Try public key as Bearer (default)
MODEMPAY_AUTH_METHOD=public_bearer

# Or try secret key as Bearer
MODEMPAY_AUTH_METHOD=secret_bearer

# Or try custom headers
MODEMPAY_AUTH_METHOD=both_headers

# Or try Basic Auth
MODEMPAY_AUTH_METHOD=basic_auth
```

Then restart your Flask application.

## Testing with Test Endpoint

You can also use the test endpoint to try different methods:
```bash
POST http://localhost:5000/payments/modempay/test-auth
```

This will test all authentication methods and return which one works.

## Next Steps

1. **Run Diagnostic Tool**: `python app/payments/gateways/modempay_diagnostic.py`
2. **Try Different Auth Methods**: Set `MODEMPAY_AUTH_METHOD` in `.env`
3. **Verify API Keys**: Check ModemPay dashboard
4. **Contact Support**: If issue persists, contact ModemPay support

## References
- ModemPay Documentation: https://docs.modempay.com/documentation/authentication
- ModemPay Error Handling: https://docs.modempay.com/documentation/errors

