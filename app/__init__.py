from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, abort, send_file, current_app, json, make_response
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from io import BytesIO, StringIO
from PIL import Image, ImageOps
import uuid
from flask_login import UserMixin, login_user, login_required, logout_user, current_user
from flask_wtf.csrf import CSRFError
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Dict, List, Tuple, Optional
import os
import json
from werkzeug.utils import secure_filename
from functools import wraps
from sqlalchemy.orm import joinedload
from sqlalchemy import inspect, text, func
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from authlib.integrations.flask_client import OAuth
import secrets
import re
from email_validator import validate_email, EmailNotValidError
import time
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import csv
import shutil
import threading
import atexit

oauth = OAuth()
ModemPay = None

# Create a requests session with retry logic for Google OAuth
def create_retry_session():
    """Create a requests session with retry logic and timeout for Google OAuth"""
    session = requests.Session()
    retry_strategy = Retry(
        total=3,  # Total number of retries
        backoff_factor=2,  # Exponential backoff: 2, 4, 8 seconds
        status_forcelist=[429, 500, 502, 503, 504],  # Retry on these status codes
        allowed_methods=["GET", "POST"]  # Only retry on GET and POST
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

# Global session for Google OAuth requests
_google_oauth_session = None

def get_google_oauth_session():
    """Get or create the Google OAuth session with retry logic"""
    global _google_oauth_session
    if _google_oauth_session is None:
        _google_oauth_session = create_retry_session()
    return _google_oauth_session

# Cache for Google OpenID configuration (24 hours)
_google_openid_config_cache = None
_google_openid_config_cache_time = None
GOOGLE_CONFIG_CACHE_TTL = 86400  # 24 hours in seconds

# Chart data cache (10 minutes TTL)
_chart_cache = {}
_chart_cache_time = {}
CHART_CACHE_TTL = 600  # 10 minutes

def get_google_openid_config():
    """Get Google OpenID configuration with caching and error handling"""
    global _google_openid_config_cache, _google_openid_config_cache_time
    
    # Check cache
    if _google_openid_config_cache and _google_openid_config_cache_time:
        if time.time() - _google_openid_config_cache_time < GOOGLE_CONFIG_CACHE_TTL:
            return _google_openid_config_cache
    
    # Fetch from Google
    session = get_google_oauth_session()
    try:
        response = session.get(
            "https://accounts.google.com/.well-known/openid-configuration",
            timeout=10  # 10-second timeout
        )
        response.raise_for_status()
        config = response.json()
        
        # Update cache
        _google_openid_config_cache = config
        _google_openid_config_cache_time = time.time()
        
        return config
    except requests.exceptions.Timeout:
        # Return cached config if available, even if expired
        if _google_openid_config_cache:
            return _google_openid_config_cache
        raise
    except requests.exceptions.RequestException as e:
        # Return cached config if available, even if expired
        if _google_openid_config_cache:
            return _google_openid_config_cache
        raise

# Import extensions and models
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Config
from .extensions import csrf, db, init_extensions, login_manager, mail, migrate
from .utils.db_backup import (
    DatabaseBackupError,
    dump_database_to_file,
    dump_database_to_memory,
)


def get_base_url() -> str:
    """
    Helper to get the canonical base URL for the app without any trailing slash.
    Always prefers the PUBLIC_URL configuration and never inspects request.url_root.
    """
    try:
        base = (current_app.config.get("PUBLIC_URL") or "").rstrip("/")
    except RuntimeError:
        # Outside an app context – fall back to configuration on the Flask app
        # Note: this assumes create_app has already configured PUBLIC_URL.
        base = (getattr(current_app, "config", {}).get("PUBLIC_URL") or "").rstrip("/")
    return base


def create_app(config_class: type[Config] | None = None):
    app = Flask(__name__, static_folder='static', static_url_path='/static')

    import logging
    app.logger.setLevel(logging.DEBUG)
    logging.getLogger("werkzeug").setLevel(logging.DEBUG)

    config_obj = config_class or Config
    app.config.from_object(config_obj)

    # Canonical URL and Flask server/url configuration
    app.config.setdefault("SERVER_NAME", None)
    app.config.setdefault("PREFERRED_URL_SCHEME", "https")
    # Default PUBLIC_URL to the production storefront domain if not provided
    app.config.setdefault(
        "PUBLIC_URL",
        os.environ.get("PUBLIC_URL", "https://store.techbuxin.com"),
    )

    if app.config.get("IS_RENDER"):
        app.logger.info("Render deployment detected – enabling secure cookies.")
        app.config.setdefault("SESSION_COOKIE_SECURE", True)
        app.config.setdefault("REMEMBER_COOKIE_SECURE", True)
        # Ensure HTTPS URLs are generated correctly behind Render's proxy
        app.config.setdefault("PREFERRED_URL_SCHEME", "https")

    # Google OAuth configuration
    # These must be configured via environment variables in production, e.g.:
    #   GOOGLE_CLIENT_ID
    #   GOOGLE_CLIENT_SECRET
    #   GOOGLE_REDIRECT_URI=https://store.techbuxin.com/auth/google/callback
    #
    # We intentionally do NOT hard-code localhost defaults here so that
    # Render / production always use the correct public callback URL.
    app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID')
    app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET')
    public_url = app.config.get('PUBLIC_URL')
    default_google_redirect = (
        f"{public_url.rstrip('/')}/auth/google/callback" if public_url else None
    )
    app.config['GOOGLE_REDIRECT_URI'] = os.environ.get(
        'GOOGLE_REDIRECT_URI',
        default_google_redirect,
    )
    
    # Email configuration removed - using Resend API instead
    
    # File uploads
    app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size
    app.config['PROFILE_PICTURE_FOLDER'] = os.path.join(app.config['UPLOAD_FOLDER'], 'profile_pictures')
    app.config['PROFILE_PICTURE_MAX_SIZE'] = 5 * 1024 * 1024  # 5MB
    app.config['PROFILE_ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'webp'}
    
    # Initialize all extensions
    init_extensions(app)
    oauth.init_app(app)
    
    # Initialize Cloudinary
    from .utils.cloudinary_utils import init_cloudinary
    init_cloudinary(app)
    if app.config.get('GOOGLE_CLIENT_ID') and app.config.get('GOOGLE_CLIENT_SECRET'):
        try:
            # Pre-fetch and cache OpenID configuration
            try:
                get_google_openid_config()
            except Exception as e:
                app.logger.warning(f"Could not pre-fetch Google OpenID config: {e}. Will fetch on first use.")
            
            oauth.register(
                name='google',
                client_id=app.config['GOOGLE_CLIENT_ID'],
                client_secret=app.config['GOOGLE_CLIENT_SECRET'],
                access_token_url='https://oauth2.googleapis.com/token',
                authorize_url='https://accounts.google.com/o/oauth2/v2/auth',
                api_base_url='https://www.googleapis.com/oauth2/v2/',
                client_kwargs={'scope': 'openid email profile'},
                server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
                authorize_params={'prompt': 'select_account'},
                # In production on Render, this must be set via the
                # GOOGLE_REDIRECT_URI env var to:
                #   https://store.techbuxin.com/auth/google/callback
                redirect_uri=app.config.get('GOOGLE_REDIRECT_URI')
            )
        except Exception as e:
            app.logger.error(f"Error registering Google OAuth: {e}")
    else:
        app.logger.warning('Google OAuth credentials not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.')
    
    from app.payments import init_payment_system
    init_payment_system(app)

    # Attach base URL helper and expose PUBLIC_URL-derived base_url to templates
    # This allows `current_app.get_base_url()` in request handlers.
    app.get_base_url = get_base_url

    @app.context_processor
    def inject_base_url():
        # Always derive from the helper so changes to PUBLIC_URL are reflected everywhere
        return {"base_url": app.get_base_url()}

    @app.route("/_health", methods=["GET"])
    def healthcheck():
        return jsonify({"status": "ok"}), 200

    # Add error handler for database connection errors
    @app.errorhandler(OperationalError)
    def handle_database_error(e):
        """Handle database connection errors gracefully."""
        app.logger.error(f"Database connection error: {e}", exc_info=True)
        # Try to rollback any pending transaction
        try:
            db.session.rollback()
        except Exception:
            pass
        
        # Return a user-friendly error message
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "error": "Database connection error",
                "message": "A temporary database error occurred. Please try again."
            }), 503
        else:
            flash("A temporary database error occurred. Please try again.", "error")
            return redirect(request.referrer or url_for("home")), 503

    return app

# Create the application
app = create_app()

# ModemPay environment variables
MODEM_PAY_API_KEY = os.getenv("MODEM_PAY_API_KEY") or os.getenv("MODEM_PAY_SECRET_KEY")
MODEM_PAY_PUBLIC_KEY = os.getenv("MODEM_PAY_PUBLIC_KEY")

# SDK is not used. All ModemPay calls are done via form-data to the test checkout endpoint.

# Initialize payment system
DEFAULT_LOGO_URL = "https://res.cloudinary.com/dfizb64hx/image/upload/v1762457701/Bux_n_1_a0ypnj.png"

@app.template_filter('product_image_url')
def product_image_url_filter(image_path):
    """
    Template filter to get the correct URL for a product image.
    Handles both formats:
    - 'uploads/products/{filename}' -> uses url_for
    - '/static/uploads/products/{filename}' -> returns as-is
    - Cloudinary URLs -> returns as-is
    """
    if not image_path:
        return None
    
    image_path = image_path.strip()
    
    # If it's already a full URL (Cloudinary), return as-is
    if image_path.startswith('http://') or image_path.startswith('https://'):
        return image_path
    
    # If path already starts with /static/, return as-is
    if image_path.startswith('/static/'):
        return image_path
    
    # Otherwise, use url_for to generate the correct URL
    return url_for('static', filename=image_path)

@app.template_filter('category_image_url')
def category_image_url_filter(image_path):
    """
    Template filter to get the correct URL for a category image.
    Handles both formats:
    - 'uploads/category/{filename}' or relative paths -> uses url_for
    - '/static/uploads/category/{filename}' -> returns as-is
    - Cloudinary URLs -> returns as-is
    """
    if not image_path:
        return None
    
    image_path = image_path.strip()
    
    # If it's already a full URL (Cloudinary), return as-is
    if image_path.startswith('http://') or image_path.startswith('https://'):
        return image_path
    
    # If path already starts with /static/, return as-is
    if image_path.startswith('/static/'):
        return image_path
    
    # Otherwise, use url_for to generate the correct URL
    return url_for('static', filename=image_path)

@app.context_processor
def inject_site_settings():
    settings = SiteSettings.query.first()
    if not settings:
        settings = SiteSettings()
        db.session.add(settings)
        db.session.commit()
    # Handle logo URL - check if it's Cloudinary or local
    if settings.logo_path:
        from .utils.cloudinary_utils import is_cloudinary_url
        if is_cloudinary_url(settings.logo_path):
            logo_url = settings.logo_path
        else:
            logo_url = url_for('static', filename=settings.logo_path)
    else:
        logo_url = DEFAULT_LOGO_URL
    
    # Handle hero image URL - check if it's Cloudinary or local
    if settings.hero_image_path:
        from .utils.cloudinary_utils import is_cloudinary_url
        if is_cloudinary_url(settings.hero_image_path):
            hero_image_url = settings.hero_image_path
        else:
            hero_image_url = url_for('static', filename=settings.hero_image_path)
    else:
        hero_image_url = None
    avatar_url = None
    display_name = None
    google_connected = False
    if current_user.is_authenticated:
        try:
            profile = ensure_user_profile(current_user)
            avatar_url = current_user.get_avatar_url(cache_bust=True)
            display_name = current_user.display_name
            google_connected = bool(current_user.google_id or (profile and profile.google_avatar_url))
        except Exception as exc:
            current_app.logger.debug(f"Unable to inject user profile context: {exc}")
    return {
        'site_settings': settings,
        'site_logo_url': logo_url,
        'hero_image_url': hero_image_url,
        'cart_currency': current_app.config.get('CART_CURRENCY_SYMBOL', 'D'),
        'current_user_avatar_url': avatar_url,
        'current_user_display_name': display_name,
        'current_user_google_connected': google_connected,
        'product_image_url': product_image_url_filter
    }

# Test route to check if the application is running
@app.route('/test')
def test():
    return 'Test route is working!'

# Create payment link (test helper using the same exact form-data flow)
@app.route('/create-payment')
def create_payment_link():
    """
    Create a ModemPay payment link using the live API:
    - POST https://checkout.modempay.com/api/pay
    - form-data ONLY, exact fields
    """
    try:
        key = (MODEM_PAY_PUBLIC_KEY or "").strip()
        if not key or key.lower().startswith("your_"):
            return jsonify({"error": "MODEM_PAY_PUBLIC_KEY is missing. Set a valid public key (pk_live_...)"}), 400

        # Build absolute URLs
        base = app.get_base_url()
        cancel_url = f"{base}{url_for('payment_failure', _external=False)}"
        return_url = f"{base}{url_for('payment_success', _external=False)}"

        # Form-data payload only, exact fields
        base_payload = {
            "amount": 450,
            "customer_name": "Test User",
            "customer_email": "usr@example.com",
            "customer_phone": "+2200000000",
            "cancel_url": cancel_url,
            "return_url": return_url,
            "currency": "GMD",
            "metadata": {"source": "flask-app"}
        }

        form_payload = {"public_key": key, **{k: v for k, v in base_payload.items() if k != "metadata"}}
        form_payload["metadata[source]"] = base_payload["metadata"]["source"]
        respA = requests.post(
            "https://checkout.modempay.com/api/pay",
            data=form_payload,
            timeout=20,
        )
        textA = respA.text
        if respA.status_code == 200 and "__NEXT_DATA__" in textA:
            import re as _re
            try:
                m = _re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', textA, _re.S)
                if m:
                    next_data = json.loads(m.group(1))
                    q = (next_data.get("query") or {})
                    intent = q.get("intent") or (next_data.get("props", {}).get("pageProps", {}).get("intent"))
                    token = q.get("token") or (next_data.get("props", {}).get("pageProps", {}).get("token"))
                    if intent and token:
                        payment_link = f"https://checkout.modempay.com/{intent}?token={token}"
                        return jsonify({"success": True, "payment_url": payment_link})
            except Exception:
                pass
        return jsonify({"success": False}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# Add context processor to make 'now' available in all templates
@app.context_processor
def inject_now():
    return {'now': datetime.utcnow()}

# Custom Jinja2 filter for formatting currency with commas
@app.template_filter('currency')
def currency_filter(value):
    """Format a number as currency with commas and 2 decimal places"""
    try:
        return f"{float(value):,.2f}"
    except (ValueError, TypeError):
        return str(value)

app.config['WTF_CSRF_ENABLED'] = True
app.config['WTF_CSRF_SECRET_KEY'] = 'a-secret-key-for-csrf'  # Change this in production
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# Email configuration removed as per user request

# CSRF error handler for JSON responses
@app.errorhandler(CSRFError)
def csrf_error(e):
    """Handle CSRF errors and return JSON for API requests"""
    if request.is_json or request.headers.get('Content-Type') == 'application/json' or \
       request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
       request.path.startswith('/admin/settings/'):
        return jsonify({'success': False, 'message': 'CSRF token missing or invalid'}), 400
    # For regular form submissions, raise the error normally
    raise e

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Models
class CartItem(db.Model):
    __table_args__ = (
        db.UniqueConstraint('user_id', 'product_id', name='uq_cart_item_user_product'),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, default=1, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship
    product = db.relationship('Product', backref='cart_items')

class WishlistItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref=db.backref('wishlist_items', lazy=True))
    product = db.relationship('Product', backref=db.backref('wishlisted_by', lazy='dynamic'))


class UserPaymentMethod(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    provider = db.Column(db.String(80), nullable=False)
    label = db.Column(db.String(120))
    account_identifier = db.Column(db.String(120), nullable=False)
    account_last4 = db.Column(db.String(4))
    is_default = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('payment_methods', lazy=True))

    def masked_identifier(self) -> str:
        if self.account_last4:
            return f"•••• {self.account_last4}"
        if len(self.account_identifier) <= 4:
            return self.account_identifier
        return f"•••• {self.account_identifier[-4:]}"

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255))
    is_admin = db.Column(db.Boolean, default=False)
    role = db.Column(db.String(50), default='customer')  # admin, china_partner, gambia_team, customer
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    google_id = db.Column(db.String(255), unique=True, index=True)
    password_updated_at = db.Column(db.DateTime)
    last_login_at = db.Column(db.DateTime)
    whatsapp_number = db.Column(db.String(32), nullable=True)  # WhatsApp number with country code
    orders = db.relationship('Order', primaryjoin='User.id == Order.user_id', backref=db.backref('customer', lazy=True), lazy=True)
    cart_items = db.relationship('CartItem', backref='user', lazy=True, cascade='all, delete-orphan')
    profile = db.relationship('UserProfile', uselist=False, back_populates='user', cascade='all, delete-orphan')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        self.password_updated_at = datetime.utcnow()
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def ensure_profile(self):
        return ensure_user_profile(self)

    @property
    def display_name(self) -> str:
        profile = self.profile
        if profile:
            full_name = " ".join(filter(None, [profile.first_name, profile.last_name])).strip()
            if full_name:
                return full_name
        if self.username:
            return self.username
        if self.email:
            return self.email.split('@')[0]
        return "Customer"

    def get_avatar_url(self, cache_bust: bool = False) -> str:
        try:
            if self.profile and self.profile.avatar_filename:
                # Check if it's a Cloudinary URL
                from .utils.cloudinary_utils import is_cloudinary_url
                if is_cloudinary_url(self.profile.avatar_filename):
                    avatar_url = self.profile.avatar_filename
                else:
                    avatar_url = url_for('static', filename=f"uploads/profile_pictures/{self.profile.avatar_filename}")
                if cache_bust and self.profile.avatar_updated_at:
                    avatar_url = f"{avatar_url}?v={int(self.profile.avatar_updated_at.timestamp())}"
                return avatar_url
            if self.profile and self.profile.google_avatar_url:
                avatar_url = self.profile.google_avatar_url
                if cache_bust and self.profile.google_avatar_synced_at:
                    version = int(self.profile.google_avatar_synced_at.timestamp())
                    separator = '&' if '?' in avatar_url else '?'
                    avatar_url = f"{avatar_url}{separator}v={version}"
                return avatar_url
        except RuntimeError:
            # Outside request context
            pass
        return url_for('static', filename='images/default-avatar.svg')

    def to_profile_dict(self) -> Dict[str, Optional[str]]:
        profile = self.ensure_profile()
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'first_name': profile.first_name,
            'last_name': profile.last_name,
            'display_name': self.display_name,
            'phone_number': profile.phone_number,
            'address': profile.address,
            'city': profile.city,
            'state': profile.state,
            'postal_code': profile.postal_code,
            'country': profile.country,
            'avatar_url': self.get_avatar_url(cache_bust=True),
            'google_connected': bool(self.google_id or profile.google_avatar_url),
            'notifications': {
                'email': profile.notify_email,
                'sms': profile.notify_sms,
                'push': profile.notify_push,
                'marketing': profile.marketing_opt_in
            }
        }


class UserProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    first_name = db.Column(db.String(120))
    last_name = db.Column(db.String(120))
    phone_number = db.Column(db.String(32))
    address = db.Column(db.String(255))
    city = db.Column(db.String(120))
    state = db.Column(db.String(120))
    postal_code = db.Column(db.String(20))
    country = db.Column(db.String(120))
    avatar_filename = db.Column(db.String(255))
    avatar_updated_at = db.Column(db.DateTime)
    google_avatar_url = db.Column(db.String(512))
    google_avatar_synced_at = db.Column(db.DateTime)
    notify_email = db.Column(db.Boolean, default=True)
    notify_sms = db.Column(db.Boolean, default=False)
    notify_push = db.Column(db.Boolean, default=False)
    marketing_opt_in = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user = db.relationship('User', back_populates='profile')


class Subscriber(db.Model):
    """Subscribers table for non-logged-in users who provide email and WhatsApp number."""
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    whatsapp_number = db.Column(db.String(32), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<Subscriber {self.email} - {self.whatsapp_number}>'


class NewsletterSubscriber(db.Model):
    __tablename__ = 'newsletter_subscriber'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False, unique=True)
    name = db.Column(db.String(100))
    is_active = db.Column(db.Boolean, default=True)
    subscribed_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_sent = db.Column(db.DateTime)

    def __repr__(self):
        return f'<NewsletterSubscriber {self.email}>'


class EmailCampaign(db.Model):
    __tablename__ = 'email_campaign'

    id = db.Column(db.Integer, primary_key=True)
    subject = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    audience = db.Column(db.String(50))
    status = db.Column(db.String(20))
    scheduled_for = db.Column(db.DateTime)
    sent_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))

    creator = db.relationship('User', backref=db.backref('email_campaigns', lazy=True))


class EmailLog(db.Model):
    __tablename__ = 'email_log'

    id = db.Column(db.Integer, primary_key=True)
    email_type = db.Column(db.String(50))
    recipient = db.Column(db.String(120), nullable=False)
    subject = db.Column(db.String(200))
    sent_at = db.Column(db.DateTime)
    status = db.Column(db.String(20))
    campaign_id = db.Column(db.Integer, db.ForeignKey('email_campaign.id'))
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))
    error_message = db.Column(db.Text)

    campaign = db.relationship('EmailCampaign', backref=db.backref('logs', lazy=True))
    order = db.relationship('Order', backref=db.backref('email_logs', lazy='dynamic'))


class WhatsAppMessageLog(db.Model):
    """Logs all WhatsApp messages sent through the system."""
    __tablename__ = 'whats_app_message_log'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)  # Nullable for subscribers
    subscriber_id = db.Column(db.Integer, db.ForeignKey('subscriber.id'), nullable=True)  # Nullable for users
    whatsapp_number = db.Column(db.String(32), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, sent, failed
    error_message = db.Column(db.Text, nullable=True)
    message_id = db.Column(db.String(100), nullable=True)  # WhatsApp message ID from API
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref='whatsapp_messages', lazy=True)
    subscriber = db.relationship('Subscriber', backref='whatsapp_messages', lazy=True)
    
    def __repr__(self):
        return f'<WhatsAppMessageLog {self.id} - {self.status} - {self.whatsapp_number}>'


class LegacyWhatsAppMessageLog(db.Model):
    """Legacy table created during early WhatsApp logging experiments."""
    __tablename__ = 'whatsapp_message_log'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    subscriber_id = db.Column(db.Integer, db.ForeignKey('subscriber.id'))
    whatsapp_number = db.Column(db.String(32), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='pending')
    error_message = db.Column(db.Text)
    message_id = db.Column(db.String(100))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


class CustomerFeedback(db.Model):
    __tablename__ = 'customer_feedback'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    image_path = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_published = db.Column(db.Boolean, default=False)

    user = db.relationship('User', backref=db.backref('customer_feedback', lazy=True))
    order = db.relationship('Order', backref=db.backref('feedback_entries', lazy=True))


class ProductRestockRequest(db.Model):
    __tablename__ = 'product_restock_request'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    is_notified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    notified_at = db.Column(db.DateTime)

    product = db.relationship('Product', backref=db.backref('restock_requests', lazy=True))


class DatabaseLog(db.Model):
    """Logs all database operations performed through the Database Manager."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    action = db.Column(db.String(50), nullable=False)  # CREATE, READ, UPDATE, DELETE, EXPORT, IMPORT, BACKUP, RESTORE
    table_name = db.Column(db.String(100), nullable=True)
    row_id = db.Column(db.String(100), nullable=True)  # Can be string for composite keys
    details = db.Column(db.Text, nullable=True)  # Additional details about the operation
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship('User', backref='database_logs', lazy=True)
    
    def __repr__(self):
        return f'<DatabaseLog {self.id} - {self.action} - {self.table_name} - {self.timestamp}>'


class DatabaseBackupLog(db.Model):
    """Tracks automated and manual database backup jobs."""
    __tablename__ = 'database_backup_log'

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    status = db.Column(db.String(20), nullable=False)  # success / fail / skipped
    file_paths = db.Column(db.Text, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    trigger = db.Column(db.String(20), nullable=True)  # manual / auto / test
    email_recipient = db.Column(db.String(255), nullable=True)

    def __repr__(self):
        return f'<DatabaseBackupLog {self.id} - {self.status} - {self.created_at}>'


def ensure_user_profile(user: "User") -> UserProfile:
    if not user:
        raise ValueError("User instance is required")
    profile = user.profile
    if profile is None:
        profile = UserProfile(user=user)
        db.session.add(profile)
        try:
            db.session.flush()
        except Exception:
            # Ignore flush errors; commit will raise if needed
            pass
        user.profile = profile
    return profile


def split_display_name(name: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not name:
        return None, None
    parts = [part.strip() for part in name.strip().split() if part.strip()]
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    first_name = parts[0]
    last_name = " ".join(parts[1:])
    return first_name, last_name


PHONE_PATTERN = re.compile(r'^\+?[0-9\s\-()]{7,20}$')
ALLOWED_IMAGE_MIME_TYPES = {'image/png', 'image/jpeg', 'image/webp'}
SUPPORTED_PAYMENT_PROVIDERS = ['Wave', 'AfriMoney', 'Orange Money', 'MoMo', 'Visa', 'Mastercard']
RESAMPLING_LANCZOS = getattr(getattr(Image, 'Resampling', Image), 'LANCZOS', Image.LANCZOS)


def normalize_phone_number(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    candidate = phone.strip()
    if not PHONE_PATTERN.match(candidate):
        raise ValueError("Phone number must contain 7-20 digits and may include +, spaces, hyphens, or parentheses.")
    prefix_plus = candidate.startswith('+')
    digits = re.sub(r'\D', '', candidate)
    if prefix_plus:
        return f"+{digits}"
    return digits


def normalize_whatsapp_number(phone: str) -> str:
    """Normalize WhatsApp number to include country code with + prefix."""
    if not phone:
        raise ValueError("Phone number is required")
    phone = phone.strip()
    
    # If already starts with +, return as is (after cleaning)
    if phone.startswith('+'):
        digits = re.sub(r'\D', '', phone)
        return f"+{digits}"
    
    # If starts with country code (e.g., 220 for Gambia), add +
    if phone.startswith('220'):
        return f"+{phone}"
    
    # If starts with 0, replace with country code
    if phone.startswith('0'):
        return f"+220{phone[1:]}"
    
    # Otherwise, assume Gambia country code
    digits = re.sub(r'\D', '', phone)
    return f"+220{digits}"


def send_whatsapp_message_with_logging(
    whatsapp_number: str,
    message: str,
    user_id: Optional[int] = None,
    subscriber_id: Optional[int] = None
) -> Tuple[bool, Optional[str], Optional[int]]:
    """
    Send WhatsApp message and log the result.
    
    Args:
        whatsapp_number: Phone number with country code
        message: Message text to send
        user_id: User ID if sending to a registered user (optional)
        subscriber_id: Subscriber ID if sending to a subscriber (optional)
    
    Returns:
        Tuple of (success: bool, error_message: Optional[str], log_id: Optional[int])
    """
    from dotenv import load_dotenv
    load_dotenv(override=True)
    
    # Get WhatsApp credentials
    access_token = os.getenv('WHATSAPP_ACCESS_TOKEN')
    phone_number_id = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
    
    if not access_token or not phone_number_id:
        error_msg = "WhatsApp is not configured"
        # Log the failure
        log_entry = WhatsAppMessageLog(
            user_id=user_id,
            subscriber_id=subscriber_id,
            whatsapp_number=whatsapp_number,
            message=message,
            status='failed',
            error_message=error_msg
        )
        db.session.add(log_entry)
        db.session.commit()
        return False, error_msg, log_entry.id
    
    # Normalize phone number
    try:
        normalized_number = normalize_whatsapp_number(whatsapp_number)
    except Exception as e:
        error_msg = f"Invalid phone number format: {str(e)}"
        log_entry = WhatsAppMessageLog(
            user_id=user_id,
            subscriber_id=subscriber_id,
            whatsapp_number=whatsapp_number,
            message=message,
            status='failed',
            error_message=error_msg
        )
        db.session.add(log_entry)
        db.session.commit()
        return False, error_msg, log_entry.id
    
    # Create log entry
    log_entry = WhatsAppMessageLog(
        user_id=user_id,
        subscriber_id=subscriber_id,
        whatsapp_number=normalized_number,
        message=message,
        status='pending'
    )
    db.session.add(log_entry)
    db.session.commit()
    
    # Send message via WhatsApp API
    url = f"https://graph.facebook.com/v22.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "messaging_product": "whatsapp",
        "to": normalized_number,
        "type": "text",
        "text": {
            "body": message
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response_data = response.json() if response.headers.get('content-type', '').startswith('application/json') else response.text
        
        if response.status_code == 200:
            # Extract message ID if available
            message_id = None
            if isinstance(response_data, dict) and 'messages' in response_data:
                message_id = response_data.get('messages', [{}])[0].get('id')
            
            # Update log entry
            log_entry.status = 'sent'
            log_entry.message_id = message_id
            db.session.commit()
            
            current_app.logger.info(f"✅ WhatsApp message sent successfully to {normalized_number}, Log ID: {log_entry.id}")
            return True, None, log_entry.id
        else:
            # Update log entry with error
            error_msg = str(response_data)[:500] if response_data else f"HTTP {response.status_code}"
            log_entry.status = 'failed'
            log_entry.error_message = error_msg
            db.session.commit()
            
            current_app.logger.error(f"❌ Failed to send WhatsApp message to {normalized_number}: HTTP {response.status_code} - {error_msg}")
            return False, error_msg, log_entry.id
            
    except requests.exceptions.RequestException as e:
        error_msg = str(e)[:500]
        log_entry.status = 'failed'
        log_entry.error_message = error_msg
        db.session.commit()
        
        current_app.logger.error(f"❌ Error sending WhatsApp message to {normalized_number}: {error_msg}")
        return False, error_msg, log_entry.id
        
    except Exception as e:
        error_msg = str(e)[:500]
        log_entry.status = 'failed'
        log_entry.error_message = error_msg
        db.session.commit()
        
        current_app.logger.error(f"❌ Unexpected error sending WhatsApp message to {normalized_number}: {error_msg}")
        return False, error_msg, log_entry.id


def _format_email_subject(subject: str) -> str:
    """
    Format email subject with prefix from database settings.
    
    Args:
        subject: The base subject line
        
    Returns:
        Formatted subject with prefix (e.g., "BuXin Store - Reset Your Password")
    """
    try:
        settings = AppSettings.query.first()
        if settings and settings.default_subject_prefix:
            prefix = settings.default_subject_prefix.strip()
            if prefix and not subject.startswith(prefix):
                return f"{prefix} - {subject}"
    except Exception:
        pass
    return subject


def _send_form_submission_notifications(
    whatsapp_number: str,
    email: str,
    user_name: Optional[str] = None,
    is_logged_in: bool = False
) -> None:
    """
    Send notifications to admin-configured receivers when a form is submitted.
    
    Args:
        whatsapp_number: The WhatsApp number submitted
        email: The email address submitted
        user_name: Name of the user (if logged in)
        is_logged_in: Whether the user is logged in
    """
    try:
        settings = AppSettings.query.first()
        if not settings:
            return
        
        # Send WhatsApp notification if receiver is configured (use new field with fallback to old)
        whatsapp_receiver = settings.whatsapp_receiver or settings.contact_whatsapp_receiver
        if whatsapp_receiver:
            try:
                receiver_number = normalize_whatsapp_number(whatsapp_receiver)
                user_type = "User" if is_logged_in else "Subscriber"
                name_info = f" ({user_name})" if user_name else ""
                notification_message = (
                    f"📱 New WhatsApp Form Submission\n\n"
                    f"Type: {user_type}{name_info}\n"
                    f"Email: {email}\n"
                    f"WhatsApp: {whatsapp_number}\n"
                    f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
                )
                
                send_whatsapp_message_with_logging(
                    whatsapp_number=receiver_number,
                    message=notification_message,
                    user_id=None,
                    subscriber_id=None
                )
            except Exception as e:
                current_app.logger.error(f"Failed to send WhatsApp notification: {str(e)}")
        
        # Send email notification if receiver is configured (use new field with fallback to old)
        email_receiver = settings.email_receiver or settings.contact_email_receiver
        if email_receiver:
            try:
                from app.utils.email_queue import queue_single_email
                
                user_type = "User" if is_logged_in else "Subscriber"
                name_info = f" ({user_name})" if user_name else ""
                
                subject = _format_email_subject(f"New WhatsApp Form Submission - {user_type}")
                html_body = f"""
                <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                    <h2 style="color: #25D366;">📱 New WhatsApp Form Submission</h2>
                    <div style="background-color: #f9f9f9; padding: 15px; border-radius: 5px; margin: 20px 0;">
                        <p><strong>Type:</strong> {user_type}{name_info}</p>
                        <p><strong>Email:</strong> {email}</p>
                        <p><strong>WhatsApp Number:</strong> {whatsapp_number}</p>
                        <p><strong>Submitted At:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
                    </div>
                    <p style="color: #666; font-size: 12px;">This is an automated notification from your BuXin store.</p>
                </body>
                </html>
                """
                
                app_obj = current_app._get_current_object()
                queue_single_email(
                    app_obj,
                    email_receiver,
                    subject,
                    html_body
                )
            except Exception as e:
                current_app.logger.error(f"Failed to send email notification: {str(e)}")
                
    except Exception as e:
        current_app.logger.error(f"Error in _send_form_submission_notifications: {str(e)}")


def is_username_available(username: str, exclude_user_id: Optional[int] = None) -> bool:
    query = User.query.filter_by(username=username)
    if exclude_user_id:
        query = query.filter(User.id != exclude_user_id)
    return query.first() is None


def ensure_allowed_image(file_storage) -> Image.Image:
    if not file_storage or not getattr(file_storage, "filename", ""):
        raise ValueError("No image provided.")
    filename = secure_filename(file_storage.filename)
    if '.' not in filename:
        raise ValueError("Image must have a valid extension.")
    extension = filename.rsplit('.', 1)[1].lower()
    if extension not in current_app.config.get('PROFILE_ALLOWED_EXTENSIONS', {'png', 'jpg', 'jpeg', 'webp'}):
        raise ValueError("Unsupported image format. Please upload PNG, JPG, or WEBP.")
    if file_storage.mimetype not in ALLOWED_IMAGE_MIME_TYPES:
        raise ValueError("Unsupported image format. Please upload PNG, JPG, or WEBP.")
    file_storage.stream.seek(0, os.SEEK_END)
    file_size = file_storage.stream.tell()
    max_size = current_app.config.get('PROFILE_PICTURE_MAX_SIZE', 5 * 1024 * 1024)
    if file_size > max_size:
        raise ValueError("Image exceeds the maximum size of 5MB.")
    file_storage.stream.seek(0)
    try:
        image = Image.open(file_storage.stream)
        image = ImageOps.exif_transpose(image)
    except Exception as exc:
        raise ValueError("The uploaded file is not a valid image.") from exc
    return image


def get_profile_picture_directory() -> str:
    folder = current_app.config.get('PROFILE_PICTURE_FOLDER', os.path.join('static', 'uploads', 'profile_pictures'))
    if not os.path.isabs(folder):
        folder = os.path.join(current_app.root_path, folder)
    os.makedirs(folder, exist_ok=True)
    return folder


def save_profile_image(image: Image.Image, extension: str = 'png') -> str:
    extension = extension.lower()
    if extension not in {'png', 'jpg', 'jpeg', 'webp'}:
        extension = 'png'
    filename = f"{uuid.uuid4().hex}.{extension if extension != 'jpg' else 'jpeg'}"
    directory = get_profile_picture_directory()
    filepath = os.path.join(directory, filename)
    image_to_save = image.convert('RGBA') if extension == 'png' else image.convert('RGB')
    image_to_save.save(filepath, quality=95)
    return filename


def delete_profile_image(filename: Optional[str]) -> None:
    if not filename:
        return
    directory = get_profile_picture_directory()
    filepath = os.path.join(directory, filename)
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except Exception:
        current_app.logger.warning(f"Unable to delete old profile image {filepath}")

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    image = db.Column(db.String(200))
    products = db.relationship('Product', backref='category_ref', lazy=True)

class SiteSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    logo_path = db.Column(db.String(255))
    hero_title = db.Column(db.String(200), default='Build the Future')
    hero_subtitle = db.Column(db.String(255), default='Premium electronics and robotics components')
    hero_button_text = db.Column(db.String(100), default='Shop Now')
    hero_button_link = db.Column(db.String(255), default='#featured')
    hero_image_path = db.Column(db.String(255))

class SiteSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    logo_path = db.Column(db.String(255))
    hero_title = db.Column(db.String(255), default='Build the Future')
    hero_subtitle = db.Column(db.String(255), default='Premium electronics and robotics components')
    hero_image_path = db.Column(db.String(255))

class AppSettings(db.Model):
    """Centralized app settings storage"""
    id = db.Column(db.Integer, primary_key=True)
    # General Settings
    business_name = db.Column(db.String(255))
    website_url = db.Column(db.String(255))
    support_email = db.Column(db.String(255))
    contact_whatsapp = db.Column(db.String(50))
    company_logo_url = db.Column(db.String(500))
    # Contact Form Receivers (legacy - kept for backward compatibility)
    contact_whatsapp_receiver = db.Column(db.String(50))  # WhatsApp number that receives form submissions
    contact_email_receiver = db.Column(db.String(255))  # Email address that receives form submissions
    # Default Communication Receivers (new - use these instead)
    whatsapp_receiver = db.Column(db.String(50), default="+2200000000")  # Default WhatsApp receiver for all communications
    email_receiver = db.Column(db.String(255), default="buxinstore9@gmail.com")  # Default email receiver for all communications
    # Payment Settings
    modempay_api_key = db.Column(db.String(255))
    modempay_public_key = db.Column(db.String(255))
    payment_return_url = db.Column(db.String(500))
    payment_cancel_url = db.Column(db.String(500))
    payments_enabled = db.Column(db.Boolean, default=True)
    # Cloudinary Settings
    cloudinary_cloud_name = db.Column(db.String(255))
    cloudinary_api_key = db.Column(db.String(255))
    cloudinary_api_secret = db.Column(db.String(255))
    # WhatsApp Settings
    whatsapp_access_token = db.Column(db.String(500))
    whatsapp_phone_number_id = db.Column(db.String(100))
    whatsapp_business_name = db.Column(db.String(255))
    whatsapp_bulk_messaging_enabled = db.Column(db.Boolean, default=False)
    # Email Settings (Resend)
    resend_api_key = db.Column(db.String(255))  # Resend API key (stored in DB, can also use env var)
    resend_from_email = db.Column(db.String(255))  # Resend from email address
    resend_default_recipient = db.Column(db.String(255))  # Default recipient for admin emails
    resend_enabled = db.Column(db.Boolean, default=True)  # Enable/disable Resend email sending
    contact_email = db.Column(db.String(255))  # Contact email for support
    default_subject_prefix = db.Column(db.String(100), default='BuXin Store')  # Default subject prefix
    # AI Settings (Optional)
    ai_api_key = db.Column(db.String(255))
    ai_auto_prompt_improvements = db.Column(db.Boolean, default=False)
    # Backup Automation
    backup_enabled = db.Column(db.Boolean, default=False)
    backup_time = db.Column(db.String(8), default='02:00')
    backup_email = db.Column(db.String(255))
    backup_retention_days = db.Column(db.Integer, default=30)
    backup_last_run = db.Column(db.DateTime, nullable=True)
    backup_last_status = db.Column(db.String(20), nullable=True)
    backup_last_message = db.Column(db.Text, nullable=True)
    # Timestamps
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    price = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, nullable=False)
    image = db.Column(db.String(200))
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    available_in_gambia = db.Column(db.Boolean, default=False, nullable=False)
    delivery_price = db.Column(db.Float, nullable=True)
    shipping_price = db.Column(db.Float, nullable=True)  # Shipping cost (can be different from delivery_price)
    location = db.Column(db.String(50), nullable=True)  # 'In The Gambia' or 'Outside The Gambia'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationship to delivery rules
    delivery_rules = db.relationship('DeliveryRule', backref='product', lazy=True, cascade='all, delete-orphan')

class DeliveryRule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    min_amount = db.Column(db.Float, nullable=False)
    max_amount = db.Column(db.Float, nullable=True)  # None means no upper limit
    fee = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<DeliveryRule {self.id}: D{self.min_amount}-{self.max_amount or "∞"} = D{self.fee}>'

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    total = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='Pending')
    payment_method = db.Column(db.String(50))
    delivery_address = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    items = db.relationship('OrderItem', backref='order', lazy=True)
    
    # Shipping and order management fields
    shipping_status = db.Column(db.String(20), default='pending')  # pending, shipped, delivered
    assigned_to = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    weight_kg = db.Column(db.Float, nullable=True)
    shipping_price = db.Column(db.Float, nullable=True)
    total_cost = db.Column(db.Float, nullable=True)  # total including shipping
    customer_name = db.Column(db.String(255), nullable=True)
    customer_address = db.Column(db.Text, nullable=True)
    customer_phone = db.Column(db.String(50), nullable=True)
    location = db.Column(db.String(50), nullable=True)  # China / Gambia
    shipped_at = db.Column(db.DateTime, nullable=True)
    delivered_at = db.Column(db.DateTime, nullable=True)
    
    # Chinese partner input fields
    product_weight_kg = db.Column(db.Float, nullable=True)
    shipping_price_gmd = db.Column(db.Float, nullable=True)
    total_cost_gmd = db.Column(db.Float, nullable=True)
    details_submitted = db.Column(db.Boolean, default=False)
    submitted_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)  # User ID who submitted
    submitted_at = db.Column(db.DateTime, nullable=True)  # When details were submitted
    
    # Relationship for assigned user
    assigned_user = db.relationship('User', primaryjoin='Order.assigned_to == User.id', foreign_keys=[assigned_to], backref=db.backref('assigned_orders', lazy=True))

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    
    # Relationship
    product = db.relationship('Product', backref='order_items')

class ShipmentRecord(db.Model):
    """Records shipment details submitted by China partners"""
    id = db.Column(db.Integer, primary_key=True)
    weight_total = db.Column(db.Float, nullable=False)
    shipping_price = db.Column(db.Float, nullable=False)
    total_cost = db.Column(db.Float, nullable=False)
    submitted_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    submission_date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    order_ids = db.Column(db.Text, nullable=False)  # Comma-separated list of order IDs
    verified = db.Column(db.Boolean, default=False, nullable=False)  # Admin verification status
    verified_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)  # Admin who verified
    verified_at = db.Column(db.DateTime, nullable=True)  # When it was verified
    
    # Relationships
    submitter = db.relationship('User', foreign_keys=[submitted_by], backref='shipment_records')
    verifier = db.relationship('User', foreign_keys=[verified_by], backref='verified_shipments')

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def merge_carts(user, guest_cart):
    """Merge the anonymous (session) cart into the user's persistent cart."""
    if not guest_cart or not user:
        session.pop('cart', None)
        return

    user_cart_map = {int(item.product_id): item for item in CartItem.query.filter_by(user_id=user.id).all()}
    changes_detected = False

    for product_id_str, raw_qty in guest_cart.items():
        try:
            product_id = int(product_id_str)
            quantity = max(int(raw_qty), 0)
        except (TypeError, ValueError):
            continue

        if quantity <= 0:
            continue

        product = Product.query.get(product_id)
        if not product:
            continue

        if product.stock is not None:
            quantity = min(quantity, product.stock or 0)

        if quantity <= 0:
            continue

        cart_item = user_cart_map.get(product_id)
        if cart_item:
            new_quantity = cart_item.quantity + quantity
            if product.stock is not None:
                new_quantity = min(new_quantity, product.stock)
            if cart_item.quantity != new_quantity:
                cart_item.quantity = new_quantity
                changes_detected = True
        else:
            db.session.add(CartItem(user_id=user.id, product_id=product_id, quantity=quantity))
            changes_detected = True

    if changes_detected:
        db.session.commit()
        db.session.expire(user, ['cart_items'])
        try:
            if current_user.is_authenticated and current_user.id == user.id:
                update_cart()
        except Exception:
            # ignore if called outside request context
            pass

    session.pop('cart', None)

