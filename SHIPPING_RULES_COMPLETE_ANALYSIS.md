# Complete Shipping Rules System Analysis

**Generated:** 2025-01-20  
**Scope:** Complete analysis of all shipping-related code, models, routes, templates, and services

---

## 1. FILE INDEX

### Core Shipping Module Files
- `app/shipping/__init__.py` - Module exports and initialization
- `app/shipping/models.py` - New ShippingRule and ShippingMode models
- `app/shipping/service.py` - ShippingService business logic
- `app/shipping/routes.py` - API routes for shipping (Blueprint: `/api/shipping`)
- `app/shipping/constants.py` - Shipping method constants and helpers

### Main Application Files
- `app/__init__.py` - Contains:
  - LegacyShippingRule model (lines 1697-1740)
  - `calculate_shipping_price()` function (lines 1784-1930)
  - Admin shipping routes (lines 5299-5700+)
  - API shipping routes (lines 2664-2767)
  - Checkout/order creation logic using shipping

### Template Files
- `app/templates/admin/admin/shipping_rules.html` - Admin list view
- `app/templates/admin/admin/shipping_rule_form.html` - Admin create/edit form
- `app/templates/checkout.html` - Checkout page with shipping selection
- `app/templates/product.html` - Product page with shipping calculator

### JavaScript Files
- `app/static/js/shipping_selector.js` - Frontend shipping selector component

### Payment/Order Files
- `app/payments/models.py` - PendingPayment model with shipping fields (lines 101-154)
- `app/payments/services.py` - PaymentService with order conversion logic

### Migration Files
- `migrations/versions/n00o123p4q5r_add_new_shipping_system.py` - New shipping system migration
- `migrations/versions/m99n012o3p4q_add_shipping_method_support.py` - Shipping method support
- `migrations/versions/i88j901f2g4h_add_shipping_rules_system.py` - Initial shipping rules
- `migrations/versions/8987a1cb18ec_add_shipping_rule_fields_to_pending_.py` - PendingPayment fields

### Documentation Files
- `SHIPPING_RULE_SYSTEM_ANALYSIS.md` - Previous analysis document
- `SHIPPING_SYSTEM_IMPLEMENTATION.md` - Implementation notes

### Script Files
- `scripts/seed_shipping_data.py` - Data seeding script

---

## 2. MODEL DEFINITIONS

### 2.1 LegacyShippingRule (DEPRECATED)

**File:** `app/__init__.py` (lines 1697-1740)  
**Table:** `shipping_rule`  
**Status:** ⚠️ **DEPRECATED** - Still in use by admin UI

```python
class LegacyShippingRule(db.Model):
    __tablename__ = 'shipping_rule'
    
    # Primary Key
    id = db.Column(db.Integer, primary_key=True)
    
    # Rule Configuration
    rule_type = db.Column(db.String(20), nullable=False, default='country')  # 'country' or 'global'
    country_id = db.Column(db.Integer, db.ForeignKey('country.id'), nullable=True)  # Nullable for global rules
    shipping_method = db.Column(db.String(20), nullable=True)  # 'express', 'ecommerce', 'economy' or None
    
    # Weight Range
    min_weight = db.Column(db.Numeric(10, 6), nullable=False)
    max_weight = db.Column(db.Numeric(10, 6), nullable=False)
    
    # Pricing
    price_gmd = db.Column(db.Numeric(10, 2), nullable=False)
    delivery_time = db.Column(db.String(100), nullable=True)
    
    # Priority & Status
    priority = db.Column(db.Integer, default=0, nullable=False)
    status = db.Column(db.Boolean, default=True, nullable=False)  # Active/Inactive
    
    # Metadata
    note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    country = db.relationship('Country', backref='shipping_rules', lazy=True)
```

**Issues:**
- ⚠️ Uses `country_id` (FK to Country) instead of `country_iso` (string)
- ⚠️ Uses `shipping_method` instead of `shipping_mode_key`
- ⚠️ Field name mismatch with new system

---

### 2.2 ShippingMode (NEW SYSTEM)

**File:** `app/shipping/models.py` (lines 11-48)  
**Table:** `shipping_modes`  
**Status:** ✅ **ACTIVE**

```python
class ShippingMode(db.Model):
    __tablename__ = 'shipping_modes'
    
    # Primary Key
    id = db.Column(db.Integer, primary_key=True)
    
    # Mode Identification
    key = db.Column(db.String(50), unique=True, nullable=False)  # 'express', 'economy_plus', 'economy'
    label = db.Column(db.String(255), nullable=False)  # Display name
    description = db.Column(db.Text, nullable=True)
    
    # Display Properties
    delivery_time_range = db.Column(db.String(100), nullable=True)  # e.g., "3–7 days"
    icon = db.Column(db.String(50), nullable=True)  # Emoji or icon identifier
    color = db.Column(db.String(50), nullable=True)  # Color for UI
    
    # Status
    active = db.Column(db.Boolean, default=True, nullable=False)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
```

**Relationships:**
- Referenced by ShippingRule via `shipping_mode_key` (FK)

---

### 2.3 ShippingRule (NEW SYSTEM)

