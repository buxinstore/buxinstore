# 🔄 WhatsApp Access Token Refresh - Quick Guide

## ⚠️ Your Token Has Expired

Your WhatsApp access token expired on **Thursday, 13-Nov-25 05:00:00 PST**.

## 🚀 Quick Fix (3 Steps)

### Step 1: Get a New Token from Meta Developer Console

1. **Go to Meta Developer Console**: https://developers.facebook.com/apps
2. **Select your WhatsApp Business App**
3. **Navigate to**: WhatsApp → API Setup
4. **Find "Temporary access token"** section
5. **Click "Copy"** to copy the new token

> ⚠️ **Important**: Temporary tokens expire in 1-24 hours. For production, see "Permanent Token" section below.

### Step 2: Update Your .env File

Open your `.env` file and update this line:

```env
WHATSAPP_ACCESS_TOKEN=YOUR_NEW_TOKEN_HERE
```

Replace `YOUR_NEW_TOKEN_HERE` with the token you copied.

### Step 3: Restart Your Flask App

1. Stop your Flask application (Ctrl+C in terminal)
2. Restart it:
   ```bash
   python run.py
   ```

## 🔒 For Production: Get a Permanent Token (Recommended)

Temporary tokens expire quickly. For production, create a **System User token** that doesn't expire:

### Steps:

1. **Go to Business Settings**: https://business.facebook.com/settings/system-users
2. **Click "Add"** to create a new system user (or use existing)
3. **Assign to your WhatsApp Business App**:
   - Click on the system user
   - Click "Assign Assets"
   - Select your WhatsApp Business App
   - Grant permissions:
     - ✅ `whatsapp_business_messaging`
     - ✅ `whatsapp_business_management`
4. **Generate Token**:
   - Click "Generate New Token"
   - Select your WhatsApp Business App
   - Grant the permissions above
   - Click "Generate Token"
   - **⚠️ COPY THE TOKEN IMMEDIATELY** (you can't see it again!)
5. **Update .env file** with the new permanent token
6. **Restart Flask app**

## ✅ Verify It Works

After updating the token:

1. Go to **Admin Panel → WhatsApp Messaging**
2. Click **"Send Test"** button
3. You should see a success message

## 📋 Current Configuration

Your current WhatsApp settings:
- **Phone Number ID**: `679601781911539`
- **Business Account ID**: `576348912079189`
- **Test Number**: `2200000000`
- **Business Name**: `buxinstore`

## 🆘 Still Having Issues?

1. **Verify token format**: Should be a long string starting with letters/numbers
2. **Check for extra spaces**: Make sure there are no spaces before/after the token in .env
3. **Verify Phone Number ID**: Should match `679601781911539`
4. **Check app permissions**: Ensure your app has WhatsApp Business API access
5. **Review Meta Developer Console**: Check for any warnings or restrictions

## 📚 Additional Resources

- **Meta WhatsApp API Docs**: https://developers.facebook.com/docs/whatsapp/cloud-api
- **Token Management**: https://developers.facebook.com/docs/whatsapp/cloud-api/get-started#get-token
- **System User Tokens**: https://developers.facebook.com/docs/marketing-api/system-users

---

**Last Updated**: November 13, 2025

