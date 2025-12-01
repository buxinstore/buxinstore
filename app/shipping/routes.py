"""
Shipping API Routes
API endpoints for shipping calculations and management.
"""

from flask import Blueprint, request, jsonify, current_app
from app.extensions import csrf
from app.shipping.service import ShippingService
from app.shipping.models import ShippingMode, ShippingRule
from app.extensions import db
from functools import wraps
from flask_login import current_user

shipping_bp = Blueprint('shipping', __name__, url_prefix='/api/shipping')


def admin_required_api(f):
    """Decorator to require admin access for API endpoints."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or (not current_user.is_admin and current_user.role != 'admin'):
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated_function


@shipping_bp.route('/calculate', methods=['POST'])
@csrf.exempt
def calculate_shipping():
    """
    Calculate shipping price for given country, mode, and weight.
    
    Request:
        {
            "country": "Gambia" or "GMB",
            "shipping_mode": "express",
            "total_weight": 0.5
        }
    
    Response:
        {
            "shipping_fee_gmd": 5000.00,
            "shipping_fee_display": "D5,000.00",
            "currency": "GMD",
            "delivery_time": "3–7 days",
            "mode": "DHL Express / FedEx International",
            "rule_id": 123
        }
    """
    try:
        data = request.get_json() if request.is_json else request.form.to_dict()
        
        country = data.get('country', '').strip()
        shipping_mode = data.get('shipping_mode', '').strip()
        total_weight = data.get('total_weight', 0.0)
        
        if not country:
            return jsonify({'error': 'country is required'}), 400
        if not shipping_mode:
            return jsonify({'error': 'shipping_mode is required'}), 400
        
        try:
            total_weight = float(total_weight)
        except (ValueError, TypeError):
            return jsonify({'error': 'total_weight must be a number'}), 400
        
        result = ShippingService.calculate_shipping(
            country_iso=country,
            shipping_mode_key=shipping_mode,
            total_weight_kg=total_weight
        )
        
        if 'error' in result:
            return jsonify(result), 404
        
        return jsonify(result), 200
        
    except Exception as e:
        current_app.logger.error(f'Error calculating shipping: {e}')
        return jsonify({'error': str(e)}), 500


@shipping_bp.route('/modes', methods=['GET'])
@csrf.exempt
def get_modes():
    """
    Get all active shipping modes with metadata.
    
    Response:
        [
            {
                "id": 1,
                "key": "express",
                "label": "DHL Express / FedEx International (Fast, 3–7 days)",
                "description": "...",
                "delivery_time_range": "3–7 days",
                "icon": "🚀",
                "color": "red",
                "active": true
            },
            ...
        ]
    """
    try:
        modes = ShippingService.get_active_modes()
        return jsonify(modes), 200
    except Exception as e:
        current_app.logger.error(f'Error getting shipping modes: {e}')
        return jsonify({'error': str(e)}), 500


# Admin CRUD endpoints
@shipping_bp.route('/admin/rules', methods=['GET'])
@admin_required_api
def list_rules():
    """List all shipping rules with optional filters."""
    try:
        country_iso = request.args.get('country_iso')
        mode_key = request.args.get('mode_key')
        active = request.args.get('active')
        
        query = ShippingRule.query
        
        if country_iso:
            query = query.filter(ShippingRule.country_iso == country_iso.upper())
        if mode_key:
            query = query.filter(ShippingRule.shipping_mode_key == mode_key)
        if active is not None:
            query = query.filter(ShippingRule.active == (active.lower() == 'true'))
        
        rules = query.order_by(
            ShippingRule.country_iso,
            ShippingRule.shipping_mode_key,
            ShippingRule.priority.desc(),
            ShippingRule.min_weight
        ).all()
        
        return jsonify([rule.to_dict() for rule in rules]), 200
    except Exception as e:
        current_app.logger.error(f'Error listing shipping rules: {e}')
        return jsonify({'error': str(e)}), 500


@shipping_bp.route('/admin/rules', methods=['POST'])
@admin_required_api
def create_rule():
    """Create a new shipping rule."""
    try:
        data = request.get_json()
        
        rule, error = ShippingService.create_rule(
            country_iso=data.get('country_iso'),
            shipping_mode_key=data.get('shipping_mode_key'),
            min_weight=float(data.get('min_weight', 0)),
            max_weight=float(data.get('max_weight', 0)),
            price_gmd=float(data.get('price_gmd', 0)),
            delivery_time=data.get('delivery_time'),
            priority=int(data.get('priority', 0)),
            notes=data.get('notes'),
            active=bool(data.get('active', True))
        )
        
        if error:
            return jsonify({'error': error}), 400
        
        return jsonify(rule.to_dict()), 201
    except Exception as e:
        current_app.logger.error(f'Error creating shipping rule: {e}')
        return jsonify({'error': str(e)}), 500


@shipping_bp.route('/admin/rules/<int:rule_id>', methods=['GET'])
@admin_required_api
def get_rule(rule_id):
    """Get a specific shipping rule."""
    try:
        rule = ShippingRule.query.get(rule_id)
        if not rule:
            return jsonify({'error': 'Rule not found'}), 404
        return jsonify(rule.to_dict()), 200
    except Exception as e:
        current_app.logger.error(f'Error getting shipping rule: {e}')
        return jsonify({'error': str(e)}), 500


@shipping_bp.route('/admin/rules/<int:rule_id>', methods=['PUT'])
@admin_required_api
def update_rule(rule_id):
    """Update a shipping rule."""
    try:
        data = request.get_json()
        
        rule, error = ShippingService.update_rule(
            rule_id=rule_id,
            country_iso=data.get('country_iso'),
            shipping_mode_key=data.get('shipping_mode_key'),
            min_weight=float(data.get('min_weight')) if data.get('min_weight') is not None else None,
            max_weight=float(data.get('max_weight')) if data.get('max_weight') is not None else None,
            price_gmd=float(data.get('price_gmd')) if data.get('price_gmd') is not None else None,
            delivery_time=data.get('delivery_time'),
            priority=int(data.get('priority')) if data.get('priority') is not None else None,
            notes=data.get('notes'),
            active=bool(data.get('active')) if data.get('active') is not None else None
        )
        
        if error:
            return jsonify({'error': error}), 400
        
        return jsonify(rule.to_dict()), 200
    except Exception as e:
        current_app.logger.error(f'Error updating shipping rule: {e}')
        return jsonify({'error': str(e)}), 500


@shipping_bp.route('/admin/rules/<int:rule_id>', methods=['DELETE'])
@admin_required_api
def delete_rule(rule_id):
    """Delete a shipping rule."""
    try:
        rule = ShippingRule.query.get(rule_id)
        if not rule:
            return jsonify({'error': 'Rule not found'}), 404
        
        db.session.delete(rule)
        db.session.commit()
        
        return jsonify({'message': 'Rule deleted successfully'}), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Error deleting shipping rule: {e}')
        return jsonify({'error': str(e)}), 500


@shipping_bp.route('/admin/modes', methods=['GET'])
@admin_required_api
def list_modes():
    """List all shipping modes (admin)."""
    try:
        modes = ShippingMode.query.order_by(ShippingMode.id).all()
        return jsonify([mode.to_dict() for mode in modes]), 200
    except Exception as e:
        current_app.logger.error(f'Error listing shipping modes: {e}')
        return jsonify({'error': str(e)}), 500


@shipping_bp.route('/admin/modes', methods=['POST'])
@admin_required_api
def create_mode():
    """Create a new shipping mode."""
    try:
        data = request.get_json()
        
        mode = ShippingMode(
            key=data.get('key'),
            label=data.get('label'),
            description=data.get('description'),
            delivery_time_range=data.get('delivery_time_range'),
            icon=data.get('icon'),
            color=data.get('color'),
            active=bool(data.get('active', True))
        )
        
        db.session.add(mode)
        db.session.commit()
        
        return jsonify(mode.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Error creating shipping mode: {e}')
        return jsonify({'error': str(e)}), 500


@shipping_bp.route('/admin/modes/<int:mode_id>', methods=['PUT'])
@admin_required_api
def update_mode(mode_id):
    """Update a shipping mode."""
    try:
        mode = ShippingMode.query.get(mode_id)
        if not mode:
            return jsonify({'error': 'Mode not found'}), 404
        
        data = request.get_json()
        
        if 'label' in data:
            mode.label = data['label']
        if 'description' in data:
            mode.description = data['description']
        if 'delivery_time_range' in data:
            mode.delivery_time_range = data['delivery_time_range']
        if 'icon' in data:
            mode.icon = data['icon']
        if 'color' in data:
            mode.color = data['color']
        if 'active' in data:
            mode.active = bool(data['active'])
        
        db.session.commit()
        
        return jsonify(mode.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Error updating shipping mode: {e}')
        return jsonify({'error': str(e)}), 500