**File:** `app/shipping/models.py` (lines 51-118)  
**Table:** `shipping_rules`  
**Status:** ✅ **ACTIVE** - Used by calculation system

```python
class ShippingRule(db.Model):
    __tablename__ = 'shipping_rules'
    
    # Primary Key
    id = db.Column(db.Integer, primary_key=True)
    
    # Rule Configuration
    country_iso = db.Column(db.String(3), nullable=False, index=True)  # ISO code or '*' for global
    shipping_mode_key = db.Column(db.String(50), db.ForeignKey('shipping_modes.key', ondelete='CASCADE'), nullable=False, index=True)
    
    # Weight Range
    min_weight = db.Column(db.Numeric(10, 3), nullable=False)
    max_weight = db.Column(db.Numeric(10, 3), nullable=False)
    
    # Pricing
    price_gmd = db.Column(db.Numeric(10, 2), nullable=False)
    delivery_time = db.Column(db.String(100), nullable=True)  # Optional override
    
    # Priority & Status
    priority = db.Column(db.Integer, default=0, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    
    # Metadata
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Constraints
    __table_args__ = (
        CheckConstraint('min_weight < max_weight', name='check_min_max_weight'),
        CheckConstraint('price_gmd >= 0', name='check_price_non_negative'),
        Index('idx_country_mode_weight', 'country_iso', 'shipping_mode_key', 'min_weight', 'max_weight'),
        Index('idx_priority', 'priority'),
    )
```

**Relationships:**
- `shipping_mode` - Relationship to ShippingMode via `shipping_mode_key`
- Referenced by PendingPayment via `shipping_rule_id` (FK)

**Key Differences from Legacy:**
- ✅ Uses `country_iso` (string) instead of `country_id` (FK)
- ✅ Uses `shipping_mode_key` instead of `shipping_method`
- ✅ Uses `active` instead of `status`
- ✅ Uses `notes` instead of `note`
- ✅ Has proper database constraints and indexes

---

### 2.4 PendingPayment (Shipping Fields)

**File:** `app/payments/models.py` (lines 101-154)  
**Table:** `pending_payments`  
**Status:** ✅ **ACTIVE**

**Shipping-Related Fields:**
```python
# Shipping rule fields (for automatic shipping calculation)
shipping_rule_id = db.Column(db.Integer, db.ForeignKey('shipping_rules.id'), nullable=True)  # NEW SYSTEM
shipping_method = db.Column(db.String(20), nullable=True)  # Selected shipping method: 'express', 'ecommerce', 'economy'
shipping_delivery_estimate = db.Column(db.String(100), nullable=True)
shipping_display_currency = db.Column(db.String(10), nullable=True)
shipping_price = db.Column(db.Float, nullable=True)
```

**Relationships:**
- `shipping_rule` - Relationship to ShippingRule (NEW SYSTEM) via `shipping_rule_id`

**Note:** Uses both `shipping_rule_id` (new system) and `shipping_method` (legacy field name)

---

## 3. FUNCTIONS & METHODS

### 3.1 Shipping Calculation Functions

#### `calculate_shipping_price()` - Main Calculation Function

**File:** `app/__init__.py` (lines 1784-1930)  
**Status:** ✅ **USES NEW SYSTEM**

**Purpose:** Calculate shipping price based on weight, country, and shipping method

**Parameters:**
- `total_weight_kg: float` - Total weight in kilograms
- `country_id: Optional[int]` - Country ID (converted to ISO)
- `shipping_method: Optional[str]` - Shipping method ('express', 'ecommerce', 'economy')
- `default_weight: float = 0.0` - Default weight if weight is 0

**Returns:**
- `Dict` with keys: `rule`, `price_gmd`, `delivery_time`, `rule_name`, `debug_info`, `available`, `shipping_method`
- `None` if no rule matches

**Logic:**
1. Converts `country_id` to `country_iso` (ISO code)
2. Maps old shipping method names to new ones:
   - `'express'` → `'express'`
   - `'ecommerce'` → `'economy_plus'`
   - `'economy'` → `'economy'`
3. Calls `ShippingService.calculate_shipping()` (NEW SYSTEM)
4. Returns formatted result

**Issues:**
- ✅ Correctly uses new ShippingRule system
- ✅ Properly maps legacy method names to new ones

---

#### `ShippingService.calculate_shipping()` - Service Layer

**File:** `app/shipping/service.py` (lines 18-144)  
**Status:** ✅ **ACTIVE**

**Purpose:** Core shipping calculation using new ShippingRule model

**Parameters:**
- `country_iso: str` - Country ISO code or '*'
- `shipping_mode_key: str` - Shipping mode key
- `total_weight_kg: float` - Total weight

**Returns:**
- `Dict` with shipping fee, delivery time, mode info, rule_id

**Logic:**
1. Tries country-specific rules first
2. Falls back to global rules (`country_iso = '*'`)
3. Orders by priority (desc) and created_at (asc)
4. Returns first matching rule

**Issues:**
- ✅ Correctly filters by `country_iso` and `shipping_mode_key`
- ✅ Properly handles weight range matching

---

#### `ShippingService.validate_rule_overlap()` - Overlap Validation

