"""
Seed Shipping Data
Import shipping rules from JSON and convert USD to GMD.
"""

import os
import sys
import json
from decimal import Decimal

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.extensions import db
from app.models.country import Country
# Don't import ShippingMode/ShippingRule models to avoid conflicts - use raw SQL instead

# USD to GMD conversion rate (update this as needed)
# As of 2025, approximate rate: 1 USD = 67 GMD
USD_TO_GMD_RATE = float(os.environ.get('USD_TO_GMD_RATE', '67.0'))


def get_country_iso(country_name_or_code):
    """Convert country name or code to ISO code."""
    # Map of country names/codes to ISO codes
    country_map = {
        'SEN': 'SEN', 'Senegal': 'SEN',
        'CIV': 'CIV', 'Côte d\'Ivoire': 'CIV', 'Ivory Coast': 'CIV',
        'GMB': 'GMB', 'Gambia': 'GMB', 'The Gambia': 'GMB',
        'MLI': 'MLI', 'Mali': 'MLI',
        'BFA': 'BFA', 'Burkina Faso': 'BFA',
        'SLE': 'SLE', 'Sierra Leone': 'SLE',
        'UGA': 'UGA', 'Uganda': 'UGA'
    }
    
    # Try direct lookup
    if country_name_or_code in country_map:
        return country_map[country_name_or_code]
    
    # Try database lookup
    country = Country.query.filter(
        db.or_(
            Country.code == country_name_or_code.upper(),
            Country.name.ilike(f'%{country_name_or_code}%')
        )
    ).first()
    
    if country:
        return country.code
    
    # Default to uppercase if 3 characters
    if len(country_name_or_code) == 3:
        return country_name_or_code.upper()
    
    return None


def convert_usd_to_gmd(usd_amount):
    """Convert USD amount to GMD."""
    return Decimal(str(usd_amount * USD_TO_GMD_RATE)).quantize(Decimal('0.01'))


def seed_shipping_modes():
    """Seed shipping modes if they don't exist."""
    # Use direct table access to avoid model conflicts
    from sqlalchemy import Table, MetaData
    metadata = MetaData()
    shipping_modes_table = Table('shipping_modes', metadata, autoload_with=db.engine)
    
    modes_data = [
        {
            'key': 'express',
            'label': 'DHL Express / FedEx International (Fast, 3–7 days)',
            'description': 'Fastest delivery. Fully tracked from China to your location.',
            'delivery_time_range': '3–7 days',
            'icon': '🚀',
            'color': 'red',
            'active': True
        },
        {
            'key': 'economy_plus',
            'label': 'DHL eCommerce / DHL Global Forwarding (Medium, 10–20 days)',
            'description': 'Reliable shipping with tracking. Delivered by DHL partner or Post Office in your country.',
            'delivery_time_range': '10–20 days',
            'icon': '📦',
            'color': 'yellow',
            'active': True
        },
        {
            'key': 'economy',
            'label': 'AliExpress Economy Mail (Slow, 20–60 days)',
            'description': 'Low-cost shipping. Parcel will be sent to your local Post Office for pickup.',
            'delivery_time_range': '20–60 days',
            'icon': '📮',
            'color': 'green',
            'active': True
        }
    ]
    
    for mode_data in modes_data:
        # Check if mode exists using raw SQL to avoid model conflicts
        result = db.session.execute(
            db.text("SELECT id FROM shipping_modes WHERE key = :key"),
            {"key": mode_data['key']}
        ).first()
        
        if not result:
            # Insert new mode
            db.session.execute(
                db.text("""
                    INSERT INTO shipping_modes (key, label, description, delivery_time_range, icon, color, active, created_at, updated_at)
                    VALUES (:key, :label, :description, :delivery_time_range, :icon, :color, :active, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """),
                mode_data
            )
            print(f"[OK] Created shipping mode: {mode_data['key']}")
        else:
            # Update existing mode
            db.session.execute(
                db.text("""
                    UPDATE shipping_modes 
                    SET label = :label, description = :description, delivery_time_range = :delivery_time_range,
                        icon = :icon, color = :color, active = :active, updated_at = CURRENT_TIMESTAMP
                    WHERE key = :key
                """),
                mode_data
            )
            print(f"[OK] Updated shipping mode: {mode_data['key']}")
    
    db.session.commit()


