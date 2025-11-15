# WhatsApp Access Token - Refresh Guide

## Issue: Token Expired (401 Error)

Your WhatsApp access token has expired and needs to be refreshed.

### Error Details
- **Error Code**: 190
- **Error Type**: OAuthException
- **Message**: "Session has expired on Monday, 28-Jul-25 23:00:00 PDT"

## How to Get a New Access Token

### Step 1: Access Meta Developer Console
1. Go to https://developers.facebook.com/apps
2. Log in with your Facebook/Meta account

### Step 2: Select Your WhatsApp App
1. Find and click on your WhatsApp Business App
2. If you don't have one, create a new app and set up WhatsApp Business API

### Step 3: Get Temporary Token (Quick Test)
1. Navigate to **WhatsApp → API Setup** in the left sidebar
2. Scroll down to find **"Temporary access token"**
3. Click **"Copy"** to copy the token
4. **Note**: Temporary tokens expire in 1-24 hours (varies by account)

### Step 4: Get Permanent Token (Production - Recommended)
For production use, create a System User token that doesn't expire:

1. Go to **Business Settings → System Users**
2. Click **"Add"** to create a new system user (or use existing)
3. Assign the system user to your WhatsApp Business App
4. Generate a token for the system user:
   - Click **"Generate New Token"**
   - Select your WhatsApp Business App
   - Grant these permissions:
     - `whatsapp_business_messaging`
     - `whatsapp_business_management`
   - Click **"Generate Token"**
   - Copy the token immediately (you can't see it again)

### Step 5: Update Your .env File
1. Open your `.env` file
2. Find the line: `WHATSAPP_ACCESS_TOKEN=...`
3. Replace the old token with your new token:
   ```env
   WHATSAPP_ACCESS_TOKEN=YOUR_NEW_TOKEN_HERE
   ```
4. Save the file

### Step 6: Restart Flask Application
1. Stop your Flask application (Ctrl+C)
2. Restart it to load the new token:
   ```bash
   python run.py
   ```

### Step 7: Test the Integration
1. Go to Admin Panel → WhatsApp Messaging
2. Click "Send Test" to verify the new token works

## Important Notes

### Token Expiration
- **Temporary Tokens**: Expire after 1-24 hours (for testing only)
- **System User Tokens**: Don't expire but can be revoked
- **Page Access Tokens**: Can be long-lived (60+ days) but require page permission

### Best Practices
1. **For Development**: Use temporary tokens from API Setup page
2. **For Production**: Use System User tokens (never expire)
3. **Monitor Expiration**: Set reminders or use token expiration webhooks
4. **Secure Storage**: Never commit tokens to public repositories

## Troubleshooting

### Token Still Not Working
1. Verify the token is correctly pasted (no extra spaces)
2. Check that your WhatsApp Business App is approved
3. Verify your Phone Number ID is correct
4. Ensure your app has the correct permissions

### Need Help?
- Meta WhatsApp Business API Docs: https://developers.facebook.com/docs/whatsapp/cloud-api
- Meta Developer Support: https://developers.facebook.com/support/

