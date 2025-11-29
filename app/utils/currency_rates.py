"""
Currency conversion rates.
Rates are relative to a base currency (GMD = 1.0).
All other currencies are converted based on their rate relative to GMD.
"""

import re
from decimal import Decimal, ROUND_HALF_UP

CURRENCY_RATES = {
    "XOF": 0.0095,   # Senegal, Mali, Burkina Faso, Côte d'Ivoire (West African CFA franc)
    "GMD": 1.0,      # Gambia (Dalasi) - Base currency
    "SLL": 0.00036,  # Sierra Leone (Leone)
    "UGX": 0.00026,  # Uganda (Shilling)
    "USD": 0.016,    # US Dollar (if needed)
    "EUR": 0.015,    # Euro (if needed)
}

CURRENCY_SYMBOLS = {
    "XOF": "CFA",
    "GMD": "D",
    "SLL": "Le",
    "UGX": "USh",
    "USD": "$",
    "EUR": "€",
}

# Map currency symbols to currency codes for parsing
SYMBOL_TO_CURRENCY = {
    "D": "GMD",
    "CFA": "XOF",
    "Le": "SLL",
    "USh": "UGX",
    "$": "USD",
    "€": "EUR",
    "EUR": "EUR",
    "USD": "USD",
}


def parse_price(price):
    """
    Parse a price value that may contain currency symbols.
    Strips currency symbols and returns the numeric value.
    
    Args:
        price: Price value (can be string like "D13998.00", "CFA100", or float like 13998.0)
    
    Returns:
        Tuple of (numeric_value, detected_currency_code)
        If currency symbol is detected, returns the currency code, otherwise returns None
    """
    if price is None:
        return 0.0, None
    
    # If it's already a number, return as-is
    if isinstance(price, (int, float)):
        return float(price), None
    
    # Convert to string for processing
    price_str = str(price).strip()
    
    # Try to detect currency symbol
    detected_currency = None
    numeric_value = price_str
    
    # Check for currency symbols (order matters - check longer symbols first)
    for symbol, currency_code in sorted(SYMBOL_TO_CURRENCY.items(), key=lambda x: -len(x[0])):
        # Check if symbol appears at the start or end
        if price_str.startswith(symbol):
            detected_currency = currency_code
            numeric_value = price_str[len(symbol):].strip()
            break
        elif price_str.endswith(symbol):
            detected_currency = currency_code
            numeric_value = price_str[:-len(symbol)].strip()
            break
    
    # Remove any remaining non-numeric characters except decimal point and minus sign
    # This handles cases like "D 13,998.00" or "13,998.00 D"
    numeric_value = re.sub(r'[^\d.-]', '', numeric_value)
    
    # Remove thousand separators (commas)
    numeric_value = numeric_value.replace(',', '')
    
    try:
        return float(numeric_value), detected_currency
    except (ValueError, TypeError):
        # If parsing fails, try to extract just numbers
        numbers = re.findall(r'\d+\.?\d*', price_str)
        if numbers:
            return float(numbers[0]), detected_currency
        return 0.0, detected_currency


def convert_price(amount, from_currency="GMD", to_currency="GMD"):
    """
    Convert price from one currency to another.
    Automatically parses prices that may contain currency symbols.
    
    Args:
        amount: The price amount to convert (can be string with symbol or numeric)
        from_currency: Source currency code (default: GMD)
        to_currency: Target currency code (default: GMD)
    
    Returns:
        Converted price rounded to 2 decimal places
    """
    # Parse the amount to extract numeric value and detect currency
    numeric_amount, detected_currency = parse_price(amount)
    
    # If currency was detected from the price string, use it as from_currency
    if detected_currency:
        from_currency = detected_currency
    
    # If currencies are the same, return the numeric value
    if from_currency == to_currency:
        return round(float(numeric_amount), 2)
    
    # Get rates (default to 1.0 if currency not found)
    from_rate = CURRENCY_RATES.get(from_currency, 1.0)
    to_rate = CURRENCY_RATES.get(to_currency, 1.0)
    
    # Convert: amount_in_base = amount / from_rate
    # Then: converted_amount = amount_in_base * to_rate
    # Simplified: amount * (to_rate / from_rate)
    if from_rate == 0:
        return round(float(numeric_amount), 2)
    
    # Use Decimal for precise calculations
    amount_decimal = Decimal(str(numeric_amount))
    from_rate_decimal = Decimal(str(from_rate))
    to_rate_decimal = Decimal(str(to_rate))
    
    # Convert to base currency (GMD) first, then to target currency
    amount_in_base = amount_decimal / from_rate_decimal
    converted_amount = amount_in_base * to_rate_decimal
    
    # Round to 2 decimal places
    result = float(converted_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    return round(result, 2)


def get_currency_symbol(currency_code):
    """Get the currency symbol for a given currency code."""
    return CURRENCY_SYMBOLS.get(currency_code, currency_code)


def format_price(amount, currency_code):
    """
    Format price with currency symbol.
    
    Args:
        amount: The price amount
        currency_code: Currency code (e.g., 'XOF', 'GMD')
    
    Returns:
        Formatted string like "1,234.56 CFA" or "1,234.56 D"
    """
    symbol = get_currency_symbol(currency_code)
    # Format with thousand separators
    formatted_amount = f"{amount:,.2f}"
    return f"{formatted_amount} {symbol}"

