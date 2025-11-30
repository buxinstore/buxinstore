"""
Currency API service for fetching real-time currency exchange rates.
Supports multiple API providers with fallback logic.
"""
import requests
from typing import Optional, Dict, Tuple
from decimal import Decimal
from datetime import datetime
import os
import logging

logger = logging.getLogger(__name__)

# API Provider configurations
EXCHANGERATE_API_BASE = "https://v6.exchangerate-api.com/v6"
CURRENCY_API_BASE = "https://api.currencyapi.com/v3"
OPENEXCHANGERATES_API_BASE = "https://openexchangerates.org/api"
EXCHANGERATES_DATA_API_BASE = "https://api.exchangeratesdata.io/v1"

# Base currency (GMD) might not be available in all APIs, so we convert via USD
BASE_CURRENCY = "GMD"
FALLBACK_BASE_CURRENCY = "USD"  # Most APIs support USD


def fetch_rate_exchangerate_api(from_currency: str, to_currency: str, api_key: Optional[str] = None) -> Optional[Decimal]:
    """
    Fetch exchange rate using ExchangeRate-API (exchangerate-api.com)
    
    Args:
        from_currency: Source currency code
        to_currency: Target currency code
        api_key: API key (if not provided, uses free tier)
    
    Returns:
        Exchange rate as Decimal, or None if failed
    """
    try:
        api_key = api_key or os.getenv('EXCHANGERATE_API_KEY', '')
        if not api_key:
            # Free tier doesn't require API key
            url = f"{EXCHANGERATE_API_BASE}/free/{from_currency}/{to_currency}"
        else:
            url = f"{EXCHANGERATE_API_BASE}/{api_key}/latest/{from_currency}"
        
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            
            if api_key:
                # Paid tier returns all rates
                if 'conversion_rates' in data:
                    rates = data['conversion_rates']
                    if to_currency in rates:
                        return Decimal(str(rates[to_currency]))
            else:
                # Free tier
                if 'conversion_rate' in data:
                    return Decimal(str(data['conversion_rate']))
                elif 'rate' in data:
                    return Decimal(str(data['rate']))
                    
    except Exception as e:
        logger.error(f"ExchangeRate-API error: {e}")
    
    return None


def fetch_rate_currencyapi(from_currency: str, to_currency: str, api_key: Optional[str] = None) -> Optional[Decimal]:
    """
    Fetch exchange rate using CurrencyAPI (currencyapi.com)
    
    Args:
        from_currency: Source currency code
        to_currency: Target currency code
        api_key: API key
    
    Returns:
        Exchange rate as Decimal, or None if failed
    """
    try:
        api_key = api_key or os.getenv('CURRENCY_API_KEY', '')
        if not api_key:
            return None
        
        url = f"{CURRENCY_API_BASE}/latest"
        params = {
            'apikey': api_key,
            'base_currency': from_currency,
            'currencies': to_currency
        }
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data and to_currency in data['data']:
                return Decimal(str(data['data'][to_currency]['value']))
                
    except Exception as e:
        logger.error(f"CurrencyAPI error: {e}")
    
    return None


def fetch_rate_openexchangerates(from_currency: str, to_currency: str, api_key: Optional[str] = None) -> Optional[Decimal]:
    """
    Fetch exchange rate using Open Exchange Rates (openexchangerates.org)
    
    Args:
        from_currency: Source currency code
        to_currency: Target currency code
        api_key: API key
    
    Returns:
        Exchange rate as Decimal, or None if failed
    """
    try:
        api_key = api_key or os.getenv('OPENEXCHANGERATES_API_KEY', '')
        if not api_key:
            return None
        
        # Open Exchange Rates uses USD as base, need to convert
        url = f"{OPENEXCHANGERATES_API_BASE}/latest.json"
        params = {'app_id': api_key}
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if 'rates' in data:
                rates = data['rates']
                from_rate = rates.get(from_currency, None)
                to_rate = rates.get(to_currency, None)
                
                if from_rate and to_rate:
                    # Convert via USD: from_currency -> USD -> to_currency
                    return Decimal(str(to_rate)) / Decimal(str(from_rate))
                    
    except Exception as e:
        logger.error(f"Open Exchange Rates API error: {e}")
    
    return None


