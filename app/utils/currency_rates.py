"""
Currency conversion rates.
Rates represent: 1 GMD = X units of target currency
All prices are stored in GMD (Gambian Dalasi) as the base currency.
When converting FROM GMD TO target currency: multiply by the rate.
Example: 1 GMD = 7.75 XOF, so 100 GMD = 775 XOF
"""

import re
from decimal import Decimal, ROUND_HALF_UP

# Exchange rates: 1 GMD = X units of target currency
# These rates represent how many units of the target currency equal 1 GMD
CURRENCY_RATES = {
    "GMD": 1.0,      # Gambia (Dalasi) - Base currency
    "XOF": 7.75,     # Senegal, Mali, Burkina Faso, Côte d'Ivoire (West African CFA franc)
                     # 1 GMD = 7.75 CFA (approximate rate, update as needed)
    "SLL": 2800.0,   # Sierra Leone (Leone)
                     # 1 GMD = 2,800 SLL (approximate rate, update as needed)
    "UGX": 38.0,     # Uganda (Shilling)
                     # 1 GMD = 38 UGX (approximate rate, update as needed)
    "USD": 0.019,    # US Dollar (if needed) - 1 GMD = 0.019 USD (approximate)
    "EUR": 0.017,    # Euro (if needed) - 1 GMD = 0.017 EUR (approximate)
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
    
    All prices are stored in GMD (base currency).
    Rates represent: 1 GMD = X units of target currency
    
    Args:
        amount: The price amount to convert (can be string with symbol or numeric)
        from_currency: Source currency code (default: GMD)
        to_currency: Target currency code (default: GMD)
    
    Returns:
        Converted price rounded to 2 decimal places
    
    Examples:
        convert_price(13998, "GMD", "XOF") -> 108484.50 (13998 * 7.75)
        convert_price(13998, "GMD", "GMD") -> 13998.00 (no conversion)
        convert_price("D13998.00", "GMD", "XOF") -> 108484.50
    """
    # Parse the amount to extract numeric value and detect currency
    numeric_amount, detected_currency = parse_price(amount)
    
    # If currency was detected from the price string, use it as from_currency
    # This handles cases like "D13998.00" where D is detected as GMD
    if detected_currency:
        from_currency = detected_currency
    
    # If currencies are the same, return the numeric value (no conversion needed)
    if from_currency == to_currency:
        return round(float(numeric_amount), 2)
    
    # Get rates (default to 1.0 if currency not found)
    from_rate = CURRENCY_RATES.get(from_currency, 1.0)
    to_rate = CURRENCY_RATES.get(to_currency, 1.0)
    
    # Use Decimal for precise calculations
    amount_decimal = Decimal(str(numeric_amount))
    from_rate_decimal = Decimal(str(from_rate))
    to_rate_decimal = Decimal(str(to_rate))
    
    # Conversion logic:
    # Since all rates are relative to GMD (1 GMD = X units of target currency):
    # 1. Convert from source currency to GMD: amount_in_gmd = amount / from_rate
    # 2. Convert from GMD to target currency: converted = amount_in_gmd * to_rate
    # Simplified: converted = amount * (to_rate / from_rate)
    
    if from_rate == 0:
        return round(float(numeric_amount), 2)
    
    # Convert to base currency (GMD) first, then to target currency
    amount_in_gmd = amount_decimal / from_rate_decimal
    converted_amount = amount_in_gmd * to_rate_decimal
    
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


def test_currency_conversions():
    """
    Test function to verify currency conversions are working correctly.
    Tests conversion from GMD to various currencies.
    """
    test_price = 13998.00  # D13998.00 in GMD
    
    print("=" * 60)
    print("Currency Conversion Tests")
    print("=" * 60)
    print(f"\nBase price: {test_price} GMD (D{test_price:,.2f})")
    print("\nConversions:")
    print("-" * 60)
    
    # Test GMD to GMD (no conversion)
    result_gmd = convert_price(test_price, "GMD", "GMD")
    symbol_gmd = get_currency_symbol("GMD")
    print(f"GMD → GMD: {symbol_gmd}{result_gmd:,.2f} (should be {symbol_gmd}{test_price:,.2f})")
    assert abs(result_gmd - test_price) < 0.01, "GMD to GMD conversion failed"
    
    # Test GMD to XOF
    result_xof = convert_price(test_price, "GMD", "XOF")
    symbol_xof = get_currency_symbol("XOF")
    expected_xof = test_price * CURRENCY_RATES["XOF"]
    print(f"GMD → XOF: {symbol_xof}{result_xof:,.2f} (expected ~{symbol_xof}{expected_xof:,.2f})")
    
    # Test GMD to SLL
    result_sll = convert_price(test_price, "GMD", "SLL")
    symbol_sll = get_currency_symbol("SLL")
    expected_sll = test_price * CURRENCY_RATES["SLL"]
    print(f"GMD → SLL: {symbol_sll}{result_sll:,.2f} (expected ~{symbol_sll}{expected_sll:,.2f})")
    
    # Test GMD to UGX
    result_ugx = convert_price(test_price, "GMD", "UGX")
    symbol_ugx = get_currency_symbol("UGX")
    expected_ugx = test_price * CURRENCY_RATES["UGX"]
    print(f"GMD → UGX: {symbol_ugx}{result_ugx:,.2f} (expected ~{symbol_ugx}{expected_ugx:,.2f})")
    
    # Test parsing price with symbol
    print("\n" + "-" * 60)
    print("Price Parsing Tests:")
    print("-" * 60)
    test_cases = [
        "D13998.00",
        "D 13,998.00",
        "13998.00 D",
        13998.0,
        "CFA100.50",
    ]
    
    for test_case in test_cases:
        parsed_value, detected_currency = parse_price(test_case)
        print(f"Input: {test_case!r:20} → Value: {parsed_value:,.2f}, Currency: {detected_currency or 'None'}")
    
    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)