**File:** `app/shipping/service.py` (lines 153-189)  
**Status:** ⚠️ **MISSING shipping_method FILTER**

**Purpose:** Check if a new rule would overlap with existing rules

**Parameters:**
- `country_iso: str`
- `shipping_mode_key: str`
- `min_weight: Decimal`
- `max_weight: Decimal`
- `exclude_rule_id: Optional[int]`

**Returns:**
- `Tuple[bool, Optional[str]]` - (has_overlap, error_message)

**Issues:**
- ✅ Correctly filters by `country_iso` and `shipping_mode_key`
- ✅ Properly checks weight range overlap
- ✅ Excludes current rule when updating

---

### 3.2 Rule Management Functions

#### `ShippingService.create_rule()` - Create Rule

**File:** `app/shipping/service.py` (lines 192-247)  
**Status:** ✅ **ACTIVE**

**Purpose:** Create a new shipping rule with validation

**Parameters:**
- `country_iso: str`
- `shipping_mode_key: str`
- `min_weight: float`
- `max_weight: float`
- `price_gmd: float`
- `delivery_time: Optional[str]`
- `priority: int = 0`
- `notes: Optional[str]`
- `active: bool = True`

**Returns:**
- `Tuple[Optional[ShippingRule], Optional[str]]` - (rule, error)

**Validation:**
- ✅ Checks min_weight < max_weight
- ✅ Checks price_gmd >= 0
- ✅ Validates shipping mode exists
- ✅ Checks for overlaps using `validate_rule_overlap()`

---

#### `ShippingService.update_rule()` - Update Rule

**File:** `app/shipping/service.py` (lines 250-323)  
**Status:** ✅ **ACTIVE**

**Purpose:** Update an existing shipping rule with validation

**Parameters:**
- `rule_id: int`
- All other fields optional

**Returns:**
- `Tuple[Optional[ShippingRule], Optional[str]]` - (rule, error)

**Validation:**
- ✅ Same as create_rule()
- ✅ Excludes current rule from overlap check

---

#### `ShippingRule.overlaps_with()` - Instance Method

**File:** `app/shipping/models.py` (lines 99-117)  
**Status:** ✅ **ACTIVE**

**Purpose:** Check if this rule's weight range overlaps with another rule

**Parameters:**
- `other: ShippingRule`

**Returns:**
- `bool` - True if overlaps

**Logic:**
- ✅ Checks same `country_iso`
- ✅ Checks same `shipping_mode_key`
- ✅ Checks weight range overlap: `min1 <= max2 AND min2 <= max1`

---

### 3.3 Admin Functions (LEGACY SYSTEM)

#### `admin_new_shipping_rule()` - Create Rule (Admin)

**File:** `app/__init__.py` (lines 5397-5507)  
**Status:** ❌ **CRITICAL BUG - Missing shipping_method extraction**

**Purpose:** Create a new shipping rule via admin UI

**Route:** `POST /admin/shipping/new`

**Form Fields Extracted:**
- ✅ `rule_type`
- ✅ `country_id`
- ❌ **MISSING:** `shipping_method` (line 5483 uses undefined variable!)
- ✅ `min_weight`
- ✅ `max_weight`
- ✅ `price_gmd`
- ✅ `delivery_time`
- ✅ `priority`
- ✅ `status`
- ✅ `note`

**Issues:**
- ❌ **CRITICAL:** Line 5483 uses `shipping_method` but it's never extracted from form (line 5405-5412)
- ⚠️ Uses LegacyShippingRule instead of new ShippingRule
- ⚠️ Overlap check doesn't filter by shipping_method (lines 5452-5477)

---

#### `admin_edit_shipping_rule()` - Edit Rule (Admin)

**File:** `app/__init__.py` (lines 5509-5629)  
**Status:** ✅ **CORRECT** - Extracts shipping_method

**Purpose:** Edit an existing shipping rule via admin UI

**Route:** `POST /admin/shipping/<id>/edit`

**Form Fields Extracted:**
- ✅ `rule_type`
- ✅ `country_id`
- ✅ `shipping_method` (line 5520 - CORRECTLY EXTRACTED)
- ✅ `min_weight`
- ✅ `max_weight`
- ✅ `price_gmd`
- ✅ `delivery_time`
- ✅ `priority`
- ✅ `status`
- ✅ `note`

**Issues:**
- ⚠️ Uses LegacyShippingRule instead of new ShippingRule
- ⚠️ Overlap check doesn't filter by shipping_method (lines 5574-5601)

---

#### `admin_delete_shipping_rule()` - Delete Rule (Admin)

**File:** `app/__init__.py` (lines 5631-5647)  
**Status:** ✅ **ACTIVE**

**Route:** `POST /admin/shipping/<id>/delete`

**Issues:**
- ⚠️ Uses LegacyShippingRule instead of new ShippingRule

---

#### `admin_duplicate_shipping_rule()` - Duplicate Rule (Admin)

**File:** `app/__init__.py` (lines 5649-5678)  
**Status:** ❌ **CRITICAL BUG - Wrong model query**

**Purpose:** Duplicate an existing shipping rule

**Route:** `POST /admin/shipping/<id>/duplicate`

