"""
Currency conversion rates.
Rates represent: 1 GMD = X units of target currency
All prices are stored in GMD (Gambian Dalasi) as the base currency.
When converting FROM GMD TO target currency: multiply by the rate.
Example: 1 GMD = 7.75 XOF, so 100 GMD = 775 XOF
"""

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Tuple, Optional

# Exchange rates: 1 GMD = X units of target currency
# These rates represent how many units of the target currency equal 1 GMD
# Rates are approximate and should be updated regularly
CURRENCY_RATES = {
    # Base currency
    "GMD": 1.0,      # Gambia (Dalasi) - Base currency
    
    # West African currencies
    "XOF": 7.75,     # West African CFA franc (Senegal, Mali, Burkina Faso, Côte d'Ivoire, etc.)
    "XAF": 7.75,     # Central African CFA franc (Cameroon, Chad, etc.) - similar to XOF
    "NGN": 28.5,     # Nigerian Naira
    "GHS": 0.28,     # Ghanaian Cedi
    "SLL": 2800.0,   # Sierra Leone Leone
    "UGX": 38.0,     # Ugandan Shilling
    "KES": 2.5,      # Kenyan Shilling
    "TZS": 45.0,     # Tanzanian Shilling
    "ETB": 1.1,      # Ethiopian Birr
    "ZAR": 0.35,     # South African Rand
    "EGP": 0.6,      # Egyptian Pound
    "MAD": 0.19,     # Moroccan Dirham
    
    # Major world currencies
    "USD": 0.019,    # US Dollar
    "EUR": 0.017,    # Euro
    "GBP": 0.015,    # British Pound
    "JPY": 2.8,      # Japanese Yen
    "CNY": 0.14,     # Chinese Yuan
    "INR": 1.58,     # Indian Rupee
    "AUD": 0.029,    # Australian Dollar
    "CAD": 0.026,    # Canadian Dollar
    "CHF": 0.017,    # Swiss Franc
    
    # Other African currencies
    "AOA": 15.0,     # Angolan Kwanza
    "BWP": 0.26,     # Botswana Pula
    "DZD": 2.6,      # Algerian Dinar
    "MZN": 1.2,      # Mozambican Metical
    "ZMW": 0.45,     # Zambian Kwacha
    "MWK": 32.0,     # Malawian Kwacha
    "RWF": 22.0,     # Rwandan Franc
    "BIF": 54.0,     # Burundian Franc
    "GNF": 165.0,    # Guinean Franc
    "LRD": 0.019,    # Liberian Dollar (uses USD)
    "MGA": 85.0,     # Malagasy Ariary
    "MUR": 0.85,     # Mauritian Rupee
    "SCR": 0.26,     # Seychellois Rupee
    "SZL": 0.35,     # Swazi Lilangeni
    "LSL": 0.35,     # Lesotho Loti
    "NAD": 0.35,     # Namibian Dollar
    "SDG": 0.11,     # Sudanese Pound
    "SSP": 0.11,     # South Sudanese Pound
    "SOS": 11.0,     # Somali Shilling
    "TND": 0.059,    # Tunisian Dinar
    "ZWL": 0.019,    # Zimbabwean Dollar
    
    # Middle East currencies
    "AED": 0.07,     # UAE Dirham
    "SAR": 0.071,    # Saudi Riyal
    "QAR": 0.069,    # Qatari Riyal
    "KWD": 0.0058,   # Kuwaiti Dinar
    "BHD": 0.0071,   # Bahraini Dinar
    "OMR": 0.0073,   # Omani Rial
    "JOD": 0.013,    # Jordanian Dinar
    "ILS": 0.07,     # Israeli Shekel
    "LBP": 285.0,    # Lebanese Pound
    "IQD": 25.0,     # Iraqi Dinar
    "IRR": 800.0,    # Iranian Rial
    "YER": 4.75,     # Yemeni Rial
    "SYP": 95.0,     # Syrian Pound
    
    # Asian currencies
    "PKR": 5.3,      # Pakistani Rupee
    "BDT": 2.1,      # Bangladeshi Taka
    "LKR": 6.0,      # Sri Lankan Rupee
    "NPR": 2.5,      # Nepalese Rupee
    "AFN": 1.4,      # Afghan Afghani
    "MMK": 40.0,     # Myanmar Kyat
    "KHR": 78.0,     # Cambodian Riel
    "LAK": 330.0,    # Lao Kip
    "VND": 470.0,    # Vietnamese Dong
    "THB": 0.68,     # Thai Baht
    "MYR": 0.09,     # Malaysian Ringgit
    "SGD": 0.026,    # Singapore Dollar
    "IDR": 300.0,    # Indonesian Rupiah
    "PHP": 1.1,      # Philippine Peso
    "KRW": 25.0,     # South Korean Won
    "TWD": 0.6,      # Taiwan Dollar
    "HKD": 0.15,     # Hong Kong Dollar
    "MOP": 0.15,     # Macanese Pataca
    "MNT": 66.0,     # Mongolian Tugrik
    "KZT": 8.5,      # Kazakhstani Tenge
    "UZS": 230.0,    # Uzbekistani Som
    "KGS": 1.7,      # Kyrgyzstani Som
    "TJS": 0.21,     # Tajikistani Somoni
    "TMT": 0.067,    # Turkmenistani Manat
    "AMD": 7.5,      # Armenian Dram
    "AZN": 0.032,    # Azerbaijani Manat
    "GEL": 0.052,    # Georgian Lari
    "RUB": 1.75,     # Russian Ruble
    "BYN": 0.062,    # Belarusian Ruble
    "MDL": 0.34,     # Moldovan Leu
    "UAH": 0.7,      # Ukrainian Hryvnia
    
    # European currencies (non-EUR)
    "GBP": 0.015,    # British Pound
    "CHF": 0.017,    # Swiss Franc
    "NOK": 0.20,     # Norwegian Krone
    "SEK": 0.20,     # Swedish Krona
    "DKK": 0.13,     # Danish Krone
    "PLN": 0.076,    # Polish Zloty
    "CZK": 0.44,     # Czech Koruna
    "HUF": 7.0,      # Hungarian Forint
    "RON": 0.084,    # Romanian Leu
    "BGN": 0.033,    # Bulgarian Lev
    "HRK": 0.13,     # Croatian Kuna
    "RSD": 2.0,      # Serbian Dinar
    "BAM": 0.033,    # Bosnia and Herzegovina Convertible Mark
    "MKD": 1.1,      # Macedonian Denar
    "ALL": 1.8,      # Albanian Lek
    "ISK": 2.6,      # Icelandic Krona
    
    # Americas currencies
    "BRL": 0.095,    # Brazilian Real
    "MXN": 0.32,     # Mexican Peso
    "ARS": 17.0,     # Argentine Peso
    "CLP": 18.0,     # Chilean Peso
    "COP": 75.0,     # Colombian Peso
    "PEN": 0.071,    # Peruvian Sol
    "VES": 0.68,     # Venezuelan Bolívar
    "UYU": 0.75,     # Uruguayan Peso
    "PYG": 140.0,    # Paraguayan Guaraní
    "BOB": 0.13,     # Bolivian Boliviano
    "GTQ": 0.15,     # Guatemalan Quetzal
    "HNL": 0.47,     # Honduran Lempira
    "NIO": 0.70,     # Nicaraguan Córdoba
    "CRC": 9.8,      # Costa Rican Colón
    "PAB": 0.019,    # Panamanian Balboa
    "DOP": 1.1,      # Dominican Peso
    "HTG": 2.5,      # Haitian Gourde
    "JMD": 3.0,      # Jamaican Dollar
    "BBD": 0.038,    # Barbadian Dollar
    "BZD": 0.038,    # Belize Dollar
    "TTD": 0.13,     # Trinidad and Tobago Dollar
    "XCD": 0.051,    # East Caribbean Dollar
    "GYD": 4.0,      # Guyanese Dollar
    "SRD": 0.70,     # Surinamese Dollar
    
    # Oceania currencies
    "NZD": 0.031,    # New Zealand Dollar
    "FJD": 0.043,    # Fijian Dollar
    "PGK": 0.068,    # Papua New Guinean Kina
    "SBD": 0.16,     # Solomon Islands Dollar
    "VUV": 2.2,      # Vanuatu Vatu
    "WST": 0.052,    # Samoan Tala
    "TOP": 0.044,    # Tongan Paʻanga
}

