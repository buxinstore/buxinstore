# Environment Variables Setup Guide

## Quick Setup

1. **Copy the `.env` file** (if it doesn't exist, create it from `.env.example`)
2. **Replace placeholder values** with your actual ModemPay credentials
3. **Update the callback URL** with your actual domain

## ModemPay Environment Variables

### Required Variables

```env
MODEMPAY_API_URL=https://api.modempay.com/v1
MODEMPAY_PUBLIC_KEY=your_actual_public_key_here
MODEMPAY_SECRET_KEY=your_actual_secret_key_here
MODEMPAY_WEBHOOK_SECRET=your_actual_webhook_secret_here
MODEMPAY_CALLBACK_URL=https://yourdomain.com/payments/modempay/webhook
```

### How to Get ModemPay Credentials

1. **Sign up/Login** to your ModemPay merchant dashboard
2. **Navigate to API Settings** or Developer Settings
3. **Generate API Keys**:
   - Public Key (also called API Key or Client ID)
   - Secret Key (also called API Secret or Client Secret)
4. **Generate Webhook Secret**:
   - This is used to verify webhook signatures
   - Keep this secret secure
5. **Set Webhook URL** in ModemPay dashboard:
   - URL: `https://yourdomain.com/payments/modempay/webhook`
   - Make sure this matches your `MODEMPAY_CALLBACK_URL`

### Test Mode / Sandbox

If ModemPay provides a test/sandbox environment:

```env
# For testing (sandbox)
MODEMPAY_API_URL=https://api-sandbox.modempay.com/v1
MODEMPAY_PUBLIC_KEY=your_sandbox_public_key
MODEMPAY_SECRET_KEY=your_sandbox_secret_key
```

### Production Setup

For production:

1. **Use production API URL**: `https://api.modempay.com/v1`
2. **Use production API keys** from your ModemPay dashboard
3. **Set production webhook URL**: `https://yourdomain.com/payments/modempay/webhook`
4. **Ensure HTTPS** is enabled on your domain
5. **Keep secrets secure** - never commit `.env` to version control

## Security Best Practices

1. ✅ **Never commit `.env` to Git**
   - Add `.env` to your `.gitignore` file
   - Use `.env.example` for documentation

2. ✅ **Use strong secrets**
   - Generate random, secure webhook secrets
   - Use different secrets for development and production

3. ✅ **Rotate keys regularly**
   - Update API keys if compromised
   - Update webhook secrets periodically

4. ✅ **Use environment-specific files**
   - `.env.development` for local development
   - `.env.production` for production (set via server environment)

## Verifying Configuration

After setting up your environment variables, verify the configuration:

```python
# In Python shell or test script
from app.payments.config import PaymentConfig

# Check if ModemPay is configured
is_enabled = PaymentConfig.is_gateway_enabled('modempay')
print(f"ModemPay enabled: {is_enabled}")

# Get configuration
config = PaymentConfig.get_gateway_config('modempay')
print(f"API URL: {config.get('api_url')}")
print(f"Public Key set: {bool(config.get('public_key'))}")
print(f"Secret Key set: {bool(config.get('secret_key'))}")
```

## Troubleshooting

### Issue: "ModemPay is not properly configured"

**Solution**: Check that both `MODEMPAY_PUBLIC_KEY` and `MODEMPAY_SECRET_KEY` are set in your `.env` file.

### Issue: "Invalid webhook signature"

**Solution**: 
- Verify `MODEMPAY_WEBHOOK_SECRET` matches the secret in your ModemPay dashboard
- Ensure the webhook URL in ModemPay dashboard matches your `MODEMPAY_CALLBACK_URL`

### Issue: API requests failing

**Solution**:
- Verify `MODEMPAY_API_URL` is correct
- Check that your API keys are valid and not expired
- Ensure your IP is whitelisted (if required by ModemPay)

## Example .env File

See `.env.example` for a complete example with all payment gateway configurations.

