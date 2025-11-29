# Currency Conversion & Language Switching Implementation

## ✅ Completed Features

### 1. Currency Conversion System

**File: `app/utils/currency_rates.py`**
- Created currency conversion rates file with rates for:
  - XOF (West African CFA franc) - 0.0095
  - GMD (Gambian Dalasi) - 1.0 (base currency)
  - SLL (Sierra Leone Leone) - 0.00036
  - UGX (Ugandan Shilling) - 0.00026
- Added helper functions:
  - `convert_price(amount, from_currency, to_currency)` - Converts prices between currencies
  - `get_currency_symbol(currency_code)` - Returns currency symbol
  - `format_price(amount, currency_code)` - Formats price with symbol

**Integration:**
- Added template filters: `convert_price` and `price_with_symbol`
- Updated `calculate_cart_totals()` to convert all prices to current currency
- Updated all product price displays in templates:
  - `index.html` - Home page products
  - `products.html` - Products listing page
  - `product.html` - Product detail page
  - `category.html` - Category pages
  - `cart.html` - Shopping cart
  - `checkout.html` - Checkout page

**How it works:**
- Product prices are stored in GMD (base currency)
- When displayed, prices are automatically converted to the selected country's currency
- Currency symbols update automatically (CFA, D, Le, USh)

### 2. Language Switching System

**Flask-Babel Integration:**
- Added Flask-Babel to `requirements.txt`
- Configured Babel in `app/extensions.py` and `app/__init__.py`
- Added locale selector function that reads from session
- Language is automatically set when country is selected

**Translation Files:**
- Created `babel.cfg` configuration file
- Created translation directories:
  - `translations/en/LC_MESSAGES/`
  - `translations/fr/LC_MESSAGES/`
- Added initial translation files with common strings

**Language Mapping:**
- Senegal → French (fr)
- Côte d'Ivoire → French (fr)
- Mali → French (fr)
- Burkina Faso → French (fr)
- Sierra Leone → English (en)
- Gambia → English (en)
- Uganda → English (en)

**Template Integration:**
- Added `_()` translation function to context processor
- Updated base template to use current language in `<html lang="">`
- Wrapped key strings in templates with `_()` function

### 3. Country Selection Updates

**API Route (`/api/country/select`):**
- Now sets `session['language']` when country is selected
- Sets `session['currency']` for currency tracking
- Page automatically reloads after selection to apply changes

**JavaScript:**
- Country selection triggers page reload
- New currency and language are applied immediately
- Works for both logged-in users and guests

## 📋 Next Steps (Optional Enhancements)

1. **Compile Translation Files:**
   ```bash
   pip install Flask-Babel Babel
   flask babel compile -d translations
   ```

2. **Add More Translations:**
   - Extract more strings from templates: `flask babel extract -F babel.cfg -k _l -o messages.pot .`
   - Update translation files with more strings
   - Recompile translations

3. **Currency Rate Updates:**
   - Consider integrating with a currency API for real-time rates
   - Add admin interface to update rates
   - Add rate history/versioning

## 🧪 Testing

To test the implementation:

1. **Currency Conversion:**
   - Select different countries from the dropdown
   - Verify prices change on all pages
   - Check currency symbols update correctly

2. **Language Switching:**
   - Select a French-speaking country (Senegal, Côte d'Ivoire, etc.)
   - Verify page text switches to French
   - Select an English-speaking country
   - Verify text switches back to English

3. **Persistence:**
   - Select a country as a guest (not logged in)
   - Verify selection persists across page reloads
   - Log in and verify country preference is saved
   - Log out and verify guest selection still works

## 📝 Notes

- Base currency is GMD (Gambian Dalasi)
- All product prices in database are stored in GMD
- Conversion happens at display time
- Currency rates can be updated in `app/utils/currency_rates.py`
- Translation files can be extended with more strings as needed