CURRENCY_SYMBOLS = {
    # Base
    "GMD": "D",
    
    # West African
    "XOF": "CFA",
    "XAF": "FCFA",
    "NGN": "₦",
    "GHS": "₵",
    "SLL": "Le",
    "UGX": "USh",
    "KES": "Sh",
    "TZS": "Sh",
    "ETB": "Br",
    "ZAR": "R",
    "EGP": "£",
    "MAD": "د.م.",
    
    # Major world
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "CNY": "¥",
    "INR": "₹",
    "AUD": "$",
    "CAD": "$",
    "CHF": "Fr",
    
    # Other currencies (add more as needed)
    "AOA": "Kz",
    "BWP": "P",
    "DZD": "د.ج",
    "MZN": "MT",
    "ZMW": "ZK",
    "MWK": "MK",
    "RWF": "Fr",
    "BIF": "Fr",
    "GNF": "Fr",
    "LRD": "$",
    "MGA": "Ar",
    "MUR": "₨",
    "SCR": "₨",
    "SZL": "L",
    "LSL": "L",
    "NAD": "$",
    "SDG": "ج.س.",
    "SSP": "£",
    "SOS": "Sh",
    "TND": "د.ت",
    "ZWL": "$",
    "AED": "د.إ",
    "SAR": "ر.س",
    "QAR": "ر.ق",
    "KWD": "د.ك",
    "BHD": ".د.ب",
    "OMR": "ر.ع.",
    "JOD": "د.ا",
    "ILS": "₪",
    "LBP": "ل.ل",
    "IQD": "ع.د",
    "IRR": "﷼",
    "YER": "﷼",
    "SYP": "£",
    "PKR": "₨",
    "BDT": "৳",
    "LKR": "₨",
    "NPR": "₨",
    "AFN": "؋",
    "MMK": "K",
    "KHR": "៛",
    "LAK": "₭",
    "VND": "₫",
    "THB": "฿",
    "MYR": "RM",
    "SGD": "$",
    "IDR": "Rp",
    "PHP": "₱",
    "KRW": "₩",
    "TWD": "NT$",
    "HKD": "$",
    "MOP": "P",
    "MNT": "₮",
    "KZT": "₸",
    "UZS": "so'm",
    "KGS": "с",
    "TJS": "ЅМ",
    "TMT": "m",
    "AMD": "֏",
    "AZN": "₼",
    "GEL": "₾",
    "RUB": "₽",
    "BYN": "Br",
    "MDL": "L",
    "UAH": "₴",
    "NOK": "kr",
    "SEK": "kr",
    "DKK": "kr",
    "PLN": "zł",
    "CZK": "Kč",
    "HUF": "Ft",
    "RON": "lei",
    "BGN": "лв",
    "HRK": "Kn",
    "RSD": "дин",
    "BAM": "КМ",
    "MKD": "ден",
    "ALL": "L",
    "ISK": "kr",
    "BRL": "R$",
    "MXN": "$",
    "ARS": "$",
    "CLP": "$",
    "COP": "$",
    "PEN": "S/",
    "VES": "Bs.S",
    "UYU": "$",
    "PYG": "₲",
    "BOB": "Bs.",
    "GTQ": "Q",
    "HNL": "L",
    "NIO": "C$",
    "CRC": "₡",
    "PAB": "B/.",
    "DOP": "$",
    "HTG": "G",
    "JMD": "$",
    "BBD": "$",
    "BZD": "$",
    "TTD": "$",
    "XCD": "$",
    "GYD": "$",
    "SRD": "$",
    "NZD": "$",
    "FJD": "$",
    "PGK": "K",
    "SBD": "$",
    "VUV": "Vt",
    "WST": "T",
    "TOP": "T$",
}

