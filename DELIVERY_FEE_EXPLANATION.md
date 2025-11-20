# Delivery Fee System - Explanation

## Overview
The delivery fee system automatically calculates shipping costs based on the product price. It can also be manually set by administrators.

---

## Default Delivery Fee Rules

The system uses a **tiered pricing structure** based on product price:

| Product Price Range | Delivery Fee |
|---------------------|--------------|
| **D0 - D1,000** | **D300** (Default) |
| **D1,001 - D2,000** | **D800** |
| **Above D2,000** | **D1,200** |

**Default Delivery Fee: D300** (used when price is missing or for products ≤ D1,000)

---

## How It Works

### 1. **Function: `calculate_delivery_price(price)`**
   - **Location:** `app/__init__.py` (line 2552)
   - **Purpose:** Calculates delivery fee based on product price
   - **Logic:**
     - If price is `None` → returns **D300** (default)
     - If price ≤ 1000 → returns **D300**
     - If price > 1000 and ≤ 2000 → returns **D800**
     - If price > 2000 → returns **D1,200**

### 2. **Database Storage**
   - **Field:** `delivery_price` in the `Product` table
   - **Type:** Float (can be `NULL`)
   - **Location:** `app/__init__.py` (line 1230)

### 3. **When Delivery Fee is Calculated**

   **A. When Creating a New Product:**
   - Admin fills out product form
   - If delivery fee field is empty or 0 → automatically calculated using `calculate_delivery_price()`
   - If admin manually enters a delivery fee → that value is used instead
   - **Location:** `app/__init__.py` (lines 4417-4419)

   **B. When Editing a Product:**
   - If admin manually sets delivery fee → uses that value
   - If product price changes and delivery fee wasn't manually set → recalculates automatically
   - If delivery fee is `NULL` → calculates it automatically
   - **Location:** `app/__init__.py` (lines 4489-4494)

   **C. When Viewing a Product Page:**
   - If product has no delivery fee stored → calculates it automatically
   - Displays the delivery fee on the product page
   - **Location:** `app/__init__.py` (lines 6985-6990)

### 4. **Auto-Calculation in Admin Form (JavaScript)**
   - When admin types a product price, delivery fee field auto-fills
   - Admin can manually override the auto-calculated value
   - **Location:** `app/templates/admin/admin/product_form.html` (lines 167-199)

### 5. **Display on Product Page**
   - Shows delivery fee below the product price
   - Format: "Delivery Fee: D[amount]"
   - Only shows if delivery fee exists
   - **Location:** `app/templates/product.html` (lines 56-60)

---

## Key Features

1. **Automatic Calculation:** System calculates delivery fee based on price tiers
2. **Manual Override:** Admins can set custom delivery fees
3. **Backward Compatibility:** If old products don't have delivery fee, it's calculated on-the-fly
4. **Default Fallback:** Always defaults to D300 if price is missing

---

## Code Locations Summary

| Component | File | Line(s) |
|-----------|------|---------|
| Calculation Function | `app/__init__.py` | 2552-2561 |
| Database Field | `app/__init__.py` | 1230 |
| Product Creation | `app/__init__.py` | 4417-4419 |
| Product Editing | `app/__init__.py` | 4489-4494 |
| Product View | `app/__init__.py` | 6985-6990 |
| Display Template | `app/templates/product.html` | 56-60 |
| Admin Form JS | `app/templates/admin/admin/product_form.html` | 167-199 |

---

## Example Scenarios

**Scenario 1: New Product (Price: D500)**
- System calculates: D300 (because 500 ≤ 1000)
- Delivery fee saved: D300

**Scenario 2: New Product (Price: D1,500)**
- System calculates: D800 (because 1000 < 1500 ≤ 2000)
- Delivery fee saved: D800

**Scenario 3: Admin Override**
- Product price: D500
- Admin manually sets delivery fee: D500
- System uses: D500 (manual override takes priority)

**Scenario 4: Old Product (No Delivery Fee)**
- Product has `delivery_price = NULL`
- When viewed, system calculates based on current price
- Calculated value is stored for future use

