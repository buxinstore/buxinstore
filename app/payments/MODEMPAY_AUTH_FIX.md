# ModemPay Authentication Fix

## Issue
ModemPay API was returning 403 Forbidden error when making payment requests.

## Root Cause
The authentication method was incorrect. ModemPay requires:
- **Server-side API calls**: Use `Authorization: Bearer {secret_key}` (secret key only)
- **Client-side calls**: Use public key (pk_test_*)

We were sending both public key and secret key in custom headers, which ModemPay doesn't accept.

## Solution
Updated `app/payments/gateways/modempay.py` to use the correct authentication method:

```python
# Correct ModemPay authentication
headers['Authorization'] = f'Bearer {secret_key}'
```

**Key Points:**
1. Only use the **secret key** (sk_test_*) for server-side API calls
2. Do NOT include the public key in server-side requests
3. Public key is only for client-side JavaScript requests
4. Use `Authorization: Bearer {secret_key}` header format

## Testing
After this fix, ModemPay API requests should work correctly. The 403 error should be resolved.

## References
- ModemPay Documentation: https://docs.modempay.com/documentation/authentication
- ModemPay Error Handling: https://docs.modempay.com/documentation/errors

