"""Country model for localization system."""
from app import db
from datetime import datetime


class Country(db.Model):
    """Country model for managing country-specific settings."""
    __tablename__ = 'country'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    code = db.Column(db.String(10), nullable=False, unique=True)  # ISO country code (e.g., 'SN', 'CI')
    currency = db.Column(db.String(10), nullable=False)  # Currency code (e.g., 'XOF', 'GMD')
    currency_symbol = db.Column(db.String(10), default='')  # Currency symbol (e.g., 'CFA', 'D')
    language = db.Column(db.String(10), nullable=False)  # Language code (e.g., 'fr', 'en')
    flag_image_path = db.Column(db.String(255))  # Path to flag image
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship with users
    users = db.relationship('User', backref='country', lazy=True)
    
    def __repr__(self):
        return f'<Country {self.name} ({self.code})>'
    
    def to_dict(self):
        """Convert country to dictionary."""
        return {
            'id': self.id,
            'name': self.name,
            'code': self.code,
            'currency': self.currency,
            'currency_symbol': self.currency_symbol,
            'language': self.language,
            'flag_image_path': self.flag_image_path,
            'flag_url': self.get_flag_url(),  # Include flag URL in API response
            'is_active': self.is_active
        }
    
    def get_flag_url(self):
        """Get the URL for the country flag image with fallback to flagcdn.com."""
        # If flag_image_path exists and is a valid URL, use it
        if self.flag_image_path:
            # Check if it's a Cloudinary URL or full URL
            if self.flag_image_path.startswith('http://') or self.flag_image_path.startswith('https://'):
                return self.flag_image_path
            
            # Check if it's already a static path
            if self.flag_image_path.startswith('/static/'):
                return self.flag_image_path
            
            # Otherwise, use url_for
            from flask import url_for
            try:
                return url_for('static', filename=self.flag_image_path)
            except RuntimeError:
                # Outside request context
                return f'/static/{self.flag_image_path}'
        
        # Fallback to flagcdn.com if no flag_image_path
        if self.code:
            return f"https://flagcdn.com/w40/{self.code.lower()}.png"
        
        return None