**Issues:**
- ❌ **CRITICAL:** Line 5654 queries `ShippingRule.query` (NEW SYSTEM) but creates `LegacyShippingRule` (line 5657)
- ❌ Missing `shipping_method` field in duplicate creation
- ⚠️ Should use LegacyShippingRule.query for consistency

---

#### `admin_toggle_shipping_rule_status()` - Toggle Status (Admin)

**File:** `app/__init__.py` (lines 5680-5699)  
**Status:** ✅ **ACTIVE**

**Route:** `POST /admin/shipping/<id>/toggle-status`

**Issues:**
- ⚠️ Uses LegacyShippingRule instead of new ShippingRule

---

### 3.4 Mode Loading Functions

#### `ShippingService.get_active_modes()` - Get Active Modes

**File:** `app/shipping/service.py` (lines 147-150)  
**Status:** ✅ **ACTIVE**

**Purpose:** Get all active shipping modes

**Returns:**
- `List[Dict]` - List of mode dictionaries

---

#### `get_all_shipping_methods()` - Get All Methods (Constants)

**File:** `app/shipping/constants.py` (lines 62-64)  
**Status:** ✅ **ACTIVE**

**Purpose:** Get list of all shipping method definitions

**Returns:**
- `list` - List of method dictionaries with id, label, description, etc.

---

## 4. ADMIN ROUTES

### 4.1 List Rules

**Route:** `GET /admin/shipping`  
**Handler:** `admin_shipping_rules()`  
**File:** `app/__init__.py` (lines 5299-5395)  
**Model Used:** ⚠️ `LegacyShippingRule`

**Features:**
- Search by note or country name
- Filter by rule type (country/global)
- Filter by country_id
- Filter by status (active/inactive)
- Sort by priority, country, min_weight, or price
- Pagination (20 per page)
- Statistics display (total, active, global, countries with rules)

**Issues:**
- ⚠️ Uses LegacyShippingRule instead of new ShippingRule
- ⚠️ Doesn't show shipping_method in table (but template displays it)

---

### 4.2 Create Rule

**Route:** `GET /admin/shipping/new` (form) | `POST /admin/shipping/new` (submit)  
**Handler:** `admin_new_shipping_rule()`  
**File:** `app/__init__.py` (lines 5397-5507)  
**Model Used:** ⚠️ `LegacyShippingRule`

**CRUD Operations:**
- ✅ Creates new rule
- ❌ **MISSING:** `shipping_method` extraction from form