# Routes
# Authentication routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        # Redirect based on role
        if current_user.is_admin or current_user.role == 'admin':
            return redirect(url_for('admin_dashboard'))
        elif current_user.role == 'china_partner':
            return redirect(url_for('china_orders'))
        elif current_user.role == 'gambia_team':
            return redirect(url_for('gambia_orders'))
        return redirect(url_for('home'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            # Don't allow China/Gambia team users to login through main login
            if user.role in ['china_partner', 'gambia_team']:
                flash('Please use the appropriate login page for your role', 'error')
                return render_template('auth/auth/login.html')
            
            login_user(user)
            user.last_login_at = datetime.utcnow()
            ensure_user_profile(user)
            db.session.commit()
            merge_carts(user, session.get('cart'))
            next_page = request.args.get('next')
            if user.is_admin or user.role == 'admin':
                return redirect(next_page or url_for('admin_dashboard'))
            return redirect(next_page or url_for('home'))
        else:
            flash('Invalid username or password', 'error')
            
    return render_template('auth/auth/login.html')

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Handle password reset request"""
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        
        if not email:
            flash('Please enter your email address', 'error')
            return render_template('auth/forgot_password.html')
        
        # Check if user exists
        user = User.query.filter_by(email=email).first()
        
        if user:
            try:
                # Generate password reset token (expires in 30 minutes)
                serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
                token = serializer.dumps({'user_id': user.id}, salt='password-reset')
                
                # Create reset link
                base = app.get_base_url()
                reset_url = f"{base}{url_for('reset_password', token=token, _external=False)}"
                
                # Get user's display name
                user_name = user.display_name
                
                html_body = f'''
                    <html>
                    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                            <h2 style="color: #06b6d4;">BuXin Store</h2>
                            <p>Hi {user_name},</p>
                            <p>Click the link below to reset your password. This link will expire in 30 minutes.</p>
                            <p style="margin: 30px 0;">
                                <a href="{reset_url}" style="background-color: #06b6d4; color: white; padding: 12px 24px; text-decoration: none; border-radius: 8px; display: inline-block;">Reset Password</a>
                            </p>
                            <p>Or copy and paste this link into your browser:</p>
                            <p style="word-break: break-all; color: #06b6d4;">{reset_url}</p>
                            <p style="margin-top: 30px; color: #666; font-size: 12px;">
                                If you didn't request a password reset, please ignore this email.
                            </p>
                        </div>
                    </body>
                    </html>
                    '''

                from app.utils.email_queue import queue_single_email
                app_obj = current_app._get_current_object()
                current_app.logger.info(
                    "forgot_password[BG]: queueing password reset email",
                    extra={"recipient": user.email},
                )
                subject = _format_email_subject('Reset Your Password')
                queue_single_email(app_obj, user.email, subject, html_body)

                current_app.logger.info(f"✅ Password reset email queued for {user.email}")
                # Don't reveal if email exists or not for security
                flash('If that email address is registered, you will receive a password reset link shortly.', 'success')
            except Exception as e:
                current_app.logger.error(f'Error sending password reset email: {str(e)}')
                import traceback
                current_app.logger.error(traceback.format_exc())
                flash('An error occurred while sending the reset email. Please try again later.', 'error')
        else:
            # Don't reveal if email exists or not for security
            flash('If that email address is registered, you will receive a password reset link shortly.', 'success')
        
        return render_template('auth/forgot_password.html')
    
    return render_template('auth/forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """Handle password reset with token"""
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    # Verify token
    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        data = serializer.loads(token, salt='password-reset', max_age=1800)  # 30 minutes = 1800 seconds
        user_id = data.get('user_id')
        user = User.query.get(user_id)
        
        if not user:
            flash('Invalid or expired reset link. Please request a new one.', 'error')
            return render_template('auth/reset_password.html', token=token, error=True)
        
        if request.method == 'POST':
            password = request.form.get('password', '').strip()
            confirm_password = request.form.get('confirm_password', '').strip()
            
            if not password:
                flash('Please enter a new password', 'error')
                return render_template('auth/reset_password.html', token=token)
            
            if password != confirm_password:
                flash('Passwords do not match', 'error')
                return render_template('auth/reset_password.html', token=token)
            
            if len(password) < 6:
                flash('Password must be at least 6 characters long', 'error')
                return render_template('auth/reset_password.html', token=token)
            
            # Update password
            user.set_password(password)
            db.session.commit()
            
            flash('✅ Your password has been reset successfully. You can now log in with your new password.', 'success')
            return redirect(url_for('login'))
        
        return render_template('auth/reset_password.html', token=token)
        
    except SignatureExpired:
        flash('The reset link has expired. Please request a new password reset.', 'error')
        return render_template('auth/reset_password.html', token=token, error=True)
    except BadSignature:
        flash('Invalid or expired reset link. Please request a new one.', 'error')
        return render_template('auth/reset_password.html', token=token, error=True)
    except Exception as e:
        current_app.logger.error(f'Error processing password reset: {str(e)}')
        flash('An error occurred while processing your request. Please try again.', 'error')
        return render_template('auth/reset_password.html', token=token, error=True)

@app.route('/auth/google/login')
def google_login():
    if 'google' not in oauth._clients:
        flash('Google login is not configured.', 'error')
        return redirect(url_for('login'))
    
    try:
        # Pre-check Google connectivity (optional, but helps with user feedback)
        try:
            get_google_openid_config()
        except requests.exceptions.Timeout:
            flash('⚠️ Cannot reach Google at the moment. Please check your network connection and try again later.', 'warning')
            return redirect(url_for('login'))
        except requests.exceptions.RequestException as e:
            current_app.logger.warning(f"Google connectivity check failed: {e}")
            # Continue anyway - the actual OAuth flow might still work
        
        next_url = request.args.get('next') or request.referrer or url_for('home')
        session['next_url'] = next_url

        # Determine redirect URI for Google OAuth.
        # In production (Render), we NEVER fall back to localhost or any
        # hard-coded domain – GOOGLE_REDIRECT_URI must be configured to:
        #   https://store.techbuxin.com/auth/google/callback
        if current_app.config.get('IS_RENDER'):
            redirect_uri = current_app.config.get('GOOGLE_REDIRECT_URI')
            if not redirect_uri:
                current_app.logger.error(
                    "GOOGLE_REDIRECT_URI is not configured on Render; "
                    "cannot start Google OAuth flow."
                )
                flash(
                    'Google login is temporarily unavailable because the callback URL '
                    'is not configured. Please contact support.',
                    'error'
                )
                return redirect(url_for('login'))
        else:
            # In development, allow using either the env var or an automatically
            # generated callback URL based on the current host (e.g. localhost).
            base = current_app.get_base_url()
            redirect_uri = current_app.config.get('GOOGLE_REDIRECT_URI') or (
                f"{base}{url_for('google_callback', _external=False)}" if base else None
            )
        nonce = secrets.token_urlsafe(16)
        session['google_oauth_nonce'] = nonce
        
        return oauth.google.authorize_redirect(
            redirect_uri,
            scope='openid email profile',
            access_type='offline',
            prompt='select_account',
            nonce=nonce
        )
    except requests.exceptions.Timeout:
        flash('⚠️ Connection to Google timed out. Please check your network connection and try again later.', 'warning')
        return redirect(url_for('login'))
    except requests.exceptions.ConnectionError:
        flash('⚠️ Cannot connect to Google. Please check your internet connection and try again later.', 'warning')
        return redirect(url_for('login'))
    except Exception as e:
        current_app.logger.error(f"Google login error: {str(e)}")
        flash('❌ Google login failed. Please try again later or use email/password login.', 'error')
        return redirect(url_for('login'))

@app.route('/auth/google/callback')
def google_callback():
    if 'google' not in oauth._clients:
        flash('Google login is not configured.', 'error')
        return redirect(url_for('login'))
    
    try:
        token = oauth.google.authorize_access_token()
    except requests.exceptions.Timeout:
        current_app.logger.error("Google OAuth token request timed out")
        flash('⚠️ Connection to Google timed out. Please try again later.', 'warning')
        return redirect(url_for('login'))
    except requests.exceptions.ConnectionError:
        current_app.logger.error("Google OAuth connection error")
        flash('⚠️ Cannot connect to Google. Please check your internet connection and try again.', 'warning')
        return redirect(url_for('login'))
    except requests.exceptions.RequestException as e:
        current_app.logger.error(f"Google OAuth request error: {str(e)}")
        flash('⚠️ Google authentication request failed. Please try again later.', 'warning')
        return redirect(url_for('login'))
    except Exception as e:
        current_app.logger.error(f"Google auth error: {str(e)}")
        flash('❌ Google authentication failed. Please try again or use email/password login.', 'error')
        return redirect(url_for('login'))
    
    nonce = session.pop('google_oauth_nonce', None)
    userinfo = None
    
    if nonce:
        try:
            userinfo = oauth.google.parse_id_token(token, nonce=nonce)
        except Exception as e:
            current_app.logger.warning(f"ID token parsing failed: {str(e)}")
    
    if not userinfo:
        try:
            resp = oauth.google.get('userinfo', timeout=10)
            resp.raise_for_status()
            userinfo = resp.json()
        except requests.exceptions.Timeout:
            current_app.logger.error("Google userinfo request timed out")
            flash('⚠️ Connection to Google timed out while fetching user information. Please try again.', 'warning')
            return redirect(url_for('login'))
        except requests.exceptions.RequestException as e:
            current_app.logger.error(f"Google userinfo request error: {str(e)}")
            flash('⚠️ Failed to fetch user information from Google. Please try again.', 'warning')
            return redirect(url_for('login'))
        except Exception as e:
            current_app.logger.error(f"Error fetching userinfo: {str(e)}")
            flash('❌ Failed to retrieve user information from Google. Please try again.', 'error')
            return redirect(url_for('login'))
    email = userinfo.get('email')
    if not email:
        flash('Google account does not provide an email address.', 'error')
        return redirect(url_for('login'))
    google_id = userinfo.get('sub')
    user = None
    if google_id:
        user = User.query.filter_by(google_id=google_id).first()
    if not user:
        user = User.query.filter_by(email=email).first()
    if not user:
        base_username = (userinfo.get('name') or email.split('@')[0]).replace(' ', '').lower()
        username = base_username
        suffix = 1
        while User.query.filter_by(username=username).first():
            username = f"{base_username}{suffix}"
            suffix += 1
        user = User(username=username, email=email, google_id=google_id)
        user.set_password(secrets.token_urlsafe(16))
        db.session.add(user)
        db.session.commit()
    else:
        if google_id and not user.google_id:
            user.google_id = google_id
        if user.email != email:
            user.email = email

    profile = ensure_user_profile(user)
    given_name = userinfo.get('given_name')
    family_name = userinfo.get('family_name')
    if not given_name and not family_name:
        given_name, family_name = split_display_name(userinfo.get('name'))
    if given_name and not profile.first_name:
        profile.first_name = given_name
    if family_name and not profile.last_name:
        profile.last_name = family_name
    picture_url = userinfo.get('picture')
    if picture_url:
        profile.google_avatar_url = picture_url
        profile.google_avatar_synced_at = datetime.utcnow()

    user.last_login_at = datetime.utcnow()
    db.session.commit()
    login_user(user, remember=True)
    merge_carts(user, session.get('cart'))
    session['user_id'] = user.id
    session['email'] = email
    session['name'] = userinfo.get('name')
    session['picture'] = userinfo.get('picture')
    session.permanent = True
    next_url = session.pop('next_url', None)
    return redirect(next_url or url_for('checkout'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        whatsapp_number = request.form.get('whatsapp_number', '').strip()
        
        if password != confirm_password:
            flash('Passwords do not match', 'danger')
            return redirect(url_for('register'))
            
        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'danger')
            return redirect(url_for('register'))
            
        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'danger')
            return redirect(url_for('register'))
        
        # Normalize and validate WhatsApp number if provided
        normalized_whatsapp = None
        if whatsapp_number:
            try:
                normalized_whatsapp = normalize_whatsapp_number(whatsapp_number)
            except ValueError as e:
                flash(f'Invalid WhatsApp number format: {str(e)}', 'danger')
                return redirect(url_for('register'))
            
        user = User(username=username, email=email)
        user.set_password(password)
        if normalized_whatsapp:
            user.whatsapp_number = normalized_whatsapp
        
        db.session.add(user)
        db.session.flush()
        ensure_user_profile(user)
        db.session.commit()
        
        # Send welcome message if WhatsApp number was provided
        if normalized_whatsapp:
            try:
                user_name = user.display_name
                welcome_message = f"Hi {user_name} 👋\nWelcome to BuXin! You'll now receive updates about our robotics and AI innovations. 🚀"
                
                success, error_msg, log_id = send_whatsapp_message_with_logging(
                    whatsapp_number=normalized_whatsapp,
                    message=welcome_message,
                    user_id=user.id
                )
                
                if not success:
                    current_app.logger.warning(f"Failed to send welcome message to {normalized_whatsapp}: {error_msg}")
            except Exception as e:
                current_app.logger.error(f"Error sending welcome message during registration: {str(e)}")
        
        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))
        
    return render_template('auth/register.html')

@app.route('/wishlist')
@login_required
def wishlist():
    wishlist_items = current_user.wishlist_items
    return render_template('wishlist.html', wishlist_items=wishlist_items)

def _get_session_wishlist_ids():
    """
    Return the guest wishlist stored in the session as a list of unique integers.
    Ensures the session copy stays normalised.
    """
    wishlist = session.get('wishlist_items', [])
    if not isinstance(wishlist, list):
        wishlist = list(wishlist) if isinstance(wishlist, (set, tuple)) else []
    normalised = []
    for value in wishlist:
        try:
            product_id = int(value)
        except (TypeError, ValueError):
            continue
        if product_id not in normalised:
            normalised.append(product_id)
    if normalised != wishlist:
        session['wishlist_items'] = normalised
        session.modified = True
    return normalised


def _store_session_wishlist_ids(ids):
    session['wishlist_items'] = list(dict.fromkeys(int(pid) for pid in ids if isinstance(pid, (int, str)) and str(pid).isdigit()))
    session.modified = True


def _resolve_wishlist_ids():
    if current_user.is_authenticated:
        try:
            wishlist_rows = WishlistItem.query.with_entities(WishlistItem.product_id).filter_by(user_id=current_user.id).all()
            return [row.product_id for row in wishlist_rows]
        except Exception as exc:
            current_app.logger.debug(f"Unable to resolve wishlist ids for user {current_user.get_id()}: {exc}")
            return []
    return _get_session_wishlist_ids()


@app.context_processor
def inject_wishlist_context():
    ids = []
    try:
        ids = _resolve_wishlist_ids()
    except Exception as exc:
        current_app.logger.debug(f"Failed to inject wishlist context: {exc}")
        ids = []
    return {
        'wishlist_product_ids': ids,
        'wishlist_count': len(ids)
    }


@app.route('/api/wishlist/toggle/<int:product_id>', methods=['POST'])
@csrf.exempt  # Temporarily disable CSRF for testing
def toggle_wishlist(product_id):
    try:
        product = Product.query.get_or_404(product_id)

        csrf_token = request.headers.get('X-CSRFToken') or request.form.get('csrf_token')
        if not csrf_token:
            return jsonify({
                'status': 'error',
                'message': 'CSRF token is missing'
            }), 400

        if current_user.is_authenticated:
            wishlist_item = WishlistItem.query.filter_by(
                user_id=current_user.id,
                product_id=product_id
            ).first()

            if wishlist_item:
                db.session.delete(wishlist_item)
                db.session.commit()
                ids = _resolve_wishlist_ids()
                return jsonify({
                    'status': 'removed',
                    'message': 'Product removed from wishlist',
                    'wishlist_count': len(ids),
                    'context': 'authenticated',
                    'in_wishlist': False
                })
            wishlist_item = WishlistItem(
                user_id=current_user.id,
                product_id=product_id
            )
            db.session.add(wishlist_item)
            db.session.commit()
            ids = _resolve_wishlist_ids()
            return jsonify({
                'status': 'added',
                'message': 'Product added to wishlist',
                'wishlist_count': len(ids),
                'context': 'authenticated',
                'in_wishlist': True
            })

        # Guest wishlist handling using the session
        wishlist_ids = _get_session_wishlist_ids()
        if product_id in wishlist_ids:
            wishlist_ids.remove(product_id)
            _store_session_wishlist_ids(wishlist_ids)
            return jsonify({
                'status': 'removed',
                'message': 'Product removed from wishlist',
                'wishlist_count': len(wishlist_ids),
                'context': 'guest',
                'in_wishlist': False
            })

        wishlist_ids.append(product_id)
        _store_session_wishlist_ids(wishlist_ids)
        return jsonify({
            'status': 'added',
            'message': 'Product added to wishlist',
            'wishlist_count': len(wishlist_ids),
            'context': 'guest',
            'in_wishlist': True
        })

    except Exception as e:
        if current_user.is_authenticated:
            db.session.rollback()
        app.logger.error(f'Error toggling wishlist: {str(e)}')
        return jsonify({
            'status': 'error',
            'message': 'An error occurred while updating your wishlist',
            'debug': str(e) if app.debug else None
        }), 500


@app.route('/api/wishlist/check/<int:product_id>')
def check_wishlist(product_id):
    if current_user.is_authenticated:
        is_in_wishlist = WishlistItem.query.filter_by(
            user_id=current_user.id,
            product_id=product_id
        ).first() is not None
    else:
        is_in_wishlist = product_id in _get_session_wishlist_ids()

    return jsonify({'in_wishlist': is_in_wishlist})


@app.route('/api/wishlist/check-multiple')
def check_wishlist_multiple():
    raw_ids = request.args.get('ids', '')
    product_ids = raw_ids.split(',') if raw_ids else []
    if not product_ids or product_ids == ['']:
        return jsonify({})

    try:
        product_ids = [int(pid) for pid in product_ids if pid.isdigit()]
    except ValueError:
        return jsonify({'error': 'Invalid product ids'}), 400

    if current_user.is_authenticated:
        wishlist_items = WishlistItem.query.filter(
            WishlistItem.user_id == current_user.id,
            WishlistItem.product_id.in_(product_ids)
        ).all()
        wishlist_product_ids = {item.product_id for item in wishlist_items}
    else:
        wishlist_product_ids = set(_get_session_wishlist_ids())

    result = {str(pid): pid in wishlist_product_ids for pid in product_ids}
    return jsonify(result)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('home'))

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or (not current_user.is_admin and current_user.role != 'admin'):
            flash('Admin access required', 'error')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def china_partner_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Allow admin users or china_partner users
        if not current_user.is_authenticated or not current_user.active:
            flash('Access required', 'error')
            return redirect(url_for('china_login', next=request.url))
        if not (current_user.is_admin or current_user.role == 'admin' or current_user.role == 'china_partner'):
            flash('China Partner or Admin access required', 'error')
            return redirect(url_for('china_login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def gambia_team_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Allow admin users or gambia_team users
        if not current_user.is_authenticated or not current_user.active:
            flash('Access required', 'error')
            return redirect(url_for('gambia_login', next=request.url))
        if not (current_user.is_admin or current_user.role == 'admin' or current_user.role == 'gambia_team'):
            flash('Gambia Team or Admin access required', 'error')
            return redirect(url_for('gambia_login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# Cart management
def get_cart():
    if current_user.is_authenticated:
        items = CartItem.query.filter_by(user_id=current_user.id).all()
        return {str(item.product_id): int(item.quantity or 0) for item in items}

    if 'cart' not in session or session['cart'] is None:
        session['cart'] = {}

    sanitized = {}
    for key, value in session['cart'].items():
        try:
            quantity = int(value)
        except (TypeError, ValueError):
            continue
        if quantity > 0:
            sanitized[str(key)] = quantity

    session['cart'] = sanitized
    session.modified = True
    return sanitized

def update_cart():
    """
    Normalize the cart for the current visitor, ensuring quantities respect stock levels
    and returning a tuple of (cart_items, subtotal).
    """
    cart_items: List[Dict[str, float]] = []
    subtotal = Decimal('0.00')

    if current_user.is_authenticated:
        changes_detected = False
        cart_query = CartItem.query.filter_by(user_id=current_user.id).options(joinedload(CartItem.product))

        for cart_item in cart_query.all():
            product = cart_item.product
            if not product:
                db.session.delete(cart_item)
                changes_detected = True
                continue

            quantity = max(int(cart_item.quantity or 0), 0)
            if product.stock is not None:
                quantity = min(quantity, product.stock)

            if quantity <= 0:
                db.session.delete(cart_item)
                changes_detected = True
                continue

            if quantity != cart_item.quantity:
                cart_item.quantity = quantity
                changes_detected = True

            price = Decimal(str(product.price))
            line_total = (price * quantity).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            subtotal += line_total

            cart_items.append({
                'id': product.id,
                'name': product.name,
                'price': float(price),
                'quantity': quantity,
                'total': float(line_total),
                'image': product.image,
                'stock': product.stock
            })

        if changes_detected:
            db.session.commit()

        return cart_items, float(subtotal)

    # Guest cart (session-backed)
    cart = get_cart()
    sanitized_cart: Dict[str, int] = {}

    for product_id_str, quantity in cart.items():
        try:
            product_id = int(product_id_str)
            quantity = int(quantity)
        except (TypeError, ValueError):
            continue

        product = Product.query.get(product_id)
        if not product:
            continue

        if quantity <= 0:
            continue

        if product.stock is not None:
            quantity = min(quantity, product.stock)
            if quantity <= 0:
                continue

        price = Decimal(str(product.price))
        line_total = (price * quantity).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        subtotal += line_total

        sanitized_cart[str(product.id)] = quantity
        cart_items.append({
            'id': product.id,
            'name': product.name,
            'price': float(price),
            'quantity': quantity,
            'total': float(line_total),
            'image': product.image,
            'stock': product.stock
        })

    session['cart'] = sanitized_cart
    session.modified = True
    return cart_items, float(subtotal)

def calculate_cart_totals(cart_items):
    """
    Given sanitized cart items, compute subtotal, tax, shipping, and total as Decimal values.
    Shipping is calculated per product based on product price:
    - If product price ≤ D1000 → Shipping = D300 per unit
    - If product price > D1000 and ≤ D2000 → Shipping = D800 per unit
    - If product price > D2000 → Shipping = D1200 per unit
    Total shipping = sum of (shipping_per_unit × quantity) for all products
    """
    currency = current_app.config.get('CART_CURRENCY_SYMBOL', 'D')
    tax_rate = Decimal(str(current_app.config.get('CART_TAX_RATE', 0) or 0))

    subtotal = Decimal('0.00')
    total_shipping = Decimal('0.00')
    
    # Calculate per-product shipping and subtotal
    for item in cart_items:
        item_subtotal = Decimal(str(item['total']))
        subtotal += item_subtotal
        
        # Calculate shipping per unit based on product-specific delivery rules
        product_price = Decimal(str(item['price']))
        quantity = Decimal(str(item['quantity']))
        product_id = item.get('id')
        
        # Use calculate_delivery_price with product_id to get custom rules
        shipping_per_unit = Decimal(str(calculate_delivery_price(float(product_price), product_id)))
        
        # Product shipping = shipping_per_unit × quantity
        item_shipping = (shipping_per_unit * quantity).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        total_shipping += item_shipping
        
        # Store per-product shipping in item for later use
        item['shipping_per_unit'] = float(shipping_per_unit)
        item['shipping'] = float(item_shipping)

    tax = (subtotal * tax_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if tax_rate else Decimal('0.00')
    shipping = total_shipping.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    total = subtotal + tax + shipping

    return {
        'currency': currency,
        'subtotal': subtotal,
        'tax': tax,
        'shipping': shipping,
        'total': total
    }


def serialize_cart_summary(cart_items):
    """
    Prepare a complete cart summary payload for templates and JSON responses.
    """
    totals = calculate_cart_totals(cart_items)
    serialized_items = [{
        'id': item['id'],
        'name': item['name'],
        'quantity': int(item['quantity']),
        'price': float(item['price']),
        'item_total': float(item['total']),
        'shipping_per_unit': float(item.get('shipping_per_unit', 0)),
        'shipping': float(item.get('shipping', 0)),
        'image': item.get('image'),
        'stock': item.get('stock'),
        'url': url_for('product', product_id=item['id'])
    } for item in cart_items]

    cart_count = sum(item['quantity'] for item in serialized_items)
    is_empty = len(serialized_items) == 0

    def _to_float(decimal_value: Decimal) -> float:
        return float(decimal_value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

    summary = {
        'currency': totals['currency'],
        'items': serialized_items,
        'count': cart_count,
        'cart_count': cart_count,
        'is_empty': is_empty,
        'subtotal': _to_float(totals['subtotal']),
        'cart_subtotal': _to_float(totals['subtotal']),
        'tax': _to_float(totals['tax']),
        'cart_tax': _to_float(totals['tax']),
        'shipping': _to_float(totals['shipping']),
        'cart_shipping': _to_float(totals['shipping']),
        'total': _to_float(totals['total']),
        'cart_total': _to_float(totals['total']),
    }

    summary['checkout'] = {
        'items': serialized_items,
        'currency': totals['currency'],
        'subtotal': summary['subtotal'],
        'tax': summary['tax'],
        'shipping': summary['shipping'],
        'total': summary['total'],
        'count': cart_count,
        'is_empty': is_empty
    }

    return summary


def find_item_in_summary(summary: Dict[str, any], product_id: int) -> Optional[Dict[str, any]]:
    for item in summary.get('items', []):
        if item.get('id') == product_id:
            return {
                'id': item['id'],
                'name': item['name'],
                'quantity': item['quantity'],
                'price': item['price'],
                'subtotal': item['item_total'],
                'image': item.get('image'),
                'stock': item.get('stock')
            }
    return None


def build_cart_response(
    summary: Dict[str, any],
    message: Optional[str] = None,
    *,
    item: Optional[Dict[str, any]] = None,
    removed: bool = False,
    status: str = 'success'
) -> Dict[str, any]:
    response = {
        'status': status,
        'cart': summary,
        'subtotal': summary['subtotal'],
        'total': summary['total'],
        'count': summary['count'],
        'cart_count': summary['count'],
        'currency': summary['currency'],
        'removed': removed
    }

    if message:
        response['message'] = message

    response['item'] = item

    return response


def get_cart_summary():
    """Helper to fetch the latest cart summary for the current user/session."""
    cart_items, _ = update_cart()
    return serialize_cart_summary(cart_items)

@app.route('/cart')
def cart():
    cart_items, _ = update_cart()
    summary = serialize_cart_summary(cart_items)
    return render_template('cart.html', cart_items=cart_items, cart_summary=summary)

@app.route('/cart/proceed', methods=['GET'])
def cart_proceed():
    """
    Proceed to checkout.
    - Auth users: redirect straight to /checkout
    - Guests: ask them to log in before continuing, preserving cart state
    """
    if not current_user.is_authenticated:
        flash('Please log in to complete your checkout.', 'info')
        return redirect(url_for('login', next=url_for('checkout')))

    return redirect(url_for('checkout'))
@app.route('/add_to_cart/<int:product_id>', methods=['POST'])
@csrf.exempt
def add_to_cart(product_id):
    # Check if request is JSON
    is_json = request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.content_type == 'application/json'
    
    cart = get_cart()
    try:
        if is_json:
            data = request.get_json(silent=True) or {}
            if not data and request.data:
                try:
                    data = json.loads(request.data)
                except json.JSONDecodeError:
                    data = {}
            quantity = int(data.get('quantity', 1))
        else:
            quantity = int(request.form.get('quantity', 1))
    except (ValueError, AttributeError, TypeError) as exc:
        message = 'Invalid quantity provided'
        if is_json:
            return jsonify({'status': 'error', 'message': message, 'error': str(exc)}), 400
        flash('Invalid quantity', 'error')
        return redirect(request.referrer or url_for('home'))
    
    # Ensure quantity is at least 1
    quantity = max(1, quantity)
    
    # Get product to check stock
    product = Product.query.get(product_id)
    if not product:
        if is_json:
            return jsonify({
                'status': 'error',
                'message': f'Product with ID {product_id} not found'
            }), 404
        else:
            flash('Product not found', 'error')
            return redirect(url_for('home'))
    
    try:
        if current_user.is_authenticated:
            user_obj = current_user._get_current_object()
            cart_item = CartItem.query.filter_by(user_id=current_user.id, product_id=product_id).first()
            current_quantity = cart_item.quantity if cart_item else 0
            new_quantity = current_quantity + quantity

            if product.stock is not None and new_quantity > product.stock:
                message = f'Not enough stock. Only {product.stock} available.'
                if is_json:
                    return jsonify({'status': 'error', 'message': message}), 400
                flash(message, 'error')
                return redirect(request.referrer or url_for('home'))

            if cart_item:
                cart_item.quantity = new_quantity
            else:
                cart_item = CartItem(user_id=current_user.id, product_id=product_id, quantity=new_quantity)
                db.session.add(cart_item)

            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                cart_item = CartItem.query.filter_by(user_id=current_user.id, product_id=product_id).first()
                if cart_item:
                    refreshed_quantity = cart_item.quantity + quantity
                    if product.stock is not None:
                        refreshed_quantity = min(refreshed_quantity, product.stock)
                    cart_item.quantity = refreshed_quantity
                    db.session.commit()

            duplicates = CartItem.query.filter_by(user_id=current_user.id, product_id=product_id)\
                .order_by(CartItem.id.asc()).all()
            if duplicates:
                cart_item = duplicates[0]
                if len(duplicates) > 1:
                    total_quantity = sum(max(int(item.quantity or 0), 0) for item in duplicates)
                    if product.stock is not None:
                        total_quantity = min(total_quantity, product.stock)
                    cart_item.quantity = total_quantity
                    for duplicate in duplicates[1:]:
                        db.session.delete(duplicate)
                    db.session.commit()

            if cart_item:
                db.session.refresh(cart_item)
            db.session.expire(user_obj, ['cart_items'])
        else:
            cart = get_cart()
            product_id_str = str(product_id)
            current_quantity = cart.get(product_id_str, 0)
            new_quantity = current_quantity + quantity

            if product.stock is not None and new_quantity > product.stock:
                message = f'Not enough stock. Only {product.stock} available.'
                if is_json:
                    return jsonify({'status': 'error', 'message': message}), 400
                flash(message, 'error')
                return redirect(request.referrer or url_for('home'))

            cart[product_id_str] = new_quantity
            session['cart'] = cart
            session.modified = True

    except Exception as exc:
        message = 'Failed to update cart'
        if is_json:
            return jsonify({'status': 'error', 'message': message, 'error': str(exc)}), 500
        flash(message, 'error')
        return redirect(request.referrer or url_for('home'))
    
    # Recompute summary from source of truth
    cart_items, _ = update_cart()
    summary = serialize_cart_summary(cart_items)
    
    # Prepare response
    item_detail = find_item_in_summary(summary, product.id)
    response_data = build_cart_response(
        summary,
        message=f'Added {product.name} to cart',
        item=item_detail,
        removed=False
    )
    
    if is_json:
        return jsonify(response_data)

    flash(response_data['message'], 'success')
    return redirect(request.referrer or url_for('home'))

@app.route('/update_cart/<int:product_id>', methods=['POST'])
def update_cart_item(product_id):
    try:
        product = Product.query.get(product_id)
        if not product:
            raise ValueError('Product not found')

        payload = request.get_json(silent=True)
        if not payload or 'quantity' not in payload:
            raise ValueError('Quantity is required')

        try:
            quantity = int(payload.get('quantity'))
        except (TypeError, ValueError, InvalidOperation):
            raise ValueError('Invalid quantity')

        if quantity < 0:
            raise ValueError('Quantity must be zero or greater')

        if product.stock is not None and quantity > product.stock:
            raise ValueError(f'Only {product.stock} item(s) available')

        removed = quantity == 0
        if current_user.is_authenticated:
            user_obj = current_user._get_current_object()
            cart_item = CartItem.query.filter_by(user_id=current_user.id, product_id=product_id).first()

            if removed:
                if cart_item:
                    db.session.delete(cart_item)
                removed = True
            else:
                if cart_item:
                    cart_item.quantity = quantity
                else:
                    cart_item = CartItem(user_id=current_user.id, product_id=product_id, quantity=quantity)
                    db.session.add(cart_item)

            db.session.commit()
            if not removed:
                db.session.refresh(cart_item)
            db.session.expire(user_obj, ['cart_items'])
        else:
            cart = get_cart()
            product_id_str = str(product_id)
            if removed:
                cart.pop(product_id_str, None)
            else:
                cart[product_id_str] = quantity
            session['cart'] = cart
            session.modified = True

        cart_items, _ = update_cart()
        summary = serialize_cart_summary(cart_items)
        item_detail = find_item_in_summary(summary, product_id)
        removed = item_detail is None
        message = 'Cart updated' if not removed else 'Item removed from cart'

        response_data = build_cart_response(
            summary,
            message=message,
            item=item_detail,
            removed=removed
        )

        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify(response_data)

        flash(message, 'success')
        return redirect(url_for('cart'))

    except ValueError as err:
        error_message = str(err)
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'status': 'error', 'message': error_message}), 400
            
        flash(error_message, 'error')
        return redirect(url_for('cart'))
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception('Unexpected cart update error')
        error_message = 'Failed to update cart. Please try again.'
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'status': 'error', 'message': error_message}), 500
            
        flash(error_message, 'error')
        return redirect(url_for('cart'))

@app.route('/remove_from_cart/<int:product_id>', methods=['POST'])
def remove_from_cart_route(product_id):
    try:
        product = Product.query.get_or_404(product_id)

        if current_user.is_authenticated:
            cart_item = CartItem.query.filter_by(user_id=current_user.id, product_id=product_id).first()
            if not cart_item:
                return jsonify({'status': 'error', 'message': 'Product not found in cart'}), 404

            user_obj = current_user._get_current_object()
            db.session.delete(cart_item)
            db.session.commit()
            db.session.expire(user_obj, ['cart_items'])
        else:
            cart = session.get('cart', {})
            product_id_str = str(product_id)

            if product_id_str not in cart:
                return jsonify({'status': 'error', 'message': 'Product not found in cart'}), 404

            cart.pop(product_id_str, None)
            session['cart'] = cart
            session.modified = True

        cart_items, _ = update_cart()
        summary = serialize_cart_summary(cart_items)
        item_detail = find_item_in_summary(summary, product_id)
        response = build_cart_response(
            summary,
            message='Product removed from cart',
            item=item_detail,
            removed=True
        )

        return jsonify(response)
        
    except Exception as e:
        error_message = str(e)
        print(f"Error removing from cart: {error_message}")
        return jsonify({
            'status': 'error',
            'message': 'Failed to remove item from cart'
        }), 500
def get_cart_count():
    summary = get_cart_summary()
    return summary['count']

@app.route('/api/cart/count')
def api_get_cart_count():
    """API endpoint to get the current cart count"""
    summary = get_cart_summary()
    response = build_cart_response(summary, message='Cart count updated', item=None, removed=False)
    return jsonify(response)


@app.route('/api/cart/summary')
def api_get_cart_summary():
    """Return the full cart summary for client-side synchronization."""
    summary = get_cart_summary()
    response = build_cart_response(summary, message='Cart summary retrieved', item=None, removed=False)
    return jsonify(response)

# ... rest of the code remains the same ...
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, SelectField, TextAreaField, SubmitField, IntegerField, FloatField, BooleanField
from wtforms.validators import DataRequired, Email, NumberRange

class CheckoutForm(FlaskForm):
    full_name = StringField('Full Name', validators=[DataRequired()])
    email = StringField('Email', validators=[DataRequired(), Email()])
    phone = StringField('Phone', validators=[DataRequired()])
    delivery_address = TextAreaField('Delivery Address', validators=[DataRequired()])
    payment_method = SelectField('Payment Method', 
                               choices=[
                                   ('', 'Select a payment method'),
                                   ('wave', 'Wave Money'),
                                   ('qmoney', 'QMoney'),
                                   ('afrimoney', 'AfriMoney'),
                                   ('ecobank', 'ECOBANK Mobile')
                               ],
                               validators=[DataRequired()])
    submit = SubmitField('Place Order')


def calculate_delivery_price(price, product_id=None):
    """
    Calculate delivery price based on product-specific delivery rules.
    If product_id is provided, uses database rules. Otherwise falls back to default.
    
    Args:
        price: Product price
        product_id: Optional product ID to look up custom delivery rules
    
    Returns:
        Delivery fee amount
    """
    if price is None:
        return 0.0  # Default fallback
    
    # If product_id is provided, try to use database rules
    if product_id:
        # Load product with delivery_rules relationship eagerly
        from sqlalchemy.orm import joinedload
        product = Product.query.options(joinedload(Product.delivery_rules)).get(product_id)
        
        if product:
            # First, try to use custom delivery rules
            if product.delivery_rules:
                # Sort rules by min_amount (ascending) to find the first matching rule
                rules = sorted(product.delivery_rules, key=lambda r: r.min_amount)
                for rule in rules:
                    # Check if price falls within this rule's range
                    if price >= rule.min_amount:
                        # If max_amount is None, it means no upper limit
                        if rule.max_amount is None or price <= rule.max_amount:
                            return float(rule.fee)
            
            # If no rule matches, use the product's delivery_price if set
            if product and product.delivery_price:
                return float(product.delivery_price)
    
    # Final fallback: default to 0.00
    return 0.0

class ProductForm(FlaskForm):
    name = StringField('Product Name', validators=[DataRequired()])
    description = TextAreaField('Description', validators=[DataRequired()])
    price = FloatField('Price', validators=[DataRequired(), NumberRange(min=0.01)])
    stock = IntegerField('Stock', validators=[DataRequired(), NumberRange(min=0)])
    category_id = SelectField('Category', coerce=int, validators=[DataRequired()], choices=[])
    image = FileField('Product Image', validators=[
        FileAllowed(['jpg', 'jpeg', 'png'], 'Images only!')
    ])
    available_in_gambia = BooleanField('Available in Gambia', default=False)
    delivery_price = FloatField('Delivery Fee', validators=[NumberRange(min=0)])
    shipping_price = FloatField('Shipping Price', validators=[NumberRange(min=0)])
    location = SelectField('Product Location', 
                          choices=[
                              ('', 'Select location'),
                              ('In The Gambia', 'In The Gambia'),
                              ('Outside The Gambia', 'Outside The Gambia')
                          ])
    submit = SubmitField('Save Product')
    
    def __init__(self, *args, **kwargs):
        super(ProductForm, self).__init__(*args, **kwargs)
        # Update choices for category field
        self.category_id.choices = [(c.id, c.name) for c in Category.query.order_by('name').all()]

@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    """
    Checkout page with ModemPay payment integration.
    GET: Shows checkout form with ModemPay payment options
    POST: Creates order and initiates ModemPay payment
    """
    cart_items, _ = update_cart()
    cart_summary = serialize_cart_summary(cart_items)
    checkout_summary = cart_summary['checkout']
    
    if not cart_items:
        flash('Your cart is empty', 'warning')
        return redirect(url_for('cart'))
    
    # Handle GET request - show checkout page
    if request.method == 'GET':
        try:
            # Pre-fill user data
            user_phone = getattr(current_user, 'phone', '')
            user_email = current_user.email
            
            # The form is needed for the GET request to render the fields
            form = CheckoutForm(
                full_name=current_user.username,
                email=user_email,
                phone=user_phone
            )

            return render_template('checkout.html', 
                                 cart_items=cart_items, 
                                 cart_summary=cart_summary,
                                 checkout_summary=checkout_summary,
                                 total=checkout_summary['total'],
                                 form=form,
                                 user_phone=user_phone,
                                 user_email=user_email,
                                 order_id=0)  # Placeholder, order is created on POST
            
        except Exception as e:
            db.session.rollback()
            app.logger.error(f'Error creating order: {str(e)}')
            flash('An error occurred while processing your order. Please try again.', 'error')
            return redirect(url_for('cart'))
    
    # Handle POST request - This will now be the primary action from the checkout form
    form = CheckoutForm()
    
    if form.validate_on_submit():
        try:
            from app.payments.services import PaymentService
            # Calculate shipping price and total cost
            shipping_price = calculate_delivery_price(checkout_summary['total'])
            total_cost = checkout_summary['total'] + shipping_price
            
            # Create order
            order = Order(
                user_id=current_user.id,
                total=checkout_summary['total'],
                payment_method=form.payment_method.data,
                delivery_address=form.delivery_address.data,
                status='pending',
                shipping_status='pending',  # New field for order management
                shipping_price=shipping_price,
                total_cost=total_cost,
                customer_name=form.full_name.data if hasattr(form, 'full_name') else current_user.username,
                customer_address=form.delivery_address.data,
                customer_phone=form.phone.data if hasattr(form, 'phone') else None,
                location='China'  # Default location, can be updated later
            )
            
            db.session.add(order)
            
            # Add order items and update stock
            for item in cart_items:
                product = Product.query.get(item['id'])
                if not product or product.stock < item['quantity']:
                    flash(f'Sorry, {item["name"]} is out of stock or the quantity is not available', 'error')
                    return redirect(url_for('cart'))
                
                order_item = OrderItem(
                    order=order,
                    product_id=item['id'],
                    quantity=item['quantity'],
                    price=item['price']
                )
                
                # Update product stock
                product.stock -= item['quantity']
                db.session.add(order_item)
            
            db.session.commit() # Commit to get the order.id
            
            # Initiate ModemPay payment
            payment_result = PaymentService.start_modempay_payment(
                order_id=order.id,
                amount=checkout_summary['total'],
                phone=form.phone.data,
                provider=form.payment_method.data,
                customer_name=form.full_name.data if hasattr(form, 'full_name') else None,
                customer_email=form.email.data if hasattr(form, 'email') else None
            )

            if payment_result.get('success'):
                payment_url = payment_result.get('data', {}).get('payment_url')
                if payment_url:
                    # Clear the cart after successful payment initiation
                    if current_user.is_authenticated:
                        CartItem.query.filter_by(user_id=current_user.id).delete()
                    else:
                        session.pop('cart', None)
                    db.session.commit()
                    
                    # Redirect user to the payment gateway
                    return redirect(payment_url)
            
            # If payment initiation fails, flash a message
            flash(payment_result.get('message', 'Failed to initiate payment. Please try again.'), 'error')
            return redirect(url_for('checkout'))
            
        except Exception as e:
            db.session.rollback()
            app.logger.error(f'Error during checkout: {str(e)}')
            flash('An error occurred while processing your order. Please try again.', 'error')
            return redirect(url_for('cart'))

    # If form is not valid or it's a GET request with a form error
    return render_template('checkout.html', 
                           cart_items=cart_items, 
                           cart_summary=cart_summary,
                           checkout_summary=checkout_summary,
                           total=checkout_summary['total'],
                           form=form)

# Admin routes
class MigrationForm(FlaskForm):
    submit = SubmitField('Run Migration')

@app.route('/admin/migrate', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_run_migration():
    """Admin endpoint to run database migrations"""
    form = MigrationForm()
    
    if form.validate_on_submit():
        try:
            from flask_migrate import upgrade
            with app.app_context():
                upgrade()
            flash('Database migration completed successfully!', 'success')
            current_app.logger.info("✅ Database migration completed via admin endpoint")
        except Exception as e:
            flash(f'Migration failed: {str(e)}', 'error')
            current_app.logger.error(f"❌ Migration failed: {str(e)}", exc_info=True)
        return redirect(url_for('admin_dashboard'))
    
    # GET request - show migration status
    try:
        from flask_migrate import current
        current_revision = current()
    except Exception as e:
        current_revision = f"Error: {str(e)}"
    
    return render_template('admin/admin/migrate.html', form=form, current_revision=current_revision)

@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    # Calculate total sales for the current month
    # Use shipping_status for delivery tracking, exclude cancelled orders
    current_month = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_sales = db.session.query(db.func.sum(Order.total)).filter(
        Order.shipping_status == 'delivered',
        Order.status != 'Cancelled',
        Order.created_at >= current_month
    ).scalar() or 0
    
    # Calculate sales change percentage
    last_month = (current_month - timedelta(days=1)).replace(day=1)
    last_month_sales = db.session.query(db.func.sum(Order.total)).filter(
        Order.shipping_status == 'delivered',
        Order.status != 'Cancelled',
        Order.created_at >= last_month,
        Order.created_at < current_month
    ).scalar() or 0
    
    sales_change = ((monthly_sales - last_month_sales) / last_month_sales * 100) if last_month_sales > 0 else 100
    
    # Get order counts (exclude cancelled)
    total_orders = Order.query.filter(Order.status != 'Cancelled').count()
    last_month_orders = Order.query.filter(
        Order.status != 'Cancelled',
        Order.created_at >= last_month,
        Order.created_at < current_month
    ).count()
    current_month_orders = Order.query.filter(
        Order.status != 'Cancelled',
        Order.created_at >= current_month
    ).count()
    orders_change = ((current_month_orders - last_month_orders) / last_month_orders * 100) if last_month_orders > 0 else 100
    
    stats = {
        'total_sales': monthly_sales,
        'sales_change': round(sales_change, 1),
        'total_orders': total_orders,
        'orders_change': round(orders_change, 1),
        'total_products': Product.query.count(),
        'total_customers': User.query.count(),
        'revenue': db.session.query(db.func.sum(Order.total)).filter(
            Order.status != 'Cancelled'
        ).scalar() or 0
    }
    
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(5).all()
    low_stock_products = Product.query.filter(Product.stock < 10).order_by(Product.stock).limit(5).all()
    
    return render_template('admin/admin/dashboard.html',
                         stats=stats,
                         recent_orders=recent_orders,
                         low_stock_products=low_stock_products)

@app.route('/admin/site-settings', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_site_settings():
    settings = SiteSettings.query.first()
    if not settings:
        settings = SiteSettings()
        db.session.add(settings)
        db.session.commit()
    if request.method == 'POST':
        try:
            settings.hero_title = request.form.get('hero_title', settings.hero_title)
            settings.hero_subtitle = request.form.get('hero_subtitle', settings.hero_subtitle)
            settings.hero_button_text = request.form.get('hero_button_text', settings.hero_button_text)
            settings.hero_button_link = request.form.get('hero_button_link', settings.hero_button_link)
            logo_file = request.files.get('logo')
            if logo_file and logo_file.filename:
                if allowed_file(logo_file.filename):
                    from .utils.cloudinary_utils import upload_to_cloudinary, delete_from_cloudinary, is_cloudinary_url, get_public_id_from_url
                    
                    # Delete old logo from Cloudinary if it exists
                    if settings.logo_path and is_cloudinary_url(settings.logo_path):
                        public_id = get_public_id_from_url(settings.logo_path)
                        if public_id:
                            delete_from_cloudinary(public_id)
                    
                    upload_result = upload_to_cloudinary(logo_file, folder='branding')
                    if upload_result:
                        settings.logo_path = upload_result['url']
                        current_app.logger.info(f"✅ Logo uploaded to Cloudinary: {settings.logo_path}")
                    else:
                        flash('Failed to upload logo to Cloudinary. Please try again.', 'error')
                        return redirect(url_for('admin_site_settings'))
                else:
                    flash('Invalid logo file type. Please upload an image.', 'error')
                    return redirect(url_for('admin_site_settings'))
            
            hero_image_file = request.files.get('hero_image')
            if hero_image_file and hero_image_file.filename:
                if allowed_file(hero_image_file.filename):
                    from .utils.cloudinary_utils import upload_to_cloudinary, delete_from_cloudinary, is_cloudinary_url, get_public_id_from_url
                    
                    # Delete old hero image from Cloudinary if it exists
                    if settings.hero_image_path and is_cloudinary_url(settings.hero_image_path):
                        public_id = get_public_id_from_url(settings.hero_image_path)
                        if public_id:
                            delete_from_cloudinary(public_id)
                    
                    upload_result = upload_to_cloudinary(hero_image_file, folder='branding')
                    if upload_result:
                        settings.hero_image_path = upload_result['url']
                        current_app.logger.info(f"✅ Hero image uploaded to Cloudinary: {settings.hero_image_path}")
                    else:
                        flash('Failed to upload hero image to Cloudinary. Please try again.', 'error')
                        return redirect(url_for('admin_site_settings'))
                else:
                    flash('Invalid hero image file type. Please upload an image.', 'error')
                    return redirect(url_for('admin_site_settings'))
            
            db.session.commit()
            flash('Site settings updated successfully.', 'success')
            return redirect(url_for('admin_site_settings'))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating site settings: {str(e)}")
            flash('Failed to update site settings. Please try again.', 'error')
    # Handle logo preview URL - check if it's Cloudinary or local
    if settings.logo_path:
        from .utils.cloudinary_utils import is_cloudinary_url
        if is_cloudinary_url(settings.logo_path):
            logo_preview = settings.logo_path
        else:
            logo_preview = url_for('static', filename=settings.logo_path)
    else:
        logo_preview = DEFAULT_LOGO_URL
    
    # Handle hero preview URL - check if it's Cloudinary or local
    if settings.hero_image_path:
        from .utils.cloudinary_utils import is_cloudinary_url
        if is_cloudinary_url(settings.hero_image_path):
            hero_preview = settings.hero_image_path
        else:
            hero_preview = url_for('static', filename=settings.hero_image_path)
    else:
        hero_preview = None
    return render_template('admin/admin/site_settings.html', settings=settings, logo_preview=logo_preview, hero_preview=hero_preview)

@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_settings():
    """Comprehensive App Settings page - main control center"""
    # Get or create settings - handle missing columns gracefully
    try:
        settings = AppSettings.query.first()
        if not settings:
            settings = AppSettings()
            db.session.add(settings)
            db.session.commit()
    except Exception as e:
        # Migration hasn't run yet - use getattr with defaults for new columns
        from sqlalchemy.exc import ProgrammingError
        if isinstance(e, ProgrammingError) and 'does not exist' in str(e):
            current_app.logger.warning("Migration not run yet - using safe access for new columns")
            # Try to get existing settings using raw query
            try:
                from sqlalchemy import text
                result = db.session.execute(text("SELECT id FROM app_settings LIMIT 1"))
                row = result.fetchone()
                if row:
                    # Create a wrapper that loads all existing fields except the new ones
                    settings_id = row[0]
                    try:
                        # Load all existing fields using raw SQL (excluding new columns)
                        result = db.session.execute(text(
                            "SELECT business_name, website_url, support_email, contact_whatsapp, "
                            "company_logo_url, modempay_api_key, modempay_public_key, "
                            "payment_return_url, payment_cancel_url, payments_enabled, "
                            "cloudinary_cloud_name, cloudinary_api_key, cloudinary_api_secret, "
                            "whatsapp_access_token, whatsapp_phone_number_id, whatsapp_business_name, "
                            "whatsapp_bulk_messaging_enabled, smtp_server, smtp_port, smtp_use_tls, "
                            "smtp_username, smtp_password, ai_api_key, ai_auto_prompt_improvements, "
                            "backup_enabled, backup_time, backup_email, backup_retention_days, "
                            "backup_last_run, backup_last_status, backup_last_message, updated_at "
                            "FROM app_settings WHERE id = :id"
                        ), {"id": settings_id})
                        data = result.fetchone()
                        
                        # Create a simple object with all fields
                        class SafeSettings:
                            def __init__(self):
                                self.id = settings_id
                                # New columns (don't exist yet)
                                self.contact_whatsapp_receiver = None
                                self.contact_email_receiver = None
                                
                                # Load existing fields
                                if data:
                                    self.business_name = data[0]
                                    self.website_url = data[1]
                                    self.support_email = data[2]
                                    self.contact_whatsapp = data[3]
                                    self.company_logo_url = data[4]
                                    self.modempay_api_key = data[5]
                                    self.modempay_public_key = data[6]
                                    self.payment_return_url = data[7]
                                    self.payment_cancel_url = data[8]
                                    self.payments_enabled = data[9]
                                    self.cloudinary_cloud_name = data[10]
                                    self.cloudinary_api_key = data[11]
                                    self.cloudinary_api_secret = data[12]
                                    self.whatsapp_access_token = data[13]
                                    self.whatsapp_phone_number_id = data[14]
                                    self.whatsapp_business_name = data[15]
                                    self.whatsapp_bulk_messaging_enabled = data[16]
                                    self.smtp_server = data[17]
                                    self.smtp_port = data[18]
                                    self.smtp_use_tls = data[19]
                                    self.smtp_username = data[20]
                                    self.smtp_password = data[21]
                                    self.ai_api_key = data[22]
                                    self.ai_auto_prompt_improvements = data[23]
                                    self.backup_enabled = data[24]
                                    self.backup_time = data[25]
                                    self.backup_email = data[26]
                                    self.backup_retention_days = data[27]
                                    self.backup_last_run = data[28]
                                    self.backup_last_status = data[29]
                                    self.backup_last_message = data[30]
                                    self.updated_at = data[31]
                                else:
                                    # Set defaults if no data
                                    self.business_name = None
                                    self.website_url = None
                                    self.support_email = None
                                    self.contact_whatsapp = None
                                    self.company_logo_url = None
                                    self.modempay_api_key = None
                                    self.modempay_public_key = None
                                    self.payment_return_url = None
                                    self.payment_cancel_url = None
                                    self.payments_enabled = None
                                    self.cloudinary_cloud_name = None
                                    self.cloudinary_api_key = None
                                    self.cloudinary_api_secret = None
                                    self.whatsapp_access_token = None
                                    self.whatsapp_phone_number_id = None
                                    self.whatsapp_business_name = None
                                    self.whatsapp_bulk_messaging_enabled = None
                                    self.smtp_server = None
                                    self.smtp_port = None
                                    self.smtp_use_tls = None
                                    self.smtp_username = None
                                    self.smtp_password = None
                                    self.ai_api_key = None
                                    self.ai_auto_prompt_improvements = None
                                    self.backup_enabled = None
                                    self.backup_time = None
                                    self.backup_email = None
                                    self.backup_retention_days = None
                                    self.backup_last_run = None
                                    self.backup_last_status = None
                                    self.backup_last_message = None
                                    self.updated_at = None
                        
                        settings = SafeSettings()
                    except Exception as e:
                        current_app.logger.error(f"Error loading settings: {str(e)}")
                        # Fallback to minimal settings
                        class SafeSettings:
                            contact_whatsapp_receiver = None
                            contact_email_receiver = None
                        settings = SafeSettings()
                else:
                    # No settings exist - create empty wrapper
                    class SafeSettings:
                        contact_whatsapp_receiver = None
                        contact_email_receiver = None
                    settings = SafeSettings()
            except Exception:
                # Fallback to empty settings
                class SafeSettings:
                    contact_whatsapp_receiver = None
                    contact_email_receiver = None
                settings = SafeSettings()
        else:
            # Different error - re-raise
            raise
    
    # Get stats for Data & Security section (with defensive error handling)
    from sqlalchemy import func, text
    from sqlalchemy.exc import InternalError, OperationalError, ProgrammingError
    
    # Safely get customer count with rollback on error
    try:
        total_customers = User.query.filter(User.role == 'customer').count()
    except (InternalError, OperationalError, ProgrammingError) as e:
        current_app.logger.warning(f"Error counting customers (role column may not exist): {str(e)}")
        try:
            db.session.rollback()
            # Try alternative query if role column doesn't exist
            total_customers = User.query.filter(~User.is_admin).count()
        except Exception as e2:
            current_app.logger.error(f"Error in fallback customer count: {str(e2)}")
            db.session.rollback()
            total_customers = 0
    
    # Safely get product count
    try:
        total_products = Product.query.count()
    except Exception as e:
        current_app.logger.error(f"Error counting products: {str(e)}")
        db.session.rollback()
        total_products = 0
    
    # Safely get order count
    try:
        total_orders = Order.query.count()
    except Exception as e:
        current_app.logger.error(f"Error counting orders: {str(e)}")
        db.session.rollback()
        total_orders = 0
    
    # Handle POST requests
    if request.method == 'POST':
        section = request.form.get('section')
        
        # Check if settings is a SafeSettings wrapper (migration hasn't run)
        is_safe_settings = hasattr(settings, '__class__') and settings.__class__.__name__ == 'SafeSettings'
        if is_safe_settings and section == 'general':
            flash('⚠️ Database migration required! Please run: python -m alembic upgrade head. The new receiver fields cannot be saved until the migration is complete.', 'warning')
            flash('Note: Contact receiver settings will be available after running the migration.', 'info')
            return redirect(url_for('admin_settings'))
        
        try:
            if section == 'general':
                settings.business_name = request.form.get('business_name', '').strip()
                settings.website_url = request.form.get('website_url', '').strip()
                settings.support_email = request.form.get('support_email', '').strip()
                settings.contact_whatsapp = request.form.get('contact_whatsapp', '').strip()
                # Only try to save new fields if they exist (migration has run)
                try:
                    if hasattr(settings, 'contact_whatsapp_receiver'):
                        settings.contact_whatsapp_receiver = request.form.get('contact_whatsapp_receiver', '').strip()
                    if hasattr(settings, 'contact_email_receiver'):
                        settings.contact_email_receiver = request.form.get('contact_email_receiver', '').strip()
                    # Save new default receiver fields
                    if hasattr(settings, 'whatsapp_receiver'):
                        settings.whatsapp_receiver = request.form.get('whatsapp_receiver', '').strip() or '+2200000000'
                    if hasattr(settings, 'email_receiver'):
                        settings.email_receiver = request.form.get('email_receiver', '').strip() or 'buxinstore9@gmail.com'
                except Exception:
                    # Columns don't exist yet - skip saving them
                    current_app.logger.warning("New receiver columns don't exist yet - migration needs to run")
                    pass
                
                # Handle logo upload
                logo_file = request.files.get('company_logo')
                if logo_file and logo_file.filename:
                    if allowed_file(logo_file.filename):
                        from .utils.cloudinary_utils import upload_to_cloudinary, delete_from_cloudinary, is_cloudinary_url, get_public_id_from_url
                        
                        # Delete old logo if exists
                        if settings.company_logo_url and is_cloudinary_url(settings.company_logo_url):
                            public_id = get_public_id_from_url(settings.company_logo_url)
                            if public_id:
                                delete_from_cloudinary(public_id)
                        
                        upload_result = upload_to_cloudinary(logo_file, folder='branding')
                        if upload_result:
                            settings.company_logo_url = upload_result['url']
                        else:
                            flash('Failed to upload logo to Cloudinary.', 'error')
                            return redirect(url_for('admin_settings'))
                    else:
                        flash('Invalid logo file type.', 'error')
                        return redirect(url_for('admin_settings'))
                
                db.session.commit()
                flash('General settings updated successfully.', 'success')
                
            elif section == 'payment':
                settings.modempay_api_key = request.form.get('modempay_api_key', '').strip()
                settings.modempay_public_key = request.form.get('modempay_public_key', '').strip()
                settings.payment_return_url = request.form.get('payment_return_url', '').strip()
                settings.payment_cancel_url = request.form.get('payment_cancel_url', '').strip()
                settings.payments_enabled = request.form.get('payments_enabled') == 'on'
                
                db.session.commit()
                flash('Payment settings updated successfully.', 'success')
                
            elif section == 'cloudinary':
                settings.cloudinary_cloud_name = request.form.get('cloudinary_cloud_name', '').strip()
                settings.cloudinary_api_key = request.form.get('cloudinary_api_key', '').strip()
                api_secret = request.form.get('cloudinary_api_secret', '').strip()
                # Only update if provided (to avoid overwriting with empty)
                if api_secret:
                    settings.cloudinary_api_secret = api_secret
                
                db.session.commit()
                flash('Cloudinary settings updated successfully.', 'success')
                
            elif section == 'whatsapp':
                settings.whatsapp_access_token = request.form.get('whatsapp_access_token', '').strip()
                settings.whatsapp_phone_number_id = request.form.get('whatsapp_phone_number_id', '').strip()
                settings.whatsapp_business_name = request.form.get('whatsapp_business_name', '').strip()
                settings.whatsapp_bulk_messaging_enabled = request.form.get('whatsapp_bulk_messaging_enabled') == 'on'
                
                db.session.commit()
                flash('WhatsApp settings updated successfully.', 'success')
                
            elif section == 'email':
                # Resend email settings
                settings.resend_api_key = request.form.get('resend_api_key', '').strip()
                settings.resend_from_email = request.form.get('resend_from_email', '').strip()
                settings.resend_default_recipient = request.form.get('resend_default_recipient', '').strip()
                settings.resend_enabled = request.form.get('resend_enabled') == 'on'
                settings.contact_email = request.form.get('contact_email', '').strip()
                settings.default_subject_prefix = request.form.get('default_subject_prefix', 'BuXin Store').strip()
                
                db.session.commit()
                flash('Email settings updated successfully.', 'success')
                
            elif section == 'ai':
                settings.ai_api_key = request.form.get('ai_api_key', '').strip()
                settings.ai_auto_prompt_improvements = request.form.get('ai_auto_prompt_improvements') == 'on'
                
                db.session.commit()
                flash('AI settings updated successfully.', 'success')
            
            return redirect(url_for('admin_settings'))
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating settings: {str(e)}")
            flash(f'Failed to update settings: {str(e)}', 'error')
            return redirect(url_for('admin_settings'))
    
    # Load current values from database or environment
    # If DB values are empty, try to load from environment
    if not settings.business_name:
        settings.business_name = os.getenv('BUSINESS_NAME', '')
    if not settings.website_url:
        # Prefer explicit WEBSITE_URL, otherwise fall back to the unified base URL helper.
        explicit_website_url = os.getenv("WEBSITE_URL")
        settings.website_url = (explicit_website_url or current_app.get_base_url() or "").rstrip("/")
    if not settings.support_email:
        settings.support_email = os.getenv('SUPPORT_EMAIL', '')
    if not settings.contact_whatsapp:
        settings.contact_whatsapp = os.getenv('CONTACT_WHATSAPP', '')
    
    if not settings.modempay_api_key:
        settings.modempay_api_key = os.getenv('MODEM_PAY_API_KEY') or os.getenv('MODEMPAY_SECRET_KEY', '')
    if not settings.modempay_public_key:
        settings.modempay_public_key = os.getenv('MODEM_PAY_PUBLIC_KEY') or os.getenv('MODEMPAY_PUBLIC_KEY', '')
    
    if not settings.cloudinary_cloud_name:
        settings.cloudinary_cloud_name = os.getenv('CLOUDINARY_CLOUD_NAME', '')
    if not settings.cloudinary_api_key:
        settings.cloudinary_api_key = os.getenv('CLOUDINARY_API_KEY', '')
    if not settings.cloudinary_api_secret:
        settings.cloudinary_api_secret = os.getenv('CLOUDINARY_API_SECRET', '')
    
    if not settings.whatsapp_access_token:
        settings.whatsapp_access_token = os.getenv('WHATSAPP_ACCESS_TOKEN', '')
    if not settings.whatsapp_phone_number_id:
        settings.whatsapp_phone_number_id = os.getenv('WHATSAPP_PHONE_NUMBER_ID', '')
    if not settings.whatsapp_business_name:
        settings.whatsapp_business_name = os.getenv('BUSINESS_NAME', os.getenv('WHATSAPP_BUSINESS_NAME', ''))
    
    # Resend email settings (load from environment if not set)
    if not settings.resend_api_key:
        settings.resend_api_key = os.getenv('RESEND_API_KEY', '')
    if not settings.resend_from_email:
        settings.resend_from_email = os.getenv('RESEND_FROM_EMAIL', 'onboarding@resend.dev')
    if not settings.resend_default_recipient:
        settings.resend_default_recipient = os.getenv('RESEND_DEFAULT_RECIPIENT', '')
    if settings.resend_enabled is None:
        settings.resend_enabled = os.getenv('RESEND_ENABLED', 'True').lower() == 'true'
    if not settings.contact_email:
        settings.contact_email = os.getenv('SUPPORT_EMAIL', '')
    if not settings.default_subject_prefix:
        settings.default_subject_prefix = os.getenv('EMAIL_SUBJECT_PREFIX', 'BuXin Store')
    # Default communication receivers (load from environment if not set)
    if not settings.whatsapp_receiver:
        settings.whatsapp_receiver = os.getenv('WHATSAPP_RECEIVER', '+2200000000')
    if not settings.email_receiver:
        settings.email_receiver = os.getenv('EMAIL_RECEIVER', 'buxinstore9@gmail.com')
    if not settings.backup_time:
        settings.backup_time = '02:00'
    if not settings.backup_email:
        settings.backup_email = settings.email_receiver or settings.contact_email or settings.resend_from_email or settings.resend_default_recipient or os.getenv('RESEND_DEFAULT_RECIPIENT', '')
    if not settings.backup_retention_days:
        settings.backup_retention_days = 30
    
    return render_template('admin/admin/settings.html', 
                         settings=settings,
                         total_customers=total_customers,
                         total_products=total_products,
                         total_orders=total_orders)

@app.route('/admin/settings/test-payment', methods=['POST'])
@login_required
@admin_required
def admin_settings_test_payment():
    """Test ModemPay payment configuration"""
    try:
        settings = AppSettings.query.first()
        if not settings:
            return jsonify({'success': False, 'message': 'Settings not found'}), 400
        
        api_key = settings.modempay_public_key or os.getenv('MODEM_PAY_PUBLIC_KEY') or os.getenv('MODEMPAY_PUBLIC_KEY')
        if not api_key:
            return jsonify({'success': False, 'message': 'ModemPay API key not configured'}), 400
        
        # Build test payment URL
        base = current_app.get_base_url()
        cancel_url = settings.payment_cancel_url or f"{base}{url_for('payment_failure', _external=False)}"
        return_url = settings.payment_return_url or f"{base}{url_for('payment_success', _external=False)}"
        
        # Create a test payment link
        payload = {
            "amount": 100,  # Test amount: 1 GMD
            "customer_name": "Test User",
            "customer_email": "test@example.com",
            "customer_phone": "+2200000000",
            "cancel_url": cancel_url,
            "return_url": return_url,
            "currency": "GMD",
        }
        
        response = requests.post(
            "https://checkout.modempay.com/api/pay",
            data=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            return jsonify({
                'success': True,
                'message': 'Payment configuration is working!',
                'payment_url': result.get('payment_url', '')
            })
        else:
            return jsonify({
                'success': False,
                'message': f'Payment test failed: {response.status_code} - {response.text[:200]}'
            }), 400
            
    except Exception as e:
        current_app.logger.error(f"Payment test error: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

@app.route('/admin/settings/test-cloudinary', methods=['POST'])
@login_required
@admin_required
def admin_settings_test_cloudinary():
    """Test Cloudinary upload"""
    try:
        settings = AppSettings.query.first()
        if not settings:
            return jsonify({'success': False, 'message': 'Settings not found'}), 400
        
        cloud_name = settings.cloudinary_cloud_name or os.getenv('CLOUDINARY_CLOUD_NAME')
        api_key = settings.cloudinary_api_key or os.getenv('CLOUDINARY_API_KEY')
        api_secret = settings.cloudinary_api_secret or os.getenv('CLOUDINARY_API_SECRET')
        
        if not all([cloud_name, api_key, api_secret]):
            return jsonify({'success': False, 'message': 'Cloudinary credentials not configured'}), 400
        
        # Temporarily update Cloudinary config
        import cloudinary
        cloudinary.config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret,
            secure=True
        )
        
        # Create a small test image
        from PIL import Image
        from io import BytesIO
        import base64
        
        img = Image.new('RGB', (100, 100), color='blue')
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        
        # Upload test image
        import cloudinary.uploader
        result = cloudinary.uploader.upload(
            buffer,
            folder='test',
            public_id=f'test_upload_{int(datetime.utcnow().timestamp())}',
            resource_type='image'
        )
        
        # Clean up test image
        try:
            cloudinary.uploader.destroy(result['public_id'])
        except:
            pass
        
        return jsonify({
            'success': True,
            'message': 'Cloudinary upload test successful!',
            'test_url': result.get('secure_url', '')
        })
        
    except Exception as e:
        current_app.logger.error(f"Cloudinary test error: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

@app.route('/admin/settings/test-whatsapp', methods=['POST'])
@login_required
@admin_required
def admin_settings_test_whatsapp():
    """Test WhatsApp message sending"""
    try:
        settings = AppSettings.query.first()
        if not settings:
            return jsonify({'success': False, 'message': 'Settings not found'}), 400
        
        access_token = settings.whatsapp_access_token or os.getenv('WHATSAPP_ACCESS_TOKEN')
        phone_number_id = settings.whatsapp_phone_number_id or os.getenv('WHATSAPP_PHONE_NUMBER_ID')
        test_number = request.json.get('test_number', '').strip() if request.is_json else request.form.get('test_number', '').strip()
        
        if not test_number:
            # Use configured receiver or default
            test_number = settings.whatsapp_receiver or settings.contact_whatsapp_receiver or os.getenv('WHATSAPP_TEST_NUMBER', '+2200000000')
        
        if not access_token or not phone_number_id:
            return jsonify({'success': False, 'message': 'WhatsApp credentials not configured'}), 400
        
        # Normalize phone number
        if not test_number.startswith('+'):
            if test_number.startswith('220'):
                test_number = '+' + test_number
            elif test_number.startswith('0'):
                test_number = '+220' + test_number[1:]
            else:
                test_number = '+220' + test_number
        
        url = f"https://graph.facebook.com/v22.0/{phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": test_number,
            "type": "text",
            "text": {
                "body": "Hello from BuXin Admin! ✅ Your WhatsApp configuration is working correctly."
            }
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        
        if response.status_code == 200:
            return jsonify({
                'success': True,
                'message': f'Test message sent successfully to {test_number}!'
            })
        else:
            return jsonify({
                'success': False,
                'message': f'Failed to send message: {response.status_code} - {response.text[:200]}'
            }), 400
            
    except Exception as e:
        current_app.logger.error(f"WhatsApp test error: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

@app.route('/admin/settings/test-email', methods=['POST'])
@login_required
@admin_required
def admin_settings_test_email():
    """Test email configuration using Resend with database settings."""
    try:
        current_app.logger.info("admin_settings_test_email: route start")
        settings = AppSettings.query.first()
        if not settings:
            current_app.logger.warning("admin_settings_test_email: settings not found")
            return jsonify({
                'success': False,
                'status': 'error',
                'message': 'Email failed: settings not found'
            }), 400
        
        test_email = request.json.get('test_email', '').strip() if request.is_json else request.form.get('test_email', '').strip()
        if not test_email:
            test_email = settings.contact_email or settings.support_email or current_user.email
        current_app.logger.info(f"admin_settings_test_email: using recipient={test_email}")

        import resend
        # Get API key from database settings or environment
        api_key = settings.resend_api_key or os.getenv("RESEND_API_KEY")
        if not api_key:
            current_app.logger.warning("admin_settings_test_email: RESEND_API_KEY is not configured")
            return jsonify({
                'success': False,
                'status': 'error',
                'message': 'Email failed: RESEND_API_KEY is not configured in environment variables or admin settings'
            }), 400

        resend.api_key = api_key
        # Get from_email from database settings
        from_email = settings.resend_from_email or os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev")
        subject_prefix = settings.default_subject_prefix or "BuXin Store"

        subject = f"{subject_prefix} - Test Email"
        html_body = f"""
            <html>
            <body>
                <p>This is a test email from your BuXin Admin settings page via Resend.</p>
                <p><strong>Recipient:</strong> {test_email}</p>
                <p><strong>From Email:</strong> {from_email}</p>
                <p><strong>Subject Prefix:</strong> {subject_prefix}</p>
                <p>If you received this email, your Resend configuration is working correctly! ✅</p>
            </body>
            </html>
        """

        current_app.logger.info(
            "admin_settings_test_email: sending test email via Resend",
            extra={"recipient": test_email, "from": from_email},
        )

        resend.Emails.send({
            "from": from_email,
            "to": test_email,
            "subject": subject,
            "html": html_body,
        })

        current_app.logger.info("admin_settings_test_email: test email sent successfully via Resend")
        return jsonify({
            'success': True,
            'status': 'success',
            'message': f'Test email sent to {test_email} via Resend.'
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Email test error (Resend): {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'status': 'error',
            'message': f'Email failed: {str(e)}'
        }), 500

@app.route('/admin/settings/backup-database', methods=['POST'])
@login_required
@admin_required
def admin_settings_backup_database():
    """Backup database"""
    try:
        backup_path = dump_database_to_file(prefix='admin')
        return jsonify({
            'success': True,
            'message': f'Database backed up successfully to {backup_path}'
        })
    except DatabaseBackupError as exc:
        current_app.logger.error(f"Database backup error: {exc}")
        return jsonify({'success': False, 'message': f'pg_dump failed: {exc}'}), 500
    except Exception as e:
        current_app.logger.error(f"Database backup error: {str(e)}")
        import traceback
        current_app.logger.error(traceback.format_exc())
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

@app.route('/admin/settings/clear-cache', methods=['POST'])
@login_required
@admin_required
def admin_settings_clear_cache():
    """Clear application cache"""
    try:
        # Clear Flask cache if configured
        if hasattr(app, 'cache'):
            app.cache.clear()
        
        # Clear any session-based cache
        # This is a placeholder - implement based on your caching strategy
        
        return jsonify({
            'success': True,
            'message': 'Cache cleared successfully'
        })
        
    except Exception as e:
        current_app.logger.error(f"Clear cache error: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

@app.route('/admin/email/customers', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_email_customers():
    """Compose and send an email to all customer emails (non-admin users).

    - Browser (HTML) usage: visiting /admin/email/customers in a normal browser
      should render the email form page.
    - API/AJAX usage: XHR/fetch or JSON clients should receive JSON responses
      (never HTML), to avoid "Unexpected token '<'" parse errors.
    """

    log = current_app.logger
    log.info("admin_email_customers: route start")

    try:
        base_url = None
        try:
            base_url = current_app.get_base_url()
            log.info(f"admin_email_customers: resolved base URL={base_url}")
        except Exception as exc:
            log.warning(f"admin_email_customers: base URL resolution failed: {exc}")

        if request.method == "GET":
            log.info("admin_email_customers[GET]: determining response type (JSON vs HTML)")
            wants_json = (
                request.is_json
                or request.headers.get("X-Requested-With") == "XMLHttpRequest"
                or "application/json" in (request.headers.get("Accept") or "")
            )
            if wants_json:
                log.info("admin_email_customers[GET]: returning JSON health payload")
                return jsonify({"status": "ok"}), 200

            log.info("admin_email_customers[GET]: rendering HTML form")
            return render_template("admin/admin/email_customers.html"), 200

        if request.method != "POST":
            log.warning(f"admin_email_customers: unexpected method {request.method}")
            return jsonify({"status": "error", "message": "Method not allowed"}), 405

        log.info("admin_email_customers[POST]: validating Resend email configuration")
        # Check Resend configuration
        settings = AppSettings.query.first()
        resend_api_key = os.getenv("RESEND_API_KEY") or (settings.resend_api_key if settings else None)
        if not resend_api_key:
            log.error(
                "admin_email_customers[POST]: Resend is not configured. "
                "Set RESEND_API_KEY in environment or admin settings."
            )
            log.info("admin_email_customers[POST]: returning JSON error due to Resend misconfiguration")
            return jsonify(
                {
                    "success": False,
                    "status": "error",
                    "message": "Resend email is not configured. Set RESEND_API_KEY in environment or admin settings.",
                    "sent_count": 0,
                }
            ), 500

        log.info("admin_email_customers[POST]: loading form data")
        subject = (request.form.get("subject") or "").strip()
        body = (request.form.get("body") or "").strip()
        test_only = request.form.get("test_only") == "on"
        test_email = (request.form.get("test_email") or "").strip()

        log.info(
            "admin_email_customers[POST]: form parsed "
            f"(test_only={test_only}, has_subject={bool(subject)}, has_body={bool(body)}, "
            f"has_test_email={bool(test_email)})"
        )

        if not subject or not body:
            log.warning("admin_email_customers[POST]: missing subject or body")
            log.info("admin_email_customers[POST]: returning JSON error for missing subject/body")
            return jsonify(
                {
                    "success": False,
                    "status": "error",
                    "message": "Subject and body are required.",
                    "sent_count": 0,
                }
            ), 400

        log.info("admin_email_customers[POST]: email configuration snapshot (Resend)")
        from app.utils.email_queue import queue_single_email, queue_bulk_email
        app_obj = app

        if test_only:
            # Use provided test email or fall back to configured email receiver
            recipient_email = test_email or (settings.email_receiver if settings else None) or os.getenv('EMAIL_RECEIVER', '')
            if not recipient_email:
                return jsonify({
                    "success": False,
                    "status": "error",
                    "message": "Test email address is required. Please provide a test email or configure EMAIL_RECEIVER in settings.",
                    "sent_count": 0,
                }), 400
            
            log.info(
                f"admin_email_customers[POST]: queueing single test email to {recipient_email} via email_queue/Resend"
            )
            html_body = render_template("emails/admin_broadcast_email.html", subject=subject, body_text=body)
            queue_single_email(app_obj, recipient_email, subject, html_body)

            response_payload = {"success": True, "status": "queued", "recipients": 1}
            log.info(f"admin_email_customers[POST]: returning response {response_payload}")
            return jsonify(response_payload), 202

        log.info("admin_email_customers[POST]: building customer email query")
        # Build query with proper email validation
        # Filter: is_admin == False, email is not None, email != "", email contains "@"
        base_query = User.query.filter(
            User.is_admin == False,  # noqa: E712
            User.email.isnot(None),
            User.email != "",
            User.email.contains("@"),
        )

        try:
            estimated_total = base_query.count()
            log.info(f"admin_email_customers[POST]: Total customers found: {estimated_total}")
        except Exception as count_exc:
            log.warning(f"admin_email_customers[POST]: could not count recipients: {count_exc}")
            estimated_total = None

        max_per_request_default = 2000
        try:
            max_per_request = int(os.getenv("MAX_EMAILS_PER_REQUEST", str(max_per_request_default)))
        except Exception:
            max_per_request = max_per_request_default

        if estimated_total is not None and estimated_total > max_per_request:
            log.warning(
                "admin_email_customers[POST]: recipient limit exceeded",
                extra={
                    "estimated_total": estimated_total,
                    "max_per_request": max_per_request,
                },
            )
            return jsonify(
                {
                    "success": False,
                    "status": "error",
                    "message": (
                        f"Email job would target {estimated_total} recipients, which exceeds the "
                        f"per-request limit of {max_per_request}. Please narrow the audience or "
                        "run from an administrative batch job with explicit confirmation."
                    ),
                }
            ), 400

        log.info(
            f"admin_email_customers[POST]: queueing batched customer email job for {estimated_total} customers",
            extra={"queued_for": estimated_total},
        )

        html_body = render_template("emails/admin_broadcast_email.html", subject=subject, body_text=body)
        job_id = queue_bulk_email(app_obj, base_query, subject, html_body)

        response_payload = {
            "success": True,
            "status": "queued",
            "job_id": job_id,
            "recipients": int(estimated_total or 0),
        }
        log.info(f"admin_email_customers[POST]: returning response {response_payload}")
        return jsonify(response_payload), 202

    except Exception as e:
        log.exception(f"admin_email_customers: unhandled error in route: {e}")
        error_payload = {
            "success": False,
            "status": "error",
            "message": "An unexpected error occurred while processing customer emails.",
        }
        log.info(f"admin_email_customers: returning error response {error_payload}")
        return jsonify(error_payload), 500


@app.route('/admin/email/status/<job_id>', methods=['GET'])
@login_required
@admin_required
def admin_email_job_status(job_id):
    """Check status of a background email job."""
    from app.utils.email_queue import email_status

    status = email_status.get(job_id)

    if not status:
        return jsonify({
            "success": False,
            "status": "not_found",
            "message": "Unknown job_id"
        }), 404

    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": status.get("status"),
        "sent": status.get("sent"),
        "failed": status.get("failed"),
        "total": status.get("total"),
    })

@app.route('/admin/test-email-config', methods=['GET'])
@login_required
@admin_required
def test_email_config():
    """Show email-related configuration values (Resend-based)."""
    env_config = {
        'RESEND_API_KEY': '***' if os.environ.get('RESEND_API_KEY') else 'Not set in env',
        'RESEND_FROM_EMAIL': os.environ.get('RESEND_FROM_EMAIL', 'Not set in env'),
    }

    app_config = {
        'RESEND_FROM_EMAIL': os.environ.get('RESEND_FROM_EMAIL', 'Not set'),
    }

    return jsonify({
        "status": "ok",
        "success": True,
        "env_config": env_config,
        "app_config": app_config,
    }), 200

@app.route('/admin/whatsapp', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_whatsapp():
    """WhatsApp Bulk Messaging admin page."""
    # Check if WhatsApp is configured
    whatsapp_token = os.environ.get('WHATSAPP_ACCESS_TOKEN')
    whatsapp_phone_id = os.environ.get('WHATSAPP_PHONE_NUMBER_ID')
    
    if not whatsapp_token or not whatsapp_phone_id:
        return render_template('admin/admin/whatsapp.html',
                               error="WhatsApp is not configured. Set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID in environment.",
                               configured=False,
                               message_logs=[]), 200
    
    # Get previous results from session if any
    results = session.get('whatsapp_results', [])
    error_response = session.get('whatsapp_error_response', None)
    session.pop('whatsapp_results', None)
    session.pop('whatsapp_error_response', None)
    
    # Get message logs (most recent first, limit to 100)
    message_logs = WhatsAppMessageLog.query.order_by(WhatsAppMessageLog.timestamp.desc()).limit(100).all()
    
    # Format logs for display
    formatted_logs = []
    for log in message_logs:
        # Get name/email
        name_email = "Unknown"
        if log.user_id:
            user = User.query.get(log.user_id)
            if user:
                name_email = f"{user.display_name} ({user.email})"
        elif log.subscriber_id:
            subscriber = Subscriber.query.get(log.subscriber_id)
            if subscriber:
                name_email = subscriber.email
        
        formatted_logs.append({
            'id': log.id,
            'name_email': name_email,
            'whatsapp_number': log.whatsapp_number,
            'message': log.message[:100] + '...' if len(log.message) > 100 else log.message,
            'full_message': log.message,
            'status': log.status,
            'error_message': log.error_message,
            'timestamp': log.timestamp,
            'message_id': log.message_id
        })
    
    return render_template('admin/admin/whatsapp.html', 
                         configured=True, 
                         results=results,
                         error_response=error_response,
                         message_logs=formatted_logs), 200

@app.route('/admin/send_test_whatsapp', methods=['POST'])
@login_required
@admin_required
def admin_send_test_whatsapp():
    """Send a test WhatsApp message using hello_world template."""
    # Reload .env file to pick up any changes
    from dotenv import load_dotenv
    load_dotenv(override=True)  # override=True forces reload
    
    # Get WhatsApp credentials
    access_token = os.getenv('WHATSAPP_ACCESS_TOKEN')
    phone_number_id = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
    
    # Get test number from form or env
    test_number = request.form.get('test_number', '').strip()
    if not test_number:
        test_number = os.getenv('WHATSAPP_TEST_NUMBER', '+2200000000')
    
    if not access_token or not phone_number_id:
        flash('WhatsApp is not configured. Please set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID', 'error')
        return redirect(url_for('admin_whatsapp'))
    
    # Normalize test number
    if not test_number.startswith('+'):
        if test_number.startswith('220'):
            test_number = '+' + test_number
        elif test_number.startswith('0'):
            test_number = '+220' + test_number[1:]
        else:
            test_number = '+220' + test_number
    
    # Use v22.0 API with regular text message (hello_world template only works with Meta's test numbers)
    url = f"https://graph.facebook.com/v22.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    # Use regular text message for testing (hello_world template requires Meta's public test numbers)
    payload = {
        "messaging_product": "whatsapp",
        "to": test_number,
        "type": "text",
        "text": {
            "body": "Hello! This is a test message from your WhatsApp Business API. Your configuration is working correctly! ✅"
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response_data = response.json() if response.headers.get('content-type', '').startswith('application/json') else response.text
        
        if response.status_code == 200:
            # Check if we got a valid response with message ID
            if isinstance(response_data, dict) and 'messages' in response_data:
                message_id = response_data.get('messages', [{}])[0].get('id', 'N/A')
                flash(f'✅ Test message sent successfully to {test_number} (Message ID: {message_id})', 'success')
                current_app.logger.info(f"✅ WhatsApp test message sent successfully to {test_number}, Message ID: {message_id}")
            else:
                flash(f'✅ API accepted the message for {test_number}. Check WhatsApp delivery status below.', 'success')
                current_app.logger.info(f"✅ WhatsApp API accepted message to {test_number}. Response: {response_data}")
            
            # Store full response for troubleshooting
            session['whatsapp_last_response'] = response_data
        else:
            # Check for token expiration (401 error with specific error code)
            error_message = f'❌ Failed to send test message. HTTP {response.status_code}.'
            if response.status_code == 401:
                try:
                    if isinstance(response_data, dict):
                        error_info = response_data.get('error', {})
                        if error_info.get('code') == 190:
                            error_message = '❌ WhatsApp access token has EXPIRED. Please refresh your token in Meta Developer Console and update WHATSAPP_ACCESS_TOKEN in your .env file. See WHATSAPP_TOKEN_REFRESH_INSTRUCTIONS.md for detailed steps.'
                            flash(error_message, 'error')
                            current_app.logger.error(f"❌ WhatsApp token expired: {error_info.get('message', 'Token expired')}")
                        else:
                            flash(error_message + ' Check error details below.', 'error')
                    else:
                        flash(error_message + ' Check error details below.', 'error')
                except (AttributeError, KeyError, TypeError):
                    flash(error_message + ' Check error details below.', 'error')
            else:
                flash(error_message + ' Check error details below.', 'error')
            
            # Store full error response for debugging
            session['whatsapp_error_response'] = {
                'status_code': response.status_code,
                'response': response_data,
                'request_payload': payload
            }
            current_app.logger.error(f"❌ Failed to send WhatsApp test message: HTTP {response.status_code} - {response_data}")
        
        session['whatsapp_results'] = [{
            'number': test_number,
            'status': 'success' if response.status_code == 200 else 'failed',
            'status_code': response.status_code,
            'response': response_data if response.status_code != 200 else response_data,  # Always store response for debugging
            'message_id': response_data.get('messages', [{}])[0].get('id') if isinstance(response_data, dict) and 'messages' in response_data else None
        }]
        
    except requests.exceptions.RequestException as e:
        error_msg = str(e)
        session['whatsapp_error_response'] = {
            'error': error_msg,
            'request_payload': payload
        }
        flash(f'❌ Error sending test message: {error_msg}', 'error')
        current_app.logger.error(f"❌ Error sending WhatsApp test message: {error_msg}")
        session['whatsapp_results'] = [{
            'number': test_number,
            'status': 'failed',
            'status_code': None,
            'error': error_msg
        }]
    except Exception as e:
        error_msg = str(e)
        session['whatsapp_error_response'] = {
            'error': error_msg,
            'request_payload': payload
        }
        flash(f'❌ Unexpected error: {error_msg}', 'error')
        current_app.logger.error(f"❌ Unexpected error sending WhatsApp test message: {error_msg}")
    
    return redirect(url_for('admin_whatsapp'))

@app.route('/admin/bulk_whatsapp', methods=['POST'])
@login_required
@admin_required
def admin_bulk_whatsapp():
    """Send bulk WhatsApp messages to all customers with personalization."""
    # Reload .env file to pick up any changes
    from dotenv import load_dotenv
    load_dotenv(override=True)  # override=True forces reload
    
    message = request.form.get('message', '').strip()
    
    # Validate required fields
    if not message:
        flash('Message text is required', 'error')
        return redirect(url_for('admin_whatsapp'))
    
    # Get WhatsApp credentials
    access_token = os.getenv('WHATSAPP_ACCESS_TOKEN')
    phone_number_id = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
    
    if not access_token or not phone_number_id:
        flash('WhatsApp is not configured. Please set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID', 'error')
        return redirect(url_for('admin_whatsapp'))
    
    # Get all customer phone numbers with customer info (users and subscribers)
    customer_data = []
    
    # Get users with WhatsApp numbers
    users = User.query.filter(User.is_admin == False).filter(User.whatsapp_number.isnot(None)).all()
    for customer in users:
        customer_name = customer.display_name
        phone = customer.whatsapp_number
        
        if phone:
            # Normalize phone number
            if not phone.startswith('+'):
                if phone.startswith('220'):
                    phone = '+' + phone
                elif phone.startswith('0'):
                    phone = '+220' + phone[1:]
                else:
                    phone = '+220' + phone
            
            customer_data.append({
                'phone': phone,
                'name': customer_name,
                'email': customer.email,
                'is_user': True,
                'user_id': customer.id
            })
    
    # Get subscribers with WhatsApp numbers
    subscribers = Subscriber.query.all()
    for subscriber in subscribers:
        if subscriber.whatsapp_number:
            phone = subscriber.whatsapp_number
            
            # Normalize phone number
            if not phone.startswith('+'):
                if phone.startswith('220'):
                    phone = '+' + phone
                elif phone.startswith('0'):
                    phone = '+220' + phone[1:]
                else:
                    phone = '+220' + phone
            
            customer_data.append({
                'phone': phone,
                'name': subscriber.email.split('@')[0],  # Use email prefix as name
                'email': subscriber.email,
                'is_user': False,
                'subscriber_id': subscriber.id
            })
    
    if not customer_data:
        flash('No customers with phone numbers found', 'error')
        return redirect(url_for('admin_whatsapp'))
    
    # Use v22.0 API
    url = f"https://graph.facebook.com/v22.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    success_count = 0
    failure_count = 0
    results = []
    first_error_response = None
    
    # Send personalized messages
    for customer in customer_data:
        # Personalize message with customer name
        personalized_message = message.replace('{{customer_name}}', customer['name'])
        personalized_message = personalized_message.replace('{{name}}', customer['name'])
        
        # Get user_id or subscriber_id
        user_id = customer.get('user_id') if customer.get('is_user') else None
        subscriber_id = customer.get('subscriber_id') if not customer.get('is_user') else None
        
        # Send message using logging function
        success, error_msg, log_id = send_whatsapp_message_with_logging(
            whatsapp_number=customer['phone'],
            message=personalized_message,
            user_id=user_id,
            subscriber_id=subscriber_id
        )
        
        if success:
            success_count += 1
            results.append({
                "number": customer['phone'],
                "name": customer['name'],
                "status": "success",
                "status_code": 200
            })
        else:
            failure_count += 1
            if not first_error_response:
                first_error_response = {
                    'error': error_msg or "Unknown error",
                    'request_payload': {
                        "to": customer['phone'],
                        "message": personalized_message
                    }
                }
            
            results.append({
                "number": customer['phone'],
                "name": customer['name'],
                "status": "failed",
                "status_code": None,
                "error": error_msg[:500] if error_msg else "Unknown error"
            })
    
    # Flash result message
    if success_count > 0 and failure_count == 0:
        flash(f'✅ Successfully sent {success_count} WhatsApp message(s) to customers', 'success')
    elif success_count > 0:
        flash(f'⚠️ Sent {success_count} message(s), {failure_count} failed', 'warning')
    else:
        flash(f'❌ Failed to send messages. All {failure_count} attempt(s) failed.', 'error')
    
    # Store results and error response in session
    session['whatsapp_results'] = results[:50]  # Limit to first 50 for display
    session['whatsapp_message'] = message
    if first_error_response:
        session['whatsapp_error_response'] = first_error_response
    
    return redirect(url_for('admin_whatsapp'))

@app.route('/api/whatsapp/submit', methods=['POST'])
def api_whatsapp_submit():
    """API endpoint for WhatsApp number popup submission (AJAX)."""
    try:
        data = request.get_json()
        whatsapp_number = data.get('whatsapp_number', '').strip()
        email = data.get('email', '').strip()  # For non-logged-in users
        
        # Validate WhatsApp number
        if not whatsapp_number:
            return jsonify({'success': False, 'message': 'WhatsApp number is required'}), 400
        
        # Check if user is logged in
        if current_user.is_authenticated:
            # Check if user already has WhatsApp number
            if current_user.whatsapp_number:
                return jsonify({'success': False, 'message': 'You already have a WhatsApp number registered'}), 400
            
            # Normalize and save WhatsApp number
            try:
                normalized_number = normalize_whatsapp_number(whatsapp_number)
                current_user.whatsapp_number = normalized_number
                db.session.commit()
                
                # Send welcome message
                user_name = current_user.display_name
                welcome_message = f"Hi {user_name} 👋\nWelcome to BuXin! You'll now receive updates about our robotics and AI innovations. 🚀"
                
                success, error_msg, log_id = send_whatsapp_message_with_logging(
                    whatsapp_number=normalized_number,
                    message=welcome_message,
                    user_id=current_user.id
                )
                
                # Send notification to admin-configured receivers
                _send_form_submission_notifications(
                    whatsapp_number=normalized_number,
                    email=current_user.email,
                    user_name=user_name,
                    is_logged_in=True
                )
                
                if success:
                    return jsonify({
                        'success': True,
                        'message': 'WhatsApp number saved and welcome message sent!'
                    }), 200
                else:
                    return jsonify({
                        'success': True,
                        'message': 'WhatsApp number saved, but welcome message failed to send. We\'ll try again later.',
                        'warning': error_msg
                    }), 200
                    
            except ValueError as e:
                return jsonify({'success': False, 'message': str(e)}), 400
        else:
            # Non-logged-in user - require email
            if not email:
                return jsonify({'success': False, 'message': 'Email is required for non-logged-in users'}), 400
            
            # Validate email
            try:
                validate_email(email, check_deliverability=False)
            except EmailNotValidError:
                return jsonify({'success': False, 'message': 'Invalid email address'}), 400
            
            # Check if subscriber already exists
            existing_subscriber = Subscriber.query.filter_by(email=email).first()
            if existing_subscriber:
                return jsonify({'success': False, 'message': 'This email is already registered'}), 400
            
            # Normalize and save subscriber
            try:
                normalized_number = normalize_whatsapp_number(whatsapp_number)
                subscriber = Subscriber(
                    email=email,
                    whatsapp_number=normalized_number
                )
                db.session.add(subscriber)
                db.session.commit()
                
                # Send welcome message
                welcome_message = f"Hi 👋\nWelcome to BuXin! You'll now receive updates about our robotics and AI innovations. 🚀"
                
                success, error_msg, log_id = send_whatsapp_message_with_logging(
                    whatsapp_number=normalized_number,
                    message=welcome_message,
                    subscriber_id=subscriber.id
                )
                
                # Send notification to admin-configured receivers
                _send_form_submission_notifications(
                    whatsapp_number=normalized_number,
                    email=email,
                    user_name=None,
                    is_logged_in=False
                )
                
                if success:
                    return jsonify({
                        'success': True,
                        'message': 'Thank you! You\'ll receive updates via WhatsApp.'
                    }), 200
                else:
                    return jsonify({
                        'success': True,
                        'message': 'Thank you for subscribing! Welcome message will be sent shortly.',
                        'warning': error_msg
                    }), 200
                    
            except ValueError as e:
                return jsonify({'success': False, 'message': str(e)}), 400
                
    except Exception as e:
        current_app.logger.error(f"Error in api_whatsapp_submit: {str(e)}")
        return jsonify({'success': False, 'message': 'An error occurred. Please try again.'}), 500

@app.route('/api/whatsapp/check', methods=['GET'])
def api_whatsapp_check():
    """Check if user already has WhatsApp number (for popup display logic)."""
    if current_user.is_authenticated:
        has_whatsapp = bool(current_user.whatsapp_number)
        return jsonify({
            'authenticated': True,
            'has_whatsapp': has_whatsapp,
            'whatsapp_number': current_user.whatsapp_number if has_whatsapp else None
        }), 200
    else:
        return jsonify({
            'authenticated': False,
            'has_whatsapp': False
        }), 200

@app.route('/admin/whatsapp/resend/<int:log_id>', methods=['POST'])
@login_required
@admin_required
def admin_whatsapp_resend(log_id):
    """Resend a failed WhatsApp message."""
    log_entry = WhatsAppMessageLog.query.get_or_404(log_id)
    
    if log_entry.status == 'sent':
        flash('This message was already sent successfully', 'info')
        return redirect(url_for('admin_whatsapp'))
    
    # Resend the message
    success, error_msg, new_log_id = send_whatsapp_message_with_logging(
        whatsapp_number=log_entry.whatsapp_number,
        message=log_entry.message,
        user_id=log_entry.user_id,
        subscriber_id=log_entry.subscriber_id
    )
    
    if success:
        flash(f'✅ Message resent successfully to {log_entry.whatsapp_number}', 'success')
    else:
        flash(f'❌ Failed to resend message: {error_msg}', 'error')
    
    return redirect(url_for('admin_whatsapp'))

@app.route('/admin/whatsapp/resend_all_failed', methods=['POST'])
@login_required
@admin_required
def admin_whatsapp_resend_all_failed():
    """Resend all failed WhatsApp messages."""
    failed_logs = WhatsAppMessageLog.query.filter_by(status='failed').order_by(WhatsAppMessageLog.timestamp.desc()).all()
    
    if not failed_logs:
        flash('No failed messages to resend', 'info')
        return redirect(url_for('admin_whatsapp'))
    
    success_count = 0
    failure_count = 0
    
    for log_entry in failed_logs:
        success, error_msg, new_log_id = send_whatsapp_message_with_logging(
            whatsapp_number=log_entry.whatsapp_number,
            message=log_entry.message,
            user_id=log_entry.user_id,
            subscriber_id=log_entry.subscriber_id
        )
        
        if success:
            success_count += 1
        else:
            failure_count += 1
    
    if success_count > 0 and failure_count == 0:
        flash(f'✅ Successfully resent {success_count} message(s)', 'success')
    elif success_count > 0:
        flash(f'⚠️ Resent {success_count} message(s), {failure_count} failed', 'warning')
    else:
        flash(f'❌ Failed to resend all {failure_count} message(s)', 'error')
    
    return redirect(url_for('admin_whatsapp'))

def allowed_file(filename, allowed_extensions=None):
    """Check if file extension is allowed"""
    if allowed_extensions is None:
        allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'mp4', 'mov', 'avi', 'pdf', 'docx'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions

def fetch_product_image(product_name):
    """Fetch product image from Unsplash API"""
    try:
        response = requests.get(
            'https://api.unsplash.com/search/photos',
            params={
                'query': product_name,
                'per_page': 1,
                'client_id': 'YOUR_UNSPLASH_ACCESS_KEY'  # Replace with your Unsplash API key
            }
        )
        if response.status_code == 200:
            data = response.json()
            if data['results']:
                return data['results'][0]['urls']['small']
    except Exception as e:
        print(f"Error fetching image: {e}")
    return None

def save_image_from_url(image_url, product_name):
    """Download and save image from URL to Cloudinary"""
    try:
        from .utils.cloudinary_utils import upload_file_from_path
        import tempfile
        
        response = requests.get(image_url, stream=True)
        if response.status_code == 200:
            # Generate a unique filename
            ext = image_url.split('.')[-1].split('?')[0]
            if ext.lower() not in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
                ext = 'jpg'
            
            # Save to temporary file first
            with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}') as tmp_file:
                for chunk in response.iter_content(1024):
                    tmp_file.write(chunk)
                tmp_file_path = tmp_file.name
            
            # Upload to Cloudinary
            upload_result = upload_file_from_path(tmp_file_path, folder='products')
            
            # Clean up temporary file
            try:
                os.remove(tmp_file_path)
            except:
                pass
            
            if upload_result:
                return upload_result['url']
    except Exception as e:
        current_app.logger.error(f"Error saving image from URL to Cloudinary: {e}")
    return None

@app.route('/admin/products')
@login_required
@admin_required
def admin_products():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    
    query = Product.query
    
    if search:
        search = f'%{search}%'
        query = query.filter(Product.name.ilike(search) | Product.description.ilike(search))
    
    products = query.order_by(Product.created_at.desc()).paginate(page=page, per_page=10)
    categories = Category.query.all()
    
    if request.headers.get('HX-Request'):
        return render_template('admin/admin/partials/_products_table.html', products=products)
        
    return render_template('admin/admin/products.html', products=products, categories=categories)

@app.route('/admin/products/search')
@login_required
@admin_required
def admin_search_products():
    search = request.args.get('search', '')
    query = Product.query
    
    if search:
        search = f'%{search}%'
        query = query.filter(Product.name.ilike(search) | Product.description.ilike(search))
    
    # Return paginated results to match the template structure
    products = query.order_by(Product.created_at.desc()).paginate(page=1, per_page=10, error_out=False)
    return render_template('admin/admin/partials/_products_table.html', products=products)

@app.route('/admin/products/toggle-gambia/<int:product_id>', methods=['POST'])
@login_required
@admin_required
def toggle_gambia(product_id):
    """Toggle Gambia availability for a product"""
    try:
        product = Product.query.get_or_404(product_id)
        product.available_in_gambia = not product.available_in_gambia
        db.session.commit()
        return jsonify({
            'status': 'success',
            'available_in_gambia': product.available_in_gambia
        })
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error toggling Gambia availability for product {product_id}: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/admin/products/add', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_add_product():
    form = ProductForm()
    
    if form.validate_on_submit():
        # Handle file upload to Cloudinary
        image_filename = None
        if form.image.data:
            image_file = form.image.data
            if image_file and image_file.filename and allowed_file(image_file.filename):
                from .utils.cloudinary_utils import upload_to_cloudinary
                try:
                    # Reset file pointer before upload (WTForms might have read it)
                    if hasattr(image_file, 'seek'):
                        try:
                            image_file.seek(0)
                        except:
                            pass
                    
                    upload_result = upload_to_cloudinary(image_file, folder='products')
                    if upload_result and upload_result.get('url'):
                        image_filename = upload_result['url']
                        current_app.logger.info(f"✅ Product image uploaded to Cloudinary: {image_filename}")
                    else:
                        # Log the actual upload_result for debugging
                        current_app.logger.error(f"❌ Upload failed. upload_result: {upload_result}")
                        error_msg = "Upload failed: No URL returned from Cloudinary"
                        flash(f'Failed to upload image to Cloudinary: {error_msg}. Please check the logs for details.', 'error')
                        return redirect(url_for('admin_add_product'))
                except Exception as e:
                    error_msg = f"Upload error: {str(e)}"
                    current_app.logger.error(f"❌ {error_msg}")
                    import traceback
                    current_app.logger.error(traceback.format_exc())
                    flash(f'Failed to upload image to Cloudinary: {error_msg}. Please check the logs for details.', 'error')
                    return redirect(url_for('admin_add_product'))
        
        # Use delivery price and shipping price from form, default to 0.00 if not provided
        delivery_price = form.delivery_price.data if form.delivery_price.data else 0.0
        shipping_price = form.shipping_price.data if form.shipping_price.data else 0.0
        
        product = Product(
            name=form.name.data,
            description=form.description.data,
            price=form.price.data,
            stock=form.stock.data,
            category_id=form.category_id.data,
            image=image_filename,
            available_in_gambia=form.available_in_gambia.data or False,
            delivery_price=delivery_price,
            shipping_price=shipping_price,
            location=form.location.data
        )
        
        db.session.add(product)
        db.session.commit()
        
        flash('Product added successfully!', 'success')
        return redirect(url_for('admin_products'))
    
    return render_template('admin/admin/product_form.html', form=form, product=None)

@app.route('/admin/products/<int:product_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_product(product_id):
    product = Product.query.get_or_404(product_id)
    form = ProductForm(obj=product)
    
    if form.validate_on_submit():
        # Handle file upload to Cloudinary
        if form.image.data and hasattr(form.image.data, 'filename') and form.image.data.filename:
            from .utils.cloudinary_utils import upload_to_cloudinary, delete_from_cloudinary, is_cloudinary_url, get_public_id_from_url
            
            # Delete old image from Cloudinary if it exists
            if product.image:
                try:
                    if is_cloudinary_url(product.image):
                        public_id = get_public_id_from_url(product.image)
                        if public_id:
                            delete_from_cloudinary(public_id)
                    else:
                        # Old local file - try to delete if exists
                        old_image_path = os.path.join(app.static_folder, product.image)
                        if os.path.exists(old_image_path):
                            os.remove(old_image_path)
                except Exception as e:
                    current_app.logger.warning(f"Error deleting old image: {e}")
            
            # Upload new image to Cloudinary
            image_file = form.image.data
            if image_file and hasattr(image_file, 'filename') and image_file.filename and allowed_file(image_file.filename):
                upload_result = upload_to_cloudinary(image_file, folder='products')
                if upload_result:
                    product.image = upload_result['url']
                    current_app.logger.info(f"✅ Product image updated in Cloudinary: {product.image}")
                else:
                    flash('Failed to upload image to Cloudinary. Please try again.', 'error')
                    return redirect(url_for('admin_edit_product', product_id=product_id))
        
        # Update product details - manually assign fields to avoid FileStorage object
        product.name = form.name.data
        product.description = form.description.data
        old_price = product.price
        product.price = form.price.data
        product.stock = form.stock.data
        product.category_id = form.category_id.data
        product.available_in_gambia = form.available_in_gambia.data or False
        product.location = form.location.data
        
        # Use delivery price and shipping price from form, default to 0.00 if not provided
        product.delivery_price = form.delivery_price.data if form.delivery_price.data else 0.0
        product.shipping_price = form.shipping_price.data if form.shipping_price.data else 0.0
        
        # Handle delivery rules
        delivery_rules_data = request.form.getlist('delivery_rules')
        rules_to_delete = request.form.getlist('delivery_rules_to_delete[]')
        
        # Delete marked rules
        for rule_id_str in rules_to_delete:
            try:
                rule_id = int(rule_id_str)
                rule = DeliveryRule.query.get(rule_id)
                if rule and rule.product_id == product.id:
                    db.session.delete(rule)
            except (ValueError, TypeError):
                pass
        
        # Process delivery rules from form
        # The form sends data as: delivery_rules[rule_id][field_name]
        rule_ids_processed = set()
        
        # Get all rule IDs from form data
        for key in request.form.keys():
            if key.startswith('delivery_rules[') and '][min_amount]' in key:
                # Extract rule ID from key like "delivery_rules[123][min_amount]"
                rule_id_str = key.split('[')[1].split(']')[0]
                rule_ids_processed.add(rule_id_str)
        
        # Update existing rules and create new ones
        for rule_id_str in rule_ids_processed:
            min_amount = request.form.get(f'delivery_rules[{rule_id_str}][min_amount]')
            max_amount = request.form.get(f'delivery_rules[{rule_id_str}][max_amount]')
            fee = request.form.get(f'delivery_rules[{rule_id_str}][fee]')
            
            if min_amount and fee:
                try:
                    min_val = float(min_amount)
                    max_val = float(max_amount) if max_amount else None
                    fee_val = float(fee)
                    
                    # Validate: min < max if max is provided
                    if max_val is not None and min_val >= max_val:
                        flash('Delivery rule error: Minimum price must be less than maximum price.', 'error')
                        continue
                    
                    # Check if it's a new rule or existing one
                    if rule_id_str.startswith('new-'):
                        # Create new rule
                        new_rule = DeliveryRule(
                            product_id=product.id,
                            min_amount=min_val,
                            max_amount=max_val,
                            fee=fee_val
                        )
                        db.session.add(new_rule)
                    else:
                        # Update existing rule
                        try:
                            rule_id = int(rule_id_str)
                            rule = DeliveryRule.query.get(rule_id)
                            if rule and rule.product_id == product.id:
                                rule.min_amount = min_val
                                rule.max_amount = max_val
                                rule.fee = fee_val
                        except (ValueError, TypeError):
                            pass
                except (ValueError, TypeError):
                    flash('Invalid delivery rule data. Please check your inputs.', 'error')
                    continue
        
        db.session.commit()
        
        flash('Product updated successfully!', 'success')
        return redirect(url_for('admin_products'))
    
    # Pre-populate delivery_price if not set (default to 0.00)
    if product.delivery_price is None:
        product.delivery_price = 0.0
        form.delivery_price.data = 0.0
    
    # Load delivery rules for the product
    delivery_rules = DeliveryRule.query.filter_by(product_id=product.id).order_by(DeliveryRule.min_amount).all()
    product.delivery_rules = delivery_rules
    
    return render_template('admin/admin/product_form.html', form=form, product=product)

@app.route('/admin/products/bulk-upload', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_bulk_upload():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file selected', 'error')
            return redirect(request.url)
        
        file = request.files['file']
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(request.url)
        
        if not allowed_file(file.filename, {'xlsx', 'csv'}):
            flash('Invalid file type. Please upload XLSX or CSV files only.', 'error')
            return redirect(request.url)
        
        try:
            # Read the uploaded file
            if file.filename.endswith('.xlsx'):
                df = pd.read_excel(file)
            else:  # CSV
                df = pd.read_csv(file)
            
            # Convert column names to lowercase and strip whitespace
            df.columns = df.columns.str.strip().str.lower()
            required_columns = ['product name', 'category']
            
            # Validate required columns
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                flash(f'Missing required columns: {", ".join(missing_columns)}', 'error')
                return redirect(request.url)
            
            results = []
            
            # Process each row
            for _, row in df.iterrows():
                try:
                    # Get or create category
                    category_name = str(row['category']).strip()
                    category = Category.query.filter_by(name=category_name).first()
                    if not category:
                        category = Category(name=category_name)
                        db.session.add(category)
                        db.session.commit()
                    
                    # Check if product exists
                    product_name = str(row['product name']).strip()
                    product = Product.query.filter_by(name=product_name).first()
                    
                    # Prepare product data
                    price = float(row.get('price (gmd)', 0)) if pd.notna(row.get('price (gmd)')) else 0
                    stock = int(row.get('stock quantity', 0)) if pd.notna(row.get('stock quantity')) else 0
                    description = str(row.get('description', '')) if pd.notna(row.get('description')) else ''
                    
                    # Handle image
                    image_url = row.get('image url') if 'image url' in row and pd.notna(row['image url']) else None
                    image_path = None
                    
                    if image_url:
                        image_path = save_image_from_url(image_url, product_name)
                    
                    # Create or update product
                    if product:
                        # Update existing product
                        product.category_id = category.id
                        product.price = price if price > 0 else product.price
                        product.stock = stock
                        product.description = description or product.description
                        if image_path:
                            # Delete old image if exists (only if local file)
                            if product.image:
                                try:
                                    from .utils.cloudinary_utils import is_cloudinary_url, delete_from_cloudinary, get_public_id_from_url
                                    if is_cloudinary_url(product.image):
                                        public_id = get_public_id_from_url(product.image)
                                        if public_id:
                                            delete_from_cloudinary(public_id)
                                    else:
                                        old_image_path = os.path.join(app.static_folder, product.image)
                                        if os.path.exists(old_image_path):
                                            os.remove(old_image_path)
                                except:
                                    pass
                            product.image = image_path
                        action = 'updated'
                    else:
                        # Create new product
                        if not image_url:
                            # Try to fetch image if not provided
                            fetched_image_url = fetch_product_image(product_name)
                            if fetched_image_url:
                                image_path = save_image_from_url(fetched_image_url, product_name)
                        
                        product = Product(
                            name=product_name,
                            description=description,
                            price=price,
                            stock=stock,
                            category_id=category.id,
                            image=image_path
                        )
                        db.session.add(product)
                        action = 'created'
                    
                    db.session.commit()
                    results.append({
                        'product': product_name,
                        'status': 'success',
                        'message': f'Successfully {action} product',
                        'action': action
                    })
                    
                except Exception as e:
                    db.session.rollback()
                    results.append({
                        'product': str(row.get('product name', 'Unknown')),
                        'status': 'error',
                        'message': str(e)
                    })
            
            flash(f'Successfully processed {len([r for r in results if r["status"] == "success"])} products', 'success')
            return render_template('admin/admin/bulk_upload_results.html', results=results)
            
        except Exception as e:
            flash(f'Error processing file: {str(e)}', 'error')
            return redirect(request.url)
    
    return render_template('admin/admin/bulk_upload.html')

@app.route('/admin/categories')
@login_required
@admin_required
def admin_categories():
    """List all categories"""
    categories = Category.query.order_by(Category.name).all()
    # Get product count for each category
    categories_with_counts = []
    for category in categories:
        count = Product.query.filter_by(category_id=category.id).count()
        categories_with_counts.append({
            'category': category,
            'product_count': count
        })
    return render_template('admin/admin/categories.html', categories_with_counts=categories_with_counts)

@app.route('/admin/categories/add', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_add_category():
    """Add a new category"""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        
        if not name:
            flash('Category name is required', 'error')
            return redirect(url_for('admin_add_category'))
        
        # Check if category already exists
        existing = Category.query.filter_by(name=name).first()
        if existing:
            flash('Category already exists', 'error')
            return redirect(url_for('admin_add_category'))
        
        # Handle image upload to Cloudinary if provided
        image_filename = None
        if 'image' in request.files:
            image_file = request.files['image']
            if image_file and image_file.filename:
                if allowed_file(image_file.filename):
                    from .utils.cloudinary_utils import upload_to_cloudinary
                    upload_result = upload_to_cloudinary(image_file, folder='categories')
                    if upload_result:
                        image_filename = upload_result['url']
                        current_app.logger.info(f"✅ Category image uploaded to Cloudinary: {image_filename}")
                    else:
                        flash('Failed to upload image to Cloudinary. Please try again.', 'error')
                        return redirect(url_for('admin_add_category'))
        
        category = Category(name=name, image=image_filename)
        db.session.add(category)
        db.session.commit()
        
        flash('Category added successfully!', 'success')
        return redirect(url_for('admin_categories'))
    
    return render_template('admin/admin/category_form.html', category=None)

@app.route('/admin/categories/<int:category_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_category(category_id):
    """Edit a category"""
    category = Category.query.get_or_404(category_id)
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        
        if not name:
            flash('Category name is required', 'error')
            return redirect(url_for('admin_edit_category', category_id=category_id))
        
        # Check if another category with the same name exists
        existing = Category.query.filter(Category.name == name, Category.id != category_id).first()
        if existing:
            flash('Category name already exists', 'error')
            return redirect(url_for('admin_edit_category', category_id=category_id))
        
        # Handle image upload to Cloudinary if provided
        if 'image' in request.files:
            image_file = request.files['image']
            if image_file and image_file.filename:
                if allowed_file(image_file.filename):
                    from .utils.cloudinary_utils import upload_to_cloudinary, delete_from_cloudinary, is_cloudinary_url, get_public_id_from_url
                    
                    # Delete old image from Cloudinary if it exists
                    if category.image:
                        try:
                            if is_cloudinary_url(category.image):
                                public_id = get_public_id_from_url(category.image)
                                if public_id:
                                    delete_from_cloudinary(public_id)
                            else:
                                # Old local file - try to delete if exists
                                old_image_path = os.path.join(app.static_folder, category.image)
                                if os.path.exists(old_image_path):
                                    os.remove(old_image_path)
                        except Exception as e:
                            current_app.logger.warning(f"Error deleting old category image: {e}")
                    
                    # Upload new image to Cloudinary
                    upload_result = upload_to_cloudinary(image_file, folder='categories')
                    if upload_result:
                        category.image = upload_result['url']
                        current_app.logger.info(f"✅ Category image updated in Cloudinary: {category.image}")
                    else:
                        flash('Failed to upload image to Cloudinary. Please try again.', 'error')
                        return redirect(url_for('admin_edit_category', category_id=category_id))
        
        category.name = name
        db.session.commit()
        
        flash('Category updated successfully!', 'success')
        return redirect(url_for('admin_categories'))
    
    return render_template('admin/admin/category_form.html', category=category)

@app.route('/admin/categories/<int:category_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_category(category_id):
    """Delete a category and all its products"""
    category = Category.query.get_or_404(category_id)
    
    try:
        # Get all products in this category
        products = Product.query.filter_by(category_id=category_id).all()
        
        # Delete all related items for each product
        for product in products:
            # Delete cart items
            CartItem.query.filter_by(product_id=product.id).delete()
            # Delete wishlist items
            WishlistItem.query.filter_by(product_id=product.id).delete()
            # Delete product image if exists
            if product.image:
                try:
                    image_path = os.path.join(app.static_folder, product.image)
                    if os.path.exists(image_path):
                        os.remove(image_path)
                except Exception as e:
                    app.logger.error(f"Error deleting product image: {e}")
            # Delete the product
            db.session.delete(product)
        
        # Delete category image if exists
        if category.image:
            try:
                image_path = os.path.join(app.static_folder, category.image)
                if os.path.exists(image_path):
                    os.remove(image_path)
            except Exception as e:
                app.logger.error(f"Error deleting category image: {e}")
        
        # Delete the category
        db.session.delete(category)
        db.session.commit()
        
        # Check if request is AJAX
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({
                'status': 'success',
                'message': f'Category and {len(products)} product(s) deleted successfully!'
            }), 200
        
        flash(f'Category and {len(products)} product(s) deleted successfully!', 'success')
        return redirect(url_for('admin_categories'))
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error deleting category: {str(e)}")
        
        # Check if request is AJAX
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({
                'status': 'error',
                'message': f'Error deleting category: {str(e)}'
            }), 500
        
        flash(f'Error deleting category: {str(e)}', 'error')
        return redirect(url_for('admin_categories'))

@app.route('/admin/reupload-missing-files', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_reupload_missing_files():
    """Reupload local files to Cloudinary that haven't been migrated yet"""
    from .utils.cloudinary_utils import upload_file_from_path, is_cloudinary_url
    
    if request.method == 'POST':
        migrated_count = 0
        error_count = 0
        errors = []
        
        # Migrate product images
        products = Product.query.filter(Product.image.isnot(None)).all()
        for product in products:
            if product.image and not is_cloudinary_url(product.image):
                try:
                    local_path = os.path.join(app.static_folder, product.image)
                    if os.path.exists(local_path):
                        upload_result = upload_file_from_path(local_path, folder='products')
                        if upload_result:
                            product.image = upload_result['url']
                            migrated_count += 1
                            current_app.logger.info(f"✅ Migrated product {product.id} image to Cloudinary")
                        else:
                            error_count += 1
                            errors.append(f"Product {product.id} ({product.name}): Upload failed")
                    else:
                        error_count += 1
                        errors.append(f"Product {product.id} ({product.name}): File not found")
                except Exception as e:
                    error_count += 1
                    errors.append(f"Product {product.id} ({product.name}): {str(e)}")
                    current_app.logger.error(f"Error migrating product {product.id}: {e}")
        
        # Migrate category images
        categories = Category.query.filter(Category.image.isnot(None)).all()
        for category in categories:
            if category.image and not is_cloudinary_url(category.image):
                try:
                    local_path = os.path.join(app.static_folder, category.image)
                    if os.path.exists(local_path):
                        upload_result = upload_file_from_path(local_path, folder='categories')
                        if upload_result:
                            category.image = upload_result['url']
                            migrated_count += 1
                            current_app.logger.info(f"✅ Migrated category {category.id} image to Cloudinary")
                        else:
                            error_count += 1
                            errors.append(f"Category {category.id} ({category.name}): Upload failed")
                    else:
                        error_count += 1
                        errors.append(f"Category {category.id} ({category.name}): File not found")
                except Exception as e:
                    error_count += 1
                    errors.append(f"Category {category.id} ({category.name}): {str(e)}")
                    current_app.logger.error(f"Error migrating category {category.id}: {e}")
        
        # Migrate site settings (logo and hero image)
        settings = SiteSettings.query.first()
        if settings:
            if settings.logo_path and not is_cloudinary_url(settings.logo_path):
                try:
                    local_path = os.path.join(app.static_folder, settings.logo_path)
                    if os.path.exists(local_path):
                        upload_result = upload_file_from_path(local_path, folder='branding')
                        if upload_result:
                            settings.logo_path = upload_result['url']
                            migrated_count += 1
                            current_app.logger.info(f"✅ Migrated logo to Cloudinary")
                        else:
                            error_count += 1
                            errors.append("Logo: Upload failed")
                    else:
                        error_count += 1
                        errors.append("Logo: File not found")
                except Exception as e:
                    error_count += 1
                    errors.append(f"Logo: {str(e)}")
                    current_app.logger.error(f"Error migrating logo: {e}")
            
            if settings.hero_image_path and not is_cloudinary_url(settings.hero_image_path):
                try:
                    local_path = os.path.join(app.static_folder, settings.hero_image_path)
                    if os.path.exists(local_path):
                        upload_result = upload_file_from_path(local_path, folder='branding')
                        if upload_result:
                            settings.hero_image_path = upload_result['url']
                            migrated_count += 1
                            current_app.logger.info(f"✅ Migrated hero image to Cloudinary")
                        else:
                            error_count += 1
                            errors.append("Hero image: Upload failed")
                    else:
                        error_count += 1
                        errors.append("Hero image: File not found")
                except Exception as e:
                    error_count += 1
                    errors.append(f"Hero image: {str(e)}")
                    current_app.logger.error(f"Error migrating hero image: {e}")
        
        # Commit all changes
        try:
            db.session.commit()
            if migrated_count > 0:
                flash(f'✅ Successfully migrated {migrated_count} file(s) to Cloudinary!', 'success')
            if error_count > 0:
                flash(f'⚠️ {error_count} file(s) could not be migrated. Check logs for details.', 'warning')
                if errors:
                    current_app.logger.warning(f"Migration errors: {errors}")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error committing migration: {e}")
            flash(f'❌ Error committing changes: {str(e)}', 'error')
        
        return redirect(url_for('admin_reupload_missing_files'))
    
    # GET request - show migration status
    from .utils.cloudinary_utils import is_cloudinary_url
    
    products_count = Product.query.filter(Product.image.isnot(None)).count()
    categories_count = Category.query.filter(Category.image.isnot(None)).count()
    settings = SiteSettings.query.first()
    
    local_products = 0
    local_categories = 0
    local_logo = False
    local_hero = False
    
    for product in Product.query.filter(Product.image.isnot(None)).all():
        if product.image and not is_cloudinary_url(product.image):
            local_products += 1
    
    for category in Category.query.filter(Category.image.isnot(None)).all():
        if category.image and not is_cloudinary_url(category.image):
            local_categories += 1
    
    if settings:
        if settings.logo_path and not is_cloudinary_url(settings.logo_path):
            local_logo = True
        if settings.hero_image_path and not is_cloudinary_url(settings.hero_image_path):
            local_hero = True
    
    total_local = local_products + local_categories + (1 if local_logo else 0) + (1 if local_hero else 0)
    
    return render_template('admin/admin/reupload_files.html', 
                         local_products=local_products,
                         local_categories=local_categories,
                         local_logo=local_logo,
                         local_hero=local_hero,
                         total_local=total_local)

@app.route('/admin/products/download-template')
@login_required
@admin_required
def download_template():
    # Create a sample DataFrame
    data = {
        'Product Name': ['Sample Product 1', 'Sample Product 2'],
        'Category': ['Electronics', 'Clothing'],
        'Price (GMD)': [1000, 500],
        'Stock Quantity': [10, 5],
        'Description': ['Sample description', 'Another description'],
        'Image URL': ['', 'https://example.com/image.jpg'],
        'Recommended Accessories': ['Accessory 1, Accessory 2', '']
    }
    
    # Create Excel file in memory
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame(data).to_excel(writer, index=False, sheet_name='Products')
    
    output.seek(0)
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='product_upload_template.xlsx'
    )

@app.route('/admin/products/<int:product_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    
    # Check if product has been ordered (preserve order history)
    order_item_count = OrderItem.query.filter_by(product_id=product_id).count()
    if order_item_count > 0:
        flash(f'Cannot delete product: It has been ordered {order_item_count} time(s). Order history must be preserved.', 'error')
        return redirect(url_for('admin_products'))
    
    # Delete associated cart items
    CartItem.query.filter_by(product_id=product_id).delete()
    
    # Delete associated wishlist items
    WishlistItem.query.filter_by(product_id=product_id).delete()
    
    # Delete associated image if exists
    if product.image:
        try:
            os.remove(os.path.join(app.static_folder, product.image))
        except:
            pass
    
    db.session.delete(product)
    db.session.commit()
    
    flash('Product deleted successfully!', 'success')
    return redirect(url_for('admin_products'))

@app.route('/admin/orders')
@login_required
@admin_required
def admin_orders():
    # Get filter parameters
    date_filter = request.args.get('date_filter', 'all')  # 'yesterday', 'today', 'tomorrow', '7days', '30days', 'this_month', 'last_month', 'this_year', 'custom', 'all'
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    page = request.args.get('page', 1, type=int)
    per_page = 20  # Number of orders per page
    
    # Base query - only paid/confirmed orders
    # Paid orders are: status in ['paid', 'completed', 'processing'] OR shipping_status in ['shipped', 'delivered']
    # Exclude: 'Pending', 'Cancelled', 'failed'
    from sqlalchemy import or_
    query = Order.query.filter(
        Order.status != 'Pending',
        Order.status != 'Cancelled',
        Order.status != 'failed',
        or_(
            Order.status.in_(['paid', 'completed', 'processing']),
            Order.shipping_status.in_(['shipped', 'delivered'])
        )
    )
    
    # Date filtering
    now = datetime.utcnow()
    date_start = None
    date_end = None
    
    if date_filter == 'yesterday':
        yesterday = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        date_start = yesterday
        date_end = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif date_filter == 'today':
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        date_start = today
        date_end = now
    elif date_filter == 'tomorrow':
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        date_start = tomorrow
        date_end = tomorrow.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif date_filter == '7days':
        date_start = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        date_end = now
    elif date_filter == '30days':
        date_start = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
        date_end = now
    elif date_filter == 'this_month':
        date_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        date_end = now
    elif date_filter == 'last_month':
        current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        date_start = (current_month_start - timedelta(days=1)).replace(day=1)
        date_end = current_month_start - timedelta(microseconds=1)
    elif date_filter == 'this_year':
        date_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        date_end = now
    elif date_filter == 'custom' and start_date and end_date:
        try:
            date_start = datetime.strptime(start_date, '%Y-%m-%d').replace(hour=0, minute=0, second=0, microsecond=0)
            date_end = datetime.strptime(end_date, '%Y-%m-%d').replace(hour=23, minute=59, second=59, microsecond=999999)
        except ValueError:
            pass
    
    if date_start and date_end:
        query = query.filter(Order.created_at >= date_start, Order.created_at <= date_end)
    
    # CSV Export
    if request.args.get('export') == 'csv':
        # Get all orders (no pagination for export)
        export_orders = query.options(
            joinedload(Order.items).joinedload(OrderItem.product),
            joinedload(Order.customer)
        ).order_by(Order.created_at.desc()).all()
        
        # Create CSV
        output = StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow(['Order ID', 'Date', 'Customer', 'Email', 'Phone', 'Total', 'Status', 'Shipping Status', 'Payment Method', 'Items'])
        
        # Write data
        for order in export_orders:
            items_str = '; '.join([f"{item.product.name if item.product else 'N/A'} (Qty: {item.quantity}, Price: D{item.price})" for item in order.items])
            customer_email = order.customer.email if order.customer else ''
            customer_phone = getattr(order, 'customer_phone', '') or ''
            writer.writerow([
                order.id,
                order.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                order.customer.username if order.customer else 'N/A',
                customer_email,
                customer_phone,
                f"D{order.total:.2f}",
                order.status,
                order.shipping_status,
                order.payment_method or 'N/A',
                items_str
            ])
        
        # Prepare response
        output.seek(0)
        filename = f"paid_orders_{date_filter}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'text/csv'
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        return response
    
    # Get pagination object with eager loading
    orders = query.options(
        joinedload(Order.items).joinedload(OrderItem.product),
        joinedload(Order.customer)
    ).order_by(Order.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False)
    
    # Calculate totals for the filtered date range
    totals_query = Order.query.filter(
        Order.status != 'Pending',
        Order.status != 'Cancelled',
        Order.status != 'failed',
        or_(
            Order.status.in_(['paid', 'completed', 'processing']),
            Order.shipping_status.in_(['shipped', 'delivered'])
        )
    )
    
    if date_start and date_end:
        totals_query = totals_query.filter(Order.created_at >= date_start, Order.created_at <= date_end)
    
    # Total Sales
    total_sales_result = totals_query.with_entities(db.func.sum(Order.total)).scalar()
    total_sales = float(total_sales_result) if total_sales_result else 0.0
    
    # Total Paid Orders
    total_orders_count = totals_query.count()
    
    # Average Order Value
    avg_order_value = (total_sales / total_orders_count) if total_orders_count > 0 else 0.0
    
    # Total Quantity Sold
    quantity_query = db.session.query(db.func.sum(OrderItem.quantity)).join(
        Order, OrderItem.order_id == Order.id
    ).filter(
        Order.status != 'Pending',
        Order.status != 'Cancelled',
        Order.status != 'failed',
        or_(
            Order.status.in_(['paid', 'completed', 'processing']),
            Order.shipping_status.in_(['shipped', 'delivered'])
        )
    )
    if date_start and date_end:
        quantity_query = quantity_query.filter(Order.created_at >= date_start, Order.created_at <= date_end)
    total_quantity = int(quantity_query.scalar() or 0)
    
    # Total Unique Customers
    unique_customers_query = totals_query.with_entities(db.func.count(db.distinct(Order.user_id)))
    unique_customers = int(unique_customers_query.scalar() or 0)
    
    def get_cached_chart_data(cache_key):
        """Get cached chart data if available and not expired"""
        global _chart_cache, _chart_cache_time
        if cache_key in _chart_cache and cache_key in _chart_cache_time:
            if time.time() - _chart_cache_time[cache_key] < CHART_CACHE_TTL:
                return _chart_cache[cache_key]
        return None
    
    def set_cached_chart_data(cache_key, data):
        """Cache chart data with timestamp"""
        global _chart_cache, _chart_cache_time
        _chart_cache[cache_key] = data
        _chart_cache_time[cache_key] = time.time()
    
    # Determine chart date range with safety limits
    chart_date_start = None
    chart_date_end = None
    use_monthly_aggregation = False
    
    if date_start and date_end:
        days_diff = (date_end - date_start).days + 1
        # Safety guard: If range > 90 days, use monthly aggregation
        if days_diff > 90:
            use_monthly_aggregation = True
            # Use the full range but aggregate by month
            chart_date_start = date_start
            chart_date_end = date_end
        # Safety guard: If range > 30 days, limit to last 30 days
        elif days_diff > 30:
            chart_date_end = now
            chart_date_start = (now - timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            chart_date_start = date_start
            chart_date_end = date_end
    else:
        # Default to last 30 days if no filter
        chart_date_end = now
        chart_date_start = (now - timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Daily Sales Chart Data (with optimizations)
    cache_key_sales = f'sales_chart_{chart_date_start.strftime("%Y%m%d")}_{chart_date_end.strftime("%Y%m%d")}_{use_monthly_aggregation}'
    daily_sales_data = get_cached_chart_data(cache_key_sales)
    
    if daily_sales_data is None:
        if use_monthly_aggregation:
            # Monthly aggregation for large ranges
            daily_sales_query = db.session.query(
                db.func.date_trunc('month', Order.created_at).label('period'),
                db.func.sum(Order.total).label('total')
            ).filter(
                Order.status != 'Pending',
                Order.status != 'Cancelled',
                Order.status != 'failed',
                or_(
                    Order.status.in_(['paid', 'completed', 'processing']),
                    Order.shipping_status.in_(['shipped', 'delivered'])
                ),
                Order.created_at >= chart_date_start,
                Order.created_at <= chart_date_end
            ).group_by(db.func.date_trunc('month', Order.created_at)).order_by(db.func.date_trunc('month', Order.created_at))
        else:
            # Daily aggregation (max 30 days)
            daily_sales_query = db.session.query(
                db.func.date(Order.created_at).label('date'),
                db.func.sum(Order.total).label('total')
            ).filter(
                Order.status != 'Pending',
                Order.status != 'Cancelled',
                Order.status != 'failed',
                or_(
                    Order.status.in_(['paid', 'completed', 'processing']),
                    Order.shipping_status.in_(['shipped', 'delivered'])
                ),
                Order.created_at >= chart_date_start,
                Order.created_at <= chart_date_end
            ).group_by(db.func.date(Order.created_at)).order_by(db.func.date(Order.created_at))
        
        daily_sales_raw = daily_sales_query.all()
        
        if use_monthly_aggregation:
            # Process monthly data
            sales_dict = {}
            for row in daily_sales_raw:
                period = row.period
                # date_trunc returns a datetime/timestamp, convert to date
                if isinstance(period, datetime):
                    period_date = period.date()
                elif isinstance(period, str):
                    period_date = datetime.strptime(period[:10], '%Y-%m-%d').date()
                elif hasattr(period, 'date'):
                    period_date = period.date()
                else:
                    continue
                # Use year-month as key for monthly aggregation
                date_key = period_date.strftime('%Y-%m-01')
                sales_dict[date_key] = float(row.total) if row.total else 0.0
            
            # Generate monthly data points (limit to max 12 months)
            daily_sales_data = []
            current = chart_date_start.replace(day=1).date()
            end_date = chart_date_end.date()
            month_count = 0
            max_months = 12
            
            while current <= end_date and month_count < max_months:
                date_key = current.strftime('%Y-%m-01')
                daily_sales_data.append({
                    'date': current,
                    'total': sales_dict.get(date_key, 0.0)
                })
                # Move to next month
                if current.month == 12:
                    current = current.replace(year=current.year + 1, month=1)
                else:
                    current = current.replace(month=current.month + 1)
                month_count += 1
        else:
            # Process daily data (max 30 days)
            sales_dict = {}
            for row in daily_sales_raw:
                date_key = row.date if isinstance(row.date, str) else row.date.strftime('%Y-%m-%d')
                sales_dict[date_key] = float(row.total) if row.total else 0.0
            
            # Fill in all days in the range (max 30 days)
            days_diff = (chart_date_end.date() - chart_date_start.date()).days + 1
            days_diff = min(days_diff, 30)  # Hard limit to 30 days
            
            daily_sales_data = []
            for i in range(days_diff):
                current_date = (chart_date_start + timedelta(days=i)).date()
                date_key = current_date.strftime('%Y-%m-%d')
                daily_sales_data.append({
                    'date': current_date,
                    'total': sales_dict.get(date_key, 0.0)
                })
        
        # Cache the result
        set_cached_chart_data(cache_key_sales, daily_sales_data)
    
    # Orders Count Chart Data (with same optimizations)
    cache_key_orders = f'orders_chart_{chart_date_start.strftime("%Y%m%d")}_{chart_date_end.strftime("%Y%m%d")}_{use_monthly_aggregation}'
    orders_count_data = get_cached_chart_data(cache_key_orders)
    
    if orders_count_data is None:
        if use_monthly_aggregation:
            # Monthly aggregation for large ranges
            orders_count_query = db.session.query(
                db.func.date_trunc('month', Order.created_at).label('period'),
                db.func.count(Order.id).label('count')
            ).filter(
                Order.status != 'Pending',
                Order.status != 'Cancelled',
                Order.status != 'failed',
                or_(
                    Order.status.in_(['paid', 'completed', 'processing']),
                    Order.shipping_status.in_(['shipped', 'delivered'])
                ),
                Order.created_at >= chart_date_start,
                Order.created_at <= chart_date_end
            ).group_by(db.func.date_trunc('month', Order.created_at)).order_by(db.func.date_trunc('month', Order.created_at))
        else:
            # Daily aggregation (max 30 days)
            orders_count_query = db.session.query(
                db.func.date(Order.created_at).label('date'),
                db.func.count(Order.id).label('count')
            ).filter(
                Order.status != 'Pending',
                Order.status != 'Cancelled',
                Order.status != 'failed',
                or_(
                    Order.status.in_(['paid', 'completed', 'processing']),
                    Order.shipping_status.in_(['shipped', 'delivered'])
                ),
                Order.created_at >= chart_date_start,
                Order.created_at <= chart_date_end
            ).group_by(db.func.date(Order.created_at)).order_by(db.func.date(Order.created_at))
        
        orders_count_raw = orders_count_query.all()
        
        if use_monthly_aggregation:
            # Process monthly data
            count_dict = {}
            for row in orders_count_raw:
                period = row.period
                # date_trunc returns a datetime/timestamp, convert to date
                if isinstance(period, datetime):
                    period_date = period.date()
                elif isinstance(period, str):
                    period_date = datetime.strptime(period[:10], '%Y-%m-%d').date()
                elif hasattr(period, 'date'):
                    period_date = period.date()
                else:
                    continue
                # Use year-month as key for monthly aggregation
                date_key = period_date.strftime('%Y-%m-01')
                count_dict[date_key] = int(row.count) if row.count else 0
            
            # Generate monthly data points (limit to max 12 months)
            orders_count_data = []
            current = chart_date_start.replace(day=1).date()
            end_date = chart_date_end.date()
            month_count = 0
            max_months = 12
            
            while current <= end_date and month_count < max_months:
                date_key = current.strftime('%Y-%m-01')
                orders_count_data.append({
                    'date': current,
                    'count': count_dict.get(date_key, 0)
                })
                # Move to next month
                if current.month == 12:
                    current = current.replace(year=current.year + 1, month=1)
                else:
                    current = current.replace(month=current.month + 1)
                month_count += 1
        else:
            # Process daily data (max 30 days)
            count_dict = {}
            for row in orders_count_raw:
                date_key = row.date if isinstance(row.date, str) else row.date.strftime('%Y-%m-%d')
                count_dict[date_key] = int(row.count) if row.count else 0
            
            # Fill in all days in the range (max 30 days)
            days_diff = (chart_date_end.date() - chart_date_start.date()).days + 1
            days_diff = min(days_diff, 30)  # Hard limit to 30 days
            
            orders_count_data = []
            for i in range(days_diff):
                current_date = (chart_date_start + timedelta(days=i)).date()
                date_key = current_date.strftime('%Y-%m-%d')
                orders_count_data.append({
                    'date': current_date,
                    'count': count_dict.get(date_key, 0)
                })
        
        # Cache the result
        set_cached_chart_data(cache_key_orders, orders_count_data)
    
    # Top Products Chart Data
    top_products_query = db.session.query(
        Product.id,
        Product.name,
        db.func.sum(OrderItem.quantity).label('total_quantity'),
        db.func.sum(OrderItem.quantity * OrderItem.price).label('total_revenue')
    ).join(
        OrderItem, Product.id == OrderItem.product_id
    ).join(
        Order, OrderItem.order_id == Order.id
    ).filter(
        Order.status != 'Pending',
        Order.status != 'Cancelled',
        Order.status != 'failed',
        or_(
            Order.status.in_(['paid', 'completed', 'processing']),
            Order.shipping_status.in_(['shipped', 'delivered'])
        )
    )
    if date_start and date_end:
        top_products_query = top_products_query.filter(Order.created_at >= date_start, Order.created_at <= date_end)
    top_products_data = top_products_query.group_by(Product.id, Product.name).order_by(
        db.func.sum(OrderItem.quantity * OrderItem.price).desc()
    ).limit(10).all()
        
    return render_template('admin/admin/orders.html',
                         orders=orders, 
                         date_filter=date_filter,
                         start_date=start_date,
                         end_date=end_date,
                         total_sales=total_sales,
                         total_orders_count=total_orders_count,
                         avg_order_value=avg_order_value,
                         total_quantity=total_quantity,
                         unique_customers=unique_customers,
                         daily_sales_data=daily_sales_data,
                         orders_count_data=orders_count_data,
                         top_products_data=top_products_data,
                         use_monthly_aggregation=use_monthly_aggregation)

@app.route('/admin/order/<int:order_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_order_detail(order_id):
    order = Order.query.get_or_404(order_id)
    
    if request.method == 'POST':
        new_status = request.form.get('status')
        if new_status in ['pending', 'processing', 'shipped', 'delivered', 'cancelled']:
            order.status = new_status
            db.session.commit()
            flash('Order status updated successfully!', 'success')
        else:
            flash('Invalid status', 'error')
        
        return redirect(url_for('admin_order_detail', order_id=order.id))
    
    return render_template('admin/admin/order_detail.html', order=order)

@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    try:
        # Get page number from query parameters, default to 1
        page = request.args.get('page', 1, type=int)
        per_page = 10  # Number of users per page
        
        # Get all users ordered by creation date (newest first) with pagination
        users = User.query.order_by(User.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False)
        
        # Debug information
        app.logger.info(f"Found {users.total} users in the database")
        
        return render_template('admin/admin/users.html', 
                             users=users.items,
                             pagination=users,
                             title='Customers')
    except Exception as e:
        app.logger.error(f"Error in admin_users: {str(e)}")
        flash('An error occurred while loading users. Please try again.', 'error')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/user/<int:user_id>')
@login_required
@admin_required
def view_user(user_id):
    try:
        user = User.query.get_or_404(user_id)
        # Get user's orders
        orders = Order.query.filter_by(user_id=user_id).order_by(Order.created_at.desc()).all()
        return render_template('admin/admin/user_detail.html', 
                             user=user, 
                             orders=orders,
                             title='User Details')
    except Exception as e:
        app.logger.error(f"Error viewing user {user_id}: {str(e)}")
        flash('An error occurred while loading user details.', 'error')
        return redirect(url_for('admin_users'))

@app.route('/admin/reports')
@login_required
@admin_required
def admin_reports():
    # Get date range for the last 30 days
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=30)
    
    # Get total sales for the period
    # Use shipping_status for delivery tracking, exclude cancelled orders
    total_sales = db.session.query(db.func.sum(Order.total)).filter(
        Order.created_at.between(start_date, end_date),
        Order.shipping_status == 'delivered',
        Order.status != 'Cancelled'
    ).scalar() or 0
    
    # Get order counts by status
    order_status = db.session.query(
        Order.status,
        db.func.count(Order.id)
    ).filter(
        Order.created_at.between(start_date, end_date)
    ).group_by(Order.status).all()
    
    # Get sales by day for the last 7 days
    sales_by_day_raw = db.session.query(
        db.func.date(Order.created_at).label('date'),
        db.func.sum(Order.total).label('total')
    ).filter(
        Order.created_at >= (end_date - timedelta(days=7)),
        Order.shipping_status == 'delivered',
        Order.status != 'Cancelled'
    ).group_by(db.func.date(Order.created_at)).all()
    
    # Convert date strings to date objects
    sales_by_day = []
    for row in sales_by_day_raw:
        date_str = row.date
        # Parse the date string (SQLite returns 'YYYY-MM-DD')
        if isinstance(date_str, str):
            date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
        else:
            date_obj = date_str
        sales_by_day.append({
            'date': date_obj,
            'total': float(row.total) if row.total else 0.0
        })
    
    # Get top selling products
    top_products_query = db.session.query(
        Product.name,
        db.func.sum(OrderItem.quantity).label('total_quantity')
    )
    top_products_query = top_products_query.join(OrderItem, OrderItem.product_id == Product.id)
    top_products_query = top_products_query.join(Order, Order.id == OrderItem.order_id)
    top_products_query = top_products_query.filter(
        Order.shipping_status == 'delivered',
        Order.status != 'Cancelled'
    )
    top_products_query = top_products_query.group_by(Product.id)
    top_products_raw = top_products_query.order_by(db.desc('total_quantity')).limit(5).all()
    
    # Convert top products to dictionaries for easier template access
    top_products = []
    for row in top_products_raw:
        top_products.append({
            'name': row.name,
            'total_quantity': int(row.total_quantity) if row.total_quantity else 0
        })
    
    return render_template('admin/admin/reports.html',
                         total_sales=total_sales,
                         order_status=dict(order_status),
                         sales_by_day=sales_by_day,
                         top_products=top_products,
                         start_date=start_date.date(),
                         end_date=end_date.date())

@app.route('/admin/sales-dashboard')
@login_required
@admin_required
def admin_sales_dashboard():
    """Comprehensive sales dashboard with 7 days, month, and year data"""
    # Get filter parameters
    category_id = request.args.get('category_id', type=int)
    date_filter = request.args.get('date_filter', 'all')  # '7days', 'month', 'year', 'all'
    
    # Base query filter - only delivered orders (use shipping_status, exclude cancelled)
    base_filter = db.and_(
        Order.shipping_status == 'delivered',
        Order.status != 'Cancelled'
    )
    
    # Category filter if specified
    category_filter = None
    if category_id:
        category_filter = Product.category_id == category_id
    
    now = datetime.utcnow()
    
    # ========== LAST 7 DAYS SECTION ==========
    seven_days_ago = now - timedelta(days=7)
    
    # If category filter is applied, we need to filter orders that have products in that category
    if category_filter:
        # Get order IDs that have products in the selected category
        filtered_order_ids = db.session.query(Order.id).distinct().join(
            OrderItem, Order.id == OrderItem.order_id
        ).join(Product, OrderItem.product_id == Product.id).filter(
            base_filter,
            category_filter,
            Order.created_at >= seven_days_ago
        ).subquery()
        
        seven_days_query = db.session.query(
            db.func.sum(Order.total).label('total_sales'),
            db.func.count(db.distinct(Order.id)).label('total_orders')
        ).filter(Order.id.in_(db.session.query(filtered_order_ids.c.id)))
    else:
        seven_days_query = db.session.query(
            db.func.sum(Order.total).label('total_sales'),
            db.func.count(Order.id).label('total_orders')
        ).filter(
            base_filter,
            Order.created_at >= seven_days_ago
        )
    
    seven_days_stats = seven_days_query.first()
    seven_days_sales = float(seven_days_stats.total_sales) if seven_days_stats.total_sales else 0.0
    seven_days_orders = int(seven_days_stats.total_orders) if seven_days_stats.total_orders else 0
    seven_days_avg = (seven_days_sales / seven_days_orders) if seven_days_orders > 0 else 0.0
    
    # Daily sales for last 7 days
    if category_filter:
        filtered_order_ids = db.session.query(Order.id).distinct().join(
            OrderItem, Order.id == OrderItem.order_id
        ).join(Product, OrderItem.product_id == Product.id).filter(
            base_filter,
            category_filter,
            Order.created_at >= seven_days_ago
        ).subquery()
        
        daily_sales_query = db.session.query(
            db.func.date(Order.created_at).label('date'),
            db.func.sum(Order.total).label('total')
        ).filter(Order.id.in_(db.session.query(filtered_order_ids.c.id))).group_by(
            db.func.date(Order.created_at)
        ).order_by(db.func.date(Order.created_at))
    else:
        daily_sales_query = db.session.query(
            db.func.date(Order.created_at).label('date'),
            db.func.sum(Order.total).label('total')
        ).filter(
            base_filter,
            Order.created_at >= seven_days_ago
        ).group_by(db.func.date(Order.created_at)).order_by(db.func.date(Order.created_at))
    
    daily_sales_raw = daily_sales_query.all()
    daily_sales = []
    # Fill in missing days with zero
    for i in range(7):
        day_date = (now - timedelta(days=6-i)).date()
        day_str = day_date.strftime('%Y-%m-%d')
        day_total = 0.0
        for row in daily_sales_raw:
            row_date_str = row.date if isinstance(row.date, str) else row.date.strftime('%Y-%m-%d')
            if row_date_str == day_str:
                day_total = float(row.total) if row.total else 0.0
                break
        daily_sales.append({
            'date': day_date,
            'total': day_total
        })
    
    # ========== LAST MONTH SECTION ==========
    current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous_month_start = (current_month_start - timedelta(days=1)).replace(day=1)
    previous_month_end = current_month_start
    
    # Helper function to get filtered order IDs for a date range
    def get_filtered_order_ids(start_date, end_date=None):
        if category_filter:
            query = db.session.query(Order.id).distinct().join(
                OrderItem, Order.id == OrderItem.order_id
            ).join(Product, OrderItem.product_id == Product.id).filter(
                base_filter,
                category_filter,
                Order.created_at >= start_date
            )
            if end_date:
                query = query.filter(Order.created_at < end_date)
            return query.subquery()
        return None
    
    # Current month stats
    if category_filter:
        month_order_ids = get_filtered_order_ids(current_month_start)
        month_query = db.session.query(
            db.func.sum(Order.total).label('total_sales'),
            db.func.count(db.distinct(Order.id)).label('total_orders')
        ).filter(Order.id.in_(db.session.query(month_order_ids.c.id)))
    else:
        month_query = db.session.query(
            db.func.sum(Order.total).label('total_sales'),
            db.func.count(Order.id).label('total_orders')
        ).filter(
            base_filter,
            Order.created_at >= current_month_start
        )
    month_stats = month_query.first()
    
    month_sales = float(month_stats.total_sales) if month_stats.total_sales else 0.0
    month_orders = int(month_stats.total_orders) if month_stats.total_orders else 0
    month_avg = (month_sales / month_orders) if month_orders > 0 else 0.0
    
    # Previous month stats for comparison
    if category_filter:
        prev_month_order_ids = get_filtered_order_ids(previous_month_start, previous_month_end)
        prev_month_query = db.session.query(
            db.func.sum(Order.total).label('total_sales'),
            db.func.count(db.distinct(Order.id)).label('total_orders')
        ).filter(Order.id.in_(db.session.query(prev_month_order_ids.c.id)))
    else:
        prev_month_query = db.session.query(
            db.func.sum(Order.total).label('total_sales'),
            db.func.count(Order.id).label('total_orders')
        ).filter(
            base_filter,
            Order.created_at >= previous_month_start,
            Order.created_at < previous_month_end
        )
    prev_month_stats = prev_month_query.first()
    
    prev_month_sales = float(prev_month_stats.total_sales) if prev_month_stats.total_sales else 0.0
    month_change = ((month_sales - prev_month_sales) / prev_month_sales * 100) if prev_month_sales > 0 else (100 if month_sales > 0 else 0)
    
    # Monthly trend for last 6 months
    monthly_trend = []
    for i in range(6):
        # Calculate proper month boundaries
        if i == 0:
            month_start = current_month_start
            month_end = now
        else:
            # Get the month that is i months before current month
            target_date = current_month_start
            for _ in range(i):
                # Go to first day of previous month
                if target_date.month == 1:
                    target_date = target_date.replace(year=target_date.year - 1, month=12, day=1)
                else:
                    target_date = target_date.replace(month=target_date.month - 1, day=1)
            month_start = target_date
            # Calculate end of that month
            if month_start.month == 12:
                month_end = month_start.replace(year=month_start.year + 1, month=1, day=1)
            else:
                month_end = month_start.replace(month=month_start.month + 1, day=1)
        
        if category_filter:
            trend_order_ids = get_filtered_order_ids(month_start, month_end)
            trend_query = db.session.query(
                db.func.sum(Order.total).label('total')
            ).filter(Order.id.in_(db.session.query(trend_order_ids.c.id)))
        else:
            trend_query = db.session.query(
                db.func.sum(Order.total).label('total')
            ).filter(
                base_filter,
                Order.created_at >= month_start,
                Order.created_at < month_end
            )
        trend_result = trend_query.scalar()
        monthly_trend.append({
            'month': month_start.strftime('%b %Y'),
            'total': float(trend_result) if trend_result else 0.0
        })
    monthly_trend.reverse()
    
    # ========== CURRENT YEAR SECTION ==========
    current_year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # Year stats
    if category_filter:
        year_order_ids = get_filtered_order_ids(current_year_start)
        year_query = db.session.query(
            db.func.sum(Order.total).label('total_sales'),
            db.func.count(db.distinct(Order.id)).label('total_orders')
        ).filter(Order.id.in_(db.session.query(year_order_ids.c.id)))
    else:
        year_query = db.session.query(
            db.func.sum(Order.total).label('total_sales'),
            db.func.count(Order.id).label('total_orders')
        ).filter(
            base_filter,
            Order.created_at >= current_year_start
        )
    year_stats = year_query.first()
    
    year_sales = float(year_stats.total_sales) if year_stats.total_sales else 0.0
    year_orders = int(year_stats.total_orders) if year_stats.total_orders else 0
    year_avg = (year_sales / year_orders) if year_orders > 0 else 0.0
    
    # Monthly breakdown for current year
    monthly_breakdown = []
    current_month = 1
    while current_month <= now.month:
        month_start = now.replace(month=current_month, day=1, hour=0, minute=0, second=0, microsecond=0)
        if current_month == 12:
            month_end = now.replace(year=now.year + 1, month=1, day=1)
        else:
            month_end = now.replace(month=current_month + 1, day=1)
        
        if category_filter:
            breakdown_order_ids = get_filtered_order_ids(month_start, month_end)
            breakdown_query = db.session.query(
                db.func.sum(Order.total).label('total'),
                db.func.count(db.distinct(Order.id)).label('orders')
            ).filter(Order.id.in_(db.session.query(breakdown_order_ids.c.id)))
        else:
            breakdown_query = db.session.query(
                db.func.sum(Order.total).label('total'),
                db.func.count(Order.id).label('orders')
            ).filter(
                base_filter,
                Order.created_at >= month_start,
                Order.created_at < month_end
            )
        breakdown_result = breakdown_query.first()
        monthly_breakdown.append({
            'month': month_start.strftime('%b'),
            'total': float(breakdown_result.total) if breakdown_result.total else 0.0,
            'orders': int(breakdown_result.orders) if breakdown_result.orders else 0
        })
        current_month += 1
    
    # Top selling products for the year
    top_products_query = db.session.query(
        Product.name,
        Product.id,
        db.func.sum(OrderItem.quantity).label('total_quantity'),
        db.func.sum(OrderItem.quantity * OrderItem.price).label('total_revenue')
    ).join(OrderItem, OrderItem.product_id == Product.id).join(Order, Order.id == OrderItem.order_id)
    
    if category_filter:
        top_products_query = top_products_query.filter(category_filter)
    top_products_query = top_products_query.filter(
        base_filter,
        Order.created_at >= current_year_start
    ).group_by(Product.id).order_by(db.desc('total_quantity')).limit(10)
    
    top_products_raw = top_products_query.all()
    top_products = []
    for row in top_products_raw:
        top_products.append({
            'name': row.name,
            'id': row.id,
            'quantity': int(row.total_quantity) if row.total_quantity else 0,
            'revenue': float(row.total_revenue) if row.total_revenue else 0.0
        })
    
    # Get all categories for filter dropdown
    categories = Category.query.order_by(Category.name).all()
    
    return render_template('admin/admin/sales_dashboard.html',
                         # 7 Days data
                         seven_days_sales=seven_days_sales,
                         seven_days_orders=seven_days_orders,
                         seven_days_avg=seven_days_avg,
                         daily_sales=daily_sales,
                         # Month data
                         month_sales=month_sales,
                         month_orders=month_orders,
                         month_avg=month_avg,
                         month_change=month_change,
                         monthly_trend=monthly_trend,
                         prev_month_sales=prev_month_sales,
                         # Year data
                         year_sales=year_sales,
                         year_orders=year_orders,
                         year_avg=year_avg,
                         monthly_breakdown=monthly_breakdown,
                         top_products=top_products,
                         # Filter data
                         categories=categories,
                         selected_category_id=category_id,
                         current_date=now.date())

@app.route('/admin/sales-dashboard/export/<section>')
@login_required
@admin_required
def admin_sales_dashboard_export(section):
    """Export sales dashboard data to CSV"""
    import csv
    
    category_id = request.args.get('category_id', type=int)
    base_filter = Order.status == 'delivered'
    category_filter = None
    if category_id:
        category_filter = Product.category_id == category_id
    
    now = datetime.utcnow()
    output = BytesIO()
    writer = csv.writer(output)
    
    if section == '7days':
        seven_days_ago = now - timedelta(days=7)
        writer.writerow(['Date', 'Sales (D)', 'Orders'])
        
        if category_filter:
            filtered_order_ids = db.session.query(Order.id).distinct().join(
                OrderItem, Order.id == OrderItem.order_id
            ).join(Product, OrderItem.product_id == Product.id).filter(
                base_filter, category_filter, Order.created_at >= seven_days_ago
            ).subquery()
            
            daily_query = db.session.query(
                db.func.date(Order.created_at).label('date'),
                db.func.sum(Order.total).label('total'),
                db.func.count(db.distinct(Order.id)).label('orders')
            ).filter(Order.id.in_(db.session.query(filtered_order_ids.c.id))).group_by(
                db.func.date(Order.created_at)
            ).order_by(db.func.date(Order.created_at))
        else:
            daily_query = db.session.query(
                db.func.date(Order.created_at).label('date'),
                db.func.sum(Order.total).label('total'),
                db.func.count(Order.id).label('orders')
            ).filter(base_filter, Order.created_at >= seven_days_ago).group_by(
                db.func.date(Order.created_at)
            ).order_by(db.func.date(Order.created_at))
        
        for row in daily_query.all():
            date_str = row.date if isinstance(row.date, str) else row.date.strftime('%Y-%m-%d')
            writer.writerow([date_str, f"{row.total:.2f}" if row.total else "0.00", row.orders or 0])
        
        filename = 'sales_7days.csv'
        
    elif section == 'month':
        current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        writer.writerow(['Month', 'Sales (D)', 'Orders'])
        
        for i in range(6):
            # Calculate proper month boundaries
            if i == 0:
                month_start = current_month_start
                month_end = now
            else:
                # Get the month that is i months before current month
                target_date = current_month_start
                for _ in range(i):
                    # Go to first day of previous month
                    if target_date.month == 1:
                        target_date = target_date.replace(year=target_date.year - 1, month=12, day=1)
                    else:
                        target_date = target_date.replace(month=target_date.month - 1, day=1)
                month_start = target_date
                # Calculate end of that month
                if month_start.month == 12:
                    month_end = month_start.replace(year=month_start.year + 1, month=1, day=1)
                else:
                    month_end = month_start.replace(month=month_start.month + 1, day=1)
            
            if category_filter:
                filtered_order_ids = db.session.query(Order.id).distinct().join(
                    OrderItem, Order.id == OrderItem.order_id
                ).join(Product, OrderItem.product_id == Product.id).filter(
                    base_filter, category_filter, Order.created_at >= month_start, Order.created_at < month_end
                ).subquery()
                
                month_query = db.session.query(
                    db.func.sum(Order.total).label('total'),
                    db.func.count(db.distinct(Order.id)).label('orders')
                ).filter(Order.id.in_(db.session.query(filtered_order_ids.c.id)))
            else:
                month_query = db.session.query(
                    db.func.sum(Order.total).label('total'),
                    db.func.count(Order.id).label('orders')
                ).filter(base_filter, Order.created_at >= month_start, Order.created_at < month_end)
            
            result = month_query.first()
            writer.writerow([
                month_start.strftime('%b %Y'),
                f"{result.total:.2f}" if result.total else "0.00",
                result.orders or 0
            ])
        
        filename = 'sales_monthly_trend.csv'
        
    elif section == 'year':
        current_year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        writer.writerow(['Month', 'Sales (D)', 'Orders'])
        
        current_month = 1
        while current_month <= now.month:
            month_start = now.replace(month=current_month, day=1, hour=0, minute=0, second=0, microsecond=0)
            if current_month == 12:
                month_end = now.replace(year=now.year + 1, month=1, day=1)
            else:
                month_end = now.replace(month=current_month + 1, day=1)
            
            if category_filter:
                filtered_order_ids = db.session.query(Order.id).distinct().join(
                    OrderItem, Order.id == OrderItem.order_id
                ).join(Product, OrderItem.product_id == Product.id).filter(
                    base_filter, category_filter, Order.created_at >= month_start, Order.created_at < month_end
                ).subquery()
                
                month_query = db.session.query(
                    db.func.sum(Order.total).label('total'),
                    db.func.count(db.distinct(Order.id)).label('orders')
                ).filter(Order.id.in_(db.session.query(filtered_order_ids.c.id)))
            else:
                month_query = db.session.query(
                    db.func.sum(Order.total).label('total'),
                    db.func.count(Order.id).label('orders')
                ).filter(base_filter, Order.created_at >= month_start, Order.created_at < month_end)
            
            result = month_query.first()
            writer.writerow([
                month_start.strftime('%B %Y'),
                f"{result.total:.2f}" if result.total else "0.00",
                result.orders or 0
            ])
            current_month += 1
        
        # Add top products
        writer.writerow([])
        writer.writerow(['Top Selling Products'])
        writer.writerow(['Product Name', 'Quantity Sold', 'Revenue (D)'])
        
        top_products_query = db.session.query(
            Product.name,
            db.func.sum(OrderItem.quantity).label('total_quantity'),
            db.func.sum(OrderItem.quantity * OrderItem.price).label('total_revenue')
        ).join(OrderItem, OrderItem.product_id == Product.id).join(Order, Order.id == OrderItem.order_id)
        
        if category_filter:
            top_products_query = top_products_query.filter(category_filter)
        top_products_query = top_products_query.filter(
            base_filter, Order.created_at >= current_year_start
        ).group_by(Product.id).order_by(db.desc('total_quantity')).limit(10)
        
        for row in top_products_query.all():
            writer.writerow([
                row.name,
                int(row.total_quantity) if row.total_quantity else 0,
                f"{row.total_revenue:.2f}" if row.total_revenue else "0.00"
            ])
        
        filename = 'sales_year.csv'
    else:
        abort(404)
    
    output.seek(0)
    return send_file(output, mimetype='text/csv', as_attachment=True, download_name=filename)

# ======================
# Order Management System Routes
# ======================

# Admin Order Management
@app.route('/admin/order-management')
@login_required
@admin_required
def admin_order_management():
    """Admin panel for full order management"""
    status = request.args.get('status', 'all')
    page = request.args.get('page', 1, type=int)
    per_page = 20
    sort_by = request.args.get('sort', 'newest')
    location_filter = request.args.get('location', 'all')
    customer_filter = request.args.get('customer', 'all')
    search_query = request.args.get('search', '')
    
    query = Order.query
    
    # Status filter
    if status == 'pending':
        query = query.filter_by(shipping_status='pending')
    elif status == 'shipped':
        query = query.filter_by(shipping_status='shipped')
    elif status == 'delivered':
        query = query.filter_by(shipping_status='delivered')
    elif status == 'submitted_price':
        query = query.filter_by(details_submitted=True, shipping_status='pending')
    
    # Location filter
    if location_filter == 'china':
        query = query.filter(Order.location != 'In The Gambia')
    elif location_filter == 'gambia':
        query = query.filter_by(location='In The Gambia')
    
    # Customer filter
    if customer_filter != 'all':
        try:
            customer_id = int(customer_filter)
            query = query.filter_by(user_id=customer_id)
        except ValueError:
            pass
    
    # Search filter
    needs_user_join = False
    if search_query:
        search_term = f'%{search_query}%'
        # Try to parse as order ID first
        try:
            order_id = int(search_query)
            query = query.filter(Order.id == order_id)
        except ValueError:
            # Search in username, customer name, phone
            needs_user_join = True
            query = query.outerjoin(User).filter(
                db.or_(
                    User.username.like(search_term),
                    Order.customer_name.like(search_term),
                    Order.customer_phone.like(search_term)
                )
            )
    
    # Sorting
    if sort_by == 'oldest':
        query = query.order_by(Order.created_at.asc())
    elif sort_by == 'customer':
        if not needs_user_join:
            query = query.outerjoin(User)
        query = query.order_by(User.username.asc())
    else:  # newest (default)
        query = query.order_by(Order.created_at.desc())
    
    orders = query.paginate(page=page, per_page=per_page, error_out=False)
    
    # Dashboard stats
    pending_count = Order.query.filter_by(shipping_status='pending').count()
    shipped_count = Order.query.filter_by(shipping_status='shipped').count()
    delivered_count = Order.query.filter_by(shipping_status='delivered').count()
    total_count = Order.query.count()
    
    # Get all customers for filter dropdown
    customers = User.query.filter(User.role == 'customer').order_by(User.username).all()
    
    # Get submitted price summary data from ShipmentRecord
    shipments = None
    submitted_summary = None
    shipment_stats = None
    
    if status == 'submitted_price':
        # Get all shipment records with filters
        shipment_query = ShipmentRecord.query
        
        # Apply sorting
        if sort_by == 'oldest':
            shipment_query = shipment_query.order_by(ShipmentRecord.submission_date.asc())
        else:  # newest (default)
            shipment_query = shipment_query.order_by(ShipmentRecord.submission_date.desc())
        
        shipments = shipment_query.all()
        
        # Calculate statistics
        if shipments:
            total_orders = sum(len(s.order_ids.split(',')) if s.order_ids else 0 for s in shipments)
            unique_partners = len(set(s.submitted_by for s in shipments))
            grand_total_cost = sum(s.total_cost for s in shipments)
            latest_date = max(s.submission_date for s in shipments) if shipments else None
            
            shipment_stats = {
                'total_orders': total_orders,
                'total_partners': unique_partners,
                'grand_total_cost': grand_total_cost,
                'grand_total_cost_formatted': f"{grand_total_cost:,.2f}",  # Format with commas and 2 decimals
                'latest_date': latest_date
            }
    else:
        # For other statuses, keep the old summary logic
        latest_shipment = ShipmentRecord.query.order_by(ShipmentRecord.submission_date.desc()).first()
        if latest_shipment:
            order_count = len(latest_shipment.order_ids.split(',')) if latest_shipment.order_ids else 0
            submitted_summary = {
                'total_weight': latest_shipment.weight_total,
                'total_shipping': latest_shipment.shipping_price,
                'total_cost': latest_shipment.total_cost,
                'submitted_at': latest_shipment.submission_date,
                'submitted_by': latest_shipment.submitter.username if latest_shipment.submitter else 'Unknown',
                'order_count': order_count
            }
    
    return render_template('admin/admin/order_management.html',
                         orders=orders,
                         status=status,
                         pending_count=pending_count,
                         shipped_count=shipped_count,
                         delivered_count=delivered_count,
                         total_count=total_count,
                         sort_by=sort_by,
                         location_filter=location_filter,
                         customer_filter=customer_filter,
                         search_query=search_query,
                         customers=customers,
                         submitted_summary=submitted_summary,
                         shipments=shipments,
                         shipment_stats=shipment_stats)

@app.route('/admin/manage-users')
@login_required
@admin_required
def admin_manage_users():
    """Admin panel for managing China and Gambia team users"""
    role_filter = request.args.get('role', 'all')
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    query = User.query.filter(User.role.in_(['china_partner', 'gambia_team', 'admin']))
    
    if role_filter == 'china_partner':
        query = query.filter_by(role='china_partner')
    elif role_filter == 'gambia_team':
        query = query.filter_by(role='gambia_team')
    elif role_filter == 'admin':
        query = query.filter_by(role='admin')
    
    users = query.order_by(User.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False)
    
    return render_template('admin/admin/manage_users.html',
                         users=users,
                         role_filter=role_filter)

@app.route('/admin/manage-users/add', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_add_user():
    """Add a new user for China or Gambia team"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role')
        email = request.form.get('email', f'{username}@buxin.com')
        
        if not username or not password or not role:
            flash('All fields are required', 'error')
            return redirect(url_for('admin_add_user'))
        
        if role not in ['china_partner', 'gambia_team', 'admin']:
            flash('Invalid role', 'error')
            return redirect(url_for('admin_add_user'))
        
        # Check if username already exists
        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'error')
            return redirect(url_for('admin_add_user'))
        
        # Check if email already exists (if provided)
        if email and User.query.filter_by(email=email).first():
            flash('Email already exists. Please use a different email or leave it blank to auto-generate.', 'error')
            return redirect(url_for('admin_add_user'))
        
        # If email is not provided or empty, generate one that's unique
        if not email or email.strip() == '':
            base_email = f'{username}@buxin.com'
            email = base_email
            counter = 1
            # Ensure generated email is unique
            while User.query.filter_by(email=email).first():
                email = f'{username}{counter}@buxin.com'
                counter += 1
        
        try:
            user = User(
                username=username,
                email=email,
                role=role,
                active=True,
                is_admin=(role == 'admin')
            )
            user.set_password(password)
            
            db.session.add(user)
            db.session.commit()
            
            flash(f'User {username} created successfully', 'success')
            return redirect(url_for('admin_manage_users'))
        except IntegrityError as e:
            db.session.rollback()
            if 'email' in str(e).lower():
                flash('Email already exists. Please use a different email.', 'error')
            elif 'username' in str(e).lower():
                flash('Username already exists. Please choose a different username.', 'error')
            else:
                flash('An error occurred while creating the user. Please try again.', 'error')
            return redirect(url_for('admin_add_user'))
        except Exception as e:
            db.session.rollback()
            app.logger.error(f'Error creating user: {str(e)}')
            flash('An error occurred while creating the user. Please try again.', 'error')
            return redirect(url_for('admin_add_user'))
    
    return render_template('admin/admin/add_user.html')

@app.route('/admin/manage-users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_user(user_id):
    """Edit a user"""
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role')
        active = request.form.get('active') == 'on'
        
        if not username or not role:
            flash('Username and role are required', 'error')
            return redirect(url_for('admin_edit_user', user_id=user_id))
        
        # Check if username already exists (excluding current user)
        existing = User.query.filter_by(username=username).first()
        if existing and existing.id != user.id:
            flash('Username already exists', 'error')
            return redirect(url_for('admin_edit_user', user_id=user_id))
        
        try:
            user.username = username
            user.role = role
            user.active = active
            user.is_admin = (role == 'admin')
            
            if password:
                user.set_password(password)
            
            db.session.commit()
            
            flash(f'User {username} updated successfully', 'success')
            return redirect(url_for('admin_manage_users'))
        except IntegrityError as e:
            db.session.rollback()
            if 'email' in str(e).lower():
                flash('Email conflict detected. Please contact support.', 'error')
            elif 'username' in str(e).lower():
                flash('Username already exists. Please choose a different username.', 'error')
            else:
                flash('An error occurred while updating the user. Please try again.', 'error')
            return redirect(url_for('admin_edit_user', user_id=user_id))
        except Exception as e:
            db.session.rollback()
            app.logger.error(f'Error updating user: {str(e)}')
            flash('An error occurred while updating the user. Please try again.', 'error')
            return redirect(url_for('admin_edit_user', user_id=user_id))
    
    return render_template('admin/admin/edit_user.html', user=user)

@app.route('/admin/manage-users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_user(user_id):
    """Delete a user"""
    user = User.query.get_or_404(user_id)
    
    if user.id == current_user.id:
        flash('You cannot delete your own account', 'error')
        return redirect(url_for('admin_manage_users'))
    
    username = user.username
    db.session.delete(user)
    db.session.commit()
    
    flash(f'User {username} deleted successfully', 'success')
    return redirect(url_for('admin_manage_users'))

@app.route('/admin/order/<int:order_id>/mark-shipped', methods=['POST'])
@login_required
@admin_required
def admin_mark_shipped(order_id):
    """Mark order as shipped (admin)"""
    order = Order.query.get_or_404(order_id)
    order.shipping_status = 'shipped'
    order.shipped_at = datetime.utcnow()
    db.session.commit()
    flash('Order marked as shipped', 'success')
    return redirect(url_for('admin_order_management', 
                           status=request.args.get('status', 'all'),
                           sort=request.args.get('sort', 'newest'),
                           location=request.args.get('location', 'all'),
                           customer=request.args.get('customer', 'all'),
                           search=request.args.get('search', '')))

@app.route('/admin/order/<int:order_id>/mark-delivered', methods=['POST'])
@login_required
@admin_required
def admin_mark_delivered(order_id):
    """Mark order as delivered (admin)"""
    order = Order.query.get_or_404(order_id)
    order.shipping_status = 'delivered'
    order.delivered_at = datetime.utcnow()
    db.session.commit()
    flash('Order marked as delivered', 'success')
    return redirect(url_for('admin_order_management', 
                           status=request.args.get('status', 'all'),
                           sort=request.args.get('sort', 'newest'),
                           location=request.args.get('location', 'all'),
                           customer=request.args.get('customer', 'all'),
                           search=request.args.get('search', '')))

@app.route('/admin/order/<int:order_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_order_details(order_id):
    """Edit order shipment details"""
    order = Order.query.get_or_404(order_id)
    
    if request.method == 'POST':
        try:
            product_weight_kg = request.form.get('product_weight_kg')
            shipping_price_gmd = request.form.get('shipping_price_gmd')
            total_cost_gmd = request.form.get('total_cost_gmd')
            
            if product_weight_kg:
                try:
                    order.product_weight_kg = float(product_weight_kg)
                except ValueError:
                    flash('Invalid weight format', 'error')
                    return redirect(url_for('admin_edit_order_details', order_id=order_id))
            
            if shipping_price_gmd:
                try:
                    order.shipping_price_gmd = float(shipping_price_gmd)
                except ValueError:
                    flash('Invalid shipping price format', 'error')
                    return redirect(url_for('admin_edit_order_details', order_id=order_id))
            
            if total_cost_gmd:
                try:
                    order.total_cost_gmd = float(total_cost_gmd)
                except ValueError:
                    flash('Invalid total cost format', 'error')
                    return redirect(url_for('admin_edit_order_details', order_id=order_id))
            
            db.session.commit()
            flash('Order details updated successfully', 'success')
            return redirect(url_for('admin_order_management', 
                                   status=request.args.get('status', 'all'),
                                   sort=request.args.get('sort', 'newest'),
                                   location=request.args.get('location', 'all'),
                                   customer=request.args.get('customer', 'all'),
                                   search=request.args.get('search', '')))
        except Exception as e:
            db.session.rollback()
            app.logger.error(f'Error updating order details: {str(e)}')
            flash('An error occurred while updating order', 'error')
            return redirect(url_for('admin_edit_order_details', order_id=order_id))
    
    return render_template('admin/admin/edit_order_details.html', order=order)

@app.route('/admin/order/<int:order_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_order(order_id):
    """Delete an order"""
    order = Order.query.get_or_404(order_id)
    
    try:
        order_id_val = order.id
        # Delete order items first
        OrderItem.query.filter_by(order_id=order_id_val).delete()
        # Delete the order
        db.session.delete(order)
        db.session.commit()
        flash(f'Order #{order_id_val} deleted successfully', 'success')
    except Exception as e:
        db.session.rollback()
        app.logger.error(f'Error deleting order: {str(e)}')
        flash('An error occurred while deleting order', 'error')
    
    return redirect(url_for('admin_order_management', 
                           status=request.args.get('status', 'all'),
                           sort=request.args.get('sort', 'newest'),
                           location=request.args.get('location', 'all'),
                           customer=request.args.get('customer', 'all'),
                           search=request.args.get('search', '')))

@app.route('/admin/orders/submit-details', methods=['POST'])
@login_required
@admin_required
def admin_submit_order_details_batch():
    """Submit shipment details for selected orders (admin)"""
    try:
        product_weight_kg = request.form.get('product_weight_kg')
        shipping_price_gmd = request.form.get('shipping_price_gmd')
        total_cost_gmd = request.form.get('total_cost_gmd')
        selected_orders_str = request.form.get('selected_orders', '')
        
        # Validate inputs
        redirect_params = {
            'status': request.args.get('status', 'all'),
            'sort': request.args.get('sort', 'newest'),
            'location': request.args.get('location', 'all'),
            'customer': request.args.get('customer', 'all'),
            'search': request.args.get('search', '')
        }
        
        if not product_weight_kg or not shipping_price_gmd or not total_cost_gmd:
            flash('All fields are required / 所有字段都是必填的', 'error')
            return redirect(url_for('admin_order_management', **redirect_params))
        
        if not selected_orders_str or selected_orders_str.strip() == '':
            flash('Please select at least one order / 请至少选择一个订单', 'error')
            return redirect(url_for('admin_order_management', **redirect_params))
        
        # Convert to float
        try:
            weight = float(product_weight_kg)
            shipping = float(shipping_price_gmd)
            total = float(total_cost_gmd)
        except ValueError:
            flash('Invalid number format / 数字格式无效', 'error')
            return redirect(url_for('admin_order_management', **redirect_params))
        
        # Parse selected order IDs and deduplicate
        try:
            selected_order_ids = list(set([int(id.strip()) for id in selected_orders_str.split(',') if id.strip()]))
        except ValueError:
            flash('Invalid order selection / 无效的订单选择', 'error')
            return redirect(url_for('admin_order_management', **redirect_params))
        
        if not selected_order_ids:
            flash('Please select at least one order / 请至少选择一个订单', 'error')
            return redirect(url_for('admin_order_management', **redirect_params))
        
        # Get selected orders
        selected_orders = Order.query.filter(Order.id.in_(selected_order_ids)).all()
        
        if not selected_orders:
            flash('No valid orders selected / 没有选择有效的订单', 'warning')
            return redirect(url_for('admin_order_management', **redirect_params))
        
        # Update selected orders
        updated_count = 0
        for order in selected_orders:
            order.product_weight_kg = weight
            order.shipping_price_gmd = shipping
            order.total_cost_gmd = total
            order.details_submitted = True
            if order.status != 'Details Submitted':
                order.status = 'Details Submitted'
            updated_count += 1
        
        db.session.commit()
        
        flash(f'✅ Details Submitted Successfully for {updated_count} order(s) / 信息提交成功，已更新 {updated_count} 个订单', 'success')
        return redirect(url_for('admin_order_management', **redirect_params))
    except Exception as e:
        db.session.rollback()
        app.logger.error(f'Error submitting order details: {str(e)}')
        flash('An error occurred / 发生错误', 'error')
        redirect_params = {
            'status': request.args.get('status', 'all'),
            'sort': request.args.get('sort', 'newest'),
            'location': request.args.get('location', 'all'),
            'customer': request.args.get('customer', 'all'),
            'search': request.args.get('search', '')
        }
        return redirect(url_for('admin_order_management', **redirect_params))

@app.route('/admin/shipment/<int:shipment_id>/verify', methods=['POST'])
@login_required
@admin_required
def admin_verify_shipment(shipment_id):
    """Verify a shipment record"""
    try:
        shipment = ShipmentRecord.query.get_or_404(shipment_id)
        shipment.verified = True
        shipment.verified_by = current_user.id
        shipment.verified_at = datetime.utcnow()
        db.session.commit()
        flash('Shipment record verified successfully / 运输记录已核实', 'success')
    except Exception as e:
        db.session.rollback()
        app.logger.error(f'Error verifying shipment: {str(e)}')
        flash('Error verifying shipment / 核实运输记录时出错', 'error')
    
    return redirect(url_for('admin_order_management', status='submitted_price'))

@app.route('/admin/shipment/<int:shipment_id>/details', methods=['GET'])
@login_required
@admin_required
def admin_shipment_details(shipment_id):
    """Get shipment details for modal"""
    try:
        shipment = ShipmentRecord.query.get_or_404(shipment_id)
        order_ids = [int(id.strip()) for id in shipment.order_ids.split(',') if id.strip()]
        orders = Order.query.filter(Order.id.in_(order_ids)).all()
        
        order_details = []
        for order in orders:
            order_details.append({
                'id': order.id,
                'customer_name': order.customer_name or (order.user.username if order.user else 'Unknown'),
                'total': order.total,
                'items': [{
                    'product_name': item.product.name if item.product else 'Unknown',
                    'quantity': item.quantity,
                    'price': item.price
                } for item in order.items]
            })
        
        return jsonify({
            'success': True,
            'shipment': {
                'id': shipment.id,
                'weight_total': shipment.weight_total,
                'shipping_price': shipment.shipping_price,
                'total_cost': shipment.total_cost,
                'submission_date': shipment.submission_date.strftime('%d-%b-%Y %H:%M') if shipment.submission_date else 'N/A',
                'submitted_by': shipment.submitter.username if shipment.submitter else 'Unknown',
                'verified': shipment.verified,
                'verified_by': shipment.verifier.username if shipment.verifier else None,
                'verified_at': shipment.verified_at.strftime('%d-%b-%Y %H:%M') if shipment.verified_at else None
            },
            'orders': order_details
        })
    except Exception as e:
        app.logger.error(f'Error getting shipment details: {str(e)}')
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/admin/shipments/export', methods=['GET'])
@login_required
@admin_required
def admin_export_shipments():
    """Export shipment records to Excel"""
    try:
        shipments = ShipmentRecord.query.order_by(ShipmentRecord.submission_date.desc()).all()
        
        # Prepare data for Excel
        data = []
        for shipment in shipments:
            order_ids_list = shipment.order_ids.split(',') if shipment.order_ids else []
            order_ids_display = ', '.join([f'#{id.strip()}' for id in order_ids_list])
            
            data.append({
                'Total Weight (kg)': shipment.weight_total,
                'Total Shipping Price (GMD)': shipment.shipping_price,
                'Total Product Cost (GMD)': shipment.total_cost,
                'Date Submitted': shipment.submission_date.strftime('%d-%b-%Y') if shipment.submission_date else 'N/A',
                'Submitted By': shipment.submitter.username if shipment.submitter else 'Unknown',
                'Related Orders': order_ids_display,
                'Verified': 'Yes' if shipment.verified else 'No',
                'Verified By': shipment.verifier.username if shipment.verifier else 'N/A',
                'Verified At': shipment.verified_at.strftime('%d-%b-%Y %H:%M') if shipment.verified_at else 'N/A'
            })
        
        # Create DataFrame
        df = pd.DataFrame(data)
        
        # Create Excel file in memory
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Shipment Records')
        
        output.seek(0)
        
        # Generate filename with current date
        filename = f'shipment_records_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.xlsx'
        
        return send_file(
            output,
            mimetype='application/vnd.openpyxl.formats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        app.logger.error(f'Error exporting shipments: {str(e)}')
        flash('Error exporting shipment records / 导出运输记录时出错', 'error')
        return redirect(url_for('admin_order_management', status='submitted_price'))

# China Partner Routes
@app.route('/china/login', methods=['GET', 'POST'])
def china_login():
    """Login page for China Partners and Admins"""
    # Allow admin or china_partner users
    if current_user.is_authenticated and current_user.active:
        if current_user.is_admin or current_user.role == 'admin' or current_user.role == 'china_partner':
            return redirect(url_for('china_orders'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        # Allow admin or china_partner users
        user = User.query.filter(
            db.or_(
                User.role == 'admin',
                User.role == 'china_partner'
            ),
            User.username == username
        ).first()
        
        if user and user.check_password(password) and user.active:
            # Check if user is admin or china_partner
            if user.is_admin or user.role == 'admin' or user.role == 'china_partner':
                login_user(user)
                user.last_login_at = datetime.utcnow()
                db.session.commit()
                next_page = request.args.get('next')
                return redirect(next_page or url_for('china_orders'))
        
        flash('Invalid credentials or account inactive', 'error')
    
    return render_template('china/login.html')

@app.route('/china/orders', methods=['GET', 'POST'])
@login_required
@china_partner_required
def china_orders():
    """China Partner orders page - shows only non-shipped orders (Pending tab removed)"""
    status = request.args.get('status', 'all')
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Redirect pending status to all (Pending tab removed)
    if status == 'pending':
        return redirect(url_for('china_orders', status='all', page=page))
    
    # Only show orders that are not yet shipped (exclude "Shipped" status)
    # This ensures submitted orders don't appear after page refresh
    orders = Order.query.filter(Order.status != "Shipped").order_by(Order.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template('china/orders.html', 
                         orders=orders,
                         status='all')

@app.route('/china/orders/submit', methods=['POST'])
@login_required
@china_partner_required
def china_submit_order_details_batch():
    """Submit shipment details for selected orders and mark them as shipped"""
    try:
        selected_ids = request.form.get('selected_ids', '')
        total_weight = request.form.get('total_weight')
        shipping_price = request.form.get('shipping_price')
        total_cost = request.form.get('total_cost')

        if not selected_ids:
            flash("Please select at least one order to mark for shipment / 请至少选择一个订单", "warning")
            return redirect('/china/orders?status=all')

        # Parse selected IDs
        ids = [int(i) for i in selected_ids.split(',') if i]
        
        # Validate inputs
        if not total_weight or not shipping_price or not total_cost:
            flash('All fields are required / 所有字段都是必填的', 'error')
            return redirect('/china/orders?status=all')
        
        # Convert to float
        try:
            weight = float(total_weight)
            shipping = float(shipping_price)
            total = float(total_cost)
        except ValueError:
            flash('Invalid number format / 数字格式无效', 'error')
            return redirect('/china/orders?status=all')
        
        # Get selected orders and update their status
        updated_orders = []
        for order_id in ids:
            order = Order.query.get(order_id)
            if order:
                order.status = "Shipped"
                # Also update shipping_status if the field exists
                if hasattr(order, 'shipping_status'):
                    order.shipping_status = 'shipped'
                updated_orders.append(order)
        
        if not updated_orders:
            flash('No valid orders selected / 没有选择有效的订单', 'warning')
            return redirect('/china/orders?status=all')
        
        # Create shipment record
        record = ShipmentRecord(
            weight_total=weight,
            shipping_price=shipping,
            total_cost=total,
            submitted_by=current_user.id,
            submission_date=datetime.utcnow(),
            order_ids=','.join(str(order_id) for order_id in ids)
        )
        db.session.add(record)
        db.session.commit()

        flash(f"✅ Shipment details submitted successfully for {len(ids)} order(s). 信息提交成功，已更新 {len(ids)} 个订单。", "success")
        return redirect('/china/orders?status=all')
    except Exception as e:
        db.session.rollback()
        app.logger.error(f'Error submitting shipment details: {str(e)}')
        import traceback
        app.logger.error(traceback.format_exc())
        flash(f'An error occurred / 发生错误: {str(e)}', 'error')
        return redirect('/china/orders?status=all')

@app.route('/china/submit_shipment', methods=['POST'])
@login_required
@china_partner_required
@csrf.exempt  # CSRF validation handled manually
def china_submit_shipment():
    """Submit shipment details for selected orders via JSON and mark them as shipped"""
    try:
        # Validate CSRF token
        csrf_token = request.headers.get('X-CSRFToken')
        if not csrf_token:
            return jsonify({
                'success': False,
                'message': 'CSRF token is missing / CSRF令牌缺失'
            }), 400
        
        # Get JSON data
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'message': 'Invalid request data / 无效的请求数据'
            }), 400
        
        order_ids = data.get('order_ids', [])
        weight = data.get('weight')
        shipping = data.get('shipping')
        total = data.get('total')
        
        # Validate inputs
        if not order_ids or len(order_ids) == 0:
            return jsonify({
                'success': False,
                'message': 'Please select at least one order / 请至少选择一个订单'
            }), 400
        
        if not weight or not shipping or not total:
            return jsonify({
                'success': False,
                'message': 'All fields are required / 所有字段都是必填的'
            }), 400
        
        # Convert to appropriate types
        try:
            order_ids = [int(oid) for oid in order_ids]
            weight = float(weight)
            shipping = float(shipping)
            total = float(total)
        except (ValueError, TypeError) as e:
            return jsonify({
                'success': False,
                'message': 'Invalid number format / 数字格式无效'
            }), 400
        
        # Get selected orders and update their status
        updated_orders = []
        for order_id in order_ids:
            order = Order.query.get(order_id)
            if order:
                order.status = "Shipped"
                # Also update shipping_status if the field exists
                if hasattr(order, 'shipping_status'):
                    order.shipping_status = 'shipped'
                # Mark details as submitted
                if hasattr(order, 'details_submitted'):
                    order.details_submitted = True
                if hasattr(order, 'submitted_by'):
                    order.submitted_by = current_user.id
                if hasattr(order, 'submitted_at'):
                    order.submitted_at = datetime.utcnow()
                updated_orders.append(order)
        
        if not updated_orders:
            return jsonify({
                'success': False,
                'message': 'No valid orders selected / 没有选择有效的订单'
            }), 400
        
        # Create shipment record
        record = ShipmentRecord(
            weight_total=weight,
            shipping_price=shipping,
            total_cost=total,
            submitted_by=current_user.id,
            submission_date=datetime.utcnow(),
            order_ids=','.join(str(order_id) for order_id in order_ids)
        )
        db.session.add(record)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Shipment details submitted successfully for {len(order_ids)} order(s). 信息提交成功，已更新 {len(order_ids)} 个订单。',
            'order_count': len(order_ids)
        })
    except Exception as e:
        db.session.rollback()
        app.logger.error(f'Error submitting shipment details: {str(e)}')
        import traceback
        app.logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'message': f'An error occurred / 发生错误: {str(e)}'
        }), 500

@app.route('/china/order/<int:order_id>/mark-shipped', methods=['POST'])
@login_required
@china_partner_required
def china_mark_shipped(order_id):
    """Mark order as shipped (China Partner)"""
    order = Order.query.get_or_404(order_id)
    
    if order.shipping_status != 'pending':
        flash('Order is not in pending status', 'error')
        return redirect(url_for('china_orders'))
    
    order.shipping_status = 'shipped'
    order.shipped_at = datetime.utcnow()
    order.assigned_to = current_user.id
    db.session.commit()
    
    flash('订单已标记为已发货', 'success')
    return redirect(url_for('china_orders'))

# Gambia Team Routes
@app.route('/gambia/login', methods=['GET', 'POST'])
def gambia_login():
    """Login page for Gambia Delivery Team and Admins"""
    # Allow admin or gambia_team users
    if current_user.is_authenticated and current_user.active:
        if current_user.is_admin or current_user.role == 'admin' or current_user.role == 'gambia_team':
            return redirect(url_for('gambia_orders'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        # Allow admin or gambia_team users
        user = User.query.filter(
            db.or_(
                User.role == 'admin',
                User.role == 'gambia_team'
            ),
            User.username == username
        ).first()
        
        if user and user.check_password(password) and user.active:
            # Check if user is admin or gambia_team
            if user.is_admin or user.role == 'admin' or user.role == 'gambia_team':
                login_user(user)
                user.last_login_at = datetime.utcnow()
                db.session.commit()
                next_page = request.args.get('next')
                return redirect(next_page or url_for('gambia_orders'))
        
        flash('Invalid credentials or account inactive', 'error')
    
    return render_template('gambia/login.html')

@app.route('/gambia/orders')
@login_required
@gambia_team_required
def gambia_orders():
    """Gambia Delivery Team orders page - shows shipped orders"""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Get all shipped orders (not yet delivered)
    orders = Order.query.filter_by(shipping_status='shipped').order_by(
        Order.shipped_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template('gambia/orders.html', orders=orders)

@app.route('/gambia/order/<int:order_id>/mark-delivered', methods=['POST'])
@login_required
@gambia_team_required
def gambia_mark_delivered(order_id):
    """Mark order as delivered (Gambia Team)"""
    order = Order.query.get_or_404(order_id)
    
    if order.shipping_status != 'shipped':
        flash('Order is not in shipped status', 'error')
        return redirect(url_for('gambia_orders'))
    
    order.shipping_status = 'delivered'
    order.delivered_at = datetime.utcnow()
    order.assigned_to = current_user.id
    db.session.commit()
    
    flash('Order marked as delivered', 'success')
    return redirect(url_for('gambia_orders'))

# User routes
@app.route('/profile')
@login_required
def profile():
    user = current_user
    profile = ensure_user_profile(user)

    orders_query = Order.query.filter_by(user_id=user.id)
    recent_orders = orders_query.order_by(Order.created_at.desc()).limit(5).all()
    total_orders = orders_query.count()
    delivered_orders = orders_query.filter(Order.status == 'delivered').count()
    open_orders = orders_query.filter(Order.status.in_(['Pending', 'Processing', 'Shipped'])).count()
    lifetime_spend = db.session.query(func.coalesce(func.sum(Order.total), 0)).filter(Order.user_id == user.id).scalar() or 0

    wishlist_preview = WishlistItem.query.filter_by(user_id=user.id)\
        .order_by(WishlistItem.created_at.desc())\
        .limit(6)\
        .all()

    notification_settings = {
        'email': profile.notify_email,
        'sms': profile.notify_sms,
        'push': profile.notify_push,
        'marketing': profile.marketing_opt_in
    }

    payment_methods = UserPaymentMethod.query.filter_by(user_id=user.id)\
        .order_by(UserPaymentMethod.is_default.desc(), UserPaymentMethod.created_at.desc())\
        .all()
    payment_methods_payload = [{
        'id': method.id,
        'provider': method.provider,
        'label': method.label or method.provider,
        'masked_identifier': method.masked_identifier(),
        'is_default': method.is_default
    } for method in payment_methods]

    return render_template(
        'profile.html',
        profile=profile,
        recent_orders=recent_orders,
        total_orders=total_orders,
        delivered_orders=delivered_orders,
        open_orders=open_orders,
        lifetime_spend=lifetime_spend,
        wishlist_preview=wishlist_preview,
        notification_settings=notification_settings,
        payment_methods=payment_methods,
        payment_methods_payload=payment_methods_payload,
        has_password=bool(user.password_hash),
        google_connected=bool(user.google_id or profile.google_avatar_url)
    )


@app.route('/api/profile', methods=['GET', 'PATCH', 'PUT'])
@login_required
def api_profile():
    user = current_user
    profile = ensure_user_profile(user)

    if request.method == 'GET':
        return jsonify({'status': 'success', 'profile': user.to_profile_dict()})

    payload = request.get_json(silent=True) or request.form.to_dict()
    if not payload:
        return jsonify({'status': 'error', 'message': 'No data received.'}), 400

    errors = {}

    new_username = payload.get('username')
    if new_username and new_username != user.username:
        candidate = new_username.strip()
        if len(candidate) < 3:
            errors['username'] = 'Username must be at least 3 characters long.'
        elif not re.match(r'^[a-zA-Z0-9_.-]+$', candidate):
            errors['username'] = 'Username may only contain letters, numbers, dots, underscores, or hyphens.'
        elif not is_username_available(candidate, exclude_user_id=user.id):
            errors['username'] = 'This username is already taken.'
        else:
            user.username = candidate

    new_email = payload.get('email')
    if new_email and new_email != user.email:
        candidate = new_email.strip()
        try:
            valid = validate_email(candidate, check_deliverability=False)
            candidate = valid.email
        except EmailNotValidError as exc:
            errors['email'] = str(exc)
        else:
            email_exists = User.query.filter(User.email == candidate, User.id != user.id).first()
            if email_exists:
                errors['email'] = 'This email address is already in use.'
            else:
                user.email = candidate

    phone_number = payload.get('phone_number')
    if phone_number is not None:
        try:
            normalized_phone = normalize_phone_number(phone_number)
            profile.phone_number = normalized_phone
        except ValueError as exc:
            errors['phone_number'] = str(exc)

    for field in ['first_name', 'last_name', 'address', 'city', 'state', 'postal_code', 'country']:
        if field in payload:
            value = payload.get(field)
            profile.__setattr__(field, value.strip() if isinstance(value, str) else value)

    for pref_field in ['notify_email', 'notify_sms', 'notify_push', 'marketing_opt_in']:
        if pref_field in payload:
            profile.__setattr__(pref_field, str(payload.get(pref_field)).lower() in ['true', '1', 'yes', 'on'])

    if errors:
        db.session.rollback()
        return jsonify({'status': 'error', 'errors': errors}), 400

    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        current_app.logger.error(f"Profile update integrity error: {exc}")
        return jsonify({'status': 'error', 'message': 'Unable to update profile due to conflicting data.'}), 409

    return jsonify({'status': 'success', 'profile': user.to_profile_dict()})


@app.route('/api/profile/avatar', methods=['POST', 'DELETE'])
@login_required
def api_profile_avatar():
    if request.method == 'DELETE':
        profile = ensure_user_profile(current_user)
        old_filename = profile.avatar_filename
        profile.avatar_filename = None
        profile.avatar_updated_at = None
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error(f"Error removing profile image: {exc}")
            return jsonify({'success': False, 'status': 'error', 'message': 'Unable to remove profile picture.'}), 500
        delete_profile_image(old_filename)
        avatar_url = current_user.get_avatar_url(cache_bust=True)
        return jsonify({'success': True, 'status': 'success', 'avatar_url': avatar_url, 'image_url': avatar_url})

    file = request.files.get('avatar')
    if not file:
        return jsonify({'success': False, 'status': 'error', 'message': 'Please select an image to upload.'}), 400

    try:
        image = ensure_allowed_image(file)
    except ValueError as exc:
        return jsonify({'success': False, 'status': 'error', 'message': str(exc)}), 400

    max_dim = 512
    width, height = image.size
    if max(width, height) > max_dim:
        image.thumbnail((max_dim, max_dim), RESAMPLING_LANCZOS)
    extension = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'png'

    profile = ensure_user_profile(current_user)
    old_filename = profile.avatar_filename

    try:
        filename = save_profile_image(image, extension=extension)
        profile.avatar_filename = filename
        profile.avatar_updated_at = datetime.utcnow()
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(f"Error saving profile image: {exc}")
        return jsonify({'success': False, 'status': 'error', 'message': 'We could not save your profile picture. Please try again.'}), 500

    delete_profile_image(old_filename)
    avatar_url = current_user.get_avatar_url(cache_bust=True)
    return jsonify({'success': True, 'status': 'success', 'avatar_url': avatar_url, 'image_url': avatar_url})


@app.route('/profile/upload', methods=['POST', 'DELETE'])
@login_required
def profile_upload():
    return api_profile_avatar()


@app.route('/api/profile/check-username')
@login_required
def api_check_username():
    username = request.args.get('username', '').strip()
    if not username:
        return jsonify({'status': 'error', 'message': 'Username is required.'}), 400
    if username == current_user.username:
        return jsonify({'status': 'success', 'available': True})
    available = is_username_available(username, exclude_user_id=current_user.id)
    return jsonify({'status': 'success', 'available': available})


@app.route('/api/profile/password', methods=['POST'])
@login_required
def api_update_password():
    data = request.get_json(silent=True) or request.form.to_dict()
    if not data:
        return jsonify({'status': 'error', 'message': 'No data received.'}), 400

    current_password = data.get('current_password')
    new_password = data.get('new_password')
    confirm_password = data.get('confirm_password')

    errors = {}

    if not new_password or len(new_password) < 8:
        errors['new_password'] = 'New password must be at least 8 characters long.'

    if new_password != confirm_password:
        errors['confirm_password'] = 'Password confirmation does not match.'

    if current_user.password_hash:
        if not current_password:
            errors['current_password'] = 'Current password is required.'
        elif not current_user.check_password(current_password):
            errors['current_password'] = 'Your current password is incorrect.'

    if errors:
        return jsonify({'status': 'error', 'errors': errors}), 400

    current_user.set_password(new_password)
    db.session.commit()

    return jsonify({'status': 'success', 'message': 'Password updated successfully.'})


@app.route('/api/profile/payment-methods', methods=['GET', 'POST'])
@login_required
def api_payment_methods():
    if request.method == 'GET':
        methods = [{
            'id': method.id,
            'provider': method.provider,
            'label': method.label or method.provider,
            'masked_identifier': method.masked_identifier(),
            'is_default': method.is_default,
            'created_at': method.created_at.isoformat()
        } for method in current_user.payment_methods]
        return jsonify({'status': 'success', 'methods': methods})

    data = request.get_json(silent=True) or request.form.to_dict()
    if not data:
        return jsonify({'status': 'error', 'message': 'No data received.'}), 400

    provider = (data.get('provider') or '').strip()
    label = (data.get('label') or '').strip()
    account_identifier = (data.get('account_identifier') or '').strip()
    is_default = str(data.get('is_default', '')).lower() in ['true', '1', 'yes', 'on']

    errors = {}

    if not provider:
        errors['provider'] = 'Provider is required.'
    elif provider not in SUPPORTED_PAYMENT_PROVIDERS:
        errors['provider'] = f'Provider must be one of: {", ".join(SUPPORTED_PAYMENT_PROVIDERS)}'

    if not account_identifier:
        errors['account_identifier'] = 'Account number is required.'
    else:
        normalized_account = re.sub(r'\D', '', account_identifier)
        if len(normalized_account) < 6:
            errors['account_identifier'] = 'Account number must contain at least 6 digits.'
        else:
            account_identifier = normalized_account

    if errors:
        return jsonify({'status': 'error', 'errors': errors}), 400

    last4 = account_identifier[-4:]
    method = UserPaymentMethod(
        user_id=current_user.id,
        provider=provider,
        label=label or f"{provider} ending {last4}",
        account_identifier=account_identifier,
        account_last4=last4,
        is_default=is_default
    )
    if is_default:
        for existing in current_user.payment_methods:
            existing.is_default = False

    db.session.add(method)
    db.session.commit()

    return jsonify({
        'status': 'success',
        'method': {
            'id': method.id,
            'provider': method.provider,
            'label': method.label,
            'masked_identifier': method.masked_identifier(),
            'is_default': method.is_default
        }
    }), 201


@app.route('/api/profile/payment-methods/<int:method_id>', methods=['DELETE'])
@login_required
def api_delete_payment_method(method_id):
    method = UserPaymentMethod.query.filter_by(id=method_id, user_id=current_user.id).first()
    if not method:
        return jsonify({'status': 'error', 'message': 'Payment method not found.'}), 404
    db.session.delete(method)
    db.session.commit()
    return jsonify({'status': 'success'})

@app.route('/orders')
@login_required
def orders():
    # Get all user's orders with eager loading of items and products
    orders = Order.query.filter_by(user_id=current_user.id)\
        .options(joinedload(Order.items).joinedload(OrderItem.product))\
        .order_by(Order.created_at.desc()).all()
    return render_template('orders.html', orders=orders)

# Main routes
@app.route('/products')
def all_products():
    # Get all products, ordered by newest first
    search_query = request.args.get('q', '')
    category_id = request.args.get('category', type=int)
    
    query = Product.query
    
    # Apply search filter if query exists
    if search_query:
        search = f"%{search_query}%"
        query = query.filter(
            (Product.name.ilike(search)) | 
            (Product.description.ilike(search))
        )
    
    # Apply category filter if selected
    if category_id:
        query = query.filter_by(category_id=category_id)
    
    # Get all categories for the sidebar
    categories = Category.query.order_by('name').all()
    
    # Get filtered products
    products = query.order_by(Product.created_at.desc()).all()
    
    return render_template('products.html', 
                         products=products,
                         categories=categories,
                         current_category=category_id,
                         search_query=search_query)

@app.route('/favicon.ico')
def favicon():
    """Serve favicon.ico to prevent 404 errors"""
    favicon_path = os.path.join(app.static_folder, 'images', 'favicon.ico')
    if os.path.exists(favicon_path):
        return send_file(favicon_path, mimetype='image/vnd.microsoft.icon'), 200, {'Cache-Control': 'public, max-age=31536000'}
    # Return 204 No Content if favicon doesn't exist (standard way to handle missing favicons)
    return '', 204

@app.route('/service-worker.js')
def service_worker():
    """Serve service worker from root for proper scope"""
    sw_path = os.path.join(app.static_folder, 'service-worker.js')
    if os.path.exists(sw_path):
        return send_file(sw_path, mimetype='application/javascript'), 200, {
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Service-Worker-Allowed': '/'
        }
    return '', 404

@app.route('/')
def home():
    search_query = request.args.get('q', '')
    
    # Get all categories with product counts
    categories_query = db.session.query(
        Category,
        db.func.count(Product.id).label('product_count')
    ).outerjoin(Product, Category.id == Product.category_id)
    categories = categories_query.group_by(Category.id).all()
    
    # Format categories with counts
    categories_with_counts = [{
        'id': category.id,
        'name': category.name,
        'count': int(product_count) if product_count is not None else 0,
        'icon': getattr(category, 'icon', 'box'),
        'gradient': getattr(category, 'gradient', 'from-gray-500 to-gray-600'),
        'image': category.image
    } for category, product_count in categories]
    
    # If there's a search query, filter products by name or description
    if search_query:
        search = f"%{search_query}%"
        featured_products = Product.query.filter(
            Product.stock > 0,
            (Product.name.ilike(search)) | (Product.description.ilike(search))
        ).order_by(Product.created_at.desc()).limit(20).all()
    else:
        # Only show products that are in stock and limit to 8
        featured_products = Product.query.filter(Product.stock > 0).order_by(Product.created_at.desc()).limit(8).all()
    
    return render_template('index.html', 
                         categories_with_counts=categories_with_counts, 
                         featured_products=featured_products, 
                         search_query=search_query)

@app.route('/category/<int:category_id>')
def category(category_id):
    category = Category.query.get_or_404(category_id)
    # Only show products that are in stock
    products = Product.query.filter(
        Product.category_id == category_id,
        Product.stock > 0
    ).all()
    return render_template('category.html', 
                         category=category, 
                         products=products)

@app.route('/product/<int:product_id>')
def product(product_id):
    product = Product.query.get_or_404(product_id)
    
    # Calculate shipping fee based on product-specific delivery rules
    shipping_fee = calculate_delivery_price(product.price, product_id=product.id)
    final_price = product.price + shipping_fee
    
    # Set delivery_price to 0.00 if not set (for backward compatibility)
    if product.delivery_price is None:
        product.delivery_price = 0.0
    
    category = Category.query.get(product.category_id)
    related_products = Product.query.filter(
        Product.category_id == product.category_id,
        Product.id != product.id
    ).limit(4).all()
    return render_template('product.html', 
                         product=product, 
                         category=category,
                         related_products=related_products,
                         shipping_fee=shipping_fee,
                         final_price=final_price)

@app.route('/test/users')
@login_required
@admin_required
def test_users():
    # Test database connection and user retrieval
    try:
        users = User.query.order_by(User.created_at.desc()).all()
        user_data = [{
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'is_admin': user.is_admin,
            'created_at': user.created_at.isoformat()
        } for user in users]
        
        return jsonify({
            'success': True,
            'user_count': len(users),
            'users': user_data
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'database_url': app.config.get('SQLALCHEMY_DATABASE_URI')
        }), 500

@app.route('/test/users-page')
def test_users_page():
    # Create test users data
    test_users = [
        {'id': 1, 'username': 'testuser1', 'email': 'test1@example.com', 'is_admin': False, 'created_at': datetime.utcnow()},
        {'id': 2, 'username': 'admin', 'email': 'admin@example.com', 'is_admin': True, 'created_at': datetime.utcnow()}
    ]
    return render_template('admin/admin/users.html', users=test_users)

# ======================
# Product Notifications
# ======================

@app.route('/request-restock-notification/<int:product_id>', methods=['POST'])
@login_required
def request_restock_notification(product_id):
    """Handle restock notification requests"""
    product = Product.query.get_or_404(product_id)
    
    # This is a placeholder for future notification system
    flash('Product notification feature is currently not available.', 'info')
    
    return redirect(url_for('product', product_id=product_id))

@app.route('/submit-feedback/<int:order_id>', methods=['GET', 'POST'])
@login_required
def submit_feedback(order_id):
    """Handle customer feedback submission"""
    order = Order.query.get_or_404(order_id)
    
    # Verify the order belongs to the current user
    if order.user_id != current_user.id:
        abort(403)
    
    # Check if feedback already submitted
    if order.feedback:
        flash('You have already submitted feedback for this order.', 'info')
        return redirect(url_for('orders'))
    
    if request.method == 'POST':
        rating = request.form.get('rating', type=int)
        comment = request.form.get('comment', '').strip()
        
        if not rating or rating < 1 or rating > 5:
            flash('Please provide a valid rating between 1 and 5 stars.', 'danger')
            return redirect(url_for('submit_feedback', order_id=order_id))
        
        # Handle file upload if any
        image_path = None
        if 'image' in request.files:
            file = request.files['image']
            if file and allowed_file(file.filename, {'jpg', 'jpeg', 'png'}):
                filename = secure_filename(f"feedback_{order_id}_{int(datetime.utcnow().timestamp())}.{file.filename.rsplit('.', 1)[1].lower()}")
                filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], 'feedback', filename)
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                file.save(filepath)
                image_path = os.path.join('feedback', filename)
        
        # Save feedback
        feedback = CustomerFeedback(
            order_id=order_id,
            user_id=current_user.id,
            rating=rating,
            comment=comment,
            image_path=image_path,
            is_published=True  # Auto-publish for now, admin can moderate later
        )
        
        db.session.add(feedback)
        db.session.commit()
        
        flash('Thank you for your feedback!', 'success')
        return redirect(url_for('orders'))
    
    return render_template('submit_feedback.html', order=order)

# ======================
# Utility Functions
# ======================

def datetimeformat(value, format='%Y-%m-%d %H:%M'):
    if value is None:
        return ''
    return value.strftime(format)

def number_format(value, format='{:,.0f}'):
    """Format a number with thousand separators"""
    if value is None:
        return '0'
    try:
        return format.format(float(value))
    except (ValueError, TypeError):
        return str(value)

# Add filters to Jinja2 environment
app.jinja_env.filters['datetimeformat'] = datetimeformat
app.jinja_env.filters['number_format'] = number_format

# ======================
# Database Manager
# ======================

def log_database_action(action, table_name=None, row_id=None, details=None):
    """Helper function to log database operations"""
    try:
        if current_user.is_authenticated:
            log = DatabaseLog(
                user_id=current_user.id,
                action=action,
                table_name=table_name,
                row_id=str(row_id) if row_id else None,
                details=details
            )
            db.session.add(log)
            db.session.commit()
    except Exception as e:
        current_app.logger.error(f"Error logging database action: {str(e)}")

# ======================
# Backup Automation
# ======================

BACKUP_JOB_ID = 'daily_database_backup'
_backup_scheduler = None
_backup_scheduler_lock = threading.Lock()
_backup_job_lock = threading.Lock()
_backup_shutdown_registered = False


def get_or_create_app_settings():
    """Ensure an AppSettings row exists."""
    settings = AppSettings.query.first()
    if not settings:
        settings = AppSettings()
        db.session.add(settings)
        db.session.commit()
    return settings


def ensure_backup_defaults(settings: AppSettings, auto_commit: bool = False) -> AppSettings:
    """Populate backup defaults if missing."""
    updated = False
    if settings.backup_time is None or not settings.backup_time.strip():
        settings.backup_time = '02:00'
        updated = True
    if settings.backup_retention_days is None:
        settings.backup_retention_days = 30
        updated = True
    if not settings.backup_email:
        fallback = settings.email_receiver or settings.resend_default_recipient or settings.resend_from_email or settings.contact_email or os.getenv('RESEND_DEFAULT_RECIPIENT', '')
        settings.backup_email = fallback
        updated = True
    if updated and auto_commit:
        db.session.commit()
    return settings


def parse_backup_time(value: Optional[str]) -> Tuple[int, int]:
    """Parse HH:MM string into hour/minute integers."""
    try:
        if not value:
            return 2, 0
        parts = value.split(':')
        hour = max(0, min(23, int(parts[0])))
        minute = max(0, min(59, int(parts[1]) if len(parts) > 1 else 0))
        return hour, minute
    except (ValueError, TypeError):
        return 2, 0


def sanitize_backup_time(raw_value: str) -> str:
    """Ensure time string is HH:MM."""
    hour, minute = parse_backup_time(raw_value)
    return f"{hour:02d}:{minute:02d}"


def get_backup_directory():
    """Ensure backups directory exists outside the static path."""
    directory = current_app.config.get('BACKUP_DIRECTORY')
    if not directory:
        app_root = os.path.dirname(current_app.root_path)
        directory = os.path.abspath(os.path.join(app_root, 'backups'))
        current_app.config['BACKUP_DIRECTORY'] = directory
    os.makedirs(directory, exist_ok=True)
    return directory


def cleanup_old_backups(directory: str, retention_days: int):
    """Delete backup files older than retention period."""
    try:
        cutoff = datetime.utcnow() - timedelta(days=max(retention_days or 30, 1))
        for filename in os.listdir(directory):
            if not filename.startswith('backup_') or not filename.endswith('.sql'):
                continue
            filepath = os.path.join(directory, filename)
            if not os.path.isfile(filepath):
                continue
            file_time = datetime.utcfromtimestamp(os.path.getmtime(filepath))
            if file_time < cutoff:
                os.remove(filepath)
    except Exception as exc:
        current_app.logger.warning(f"Could not cleanup old backups: {exc}")


def _send_backup_success_email(recipient: Optional[str], backup_path: str, timestamp: datetime):
    if not recipient:
        current_app.logger.warning("Backup completed but no recipient configured; skipping email.")
        return

    from app.utils.email_queue import queue_single_email
    app_obj = current_app._get_current_object()

    subject = _format_email_subject(f"Daily Database Backup – {timestamp.strftime('%Y/%m/%d')}")
    html_body = (
        "Your automated daily database backup is ready.<br>"
        "Attached file path (server side): {path}".format(path=backup_path)
    )
    current_app.logger.info(
        "backup_success_email: queueing backup success email",
        extra={"recipient": recipient},
    )
    queue_single_email(app_obj, recipient, subject, html_body)


def _send_backup_failure_email(recipient: Optional[str], error_message: str, timestamp: datetime):
    if not recipient:
        current_app.logger.warning("Backup failed but no recipient configured for warning email.")
        return

    from app.utils.email_queue import queue_single_email
    app_obj = current_app._get_current_object()

    subject = _format_email_subject("⚠️ Daily Backup Failed – Action Required")
    html_body = (
        "Automated backup failed at {ts}.<br><br>Error: {err}<br>"
        "Please review the server logs and retry the backup manually.".format(
            ts=timestamp.strftime('%Y-%m-%d %H:%M UTC'), err=error_message
        )
    )
    current_app.logger.info(
        "backup_failure_email: queueing backup failure email",
        extra={"recipient": recipient},
    )
    queue_single_email(app_obj, recipient, subject, html_body)


def run_database_backup(trigger: str = 'manual', email_override: Optional[str] = None) -> Dict[str, str]:
    """Create DB + SQL backups, email them, and log the result."""
    if not _backup_job_lock.acquire(blocking=False):
        return {'success': False, 'message': 'Backup already running'}
    backup_path = None
    recipient = None
    try:
        with app.app_context():
            settings = ensure_backup_defaults(get_or_create_app_settings(), auto_commit=True)
            timestamp = datetime.utcnow()
            backup_path = str(dump_database_to_file(prefix=trigger or 'backup'))
            backup_dir = os.path.dirname(backup_path)
            cleanup_old_backups(backup_dir, settings.backup_retention_days or 30)

            recipient = email_override or settings.backup_email or settings.email_receiver or settings.resend_default_recipient or settings.resend_from_email
            try:
                _send_backup_success_email(recipient, backup_path, timestamp)
            except Exception as email_exc:
                current_app.logger.error(f"Backup email failed: {email_exc}")
                raise

            settings.backup_last_run = timestamp
            settings.backup_last_status = 'success'
            settings.backup_last_message = None

            log_entry = DatabaseBackupLog(
                status='success',
                file_paths=json.dumps({'sql': backup_path}),
                trigger=trigger,
                email_recipient=recipient
            )
            db.session.add(log_entry)
            db.session.commit()

            current_app.logger.info(f"Database backup completed ({backup_path})")
            return {
                'success': True,
                'message': 'Backup completed and emailed successfully.',
                'sql_backup': backup_path
            }
    except Exception as exc:
        with app.app_context():
            db.session.rollback()
            try:
                settings = get_or_create_app_settings()
                failure_time = datetime.utcnow()
                settings.backup_last_run = failure_time
                settings.backup_last_status = 'fail'
                settings.backup_last_message = str(exc)
                failure_recipient = recipient or email_override or settings.backup_email or settings.email_receiver or settings.resend_default_recipient or settings.resend_from_email
                file_map = {}
                if backup_path:
                    file_map['sql'] = backup_path
                log_entry = DatabaseBackupLog(
                    status='fail',
                    file_paths=json.dumps(file_map) if file_map else None,
                    error_message=str(exc),
                    trigger=trigger,
                    email_recipient=failure_recipient
                )
                db.session.add(log_entry)
                db.session.commit()
                _send_backup_failure_email(failure_recipient, str(exc), failure_time)
            except Exception as log_exc:
                db.session.rollback()
                current_app.logger.error(f"Failed to record backup failure: {log_exc}")
            current_app.logger.error(f"Database backup failed: {exc}", exc_info=True)
            return {'success': False, 'message': str(exc)}
    finally:
        _backup_job_lock.release()


def _scheduled_backup_job():
    """Wrapper for APScheduler job."""
    with app.app_context():
        result = run_database_backup(trigger='auto')
        if not result.get('success'):
            current_app.logger.warning(f"Scheduled backup failed: {result.get('message')}")


def schedule_backup_job():
    """Add or remove the APScheduler job based on settings."""
    global _backup_scheduler, _backup_shutdown_registered
    with _backup_scheduler_lock:
        if not _backup_scheduler:
            timezone = app.config.get('BACKUP_TIMEZONE') or os.getenv('BACKUP_TIMEZONE') or 'UTC'
            _backup_scheduler = BackgroundScheduler(timezone=timezone)
            _backup_scheduler.start()
            app.config['BACKUP_SCHEDULER_STARTED'] = True
            if not _backup_shutdown_registered:
                atexit.register(shutdown_backup_scheduler)
                _backup_shutdown_registered = True

        job = _backup_scheduler.get_job(BACKUP_JOB_ID)
        with app.app_context():
            settings = ensure_backup_defaults(get_or_create_app_settings(), auto_commit=True)
            if not settings.backup_enabled:
                if job:
                    _backup_scheduler.remove_job(BACKUP_JOB_ID)
                    app.logger.info("Daily backup disabled; scheduler job removed.")
                return
            hour, minute = parse_backup_time(settings.backup_time)
        trigger = CronTrigger(hour=hour, minute=minute, timezone=_backup_scheduler.timezone)
        _backup_scheduler.add_job(
            _scheduled_backup_job,
            trigger=trigger,
            id=BACKUP_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600
        )
        app.logger.info(f"Daily backup scheduled at {hour:02d}:{minute:02d} ({_backup_scheduler.timezone})")


def shutdown_backup_scheduler():
    global _backup_scheduler
    if _backup_scheduler and _backup_scheduler.running:
        _backup_scheduler.shutdown(wait=False)
        _backup_scheduler = None


def initialize_backup_scheduler():
    """Start scheduler once per process."""
    if app.config.get('TESTING'):
        return
    if app.config.get('BACKUP_SCHEDULER_STARTED'):
        return
    if app.config.get('DEBUG') and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        # Avoid double-starting in debug reloader
        return
    schedule_backup_job()
    app.config['BACKUP_SCHEDULER_STARTED'] = True

def get_table_info(table_name):
    """Get information about a table including columns and row count"""
    inspector = inspect(db.engine)
    columns = inspector.get_columns(table_name)
    pk_constraint = inspector.get_pk_constraint(table_name)
    primary_keys = pk_constraint.get('constrained_columns', [])
    
    # Get row count
    with db.engine.connect() as conn:
        result = conn.execute(text(f'SELECT COUNT(*) FROM "{table_name}"'))
        row_count = result.scalar()
    
    return {
        'columns': columns,
        'primary_keys': primary_keys,
        'row_count': row_count
    }

@app.route('/admin/database', methods=['GET'])
@login_required
@admin_required
def admin_database_manager():
    """Main database manager page"""
    try:
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        
        # Get info for each table
        tables_info = []
        for table in tables:
            try:
                info = get_table_info(table)
                tables_info.append({
                    'name': table,
                    'row_count': info['row_count'],
                    'columns': info['columns'],
                    'primary_keys': info['primary_keys']
                })
            except Exception as e:
                current_app.logger.error(f"Error getting info for table {table}: {str(e)}")
                tables_info.append({
                    'name': table,
                    'row_count': 0,
                    'columns': [],
                    'primary_keys': [],
                    'error': str(e)
                })
        
        return render_template('admin/admin/database_manager.html', tables=tables_info)
    except Exception as e:
        current_app.logger.error(f"Error in database manager: {str(e)}")
        flash(f'Error loading database manager: {str(e)}', 'error')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/database/table/<table_name>', methods=['GET'])
@login_required
@admin_required
def admin_database_table_view(table_name):
    """View table data with pagination, search, and sorting"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        search = request.args.get('search', '')
        sort_column = request.args.get('sort', '')
        sort_order = request.args.get('order', 'asc')
        
        # Validate table name
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        if table_name not in tables:
            flash('Table not found', 'error')
            return redirect(url_for('admin_database_manager'))
        
        table_info = get_table_info(table_name)
        columns = [col['name'] for col in table_info['columns']]
        primary_keys = table_info['primary_keys']
        
        # Build query
        query = f'SELECT * FROM "{table_name}"'
        conditions = []
        params = {}
        
        if search:
            search_conditions = []
            for i, col in enumerate(columns):
                param_name = f'search_{i}'
                search_conditions.append(f'CAST("{col}" AS TEXT) LIKE :{param_name}')
                params[param_name] = f'%{search}%'
            if search_conditions:
                conditions.append(f"({' OR '.join(search_conditions)})")
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        if sort_column and sort_column in columns:
            query += f' ORDER BY "{sort_column}" {sort_order.upper()}'
        elif primary_keys:
            query += f' ORDER BY "{primary_keys[0]}" ASC'
        
        # Get total count
        count_query = f'SELECT COUNT(*) FROM "{table_name}"'
        if conditions:
            count_query += " WHERE " + " AND ".join(conditions)
        
        with db.engine.connect() as conn:
            total_result = conn.execute(text(count_query), params)
            total = total_result.scalar()
            
            # Get paginated data
            offset = (page - 1) * per_page
            query += f" LIMIT {per_page} OFFSET {offset}"
            result = conn.execute(text(query), params)
            rows = result.fetchall()
            
            # Convert rows to dictionaries
            data = []
            for row in rows:
                row_dict = {}
                for i, col in enumerate(columns):
                    value = row[i]
                    if isinstance(value, datetime):
                        value = value.strftime('%Y-%m-%d %H:%M:%S')
                    elif value is None:
                        value = 'NULL'
                    row_dict[col] = value
                data.append(row_dict)
        
        log_database_action('READ', table_name)
        
        return render_template('admin/admin/database_table_view.html',
                             table_name=table_name,
                             columns=columns,
                             data=data,
                             primary_keys=primary_keys,
                             page=page,
                             per_page=per_page,
                             total=total,
                             search=search,
                             sort_column=sort_column,
                             sort_order=sort_order,
                             table_info=table_info)
    except Exception as e:
        current_app.logger.error(f"Error viewing table {table_name}: {str(e)}")
        flash(f'Error viewing table: {str(e)}', 'error')
        return redirect(url_for('admin_database_manager'))

@app.route('/admin/database/table/<table_name>/add', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_database_add_row(table_name):
    """Add a new row to a table"""
    try:
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        if table_name not in tables:
            flash('Table not found', 'error')
            return redirect(url_for('admin_database_manager'))
        
        table_info = get_table_info(table_name)
        columns = table_info['columns']
        
        if request.method == 'POST':
            try:
                # Build INSERT query
                column_names = []
                values = []
                params = {}
                
                for col in columns:
                    col_name = col['name']
                    # Skip auto-increment primary keys
                    if col.get('autoincrement', False):
                        continue
                    
                    value = request.form.get(col_name)
                    if value is not None and value != '':
                        column_names.append(f'"{col_name}"')
                        values.append(f":{col_name}")
                        # Convert value based on type
                        col_type = str(col['type']).upper()
                        if 'INTEGER' in col_type or 'INT' in col_type:
                            params[col_name] = int(value) if value else None
                        elif 'REAL' in col_type or 'FLOAT' in col_type or 'DOUBLE' in col_type:
                            params[col_name] = float(value) if value else None
                        elif 'BOOLEAN' in col_type or 'BOOL' in col_type:
                            params[col_name] = bool(value) if value else None
                        else:
                            params[col_name] = value
                    elif col.get('nullable', True) is False and not col.get('autoincrement', False):
                        # Required field
                        if col.get('default') is None:
                            flash(f'Field {col_name} is required', 'error')
                            return render_template('admin/admin/database_add_row.html',
                                                 table_name=table_name,
                                                 columns=columns)
                
                if not column_names:
                    flash('No data to insert', 'error')
                    return render_template('admin/admin/database_add_row.html',
                                         table_name=table_name,
                                         columns=columns)
                
                query = f'INSERT INTO "{table_name}" ({", ".join(column_names)}) VALUES ({", ".join(values)})'
                
                with db.engine.connect() as conn:
                    result = conn.execute(text(query), params)
                    conn.commit()
                    row_id = result.lastrowid
                
                log_database_action('CREATE', table_name, row_id, f"Added new row")
                flash('Row added successfully', 'success')
                return redirect(url_for('admin_database_table_view', table_name=table_name))
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Error adding row: {str(e)}")
                flash(f'Error adding row: {str(e)}', 'error')
                return render_template('admin/admin/database_add_row.html',
                                     table_name=table_name,
                                     columns=columns)
        
        return render_template('admin/admin/database_add_row.html',
                             table_name=table_name,
                             columns=columns)
    except Exception as e:
        current_app.logger.error(f"Error in add row: {str(e)}")
        flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('admin_database_manager'))

@app.route('/admin/database/table/<table_name>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_database_edit_row(table_name):
    """Edit an existing row"""
    try:
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        if table_name not in tables:
            flash('Table not found', 'error')
            return redirect(url_for('admin_database_manager'))
        
        table_info = get_table_info(table_name)
        columns = table_info['columns']
        primary_keys = table_info['primary_keys']
        
        if not primary_keys:
            flash('Table has no primary key', 'error')
            return redirect(url_for('admin_database_table_view', table_name=table_name))
        
        # Get row identifier from query params
        row_id = request.args.get('id')
        if not row_id:
            flash('Row ID required', 'error')
            return redirect(url_for('admin_database_table_view', table_name=table_name))
        
        # Build WHERE clause
        pk_col = primary_keys[0]
        where_clause = f'"{pk_col}" = :pk_value'
        params = {'pk_value': row_id}
        
        if request.method == 'POST':
            try:
                # Build UPDATE query
                updates = []
                update_params = {'pk_value': row_id}
                
                for col in columns:
                    col_name = col['name']
                    # Skip primary keys and auto-increment columns
                    if col_name in primary_keys or col.get('autoincrement', False):
                        continue
                    
                    value = request.form.get(col_name)
                    updates.append(f'"{col_name}" = :{col_name}')
                    # Convert value based on type
                    col_type = str(col['type']).upper()
                    if value == '':
                        if col.get('nullable', True):
                            update_params[col_name] = None
                        else:
                            flash(f'Field {col_name} cannot be null', 'error')
                            # Re-fetch row data
                            with db.engine.connect() as conn:
                                result = conn.execute(text(f'SELECT * FROM "{table_name}" WHERE {where_clause}'), params)
                                row = result.fetchone()
                                if row:
                                    row_data = {}
                                    for i, col in enumerate(columns):
                                        row_data[col['name']] = row[i]
                                    return render_template('admin/admin/database_edit_row.html',
                                                         table_name=table_name,
                                                         columns=columns,
                                                         row_data=row_data,
                                                         primary_keys=primary_keys,
                                                         row_id=row_id)
                            return redirect(url_for('admin_database_table_view', table_name=table_name))
                    elif 'INTEGER' in col_type or 'INT' in col_type:
                        update_params[col_name] = int(value) if value else None
                    elif 'REAL' in col_type or 'FLOAT' in col_type or 'DOUBLE' in col_type:
                        update_params[col_name] = float(value) if value else None
                    elif 'BOOLEAN' in col_type or 'BOOL' in col_type:
                        update_params[col_name] = bool(value) if value else None
                    else:
                        update_params[col_name] = value
                
                query = f'UPDATE "{table_name}" SET {", ".join(updates)} WHERE {where_clause}'
                
                with db.engine.connect() as conn:
                    conn.execute(text(query), update_params)
                    conn.commit()
                
                log_database_action('UPDATE', table_name, row_id, f"Updated row")
                flash('Row updated successfully', 'success')
                return redirect(url_for('admin_database_table_view', table_name=table_name))
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Error updating row: {str(e)}")
                flash(f'Error updating row: {str(e)}', 'error')
                return redirect(url_for('admin_database_table_view', table_name=table_name))
        
        # GET request - fetch row data
        with db.engine.connect() as conn:
            result = conn.execute(text(f'SELECT * FROM "{table_name}" WHERE {where_clause}'), params)
            row = result.fetchone()
            if not row:
                flash('Row not found', 'error')
                return redirect(url_for('admin_database_table_view', table_name=table_name))
            
            row_data = {}
            for i, col in enumerate(columns):
                value = row[i]
                if isinstance(value, datetime):
                    value = value.strftime('%Y-%m-%d %H:%M:%S')
                elif value is None:
                    value = ''
                row_data[col['name']] = value
        
        return render_template('admin/admin/database_edit_row.html',
                             table_name=table_name,
                             columns=columns,
                             row_data=row_data,
                             primary_keys=primary_keys,
                             row_id=row_id)
    except Exception as e:
        current_app.logger.error(f"Error in edit row: {str(e)}")
        flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('admin_database_manager'))

@app.route('/admin/database/table/<table_name>/delete', methods=['POST'])
@login_required
@admin_required
def admin_database_delete_row(table_name):
    """Delete a row from a table"""
    try:
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        if table_name not in tables:
            return jsonify({'success': False, 'message': 'Table not found'}), 404
        
        table_info = get_table_info(table_name)
        primary_keys = table_info['primary_keys']
        
        if not primary_keys:
            return jsonify({'success': False, 'message': 'Table has no primary key'}), 400
        
        row_id = request.form.get('id') or request.json.get('id') if request.is_json else None
        if not row_id:
            return jsonify({'success': False, 'message': 'Row ID required'}), 400
        
        pk_col = primary_keys[0]
        query = f'DELETE FROM "{table_name}" WHERE "{pk_col}" = :pk_value'
        params = {'pk_value': row_id}
        
        with db.engine.connect() as conn:
            conn.execute(text(query), params)
            conn.commit()
        
        log_database_action('DELETE', table_name, row_id, f"Deleted row")
        return jsonify({'success': True, 'message': 'Row deleted successfully'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting row: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/admin/database/table/<table_name>/delete-multiple', methods=['POST'])
@login_required
@admin_required
def admin_database_delete_multiple(table_name):
    """Delete multiple rows from a table"""
    try:
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        if table_name not in tables:
            return jsonify({'success': False, 'message': 'Table not found'}), 404
        
        table_info = get_table_info(table_name)
        primary_keys = table_info['primary_keys']
        
        if not primary_keys:
            return jsonify({'success': False, 'message': 'Table has no primary key'}), 400
        
        row_ids = request.json.get('ids', []) if request.is_json else request.form.getlist('ids')
        if not row_ids:
            return jsonify({'success': False, 'message': 'No row IDs provided'}), 400
        
        pk_col = primary_keys[0]
        placeholders = ', '.join([f':id_{i}' for i in range(len(row_ids))])
        query = f'DELETE FROM "{table_name}" WHERE "{pk_col}" IN ({placeholders})'
        params = {f'id_{i}': row_id for i, row_id in enumerate(row_ids)}
        
        with db.engine.connect() as conn:
            conn.execute(text(query), params)
            conn.commit()
        
        log_database_action('DELETE', table_name, ','.join(map(str, row_ids)), f"Deleted {len(row_ids)} rows")
        return jsonify({'success': True, 'message': f'{len(row_ids)} row(s) deleted successfully'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting rows: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/admin/database/clear/<table_name>', methods=['POST'])
@login_required
@admin_required
def admin_database_clear_table(table_name):
    """Clear all rows from a table with security restrictions"""
    try:
        # Define allowed tables (tables that can be cleared)
        ALLOWED_TABLES = {
            'product', 'category', 'order', 'order_item', 'cart_item', 
            'wishlist_item', 'customer_feedback', 'shipment_record', 
            'newsletter_subscriber', 'subscriber', 'payments', 
            'payment_transactions', 'user_profile', 'user_payment_method',
            'what_app_message_log', 'whatsapp_message_log', 
            'product_restock_request', 'user', 'email_campaign', 'email_log'
        }
        
        # Define protected tables (tables that cannot be cleared)
        PROTECTED_TABLES = {
            'app_settings', 'site_settings', 'database_log', 'payment_methods'
        }
        
        # Validate table exists
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        if table_name not in tables:
            flash('Table not found', 'error')
            return redirect(url_for('admin_database_manager'))
        
        # Check if table is protected
        if table_name in PROTECTED_TABLES:
            flash(f'Cannot clear protected table: {table_name}', 'error')
            return redirect(url_for('admin_database_manager'))
        
        # Check if table is allowed
        if table_name not in ALLOWED_TABLES:
            flash(f'Table {table_name} is not in the allowed list for clearing', 'error')
            return redirect(url_for('admin_database_manager'))
        
        # Special handling for user table - preserve admin accounts
        if table_name == 'user':
            # Delete all non-admin users
            # Preserve users where is_admin = 1 OR role = 'admin'
            # Using COALESCE to handle NULL values safely
            query = 'DELETE FROM "user" WHERE (COALESCE(is_admin, 0) != 1) AND (COALESCE(role, \'\') != \'admin\')'
        else:
            # For all other allowed tables, delete all rows
            query = f'DELETE FROM "{table_name}"'
        
        with db.engine.connect() as conn:
            result = conn.execute(text(query))
            conn.commit()
            deleted_count = result.rowcount
        
        # Log the action with action="clear_table" as requested
        log_database_action('clear_table', table_name, None, f"Cleared table - deleted {deleted_count} rows")
        
        flash(f'Successfully cleared {table_name}. {deleted_count} row(s) deleted.', 'success')
        return redirect(url_for('admin_database_manager'))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error clearing table: {str(e)}")
        flash(f'Error clearing table: {str(e)}', 'error')
        return redirect(url_for('admin_database_manager'))

@app.route('/admin/database/export/db', methods=['GET'])
@login_required
@admin_required
def admin_database_export_db():
    """Export full database as SQLite file"""
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'store_backup_{timestamp}.sql'
        buffer = dump_database_to_memory()
        log_database_action('EXPORT', None, None, f"Exported database as {filename}")
        return send_file(buffer, as_attachment=True, download_name=filename, mimetype='application/sql')
    except DatabaseBackupError as exc:
        current_app.logger.error(f"Error exporting database: {exc}")
        flash(f'pg_dump failed: {exc}', 'error')
        return redirect(url_for('admin_database_manager'))
    except Exception as e:
        current_app.logger.error(f"Error exporting database: {str(e)}")
        flash(f'Error exporting database: {str(e)}', 'error')
        return redirect(url_for('admin_database_manager'))

@app.route('/admin/database/export/sql', methods=['GET'])
@login_required
@admin_required
def admin_database_export_sql():
    """Export database as SQL dump"""
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'store_dump_{timestamp}.sql'
        buffer = dump_database_to_memory()
        log_database_action('EXPORT', None, None, f"Exported database as SQL dump {filename}")
        return send_file(buffer, as_attachment=True, download_name=filename, mimetype='text/sql')
    except DatabaseBackupError as exc:
        current_app.logger.error(f"Error exporting SQL: {exc}")
        flash(f'pg_dump failed: {exc}', 'error')
        return redirect(url_for('admin_database_manager'))
    except Exception as e:
        current_app.logger.error(f"Error exporting SQL: {str(e)}")
        flash(f'Error exporting SQL: {str(e)}', 'error')
        return redirect(url_for('admin_database_manager'))

@app.route('/admin/database/export/csv/<table_name>', methods=['GET'])
@login_required
@admin_required
def admin_database_export_csv(table_name):
    """Export a table as CSV"""
    try:
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        if table_name not in tables:
            flash('Table not found', 'error')
            return redirect(url_for('admin_database_manager'))
        
        table_info = get_table_info(table_name)
        columns = [col['name'] for col in table_info['columns']]
        
        query = f'SELECT * FROM "{table_name}"'
        
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)
        
        with db.engine.connect() as conn:
            result = conn.execute(text(query))
            for row in result:
                # Convert row values to strings, handling None and datetime objects
                row_data = []
                for val in row:
                    if val is None:
                        row_data.append('')
                    elif isinstance(val, datetime):
                        row_data.append(val.strftime('%Y-%m-%d %H:%M:%S'))
                    else:
                        row_data.append(str(val))
                writer.writerow(row_data)
        
        output.seek(0)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'{table_name}_{timestamp}.csv'
        
        log_database_action('EXPORT', table_name, None, f"Exported table as CSV {filename}")
        
        # Convert StringIO to BytesIO for send_file
        output_bytes = BytesIO(output.getvalue().encode('utf-8-sig'))  # utf-8-sig for Excel compatibility
        output_bytes.seek(0)
        
        return send_file(output_bytes, as_attachment=True, download_name=filename, mimetype='text/csv')
    except Exception as e:
        current_app.logger.error(f"Error exporting CSV: {str(e)}")
        flash(f'Error exporting CSV: {str(e)}', 'error')
        return redirect(url_for('admin_database_manager'))

@app.route('/admin/database/import/db', methods=['POST'])
@login_required
@admin_required
def admin_database_import_db():
    """Import database from SQLite file"""
    flash('Direct .db imports are not supported when using PostgreSQL. Please use pg_restore or Alembic migrations.', 'error')
    return redirect(url_for('admin_database_manager'))

@app.route('/admin/database/import/sql', methods=['POST'])
@login_required
@admin_required
def admin_database_import_sql():
    """Import SQL dump file"""
    flash('Direct SQL imports through the dashboard are disabled for PostgreSQL. Use `psql` or Flask-Migrate instead.', 'error')
    return redirect(url_for('admin_database_manager'))

@app.route('/admin/database/backup', methods=['POST'])
@login_required
@admin_required
def admin_database_backup():
    """Create a backup of the database"""
    try:
        backup_path = dump_database_to_file(prefix='manual')
        filename = os.path.basename(backup_path)
        log_database_action('BACKUP', None, None, f"Created backup: {filename}")
        flash(f'Backup created successfully: {filename}', 'success')
        return redirect(url_for('admin_database_manager'))
    except DatabaseBackupError as exc:
        current_app.logger.error(f"Error creating backup: {exc}")
        flash(f'pg_dump failed: {exc}', 'error')
        return redirect(url_for('admin_database_manager'))
    except Exception as e:
        current_app.logger.error(f"Error creating backup: {str(e)}")
        flash(f'Error creating backup: {str(e)}', 'error')
        return redirect(url_for('admin_database_manager'))

@app.route('/admin/database/restore', methods=['POST'])
@login_required
@admin_required
def admin_database_restore():
    """Restore database from a backup"""
    flash('Dashboard-based restore is disabled. Use `psql`/`pg_restore` with the downloaded .sql backup.', 'error')
    return redirect(url_for('admin_database_manager'))

@app.route('/admin/database/backups', methods=['GET'])
@login_required
@admin_required
def admin_database_list_backups():
    """List all available backups"""
    try:
        backups_dir = get_backup_directory()
        if not os.path.exists(backups_dir):
            return jsonify({'backups': []})
        
        backups = []
        for filename in os.listdir(backups_dir):
            if filename.endswith('.sql'):
                filepath = os.path.join(backups_dir, filename)
                stat = os.stat(filepath)
                backups.append({
                    'filename': filename,
                    'size': stat.st_size,
                    'created': datetime.fromtimestamp(stat.st_ctime).strftime('%Y-%m-%d %H:%M:%S')
                })
        
        backups.sort(key=lambda x: x['created'], reverse=True)
        return jsonify({'backups': backups})
    except Exception as e:
        current_app.logger.error(f"Error listing backups: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/admin/database/automation', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_database_automation():
    """Manage automated backup settings and review logs."""
    settings = ensure_backup_defaults(get_or_create_app_settings(), auto_commit=True)
    if request.method == 'POST':
        try:
            settings.backup_enabled = request.form.get('backup_enabled') == 'on'
            settings.backup_time = sanitize_backup_time(request.form.get('backup_time', settings.backup_time or '02:00'))
            backup_email = request.form.get('backup_email', '').strip()
            if backup_email:
                settings.backup_email = backup_email
            else:
                settings.backup_email = settings.email_receiver or settings.resend_default_recipient or settings.resend_from_email or settings.contact_email or os.getenv('RESEND_DEFAULT_RECIPIENT', '')
            try:
                retention_input = int(request.form.get('backup_retention_days', settings.backup_retention_days or 30))
                settings.backup_retention_days = max(1, min(retention_input, 365))
            except (ValueError, TypeError):
                settings.backup_retention_days = 30
            db.session.commit()
            schedule_backup_job()
            flash('Backup automation settings updated.', 'success')
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Failed to update backup settings: {e}")
            flash(f'Error updating backup automation: {str(e)}', 'error')
        return redirect(url_for('admin_database_automation'))
    
    logs = DatabaseBackupLog.query.order_by(DatabaseBackupLog.created_at.desc()).limit(50).all()
    next_run = None
    if _backup_scheduler:
        job = _backup_scheduler.get_job(BACKUP_JOB_ID)
        if job and job.next_run_time:
            next_run = job.next_run_time
    return render_template(
        'admin/admin/database_automation.html',
        settings=settings,
        logs=logs,
        next_run=next_run
    )


@app.route('/admin/database/automation/run-now', methods=['POST'])
@login_required
@admin_required
def admin_database_automation_run_now():
    """Trigger manual backup with current settings."""
    if request.is_json:
        email_override = request.json.get('email')
    else:
        email_override = request.form.get('email')
    result = run_database_backup(trigger='manual', email_override=email_override)
    status = 200 if result.get('success') else 500
    return jsonify(result), status


@app.route('/admin/database/automation/logs.json', methods=['GET'])
@login_required
@admin_required
def admin_database_automation_logs():
    """Return recent backup logs for AJAX refresh."""
    logs = DatabaseBackupLog.query.order_by(DatabaseBackupLog.created_at.desc()).limit(100).all()
    data = []
    for log in logs:
        data.append({
            'id': log.id,
            'created_at': log.created_at.isoformat(),
            'status': log.status,
            'file_paths': log.file_paths,
            'error_message': log.error_message,
            'trigger': log.trigger,
            'email_recipient': log.email_recipient
        })
    return jsonify({'logs': data})

@app.route('/admin/database/logs', methods=['GET'])
@login_required
@admin_required
def admin_database_logs():
    """View database operation logs"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        
        logs = DatabaseLog.query.order_by(DatabaseLog.timestamp.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return render_template('admin/admin/database_logs.html', logs=logs)
    except Exception as e:
        current_app.logger.error(f"Error viewing logs: {str(e)}")
        flash(f'Error viewing logs: {str(e)}', 'error')
        return redirect(url_for('admin_database_manager'))

# ======================
# Background Tasks
# ======================

def check_scheduled_campaigns():
    """Placeholder for scheduled tasks"""
    pass

def check_abandoned_carts():
    """Placeholder for cart-related tasks"""
    pass

def check_restock_notifications():
    """Placeholder for inventory-related tasks"""
    pass

def init_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=check_scheduled_campaigns, trigger='interval', minutes=5)
    scheduler.add_job(func=check_abandoned_carts, trigger='interval', hours=1)
    scheduler.add_job(func=check_restock_notifications, trigger='interval', minutes=15)
    scheduler.start()

# Initialize scheduler when app starts
init_scheduler()

# Update order status and send email
@app.route('/admin/order/<int:order_id>/update-status', methods=['POST'])
@login_required
@admin_required
def update_order_status(order_id):
    """Update order status and send email notification"""
    order = Order.query.get_or_404(order_id)
    new_status = request.form.get('status')
    
    if not new_status or new_status == order.status:
        return jsonify({'success': False, 'message': 'No status change'})
    
    # Update status
    old_status = order.status
    order.status = new_status
    db.session.commit()
    
    
    return jsonify({
        'success': True,
        'message': f'Order status updated to {new_status}',
        'status': new_status
    })

@app.route('/order-confirmation/<int:order_id>')
@login_required
def order_confirmation(order_id):
    """Display order confirmation page"""
    order = Order.query.get_or_404(order_id)
    
    # Verify the order belongs to the current user
    if order.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    
    # Log that the confirmation page was viewed
    app.logger.info(f'Order {order_id} confirmation page viewed by user {current_user.id}')
    
    return render_template('order_confirmation.html', order=order)

@app.route('/api/payment/process', methods=['POST'])
@login_required
def process_payment():
    """
    Process payment and send confirmation email
    Expected JSON payload:
    {
        "order_id": 123,
        "payment_method": "wave",
        "reference": "PAY123456789",
        "amount": 100.00
    }
    """
    data = request.get_json()
    
    # Validate required fields
    required_fields = ['order_id', 'payment_method', 'reference', 'amount']
    if not all(field in data for field in required_fields):
        return jsonify({
            'success': False,
            'message': 'Missing required fields',
            'required': required_fields
        }), 400
    
    # Validate payment method (include modempay for unified gateway)
    valid_methods = ['wave', 'qmoney', 'afrimoney', 'ecobank', 'modempay']
    if data['payment_method'] not in valid_methods:
        return jsonify({
            'success': False,
            'message': 'Invalid payment method',
            'valid_methods': valid_methods
        }), 400
    
    # Get and validate order
    order = Order.query.get(data['order_id'])
    if not order:
        return jsonify({
            'success': False,
            'message': 'Order not found'
        }), 404
    
    # Verify order belongs to user or user is admin
    if order.user_id != current_user.id and not current_user.is_admin:
        return jsonify({
            'success': False,
            'message': 'Unauthorized access to this order'
        }), 403
    
    # Verify payment amount matches order total (with small tolerance for floating point)
    if abs(float(data['amount']) - order.total) > 0.01:
        return jsonify({
            'success': False,
            'message': 'Payment amount does not match order total',
            'order_total': order.total,
            'payment_amount': data['amount']
        }), 400
    
    try:
        # Import Payment model from payments module
        from app.payments.models import Payment
        
        # Create payment record
        payment = Payment(
            order_id=order.id,
            amount=data['amount'],
            method=data['payment_method'],
            reference=data['reference'],
            status='completed',
            paid_at=datetime.utcnow()
        )
        
        # Update order status
        order.status = 'paid'
        
        # Save to database
        db.session.add(payment)
        db.session.commit()
        
        
        return jsonify({
            'success': True,
            'message': 'Payment processed successfully',
            'payment_id': payment.id,
            'order_id': order.id
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error processing payment: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'An error occurred while processing your payment',
            'error': str(e)
        }), 500

# The main checkout route is defined above with all the functionality
# This duplicate definition has been removed to fix the duplicate endpoint error

# Set up model relationships and perform one-time bootstrap tasks.
# IMPORTANT: This must not run at import time (e.g., on Render) to avoid
# triggering app.app_context() before SERVER_NAME is safely configured.
def bootstrap_app_context() -> None:
    with app.app_context():
        # Get model classes that are defined in this file
        User = globals().get('User')
        Product = globals().get('Product')
        Order = globals().get('Order')

        # Create admin user if it doesn't exist
        # Wrap in try-except to handle case where new columns don't exist yet (migrations run in run.py)
        try:
            admin_user = User.query.filter_by(username='buxin').first()
            if not admin_user:
                admin_user = User(
                    username='buxin',
                    email='buxin@buxin.com',
                    is_admin=True,
                    role='admin',
                    active=True
                )
                admin_user.set_password('buxin')
                db.session.add(admin_user)
                db.session.commit()
                print("Admin user 'buxin' created successfully!")
            else:
                # Update existing user to admin if not already admin
                if not admin_user.is_admin:
                    admin_user.is_admin = True
                # Update role if it's not set or is 'customer' (use getattr to safely access)
                try:
                    current_role = getattr(admin_user, 'role', None)
                    if current_role in [None, 'customer']:
                        admin_user.role = 'admin'
                except (AttributeError, Exception):
                    # Column might not exist yet, migrations will handle it
                    pass
                # Update active status
                try:
                    current_active = getattr(admin_user, 'active', None)
                    if current_active is None:
                        admin_user.active = True
                except (AttributeError, Exception):
                    # Column might not exist yet, migrations will handle it
                    pass
                db.session.commit()
        except Exception as e:
            # If columns don't exist yet, migrations in run.py will handle it
            db.session.rollback()
            current_app.logger.warning(
                f"Could not create/update admin user (migrations may not have run yet): {str(e)}"
            )

        # Initialize automated backup scheduler
        try:
            initialize_backup_scheduler()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Failed to initialize backup scheduler: {str(e)}")

        # Create necessary directories
        os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'feedback'), exist_ok=True)
        os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'branding'), exist_ok=True)

        # Ensure site settings exist
        try:
            if not SiteSettings.query.first():
                default_settings = SiteSettings()
                db.session.add(default_settings)
                db.session.commit()
        except (ProgrammingError, OperationalError) as exc:
            db.session.rollback()
            current_app.logger.warning(
                f"Skipping site settings bootstrap; tables not ready: {exc}"
            )

        # Ensure supporting upload directories exist
        try:
            os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'profile_pictures'), exist_ok=True)
        except Exception as e:
            current_app.logger.error(f"Error ensuring profile picture directory: {str(e)}")

        # Ensure every user has an associated profile
        try:
            users_without_profiles = []
            for user in User.query.all():
                if not getattr(user, 'profile', None):
                    ensure_user_profile(user)
                    users_without_profiles.append(user.id)
            if users_without_profiles:
                db.session.commit()
                current_app.logger.info(
                    f"Created profiles for users: {users_without_profiles}"
                )
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error ensuring user profiles: {str(e)}")


if __name__ == '__main__':
    # For local development, perform bootstrap tasks explicitly,
    # then run on all network interfaces (0.0.0.0) and port 5000.
    bootstrap_app_context()
    app.run(host='0.0.0.0', port=5000, debug=True)