# Map currency symbols to currency codes for parsing
# Note: Some symbols are ambiguous (e.g., $, £, €) - we prioritize common ones
SYMBOL_TO_CURRENCY = {
    "D": "GMD",
    "CFA": "XOF",
    "FCFA": "XAF",
    "Le": "SLL",
    "USh": "UGX",
    "₦": "NGN",
    "₵": "GHS",
    "Sh": "KES",  # Could be KES, TZS, SOS - default to KES
    "Br": "ETB",  # Could be ETB, BYN - default to ETB
    "R": "ZAR",
    "€": "EUR",
    "$": "USD",  # Most common, but could be many others
    "£": "GBP",  # Most common, but could be EGP, SSP, SYP
    "¥": "JPY",  # Could be JPY or CNY - default to JPY
    "₹": "INR",
    "Fr": "CHF",  # Could be CHF, RWF, BIF, GNF, DJF, KMF - default to CHF
    "₨": "PKR",  # Could be PKR, LKR, NPR, MUR, SCR - default to PKR
    "₪": "ILS",
    "₩": "KRW",  # Could be KRW or KPW - default to KRW
    "₱": "PHP",
    "฿": "THB",
    "₫": "VND",
    "₽": "RUB",
    "₴": "UAH",
    "zł": "PLN",
    "Kč": "CZK",
    "Ft": "HUF",
    "lei": "RON",
    "лв": "BGN",
    "R$": "BRL",
    "S/": "PEN",
    "Bs.": "BOB",
    "Bs.S": "VES",
    "₲": "PYG",
    "NT$": "TWD",
    "RM": "MYR",
    "Rp": "IDR",
    "kr": "SEK",  # Could be SEK, NOK, DKK, ISK - default to SEK
    "Kč": "CZK",
    "ден": "MKD",
    "КМ": "BAM",
    "дин": "RSD",
    "L": "ALL",  # Could be ALL, MDL, HNL, LSL, SZL - default to ALL
    "Q": "GTQ",
    "C$": "NIO",
    "₡": "CRC",
    "B/.": "PAB",
    "T$": "TOP",
    "Vt": "VUV",
    "T": "WST",
    "K": "PGK",  # Could be PGK or MMK - default to PGK
    "P": "BWP",  # Could be BWP or MOP - default to BWP
    "Esc": "CVE",
    "Db": "STN",
    "Nfk": "ERN",
    "UM": "MRU",
    "Ar": "MGA",
    "MT": "MZN",
    "ZK": "ZMW",
    "MK": "MWK",
    "so'm": "UZS",
    "с": "KGS",
    "ЅМ": "TJS",
    "m": "TMT",
    "֏": "AMD",
    "₼": "AZN",
    "₾": "GEL",
    "Kn": "HRK",
    "K": "MMK",  # Alternative for Myanmar
    "P": "MOP",  # Alternative for Macau
    "L": "HNL",  # Alternative for Honduras
    "L": "LSL",  # Alternative for Lesotho
    "L": "SZL",  # Alternative for Swaziland
    "L": "MDL",  # Alternative for Moldova
    "Fr": "RWF",  # Alternative for Rwanda
    "Fr": "BIF",  # Alternative for Burundi
    "Fr": "GNF",  # Alternative for Guinea
    "Fr": "DJF",  # Alternative for Djibouti
    "Fr": "KMF",  # Alternative for Comoros
    "Sh": "TZS",  # Alternative for Tanzania
    "Sh": "SOS",  # Alternative for Somalia
    "Br": "BYN",  # Alternative for Belarus
    "₨": "LKR",  # Alternative for Sri Lanka
    "₨": "NPR",  # Alternative for Nepal
    "₨": "MUR",  # Alternative for Mauritius
    "₨": "SCR",  # Alternative for Seychelles
    "$": "CAD",  # Alternative for Canada
    "$": "AUD",  # Alternative for Australia
    "$": "NZD",  # Alternative for New Zealand
    "$": "SGD",  # Alternative for Singapore
    "$": "HKD",  # Alternative for Hong Kong
    "$": "MXN",  # Alternative for Mexico
    "$": "ARS",  # Alternative for Argentina
    "$": "CLP",  # Alternative for Chile
    "$": "COP",  # Alternative for Colombia
    "$": "DOP",  # Alternative for Dominican Republic
    "$": "JMD",  # Alternative for Jamaica
    "$": "BBD",  # Alternative for Barbados
    "$": "BZD",  # Alternative for Belize
    "$": "TTD",  # Alternative for Trinidad
    "$": "XCD",  # Alternative for East Caribbean
    "$": "GYD",  # Alternative for Guyana
    "$": "SRD",  # Alternative for Suriname
    "$": "SBD",  # Alternative for Solomon Islands
    "$": "FJD",  # Alternative for Fiji
    "$": "NAD",  # Alternative for Namibia
    "$": "ZWL",  # Alternative for Zimbabwe
    "$": "LRD",  # Alternative for Liberia
    "£": "EGP",  # Alternative for Egypt
    "£": "SSP",  # Alternative for South Sudan
    "£": "SYP",  # Alternative for Syria
    "¥": "CNY",  # Alternative for China
    "kr": "NOK",  # Alternative for Norway
    "kr": "DKK",  # Alternative for Denmark
    "kr": "ISK",  # Alternative for Iceland
    "L": "HNL",  # Alternative for Honduras
    "L": "MDL",  # Alternative for Moldova
    "L": "LSL",  # Alternative for Lesotho
    "L": "SZL",  # Alternative for Swaziland
    "L": "ALL",  # Alternative for Albania
    "Fr": "CHF",  # Alternative for Switzerland
    "Fr": "RWF",  # Alternative for Rwanda
    "Fr": "BIF",  # Alternative for Burundi
    "Fr": "GNF",  # Alternative for Guinea
    "Fr": "DJF",  # Alternative for Djibouti
    "Fr": "KMF",  # Alternative for Comoros
    "Fr": "XPF",  # Alternative for CFP Franc
    "Fr": "KMF",  # Alternative for Comoros
    "Fr": "DJF",  # Alternative for Djibouti
    "Fr": "GNF",  # Alternative for Guinea
    "Fr": "BIF",  # Alternative for Burundi
    "Fr": "RWF",  # Alternative for Rwanda
    "Fr": "XPF",  # Alternative for CFP Franc
    "Fr": "KMF",  # Alternative for Comoros
    "Fr": "DJF",  # Alternative for Djibouti
    "Fr": "GNF",  # Alternative for Guinea
    "Fr": "BIF",  # Alternative for Burundi
    "Fr": "RWF",  # Alternative for Rwanda
    "Fr": "XPF",  # Alternative for CFP Franc
    "Fr": "KMF",  # Alternative for Comoros
    "Fr": "DJF",  # Alternative for Djibouti
    "Fr": "GNF",  # Alternative for Guinea
    "Fr": "BIF",  # Alternative for Burundi
    "Fr": "RWF",  # Alternative for Rwanda
    "EUR": "EUR",
    "USD": "USD",
    "GBP": "GBP",
    "JPY": "JPY",
    "CNY": "CNY",
    "INR": "INR",
    "AUD": "AUD",
    "CAD": "CAD",
    "CHF": "CHF",
    "NOK": "NOK",
    "SEK": "SEK",
    "DKK": "DKK",
    "PLN": "PLN",
    "CZK": "CZK",
    "HUF": "HUF",
    "RON": "RON",
    "BGN": "BGN",
    "HRK": "HRK",
    "RSD": "RSD",
    "BAM": "BAM",
    "MKD": "MKD",
    "ALL": "ALL",
    "ISK": "ISK",
    "BRL": "BRL",
    "MXN": "MXN",
    "ARS": "ARS",
    "CLP": "CLP",
    "COP": "COP",
    "PEN": "PEN",
    "VES": "VES",
    "UYU": "UYU",
    "PYG": "PYG",
    "BOB": "BOB",
    "GTQ": "GTQ",
    "HNL": "HNL",
    "NIO": "NIO",
    "CRC": "CRC",
    "PAB": "PAB",
    "DOP": "DOP",
    "HTG": "HTG",
    "JMD": "JMD",
    "BBD": "BBD",
    "BZD": "BZD",
    "TTD": "TTD",
    "XCD": "XCD",
    "GYD": "GYD",
    "SRD": "SRD",
    "NZD": "NZD",
    "FJD": "FJD",
    "PGK": "PGK",
    "SBD": "SBD",
    "VUV": "VUV",
    "WST": "WST",
    "TOP": "TOP",
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


def get_rate_from_db_or_fallback(from_currency: str, to_currency: str) -> Tuple[Decimal, bool]:
    """
    Get conversion rate from database or fallback to hardcoded rates.
    
    Args:
        from_currency: Source currency code
        to_currency: Target currency code
    
    Returns:
        Tuple of (rate as Decimal, is_from_db: bool)
        Rate means: 1 from_currency = rate to_currency
    """
    from_upper = from_currency.upper()
    to_upper = to_currency.upper()
    
    try:
        from app.models.currency_rate import CurrencyRate
        
        # Try to get rate from database
        rate = CurrencyRate.get_rate(from_upper, to_upper)
        if rate is not None:
            return rate, True
    except Exception:
        # If database lookup fails, fall back to hardcoded rates
        pass
    
    # Fallback to hardcoded rates (all relative to GMD)
    from_rate = CURRENCY_RATES.get(from_upper, None)
    to_rate = CURRENCY_RATES.get(to_upper, None)
    
    # If converting from GMD to another currency
    if from_upper == "GMD" and to_rate is not None:
        return Decimal(str(to_rate)), False
    # If converting to GMD from another currency
    elif to_upper == "GMD" and from_rate is not None and from_rate != 0:
        return Decimal('1.0') / Decimal(str(from_rate)), False
    # If both currencies have rates relative to GMD, calculate cross rate
    elif from_rate is not None and to_rate is not None and from_rate != 0:
        # 1 from_currency = (to_rate / from_rate) to_currency
        return Decimal(str(to_rate)) / Decimal(str(from_rate)), False
    
    # No rate found, return 1.0 (no conversion)
    return Decimal('1.0'), False


def convert_price(amount, from_currency="GMD", to_currency="GMD"):
    """
    Convert price from one currency to another.
    Automatically parses prices that may contain currency symbols.
    
    All prices are stored in GMD (base currency).
    Rates represent: 1 GMD = X units of target currency
    
    This function now uses database rates with fallback to hardcoded rates.
    
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
    if from_currency.upper() == to_currency.upper():
        return round(float(numeric_amount), 2)
    
    # Get rate from database or fallback
    conversion_rate, _ = get_rate_from_db_or_fallback(from_currency, to_currency)
    
    # Use Decimal for precise calculations
    amount_decimal = Decimal(str(numeric_amount))
    
    # Convert using the rate
    if conversion_rate == 0:
        return round(float(numeric_amount), 2)
    
    converted_amount = amount_decimal * conversion_rate
    
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

