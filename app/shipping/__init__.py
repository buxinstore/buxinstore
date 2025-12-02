"""
Shipping module for managing shipping methods and calculations.
"""

from app.shipping.constants import (
    SHIPPING_METHODS,
    get_shipping_method as get_shipping_method_const,
    get_all_shipping_methods as get_all_shipping_methods_const,
    get_shipping_method_ids,
    is_valid_shipping_method
)
from app.shipping.models import ShippingMode, ShippingRule
from app.shipping.service import ShippingService

def get_all_shipping_methods():
    """
    Get all shipping methods from database (active only).
    Falls back to constants if database is empty.
    """
    try:
        # Try to get from database first
        db_methods = ShippingMode.query.filter_by(active=True).order_by(ShippingMode.id).all()
        
        if db_methods:
            # Convert database models to dict format compatible with constants
            methods = []
            for mode in db_methods:
                methods.append({
                    'id': mode.key,
                    'label': mode.label,
                    'short_label': mode.label,  # Use label as short_label if not available
                    'description': mode.description or '',
                    'guarantee': mode.delivery_time_range or '',
                    'notes': [],
                    'color': mode.color or 'blue',
                    'icon': mode.icon or '📦'
                })
            return methods
        
        # Fallback to constants if database is empty
        return get_all_shipping_methods_const()
    except Exception:
        # If database query fails, fallback to constants
        return get_all_shipping_methods_const()

def get_shipping_method(method_id: str):
    """
    Get shipping method by ID from database or constants.
    """
    try:
        # Try database first
        mode = ShippingMode.query.filter_by(key=method_id, active=True).first()
        if mode:
            return {
                'id': mode.key,
                'label': mode.label,
                'short_label': mode.label,
                'description': mode.description or '',
                'guarantee': mode.delivery_time_range or '',
                'notes': [],
                'color': mode.color or 'blue',
                'icon': mode.icon or '📦'
            }
    except Exception:
        pass
    
    # Fallback to constants
    return get_shipping_method_const(method_id)

__all__ = [
    'SHIPPING_METHODS',
    'get_shipping_method',
    'get_all_shipping_methods',
    'get_shipping_method_ids',
    'is_valid_shipping_method',
    'ShippingMode',
    'ShippingRule',
    'ShippingService'
]

