"""
Payment Models
All database models related to payments.
"""

from datetime import datetime
from app.extensions import db


class Payment(db.Model):
    """
    Payment model to track all payment transactions.
    Can be linked to either an Order (after payment confirmation) or PendingPayment (before confirmation).
    """
    __tablename__ = 'payments'
    
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=True)  # Now nullable - set after order creation
    pending_payment_id = db.Column(db.Integer, db.ForeignKey('pending_payments.id'), nullable=True)  # Link to pending payment
    amount = db.Column(db.Float, nullable=False)
    method = db.Column(db.String(50), nullable=False)  # wave, qmoney, afrimoney, ecobank
    reference = db.Column(db.String(100), unique=True, nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, completed, failed, refunded
    transaction_id = db.Column(db.String(100), unique=True, nullable=True)
    paid_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Additional fields for payment details
    payment_provider_response = db.Column(db.Text, nullable=True)  # Store raw response from provider
    failure_reason = db.Column(db.String(255), nullable=True)
    
    # Relationships
    order = db.relationship('Order', backref='payments', lazy=True)
    pending_payment = db.relationship('PendingPayment', backref='payments', lazy=True)
    
    def __repr__(self):
        return f'<Payment {self.id} - {self.method} - {self.status}>'
    
    def to_dict(self):
        """Convert payment to dictionary for JSON responses."""
        return {
            'id': self.id,
            'order_id': self.order_id,
            'amount': self.amount,
            'method': self.method,
            'reference': self.reference,
            'status': self.status,
            'transaction_id': self.transaction_id,
            'paid_at': self.paid_at.isoformat() if self.paid_at else None,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }


class PaymentMethod(db.Model):
    """
    Payment method configuration and settings.
    """
    __tablename__ = 'payment_methods'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)  # wave, qmoney, etc.
    display_name = db.Column(db.String(100), nullable=False)  # Wave Money, QMoney, etc.
    is_active = db.Column(db.Boolean, default=True)
    is_enabled = db.Column(db.Boolean, default=True)
    min_amount = db.Column(db.Float, default=0.0)
    max_amount = db.Column(db.Float, nullable=True)
    fee_percentage = db.Column(db.Float, default=0.0)
    fee_fixed = db.Column(db.Float, default=0.0)
    config = db.Column(db.Text, nullable=True)  # JSON configuration
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<PaymentMethod {self.name} - Active: {self.is_active}>'


class PaymentTransaction(db.Model):
    """
    Detailed transaction log for payment processing.
    """
    __tablename__ = 'payment_transactions'
    
    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.Integer, db.ForeignKey('payments.id'), nullable=False)
    action = db.Column(db.String(50), nullable=False)  # initiate, verify, complete, refund
    status = db.Column(db.String(20), nullable=False)  # success, failed, pending
    request_data = db.Column(db.Text, nullable=True)  # JSON request data
    response_data = db.Column(db.Text, nullable=True)  # JSON response data
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationship
    payment = db.relationship('Payment', backref='transactions', lazy=True)
    
    def __repr__(self):
        return f'<PaymentTransaction {self.id} - {self.action} - {self.status}>'


class PendingPayment(db.Model):
    """
    Pending payment model to track incomplete payment attempts.
    Orders are only created after successful payment confirmation.
    """
    __tablename__ = 'pending_payments'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='waiting')  # waiting, failed, completed
    modempay_transaction_id = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Additional fields to store checkout info for order creation
    payment_method = db.Column(db.String(50), nullable=True)
    delivery_address = db.Column(db.Text, nullable=True)
    customer_name = db.Column(db.String(255), nullable=True)
    customer_phone = db.Column(db.String(50), nullable=True)
    customer_email = db.Column(db.String(255), nullable=True)
    shipping_price = db.Column(db.Float, nullable=True)
    total_cost = db.Column(db.Float, nullable=True)
    location = db.Column(db.String(50), nullable=True)
    
    # Shipping rule fields (for automatic shipping calculation)
    shipping_rule_id = db.Column(db.Integer, db.ForeignKey('shipping_rules.id'), nullable=True)  # Which shipping rule was applied (new system)
    shipping_mode_key = db.Column(db.String(20), nullable=True)  # Selected shipping method: 'express', 'ecommerce', 'economy'
    shipping_delivery_estimate = db.Column(db.String(100), nullable=True)  # Delivery time estimate from rule
    shipping_display_currency = db.Column(db.String(10), nullable=True)  # Currency used for display (e.g., 'GMD', 'XOF')
    
    # Store cart items as JSON for simplicity (will be converted to OrderItems on success)
    cart_items_json = db.Column(db.Text, nullable=True)  # JSON string of cart items
    
    # Relationships
    user = db.relationship('User', backref='pending_payments', lazy=True)
    shipping_rule = db.relationship('app.shipping.models.ShippingRule', foreign_keys=[shipping_rule_id], backref='pending_payments', lazy=True)
    
    def __repr__(self):
        return f'<PendingPayment {self.id} - User {self.user_id} - {self.status}>'
    
    def to_dict(self):
        """Convert pending payment to dictionary for JSON responses."""
        import json
        return {
            'id': self.id,
            'user_id': self.user_id,
            'amount': self.amount,
            'status': self.status,
            'modempay_transaction_id': self.modempay_transaction_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'cart_items': json.loads(self.cart_items_json) if self.cart_items_json else []
        }
