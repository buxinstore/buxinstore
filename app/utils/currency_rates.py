"""
Currency conversion rates.
Rates are relative to a base currency (GMD = 1.0).
All other currencies are converted based on their rate relative to GMD.
"""

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


def convert_price(amount, from_currency="GMD", to_currency="GMD"):
    """
    Convert price from one currency to another.
    
    Args:
        amount: The price amount to convert
        from_currency: Source currency code (default: GMD)
        to_currency: Target currency code (default: GMD)
    
    Returns:
        Converted price rounded to 2 decimal places
    """
    if from_currency == to_currency:
        return round(float(amount), 2)
    
    # Get rates (default to 1.0 if currency not found)
    from_rate = CURRENCY_RATES.get(from_currency, 1.0)
    to_rate = CURRENCY_RATES.get(to_currency, 1.0)
    
    # Convert: amount_in_base = amount / from_rate
    # Then: converted_amount = amount_in_base * to_rate
    # Simplified: amount * (to_rate / from_rate)
    if from_rate == 0:
        return round(float(amount), 2)
    
    converted = float(amount) * (to_rate / from_rate)
    return round(converted, 2)


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

