# ModemPay 403 Forbidden Error - Authentication Fix

## Issue
Getting `403 Client Error: Forbidden` when calling ModemPay API.

## Current Authentication Method

The gateway now uses:
```python
headers = {
    'Authorization': f'Bearer {secret_key}',
    'X-API-Key': public_key,
    'Content-Type': 'application/json',
    'Accept': 'application/json'
}
```

## Possible Solutions

### Option 1: Try Public Key as Bearer Token
If ModemPay uses public key as the Bearer token instead of secret key:

```python
headers['Authorization'] = f'Bearer {public_key}'
```

### Option 2: Use Secret Key Directly (No Bearer)
Some APIs don't use "Bearer" prefix:

```python
headers['Authorization'] = secret_key
```

### Option 3: Use Basic Authentication
If ModemPay uses Basic Auth:

```python
import base64
auth_string = base64.b64encode(f'{public_key}:{secret_key}'.encode()).decode()
headers['Authorization'] = f'Basic {auth_string}'
```

### Option 4: Custom Headers
If ModemPay uses custom header names:

```python
headers['X-Public-Key'] = public_key
headers['X-Secret-Key'] = secret_key
# Remove Authorization header
```

### Option 5: Check API Documentation
Verify the exact authentication method from ModemPay documentation:
- What header names are required?
- What format for Authorization header?
- Are both keys required or just one?

## Testing Different Methods

To test which method works, you can temporarily modify `_make_modempay_request()` in `app/payments/gateways/modempay.py`:

1. **Try Public Key as Bearer**:
   ```python
   headers['Authorization'] = f'Bearer {public_key}'
   # Remove or comment: headers['X-API-Key'] = public_key
   ```

2. **Try Secret Key Directly**:
   ```python
   headers['Authorization'] = secret_key  # No Bearer prefix
   ```

3. **Try Both Keys in Custom Headers**:
   ```python
   headers['X-Public-Key'] = public_key
   headers['X-Secret-Key'] = secret_key
   # Remove Authorization header
   ```

## Debugging Steps

1. **Check API Response**: The error handler now logs the full response
2. **Verify Keys**: Ensure keys are correct and not expired
3. **Check API URL**: Verify `MODEMPAY_API_URL` is correct
4. **Test with curl**: Try manual API call to see what works:
   ```bash
   curl -X POST https://api.modempay.com/v1/transactions \
     -H "Authorization: Bearer YOUR_SECRET_KEY" \
     -H "X-API-Key: YOUR_PUBLIC_KEY" \
     -H "Content-Type: application/json" \
     -d '{"amount": 100, "phone": "+2201234567", "provider": "wave"}'
   ```

## Current Implementation

The gateway now:
- Uses `Authorization: Bearer {secret_key}`
- Includes `X-API-Key: {public_key}`
- Provides detailed error messages for 403 errors
- Logs request/response for debugging

## Next Steps

1. Check ModemPay API documentation for exact authentication method
2. Try the alternative methods listed above
3. Check if API keys are valid and have correct permissions
4. Verify the API endpoint URL is correct
5. Contact ModemPay support if issue persists


