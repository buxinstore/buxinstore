# ModemPay Webhook - Ngrok Setup

## Webhook URL Configuration

### Current Configuration
- **Webhook URL**: `https://carissa-prosodemic-gratifyingly.ngrok-free.dev/payments/modempay/webhook`
- **Environment Variable**: `MODEMPAY_CALLBACK_URL`
- **Route**: `/payments/modempay/webhook` (POST)

## Setup Steps

### 1. ✅ Environment Variable Updated
The `.env` file has been updated with the ngrok URL:
```bash
MODEMPAY_CALLBACK_URL=https://carissa-prosodemic-gratifyingly.ngrok-free.dev/payments/modempay/webhook
```

### 2. ✅ Webhook Route Configuration
The webhook endpoint is configured at:
- **Route**: `@payment_bp.route('/modempay/webhook', methods=['POST'])`
- **File**: `app/payments/routes.py`
- **Handler**: `modempay_webhook()`

### 3. ✅ Webhook Processing
The webhook handler:
- Receives POST requests from ModemPay
- Validates webhook signature (if `MODEMPAY_WEBHOOK_SECRET` is set)
- Processes payment status updates
- Updates payment records in database

## Ngrok Configuration

### Important Notes:
1. **Ngrok URL Changes**: Ngrok free URLs change on each restart. Update `MODEMPAY_CALLBACK_URL` in `.env` if the URL changes.

2. **Ngrok Free Tier**: The free tier may require:
   - Visiting the ngrok URL in a browser first to bypass the warning page
   - Handling ngrok's browser warning page

3. **Webhook Testing**: 
   - Test the webhook endpoint: `POST https://carissa-prosodemic-gratifyingly.ngrok-free.dev/payments/modempay/webhook`
   - Use ModemPay's webhook testing tool or manually send test requests

## ModemPay Dashboard Configuration

### Configure Webhook in ModemPay Dashboard:
1. Log into ModemPay dashboard
2. Go to Webhooks/Settings
3. Set webhook URL to: `https://carissa-prosodemic-gratifyingly.ngrok-free.dev/payments/modempay/webhook`
4. Set webhook secret to: `[REDACTED]`
5. Save configuration

## Testing the Webhook

### Manual Test (using curl):
```bash
curl -X POST https://carissa-prosodemic-gratifyingly.ngrok-free.dev/payments/modempay/webhook \
  -H "Content-Type: application/json" \
  -H "X-ModemPay-Signature: test_signature" \
  -d '{
    "transaction_id": "test_123",
    "status": "completed",
    "amount": 100.00,
    "reference": "TEST_REF"
  }'
```

### Expected Response:
```json
{
  "success": true,
  "message": "Webhook processed successfully"
}
```

## Security

### Webhook Signature Verification:
- Webhook signature is verified if `MODEMPAY_WEBHOOK_SECRET` is set
- Signature is checked from headers: `X-ModemPay-Signature`, `X-Signature`, or `Signature`
- Invalid signatures will result in a 400 error

## Troubleshooting

### Issue: Webhook not receiving requests
1. ✅ Verify ngrok is running and the URL is accessible
2. ✅ Check ModemPay dashboard webhook configuration
3. ✅ Verify the webhook URL in ModemPay matches the ngrok URL
4. ✅ Check ngrok logs for incoming requests

### Issue: 403 Forbidden from ngrok
- Ngrok free tier may show a warning page
- Visit the ngrok URL in a browser first to bypass the warning
- Consider upgrading to ngrok paid tier for production

### Issue: Webhook signature validation fails
1. ✅ Verify `MODEMPAY_WEBHOOK_SECRET` is set correctly in `.env`
2. ✅ Check ModemPay dashboard for the correct webhook secret
3. ✅ Verify the signature header name matches ModemPay's format

## Next Steps

1. ✅ Update `.env` with ngrok URL (DONE)
2. ⏳ Configure webhook URL in ModemPay dashboard
3. ⏳ Test webhook with a test payment
4. ⏳ Monitor webhook logs for incoming requests
5. ⏳ Verify payment status updates in database

## Production Deployment

When deploying to production:
1. Replace ngrok URL with your production domain
2. Update `MODEMPAY_CALLBACK_URL` to production URL
3. Ensure HTTPS is enabled
4. Verify webhook URL in ModemPay dashboard
5. Test webhook endpoints thoroughly

## Current Status

- ✅ Ngrok URL configured in `.env`
- ✅ Webhook route is set up
- ✅ Webhook handler is implemented
- ✅ Signature verification is configured
- ⏳ Waiting for ModemPay dashboard configuration
- ⏳ Waiting for test webhook requests

