"""
Shipping Models
Database models for shipping modes and rules.
"""

from datetime import datetime
from app.extensions import db
from sqlalchemy import CheckConstraint, Index, ForeignKey


class ShippingMode(db.Model):
    """
    Shipping mode definitions (Express, DHL eCommerce, AliExpress Economy).
    """
    __tablename__ = 'shipping_modes'
    
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)  # 'express', 'economy_plus', 'economy'
    label = db.Column(db.String(255), nullable=False)  # Display name
    description = db.Column(db.Text, nullable=True)  # Full description
    delivery_time_range = db.Column(db.String(100), nullable=True)  # e.g., "3–7 days"
    icon = db.Column(db.String(50), nullable=True)  # Emoji or icon identifier
    color = db.Column(db.String(50), nullable=True)  # Color for UI
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships - commented out to avoid conflicts with old ShippingRule model
    # Can be accessed via ShippingRule.query.filter_by(shipping_mode_key=self.key)
    # rules = db.relationship('ShippingRule', backref='shipping_mode', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<ShippingMode {self.key}: {self.label}>'
    
    def to_dict(self):
        """Convert shipping mode to dictionary."""
        return {
            'id': self.id,
            'key': self.key,
            'label': self.label,
            'description': self.description,
            'delivery_time_range': self.delivery_time_range,
            'icon': self.icon,
            'color': self.color,
            'active': self.active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class ShippingRule(db.Model):
    """
    Shipping rules for calculating shipping costs based on country, mode, and weight.
    """
    __tablename__ = 'shipping_rules'
    
    id = db.Column(db.Integer, primary_key=True)
    country_iso = db.Column(db.String(3), nullable=False, index=True)  # ISO code or '*' for global
    shipping_mode_key = db.Column(db.String(50), db.ForeignKey('shipping_modes.key', ondelete='CASCADE'), nullable=False, index=True)
    min_weight = db.Column(db.Numeric(10, 3), nullable=False)  # Minimum weight in kg
    max_weight = db.Column(db.Numeric(10, 3), nullable=False)  # Maximum weight in kg
    price_gmd = db.Column(db.Numeric(10, 2), nullable=False)  # Price in GMD
    delivery_time = db.Column(db.String(100), nullable=True)  # Optional override
    priority = db.Column(db.Integer, default=0, nullable=False)  # Higher priority = applied first
    notes = db.Column(db.Text, nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Constraints
    __table_args__ = (
        CheckConstraint('min_weight < max_weight', name='check_min_max_weight'),
        CheckConstraint('price_gmd >= 0', name='check_price_non_negative'),
        Index('idx_country_mode_weight', 'country_iso', 'shipping_mode_key', 'min_weight', 'max_weight'),
        Index('idx_priority', 'priority'),
    )
    
    # Relationship to ShippingMode - access via shipping_mode_key foreign key
    # Using primaryjoin to match on key field instead of id
    shipping_mode = db.relationship('ShippingMode',
                                    primaryjoin='ShippingRule.shipping_mode_key == ShippingMode.key',
                                    foreign_keys='[ShippingRule.shipping_mode_key]',
                                    viewonly=True,
                                    lazy='select')
    
    def __repr__(self):
        return f'<ShippingRule {self.id}: {self.country_iso} {self.shipping_mode_key} {self.min_weight}-{self.max_weight}kg = D{self.price_gmd}>'
    
    def to_dict(self):
        """Convert shipping rule to dictionary."""
        # Safely access shipping_mode relationship
        shipping_mode_obj = None
        shipping_mode_label = None
        try:
            # Try to access the relationship
            if hasattr(self, 'shipping_mode') and self.shipping_mode:
                shipping_mode_obj = self.shipping_mode
                shipping_mode_label = shipping_mode_obj.label if shipping_mode_obj else None
        except (AttributeError, Exception):
            # If relationship fails, query directly using shipping_mode_key (NOT shipping_method)
            try:
                shipping_mode_obj = ShippingMode.query.filter_by(key=self.shipping_mode_key).first()
                shipping_mode_label = shipping_mode_obj.label if shipping_mode_obj else None
            except Exception:
                shipping_mode_label = None
        
        return {
            'id': self.id,
            'country_iso': self.country_iso,
            'shipping_mode_key': self.shipping_mode_key,
            'shipping_mode_label': shipping_mode_label,
            'min_weight': float(self.min_weight) if self.min_weight else 0.0,
            'max_weight': float(self.max_weight) if self.max_weight else 0.0,
            'price_gmd': float(self.price_gmd) if self.price_gmd else 0.0,
            'delivery_time': self.delivery_time,
            'priority': self.priority,
            'notes': self.notes,
            'active': self.active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    def overlaps_with(self, other) -> bool:
        """
        Check if this rule's weight range overlaps with another rule.
        Rules overlap if they have the same country_iso and shipping_mode_key
        and their weight ranges intersect.
        """
        if self.country_iso != other.country_iso:
            return False
        if self.shipping_mode_key != other.shipping_mode_key:
            return False
        
        # Check if weight ranges overlap
        # Ranges overlap if: min1 <= max2 AND min2 <= max1
        min1 = float(self.min_weight)
        max1 = float(self.max_weight)
        min2 = float(other.min_weight)
        max2 = float(other.max_weight)
        
        return min1 <= max2 and min2 <= max1