**Validation:**
- ✅ Min/max weight validation
- ✅ Price >= 0 validation
- ⚠️ Overlap check (but doesn't filter by shipping_method)

**Issues:**
- ❌ **CRITICAL:** `shipping_method` not extracted from form (line 5483 uses undefined variable)
- ⚠️ Uses LegacyShippingRule instead of new ShippingRule

---

### 4.3 Edit Rule

**Route:** `GET /admin/shipping/<id>/edit` (form) | `POST /admin/shipping/<id>/edit` (submit)  
**Handler:** `admin_edit_shipping_rule()`  
**File:** `app/__init__.py` (lines 5509-5629)  
**Model Used:** ⚠️ `LegacyShippingRule`

**CRUD Operations:**
- ✅ Updates existing rule
- ✅ Extracts `shipping_method` correctly (line 5520)

**Validation:**
- ✅ Validates shipping_method if provided
- ✅ Min/max weight validation
- ✅ Price >= 0 validation
- ⚠️ Overlap check (but doesn't filter by shipping_method)

**Issues:**
- ⚠️ Uses LegacyShippingRule instead of new ShippingRule
- ⚠️ Overlap check doesn't filter by shipping_method

---

### 4.4 Delete Rule

**Route:** `POST /admin/shipping/<id>/delete`  
**Handler:** `admin_delete_shipping_rule()`  
**File:** `app/__init__.py` (lines 5631-5647)  
**Model Used:** ⚠️ `LegacyShippingRule`

**CRUD Operations:**
- ✅ Deletes rule

**Issues:**
- ⚠️ Uses LegacyShippingRule instead of new ShippingRule

---

### 4.5 Duplicate Rule

**Route:** `POST /admin/shipping/<id>/duplicate`  
**Handler:** `admin_duplicate_shipping_rule()`  
**File:** `app/__init__.py` (lines 5649-5678)  
**Model Used:** ❌ **MIXED** - Queries new, creates legacy

**CRUD Operations:**
- ❌ **BUG:** Queries `ShippingRule` (new) but creates `LegacyShippingRule` (old)
- ❌ Missing `shipping_method` field in duplicate

**Issues:**
- ❌ **CRITICAL:** Line 5654 uses `ShippingRule.query` but should use `LegacyShippingRule.query`
- ❌ Missing `shipping_method` in duplicate creation (line 5657-5667)

---

### 4.6 Toggle Status

**Route:** `POST /admin/shipping/<id>/toggle-status`  
**Handler:** `admin_toggle_shipping_rule_status()`  
**File:** `app/__init__.py` (lines 5680-5699)  
**Model Used:** ⚠️ `LegacyShippingRule`

**CRUD Operations:**
- ✅ Toggles active/inactive status

**Issues:**
- ⚠️ Uses LegacyShippingRule instead of new ShippingRule

---

### 4.7 Export Rules

**Route:** `GET /admin/shipping/export?format=csv|json`  
**Handler:** `admin_export_shipping_rules()`  
**File:** `app/__init__.py` (lines 5701-5748)  
**Model Used:** ⚠️ `LegacyShippingRule`

**Features:**
- ✅ Exports to CSV or JSON
- ⚠️ Exports LegacyShippingRule data

**Issues:**
- ⚠️ Uses LegacyShippingRule instead of new ShippingRule
- ⚠️ CSV export doesn't include shipping_method column

---

### 4.8 Import Rules

**Route:** `POST /admin/shipping/import`  
**Handler:** `admin_import_shipping_rules()`  
**File:** `app/__init__.py` (lines 5750-5850+)  
**Model Used:** ⚠️ `LegacyShippingRule`

**Features:**
- ✅ Imports from CSV or XLSX
- ⚠️ Imports to LegacyShippingRule

**Issues:**
- ⚠️ Uses LegacyShippingRule instead of new ShippingRule

---

## 5. API ROUTES (NEW SYSTEM)

### 5.1 Calculate Shipping

**Route:** `POST /api/shipping/calculate`  
**Handler:** `calculate_shipping()`  
**File:** `app/shipping/routes.py` (lines 27-80)  
**Blueprint:** `shipping_bp` (prefix: `/api/shipping`)

**Input Fields:**
- `country` (string) - Country name or ISO code
- `shipping_mode` (string) - Shipping mode key
- `total_weight` (float) - Total weight in kg

**Model Used:** ✅ `ShippingRule` (NEW SYSTEM)

**Service Layer:** ✅ Uses `ShippingService.calculate_shipping()`

**Status:** ✅ **CORRECT** - Uses new system

---

### 5.2 Get Shipping Modes

**Route:** `GET /api/shipping/modes`  
**Handler:** `get_modes()`  
**File:** `app/shipping/routes.py` (lines 83-109)

**Model Used:** ✅ `ShippingMode` (NEW SYSTEM)

**Service Layer:** ✅ Uses `ShippingService.get_active_modes()`

**Status:** ✅ **CORRECT**

---

### 5.3 Admin: List Rules (API)

**Route:** `GET /api/shipping/admin/rules`  
**Handler:** `list_rules()`  
**File:** `app/shipping/routes.py` (lines 113-141)  
**Auth:** ✅ Admin required

**Query Parameters:**
- `country_iso` (optional)
- `mode_key` (optional)
- `active` (optional)

**Model Used:** ✅ `ShippingRule` (NEW SYSTEM)

**Status:** ✅ **CORRECT** - Uses new system

---

### 5.4 Admin: Create Rule (API)

**Route:** `POST /api/shipping/admin/rules`  
**Handler:** `create_rule()`  
**File:** `app/shipping/routes.py` (lines 144-169)  
**Auth:** ✅ Admin required

**Input Fields:**
- `country_iso`
- `shipping_mode_key`
- `min_weight`
- `max_weight`
- `price_gmd`
- `delivery_time` (optional)
- `priority` (optional)
- `notes` (optional)
- `active` (optional)

**Model Used:** ✅ `ShippingRule` (NEW SYSTEM)

**Service Layer:** ✅ Uses `ShippingService.create_rule()`

**Status:** ✅ **CORRECT** - Uses new system

---

### 5.5 Admin: Get Rule (API)

**Route:** `GET /api/shipping/admin/rules/<id>`  
**Handler:** `get_rule()`  
**File:** `app/shipping/routes.py` (lines 172-183)  
**Auth:** ✅ Admin required

**Model Used:** ✅ `ShippingRule` (NEW SYSTEM)

**Status:** ✅ **CORRECT**

---

### 5.6 Admin: Update Rule (API)

**Route:** `PUT /api/shipping/admin/rules/<id>`  
**Handler:** `update_rule()`  
**File:** `app/shipping/routes.py` (lines 186-212)  
**Auth:** ✅ Admin required

**Model Used:** ✅ `ShippingRule` (NEW SYSTEM)

**Service Layer:** ✅ Uses `ShippingService.update_rule()`

**Status:** ✅ **CORRECT**

---

### 5.7 Admin: Delete Rule (API)

**Route:** `DELETE /api/shipping/admin/rules/<id>`  
**Handler:** `delete_rule()`  
**File:** `app/shipping/routes.py` (lines 215-231)  
**Auth:** ✅ Admin required

**Model Used:** ✅ `ShippingRule` (NEW SYSTEM)

**Status:** ✅ **CORRECT**

---

### 5.8 Legacy API: Shipping Estimate

**Route:** `POST /api/shipping/estimate`  
**Handler:** `api_shipping_estimate()`  
**File:** `app/__init__.py` (lines 2698-2767)

**Input Fields:**
- `country_id` (optional)
- `weight` (required)
- `shipping_method` (optional)

**Model Used:** ✅ Uses `calculate_shipping_price()` which uses NEW SYSTEM

**Status:** ✅ **CORRECT** - Wrapper that uses new system

---

### 5.9 Legacy API: Shipping Rules List

**Route:** `GET /api/shipping/rules`  
**Handler:** `api_shipping_rules()`  
**File:** `app/__init__.py` (lines 2664-2696)  
**Model Used:** ⚠️ `LegacyShippingRule`

**Status:** ⚠️ **LEGACY** - Still uses old system

---

## 6. TEMPLATE FORMS

### 6.1 Shipping Rule Form (Admin)

**File:** `app/templates/admin/admin/shipping_rule_form.html`  
**Used By:** Create and Edit routes

**Form Fields:**

1. **Rule Type** (`rule_type`)
   - Type: Select
   - Options: 'country', 'global'
   - Required: ✅ Yes

2. **Country** (`country_id`)
   - Type: Select
   - Required: ✅ Yes (if rule_type = 'country')
   - Options: All active countries

3. **Shipping Method** (`shipping_method`)
   - Type: Select
   - Required: ❌ No (optional)
   - Options:
     - Empty (All Methods)
     - 'express' (DHL Express)
     - 'ecommerce' (DHL eCommerce)
     - 'economy' (Economy Mail)
   - **Status:** ✅ Field exists in template (lines 71-83)

4. **Min Weight** (`min_weight`)
   - Type: Number
   - Step: 0.000001
   - Required: ✅ Yes

5. **Max Weight** (`max_weight`)
   - Type: Number
   - Step: 0.000001
   - Required: ✅ Yes

6. **Price** (`price_gmd`)
   - Type: Number
   - Step: 0.01
   - Required: ✅ Yes

7. **Delivery Time** (`delivery_time`)
   - Type: Text
   - Required: ❌ No
   - Max Length: 100

8. **Priority** (`priority`)
   - Type: Number
   - Required: ❌ No
   - Default: 0

9. **Status** (`status`)
   - Type: Checkbox
   - Required: ❌ No
   - Default: Checked (active)

10. **Notes** (`note`)
    - Type: Textarea
    - Required: ❌ No

**Mismatches:**

| Template Field | Admin Handler (Create) | Admin Handler (Edit) | Model Field (Legacy) | Model Field (New) |
|----------------|------------------------|----------------------|----------------------|-------------------|
| `shipping_method` | ❌ **NOT EXTRACTED** | ✅ Extracted (line 5520) | `shipping_method` | `shipping_mode_key` |
| `rule_type` | ✅ Extracted | ✅ Extracted | `rule_type` | N/A (uses country_iso='*') |
| `country_id` | ✅ Extracted | ✅ Extracted | `country_id` | N/A (uses country_iso) |
| `min_weight` | ✅ Extracted | ✅ Extracted | `min_weight` | `min_weight` |
| `max_weight` | ✅ Extracted | ✅ Extracted | `max_weight` | `max_weight` |
| `price_gmd` | ✅ Extracted | ✅ Extracted | `price_gmd` | `price_gmd` |
| `delivery_time` | ✅ Extracted | ✅ Extracted | `delivery_time` | `delivery_time` |
| `priority` | ✅ Extracted | ✅ Extracted | `priority` | `priority` |
| `status` | ✅ Extracted | ✅ Extracted | `status` | `active` |
| `note` | ✅ Extracted | ✅ Extracted | `note` | `notes` |

**Critical Issues:**
- ❌ **MISSING:** `shipping_method` extraction in `admin_new_shipping_rule()` (line 5483 uses undefined variable)
- ⚠️ Template uses `shipping_method` but new system uses `shipping_mode_key`
- ⚠️ Template uses `country_id` but new system uses `country_iso`

---

### 6.2 Shipping Rules List (Admin)

**File:** `app/templates/admin/admin/shipping_rules.html`  
**Used By:** List route

**Displayed Fields:**
- ✅ Rule Type (country/global)
- ✅ Country (with flag)
- ✅ Shipping Method (displays badge)
- ✅ Weight Range
- ✅ Price
- ✅ Delivery Time
- ✅ Priority
- ✅ Status
- ✅ Actions (Edit, Duplicate, Toggle, Delete)

**Issues:**
- ⚠️ Displays LegacyShippingRule data
- ✅ Correctly displays shipping_method field

---

## 7. CRITICAL PROBLEMS

### 7.1 Missing shipping_method Extraction in Create Route

**Severity:** 🔴 **CRITICAL**

**Location:** `app/__init__.py`, line 5483

**Problem:**
```python
# Line 5405-5412: Form fields extracted
rule_type = request.form.get('rule_type', 'country')
country_id = request.form.get('country_id')
# shipping_method is NOT extracted here!

# Line 5483: shipping_method used but undefined
rule = LegacyShippingRule(
    ...
    shipping_method=shipping_method,  # ❌ UNDEFINED VARIABLE!
    ...
)
```

**Impact:**
- Creating new rules via admin UI will fail with `NameError: name 'shipping_method' is not defined`
- Shipping method selection in form is ignored

**Fix Required:**
```python
# Add after line 5412:
shipping_method = request.form.get('shipping_method', '').strip() or None
```

---

### 7.2 Admin UI Uses Legacy Table Instead of New System

**Severity:** 🟡 **HIGH**

**Problem:**
- All admin routes (`/admin/shipping/*`) use `LegacyShippingRule` model
- Calculation system uses new `ShippingRule` model
- Admin-created rules are stored in `shipping_rule` table (legacy)
- Calculation system reads from `shipping_rules` table (new)

**Impact:**
- Rules created via admin UI are **NOT used** by calculation system
- Admin and calculation system are completely disconnected
- Data duplication required (rules must be created in both systems)

**Affected Routes:**
- `GET /admin/shipping` - Lists legacy rules
- `POST /admin/shipping/new` - Creates legacy rules
- `POST /admin/shipping/<id>/edit` - Updates legacy rules
- `POST /admin/shipping/<id>/delete` - Deletes legacy rules
- `POST /admin/shipping/<id>/duplicate` - Duplicates legacy rules
- `POST /admin/shipping/<id>/toggle-status` - Toggles legacy rules

**Fix Required:**
- Migrate all admin routes to use new `ShippingRule` model
- Convert `country_id` → `country_iso` in form handlers
- Convert `shipping_method` → `shipping_mode_key` in form handlers
- Update templates to use new field names

---

### 7.3 Duplicate Route Uses Wrong Model

**Severity:** 🔴 **CRITICAL**

**Location:** `app/__init__.py`, line 5654

**Problem:**
```python
@app.route('/admin/shipping/<int:rule_id>/duplicate', methods=['POST'])
def admin_duplicate_shipping_rule(rule_id):
    original_rule = ShippingRule.query.get_or_404(rule_id)  # ❌ Queries NEW system
    
    new_rule = LegacyShippingRule(  # ❌ Creates in OLD system
        rule_type=original_rule.rule_type,  # ❌ Field doesn't exist in new system
        country_id=original_rule.country_id,  # ❌ Field doesn't exist in new system
        # ❌ Missing shipping_method field
        ...
    )
```

**Impact:**
- Will fail with `AttributeError` when trying to access `rule_type` or `country_id` on new ShippingRule
- Cannot duplicate rules from either system correctly

**Fix Required:**
- Use `LegacyShippingRule.query` if duplicating legacy rules
- Or migrate to new system and use `ShippingRule.query`

---

### 7.4 Overlap Checks Don't Filter by shipping_method

**Severity:** 🟡 **MEDIUM**

**Location:** `app/__init__.py`, lines 5452-5477, 5574-5601

**Problem:**
```python
# Overlap check for country rules
overlapping = LegacyShippingRule.query.filter(
    LegacyShippingRule.rule_type == 'country',
    LegacyShippingRule.country_id == country_id,
    LegacyShippingRule.status == True,
    # ❌ Missing: LegacyShippingRule.shipping_method filter
    db.or_(...)
).first()
```

**Impact:**
- Rules with different shipping methods can overlap
- Example: Express rule (0-1kg) and Economy rule (0-1kg) for same country will trigger overlap warning
- This is incorrect - different methods should be allowed to overlap

**Fix Required:**
- Add `shipping_method` filter to overlap checks:
```python
if shipping_method:
    query = query.filter(LegacyShippingRule.shipping_method == shipping_method)
```

---

### 7.5 Field Name Mismatches Between Systems

**Severity:** 🟡 **MEDIUM**

**Problem:**
- Legacy system uses `shipping_method` (string: 'express', 'ecommerce', 'economy')
- New system uses `shipping_mode_key` (string: 'express', 'economy_plus', 'economy')
- Legacy system uses `country_id` (FK to Country)
- New system uses `country_iso` (string: 'GMB', 'SEN', '*')
- Legacy system uses `status` (boolean)
- New system uses `active` (boolean)
- Legacy system uses `note` (text)
- New system uses `notes` (text)

**Impact:**
- Cannot directly migrate data between systems
- Mapping required for conversion
- Method name mismatch: 'ecommerce' (legacy) vs 'economy_plus' (new)

**Fix Required:**
- Create migration script to convert legacy → new
- Update admin UI to use new field names
- Map method names during conversion

---

### 7.6 Calculation System Ignores Admin Rules

**Severity:** 🔴 **CRITICAL**

**Problem:**
- Admin UI creates rules in `shipping_rule` table (LegacyShippingRule)
- Calculation system reads from `shipping_rules` table (ShippingRule)
- These are **completely separate tables**

**Impact:**
- **Rules created via admin UI are NOT used for shipping calculations**
- Admin must use API endpoints (`/api/shipping/admin/rules`) to create rules that work
- Or manually insert into `shipping_rules` table

**Evidence:**
- `calculate_shipping_price()` uses `ShippingService.calculate_shipping()`
- `ShippingService.calculate_shipping()` queries `ShippingRule` (new system)
- Admin routes use `LegacyShippingRule` (old system)

**Fix Required:**
- Migrate admin routes to use new ShippingRule model
- Or create sync mechanism between systems

---

## 8. FINAL SUMMARY

### 8.1 How Shipping Currently Works

**Current Architecture:**

1. **Two Separate Systems:**
   - **Legacy System:** `LegacyShippingRule` model, `shipping_rule` table
     - Used by: Admin UI (`/admin/shipping/*`)
     - Fields: `country_id`, `shipping_method`, `rule_type`, `status`
   
   - **New System:** `ShippingRule` model, `shipping_rules` table
     - Used by: Calculation system, API routes (`/api/shipping/*`)
     - Fields: `country_iso`, `shipping_mode_key`, `active`

2. **Calculation Flow:**
   ```
   User selects shipping → calculate_shipping_price() 
   → ShippingService.calculate_shipping() 
   → Queries ShippingRule (NEW SYSTEM)
   → Returns price
   ```

3. **Admin Flow:**
   ```
   Admin creates rule → admin_new_shipping_rule() 
   → Creates LegacyShippingRule (OLD SYSTEM)
   → Stored in shipping_rule table
   → ❌ NOT USED by calculation system
   ```

**Key Finding:** Admin UI and calculation system are **completely disconnected**.

---

### 8.2 Full List of Broken Areas

#### 🔴 Critical (System Breaking)

1. **Missing shipping_method extraction** (`app/__init__.py:5483`)
   - Create route will crash with `NameError`
   - **Fix:** Add `shipping_method = request.form.get('shipping_method', '').strip() or None`

2. **Admin rules not used by calculations**
   - Admin creates in legacy table, calculations read from new table
   - **Fix:** Migrate admin routes to use new ShippingRule model

3. **Duplicate route uses wrong model** (`app/__init__.py:5654`)
   - Queries new system but creates in old system
   - **Fix:** Use consistent model (preferably new system)

#### 🟡 High Priority (Functional Issues)

4. **Overlap checks ignore shipping_method**
   - Different methods can't overlap, but system allows it
   - **Fix:** Add shipping_method filter to overlap queries

5. **Field name mismatches**
   - `shipping_method` vs `shipping_mode_key`
   - `country_id` vs `country_iso`
   - `status` vs `active`
   - **Fix:** Standardize on new system field names

6. **Method name mismatch**
   - Legacy: 'ecommerce' → New: 'economy_plus'
   - **Fix:** Update mapping or standardize names

#### 🟢 Medium Priority (Data Integrity)

7. **Export doesn't include shipping_method**
   - CSV export missing shipping method column
   - **Fix:** Add shipping_method to export

8. **No validation for shipping_method in create route**
   - Edit route validates, create route doesn't (and can't - variable undefined)
   - **Fix:** Add validation after fixing extraction

---

### 8.3 Recommended Fixes in Priority Order

#### Priority 1: Fix Critical Bugs (Immediate)

1. **Fix missing shipping_method extraction**
   ```python
   # In admin_new_shipping_rule(), add after line 5412:
   shipping_method = request.form.get('shipping_method', '').strip() or None
   ```

2. **Fix duplicate route model mismatch**
   ```python
   # Change line 5654 from:
   original_rule = ShippingRule.query.get_or_404(rule_id)
   # To:
   original_rule = LegacyShippingRule.query.get_or_404(rule_id)
   # And add shipping_method to duplicate creation
   ```

#### Priority 2: Migrate Admin to New System (High Impact)

3. **Update admin routes to use new ShippingRule model**
   - Convert `country_id` → `country_iso` in handlers
   - Convert `shipping_method` → `shipping_mode_key` in handlers
   - Update overlap checks to use new model
   - Update templates to use new field names

4. **Add shipping_method filter to overlap checks**
   ```python
   # In both create and edit routes, add:
   if shipping_method:
       query = query.filter(LegacyShippingRule.shipping_method == shipping_method)
   ```

#### Priority 3: Data Migration (Medium Term)

5. **Create migration script**
   - Convert all LegacyShippingRule → ShippingRule
   - Map `country_id` → `country_iso`
   - Map `shipping_method` → `shipping_mode_key` (with name conversion)
   - Map `status` → `active`
   - Map `note` → `notes`

6. **Deprecate LegacyShippingRule**
   - Remove legacy model after migration
   - Update all references to use new model

#### Priority 4: Enhancements (Long Term)

7. **Add shipping_method to export**
8. **Add validation for shipping_method in create route**
9. **Update documentation**

---

### 8.4 System Status Summary

| Component | Status | Model Used | Issues |
|-----------|--------|------------|--------|
| **Calculation System** | ✅ Working | ShippingRule (NEW) | None |
| **API Routes** | ✅ Working | ShippingRule (NEW) | None |
| **Admin List** | ⚠️ Partial | LegacyShippingRule | Not used by calculations |
| **Admin Create** | ❌ Broken | LegacyShippingRule | Missing shipping_method extraction |
| **Admin Edit** | ⚠️ Partial | LegacyShippingRule | Not used by calculations |
| **Admin Delete** | ⚠️ Partial | LegacyShippingRule | Not used by calculations |
| **Admin Duplicate** | ❌ Broken | Mixed (wrong) | Queries new, creates old |
| **Admin Toggle** | ⚠️ Partial | LegacyShippingRule | Not used by calculations |

**Overall Status:** 🟡 **PARTIALLY FUNCTIONAL**
- Calculation system works correctly
- Admin UI is broken/disconnected
- Critical bugs prevent rule creation

---

**End of Report**

