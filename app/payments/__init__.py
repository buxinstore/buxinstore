"""
Payment System Package
All payment-related functionality is contained in this package.
"""

from flask import Blueprint

# Create payment blueprint
payment_bp = Blueprint('payments', __name__, url_prefix='/payments')

# Import routes to register them with the blueprint
from . import routes

def init_payment_system(app):
    """
    Initialize the payment system with the Flask app.
    This should be called in the main app initialization.
    """
    # Register the payment blueprint
    app.register_blueprint(payment_bp)
    
    return app

