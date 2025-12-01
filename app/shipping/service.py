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
        # Convert country name to ISO if needed
        if len(country_iso) > 3:
            from app.models.country import Country
            country = Country.query.filter(Country.name.ilike(f'%{country_iso}%')).first()
            if country:
                country_iso = country.code
            else:
                # Try to find by partial match
                country = Country.query.filter(Country.name.ilike(f'%{country_iso.split()[0]}%')).first()
                if country:
                    country_iso = country.code
        
        # Ensure weight is valid
        if total_weight_kg is None or total_weight_kg < 0:
            total_weight_kg = 0.0
        
        weight = Decimal(str(total_weight_kg))
        
        # Step 1: Try country-specific rules first
        country_rules = ShippingRule.query.filter(
            ShippingRule.country_iso == country_iso.upper(),
            ShippingRule.shipping_mode_key == shipping_mode_key,
            ShippingRule.active == True,
            ShippingRule.min_weight <= weight,
            ShippingRule.max_weight >= weight
        ).order_by(
            ShippingRule.priority.desc(),
            ShippingRule.created_at.asc()
        ).all()
        
        if country_rules:
            rule = country_rules[0]
            mode = rule.shipping_mode
            return {
                'shipping_fee_gmd': float(rule.price_gmd),
                'shipping_fee_display': f"D{float(rule.price_gmd):,.2f}",
                'currency': 'GMD',
                'delivery_time': rule.delivery_time or (mode.delivery_time_range if mode else 'N/A'),
                'mode': mode.label if mode else shipping_mode_key,
                'rule_id': rule.id,
                'available': True
            }
        
        # Step 2: Try global rules (country_iso = '*')
        global_rules = ShippingRule.query.filter(
            ShippingRule.country_iso == '*',
            ShippingRule.shipping_mode_key == shipping_mode_key,
            ShippingRule.active == True,
            ShippingRule.min_weight <= weight,
            ShippingRule.max_weight >= weight
        ).order_by(
            ShippingRule.priority.desc(),
            ShippingRule.created_at.asc()
        ).all()
        
        if global_rules:
            rule = global_rules[0]
            mode = rule.shipping_mode
            return {
                'shipping_fee_gmd': float(rule.price_gmd),
                'shipping_fee_display': f"D{float(rule.price_gmd):,.2f}",
                'currency': 'GMD',
                'delivery_time': rule.delivery_time or (mode.delivery_time_range if mode else 'N/A'),
                'mode': mode.label if mode else shipping_mode_key,
                'rule_id': rule.id,
                'available': True
            }
        
        # Step 3: No rule found
        return {
            'error': f"No shipping rule found for {country_iso}, {shipping_mode_key}, {total_weight_kg} kg",
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
                    f"Overlaps with existing rule ID {rule.id} "
                    f"({rule.min_weight}-{rule.max_weight} kg)"
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
        
        # Check for overlaps
        has_overlap, error_msg = ShippingService.validate_rule_overlap(
            country_iso, shipping_mode_key, Decimal(str(min_weight)), Decimal(str(max_weight))
        )
        if has_overlap:
            return None, error_msg
        
        # Create rule
        try:
            rule = ShippingRule(
                country_iso=country_iso.upper(),
                shipping_mode_key=shipping_mode_key,
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
            return rule, None
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error creating shipping rule: {str(e)}")
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

