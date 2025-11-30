"""Currency Rate model for storing conversion rates between currencies."""
from app import db
from datetime import datetime
from decimal import Decimal
from typing import Optional


class CurrencyRate(db.Model):
    """Currency Rate model for managing conversion rates between currency pairs."""
    __tablename__ = 'currency_rate'
    
    id = db.Column(db.Integer, primary_key=True)
    from_currency = db.Column(db.String(10), nullable=False, index=True)  # Source currency code (e.g., 'GMD')
    to_currency = db.Column(db.String(10), nullable=False, index=True)  # Target currency code (e.g., 'XOF')
    rate = db.Column(db.Numeric(20, 6), nullable=False)  # Conversion rate (1 from_currency = rate to_currency)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    notes = db.Column(db.Text, nullable=True)  # Optional notes
    
    # API sync settings
    api_sync_enabled = db.Column(db.Boolean, default=False, nullable=False)
    api_provider = db.Column(db.String(50), nullable=True)  # e.g., 'exchangerate-api', 'currencyapi'
    last_api_sync = db.Column(db.DateTime, nullable=True)
    api_sync_error = db.Column(db.Text, nullable=True)  # Last error message if sync failed
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Unique constraint: one rate per currency pair
    __table_args__ = (
        db.UniqueConstraint('from_currency', 'to_currency', name='unique_currency_pair'),
        db.Index('idx_currency_pair', 'from_currency', 'to_currency'),
        db.Index('idx_active_rates', 'is_active', 'from_currency', 'to_currency'),
    )
    
    def __repr__(self):
        return f'<CurrencyRate {self.from_currency} → {self.to_currency}: {self.rate}>'
    
    def to_dict(self):
        """Convert currency rate to dictionary."""
        return {
            'id': self.id,
            'from_currency': self.from_currency,
            'to_currency': self.to_currency,
            'rate': float(self.rate) if self.rate else None,
            'is_active': self.is_active,
            'last_updated': self.last_updated.isoformat() if self.last_updated else None,
            'notes': self.notes,
            'api_sync_enabled': self.api_sync_enabled,
            'api_provider': self.api_provider,
            'last_api_sync': self.last_api_sync.isoformat() if self.last_api_sync else None,
            'api_sync_error': self.api_sync_error,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    @staticmethod
    def get_rate(from_currency: str, to_currency: str, default_rate: float = None) -> Optional[Decimal]:
        """
        Get the active conversion rate between two currencies.
        
        Args:
            from_currency: Source currency code
            to_currency: Target currency code
            default_rate: Fallback rate if not found in database
            
        Returns:
            Conversion rate as Decimal, or None if not found
        """
        # Same currency = 1.0
        if from_currency == to_currency:
            return Decimal('1.0')
        
        # Try direct rate (from → to)
        rate = CurrencyRate.query.filter_by(
            from_currency=from_currency.upper(),
            to_currency=to_currency.upper(),
            is_active=True
        ).first()
        
        if rate:
            return Decimal(str(rate.rate))
        
        # Try reverse rate (to → from) and calculate inverse
        reverse_rate = CurrencyRate.query.filter_by(
            from_currency=to_currency.upper(),
            to_currency=from_currency.upper(),
            is_active=True
        ).first()
        
        if reverse_rate and reverse_rate.rate and float(reverse_rate.rate) != 0:
            return Decimal('1.0') / Decimal(str(reverse_rate.rate))
        
        # Return default rate if provided
        if default_rate is not None:
            return Decimal(str(default_rate))
        
        return None