def fetch_rate_exchangeratesdata(from_currency: str, to_currency: str, api_key: Optional[str] = None) -> Optional[Decimal]:
    """
    Fetch exchange rate using ExchangeRatesData API (exchangeratesdata.io)
    
    Args:
        from_currency: Source currency code
        to_currency: Target currency code
        api_key: API key (optional for free tier)
    
    Returns:
        Exchange rate as Decimal, or None if failed
    """
    try:
        api_key = api_key or os.getenv('EXCHANGERATES_DATA_API_KEY', '')
        
        if api_key:
            url = f"{EXCHANGERATES_DATA_API_BASE}/latest"
            params = {
                'access_key': api_key,
                'base': from_currency,
                'symbols': to_currency
            }
        else:
            # Free tier (limited)
            url = f"{EXCHANGERATES_DATA_API_BASE}/latest"
            params = {
                'base': from_currency,
                'symbols': to_currency
            }
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if 'success' in data and data['success'] and 'rates' in data:
                if to_currency in data['rates']:
                    return Decimal(str(data['rates'][to_currency]))
                    
    except Exception as e:
        logger.error(f"ExchangeRatesData API error: {e}")
    
    return None


def fetch_currency_rate(
    from_currency: str,
    to_currency: str,
    provider: Optional[str] = None,
    api_key: Optional[str] = None
) -> Tuple[Optional[Decimal], Optional[str], Optional[str]]:
    """
    Fetch currency exchange rate from external API.
    Tries multiple providers if provider is not specified.
    
    Args:
        from_currency: Source currency code
        to_currency: Target currency code
        provider: API provider name ('exchangerate', 'currencyapi', 'openexchangerates', 'exchangeratesdata')
        api_key: API key (provider-specific)
    
    Returns:
        Tuple of (rate as Decimal or None, provider_name or None, error_message or None)
    """
    # If same currency, return 1.0
    if from_currency.upper() == to_currency.upper():
        return Decimal('1.0'), None, None
    
    # Try specific provider first
    if provider:
        provider = provider.lower()
        
        if provider == 'exchangerate':
            rate = fetch_rate_exchangerate_api(from_currency, to_currency, api_key)
            return rate, 'exchangerate', None if rate else "Failed to fetch rate"
        elif provider == 'currencyapi':
            rate = fetch_rate_currencyapi(from_currency, to_currency, api_key)
            return rate, 'currencyapi', None if rate else "Failed to fetch rate"
        elif provider == 'openexchangerates':
            rate = fetch_rate_openexchangerates(from_currency, to_currency, api_key)
            return rate, 'openexchangerates', None if rate else "Failed to fetch rate"
        elif provider == 'exchangeratesdata':
            rate = fetch_rate_exchangeratesdata(from_currency, to_currency, api_key)
            return rate, 'exchangeratesdata', None if rate else "Failed to fetch rate"
    
    # Try all providers in order
    providers = [
        ('exchangerate', fetch_rate_exchangerate_api),
        ('currencyapi', fetch_rate_currencyapi),
        ('openexchangerates', fetch_rate_openexchangerates),
        ('exchangeratesdata', fetch_rate_exchangeratesdata),
    ]
    
    for provider_name, fetch_func in providers:
        try:
            rate = fetch_func(from_currency, to_currency, api_key)
            if rate and rate > 0:
                logger.info(f"Successfully fetched rate {from_currency}/{to_currency} from {provider_name}: {rate}")
                return rate, provider_name, None
        except Exception as e:
            logger.warning(f"Provider {provider_name} failed: {e}")
            continue
    
    error_msg = f"All API providers failed to fetch rate for {from_currency}/{to_currency}"
    logger.error(error_msg)
    return None, None, error_msg


def sync_currency_rate_from_api(
    from_currency: str,
    to_currency: str,
    provider: Optional[str] = None,
    api_key: Optional[str] = None
) -> Tuple[bool, Optional[str], Optional[Decimal]]:
    """
    Sync a currency rate from API and return result.
    
    Args:
        from_currency: Source currency code
        to_currency: Target currency code
        provider: API provider name
        api_key: API key
    
    Returns:
        Tuple of (success: bool, error_message or None, rate or None)
    """
    try:
        rate, provider_used, error = fetch_currency_rate(from_currency, to_currency, provider, api_key)
        
        if rate and rate > 0:
            return True, None, rate
        else:
            return False, error or "Failed to fetch rate", None
            
    except Exception as e:
        error_msg = f"Error syncing rate: {str(e)}"
        logger.error(error_msg)
        return False, error_msg, None

