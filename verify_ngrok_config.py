"""
Quick verification script to check if Ngrok and ModemPay configuration is loaded correctly.
Run this script to verify your .env file is configured properly.
"""

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

print("=" * 60)
print("Ngrok & ModemPay Configuration Verification")
print("=" * 60)
print()

# Check Ngrok URL
ngrok_url = os.getenv('NGROK_URL', '').strip()
if ngrok_url:
    print(f"[OK] NGROK_URL: {ngrok_url}")
    if not ngrok_url.startswith('http'):
        print(f"   [WARNING] URL should start with https://")
        print(f"   -> Will be auto-converted to: https://{ngrok_url}")
else:
    print("[ERROR] NGROK_URL: Not set")
    print("   -> Set NGROK_URL in your .env file")

print()

# Check ModemPay Public Key
modempay_public_key = os.getenv('MODEMPAY_PUBLIC_KEY', '').strip()
if modempay_public_key:
    # Mask the key for security
    masked_key = modempay_public_key[:10] + "..." + modempay_public_key[-10:] if len(modempay_public_key) > 20 else "***"
    print(f"[OK] MODEMPAY_PUBLIC_KEY: {masked_key}")
    if modempay_public_key.startswith('pk_live_'):
        print("   [OK] Using LIVE mode")
    elif modempay_public_key.startswith('pk_test_'):
        print("   [WARNING] Using TEST mode")
    else:
        print("   [WARNING] Key format not recognized")
else:
    print("[ERROR] MODEMPAY_PUBLIC_KEY: Not set")

print()

# Check ModemPay Secret Key
modempay_secret_key = os.getenv('MODEMPAY_SECRET_KEY', '').strip()
if modempay_secret_key:
    masked_key = modempay_secret_key[:10] + "..." + modempay_secret_key[-10:] if len(modempay_secret_key) > 20 else "***"
    print(f"[OK] MODEMPAY_SECRET_KEY: {masked_key}")
else:
    print("[ERROR] MODEMPAY_SECRET_KEY: Not set")

print()

# Verify callback URLs that will be used
if ngrok_url:
    # Ensure HTTPS
    base_url = ngrok_url if ngrok_url.startswith('http') else f"https://{ngrok_url}"
    base_url = base_url.rstrip('/')
    
    print("Callback URLs that will be used:")
    print(f"  Return URL: {base_url}/payments/success?order_id=<order_id>&reference=<reference>")
    print(f"  Cancel URL: {base_url}/payments/failure?order_id=<order_id>&reference=<reference>")
    print()
    print("[OK] Configuration looks good!")
    print()
    print("Next steps:")
    print("1. Make sure your Flask app is running")
    print("2. Ensure Ngrok is running and forwarding to localhost:5000")
    print("3. Test a live payment - callbacks will use the Ngrok URL above")
else:
    print("[WARNING] Please set NGROK_URL in your .env file to enable Ngrok callbacks")

print()
print("=" * 60)

