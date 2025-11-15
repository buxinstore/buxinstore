"""
ModemPay API Diagnostic Tool
This script helps diagnose authentication issues with ModemPay API.
"""

import os
import requests
import json
from typing import Dict, Any, List


def test_modempay_auth():
    """
    Test different ModemPay authentication methods to identify which one works.
    """
    # Get API keys from environment
    public_key = os.environ.get('MODEMPAY_PUBLIC_KEY', '')
    secret_key = os.environ.get('MODEMPAY_SECRET_KEY', '')
    api_url = os.environ.get('MODEMPAY_API_URL', 'https://api.modempay.com/v1')
    
    if not public_key or not secret_key:
        print("ERROR: MODEMPAY_PUBLIC_KEY or MODEMPAY_SECRET_KEY not found in environment")
        return
    
    print(f"Testing ModemPay API: {api_url}")
    print(f"Public Key: {public_key[:20]}...{public_key[-10:]}")
    print(f"Secret Key: {secret_key[:20]}...{secret_key[-10:]}")
    print("\n" + "="*80 + "\n")
    
    # Test data
    test_data = {
        'amount': 100.00,
        'currency': 'GMD',
        'phone': '+2201234567',
        'provider': 'wave'
    }
    
    url = f"{api_url}/transactions"
    results = []
    
    # Test Method 1: Public key as Bearer
    print("Test 1: Public key as Bearer token")
    headers1 = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {public_key}',
    }
    result1 = test_request(url, test_data, headers1, "Public Key as Bearer")
    results.append(result1)
    print_result(result1)
    print("\n" + "-"*80 + "\n")
    
    # Test Method 2: Secret key as Bearer
    print("Test 2: Secret key as Bearer token")
    headers2 = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {secret_key}',
    }
    result2 = test_request(url, test_data, headers2, "Secret Key as Bearer")
    results.append(result2)
    print_result(result2)
    print("\n" + "-"*80 + "\n")
    
    # Test Method 3: Public key as Bearer + Secret key in header
    print("Test 3: Public key as Bearer + Secret key in X-Secret-Key header")
    headers3 = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {public_key}',
        'X-Secret-Key': secret_key,
    }
    result3 = test_request(url, test_data, headers3, "Public Bearer + X-Secret-Key")
    results.append(result3)
    print_result(result3)
    print("\n" + "-"*80 + "\n")
    
    # Test Method 4: Secret key as Bearer + Public key in header
    print("Test 4: Secret key as Bearer + Public key in X-API-Key header")
    headers4 = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {secret_key}',
        'X-API-Key': public_key,
    }
    result4 = test_request(url, test_data, headers4, "Secret Bearer + X-API-Key")
    results.append(result4)
    print_result(result4)
    print("\n" + "-"*80 + "\n")
    
    # Test Method 5: Custom headers only
    print("Test 5: Custom headers (X-Public-Key, X-Secret-Key)")
    headers5 = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-Public-Key': public_key,
        'X-Secret-Key': secret_key,
    }
    result5 = test_request(url, test_data, headers5, "Custom Headers")
    results.append(result5)
    print_result(result5)
    print("\n" + "-"*80 + "\n")
    
    # Test Method 6: Basic Authentication
    print("Test 6: Basic Authentication")
    import base64
    auth_string = base64.b64encode(f'{public_key}:{secret_key}'.encode()).decode()
    headers6 = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Basic {auth_string}',
    }
    result6 = test_request(url, test_data, headers6, "Basic Auth")
    results.append(result6)
    print_result(result6)
    print("\n" + "-"*80 + "\n")
    
    # Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    working_methods = [r for r in results if r.get('status_code') == 200]
    if working_methods:
        print(f"✅ SUCCESS: Found {len(working_methods)} working authentication method(s):")
        for method in working_methods:
            print(f"   - {method['method']}")
    else:
        print("❌ FAILED: No working authentication method found")
        print("\nPossible issues:")
        print("1. API keys are invalid or expired")
        print("2. API endpoint URL is incorrect")
        print("3. API requires IP whitelisting")
        print("4. API keys don't have required permissions")
        print("5. Request format is incorrect")
        print("\nNext steps:")
        print("1. Verify API keys in ModemPay dashboard")
        print("2. Check ModemPay API documentation for correct endpoint")
        print("3. Contact ModemPay support for assistance")
        print("4. Verify your IP is whitelisted (if required)")


def test_request(url: str, data: Dict[str, Any], headers: Dict[str, str], method_name: str) -> Dict[str, Any]:
    """Test a single authentication method."""
    try:
        response = requests.post(url, json=data, headers=headers, timeout=10)
        return {
            'method': method_name,
            'status_code': response.status_code,
            'success': response.status_code == 200,
            'headers': dict(response.headers),
            'response': response.text[:500] if response.text else '',
            'error': None
        }
    except requests.exceptions.RequestException as e:
        return {
            'method': method_name,
            'status_code': None,
            'success': False,
            'headers': {},
            'response': '',
            'error': str(e)
        }


def print_result(result: Dict[str, Any]):
    """Print test result."""
    print(f"Status Code: {result.get('status_code', 'N/A')}")
    if result.get('error'):
        print(f"Error: {result['error']}")
    else:
        print(f"Response: {result.get('response', '')[:200]}")
        if result.get('status_code') == 200:
            print("✅ SUCCESS!")
        elif result.get('status_code') == 403:
            print("❌ 403 Forbidden - Authentication failed")
        elif result.get('status_code') == 401:
            print("❌ 401 Unauthorized - Invalid credentials")
        else:
            print(f"⚠️  Status: {result.get('status_code')}")


if __name__ == '__main__':
    # Load environment variables from .env file
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        print("Warning: python-dotenv not installed. Using system environment variables only.")
    
    test_modempay_auth()

