"""
Shipping Service
Business logic for shipping calculations and rule management.
"""

from decimal import Decimal
from typing import Dict, Optional, List, Tuple
from flask import current_app
from app.extensions import db
from app.shipping.models import ShippingMode, ShippingRule
from sqlalchemy import and_, or_


class ShippingService:
    """Service class for shipping calculations and rule management."""
    
    @staticmethod
    def calculate_shipping(
        country_iso: str,
        shipping_mode_key: str,
        total_weight_kg: float
    ) -> Dict:
        """
        Calculate shipping price for given country, mode, and weight.
        
        Args:
            country_iso: Country ISO code (e.g., 'GMB', 'SEN') or country name
            shipping_mode_key: Shipping mode key ('express', 'economy_plus', 'economy')
            total_weight_kg: Total weight in kilograms
            
        Returns:
            Dict with shipping fee, delivery time, mode info, and rule_id
            or error message if no rule found
        """
        # Ensure country_iso is a string
        if not country_iso:
            country_iso = '*'
        
        # Convert country name to ISO if needed (if longer than 3 chars, it's likely a name)
        original_country_iso = country_iso
        if len(country_iso) > 3 and country_iso != '*':
            from app.models.country import Country
            country = Country.query.filter(Country.name.ilike(f'%{country_iso}%')).first()
            if country:
                country_iso = country.code
            else:
                # Try to find by partial match
                country = Country.query.filter(Country.name.ilike(f'%{country_iso.split()[0]}%')).first()
                if country:
                    country_iso = country.code
        
        # Normalize to uppercase
        country_iso = country_iso.upper() if country_iso else '*'
        
        # Handle 2-letter vs 3-letter ISO code mismatch
        # Country model uses 2-letter codes (GM), but ShippingRule might have 2 or 3-letter codes
        # Try both formats if it's a 2-letter code
        country_iso_variants = [country_iso]
        if len(country_iso) == 2 and country_iso != '*':
            # Try to convert 2-letter to 3-letter (common mappings)
            iso2_to_iso3 = {
                'GM': 'GMB',  # Gambia
                'SN': 'SEN',  # Senegal
                'CI': 'CIV',  # Côte d'Ivoire
                'GH': 'GHA',  # Ghana
                'NG': 'NGA',  # Nigeria
                'KE': 'KEN',  # Kenya
                'UG': 'UGA',  # Uganda
                'TZ': 'TZA',  # Tanzania
            }
            if country_iso in iso2_to_iso3:
                country_iso_variants.append(iso2_to_iso3[country_iso])
        
        # Ensure weight is valid
        if total_weight_kg is None or total_weight_kg < 0:
            total_weight_kg = 0.0
        
        weight = Decimal(str(total_weight_kg))
        base_weight = Decimal('0.5')  # Base weight for pricing (0.5 kg)
        
        # NEW SIMPLIFIED CALCULATION:
        # Find the base price for 0.5kg, then calculate: (total_weight_kg / 0.5) × base_price
        
        # Step 1: Try to find base price (0.5kg rule) for country-specific rules
        base_rule = None
        for iso_variant in country_iso_variants:
            # Look for a rule with min_weight = 0.5 (or close to it, like 0.0-0.5 or 0.5-0.5)
            # We'll accept any rule where min_weight <= 0.5 and the rule is for this method
            rules = ShippingRule.query.filter(
                ShippingRule.country_iso == iso_variant,
                ShippingRule.shipping_mode_key == shipping_mode_key,
                ShippingRule.active == True,
                ShippingRule.min_weight <= base_weight
            ).order_by(
                ShippingRule.min_weight.asc(),  # Prefer rules starting at 0.5 or lower
                ShippingRule.priority.desc(),
                ShippingRule.created_at.asc()
            ).all()
            
            if rules:
                # Use the first rule (should be the one with min_weight closest to 0.5)
                base_rule = rules[0]
                break
        
        # Step 2: If no country-specific rule found, try global rules
        if not base_rule and country_iso != '*':
            global_rules = ShippingRule.query.filter(
                ShippingRule.country_iso == '*',
                ShippingRule.shipping_mode_key == shipping_mode_key,
                ShippingRule.active == True,
                ShippingRule.min_weight <= base_weight
            ).order_by(
                ShippingRule.min_weight.asc(),
                ShippingRule.priority.desc(),
                ShippingRule.created_at.asc()
            ).all()
            
            if global_rules:
                base_rule = global_rules[0]
        
        # Step 3: Calculate shipping price using the formula: (weight / 0.5) × base_price
        if base_rule:
            mode = base_rule.shipping_mode
            base_price = float(base_rule.price_gmd)
            
            # Calculate: (total_weight_kg / 0.5) × base_price
            calculated_price = (float(total_weight_kg) / 0.5) * base_price
            
            # Debug logging
            from flask import current_app
            if current_app:
                current_app.logger.debug(
                    f"ShippingService.calculate_shipping: country_iso={country_iso}, mode={shipping_mode_key}, "
                    f"weight={total_weight_kg}kg, base_price={base_price} (from rule {base_rule.id}), "
                    f"calculated_price={calculated_price} (formula: ({total_weight_kg} / 0.5) × {base_price})"
                )
            
            return {
                'shipping_fee_gmd': calculated_price,
                'shipping_fee_display': f"D{calculated_price:,.2f}",
                'currency': 'GMD',
                'delivery_time': base_rule.delivery_time or (mode.delivery_time_range if mode else 'N/A'),
                'mode': mode.label if mode else shipping_mode_key,
                'rule_id': base_rule.id,
                'available': True
            }
        
        # Step 4: No base rule found
        from flask import current_app
        if current_app:
            # Check if any rules exist at all for this country/mode
            all_rules = []
            for iso_variant in country_iso_variants:
                rules = ShippingRule.query.filter(
                    ShippingRule.country_iso == iso_variant,
                    ShippingRule.shipping_mode_key == shipping_mode_key,
                    ShippingRule.active == True
                ).all()
                all_rules.extend(rules)
            
            current_app.logger.warning(
                f"No shipping base price rule found: country_iso={country_iso}, mode={shipping_mode_key}. "
                f"Found {len(all_rules)} active rules for this country/mode. "
                f"Need a rule with min_weight <= 0.5kg to use as base price."
            )
        
        return {
            'error': f"No shipping base price rule found for {country_iso}, {shipping_mode_key}. Need a rule with base price for 0.5kg.",
            'available': False
        }
    
    @staticmethod
    def get_active_modes() -> List[Dict]:
        """Get all active shipping modes with metadata."""
        modes = ShippingMode.query.filter(ShippingMode.active == True).order_by(ShippingMode.id).all()
        return [mode.to_dict() for mode in modes]
    
    @staticmethod
    def validate_rule_overlap(
        country_iso: str,
        shipping_mode_key: str,
        min_weight: Decimal,
        max_weight: Decimal,
        exclude_rule_id: Optional[int] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if a new rule would overlap with existing rules.
        
        Returns:
            Tuple of (has_overlap: bool, error_message: Optional[str])
        """
        # Find all rules for the same country and mode
        query = ShippingRule.query.filter(
            ShippingRule.country_iso == country_iso.upper(),
            ShippingRule.shipping_mode_key == shipping_mode_key
        )
        
        if exclude_rule_id:
            query = query.filter(ShippingRule.id != exclude_rule_id)
        
        existing_rules = query.all()
        
        for rule in existing_rules:
            # Check if weight ranges overlap
            # Ranges overlap if: min1 <= max2 AND min2 <= max1
            rule_min = Decimal(str(rule.min_weight))
            rule_max = Decimal(str(rule.max_weight))
            
            if min_weight <= rule_max and rule_min <= max_weight:
                return True, (
                    f"A base price rule already exists for this country and shipping method (Rule ID {rule.id}). "
                    f"Only one base price per country/method is needed. Please edit the existing rule instead."
                )
        
        return False, None
    
    @staticmethod
    def create_rule(
        country_iso: str,
        shipping_mode_key: str,
        min_weight: float,
        max_weight: float,
        price_gmd: float,
        delivery_time: Optional[str] = None,
        priority: int = 0,
        notes: Optional[str] = None,
        active: bool = True
    ) -> Tuple[Optional[ShippingRule], Optional[str]]:
        """
        Create a new shipping rule with validation.
        
        Returns:
            Tuple of (rule: Optional[ShippingRule], error: Optional[str])
        """
        # Validate inputs
        if min_weight >= max_weight:
            return None, "min_weight must be less than max_weight"
        
        if price_gmd < 0:
            return None, "price_gmd must be >= 0"
        
        # Check if mode exists
        mode = ShippingMode.query.filter_by(key=shipping_mode_key).first()
        if not mode:
            return None, f"Shipping mode '{shipping_mode_key}' not found"
        
        # Log before creating rule for debugging
        current_app.logger.info(f"ShippingService.create_rule called: mode_key={shipping_mode_key}, country={country_iso}, price={price_gmd}, min_weight={min_weight}, max_weight={max_weight}")
        
        # Check for overlaps
        has_overlap, error_msg = ShippingService.validate_rule_overlap(
            country_iso, shipping_mode_key, Decimal(str(min_weight)), Decimal(str(max_weight))
        )
        if has_overlap:
            return None, error_msg
        
        # Create rule
        # CRITICAL: Use shipping_mode_key, NEVER use shipping_method as a variable
        try:
            # Double-check that shipping_mode_key is set and not shipping_method
            if 'shipping_method' in locals() or 'shipping_method' in globals():
                current_app.logger.error("CRITICAL: shipping_method variable detected in scope - this should not happen!")
                return None, "Internal error: shipping_method variable detected"
            
            rule = ShippingRule(
                country_iso=country_iso.upper(),
                shipping_mode_key=shipping_mode_key,  # Use shipping_mode_key, NOT shipping_method
                min_weight=Decimal(str(min_weight)),
                max_weight=Decimal(str(max_weight)),
                price_gmd=Decimal(str(price_gmd)),
                delivery_time=delivery_time,
                priority=priority,
                notes=notes,
                active=active
            )
            db.session.add(rule)
            db.session.commit()
            current_app.logger.info(f"Shipping rule created successfully: id={rule.id}, mode_key={shipping_mode_key}, country={country_iso}")
            return rule, None
        except NameError as ne:
            # Specifically catch NameError to provide better debugging
            db.session.rollback()
            import traceback
            error_traceback = traceback.format_exc()
            current_app.logger.error(f"NameError in ShippingService.create_rule: {ne}\n{error_traceback}", exc_info=True)
            if 'shipping_method' in str(ne):
                return None, f"Internal code error: shipping_method variable referenced. Traceback logged."
            return None, f"NameError: {str(ne)}"
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error creating shipping rule: {str(e)}", exc_info=True)
            # Check if error message contains shipping_method
            if 'shipping_method' in str(e).lower():
                return None, "Internal code error: shipping_method variable referenced. Traceback logged."
            return None, str(e)
    
    @staticmethod
    def update_rule(
        rule_id: int,
        country_iso: Optional[str] = None,
        shipping_mode_key: Optional[str] = None,
        min_weight: Optional[float] = None,
        max_weight: Optional[float] = None,
        price_gmd: Optional[float] = None,
        delivery_time: Optional[str] = None,
        priority: Optional[int] = None,
        notes: Optional[str] = None,
        active: Optional[bool] = None
    ) -> Tuple[Optional[ShippingRule], Optional[str]]:
        """
        Update an existing shipping rule with validation.
        
        Returns:
            Tuple of (rule: Optional[ShippingRule], error: Optional[str])
        """
        rule = ShippingRule.query.get(rule_id)
        if not rule:
            return None, "Rule not found"
        
        # Update fields
        if country_iso is not None:
            rule.country_iso = country_iso.upper()
        if shipping_mode_key is not None:
            # Check if mode exists
            mode = ShippingMode.query.filter_by(key=shipping_mode_key).first()
            if not mode:
                return None, f"Shipping mode '{shipping_mode_key}' not found"
            rule.shipping_mode_key = shipping_mode_key
        
        # Get updated values for overlap check
        check_country = rule.country_iso
        check_mode = rule.shipping_mode_key
        check_min = Decimal(str(min_weight)) if min_weight is not None else rule.min_weight
        check_max = Decimal(str(max_weight)) if max_weight is not None else rule.max_weight
        
        if min_weight is not None:
            rule.min_weight = Decimal(str(min_weight))
        if max_weight is not None:
            rule.max_weight = Decimal(str(max_weight))
        
        # Validate weight range
        if float(rule.min_weight) >= float(rule.max_weight):
            return None, "min_weight must be less than max_weight"
        
        # Check for overlaps (excluding this rule)
        has_overlap, error_msg = ShippingService.validate_rule_overlap(
            check_country, check_mode, check_min, check_max, exclude_rule_id=rule_id
        )
        if has_overlap:
            return None, error_msg
        
        if price_gmd is not None:
            if price_gmd < 0:
                return None, "price_gmd must be >= 0"
            rule.price_gmd = Decimal(str(price_gmd))
        if delivery_time is not None:
            rule.delivery_time = delivery_time
        if priority is not None:
            rule.priority = priority
        if notes is not None:
            rule.notes = notes
        if active is not None:
            rule.active = active
        
        try:
            db.session.commit()
            return rule, None
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating shipping rule: {str(e)}")
            return None, str(e)