def seed_shipping_rules_from_json(json_data, weight_multipliers=None):
    """
    Seed shipping rules from JSON data.
    
    Args:
        json_data: List of rule dictionaries with keys:
            - country: ISO code or country name
            - mode: shipping mode key ('express', 'economy_plus', 'economy')
            - min_weight: minimum weight in kg
            - max_weight: maximum weight in kg
            - price_usd_min: minimum price in USD
            - price_usd_max: maximum price in USD
            - delivery: delivery time string
        weight_multipliers: Dict mapping weight ranges to multipliers
            e.g., {'0.5-1.0': 1.6, '1.0-2.0': 2.5}
    """
    if weight_multipliers is None:
        weight_multipliers = {
            '0.5-1.0': 1.6,
            '1.0-2.0': 2.5
        }
    
    created_count = 0
    updated_count = 0
    error_count = 0
    
    for rule_data in json_data:
        try:
            country_iso = get_country_iso(rule_data.get('country', ''))
            if not country_iso:
                print(f"[WARN] Skipping rule: Country '{rule_data.get('country')}' not found")
                error_count += 1
                continue
            
            mode_key = rule_data.get('mode', '').strip()
            if mode_key not in ['express', 'economy_plus', 'economy']:
                print(f"[WARN] Skipping rule: Invalid mode '{mode_key}'")
                error_count += 1
                continue
            
            # Check if mode exists using raw SQL
            mode_check = db.session.execute(
                db.text("SELECT id FROM shipping_modes WHERE key = :key"),
                {"key": mode_key}
            ).first()
            if not mode_check:
                print(f"[WARN] Skipping rule: Shipping mode '{mode_key}' not found. Run seed_shipping_modes() first.")
                error_count += 1
                continue
            
            # Get base weight range (0-0.5 kg)
            base_min = float(rule_data.get('min_weight', 0.0))
            base_max = float(rule_data.get('max_weight', 0.5))
            
            # Calculate price (use midpoint of USD range)
            price_usd_min = float(rule_data.get('price_usd_min', 0))
            price_usd_max = float(rule_data.get('price_usd_max', 0))
            price_usd_mid = (price_usd_min + price_usd_max) / 2.0
            price_gmd = convert_usd_to_gmd(price_usd_mid)
            
            delivery_time = rule_data.get('delivery', '')
            
            # Create rules for different weight brackets
            weight_brackets = [
                (base_min, base_max, 1.0),  # 0-0.5 kg: use base price
                (base_max, base_max * 2, weight_multipliers.get('0.5-1.0', 1.6)),  # 0.5-1.0 kg
                (base_max * 2, base_max * 4, weight_multipliers.get('1.0-2.0', 2.5))  # 1.0-2.0 kg
            ]
            
            for min_w, max_w, multiplier in weight_brackets:
                rule_price_gmd = price_gmd * Decimal(str(multiplier))
                
                # Check if rule already exists using raw SQL
                existing_rule = db.session.execute(
                    db.text("""
                        SELECT id FROM shipping_rules 
                        WHERE country_iso = :country_iso 
                        AND shipping_mode_key = :mode_key 
                        AND min_weight = :min_w 
                        AND max_weight = :max_w
                    """),
                    {
                        "country_iso": country_iso,
                        "mode_key": mode_key,
                        "min_w": str(min_w),
                        "max_w": str(max_w)
                    }
                ).first()
                
                notes = f"Seeded: {min_w}-{max_w}kg bracket (USD {price_usd_mid:.2f} * {multiplier} = GMD {rule_price_gmd})"
                
                if existing_rule:
                    # Update existing rule
                    db.session.execute(
                        db.text("""
                            UPDATE shipping_rules 
                            SET price_gmd = :price_gmd, delivery_time = :delivery_time, active = true, updated_at = CURRENT_TIMESTAMP
                            WHERE id = :id
                        """),
                        {
                            "id": existing_rule.id,
                            "price_gmd": str(rule_price_gmd),
                            "delivery_time": delivery_time
                        }
                    )
                    updated_count += 1
                    print(f"[OK] Updated rule: {country_iso} {mode_key} {min_w}-{max_w}kg = D{rule_price_gmd}")
                else:
                    # Create new rule
                    db.session.execute(
                        db.text("""
                            INSERT INTO shipping_rules 
                            (country_iso, shipping_mode_key, min_weight, max_weight, price_gmd, delivery_time, priority, active, notes, created_at, updated_at)
                            VALUES (:country_iso, :mode_key, :min_w, :max_w, :price_gmd, :delivery_time, 0, true, :notes, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """),
                        {
                            "country_iso": country_iso,
                            "mode_key": mode_key,
                            "min_w": str(min_w),
                            "max_w": str(max_w),
                            "price_gmd": str(rule_price_gmd),
                            "delivery_time": delivery_time,
                            "notes": notes
                        }
                    )
                    created_count += 1
                    print(f"[OK] Created rule: {country_iso} {mode_key} {min_w}-{max_w}kg = D{rule_price_gmd}")
        
        except Exception as e:
            print(f"[ERROR] Error processing rule: {str(e)}")
            error_count += 1
            continue
    
    db.session.commit()
    print(f"\nSummary: Created {created_count}, Updated {updated_count}, Errors {error_count}")


def main():
    """Main function to seed shipping data."""
    import sys
    # Fix Windows console encoding for emojis
    if sys.platform == 'win32':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    
    app = create_app()
    
    with app.app_context():
        print("Starting shipping data seed...")
        print(f"Using USD to GMD rate: {USD_TO_GMD_RATE}")
        print()
        
        # Seed shipping modes
        print("Seeding shipping modes...")
        seed_shipping_modes()
        print()
        
        # Load JSON data
        json_file = os.path.join(os.path.dirname(__file__), '..', 'data', 'shipping_seed_data.json')
        
        if os.path.exists(json_file):
            print(f"Loading seed data from {json_file}...")
            with open(json_file, 'r', encoding='utf-8') as f:
                seed_data = json.load(f)
            
            print("Seeding shipping rules...")
            seed_shipping_rules_from_json(seed_data)
        else:
            print(f"Seed data file not found: {json_file}")
            print("Creating sample seed data...")
            
            # Sample seed data from requirements
            sample_data = [
                {"country": "SEN", "mode": "express", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 32, "price_usd_max": 38, "delivery": "3–7 days"},
                {"country": "CIV", "mode": "express", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 30, "price_usd_max": 36, "delivery": "3–7 days"},
                {"country": "GMB", "mode": "express", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 35, "price_usd_max": 40, "delivery": "3–7 days"},
                {"country": "MLI", "mode": "express", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 33, "price_usd_max": 38, "delivery": "3–7 days"},
                {"country": "BFA", "mode": "express", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 34, "price_usd_max": 40, "delivery": "3–7 days"},
                {"country": "SLE", "mode": "express", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 36, "price_usd_max": 42, "delivery": "3–7 days"},
                {"country": "UGA", "mode": "express", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 32, "price_usd_max": 38, "delivery": "3–7 days"},
                {"country": "SEN", "mode": "economy", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 4, "price_usd_max": 6, "delivery": "20–60 days"},
                {"country": "CIV", "mode": "economy", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 4, "price_usd_max": 6, "delivery": "20–60 days"},
                {"country": "GMB", "mode": "economy", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 5, "price_usd_max": 7, "delivery": "20–60 days"},
                {"country": "MLI", "mode": "economy", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 4, "price_usd_max": 6, "delivery": "20–60 days"},
                {"country": "BFA", "mode": "economy", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 5, "price_usd_max": 7, "delivery": "20–60 days"},
                {"country": "SLE", "mode": "economy", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 5, "price_usd_max": 7, "delivery": "20–60 days"},
                {"country": "UGA", "mode": "economy", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 4, "price_usd_max": 6, "delivery": "20–60 days"},
                {"country": "SEN", "mode": "economy_plus", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 12, "price_usd_max": 18, "delivery": "10–20 days"},
                {"country": "CIV", "mode": "economy_plus", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 11, "price_usd_max": 17, "delivery": "10–20 days"},
                {"country": "GMB", "mode": "economy_plus", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 13, "price_usd_max": 20, "delivery": "10–20 days"},
                {"country": "MLI", "mode": "economy_plus", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 12, "price_usd_max": 18, "delivery": "10–20 days"},
                {"country": "BFA", "mode": "economy_plus", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 12, "price_usd_max": 19, "delivery": "10–20 days"},
                {"country": "SLE", "mode": "economy_plus", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 13, "price_usd_max": 20, "delivery": "10–20 days"},
                {"country": "UGA", "mode": "economy_plus", "min_weight": 0.0, "max_weight": 0.5, "price_usd_min": 11, "price_usd_max": 17, "delivery": "10–20 days"}
            ]
            
            seed_shipping_rules_from_json(sample_data)
        
        print()
        print("Shipping data seed completed!")


if __name__ == '__main__':
    main()

