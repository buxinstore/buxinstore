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
from .extensions import csrf, db, init_extensions, login_manager, mail, migrate, babel

# Import forum models so Alembic can detect them
from .models.forum import ForumPost, ForumFile, ForumLink, ForumComment, ForumReaction, ForumBan
# Import bulk email models so Alembic can detect them
from .models.bulk_email import BulkEmailJob, BulkEmailRecipient, BulkEmailJobLock
# Import country model so Alembic can detect it
from .models.country import Country
# Import currency rate model so Alembic can detect it
from .models.currency_rate import CurrencyRate
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


# Database-free routes - these must NEVER access the database
# Used to enforce strict isolation between application availability and database availability
DATABASE_FREE_ROUTES = {
    '/_health',
    '/health',
    '/status',
    '/ping',
    '/favicon.ico',
    '/service-worker.js',
    '/manifest.json',
    '/robots.txt',
    '/.well-known/',
}


def is_non_human_request(request_obj=None) -> bool:
    """
    Detect if a request is from a bot, crawler, monitoring tool, or non-interactive source.
    
    Returns True if the request appears to be automated/non-human, which means
    it should be database-free to prevent unnecessary Neon compute usage.
    
    This detects:
    - HEAD requests (often used by monitoring/probes)
    - Bot/crawler user agents
    - Monitoring tool user agents (UptimeRobot, Pingdom, etc.)
    - Requests with no cookies (monitoring tools typically don't send cookies)
    """
    from flask import request as flask_request, has_request_context
    
    if request_obj is None:
        if not has_request_context():
            return False
        request_obj = flask_request
    
    # HEAD requests are typically probes/monitoring
    if request_obj.method == 'HEAD':
        return True
    
    user_agent = request_obj.headers.get('User-Agent', '').lower()
    if not user_agent:
        # No user agent often indicates automated tools
        return True
    
    # Common bot/crawler/monitoring user agent patterns
    non_human_patterns = [
        'bot', 'crawler', 'spider', 'scraper',
        'uptimerobot', 'pingdom', 'monitoring', 'healthcheck',
        'uptime', 'status', 'ping', 'probe',
        'curl', 'wget', 'python-requests', 'go-http-client',
        'googlebot', 'bingbot', 'slurp', 'duckduckbot',
        'baiduspider', 'yandexbot', 'sogou', 'exabot',
        'facebot', 'ia_archiver', 'archive.org',
        'newrelic', 'datadog', 'splunk',
        'headless', 'phantom', 'selenium', 'webdriver',
    ]
    
    for pattern in non_human_patterns:
        if pattern in user_agent:
            return True
    
    # Requests with no cookies AND no session data are likely monitoring tools
    # (real browsers usually send at least some cookies or session data)
    has_cookies = bool(request_obj.cookies)
    try:
        from flask import session
        has_session = bool(session) and bool(dict(session))
    except (RuntimeError, AttributeError):
        has_session = False
    
    # If no cookies AND no session data, likely a monitoring probe
    if not has_cookies and not has_session:
        # Additional check: if it's a simple GET with no query params, more likely a probe
        if request_obj.method == 'GET' and not request_obj.query_string:
            return True
    
    return False


def is_database_free_route(path: str, request_obj=None) -> bool:
    """
    Check if a route path should be database-free.
    Returns True if the route should never access the database.
    
    This ensures strict isolation: health checks, monitoring, and public assets
    can work even when the database is unavailable, paused, or out of compute.
    
    For the homepage (/), also checks if the request is non-human/interactive.
    """
    if not path:
        return False
    
    # Exact matches (always database-free)
    if path in DATABASE_FREE_ROUTES:
        return True
    
    # Prefix matches (always database-free)
    for free_route in DATABASE_FREE_ROUTES:
        if path.startswith(free_route):
            return True
    
    # Static files are database-free
    if path.startswith('/static/'):
        return True
    
    # For homepage (/), check if request is non-human
    if path == '/':
        return is_non_human_request(request_obj)
    
    return False


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
    
    # Configure Babel locale and timezone selectors (Flask-Babel 3.0+ API)
    # Define locale selector function
    def get_locale():
        """
        Get the locale for Babel based on session or default.
        
        CRITICAL: For database-free routes, returns 'en' without any database access.
        This ensures health checks, monitoring, and public assets never trigger database connections.
        """
        from flask import session, request, has_request_context
        
        # Skip database access for database-free routes
        if has_request_context() and is_database_free_route(request.path, request):
            return 'en'
        
        # Try to get language from session (session-based, no DB access)
        try:
            language = session.get('language')
            if language:
                return language
        except (RuntimeError, AttributeError):
            # Outside request context or session not available
            pass
        
        # For non-database-free routes, we could call get_current_country(),
        # but that function will handle database-free routes internally.
        # To avoid any potential DB access here, we default to English.
        
        # Default to English
        return 'en'
    
    def get_timezone():
        """Get the timezone for Babel."""
        return 'UTC'
    
    # Initialize Babel with selector functions (Flask-Babel 3.0+ requires this)
    # This must be done after init_extensions but before any routes that use translations
    babel.init_app(app, locale_selector=get_locale, timezone_selector=get_timezone)
    
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
    
    # Register forum blueprint
    from app.routes.forum import forum_bp
    app.register_blueprint(forum_bp)
    
    # Register shipping blueprint
    from app.shipping.routes import shipping_bp
    app.register_blueprint(shipping_bp)
    
    # NOTE: Database table checks removed to prevent database access on startup.
    # Migrations should be run via Render's releaseCommand or manually.
    # The app will work correctly even if migrations haven't run yet.

    # Attach base URL helper and expose PUBLIC_URL-derived base_url to templates
    # This allows `current_app.get_base_url()` in request handlers.
    app.get_base_url = get_base_url

    # Register health endpoint FIRST, before any context processors or middleware
    # This endpoint must be 100% database-free for uptime monitoring
    @app.route("/_health", methods=["GET"])
    def healthcheck():
        """Database-free health check endpoint for uptime monitoring."""
        return jsonify({"status": "ok"}), 200

    @app.context_processor
    def inject_base_url():
        # Always derive from the helper so changes to PUBLIC_URL are reflected everywhere
        return {"base_url": app.get_base_url()}

    # Add error handler for database connection errors
    @app.errorhandler(OperationalError)
    def handle_database_error(e):
        """
        Handle database connection errors gracefully.
        
        CRITICAL: For database-free routes, this handler should never be triggered
        because those routes never access the database. If it is triggered for a
        database-free route, something is wrong with the isolation.
        """
        error_msg = str(e).lower()
        
        # Check if this is a compute quota error from Neon
        is_compute_error = (
            "compute" in error_msg and "quota" in error_msg
        ) or "compute time quota exceeded" in error_msg
        
        # Log the error (only once, don't spam logs)
        app.logger.error(
            f"Database connection error on {request.path}: {e}" + 
            (" (Neon compute quota exceeded)" if is_compute_error else ""),
            exc_info=True
        )
        
        # Try to rollback any pending transaction (only if session exists)
        # Don't attempt new connections - just clean up if possible
        try:
            if hasattr(db, 'session') and db.session.is_active:
                db.session.rollback()
        except Exception:
            # Silently fail - we're in error handling, don't make it worse
            pass
        
        # For database-free routes, return a simple error (shouldn't happen, but be safe)
        if is_database_free_route(request.path, request):
            return jsonify({
                "error": "Internal error",
                "message": "Service temporarily unavailable"
            }), 503
        
        # Return a user-friendly error message for database-requiring routes
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
DEFAULT_LOGO_URL = "https://res.cloudinary.com/dfjffnmzf/image/upload/v1763781131/Gemini_Generated_Image_ufkia2ufkia2ufki_pcf2lq.png"

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

@app.template_filter('convert_price')
def convert_price_filter(price, from_currency="GMD"):
    """Template filter to convert product price to current currency."""
    from .utils.currency_rates import convert_price, parse_price
    try:
        # Parse price to extract numeric value (handles strings with symbols)
        numeric_price, _ = parse_price(price)
        
        country = get_current_country()
        if country:
            to_currency = country.currency
            # Always convert from GMD (base currency) to target currency
            # The parse_price function already extracted the numeric value
            converted = convert_price(numeric_price, from_currency, to_currency)
            return f"{converted:.2f}"
        return f"{numeric_price:.2f}"
    except Exception as e:
        # Fallback: try to parse and return numeric value
        try:
            numeric_price, _ = parse_price(price)
            return f"{numeric_price:.2f}"
        except:
            return "0.00"

@app.template_filter('price_with_symbol')
def price_with_symbol_filter(price, from_currency="GMD", apply_profit=False):
    """Template filter to format price with currency symbol. Optionally applies profit if apply_profit=True."""
    from .utils.currency_rates import convert_price, get_currency_symbol, parse_price
    try:
        # Parse price to extract numeric value (handles strings with symbols)
        numeric_price, _ = parse_price(price)
        
        # Apply profit if requested (for product prices)
        if apply_profit:
            final_price, _, _ = get_product_price_with_profit(float(numeric_price))
            numeric_price = final_price
        
        country = get_current_country()
        if country:
            to_currency = country.currency
            # Always convert from GMD (base currency) to target currency
            converted = convert_price(numeric_price, from_currency, to_currency)
            symbol = get_currency_symbol(to_currency)
            return f"{symbol}{converted:.2f}"
        # Default to GMD if no country selected
        return f"D{numeric_price:.2f}"
    except Exception as e:
        # Fallback: try to parse and return with default symbol
        try:
            numeric_price, _ = parse_price(price)
            if apply_profit:
                final_price, _, _ = get_product_price_with_profit(float(numeric_price))
                numeric_price = final_price
            return f"D{numeric_price:.2f}"
        except:
            return "D0.00"

@app.template_filter('product_price_with_profit')
def product_price_with_profit_filter(product):
    """Template filter to format product price with profit applied."""
    if not product or not hasattr(product, 'price'):
        return "D0.00"
    base_price = float(product.price) if product.price else 0.0
    return price_with_symbol_filter(base_price, "GMD", apply_profit=True)

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
    """
    Inject site settings into templates.
    
    CRITICAL: For database-free routes, returns safe defaults without any database access.
    This ensures health checks, monitoring, and public assets work even when the database
    is unavailable, paused, or out of compute.
    """
    from flask import request, has_request_context
    
    # Skip ALL database access for database-free routes - use safe defaults
    if has_request_context() and is_database_free_route(request.path, request):
        # Return minimal safe defaults for database-free routes
        return {
            'site_settings': None,
            'site_logo_url': DEFAULT_LOGO_URL,
            'hero_image_url': None,
            'cart_currency': 'D',
            'current_user_avatar_url': None,
            'current_user_display_name': None,
            'current_user_google_connected': False,
            'app_settings': None,
            'floating_whatsapp': None,
            'floating_email': None,
            'floating_email_subject': 'Support Request',
            'floating_email_body': 'Hello, I need help with ...',
            'current_country': None,
            'current_currency': 'D',
            'current_currency_code': 'GMD',
            'current_language': 'en',
            'country_selected': False,
            'convert_price': lambda price, from_currency="GMD": float(price) if isinstance(price, (int, float)) else 0.0,
            'get_currency_symbol': lambda currency: 'D',
            'format_price': lambda price: f"{float(price):,.2f}" if price else "0.00",
            '_': lambda text: text,
            'pwa_app_name': 'buxin store',
            'pwa_short_name': 'buxin store',
            'pwa_theme_color': '#ffffff',
            'pwa_background_color': '#ffffff',
            'pwa_start_url': '/',
            'pwa_display': 'standalone',
            'pwa_description': 'buxin store - Your gateway to the future of technology. Explore robotics, coding, and artificial intelligence.',
            'pwa_logo_path': None,
            'pwa_favicon_path': None,
        }
    
    # For routes that need database access, proceed normally
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
        # Set default logo in database if not set
        settings.logo_path = DEFAULT_LOGO_URL
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
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
    
    # Get AppSettings for floating contact widget and other app-wide settings
    # Use try-except to handle case where columns don't exist yet (before migration runs)
    app_settings = None
    floating_whatsapp = None
    floating_email = None
    floating_email_subject = 'Support Request'
    floating_email_body = 'Hello, I need help with ...'
    
    try:
        # Check if floating_whatsapp_number column exists before querying AppSettings
        # SQLAlchemy will try to SELECT all columns in the model, which will fail if new columns don't exist
        from sqlalchemy import text
        
        # First, check if the migration has been run by checking if the column exists
        check_query = text("""
            SELECT 1 
            FROM information_schema.columns 
            WHERE table_name = 'app_settings' 
            AND column_name = 'floating_whatsapp_number'
            LIMIT 1
        """)
        
        migration_complete = False
        try:
            result = db.session.execute(check_query)
            migration_complete = result.fetchone() is not None
        except Exception:
            # If we can't check, assume migration hasn't run
            migration_complete = False
            try:
                db.session.rollback()
            except Exception:
                pass
        
        if migration_complete:
            # Migration has run, safe to query AppSettings normally
            app_settings = AppSettings.query.first()
            if app_settings:
                floating_whatsapp = getattr(app_settings, 'floating_whatsapp_number', None)
                floating_email = getattr(app_settings, 'floating_support_email', None)
                floating_email_subject = getattr(app_settings, 'floating_email_subject', 'Support Request') or 'Support Request'
                floating_email_body = getattr(app_settings, 'floating_email_body', 'Hello, I need help with ...') or 'Hello, I need help with ...'
                
                # Extract PWA settings with safe attribute access and defaults
                pwa_app_name = getattr(app_settings, 'pwa_app_name', None) or 'buxin store'
                pwa_short_name = getattr(app_settings, 'pwa_short_name', None) or 'buxin store'
                pwa_theme_color = getattr(app_settings, 'pwa_theme_color', None) or '#ffffff'
                pwa_background_color = getattr(app_settings, 'pwa_background_color', None) or '#ffffff'
                pwa_start_url = getattr(app_settings, 'pwa_start_url', None) or '/'
                pwa_display = getattr(app_settings, 'pwa_display', None) or 'standalone'
                pwa_description = getattr(app_settings, 'pwa_description', None) or 'buxin store - Your gateway to the future of technology. Explore robotics, coding, and artificial intelligence.'
                pwa_logo_path = getattr(app_settings, 'pwa_logo_path', None)
                pwa_favicon_path = getattr(app_settings, 'pwa_favicon_path', None)
            else:
                # No app settings - use defaults
                pwa_app_name = 'buxin store'
                pwa_short_name = 'buxin store'
                pwa_theme_color = '#ffffff'
                pwa_background_color = '#ffffff'
                pwa_start_url = '/'
                pwa_display = 'standalone'
                pwa_description = 'buxin store - Your gateway to the future of technology. Explore robotics, coding, and artificial intelligence.'
                pwa_logo_path = None
                pwa_favicon_path = None
        else:
            # Migration hasn't run yet, use defaults
            pwa_app_name = 'buxin store'
            pwa_short_name = 'buxin store'
            pwa_theme_color = '#ffffff'
            pwa_background_color = '#ffffff'
            pwa_start_url = '/'
            pwa_display = 'standalone'
            pwa_description = 'buxin store - Your gateway to the future of technology. Explore robotics, coding, and artificial intelligence.'
            pwa_logo_path = None
            pwa_favicon_path = None
    except Exception as e:
        # If anything fails, rollback and use defaults
        try:
            db.session.rollback()
        except Exception:
            pass
        current_app.logger.warning(f"Could not load AppSettings for floating contact widget and PWA settings (migration may be pending): {e}")
        app_settings = None
        # Use defaults for PWA settings
        pwa_app_name = 'buxin store'
        pwa_short_name = 'buxin store'
        pwa_theme_color = '#ffffff'
        pwa_background_color = '#ffffff'
        pwa_start_url = '/'
        pwa_display = 'standalone'
        pwa_description = 'buxin store - Your gateway to the future of technology. Explore robotics, coding, and artificial intelligence.'
        pwa_logo_path = None
        pwa_favicon_path = None
    
    # Get current country for localization
    current_country = None
    current_currency = current_app.config.get('CART_CURRENCY_SYMBOL', 'D')
    current_language = 'en'
    country_selected = session.get('country_selected', False)
    
    try:
        # Try to get current country (this function is defined later in the file)
        # We'll use a try-except to handle cases where Country table doesn't exist yet
        from sqlalchemy import text
        check_query = text("""
            SELECT 1 
            FROM information_schema.tables 
            WHERE table_name = 'country'
            LIMIT 1
        """)
        try:
            result = db.session.execute(check_query)
            table_exists = result.fetchone() is not None
        except Exception:
            table_exists = False
            try:
                db.session.rollback()
            except Exception:
                pass
        
        if table_exists:
            # Get country from user profile or session
            if current_user.is_authenticated and hasattr(current_user, 'country_id') and current_user.country_id:
                current_country = Country.query.get(current_user.country_id)
            elif session.get('selected_country_id'):
                current_country = Country.query.get(session.get('selected_country_id'))
            
            # If no country selected, get default (first active country)
            if not current_country:
                current_country = Country.query.filter_by(is_active=True).first()
            
            if current_country and current_country.is_active:
                current_currency = current_country.currency_symbol or current_country.currency
                current_language = current_country.language
                # Update session if not already set
                if not session.get('language'):
                    session['language'] = current_country.language
                if not session.get('currency'):
                    session['currency'] = current_country.currency
    except Exception as e:
        current_app.logger.debug(f"Could not load country settings (migration may be pending): {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
    
    # Import currency conversion helper
    from .utils.currency_rates import convert_price, get_currency_symbol, format_price
    from flask_babel import gettext as _
    
    # Helper function for templates to convert prices
    def convert_product_price(price, from_currency="GMD"):
        """Convert product price to current currency."""
        from .utils.currency_rates import parse_price
        # Parse price to extract numeric value (handles strings with symbols)
        numeric_price, _ = parse_price(price)
        if not current_country:
            return float(numeric_price)
        to_currency = current_country.currency
        return convert_price(numeric_price, from_currency, to_currency)
    
    return {
        'site_settings': settings,
        'site_logo_url': logo_url,
        'hero_image_url': hero_image_url,
        'cart_currency': current_currency,
        'current_user_avatar_url': avatar_url,
        'current_user_display_name': display_name,
        'current_user_google_connected': google_connected,
        'product_image_url': product_image_url_filter,
        'app_settings': app_settings,
        'floating_whatsapp': floating_whatsapp,
        'floating_email': floating_email,
        'floating_email_subject': floating_email_subject,
        'floating_email_body': floating_email_body,
        # Country/Localization Settings
        'current_country': current_country,
        'current_currency': current_currency,
        'current_currency_code': current_country.currency if current_country else 'GMD',
        'current_language': current_language,
        'country_selected': country_selected,
        # Currency conversion helpers
        'convert_price': convert_product_price,
        'get_currency_symbol': get_currency_symbol,
        'format_price': format_price,
        # Translation helper
        '_': _,
        # PWA Settings (with fallback defaults)
        'pwa_app_name': pwa_app_name if 'pwa_app_name' in locals() else 'buxin store',
        'pwa_short_name': pwa_short_name if 'pwa_short_name' in locals() else 'buxin store',
        'pwa_theme_color': pwa_theme_color if 'pwa_theme_color' in locals() else '#ffffff',
        'pwa_background_color': pwa_background_color if 'pwa_background_color' in locals() else '#ffffff',
        'pwa_start_url': pwa_start_url if 'pwa_start_url' in locals() else '/',
        'pwa_display': pwa_display if 'pwa_display' in locals() else 'standalone',
        'pwa_description': pwa_description if 'pwa_description' in locals() else 'buxin store - Your gateway to the future of technology. Explore robotics, coding, and artificial intelligence.',
        'pwa_logo_path': pwa_logo_path if 'pwa_logo_path' in locals() else None,
        'pwa_favicon_path': pwa_favicon_path if 'pwa_favicon_path' in locals() else None,
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

# ======================
# CSRF Configuration (Production-Ready)
# ======================
app.config['WTF_CSRF_ENABLED'] = True
app.config['WTF_CSRF_SECRET_KEY'] = os.environ.get('CSRF_SECRET_KEY', 'change-this-in-production-csrf-key-2024')
app.config['WTF_CSRF_TIME_LIMIT'] = 3600  # Extend CSRF token validity to 1 hour
app.config['WTF_CSRF_SSL_STRICT'] = False  # Allow CSRF on non-HTTPS for development
app.config['WTF_CSRF_CHECK_DEFAULT'] = True

# ======================
# Session Cookie Configuration (Production-Ready)
# ======================
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('IS_RENDER', 'false').lower() == 'true'  # True in production
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# Set domain for cross-subdomain if needed (uncomment if using subdomains)
# app.config['SESSION_COOKIE_DOMAIN'] = '.techbuxin.com'

app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# Email configuration removed as per user request

# ======================
# Cache Control - Prevent stale pages with expired CSRF tokens
# ======================
@app.after_request
def add_cache_control_headers(response):
    """Add cache control headers to prevent Cloudflare/browser from serving stale pages"""
    # Apply no-cache headers to admin pages, form pages, and auth pages
    if (request.path.startswith('/admin/') or 
        request.path.startswith('/login') or 
        request.path.startswith('/register') or
        request.path.startswith('/checkout') or
        request.path.startswith('/profile') or
        request.path.startswith('/china/') or
        request.path.startswith('/gambia/') or
        request.path.startswith('/forum/') or
        request.method == 'POST'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

# ======================
# CSRF Error Handler with Logging
# ======================
@app.errorhandler(CSRFError)
def csrf_error(e):
    """Handle CSRF errors with detailed logging and user-friendly responses"""
    # Log the CSRF error for debugging
    error_details = {
        'error_type': 'CSRF_ERROR',
        'description': str(e.description) if hasattr(e, 'description') else str(e),
        'path': request.path,
        'method': request.method,
        'referrer': request.referrer,
        'user_agent': request.headers.get('User-Agent', 'Unknown'),
        'remote_addr': request.remote_addr,
        'has_csrf_token': bool(request.form.get('csrf_token') or request.headers.get('X-CSRFToken')),
        'content_type': request.content_type,
        'is_xhr': request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    }
    app.logger.warning(f"[CSRF ERROR] {error_details}")
    
    # Check if this is an AJAX/API request
    is_ajax = (request.is_json or 
               request.headers.get('Content-Type') == 'application/json' or 
               request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
               request.path.startswith('/admin/') or
               request.path.startswith('/api/'))
    
    # Determine error message based on error type
    error_str = str(e.description) if hasattr(e, 'description') else str(e)
    if 'expired' in error_str.lower() or 'time' in error_str.lower():
        error_msg = 'Your session has expired. Please refresh the page and try again.'
    elif 'missing' in error_str.lower():
        error_msg = 'Security token is missing. Please refresh the page and try again.'
    else:
        error_msg = 'Security validation failed. Please refresh the page and try again.'
    
    if is_ajax:
        return jsonify({
            'success': False, 
            'message': error_msg,
            'csrf_expired': True,
            'error_code': 'CSRF_ERROR',
            'redirect': request.referrer or url_for('home')
        }), 400
    
    # For regular form submissions, flash a message and redirect
    flash(error_msg, 'error')
    return redirect(request.referrer or url_for('home'))

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
    country_id = db.Column(db.Integer, db.ForeignKey('country.id'), nullable=True)  # User's selected country
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
    from app.utils.whatsapp_token import get_whatsapp_token
    
    # Get WhatsApp credentials dynamically (DB first, then .env)
    access_token, phone_number_id = get_whatsapp_token()
    
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
        Formatted subject with prefix (e.g., "buxin store - Reset Your Password")
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
                    <p style="color: #666; font-size: 12px;">This is an automated notification from your buxin store.</p>
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
    # Relationship to products is defined via backref in Product model

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
    default_subject_prefix = db.Column(db.String(100), default='buxin store')  # Default subject prefix
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
    # Floating Contact Widget Settings
    floating_whatsapp_number = db.Column(db.String(50), nullable=True)  # WhatsApp number for floating widget
    floating_support_email = db.Column(db.String(255), nullable=True)  # Support email for floating widget
    floating_email_subject = db.Column(db.String(255), nullable=True, default='Support Request')  # Default email subject
    floating_email_body = db.Column(db.Text, nullable=True, default='Hello, I need help with ...')  # Default email body
    # Gambia Contact Settings (for order confirmation page)
    gambia_whatsapp_number = db.Column(db.String(50), nullable=True)  # WhatsApp number for Gambia orders
    gambia_phone_number = db.Column(db.String(50), nullable=True)  # Phone number for Gambia orders
    # PWA Settings
    pwa_app_name = db.Column(db.String(255), nullable=True)  # PWA app name
    pwa_short_name = db.Column(db.String(100), nullable=True)  # PWA short name
    pwa_theme_color = db.Column(db.String(20), nullable=True, default='#ffffff')  # PWA theme color
    pwa_background_color = db.Column(db.String(20), nullable=True, default='#ffffff')  # PWA background color
    pwa_start_url = db.Column(db.String(255), nullable=True, default='/')  # PWA start URL
    pwa_display = db.Column(db.String(50), nullable=True, default='standalone')  # PWA display mode (standalone/fullscreen/minimal-ui)
    pwa_description = db.Column(db.Text, nullable=True)  # PWA description
    pwa_logo_path = db.Column(db.String(500), nullable=True)  # PWA logo path (512x512)
    pwa_favicon_path = db.Column(db.String(500), nullable=True)  # PWA favicon path (32x32 or 64x64)
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
    weight_kg = db.Column(db.Numeric(10, 6), nullable=True)  # Product weight in kilograms for shipping calculation
    location = db.Column(db.String(50), nullable=True)  # 'In The Gambia' or 'Outside The Gambia'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    category = db.relationship('Category', backref='products', lazy=True)
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
    customer_email = db.Column(db.String(255), nullable=True)
    customer_photo_url = db.Column(db.String(500), nullable=True)  # Optional customer photo URL
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
    
    # Shipping rule fields (for automatic shipping calculation)
    shipping_rule_id = db.Column(db.Integer, db.ForeignKey('shipping_rules.id'), nullable=True)  # Which shipping rule was applied (new system)
    shipping_mode_key = db.Column(db.String(20), nullable=True)  # Selected shipping method: 'express', 'ecommerce', 'economy'
    shipping_delivery_estimate = db.Column(db.String(100), nullable=True)  # Delivery time estimate from rule
    shipping_display_currency = db.Column(db.String(10), nullable=True)  # Currency used for display (e.g., 'GMD', 'XOF')
    
    # Profit tracking fields
    total_profit_gmd = db.Column(db.Float, nullable=True)  # Total profit for this order in GMD
    total_revenue_gmd = db.Column(db.Float, nullable=True)  # Total revenue (base prices + profit) in GMD
    
    # Relationship for assigned user
    assigned_user = db.relationship('User', primaryjoin='Order.assigned_to == User.id', foreign_keys=[assigned_to], backref=db.backref('assigned_orders', lazy=True))
    shipping_rule = db.relationship('app.shipping.models.ShippingRule', foreign_keys=[shipping_rule_id], backref='orders', lazy=True)

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)  # Final price (base + profit) per unit in GMD
    base_price = db.Column(db.Float, nullable=True)  # Base price before profit in GMD
    profit_amount = db.Column(db.Float, nullable=True)  # Profit amount per unit in GMD
    profit_rule_id = db.Column(db.Integer, db.ForeignKey('profit_rule.id'), nullable=True)  # Which profit rule was applied
    
    # Relationships
    product = db.relationship('Product', backref='order_items')
    profit_rule = db.relationship('ProfitRule', backref='order_items', lazy=True)

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

class LegacyShippingRule(db.Model):
    """Legacy shipping rules - DEPRECATED: Use app.shipping.models.ShippingRule instead"""
    __tablename__ = 'shipping_rule'
    
    id = db.Column(db.Integer, primary_key=True)
    rule_type = db.Column(db.String(20), nullable=False, default='country')  # 'country' or 'global'
    country_id = db.Column(db.Integer, db.ForeignKey('country.id'), nullable=True)  # Nullable for global rules
    shipping_mode_key = db.Column(db.String(20), nullable=True)  # 'express', 'ecommerce', 'economy' or None for all methods
    min_weight = db.Column(db.Numeric(10, 6), nullable=False)  # Decimal precision for small weights
    max_weight = db.Column(db.Numeric(10, 6), nullable=False)
    price_gmd = db.Column(db.Numeric(10, 2), nullable=False)  # Price in GMD
    delivery_time = db.Column(db.String(100), nullable=True)  # e.g., "7-30 days"
    priority = db.Column(db.Integer, default=0, nullable=False)  # Higher priority rules apply first
    status = db.Column(db.Boolean, default=True, nullable=False)  # Active/Inactive
    note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    country = db.relationship('Country', backref='shipping_rules', lazy=True)
    
    def __repr__(self):
        country_name = self.country.name if self.country else 'Global'
        return f'<ShippingRule {self.id}: {country_name} {self.min_weight}-{self.max_weight}kg = D{self.price_gmd}>'
    
    def to_dict(self):
        """Convert shipping rule to dictionary"""
        return {
            'id': self.id,
            'rule_type': self.rule_type,
            'country_id': self.country_id,
            'country_name': self.country.name if self.country else None,
            'country_code': self.country.code if self.country else None,
            'shipping_mode_key': self.shipping_mode_key,
            'min_weight': float(self.min_weight) if self.min_weight else 0.0,
            'max_weight': float(self.max_weight) if self.max_weight else 0.0,
            'price_gmd': float(self.price_gmd) if self.price_gmd else 0.0,
            'delivery_time': self.delivery_time,
            'priority': self.priority,
            'status': self.status,
            'note': self.note,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

class ProfitRule(db.Model):
    """Profit rules for calculating profit based on product base price ranges"""
    __tablename__ = 'profit_rule'
    
    id = db.Column(db.Integer, primary_key=True)
    min_price = db.Column(db.Numeric(10, 2), nullable=False)  # Minimum base price in GMD
    max_price = db.Column(db.Numeric(10, 2), nullable=True)  # Maximum base price in GMD (None = no upper limit)
    profit_amount = db.Column(db.Numeric(10, 2), nullable=False)  # Profit amount in GMD
    priority = db.Column(db.Integer, default=0, nullable=False)  # Higher priority rules apply first
    is_active = db.Column(db.Boolean, default=True, nullable=False)  # Active/Inactive
    note = db.Column(db.Text, nullable=True)  # Optional note
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    def __repr__(self):
        max_str = f"{self.max_price}" if self.max_price else "∞"
        return f'<ProfitRule {self.id}: D{self.min_price}-{max_str} = +D{self.profit_amount} (priority: {self.priority})>'
    
    def to_dict(self):
        """Convert profit rule to dictionary"""
        return {
            'id': self.id,
            'min_price': float(self.min_price) if self.min_price else 0.0,
            'max_price': float(self.max_price) if self.max_price else None,
            'profit_amount': float(self.profit_amount) if self.profit_amount else 0.0,
            'priority': self.priority,
            'is_active': self.is_active,
            'note': self.note,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    def matches_price(self, base_price: float) -> bool:
        """Check if this rule matches the given base price"""
        if not self.is_active:
            return False
        
        min_price = float(self.min_price) if self.min_price else 0.0
        max_price = float(self.max_price) if self.max_price else float('inf')
        
        return min_price <= base_price <= max_price

def calculate_shipping_price(total_weight_kg: float, country_id: Optional[int] = None, shipping_mode_key: Optional[str] = None, default_weight: float = 0.0) -> Optional[Dict[str, any]]:
    """
    Calculate shipping price based on total cart weight, country, and shipping method.
    Uses the NEW shipping system (app.shipping.models.ShippingRule) with country_iso.
    
    Args:
        total_weight_kg: Total weight in kilograms
        country_id: Optional country ID to filter rules (will be converted to country_iso)
        shipping_mode_key: Optional shipping method ID ('express', 'economy_plus', 'economy') to filter rules
        default_weight: Default weight to use if total_weight_kg is 0 or None
    
    Returns:
        Dict with keys: 'rule', 'price_gmd', 'delivery_time', 'rule_name', 'debug_info', 'available', 'shipping_mode_key'
        or None if no rule matches (should default to 0)
    """
    # Use the new shipping service
    from app.shipping.service import ShippingService
    from app.shipping.models import ShippingRule as NewShippingRule
    
    if total_weight_kg is None or total_weight_kg <= 0:
        total_weight_kg = default_weight
    
    # Convert country_id to country_iso
    country_iso = None
    country_name = None
    if country_id:
        try:
            country_id = int(country_id)
            country = Country.query.get(country_id)
            if country:
                country_iso = country.code  # Use ISO code (e.g., 'GMB')
                country_name = country.name
                current_app.logger.debug(f"Shipping calculation: country_id={country_id}, country_iso={country_iso}, country_name={country_name}")
        except (ValueError, TypeError):
            country_id = None
    
    # Map old shipping_mode_key names to new ones
    method_mapping = {
        'express': 'express',
        'ecommerce': 'economy_plus',  # Old 'ecommerce' maps to new 'economy_plus'
        'economy': 'economy'
    }
    if shipping_mode_key:
        shipping_mode_key = method_mapping.get(shipping_mode_key, shipping_mode_key)
    
    # Ensure we have a valid country_iso string (use '*' for global if None)
    # Always prefer ISO code over country name
    country_iso_str = country_iso if country_iso else '*'
    
    current_app.logger.debug(f"Shipping calculation: country_iso_str={country_iso_str}, shipping_mode_key={shipping_mode_key}, weight={total_weight_kg}kg")
    
    # If no shipping method specified, try all methods and return the first match
    if not shipping_mode_key:
        # Try each active method from database in order
        from app.shipping.models import ShippingMode
        active_methods = ShippingMode.query.filter_by(active=True).order_by(ShippingMode.id).all()
        for mode in active_methods:
            mode_key = mode.key
            result = ShippingService.calculate_shipping(
                country_iso=country_iso_str,
                shipping_mode_key=mode_key,
                total_weight_kg=total_weight_kg
            )
            if result.get('available'):
                # Convert result to expected format
                return {
                    'rule': None,  # Can't return rule object directly
                    'price_gmd': result.get('shipping_fee_gmd', 0.0),
                    'delivery_time': result.get('delivery_time'),
                    'rule_name': f"{country_name or 'Global'} ({mode_key})",
                    'rule_type': 'country' if country_iso else 'global',
                    'country_id': country_id,
                    'country_name': country_name,
                    'shipping_mode_key': mode_key,
                    'debug_info': {
                        'chosen_country': country_name,
                        'chosen_country_id': country_id,
                        'shipping_mode_key': mode_key,
                        'weight_kg': total_weight_kg,
                        'applied_rule_id': result.get('rule_id'),
                        'final_shipping_price': result.get('shipping_fee_gmd', 0.0),
                        'rule_selection_reason': f"Matched {mode_key} mode",
                        'shipping_source': 'New Shipping System'
                    },
                    'available': True
                }
        # No method matched
        return None
    
    # Calculate with specific method
    result = ShippingService.calculate_shipping(
        country_iso=country_iso_str,
        shipping_mode_key=shipping_mode_key,
        total_weight_kg=total_weight_kg
    )
    
    if result and isinstance(result, dict) and result.get('available'):
        # Get the rule object for compatibility
        rule_obj = None
        if result.get('rule_id'):
            rule_obj = NewShippingRule.query.get(result.get('rule_id'))
        
        return {
            'rule': rule_obj,
            'price_gmd': result.get('shipping_fee_gmd', 0.0),
            'delivery_time': result.get('delivery_time'),
            'rule_name': f"{country_name or 'Global'} ({shipping_mode_key})",
            'rule_type': 'country' if country_iso else 'global',
            'country_id': country_id,
            'country_name': country_name,
            'shipping_mode_key': shipping_mode_key,
            'debug_info': {
                'chosen_country': country_name,
                'chosen_country_id': country_id,
                'shipping_mode_key': shipping_mode_key,
                'weight_kg': total_weight_kg,
                'applied_rule_id': result.get('rule_id'),
                'final_shipping_price': result.get('shipping_fee_gmd', 0.0),
                'rule_selection_reason': 'Matched shipping rule',
                'shipping_source': 'New Shipping System'
            },
            'available': True
        }
    
    # No rule found
    debug_info = {
        'chosen_country': country_name if country_id else None,
        'chosen_country_id': country_id,
        'shipping_mode_key': shipping_mode_key,
        'weight_kg': total_weight_kg,
        'applied_rule_id': None,
        'weight_range_matched': None,
        'final_shipping_price': 0.0,
        'rule_selection_reason': 'No matching rule found',
        'other_shipping_sources_used': False,
        'shipping_source': 'New Shipping System',
        'confirmation': 'No matching rule found in new shipping system'
    }
    
    current_app.logger.warning(
        f"❌ NO SHIPPING RULE MATCHED: "
        f"Country={country_name if country_id else 'None'}, "
        f"Country ID={country_id}, Method={shipping_mode_key or 'any'}, Weight={total_weight_kg}kg. "
        f"Shipping fee will default to 0. "
        f"Debug Info: {debug_info}. "
        f"CONFIRMED: New Shipping System - no other sources used."
    )
    
    return None

def calculate_profit_for_price(base_price: float) -> Tuple[float, Optional[int]]:
    """
    Calculate profit amount for a given base price using Profit Rules.
    
    Selection logic:
    1. Find all active rules where min_price <= base_price <= max_price (or max_price is None)
    2. If multiple rules match, select the one with highest priority
    3. If no rule matches, return 0 profit
    
    Args:
        base_price: Base product price in GMD (before profit)
    
    Returns:
        Tuple of (profit_amount in GMD, profit_rule_id or None)
    """
    if base_price is None or base_price < 0:
        return 0.0, None
    
    # Find all matching active rules
    matching_rules = ProfitRule.query.filter(
        ProfitRule.is_active == True,
        ProfitRule.min_price <= base_price
    ).all()
    
    # Filter rules where max_price is None (no upper limit) or base_price <= max_price
    valid_rules = []
    for rule in matching_rules:
        if rule.max_price is None:
            valid_rules.append(rule)
        elif float(rule.max_price) >= base_price:
            valid_rules.append(rule)
    
    if not valid_rules:
        return 0.0, None
    
    # Sort by priority (highest first), then by min_price (lowest first) for consistency
    valid_rules.sort(key=lambda r: (-r.priority, float(r.min_price)))
    
    # Return the highest priority rule
    selected_rule = valid_rules[0]
    profit_amount = float(selected_rule.profit_amount) if selected_rule.profit_amount else 0.0
    
    return profit_amount, selected_rule.id

def get_product_price_with_profit(base_price: float) -> Tuple[float, float, Optional[int]]:
    """
    Get final product price with profit applied.
    
    Args:
        base_price: Base product price in GMD (before profit)
    
    Returns:
        Tuple of (final_price_in_gmd, profit_amount, profit_rule_id)
    """
    profit_amount, profit_rule_id = calculate_profit_for_price(base_price)
    final_price = base_price + profit_amount
    return final_price, profit_amount, profit_rule_id

def calculate_cart_total_weight(cart_items: List[Dict[str, any]], default_weight: float = 0.0) -> float:
    """
    Calculate total weight of cart items.
    Products MUST have weight set - no default fallback.
    
    Args:
        cart_items: List of cart items with product info
        default_weight: Not used - kept for compatibility but products must have weight
    
    Returns:
        Total weight in kilograms (0.0 if products missing weight)
    """
    total_weight = Decimal('0.0')
    
    for item in cart_items:
        product_id = item.get('id') or item.get('product_id')
        quantity = item.get('quantity', 1)
        
        if product_id:
            product = Product.query.get(product_id)
            if product and product.weight_kg and product.weight_kg > 0:
                weight = Decimal(str(product.weight_kg)) * quantity
                total_weight += weight
            else:
                # Product missing weight - log error but don't add weight
                current_app.logger.error(
                    f"Product {product_id} ({product.name if product else 'Unknown'}) has no valid weight. "
                    f"Skipping weight for this item. Please set weight in admin."
                )
                # Don't add weight - product must have weight set
    
    return float(total_weight)

@login_manager.user_loader
def load_user(user_id):
    """
    Load user from database.
    
    CRITICAL: For database-free routes, returns None without any database access.
    This ensures health checks and monitoring never trigger database connections.
    """
    from flask import has_request_context, request
    # Skip database access for database-free routes
    if has_request_context() and is_database_free_route(request.path, request):
        return None
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

# ======================
# FORCE ONBOARDING - TOP PRIORITY
# This runs BEFORE every single request
# ======================
@app.before_request
def force_onboarding_for_new_users():
    """
    ABSOLUTE PRIORITY: Force onboarding for ALL new users
    
    If a user has NOT completed onboarding, they MUST see the onboarding page first.
    This applies to EVERY page - home, login, products, cart, everything.
    
    Onboarding flow:
    1. Slide 1: Welcome slide
    2. Slide 2: What You Can Buy slide  
    3. Slide 3: Country and Language Setup
    4. After completion: Redirect to Sign In
    
    CRITICAL: Skips ALL database-free routes to ensure no database access.
    """
    # Skip for database-free routes (health checks, monitoring, static files, etc.)
    if is_database_free_route(request.path, request):
        return None
    
    # Skip for other non-user routes
    skip_paths = [
        '/onboarding',
        '/clear-onboarding',
        '/check-onboarding',
        '/api/',
        '/admin/',  # Admin has its own auth
        '/china/',  # China partner has its own auth
        '/gambia/', # Gambia team has its own auth
    ]
    
    # Check if current path should be skipped
    for skip_path in skip_paths:
        if request.path.startswith(skip_path):
            return None  # Don't redirect, proceed normally
    
    # Check if onboarding is completed (cookie OR session)
    onboarding_completed = (
        request.cookies.get('buxin_onboarding_completed') == 'true' or
        session.get('onboarding_completed') == True
    )
    
    # If onboarding NOT completed, FORCE redirect to onboarding
    if not onboarding_completed:
        app.logger.info(f"[ONBOARDING] New user detected on {request.path} - redirecting to onboarding")
        return redirect(url_for('onboarding'))
    
    # Onboarding completed, proceed normally
    return None

# Routes
# Onboarding routes
@app.route('/onboarding')
def onboarding():
    """Show onboarding flow for first-time users
    
    IMPORTANT: This route should ALWAYS show the onboarding page when accessed directly.
    The redirect logic to FORCE users to see onboarding is in other routes (home, login, etc.),
    NOT here. If a user navigates to /onboarding, they want to see it regardless of completion status.
    """
    # Support reset parameter to force clear onboarding data (for testing or re-onboarding)
    reset_requested = request.args.get('reset') == '1'
    
    if reset_requested:
        # Clear all onboarding-related session data
        session.pop('onboarding_completed', None)
        session.pop('selected_country_code', None)
        session.pop('selected_language', None)
        session.pop('user_address', None)
        session.pop('country_id', None)
        session.pop('currency', None)
        session.pop('currency_symbol', None)
        session.pop('lang', None)
    
    # Get active countries for the selector
    countries = Country.query.filter_by(is_active=True).order_by(Country.name).all()
    
    # If no countries in DB, use default list
    if not countries:
        from .data.world_countries import WORLD_COUNTRIES
        countries = [
            type('Country', (), {
                'code': c['code'],
                'name': c['name'],
                'currency': c['currency'],
                'language': c['language']
            })()
            for c in WORLD_COUNTRIES if c.get('is_active', False)
        ]
    
    # Always show onboarding page - NO REDIRECT HERE
    # The onboarding page itself should always be accessible
    resp = make_response(render_template('onboarding.html', countries=countries))
    
    # If reset was requested, clear the cookie too
    if reset_requested:
        resp.set_cookie('buxin_onboarding_completed', '', expires=0)  # Clear the cookie
    
    return resp

@app.route('/onboarding/complete', methods=['POST'])
@csrf.exempt  # Exempt from CSRF - onboarding uses JavaScript fetch without CSRF token
def onboarding_complete():
    """Handle onboarding completion"""
    country_code = request.form.get('country')
    language = request.form.get('language')
    address = request.form.get('address', '')
    
    if not country_code or not language:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': 'Please select your country and language'})
        flash('Please select your country and language', 'error')
        return redirect(url_for('onboarding'))
    
    # Save preferences to session
    session['onboarding_completed'] = True
    session['selected_country_code'] = country_code
    session['selected_language'] = language
    if address:
        session['user_address'] = address
    
    # Try to find and set the country
    country = Country.query.filter_by(code=country_code, is_active=True).first()
    if country:
        session['country_id'] = country.id
        session['currency'] = country.currency
        session['currency_symbol'] = country.currency_symbol or country.currency
    
    # Set language preference
    session['lang'] = language
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        # For AJAX requests, return JSON AND set the cookie server-side for reliability
        resp = make_response(jsonify({
            'success': True,
            'message': 'Setup complete!',
            'redirect': url_for('login', from_onboarding='1')
        }))
        resp.set_cookie(
            'buxin_onboarding_completed',
            'true',
            max_age=31536000,
            secure=False,
            httponly=False,
            samesite='Lax'
        )
        return resp
    
    # Create response with cookie to persist onboarding completion
    resp = make_response(redirect(url_for('login')))
    resp.set_cookie(
        'buxin_onboarding_completed',
        'true',
        max_age=31536000,
        secure=False,
        httponly=False,
        samesite='Lax'
    )
    return resp

@app.route('/check-onboarding')
def check_onboarding():
    """API endpoint to check if user needs onboarding - DEBUG VERSION"""
    cookie_value = request.cookies.get('buxin_onboarding_completed')
    session_value = session.get('onboarding_completed')
    
    return jsonify({
        'completed': bool(cookie_value == 'true' or session_value == True),
        'debug': {
            'cookie_raw': cookie_value,
            'cookie_is_true': cookie_value == 'true',
            'session_raw': session_value,
            'session_is_true': session_value == True,
            'all_cookies': dict(request.cookies),
            'before_request_active': True,  # Confirms code is deployed
            'version': '2024-12-03-v2'  # Version tag to confirm deployment
        }
    })

@app.route('/clear-onboarding')
def clear_onboarding():
    """Clear onboarding status and redirect to onboarding page - for testing"""
    # Clear session data
    session.pop('onboarding_completed', None)
    session.pop('selected_country_code', None)
    session.pop('selected_language', None)
    session.pop('user_address', None)
    session.pop('country_id', None)
    session.pop('currency', None)
    session.pop('currency_symbol', None)
    session.pop('lang', None)
    
    # Create response that clears the cookie and redirects to onboarding
    resp = make_response(redirect(url_for('onboarding')))
    resp.set_cookie('buxin_onboarding_completed', '', expires=0)
    resp.set_cookie('whatsapp_popup_dismissed', '', expires=0)
    return resp

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
    
    # Onboarding check is now handled globally by @app.before_request (force_onboarding_for_new_users)
    # No need to check here - the global handler ensures users complete onboarding first
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        # Support both username and email login
        user = User.query.filter(
            db.or_(User.username == username, User.email == username)
        ).first()
        
        if user and user.check_password(password):
            # Don't allow China/Gambia team users to login through main login
            if user.role in ['china_partner', 'gambia_team']:
                flash('Please use the appropriate login page for your role', 'error')
                return render_template('auth/signin.html')
            
            login_user(user)
            user.last_login_at = datetime.utcnow()
            ensure_user_profile(user)
            db.session.commit()
            merge_carts(user, session.get('cart'))
            
            # Mark onboarding as completed after successful login
            session['onboarding_completed'] = True
            
            next_page = request.args.get('next')
            if user.is_admin or user.role == 'admin':
                redirect_url = next_page or url_for('admin_dashboard')
            else:
                redirect_url = next_page or url_for('home')
            
            # Create response with onboarding completed cookie
            response = make_response(redirect(redirect_url))
            response.set_cookie('buxin_onboarding_completed', 'true', max_age=31536000, secure=False, httponly=False, samesite='Lax')
            return response
        else:
            flash('Invalid username or password', 'error')
    
    # Use the futuristic sign-in template
    return render_template('auth/signin.html')

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
                            <h2 style="color: #06b6d4;">buxin store</h2>
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
    session['onboarding_completed'] = True
    session.permanent = True
    next_url = session.pop('next_url', None)
    
    # Create response with onboarding completed cookie
    response = make_response(redirect(next_url or url_for('home')))
    response.set_cookie('buxin_onboarding_completed', 'true', max_age=31536000, secure=False, httponly=False, samesite='Lax')
    return response

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    
    # Check if user should see onboarding first
    onboarding_completed = session.get('onboarding_completed') or request.cookies.get('buxin_onboarding_completed')
    if not onboarding_completed and not request.args.get('skip_onboarding'):
        return redirect(url_for('onboarding'))
        
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
                welcome_message = f"Hi {user_name} 👋\nWelcome to buxin store! You'll now receive updates about our robotics and AI innovations. 🚀"
                
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
        # Redirect to login with onboarding flag to preserve onboarding completion
        response = make_response(redirect(url_for('login', from_onboarding='1')))
        response.set_cookie('buxin_onboarding_completed', 'true', max_age=31536000, secure=False, httponly=False, samesite='Lax')
        return response
    
    # Use the futuristic sign-up template
    return render_template('auth/signup.html')

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
def inject_pending_payment_notifications():
    """
    Inject pending payment notification count for authenticated users.
    Shows count of pending manual payments waiting for approval.
    """
    from flask import request, has_request_context
    from flask_login import current_user
    
    # Skip for database-free routes
    if has_request_context() and is_database_free_route(request.path, request):
        return {'pending_payment_count': 0}
    
    # Only for authenticated users
    if current_user.is_authenticated:
        try:
            from app.payments.models import ManualPayment
            count = ManualPayment.query.filter_by(
                user_id=current_user.id,
                status='pending'
            ).count()
            return {'pending_payment_count': count}
        except Exception:
            return {'pending_payment_count': 0}
    
    return {'pending_payment_count': 0}

@app.context_processor
def inject_wishlist_context():
    """
    Inject wishlist context into templates.
    
    CRITICAL: For database-free routes, returns empty wishlist without any database access.
    """
    from flask import request, has_request_context
    
    # Skip ALL database access for database-free routes
    if has_request_context() and is_database_free_route(request.path, request):
        return {
            'wishlist_product_ids': [],
            'wishlist_count': 0
        }
    
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

# ======================
# Shipping API Endpoints
# ======================

@app.route('/api/profit/calculate', methods=['GET'])
@csrf.exempt
def api_calculate_profit():
    """API endpoint to calculate profit for a given base price."""
    try:
        base_price = request.args.get('base_price', type=float)
        if base_price is None or base_price < 0:
            return jsonify({
                'success': False,
                'error': 'Invalid base price. Must be a positive number.'
            }), 400
        
        final_price, profit_amount, profit_rule_id = get_product_price_with_profit(base_price)
        
        return jsonify({
            'success': True,
            'base_price': base_price,
            'profit_amount': profit_amount,
            'final_price': final_price,
            'rule_id': profit_rule_id
        })
    except Exception as e:
        current_app.logger.error(f'Error calculating profit: {e}')
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/shipping/rules', methods=['GET'])
def api_shipping_rules():
    """Get shipping rules with optional filters."""
    country_id = request.args.get('country_id', type=int)
    weight = request.args.get('weight', type=float)
    rule_type = request.args.get('type', '')  # 'country' or 'global'
    
    query = LegacyShippingRule.query.filter_by(status=True)
    
    if rule_type:
        query = query.filter(LegacyShippingRule.rule_type == rule_type)
    
    if country_id:
        query = query.filter(
            db.or_(
                LegacyShippingRule.country_id == country_id,
                LegacyShippingRule.rule_type == 'global'
            )
        )
    
    if weight is not None:
        weight_decimal = Decimal(str(weight))
        query = query.filter(
            LegacyShippingRule.min_weight <= weight_decimal,
            LegacyShippingRule.max_weight >= weight_decimal
        )
    
    rules = query.order_by(LegacyShippingRule.priority.desc(), LegacyShippingRule.min_weight.asc()).all()
    
    return jsonify({
        'success': True,
        'rules': [rule.to_dict() for rule in rules]
    })

@app.route('/api/shipping/estimate', methods=['POST'])
@csrf.exempt
def api_shipping_estimate():
    """Calculate shipping estimate for given country and weight."""
    try:
        data = request.get_json() if request.is_json else request.form.to_dict()
        
        # Ensure country_id is always an integer or None - never a string
        country_id = data.get('country_id')
        if country_id is not None:
            try:
                country_id = int(country_id)
            except (ValueError, TypeError):
                country_id = None
        
        weight = data.get('weight')
        if weight:
            try:
                weight = float(weight)
            except (ValueError, TypeError):
                weight = 0.0
        else:
            weight = 0.0
        
        # Get shipping method if provided
        shipping_mode_key = data.get('shipping_mode_key')
        if shipping_mode_key:
            shipping_mode_key = shipping_mode_key.strip() or None
        
        # Calculate shipping
        result = calculate_shipping_price(weight, country_id, shipping_mode_key)
        
        if result:
            # Include comprehensive debug info in API response
            debug_info = result.get('debug_info', {})
            # Ensure result is a dict before accessing
            if not isinstance(result, dict):
                return jsonify({
                    'success': False,
                    'error': 'Invalid shipping result format'
                }), 500
            
            return jsonify({
                'success': True,
                'available': True,
                'price_gmd': result.get('price_gmd', 0.0),
                'delivery_time': result.get('delivery_time'),
                'shipping_mode_key': result.get('shipping_mode_key'),
                'rule_name': result.get('rule_name', 'Unknown rule'),
                'rule_type': result.get('rule_type', 'unknown'),
                'country_id': result.get('country_id'),
                'country_name': result.get('country_name'),
                'rule': result.get('rule').to_dict() if result.get('rule') else None,
                'debug_info': debug_info
            })
        else:
            # No rule matched - return 0 shipping with debug info
            # NO nearest rule logic - only exact weight range matches allowed
            return jsonify({
                'success': True,
                'available': False,
                'price_gmd': 0.0,
                'message': f'No shipping rule found for country_id={country_id}, weight={weight}kg, method={shipping_mode_key or "any"}. Shipping fee = 0.',
                'debug_info': {
                    'chosen_country_id': country_id,
                    'weight_kg': weight,
                    'applied_rule_id': None,
                    'weight_range_matched': None,
                    'final_shipping_price': 0.0,
                    'rule_selection_reason': 'No matching rule found in Shipping Rules table',
                    'other_shipping_sources_used': False,
                    'shipping_source': 'Shipping Rules Table ONLY',
                    'confirmation': 'No other shipping sources used - ONLY Shipping Rules table checked',
                    'shipping_mode_key': shipping_mode_key
                }
            }), 200
    
    except Exception as e:
        current_app.logger.error(f'Error calculating shipping estimate: {e}')
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/shipping/methods', methods=['POST'])
@csrf.exempt
def api_shipping_methods():
    """Get all available shipping methods with prices for a given weight and country."""
    try:
        from app.shipping import get_all_shipping_methods, get_shipping_method
        
        data = request.get_json() if request.is_json else request.form.to_dict()
        
        # Ensure country_id is always an integer or None - never a string
        country_id = data.get('country_id')
        if country_id is not None:
            try:
                country_id = int(country_id)
            except (ValueError, TypeError):
                country_id = None
        
        weight = data.get('weight')
        if weight:
            try:
                weight = float(weight)
            except (ValueError, TypeError):
                weight = 0.0
        else:
            weight = 0.0
        
        # Get all shipping methods
        all_methods = get_all_shipping_methods()
        methods_with_prices = []
        
        # Map old method IDs to new shipping_mode_key values
        method_mapping = {
            'ecommerce': 'economy_plus',
            'express': 'express',
            'economy': 'economy'
        }
        
        # Calculate price for each method
        for method in all_methods:
            method_id = method['id']
            # Map to shipping_mode_key (new system uses economy_plus instead of ecommerce)
            shipping_mode_key = method_mapping.get(method_id, method_id)
            result = calculate_shipping_price(weight, country_id, shipping_mode_key, default_weight=0.0)
            
            # Ensure result is a dict and has the expected structure
            if result and isinstance(result, dict) and result.get('available'):
                methods_with_prices.append({
                    'id': method_id,
                    'label': method['label'],
                    'short_label': method['short_label'],
                    'description': method['description'],
                    'guarantee': method['guarantee'],
                    'notes': method['notes'],
                    'color': method['color'],
                    'icon': method['icon'],
                    'price_gmd': result.get('price_gmd', 0.0),
                    'delivery_time': result.get('delivery_time') or method.get('guarantee', ''),
                    'available': True,
                    'rule_id': result.get('rule_id') if result.get('rule_id') else (result.get('rule').id if result.get('rule') else None)
                })
            else:
                # Method not available for this weight/country
                methods_with_prices.append({
                    'id': method_id,
                    'label': method['label'],
                    'short_label': method['short_label'],
                    'description': method['description'],
                    'guarantee': method['guarantee'],
                    'notes': method['notes'],
                    'color': method['color'],
                    'icon': method['icon'],
                    'price_gmd': None,
                    'delivery_time': method['guarantee'],
                    'available': False,
                    'rule_id': None
                })
        
        return jsonify({
            'success': True,
            'methods': methods_with_prices,
            'weight_kg': weight,
            'country_id': country_id
        })
        
    except Exception as e:
        current_app.logger.error(f'Error getting shipping methods: {e}')
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

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

            # Get base price and apply profit
            base_price = Decimal(str(product.price))
            final_price, profit_amount, profit_rule_id = get_product_price_with_profit(float(base_price))
            final_price_decimal = Decimal(str(final_price))
            
            line_total = (final_price_decimal * quantity).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            subtotal += line_total

            cart_items.append({
                'id': product.id,
                'name': product.name,
                'price': float(final_price_decimal),  # Final price with profit
                'base_price': float(base_price),  # Base price for reference
                'profit_amount': float(profit_amount),  # Profit amount
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

        # Get base price and apply profit
        base_price = Decimal(str(product.price))
        final_price, profit_amount, profit_rule_id = get_product_price_with_profit(float(base_price))
        final_price_decimal = Decimal(str(final_price))
        
        line_total = (final_price_decimal * quantity).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        subtotal += line_total

        sanitized_cart[str(product.id)] = quantity
        cart_items.append({
            'id': product.id,
            'name': product.name,
            'price': float(final_price_decimal),  # Final price with profit
            'base_price': float(base_price),  # Base price for reference
            'profit_amount': float(profit_amount),  # Profit amount
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
    Prices are converted to the current country's currency.
    Shipping is now calculated using the shipping rules system based on total cart weight and country.
    """
    from .utils.currency_rates import convert_price, get_currency_symbol, parse_price
    
    # Get current country for currency conversion
    country = get_current_country()
    to_currency = country.currency if country else 'GMD'
    currency_symbol = get_currency_symbol(to_currency) if country else 'D'
    country_id = country.id if country else None
    
    tax_rate = Decimal(str(current_app.config.get('CART_TAX_RATE', 0) or 0))

    subtotal = Decimal('0.00')
    total_weight = Decimal('0.00')
    has_local_gambia_products = False
    has_china_products = False
    
    # Calculate subtotal and total weight
    # CRITICAL: Separate local Gambian products from China products for shipping calculation
    for item in cart_items:
        # Ensure item is a dictionary, not a string
        if not isinstance(item, dict):
            current_app.logger.error(f"Invalid cart item type: {type(item)}, value: {item}")
            continue
        
        # Parse and convert prices to current currency
        numeric_price, _ = parse_price(item.get('price', 0))
        base_price = Decimal(str(numeric_price))
        converted_price = Decimal(str(convert_price(float(base_price), 'GMD', to_currency)))
        item['price'] = float(converted_price)  # Update item price to converted value
        
        item_subtotal = Decimal(str(converted_price * Decimal(str(item['quantity']))))
        subtotal += item_subtotal
        
        # Check if product is available in Gambia (local product - no shipping)
        product_id = item.get('id')
        if product_id:
            product = Product.query.get(product_id)
            if product:
                # Mark item as local or China product
                if product.available_in_gambia:
                    has_local_gambia_products = True
                    item['is_local_gambia'] = True
                    # Local products have no shipping - skip weight calculation for shipping
                else:
                    has_china_products = True
                    item['is_local_gambia'] = False
                    # Only calculate weight for China products (for shipping calculation)
                    if product.weight_kg and product.weight_kg > 0:
                        item_weight = Decimal(str(product.weight_kg)) * Decimal(str(item['quantity']))
                        total_weight += item_weight
                    else:
                        # Product missing weight - log error but continue with 0 weight
                        current_app.logger.error(
                            f"Product {product_id} ({product.name}) has no valid weight. "
                            f"Skipping weight for this item. Please set weight in admin."
                        )
            else:
                # Product not found - treat as China product (will need shipping)
                has_china_products = True
                item['is_local_gambia'] = False
    
    # CRITICAL: If cart contains ONLY local Gambian products, shipping is always 0
    # If cart contains a mix, only calculate shipping for China products (total_weight already excludes local products)
    if has_local_gambia_products and not has_china_products:
        # All products are local - no shipping needed
        total_shipping_gmd = Decimal('0.00')
        shipping_delivery_time = None
        shipping_rule_name = None
        shipping_result = None
        current_app.logger.info("Cart contains only local Gambian products - shipping set to 0")
    else:
        # Cart has China products (or mix) - calculate shipping for China products only
        # Get selected shipping method from session
        selected_shipping_mode_key = session.get('selected_shipping_method')
        
        # Calculate shipping using shipping rules system (based on total cart weight and selected method)
        # Only calculate if there are China products (total_weight > 0)
        if total_weight > 0:
            shipping_result = calculate_shipping_price(float(total_weight), country_id, selected_shipping_mode_key, default_weight=0.0)
        else:
            shipping_result = None
    
        # Initialize shipping variables
        total_shipping_gmd = Decimal('0.00')
        shipping_delivery_time = None
        shipping_rule_name = None
        
        if shipping_result and isinstance(shipping_result, dict) and shipping_result.get('available'):
            total_shipping_gmd = Decimal(str(shipping_result.get('price_gmd', 0.0)))
            shipping_delivery_time = shipping_result.get('delivery_time')
            shipping_rule_name = shipping_result.get('rule_name', 'Unknown rule')
            shipping_debug_info = shipping_result.get('debug_info', {})
            
            # Admin debug output (temporary)
            if current_user.is_authenticated and (current_user.is_admin or current_user.role == 'admin'):
                # Calculate item details for debug
                item_details = []
                for item in cart_items:
                    product_id = item.get('id')
                    if product_id:
                        product = Product.query.get(product_id)
                        if product:
                            item_weight = float(product.weight_kg) if product.weight_kg else 0.0
                            item_details.append({
                                'product_id': product_id,
                                'product_name': product.name,
                                'weight_kg': item_weight,
                                'quantity': item['quantity'],
                                'total_weight': item_weight * item['quantity'],
                                'is_local_gambia': product.available_in_gambia
                            })
                
                current_app.logger.info(
                    f"🔍 ADMIN DEBUG - Cart Shipping: "
                    f"Total Weight={float(total_weight)}kg (China products only), "
                    f"Country={country.name if country else 'None'}, "
                    f"Rule ID={shipping_debug_info.get('applied_rule_id')}, "
                    f"Shipping Fee={float(total_shipping_gmd)}GMD, "
                    f"Has Local Products={has_local_gambia_products}, "
                    f"Has China Products={has_china_products}, "
                    f"Items={item_details}, "
                    f"Debug Info={shipping_debug_info}"
                )
            
            current_app.logger.debug(
                f"Cart shipping calculated: Rule={shipping_rule_name}, "
                f"Country ID={country_id}, Weight={float(total_weight)}kg (China products only), "
                f"Shipping={float(total_shipping_gmd)}GMD"
            )
        else:
            # If no rule found, default to 0 (as requested)
            current_app.logger.debug(
                f"No shipping rule found for cart: "
                f"Country ID={country_id}, Weight={float(total_weight)}kg. "
                f"Shipping fee defaulted to 0"
            )
    
    # Convert shipping to display currency if needed
    if country and country.currency != 'GMD':
        shipping_display = Decimal(str(convert_price(float(total_shipping_gmd), 'GMD', to_currency)))
    else:
        shipping_display = total_shipping_gmd
    
    # If shipping was calculated by rules (not per-item), distribute it proportionally
    # CRITICAL: Only distribute shipping to China products, not local Gambian products
    if shipping_result and shipping_result.get('available') and total_weight > 0:
        # Distribute total shipping proportionally by item weight (only for China products)
        for item in cart_items:
            # Skip local Gambian products - they have no shipping
            if item.get('is_local_gambia'):
                item['shipping'] = 0.0
                item['shipping_per_unit'] = 0.0
                continue
            
            # Only calculate shipping for China products
            product_id = item.get('id')
            if product_id:
                product = Product.query.get(product_id)
                if product and product.weight_kg and product.weight_kg > 0:
                    item_weight = Decimal(str(product.weight_kg))
                    item_weight_total = item_weight * Decimal(str(item['quantity']))
                    weight_ratio = item_weight_total / total_weight if total_weight > 0 else Decimal('0')
                    item_shipping = (shipping_display * weight_ratio).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                    item['shipping'] = float(item_shipping)
                    item['shipping_per_unit'] = float(item_shipping / Decimal(str(item['quantity']))) if item['quantity'] > 0 else 0.0
                else:
                    # Product without weight - no shipping assigned
                    item['shipping'] = 0.0
                    item['shipping_per_unit'] = 0.0
    else:
        # No shipping calculated - set all items to 0 shipping
        for item in cart_items:
            item['shipping'] = 0.0
            item['shipping_per_unit'] = 0.0

    tax = (subtotal * tax_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if tax_rate else Decimal('0.00')
    shipping = shipping_display.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    total = subtotal + tax + shipping

    return {
        'currency': currency_symbol,
        'subtotal': subtotal,
        'tax': tax,
        'shipping': shipping,
        'total': total,
        'shipping_delivery_time': shipping_delivery_time,
        'has_local_gambia_products': has_local_gambia_products,
        'has_china_products': has_china_products,
        'is_all_local': has_local_gambia_products and not has_china_products
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
        'has_local_gambia_products': totals.get('has_local_gambia_products', False),
        'has_china_products': totals.get('has_china_products', False),
        'is_all_local': totals.get('is_all_local', False)
    }

    summary['checkout'] = {
        'items': serialized_items,
        'currency': totals['currency'],
        'subtotal': summary['subtotal'],
        'tax': summary['tax'],
        'shipping': summary['shipping'],
        'total': summary['total'],
        'count': cart_count,
        'is_empty': is_empty,
        'has_local_gambia_products': totals.get('has_local_gambia_products', False),
        'has_china_products': totals.get('has_china_products', False),
        'is_all_local': totals.get('is_all_local', False)
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
    
    # Get available shipping methods for display
    from app.shipping import get_all_shipping_methods
    shipping_methods = get_all_shipping_methods()
    selected_shipping_method = session.get('selected_shipping_method')
    
    return render_template('cart.html', 
                         cart_items=cart_items, 
                         cart_summary=summary,
                         shipping_methods=shipping_methods,
                         selected_shipping_method=selected_shipping_method)

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
    
    # Check if cart is empty before proceeding
    try:
        cart_items, _ = update_cart()
        if not cart_items:
            flash('Your cart is empty. Please add items before checkout.', 'warning')
            return redirect(url_for('cart'))
    except Exception as e:
        app.logger.error(f'Error checking cart in cart_proceed: {str(e)}', exc_info=True)
        flash('An error occurred while processing your cart. Please try again.', 'error')
        return redirect(url_for('cart'))

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

@app.route('/api/cart/select-shipping-method', methods=['POST'])
@csrf.exempt
def api_select_shipping_method():
    """Save selected shipping method in session and recalculate shipping."""
    try:
        data = request.get_json() if request.is_json else request.form.to_dict()
        shipping_mode_key = data.get('shipping_mode_key', '').strip()
        
        if not shipping_mode_key:
            return jsonify({'success': False, 'message': 'Shipping method is required'}), 400
        
        # Validate shipping mode key - check against database
        from app.shipping.models import ShippingMode
        valid_method = ShippingMode.query.filter_by(key=shipping_mode_key, active=True).first()
        if not valid_method:
            # Get list of valid keys for error message
            valid_methods = ShippingMode.query.filter_by(active=True).all()
            valid_keys = [m.key for m in valid_methods]
            return jsonify({'success': False, 'message': f'Invalid shipping method. Must be one of: {", ".join(valid_keys) if valid_keys else "No active shipping methods available"}'}), 400
        
        # Save to session
        session['selected_shipping_method'] = shipping_mode_key
        session.modified = True
        
        current_app.logger.info(f'Shipping method selected: {shipping_mode_key} for user {current_user.id if current_user.is_authenticated else "guest"}')
        
        # Recalculate cart summary with new shipping method
        # Use update_cart() to get properly formatted cart items (list of dicts)
        cart_items, _ = update_cart()
        summary = serialize_cart_summary(cart_items)
        
        # CRITICAL: Save the shipping price from cart calculation to session
        # This ensures checkout uses the same shipping price instead of recalculating
        if 'checkout' in summary and 'shipping' in summary['checkout']:
            session['cart_shipping_price'] = summary['checkout']['shipping']
            session['cart_total'] = summary['checkout']['total']
            session['cart_subtotal'] = summary['checkout']['subtotal']
            session.modified = True
            current_app.logger.info(f'Cart shipping price saved to session: {summary["checkout"]["shipping"]}, total: {summary["checkout"]["total"]}')
        
        return jsonify({
            'success': True,
            'message': 'Shipping method selected successfully',
            'cart': summary,
            'selected_shipping_method': shipping_mode_key
        })
    except Exception as e:
        current_app.logger.error(f'Error selecting shipping method: {e}', exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500

# ======================
# Country Localization API Routes
# ======================

def get_current_country():
    """
    Get the current user's selected country from session or user profile.
    
    CRITICAL: For database-free routes, returns None without any database access.
    This ensures health checks, monitoring, and public assets never trigger database connections.
    """
    from flask import request, has_request_context
    
    # Skip ALL database access for database-free routes
    if has_request_context() and is_database_free_route(request.path, request):
        return None
    
    try:
        # First, try to get from authenticated user's profile
        if current_user.is_authenticated and current_user.country_id:
            country = Country.query.get(current_user.country_id)
            if country and country.is_active:
                return country
        
        # Fall back to session
        country_id = session.get('selected_country_id')
        if country_id:
            # Ensure country_id is always an integer - never a string
            try:
                country_id = int(country_id)
            except (ValueError, TypeError):
                country_id = None
            if country_id:
                country = Country.query.get(country_id)
            if country and country.is_active:
                return country
        
        # Default: return first active country or None
        default_country = Country.query.filter_by(is_active=True).first()
        return default_country
    except Exception as e:
        current_app.logger.error(f"Error getting current country: {e}")
        return None

def is_user_in_gambia():
    """Check if the user's selected country is Gambia (code 'GM').
    Returns True if user is in Gambia, False otherwise.
    """
    try:
        country = get_current_country()
        if country and country.code:
            return country.code.upper() == 'GM'
        return False
    except Exception as e:
        current_app.logger.error(f"Error checking if user is in Gambia: {e}")
        return False

def get_product_base_query(include_gambia_products=None):
    """Get base product query with optional Gambia product filtering.
    
    Args:
        include_gambia_products: If None, auto-detect based on user's country.
                                If True, include Gambia products.
                                If False, exclude Gambia products.
    """
    query = Product.query
    
    # Auto-detect if not explicitly specified
    if include_gambia_products is None:
        include_gambia_products = is_user_in_gambia()
    
    # If user is NOT in Gambia, exclude Gambia-only products
    if not include_gambia_products:
        query = query.filter(Product.available_in_gambia == False)
    
    return query

def get_categories_with_counts(include_gambia_products=None):
    """Get categories with product counts, optionally excluding Gambia-only categories.
    
    Args:
        include_gambia_products: If None, auto-detect based on user's country.
    """
    # Auto-detect if not explicitly specified
    if include_gambia_products is None:
        include_gambia_products = is_user_in_gambia()
    
    if include_gambia_products:
        # User is in Gambia - show all products and categories
        categories_query = db.session.query(
            Category,
            db.func.count(Product.id).label('product_count')
        ).outerjoin(Product, Category.id == Product.category_id)
        categories = categories_query.group_by(Category.id).all()
    else:
        # User is NOT in Gambia - only count non-Gambia products
        categories_query = db.session.query(
            Category,
            db.func.count(Product.id).label('product_count')
        ).outerjoin(
            Product, 
            db.and_(
                Category.id == Product.category_id,
                Product.available_in_gambia == False
            )
        )
        categories = categories_query.group_by(Category.id).all()
    
    # Format categories and filter out those with 0 products (if not in Gambia)
    categories_with_counts = []
    for category, product_count in categories:
        count = int(product_count) if product_count is not None else 0
        # If user is not in Gambia and category has 0 non-Gambia products, skip it
        if not include_gambia_products and count == 0:
            continue
        categories_with_counts.append({
            'id': category.id,
            'name': category.name,
            'count': count,
            'icon': getattr(category, 'icon', 'box'),
            'gradient': getattr(category, 'gradient', 'from-gray-500 to-gray-600'),
            'image': category.image
        })
    
    return categories_with_counts

@app.route('/api/countries', methods=['GET'])
def api_get_countries():
    """Get all active countries."""
    try:
        countries = Country.query.filter_by(is_active=True).order_by(Country.name).all()
        return jsonify({
            'success': True,
            'countries': [country.to_dict() for country in countries]
        })
    except Exception as e:
        current_app.logger.error(f"Error fetching countries: {e}")
        return jsonify({'success': False, 'message': 'Failed to fetch countries'}), 500

@app.route('/api/country/current', methods=['GET'])
def api_get_current_country():
    """Get the current user's selected country."""
    try:
        country = get_current_country()
        if country:
            return jsonify({
                'success': True,
                'country': country.to_dict()
            })
        return jsonify({
            'success': False,
            'message': 'No country selected'
        })
    except Exception as e:
        current_app.logger.error(f"Error getting current country: {e}")
        return jsonify({'success': False, 'message': 'Failed to get current country'}), 500

@app.route('/api/country/select', methods=['POST'])
def api_select_country():
    """Select a country for the current user/session."""
    try:
        data = request.get_json()
        country_id = data.get('country_id')
        
        if not country_id:
            return jsonify({'success': False, 'message': 'Country ID is required'}), 400
        
        # Ensure country_id is always an integer - never a string
        try:
            country_id = int(country_id)
        except (ValueError, TypeError):
            return jsonify({'success': False, 'message': 'Invalid country ID. Must be a number.'}), 400
        
        country = Country.query.get(country_id)
        if not country:
            return jsonify({'success': False, 'message': 'Country not found'}), 404
        
        if not country.is_active:
            return jsonify({'success': False, 'message': 'Country is not active'}), 400
        
        # Save to user profile if authenticated
        if current_user.is_authenticated:
            current_user.country_id = country_id
            db.session.commit()
        
        # Also save to session
        session['selected_country_id'] = country_id
        session['country_selected'] = True  # Flag to prevent pop-up from showing again
        session['language'] = country.language  # Set language for Babel
        session['currency'] = country.currency  # Set currency for conversion
        session.permanent = True
        
        return jsonify({
            'success': True,
            'message': 'Country selected successfully',
            'country': country.to_dict()
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error selecting country: {e}")
        return jsonify({'success': False, 'message': 'Failed to select country'}), 500

# ... rest of the code remains the same ...
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, SelectField, TextAreaField, SubmitField, IntegerField, FloatField, BooleanField
from wtforms.validators import DataRequired, Email, NumberRange

class CheckoutForm(FlaskForm):
    full_name = StringField('Full Name', validators=[DataRequired(message='Full name is required')])
    email = StringField('Email', validators=[DataRequired(message='Email is required'), Email(message='Please enter a valid email address')])
    phone = StringField('Phone Number', validators=[DataRequired(message='Phone number is required')])
    country = StringField('Country', validators=[DataRequired(message='Country is required')])
    city = StringField('City / Region', validators=[DataRequired(message='City or region is required')])
    delivery_address = TextAreaField('Full Delivery Address', validators=[DataRequired(message='Delivery address is required')])
    delivery_notes = TextAreaField('Delivery Notes (Optional)', validators=[])
    submit = SubmitField('Proceed to Payment')


# REMOVED: calculate_delivery_price() function
# All shipping calculations now come ONLY from Shipping Rules table at /admin/shipping
# This function has been removed to prevent using old shipping sources

class ProductForm(FlaskForm):
    name = StringField('Product Name', validators=[DataRequired()])
    description = TextAreaField('Description', validators=[DataRequired()])
    price = FloatField('Price', validators=[DataRequired(), NumberRange(min=0.01)])
    stock = IntegerField('Stock', validators=[DataRequired(), NumberRange(min=0)])
    category_id = SelectField('Category', coerce=int, validators=[DataRequired()], choices=[])
    weight_kg = FloatField('Weight (kg)', validators=[
        DataRequired(message='Weight is required'),
        NumberRange(min=0.00001, max=500, message='Weight must be between 0.00001 and 500 kg')
    ])
    image = FileField('Product Image', validators=[
        FileAllowed(['jpg', 'jpeg', 'png'], 'Images only!')
    ])
    available_in_gambia = BooleanField('Available in The Gambia (No Shipping)', default=False)
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
    try:
        cart_items, _ = update_cart()
        
        # Check for empty cart early
        if not cart_items:
            flash('Your cart is empty', 'warning')
            return redirect(url_for('cart'))
        
        cart_summary = serialize_cart_summary(cart_items)
        checkout_summary = cart_summary.get('checkout', {})
        
        # Validate checkout summary
        if not checkout_summary or 'total' not in checkout_summary:
            app.logger.error(f'Invalid checkout summary: {checkout_summary}')
            flash('An error occurred while processing your cart. Please try again.', 'error')
            return redirect(url_for('cart'))
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f'Error processing cart in checkout: {str(e)}', exc_info=True)
        flash('An error occurred while processing your cart. Please try again.', 'error')
        return redirect(url_for('cart'))
    
    # Handle GET request - show checkout page
    if request.method == 'GET':
        try:
            from app.payments.models import PendingPayment
            import json
            
            # Get or create user profile
            profile = ensure_user_profile(current_user)
            
            # Pre-fill user data from profile
            user_phone = profile.phone_number or getattr(current_user, 'phone', '')
            user_email = current_user.email
            full_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip() or current_user.username
            user_country = profile.country or ''
            user_city = profile.city or ''
            user_address = profile.address or ''
            
            # CRITICAL FIX: Use shipping price from cart instead of recalculating
            # The cart already calculated the correct shipping price, so we should use that
            shipping_info = None
            cart_shipping_price = session.get('cart_shipping_price')
            selected_shipping_mode_key = session.get('selected_shipping_method')
            
            # If we have a saved shipping price from cart, use it
            if cart_shipping_price is not None and selected_shipping_mode_key:
                try:
                    country = Country.query.filter_by(name=user_country, is_active=True).first() if user_country else None
                    from .utils.currency_rates import convert_price
                    
                    # Use the shipping price from cart (already in display currency)
                    shipping_price_display = float(cart_shipping_price)
                    
                    # Convert back to GMD if needed for internal calculations
                    if country and country.currency != 'GMD':
                        # shipping_price_display is already in display currency, so we need to convert back
                        # For now, we'll use the display price as-is since it's what the user saw
                        shipping_price_gmd = convert_price(shipping_price_display, country.currency, 'GMD')
                    else:
                        shipping_price_gmd = shipping_price_display
                    
                    # Get delivery time from shipping method (optional, for display)
                    delivery_time = None
                    try:
                        from app.shipping.service import ShippingService
                        if country:
                            total_weight = calculate_cart_total_weight(cart_items, default_weight=0.0)
                            shipping_result = ShippingService.calculate_shipping(
                                country_iso=country.code.upper(),
                                shipping_mode_key=selected_shipping_mode_key,
                                total_weight_kg=float(total_weight)
                            )
                            if shipping_result and shipping_result.get('available'):
                                delivery_time = shipping_result.get('delivery_time')
                    except Exception:
                        pass  # Delivery time is optional
                    
                    shipping_info = {
                        'price_gmd': shipping_price_gmd,
                        'price_display': shipping_price_display,
                        'delivery_time': delivery_time,
                        'rule_name': f"{user_country or 'Global'} ({selected_shipping_mode_key})",
                        'rule': None
                    }
                    
                    current_app.logger.info(f'Checkout using cart shipping price: {shipping_price_display} (method: {selected_shipping_mode_key})')
                except Exception as e:
                    app.logger.warning(f'Error using cart shipping price: {str(e)}')
                    # Fall back to recalculating if there's an error
                    shipping_info = None
            
            # Fallback: Only recalculate if we don't have cart shipping price
            if shipping_info is None and user_country:
                try:
                    country = Country.query.filter_by(name=user_country, is_active=True).first()
                    if country:
                        country_id = country.id
                        total_weight = calculate_cart_total_weight(cart_items, default_weight=0.0)
                        # Get selected shipping method from session, or auto-select one
                        selected_shipping_mode_key = session.get('selected_shipping_method')
                        
                        # If no shipping method is selected, auto-select based on priority (first active method from database)
                        if not selected_shipping_mode_key:
                            from app.shipping.service import ShippingService
                            from app.shipping.models import ShippingMode
                            # Try to find the first available method from database
                            active_methods = ShippingMode.query.filter_by(active=True).order_by(ShippingMode.id).all()
                            for mode in active_methods:
                                mode_key = mode.key
                                test_result = ShippingService.calculate_shipping(
                                    country_iso=country.code.upper(),
                                    shipping_mode_key=mode_key,
                                    total_weight_kg=float(total_weight)
                                )
                                if test_result and test_result.get('available'):
                                    selected_shipping_mode_key = mode_key
                                    session['selected_shipping_method'] = mode_key
                                    break
                        
                        shipping_result = calculate_shipping_price(total_weight, country_id, selected_shipping_mode_key, default_weight=0.0)
                        
                        # Default to 0 if no rule found (as requested)
                        shipping_price_gmd = 0.0
                        shipping_price_display = 0.0
                        delivery_time = None
                        rule_name = None
                        
                        if shipping_result and isinstance(shipping_result, dict) and shipping_result.get('available'):
                            from .utils.currency_rates import convert_price
                            shipping_price_gmd = shipping_result.get('price_gmd', 0.0)
                            rule_name = shipping_result.get('rule_name', 'Unknown rule')
                            delivery_time = shipping_result.get('delivery_time')
                            
                            if country.currency != 'GMD':
                                shipping_price_display = convert_price(shipping_price_gmd, 'GMD', country.currency)
                            else:
                                shipping_price_display = shipping_price_gmd
                        
                        shipping_info = {
                            'price_gmd': shipping_price_gmd,
                            'price_display': shipping_price_display,
                            'delivery_time': delivery_time,
                            'rule_name': rule_name,
                            'rule': shipping_result.get('rule') if shipping_result and isinstance(shipping_result, dict) and shipping_result.get('available') else None
                        }
                except Exception as e:
                    app.logger.warning(f'Error calculating shipping for display: {str(e)}')
                    # Continue without shipping info - it will be calculated on POST
            
            # Create a PendingPayment for this checkout session
            # It will be used when user clicks "Pay Now" via JavaScript
            # Use cart total from session if available to ensure consistency
            cart_total_for_pending = session.get('cart_total') or checkout_summary['total']
            try:
                pending_payment = PendingPayment(
                    user_id=current_user.id,
                    amount=cart_total_for_pending,  # Use cart total to ensure consistency
                    status='waiting',
                    cart_items_json=json.dumps(cart_items)
                )
                db.session.add(pending_payment)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                app.logger.error(f'Error creating pending payment: {str(e)}', exc_info=True)
                raise
            
            # Pre-fill form with user profile data
            form = CheckoutForm(
                full_name=full_name,
                email=user_email,
                phone=user_phone,
                country=user_country,
                city=user_city,
                delivery_address=user_address
            )

            # Get available shipping methods for display
            from app.shipping import get_all_shipping_methods
            shipping_methods = get_all_shipping_methods()
            selected_shipping_method = session.get('selected_shipping_method')
            
            return render_template('checkout.html', 
                                 cart_items=cart_items, 
                                 cart_summary=cart_summary,
                                 checkout_summary=checkout_summary,
                                 total=checkout_summary['total'],
                                 form=form,
                                 user_phone=user_phone,
                                 user_email=user_email,
                                 pending_payment_id=pending_payment.id,
                                 shipping_info=shipping_info,
                                 shipping_methods=shipping_methods,
                                 selected_shipping_method=selected_shipping_method,
                                 has_saved_address=bool(profile.address and profile.city and profile.country))  # Pass pending_payment_id instead of order_id
            
        except Exception as e:
            db.session.rollback()
            app.logger.error(f'Error in checkout GET: {str(e)}', exc_info=True)
            flash('An error occurred while processing your order. Please try again.', 'error')
            return redirect(url_for('cart'))
    
    # Handle POST request - This will now be the primary action from the checkout form
    form = CheckoutForm()
    
    if form.validate_on_submit():
        try:
            from app.payments.services import PaymentService
            from app.payments.models import PendingPayment
            import json
            
            # CRITICAL FIX: Use shipping price from cart instead of recalculating
            # The cart already calculated the correct shipping price, so we should use that
            shipping_mode_key = session.get('selected_shipping_method')
            cart_shipping_price = session.get('cart_shipping_price')
            cart_total = session.get('cart_total')
            cart_subtotal = session.get('cart_subtotal')
            
            # Get user's country for shipping rule lookup
            country = None
            country_id = None
            if form.country.data:
                country = Country.query.filter_by(name=form.country.data, is_active=True).first()
                if country:
                    country_id = country.id
            
            shipping_price_gmd = Decimal('0.00')
            shipping_rule_id = None
            shipping_delivery_estimate = None
            shipping_display_currency = 'GMD'
            
            # Use cart shipping price if available, otherwise recalculate
            if cart_shipping_price is not None and shipping_mode_key:
                # Use the shipping price from cart (already calculated correctly)
                from .utils.currency_rates import convert_price
                
                # cart_shipping_price is in display currency, convert to GMD if needed
                if country and country.currency != 'GMD':
                    shipping_price_gmd = Decimal(str(convert_price(float(cart_shipping_price), country.currency, 'GMD')))
                else:
                    shipping_price_gmd = Decimal(str(cart_shipping_price))
                
                shipping_display_currency = country.currency if country else 'GMD'
                
                # Get delivery time and rule info (optional, for record keeping)
                try:
                    total_weight = calculate_cart_total_weight(cart_items, default_weight=0.0)
                    shipping_result = calculate_shipping_price(total_weight, country_id, shipping_mode_key, default_weight=0.0)
                    if shipping_result and isinstance(shipping_result, dict) and shipping_result.get('available'):
                        shipping_rule_id = shipping_result.get('rule_id') or (shipping_result.get('rule').id if shipping_result.get('rule') else None)
                        shipping_delivery_estimate = shipping_result.get('delivery_time')
                except Exception as e:
                    app.logger.warning(f'Error getting shipping rule info: {str(e)}')
                    # Continue with cart shipping price even if rule lookup fails
                
                current_app.logger.info(f'Checkout POST using cart shipping price: {cart_shipping_price} (GMD: {shipping_price_gmd}), method: {shipping_mode_key}')
            else:
                # Fallback: Recalculate if cart shipping price not available
                current_app.logger.warning('Cart shipping price not found in session, recalculating...')
                
                # Calculate total cart weight
                total_weight = calculate_cart_total_weight(cart_items, default_weight=0.0)
                
                # Get shipping method from form or session
                shipping_mode_key = request.form.get('shipping_mode_key', '').strip() or session.get('selected_shipping_method') or None
                
                # Calculate shipping using shipping rules
                shipping_result = calculate_shipping_price(total_weight, country_id, shipping_mode_key, default_weight=0.0)
                
                if shipping_result and isinstance(shipping_result, dict) and shipping_result.get('available'):
                    shipping_price_gmd = Decimal(str(shipping_result.get('price_gmd', 0.0)))
                    shipping_rule_id = shipping_result.get('rule_id') or (shipping_result.get('rule').id if shipping_result.get('rule') else None)
                    shipping_delivery_estimate = shipping_result.get('delivery_time')
                    shipping_display_currency = country.currency if country else 'GMD'
                    shipping_rule_name = shipping_result.get('rule_name', 'Unknown rule')
                    shipping_debug_info = shipping_result.get('debug_info', {})
                    
                    # Admin debug output (temporary)
                    if current_user.is_authenticated and (current_user.is_admin or current_user.role == 'admin'):
                        # Calculate item details for debug
                        item_details = []
                        for item in cart_items:
                            product_id = item.get('id')
                            if product_id:
                                product = Product.query.get(product_id)
                                if product:
                                    item_weight = float(product.weight_kg) if product.weight_kg else 0.0
                                    item_details.append({
                                        'product_id': product_id,
                                        'product_name': product.name,
                                        'weight_kg': item_weight,
                                        'quantity': item['quantity'],
                                        'total_weight': item_weight * item['quantity']
                                    })
                        
                        app.logger.info(
                            f"🔍 ADMIN DEBUG - Checkout Shipping: "
                            f"Total Weight={total_weight}kg, "
                            f"Country={country.name if country else 'None'}, "
                            f"Rule ID={shipping_debug_info.get('applied_rule_id')}, "
                            f"Shipping Fee={float(shipping_price_gmd)}GMD, "
                            f"Items={item_details}, "
                            f"Debug Info={shipping_debug_info}"
                        )
                    
                    app.logger.debug(
                        f"Checkout shipping calculated: Rule={shipping_rule_name}, "
                        f"Country ID={country_id}, Weight={total_weight}kg, "
                        f"Shipping={float(shipping_price_gmd)}GMD"
                    )
                else:
                    # Shipping unavailable - default to 0 instead of showing error
                    shipping_price_gmd = Decimal('0.00')
                    shipping_rule_id = None
                    shipping_delivery_estimate = None
                    shipping_display_currency = country.currency if country else 'GMD'
                    
                    app.logger.warning(
                        f"No shipping rule found for checkout: "
                        f"Country ID={country_id}, Weight={total_weight}kg. "
                        f"Shipping fee defaulted to 0"
                    )
                    # Don't block checkout if shipping is unavailable - just default to 0
                    # flash('Shipping is not available for your selected country and cart weight. Shipping fee set to 0.', 'warning')
            
            # Convert shipping price to display currency if needed
            from .utils.currency_rates import convert_price, get_currency_symbol
            if country and country.currency != 'GMD':
                shipping_price_display = Decimal(str(convert_price(float(shipping_price_gmd), 'GMD', country.currency)))
            else:
                shipping_price_display = shipping_price_gmd
            
            # CRITICAL FIX: Use cart total if available, otherwise calculate
            # This ensures Cart Total = Checkout Total = Payment Total
            if cart_total is not None:
                # Use the cart total directly (already includes correct shipping)
                total_cost = Decimal(str(cart_total))
                current_app.logger.info(f'Checkout POST using cart total: {total_cost} (from session)')
            else:
                # Fallback: Calculate total cost (subtotal + shipping, in display currency)
                subtotal_decimal = Decimal(str(checkout_summary['subtotal']))
                total_cost = subtotal_decimal + shipping_price_display
                current_app.logger.warning(f'Cart total not found in session, calculating: {total_cost}')
            
            # Validate stock before creating pending payment
            for item in cart_items:
                product = Product.query.get(item['id'])
                if not product or (product.stock is not None and product.stock < item['quantity']):
                    flash(f'Sorry, {item["name"]} is out of stock or the quantity is not available', 'error')
                    return redirect(url_for('cart'))
            
            # Save address to user profile
            profile = ensure_user_profile(current_user)
            # Split full_name into first_name and last_name
            name_parts = form.full_name.data.strip().split(' ', 1)
            profile.first_name = name_parts[0] if name_parts else ''
            profile.last_name = name_parts[1] if len(name_parts) > 1 else ''
            profile.phone_number = form.phone.data
            profile.country = form.country.data
            profile.city = form.city.data
            profile.address = form.delivery_address.data
            db.session.commit()
            
            # Create PendingPayment instead of Order
            # Orders will only be created AFTER successful payment confirmation
            # Always use ModemPay as the payment method
            pending_payment = PendingPayment(
                user_id=current_user.id,
                amount=float(total_cost),  # Total including shipping in display currency
                status='waiting',
                payment_method='modempay',  # Always use ModemPay
                delivery_address=form.delivery_address.data,
                customer_name=form.full_name.data if hasattr(form, 'full_name') else current_user.username,
                customer_phone=form.phone.data if hasattr(form, 'phone') else None,
                customer_email=form.email.data if hasattr(form, 'email') else None,
                shipping_price=float(shipping_price_gmd),  # Store in GMD
                total_cost=float(total_cost),  # Total in display currency
                location='China',  # Default location, can be updated later
                shipping_rule_id=shipping_rule_id,
                shipping_mode_key=shipping_mode_key,  # Store selected shipping method
                shipping_delivery_estimate=shipping_delivery_estimate,
                shipping_display_currency=shipping_display_currency,
                cart_items_json=json.dumps(cart_items)  # Store cart items as JSON
            )
            
            db.session.add(pending_payment)
            db.session.commit()  # Commit to get the pending_payment.id
            
            # Initiate ModemPay payment using pending_payment_id
            # Automatically use ModemPay - no user selection needed
            payment_result = PaymentService.start_modempay_payment(
                pending_payment_id=pending_payment.id,  # Changed from order_id
                amount=float(total_cost),  # Use total including shipping
                phone=form.phone.data,
                provider='modempay',  # Always use ModemPay
                customer_name=form.full_name.data if hasattr(form, 'full_name') else None,
                customer_email=form.email.data if hasattr(form, 'email') else None
            )

            if payment_result.get('success'):
                # Read payment_url directly from result (format_payment_response merges data into top level)
                payment_url = payment_result.get('payment_url')
                if payment_url:
                    # DON'T clear cart yet - wait for payment confirmation
                    # Cart will be cleared when payment is confirmed and Order is created
                    
                    # Redirect user to the payment gateway immediately
                    return redirect(payment_url)
            
            # If payment initiation fails, mark pending payment as failed
            pending_payment.status = 'failed'
            db.session.commit()
            
            # If payment initiation fails, flash a message
            flash(payment_result.get('message', 'Failed to initiate payment. Please try again.'), 'error')
            return redirect(url_for('checkout'))
            
        except Exception as e:
            db.session.rollback()
            app.logger.error(f'Error during checkout: {str(e)}')
            flash('An error occurred while processing your order. Please try again.', 'error')
            return redirect(url_for('cart'))

    # If form is not valid or it's a GET request with a form error
    # Get available shipping methods for display
    from app.shipping import get_all_shipping_methods
    shipping_methods = get_all_shipping_methods()
    selected_shipping_method = session.get('selected_shipping_method')
    
    return render_template('checkout.html', 
                           cart_items=cart_items, 
                           cart_summary=cart_summary,
                           checkout_summary=checkout_summary,
                           total=checkout_summary['total'],
                           form=form,
                           shipping_methods=shipping_methods,
                           selected_shipping_method=selected_shipping_method)

# Admin routes
class MigrationForm(FlaskForm):
    submit = SubmitField('Run Migration')

@app.route('/admin/migrate', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_run_migration():
    """
    Admin endpoint to run database migrations safely.
    Only accessible to admin users.
    """
    import os
    import io
    import sys
    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    
    # Get base directory (project root where alembic.ini is located)
    # Use app.root_path which points to the project root
    basedir = app.root_path
    alembic_ini_path = os.path.join(basedir, 'alembic.ini')
    migrations_path = os.path.join(basedir, 'migrations')
    
    form = MigrationForm()
    migration_output = None
    current_revision = None
    target_revision = None
    is_up_to_date = False
    error_message = None
    alembic_cfg = None
    script_dir = None
    head_revision = None
    
    # Setup Alembic configuration
    try:
        alembic_cfg = Config(alembic_ini_path)
        alembic_cfg.set_main_option("script_location", migrations_path)
        
        # Set database URL if not already set
        if not alembic_cfg.get_main_option('sqlalchemy.url'):
            database_url = os.getenv('DATABASE_URL') or app.config.get('SQLALCHEMY_DATABASE_URI')
            if database_url:
                alembic_cfg.set_main_option('sqlalchemy.url', database_url)
        
        # Get script directory to find head revision
        script_dir = ScriptDirectory.from_config(alembic_cfg)
        head_revision = script_dir.get_current_head()
        
        # Get current revision from database (we're already in a request context)
        conn = db.engine.connect()
        try:
            context = MigrationContext.configure(conn)
            current_rev = context.get_current_revision()
            
            if current_rev:
                try:
                    current_script = script_dir.get_revision(current_rev)
                    current_revision = f"{current_rev[:12]} - {current_script.doc or 'No description'}"
                except:
                    current_revision = current_rev[:12]
            else:
                current_revision = "None (no migrations applied yet)"
            
            # Check if already at head
            if current_rev == head_revision:
                is_up_to_date = True
            
            # Get target revision info
            try:
                head_script = script_dir.get_revision(head_revision)
                target_revision = f"{head_revision[:12]} - {head_script.doc or 'No description'}"
            except:
                target_revision = head_revision[:12] if head_revision else "Unknown"
                
        finally:
            conn.close()
                
    except Exception as e:
        error_message = f"Error checking migration status: {str(e)}"
        app.logger.error(f"Migration status check failed: {error_message}", exc_info=True)
    
    # Handle POST request - Run migration
    if form.validate_on_submit():
        if is_up_to_date:
            flash('All migrations already applied. Database is up to date.', 'info')
            return redirect(url_for('admin_run_migration'))
        
        # Ensure Alembic config is available (in case initial setup failed)
        if not alembic_cfg:
            try:
                alembic_cfg = Config(alembic_ini_path)
                alembic_cfg.set_main_option("script_location", migrations_path)
                if not alembic_cfg.get_main_option('sqlalchemy.url'):
                    database_url = os.getenv('DATABASE_URL') or app.config.get('SQLALCHEMY_DATABASE_URI')
                    if database_url:
                        alembic_cfg.set_main_option('sqlalchemy.url', database_url)
            except Exception as e:
                error_message = f"Failed to initialize Alembic config: {str(e)}"
                flash(error_message, 'error')
                app.logger.error(f"Failed to initialize Alembic: {error_message}", exc_info=True)
                return render_template('admin/admin/migrate.html',
                                     form=form,
                                     current_revision=current_revision,
                                     target_revision=target_revision,
                                     is_up_to_date=is_up_to_date,
                                     migration_output=migration_output,
                                     error_message=error_message)
        
        try:
            # Capture Alembic output
            output_buffer = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = output_buffer
            
            try:
                # Run the upgrade command (we're already in a request context)
                command.upgrade(alembic_cfg, "head")
                    
                migration_output = output_buffer.getvalue()
                
                # Log success
                app.logger.info(f"✅ Database migration completed via admin endpoint by {current_user.username}")
                
                flash('Database migration completed successfully!', 'success')
                
            finally:
                sys.stdout = old_stdout
                output_buffer.close()
                
        except Exception as e:
            error_msg = str(e)
            error_message = error_msg
            migration_output = output_buffer.getvalue() if 'output_buffer' in locals() else None
            sys.stdout = old_stdout if 'old_stdout' in locals() else sys.stdout
            
            app.logger.error(f"❌ Migration failed: {error_msg}", exc_info=True)
            flash(f'Migration failed: {error_msg}', 'error')
        
        # After POST, show results on the same page (don't redirect immediately)
        # Refresh migration status after running (only if we have the config)
        if not error_message and alembic_cfg and script_dir:
            try:
                conn = db.engine.connect()
                try:
                    context = MigrationContext.configure(conn)
                    current_rev = context.get_current_revision()
                    if current_rev:
                        try:
                            current_script = script_dir.get_revision(current_rev)
                            current_revision = f"{current_rev[:12]} - {current_script.doc or 'No description'}"
                        except:
                            current_revision = current_rev[:12]
                    else:
                        current_revision = "None (no migrations applied yet)"
                    
                    # Check if now up to date
                    is_up_to_date = (current_rev == head_revision) if head_revision else False
                finally:
                    conn.close()
            except Exception as e:
                if not error_message:
                    error_message = f"Error refreshing status: {str(e)}"
    
    # Render template with migration status and results
    return render_template('admin/admin/migrate.html',
                         form=form,
                         current_revision=current_revision,
                         target_revision=target_revision,
                         is_up_to_date=is_up_to_date,
                         migration_output=migration_output,
                         error_message=error_message)

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
                        # Fallback to minimal settings with all required attributes
                        class SafeSettings:
                            id = None
                            business_name = None
                            website_url = None
                            support_email = None
                            contact_whatsapp = None
                            company_logo_url = None
                            contact_whatsapp_receiver = None
                            contact_email_receiver = None
                            whatsapp_receiver = '+2200000000'
                            email_receiver = 'buxinstore9@gmail.com'
                            modempay_api_key = None
                            modempay_public_key = None
                            payment_return_url = None
                            payment_cancel_url = None
                            payments_enabled = True
                            cloudinary_cloud_name = None
                            cloudinary_api_key = None
                            cloudinary_api_secret = None
                            whatsapp_access_token = None
                            whatsapp_phone_number_id = None
                            whatsapp_business_name = None
                            whatsapp_bulk_messaging_enabled = False
                            resend_api_key = None
                            resend_from_email = None
                            resend_default_recipient = None
                            resend_enabled = True
                            contact_email = None
                            default_subject_prefix = 'buxin store'
                            ai_api_key = None
                            ai_auto_prompt_improvements = False
                            backup_enabled = False
                            backup_time = '02:00'
                            backup_email = None
                            backup_retention_days = 30
                            backup_last_run = None
                            backup_last_status = None
                            backup_last_message = None
                            floating_whatsapp_number = None
                            floating_support_email = None
                            floating_email_subject = 'Support Request'
                            floating_email_body = 'Hello, I need help with ...'
                            gambia_whatsapp_number = None
                            gambia_phone_number = None
                            pwa_app_name = 'buxin store'
                            pwa_short_name = 'buxin store'
                            pwa_theme_color = '#ffffff'
                            pwa_background_color = '#ffffff'
                            pwa_start_url = '/'
                            pwa_display = 'standalone'
                            pwa_description = None
                            pwa_logo_path = None
                            pwa_favicon_path = None
                            updated_at = None
                        settings = SafeSettings()
                else:
                    # No settings exist - create empty wrapper with all required attributes
                    class SafeSettings:
                        id = None
                        business_name = None
                        website_url = None
                        support_email = None
                        contact_whatsapp = None
                        company_logo_url = None
                        contact_whatsapp_receiver = None
                        contact_email_receiver = None
                        whatsapp_receiver = '+2200000000'
                        email_receiver = 'buxinstore9@gmail.com'
                        modempay_api_key = None
                        modempay_public_key = None
                        payment_return_url = None
                        payment_cancel_url = None
                        payments_enabled = True
                        cloudinary_cloud_name = None
                        cloudinary_api_key = None
                        cloudinary_api_secret = None
                        whatsapp_access_token = None
                        whatsapp_phone_number_id = None
                        whatsapp_business_name = None
                        whatsapp_bulk_messaging_enabled = False
                        resend_api_key = None
                        resend_from_email = None
                        resend_default_recipient = None
                        resend_enabled = True
                        contact_email = None
                        default_subject_prefix = 'buxin store'
                        ai_api_key = None
                        ai_auto_prompt_improvements = False
                        backup_enabled = False
                        backup_time = '02:00'
                        backup_email = None
                        backup_retention_days = 30
                        backup_last_run = None
                        backup_last_status = None
                        backup_last_message = None
                        floating_whatsapp_number = None
                        floating_support_email = None
                        floating_email_subject = 'Support Request'
                        floating_email_body = 'Hello, I need help with ...'
                        gambia_whatsapp_number = None
                        gambia_phone_number = None
                        pwa_app_name = 'buxin store'
                        pwa_short_name = 'buxin store'
                        pwa_theme_color = '#ffffff'
                        pwa_background_color = '#ffffff'
                        pwa_start_url = '/'
                        pwa_display = 'standalone'
                        pwa_description = None
                        pwa_logo_path = None
                        pwa_favicon_path = None
                        updated_at = None
                    settings = SafeSettings()
            except Exception:
                # Fallback to empty settings with all required attributes
                class SafeSettings:
                    id = None
                    business_name = None
                    website_url = None
                    support_email = None
                    contact_whatsapp = None
                    company_logo_url = None
                    contact_whatsapp_receiver = None
                    contact_email_receiver = None
                    whatsapp_receiver = '+2200000000'
                    email_receiver = 'buxinstore9@gmail.com'
                    modempay_api_key = None
                    modempay_public_key = None
                    payment_return_url = None
                    payment_cancel_url = None
                    payments_enabled = True
                    cloudinary_cloud_name = None
                    cloudinary_api_key = None
                    cloudinary_api_secret = None
                    whatsapp_access_token = None
                    whatsapp_phone_number_id = None
                    whatsapp_business_name = None
                    whatsapp_bulk_messaging_enabled = False
                    resend_api_key = None
                    resend_from_email = None
                    resend_default_recipient = None
                    resend_enabled = True
                    contact_email = None
                    default_subject_prefix = 'buxin store'
                    ai_api_key = None
                    ai_auto_prompt_improvements = False
                    backup_enabled = False
                    backup_time = '02:00'
                    backup_email = None
                    backup_retention_days = 30
                    backup_last_run = None
                    backup_last_status = None
                    backup_last_message = None
                    floating_whatsapp_number = None
                    floating_support_email = None
                    floating_email_subject = 'Support Request'
                    floating_email_body = 'Hello, I need help with ...'
                    gambia_whatsapp_number = None
                    gambia_phone_number = None
                    pwa_app_name = 'buxin store'
                    pwa_short_name = 'buxin store'
                    pwa_theme_color = '#ffffff'
                    pwa_background_color = '#ffffff'
                    pwa_start_url = '/'
                    pwa_display = 'standalone'
                    pwa_description = None
                    pwa_logo_path = None
                    pwa_favicon_path = None
                    updated_at = None
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
                
                # Floating Contact Widget Settings
                if hasattr(settings, 'floating_whatsapp_number'):
                    settings.floating_whatsapp_number = request.form.get('floating_whatsapp_number', '').strip()
                if hasattr(settings, 'floating_support_email'):
                    settings.floating_support_email = request.form.get('floating_support_email', '').strip()
                if hasattr(settings, 'floating_email_subject'):
                    settings.floating_email_subject = request.form.get('floating_email_subject', 'Support Request').strip()
                if hasattr(settings, 'floating_email_body'):
                    settings.floating_email_body = request.form.get('floating_email_body', 'Hello, I need help with ...').strip()
                
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
                new_access_token = request.form.get('whatsapp_access_token', '').strip()
                new_phone_number_id = request.form.get('whatsapp_phone_number_id', '').strip()
                settings.whatsapp_business_name = request.form.get('whatsapp_business_name', '').strip()
                settings.whatsapp_bulk_messaging_enabled = request.form.get('whatsapp_bulk_messaging_enabled') == 'on'
                
                # Only update token if provided (don't overwrite with empty)
                if new_access_token:
                    settings.whatsapp_access_token = new_access_token
                if new_phone_number_id:
                    settings.whatsapp_phone_number_id = new_phone_number_id
                
                db.session.commit()
                
                # Update .env file and reload environment immediately
                if new_access_token or new_phone_number_id:
                    from app.utils.whatsapp_token import save_whatsapp_token_to_env, get_whatsapp_token
                    # Get the actual values (from DB or form)
                    final_token = settings.whatsapp_access_token or new_access_token
                    final_phone_id = settings.whatsapp_phone_number_id or new_phone_number_id
                    
                    if final_token and final_phone_id:
                        if save_whatsapp_token_to_env(final_token, final_phone_id):
                            flash('WhatsApp settings updated successfully. Token saved to .env and reloaded.', 'success')
                        else:
                            flash('WhatsApp settings saved to database, but .env update failed. You may need to restart the server.', 'warning')
                    else:
                        flash('WhatsApp settings updated successfully.', 'success')
                else:
                    flash('WhatsApp settings updated successfully.', 'success')
                
            elif section == 'email':
                # Resend email settings
                new_resend_api_key = request.form.get('resend_api_key', '').strip()
                new_resend_from_email = request.form.get('resend_from_email', '').strip()
                settings.resend_default_recipient = request.form.get('resend_default_recipient', '').strip()
                settings.resend_enabled = request.form.get('resend_enabled') == 'on'
                settings.contact_email = request.form.get('contact_email', '').strip()
                settings.default_subject_prefix = request.form.get('default_subject_prefix', 'buxin store').strip()
                
                # Validate FROM email domain if provided (non-blocking if API key lacks permissions)
                if new_resend_from_email:
                    from app.utils.resend_domain import is_from_email_domain_verified
                    # Use new API key if provided, otherwise use existing one
                    api_key_to_check = new_resend_api_key or settings.resend_api_key or os.getenv("RESEND_API_KEY")
                    is_verified, error_msg, can_verify = is_from_email_domain_verified(new_resend_from_email, api_key_to_check)
                    
                    if not is_verified:
                        # If API key can't verify domains (restricted permissions), allow saving with warning
                        if not can_verify:
                            # API key doesn't have domain listing permissions - allow saving but warn
                            current_app.logger.warning(
                                f"Domain verification skipped due to API key restrictions. "
                                f"Settings will be saved. {error_msg}"
                            )
                            flash(
                                f'Email settings saved. Note: {error_msg or "Domain verification could not be performed."} '
                                'Email sending will work, but please verify your domain at https://resend.com/domains.',
                                'warning'
                            )
                        else:
                            # Domain verification was attempted but domain is not verified
                            db.session.rollback()
                            flash(
                                f'The FROM email domain is not verified in Resend. {error_msg or ""} '
                                'Please verify it at https://resend.com/domains.',
                                'error'
                            )
                            return redirect(url_for('admin_settings'))
                
                # Save settings if validation passed
                if new_resend_api_key:
                    settings.resend_api_key = new_resend_api_key
                if new_resend_from_email:
                    settings.resend_from_email = new_resend_from_email
                
                db.session.commit()
                flash('Email settings updated successfully.', 'success')
                
            elif section == 'ai':
                settings.ai_api_key = request.form.get('ai_api_key', '').strip()
                settings.ai_auto_prompt_improvements = request.form.get('ai_auto_prompt_improvements') == 'on'
                
                db.session.commit()
                flash('AI settings updated successfully.', 'success')
            
            elif section == 'pwa':
                # Save PWA text settings
                if hasattr(settings, 'pwa_app_name'):
                    settings.pwa_app_name = request.form.get('pwa_app_name', 'buxin store').strip()
                if hasattr(settings, 'pwa_short_name'):
                    settings.pwa_short_name = request.form.get('pwa_short_name', 'buxin store').strip()
                if hasattr(settings, 'pwa_theme_color'):
                    settings.pwa_theme_color = request.form.get('pwa_theme_color', '#ffffff').strip()
                if hasattr(settings, 'pwa_background_color'):
                    settings.pwa_background_color = request.form.get('pwa_background_color', '#ffffff').strip()
                if hasattr(settings, 'pwa_start_url'):
                    settings.pwa_start_url = request.form.get('pwa_start_url', '/').strip()
                if hasattr(settings, 'pwa_display'):
                    settings.pwa_display = request.form.get('pwa_display', 'standalone').strip()
                if hasattr(settings, 'pwa_description'):
                    settings.pwa_description = request.form.get('pwa_description', '').strip()
                
                # Handle PWA logo upload (512x512)
                pwa_logo_file = request.files.get('pwa_logo')
                if pwa_logo_file and pwa_logo_file.filename:
                    if allowed_file(pwa_logo_file.filename):
                        try:
                            # Create pwa directory if it doesn't exist
                            pwa_dir = os.path.join(app.static_folder, 'pwa')
                            os.makedirs(pwa_dir, exist_ok=True)
                            
                            # Delete old logo if exists
                            if hasattr(settings, 'pwa_logo_path') and settings.pwa_logo_path:
                                old_path = os.path.join(app.static_folder, settings.pwa_logo_path.lstrip('/static/'))
                                if os.path.exists(old_path):
                                    try:
                                        os.remove(old_path)
                                    except Exception as e:
                                        current_app.logger.warning(f"Could not delete old PWA logo: {e}")
                            
                            # Save new logo
                            from werkzeug.utils import secure_filename
                            filename = f"pwa_logo_{uuid.uuid4().hex[:8]}.{pwa_logo_file.filename.rsplit('.', 1)[1].lower()}"
                            filepath = os.path.join(pwa_dir, filename)
                            pwa_logo_file.save(filepath)
                            
                            # Store relative path in database
                            if hasattr(settings, 'pwa_logo_path'):
                                settings.pwa_logo_path = f"pwa/{filename}"
                            current_app.logger.info(f"✅ PWA logo saved: {filepath}")
                        except Exception as e:
                            current_app.logger.error(f"Error saving PWA logo: {str(e)}")
                            flash(f'Failed to upload PWA logo: {str(e)}', 'error')
                    else:
                        flash('Invalid PWA logo file type. Please upload PNG or JPG.', 'error')
                        return redirect(url_for('admin_settings'))
                
                # Handle PWA favicon upload (32x32 or 64x64)
                pwa_favicon_file = request.files.get('pwa_favicon')
                if pwa_favicon_file and pwa_favicon_file.filename:
                    if allowed_file(pwa_favicon_file.filename, {'png', 'jpg', 'jpeg', 'ico'}):
                        try:
                            # Create pwa directory if it doesn't exist
                            pwa_dir = os.path.join(app.static_folder, 'pwa')
                            os.makedirs(pwa_dir, exist_ok=True)
                            
                            # Delete old favicon if exists
                            if hasattr(settings, 'pwa_favicon_path') and settings.pwa_favicon_path:
                                old_path = os.path.join(app.static_folder, settings.pwa_favicon_path.lstrip('/static/'))
                                if os.path.exists(old_path):
                                    try:
                                        os.remove(old_path)
                                    except Exception as e:
                                        current_app.logger.warning(f"Could not delete old PWA favicon: {e}")
                            
                            # Save new favicon
                            from werkzeug.utils import secure_filename
                            ext = pwa_favicon_file.filename.rsplit('.', 1)[1].lower()
                            filename = f"pwa_favicon_{uuid.uuid4().hex[:8]}.{ext}"
                            filepath = os.path.join(pwa_dir, filename)
                            pwa_favicon_file.save(filepath)
                            
                            # Store relative path in database
                            if hasattr(settings, 'pwa_favicon_path'):
                                settings.pwa_favicon_path = f"pwa/{filename}"
                            current_app.logger.info(f"✅ PWA favicon saved: {filepath}")
                        except Exception as e:
                            current_app.logger.error(f"Error saving PWA favicon: {str(e)}")
                            flash(f'Failed to upload PWA favicon: {str(e)}', 'error')
                    else:
                        flash('Invalid PWA favicon file type. Please upload PNG, JPG, or ICO.', 'error')
                        return redirect(url_for('admin_settings'))
                
                db.session.commit()
                flash('PWA settings updated successfully.', 'success')
            
            elif section == 'gambia':
                # Gambia Contact Settings
                gambia_whatsapp = request.form.get('gambia_whatsapp_number', '').strip()
                gambia_phone = request.form.get('gambia_phone_number', '').strip()
                
                # Validate phone numbers (must start with + if not empty)
                import re
                phone_pattern = re.compile(r'^\+[0-9]+$')
                
                if gambia_whatsapp and not phone_pattern.match(gambia_whatsapp):
                    flash('WhatsApp number must start with + followed by numbers only (e.g., +220XXXXXXXX).', 'error')
                    return redirect(url_for('admin_settings'))
                
                if gambia_phone and not phone_pattern.match(gambia_phone):
                    flash('Phone number must start with + followed by numbers only (e.g., +220XXXXXXXX).', 'error')
                    return redirect(url_for('admin_settings'))
                
                # Save settings
                if hasattr(settings, 'gambia_whatsapp_number'):
                    settings.gambia_whatsapp_number = gambia_whatsapp
                if hasattr(settings, 'gambia_phone_number'):
                    settings.gambia_phone_number = gambia_phone
                
                db.session.commit()
                flash('Gambia contact numbers updated successfully.', 'success')
            
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
    
    # PWA Settings - initialize defaults if not set
    if hasattr(settings, 'pwa_app_name') and not settings.pwa_app_name:
        settings.pwa_app_name = 'buxin store'
    if hasattr(settings, 'pwa_short_name') and not settings.pwa_short_name:
        settings.pwa_short_name = 'buxin store'
    if hasattr(settings, 'pwa_theme_color') and not settings.pwa_theme_color:
        settings.pwa_theme_color = '#ffffff'
    if hasattr(settings, 'pwa_background_color') and not settings.pwa_background_color:
        settings.pwa_background_color = '#ffffff'
    if hasattr(settings, 'pwa_start_url') and not settings.pwa_start_url:
        settings.pwa_start_url = '/'
    if hasattr(settings, 'pwa_display') and not settings.pwa_display:
        settings.pwa_display = 'standalone'
    if hasattr(settings, 'pwa_description') and not settings.pwa_description:
        settings.pwa_description = 'buxin store - Your gateway to the future of technology. Explore robotics, coding, and artificial intelligence.'
    if not settings.resend_default_recipient:
        settings.resend_default_recipient = os.getenv('RESEND_DEFAULT_RECIPIENT', '')
    if settings.resend_enabled is None:
        settings.resend_enabled = os.getenv('RESEND_ENABLED', 'True').lower() == 'true'
    if not settings.contact_email:
        settings.contact_email = os.getenv('SUPPORT_EMAIL', '')
    if not settings.default_subject_prefix:
        settings.default_subject_prefix = os.getenv('EMAIL_SUBJECT_PREFIX', 'buxin store')
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
    
    # Get WhatsApp token status for display
    from app.utils.whatsapp_token import get_token_status
    whatsapp_token_status = get_token_status()
    
    return render_template('admin/admin/settings.html', 
                         settings=settings,
                         total_customers=total_customers,
                         total_products=total_products,
                         total_orders=total_orders,
                         whatsapp_token_status=whatsapp_token_status)

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
    """Test WhatsApp message sending with proper error handling"""
    try:
        from app.utils.whatsapp_token import get_whatsapp_token, check_token_expiration_from_error
        
        settings = AppSettings.query.first()
        if not settings:
            return jsonify({'success': False, 'message': 'Settings not found'}), 400
        
        # Get token dynamically (DB first, then .env)
        access_token, phone_number_id = get_whatsapp_token()
        
        test_number = request.json.get('test_number', '').strip() if request.is_json else request.form.get('test_number', '').strip()
        
        if not test_number:
            # Use configured receiver or default
            test_number = settings.whatsapp_receiver or settings.contact_whatsapp_receiver or os.getenv('WHATSAPP_TEST_NUMBER', '+2200000000')
        
        if not access_token or not phone_number_id:
            return jsonify({
                'success': False, 
                'message': 'WhatsApp credentials not configured. Please set access token and phone number ID in Settings.',
                'error_type': 'ConfigurationError'
            }), 400
        
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
                "body": "Hello from buxin store Admin! ✅ Your WhatsApp configuration is working correctly."
            }
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response_data = response.json() if response.headers.get('content-type', '').startswith('application/json') else {}
        
        if response.status_code == 200:
            # Verify we got a valid response with message ID
            message_id = None
            if isinstance(response_data, dict) and 'messages' in response_data:
                message_id = response_data.get('messages', [{}])[0].get('id', 'N/A')
            
            return jsonify({
                'success': True,
                'message': f'Test message sent successfully to {test_number}!',
                'message_id': message_id,
                'status_code': 200
            })
        else:
            # Parse error response for detailed information
            error_info = response_data.get('error', {}) if isinstance(response_data, dict) else {}
            error_code = error_info.get('code')
            error_subcode = error_info.get('error_subcode')
            error_message = error_info.get('message', response.text[:200])
            error_type = error_info.get('type', 'UnknownError')
            
            # Check if token is expired
            error_response_dict = {
                'status_code': response.status_code,
                'error_code': error_code,
                'error_subcode': error_subcode,
                'message': error_message,
                'error_type': error_type
            }
            
            is_expired = check_token_expiration_from_error(error_response_dict)
            
            if is_expired:
                detailed_message = (
                    f'❌ WhatsApp access token has EXPIRED (HTTP {response.status_code}).\n\n'
                    f'Error Code: {error_code}\n'
                    f'Error Subcode: {error_subcode}\n'
                    f'Message: {error_message}\n\n'
                    f'Please generate a new token from Meta Developer Console:\n'
                    f'https://developers.facebook.com/apps → WhatsApp → API Setup → Generate Token'
                )
            else:
                detailed_message = (
                    f'Failed to send message (HTTP {response.status_code}).\n\n'
                    f'Error Code: {error_code}\n'
                    f'Error Subcode: {error_subcode}\n'
                    f'Error Type: {error_type}\n'
                    f'Message: {error_message}'
                )
            
            return jsonify({
                'success': False,
                'message': detailed_message,
                'status_code': response.status_code,
                'error_code': error_code,
                'error_subcode': error_subcode,
                'error_type': error_type,
                'is_expired': is_expired
            }), 400
            
    except requests.exceptions.RequestException as e:
        current_app.logger.error(f"WhatsApp test network error: {str(e)}")
        return jsonify({
            'success': False, 
            'message': f'Network error: {str(e)}',
            'error_type': 'NetworkError'
        }), 500
    except Exception as e:
        current_app.logger.error(f"WhatsApp test error: {str(e)}", exc_info=True)
        return jsonify({
            'success': False, 
            'message': f'Error: {str(e)}',
            'error_type': 'UnexpectedError'
        }), 500

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
        
        # Format FROM email with business name if available
        business_name = getattr(settings, 'business_name', None) or os.getenv("BUSINESS_NAME", "Store")
        if business_name and '<' not in from_email:
            from_email = f"{business_name} <{from_email}>"
        
        subject_prefix = settings.default_subject_prefix or "buxin store"

        subject = f"{subject_prefix} - Test Email"
        html_body = f"""
            <html>
            <body>
                <p>This is a test email from your buxin store Admin settings page via Resend.</p>
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

        # Use official Resend API format: "to" must be a list
        resend.Emails.send({
            "from": from_email,
            "to": [test_email],  # Resend API requires "to" as a list
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

# ======================
# Admin Shipping Rules Management Routes
# ======================

@app.route('/admin/shipping', methods=['GET'])
@login_required
@admin_required
def admin_shipping_rules():
    """Admin page for managing shipping rules with search, sorting, and pagination."""
    from app.shipping.models import ShippingRule, ShippingMode
    
    # Get filter parameters
    search = request.args.get('search', '').strip()
    country_iso = request.args.get('country_iso', '').strip().upper()
    mode_key = request.args.get('mode_key', '').strip()
    status_filter = request.args.get('status', '')  # 'active' or 'inactive'
    sort_by = request.args.get('sort', 'priority')  # 'priority', 'country', 'min_weight', 'price'
    sort_order = request.args.get('order', 'desc')  # 'asc' or 'desc'
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Build query using NEW ShippingRule system
    query = ShippingRule.query.join(ShippingMode, ShippingRule.shipping_mode_key == ShippingMode.key)
    
    # Apply filters
    if search:
        query = query.filter(
            db.or_(
                ShippingRule.notes.ilike(f'%{search}%'),
                ShippingRule.country_iso.ilike(f'%{search}%'),
                ShippingMode.label.ilike(f'%{search}%')
            )
        )
    
    if country_iso:
        query = query.filter(ShippingRule.country_iso == country_iso)
    
    if mode_key:
        query = query.filter(ShippingRule.shipping_mode_key == mode_key)
    
    if status_filter == 'active':
        query = query.filter(ShippingRule.active == True)
    elif status_filter == 'inactive':
        query = query.filter(ShippingRule.active == False)
    
    # Apply sorting
    if sort_by == 'country':
        if sort_order == 'asc':
            query = query.order_by(
                db.case((ShippingRule.country_iso == '*', 'ZZZ'), else_=ShippingRule.country_iso).asc(),
                ShippingRule.priority.desc()
            )
        else:
            query = query.order_by(
                db.case((ShippingRule.country_iso == '*', 'ZZZ'), else_=ShippingRule.country_iso).desc(),
                ShippingRule.priority.desc()
            )
    elif sort_by == 'min_weight':
        if sort_order == 'asc':
            query = query.order_by(ShippingRule.min_weight.asc())
        else:
            query = query.order_by(ShippingRule.min_weight.desc())
    elif sort_by == 'price':
        if sort_order == 'asc':
            query = query.order_by(ShippingRule.price_gmd.asc())
        else:
            query = query.order_by(ShippingRule.price_gmd.desc())
    else:  # priority (default)
        if sort_order == 'asc':
            query = query.order_by(ShippingRule.priority.asc())
        else:
            query = query.order_by(ShippingRule.priority.desc())
    
    # Paginate
    rules = query.paginate(page=page, per_page=per_page, error_out=False)
    
    # Get statistics
    total_rules = ShippingRule.query.count()
    active_rules = ShippingRule.query.filter_by(active=True).count()
    global_rules = ShippingRule.query.filter_by(country_iso='*', active=True).count()
    countries_with_rules = db.session.query(db.func.count(db.distinct(ShippingRule.country_iso))).filter(
        ShippingRule.country_iso != '*',
        ShippingRule.active == True
    ).scalar() or 0
    
    # Get all countries for filter dropdown
    countries = Country.query.filter_by(is_active=True).order_by(Country.name).all()
    
    # Get all shipping modes for filter dropdown
    shipping_modes = ShippingMode.query.filter_by(active=True).order_by(ShippingMode.id).all()
    
    return render_template('admin/admin/shipping_rules.html',
                         rules=rules,
                         search=search,
                         country_iso=country_iso,
                         mode_key=mode_key,
                         status_filter=status_filter,
                         sort_by=sort_by,
                         sort_order=sort_order,
                         countries=countries,
                         shipping_modes=shipping_modes,
                         total_rules=total_rules,
                         active_rules=active_rules,
                         global_rules=global_rules,
                         countries_with_rules=countries_with_rules)

@app.route('/admin/shipping/new', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_new_shipping_rule():
    """Create a new shipping rule using NEW ShippingRule system."""
    from app.shipping.models import ShippingRule, ShippingMode
    from app.shipping.service import ShippingService
    
    # CRITICAL: Ensure shipping_method is NEVER used as a variable
    # This prevents NameError: name 'shipping_method' is not defined
    # Only shipping_mode_key should be used internally
    
    if request.method == 'POST':
        try:
            # Extract all form data first
            rule_type = request.form.get('rule_type', 'country')
            country_id = request.form.get('country_id')
            
            # PRIMARY FIELD: Always use shipping_mode_key from form
            # This is the ONLY variable we use for shipping method internally
            shipping_mode_key = request.form.get('shipping_mode_key', '').strip()
            
            # LEGACY FALLBACK: Only use old shipping_method field if shipping_mode_key is empty
            # Apply fallback immediately after reading shipping_mode_key
            # IMPORTANT: We read from form but NEVER create a shipping_method variable
            if not shipping_mode_key:
                # Read legacy field from form - store in old_field_value (NOT shipping_method)
                old_field_value = request.form.get('shipping_method', '').strip()
                if old_field_value:
                    # Map legacy values to new system
                    method_mapping = {
                        'express': 'express',
                        'ecommerce': 'economy_plus',
                        'economy': 'economy'
                    }
                    # Always assign to shipping_mode_key, never to shipping_method
                    shipping_mode_key = method_mapping.get(old_field_value, old_field_value)
            
            # BRACKET-BASED SYSTEM: Read min and max weight from form
            # These define the weight bracket for this shipping price
            min_weight_str = request.form.get('min_weight', '0.0').strip()
            max_weight_str = request.form.get('max_weight', '0.5').strip()
            price_gmd_str = request.form.get('price_gmd', '').strip()
            delivery_time = request.form.get('delivery_time', '').strip() or None
            priority_str = request.form.get('priority', '0').strip()
            active = request.form.get('active') == 'on'
            notes = request.form.get('notes', '').strip() or None
            
            # Validation - shipping_mode_key must be set after fallback
            if not shipping_mode_key:
                db.session.rollback()
                flash('Shipping method is required', 'error')
                current_app.logger.error('Shipping rule creation failed: shipping_mode_key is missing')
                return redirect(url_for('admin_new_shipping_rule'))
            
            if not min_weight_str or not max_weight_str or not price_gmd_str:
                db.session.rollback()
                flash('Min weight, max weight, and price are required', 'error')
                current_app.logger.error('Shipping rule creation failed: missing required fields')
                return redirect(url_for('admin_new_shipping_rule'))
            
            # Validate shipping mode exists
            mode = ShippingMode.query.filter_by(key=shipping_mode_key).first()
            if not mode:
                db.session.rollback()
                flash(f'Invalid shipping method: {shipping_mode_key}', 'error')
                current_app.logger.error(f'Shipping rule creation failed: invalid shipping_mode_key={shipping_mode_key}')
                return redirect(url_for('admin_new_shipping_rule'))
            
            # Convert to numeric types with proper validation
            try:
                min_weight = float(min_weight_str)
                max_weight = float(max_weight_str)
                price_gmd = float(price_gmd_str)
                priority = int(priority_str) if priority_str else 0
            except (ValueError, TypeError) as ve:
                db.session.rollback()
                flash(f'Invalid numeric value: {str(ve)}', 'error')
                current_app.logger.error(f'Invalid numeric value in shipping rule creation: {str(ve)}')
                return redirect(url_for('admin_new_shipping_rule'))
            
            # Validate numeric values are positive (non-negative for min_weight, positive for others)
            if min_weight < 0:
                db.session.rollback()
                flash('Min weight must be >= 0', 'error')
                return redirect(url_for('admin_new_shipping_rule'))
            
            if max_weight <= 0:
                db.session.rollback()
                flash('Max weight must be greater than 0', 'error')
                return redirect(url_for('admin_new_shipping_rule'))
            
            if max_weight <= min_weight:
                db.session.rollback()
                flash('Max weight must be greater than min weight', 'error')
                return redirect(url_for('admin_new_shipping_rule'))
            
            if price_gmd < 0:
                db.session.rollback()
                flash('Price must be >= 0', 'error')
                return redirect(url_for('admin_new_shipping_rule'))
            
            # Convert country_id to country_iso
            country_iso = '*'
            if rule_type == 'country':
                if not country_id:
                    db.session.rollback()
                    flash('Country is required for country-specific rules', 'error')
                    return redirect(url_for('admin_new_shipping_rule'))
                try:
                    country_id_int = int(country_id)
                    country = Country.query.get(country_id_int)
                    if not country:
                        db.session.rollback()
                        flash('Invalid country', 'error')
                        return redirect(url_for('admin_new_shipping_rule'))
                    country_iso = country.code.upper()
                except (ValueError, TypeError) as ve:
                    db.session.rollback()
                    flash(f'Invalid country ID: {str(ve)}', 'error')
                    current_app.logger.error(f'Invalid country ID in shipping rule creation: {str(ve)}')
                    return redirect(url_for('admin_new_shipping_rule'))
            
            # Log before creating rule for debugging
            current_app.logger.info(f"Creating shipping rule: mode_key={shipping_mode_key}, country={country_iso}, price={price_gmd}, min_weight={min_weight}, max_weight={max_weight}")
            
            # Check for overlapping rules using ShippingService
            has_overlap, overlap_error_msg = ShippingService.validate_rule_overlap(
                country_iso=country_iso,
                shipping_mode_key=shipping_mode_key,
                min_weight=Decimal(str(min_weight)),
                max_weight=Decimal(str(max_weight))
            )
            
            if has_overlap:
                flash(f'Warning: {overlap_error_msg}. Rule created anyway.', 'warning')
            
            # Create rule using ShippingService
            # CRITICAL: Ensure shipping_mode_key is passed correctly - NEVER use shipping_method
            try:
                new_rule, create_error = ShippingService.create_rule(
                    country_iso=country_iso,
                    shipping_mode_key=shipping_mode_key,  # Use shipping_mode_key, NOT shipping_method
                    min_weight=min_weight,
                    max_weight=max_weight,
                    price_gmd=price_gmd,
                    delivery_time=delivery_time,
                    priority=priority,
                    notes=notes,
                    active=active
                )
            except NameError as ne:
                # Catch NameError specifically here in case it happens in ShippingService
                db.session.rollback()
                import traceback
                error_traceback = traceback.format_exc()
                current_app.logger.error(f'NameError in ShippingService.create_rule call: {ne}\n{error_traceback}', exc_info=True)
                if 'shipping_method' in str(ne):
                    flash('Error: Internal code error - shipping_method variable referenced in ShippingService. Please contact support.', 'error')
                else:
                    flash(f'Error creating shipping rule: {str(ne)}', 'error')
                return redirect(url_for('admin_new_shipping_rule'))
            
            if create_error:
                # Check if the error message contains shipping_method reference
                if 'shipping_method' in str(create_error).lower():
                    current_app.logger.error(f'ShippingService returned error with shipping_method reference: {create_error}')
                    flash('Error: Internal code error - shipping_method variable referenced. Please contact support.', 'error')
                else:
                    flash(f'Error creating shipping rule: {create_error}', 'error')
                return redirect(url_for('admin_new_shipping_rule'))
            
            if not new_rule:
                flash('Error creating shipping rule: Unknown error', 'error')
                return redirect(url_for('admin_new_shipping_rule'))
            
            flash('Shipping rule created successfully!', 'success')
            return redirect(url_for('admin_shipping_rules'))
            
        except NameError as ne:
            # Specifically catch NameError to provide better debugging
            db.session.rollback()
            import traceback
            error_traceback = traceback.format_exc()
            current_app.logger.error(f'NameError in shipping rule creation: {ne}\n{error_traceback}', exc_info=True)
            # Check if it's the shipping_method error
            if 'shipping_method' in str(ne):
                flash('Error: Internal code error - shipping_method variable referenced. Please contact support.', 'error')
            else:
                flash(f'Error creating shipping rule: {str(ne)}', 'error')
            return redirect(url_for('admin_new_shipping_rule'))
        except Exception as e:
            db.session.rollback()
            import traceback
            error_traceback = traceback.format_exc()
            current_app.logger.error(f'Error creating shipping rule: {e}\n{error_traceback}', exc_info=True)
            flash(f'Error creating shipping rule: {str(e)}', 'error')
            return redirect(url_for('admin_new_shipping_rule'))
    
    # GET request - show form
    countries = Country.query.filter_by(is_active=True).order_by(Country.name).all()
    shipping_modes = ShippingMode.query.filter_by(active=True).order_by(ShippingMode.id).all()
    return render_template('admin/admin/shipping_rule_form.html', rule=None, countries=countries, shipping_modes=shipping_modes)

@app.route('/admin/shipping/<int:rule_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_shipping_rule(rule_id):
    """Edit an existing shipping rule using NEW ShippingRule system."""
    from app.shipping.models import ShippingRule, ShippingMode
    from app.shipping.service import ShippingService
    
    rule = ShippingRule.query.get_or_404(rule_id)
    
    if request.method == 'POST':
        try:
            rule_type = request.form.get('rule_type', 'country')
            country_id = request.form.get('country_id')
            
            # Get shipping_mode_key - PRIMARY FIELD: Always use shipping_mode_key from form
            shipping_mode_key = request.form.get('shipping_mode_key', '').strip()
            
            # LEGACY FALLBACK: Only use old shipping_method field if shipping_mode_key is empty
            # Apply fallback immediately after reading shipping_mode_key
            if not shipping_mode_key:
                # Read legacy field - NEVER use shipping_method as a variable, only read from form
                old_field_value = request.form.get('shipping_method', '').strip()
                if old_field_value:
                    # Map legacy values to new system
                    method_mapping = {
                        'express': 'express',
                        'ecommerce': 'economy_plus',
                        'economy': 'economy'
                    }
                    shipping_mode_key = method_mapping.get(old_field_value, old_field_value)
            
            # BRACKET-BASED SYSTEM: Read min and max weight from form
            # These define the weight bracket for this shipping price
            min_weight = request.form.get('min_weight', str(rule.min_weight)).strip()
            max_weight = request.form.get('max_weight', str(rule.max_weight)).strip()
            price_gmd = request.form.get('price_gmd')
            delivery_time = request.form.get('delivery_time', '').strip()
            priority = request.form.get('priority', 0)
            active = request.form.get('active') == 'on'
            notes = request.form.get('notes', '').strip()
            
            # Validation - shipping_mode_key must be set after fallback
            if not shipping_mode_key:
                db.session.rollback()
                flash('Shipping method is required', 'error')
                current_app.logger.error(f'Shipping rule edit failed: shipping_mode_key is missing for rule_id={rule_id}')
                return redirect(url_for('admin_edit_shipping_rule', rule_id=rule_id))
            
            # Validate shipping mode exists
            mode = ShippingMode.query.filter_by(key=shipping_mode_key).first()
            if not mode:
                db.session.rollback()
                flash(f'Invalid shipping method: {shipping_mode_key}', 'error')
                current_app.logger.error(f'Shipping rule edit failed: invalid shipping_mode_key={shipping_mode_key} for rule_id={rule_id}')
                return redirect(url_for('admin_edit_shipping_rule', rule_id=rule_id))
            
            if not min_weight or not max_weight or not price_gmd:
                db.session.rollback()
                flash('Min weight, max weight, and price are required', 'error')
                current_app.logger.error(f'Shipping rule edit failed: missing required fields for rule_id={rule_id}')
                return redirect(url_for('admin_edit_shipping_rule', rule_id=rule_id))
            
            # Convert to numeric types with proper validation
            try:
                min_weight = float(min_weight)
                max_weight = float(max_weight)
                price_gmd = float(price_gmd)
                priority = int(priority) if priority else 0
            except (ValueError, TypeError) as ve:
                db.session.rollback()
                flash(f'Invalid numeric value: {str(ve)}', 'error')
                current_app.logger.error(f'Invalid numeric value in shipping rule edit: {str(ve)} for rule_id={rule_id}')
                return redirect(url_for('admin_edit_shipping_rule', rule_id=rule_id))
            
            # Validate numeric values are positive (non-negative for min_weight, positive for others)
            if min_weight < 0:
                db.session.rollback()
                flash('Min weight must be >= 0', 'error')
                return redirect(url_for('admin_edit_shipping_rule', rule_id=rule_id))
            
            if max_weight <= 0:
                db.session.rollback()
                flash('Max weight must be greater than 0', 'error')
                return redirect(url_for('admin_edit_shipping_rule', rule_id=rule_id))
            
            if max_weight <= min_weight:
                db.session.rollback()
                flash('Max weight must be greater than min weight', 'error')
                return redirect(url_for('admin_edit_shipping_rule', rule_id=rule_id))
            
            if price_gmd < 0:
                db.session.rollback()
                flash('Price must be >= 0', 'error')
                return redirect(url_for('admin_edit_shipping_rule', rule_id=rule_id))
            
            # Convert country_id to country_iso
            country_iso = '*'
            if rule_type == 'country':
                if not country_id:
                    db.session.rollback()
                    flash('Country is required for country-specific rules', 'error')
                    return redirect(url_for('admin_edit_shipping_rule', rule_id=rule_id))
                try:
                    country_id = int(country_id)
                    country = Country.query.get(country_id)
                    if not country:
                        db.session.rollback()
                        flash('Invalid country', 'error')
                        return redirect(url_for('admin_edit_shipping_rule', rule_id=rule_id))
                    country_iso = country.code.upper()
                except (ValueError, TypeError) as ve:
                    db.session.rollback()
                    flash(f'Invalid country ID: {str(ve)}', 'error')
                    current_app.logger.error(f'Invalid country ID in shipping rule edit: {str(ve)} for rule_id={rule_id}')
                    return redirect(url_for('admin_edit_shipping_rule', rule_id=rule_id))
            
            # Log before updating rule for debugging
            current_app.logger.info(f"Updating shipping rule: rule_id={rule_id}, mode_key={shipping_mode_key}, country={country_iso}, price={price_gmd}, min_weight={min_weight}, max_weight={max_weight}")
            
            # Update rule using ShippingService
            updated_rule, error = ShippingService.update_rule(
                rule_id=rule_id,
                country_iso=country_iso,
                shipping_mode_key=shipping_mode_key,
                min_weight=min_weight,
                max_weight=max_weight,
                price_gmd=price_gmd,
                delivery_time=delivery_time if delivery_time else None,
                priority=priority,
                notes=notes if notes else None,
                active=active
            )
            
            if error:
                flash(f'Error updating shipping rule: {error}', 'error')
                return redirect(url_for('admin_edit_shipping_rule', rule_id=rule_id))
            
            flash('Shipping rule updated successfully!', 'success')
            return redirect(url_for('admin_shipping_rules'))
            
        except Exception as e:
            db.session.rollback()
            import traceback
            error_traceback = traceback.format_exc()
            current_app.logger.error(f'Error updating shipping rule: {e}\n{error_traceback}', exc_info=True)
            flash(f'Error updating shipping rule: {str(e)}', 'error')
            return redirect(url_for('admin_edit_shipping_rule', rule_id=rule_id))
    
    # GET request - show form
    countries = Country.query.filter_by(is_active=True).order_by(Country.name).all()
    shipping_modes = ShippingMode.query.filter_by(active=True).order_by(ShippingMode.id).all()
    return render_template('admin/admin/shipping_rule_form.html', rule=rule, countries=countries, shipping_modes=shipping_modes)

@app.route('/admin/shipping/<int:rule_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_shipping_rule(rule_id):
    """Delete a shipping rule using NEW ShippingRule system with dependency checking."""
    from app.shipping.models import ShippingRule
    
    current_app.logger.info(f'Attempting to delete shipping rule ID: {rule_id}')
    
    rule = ShippingRule.query.get_or_404(rule_id)
    
    try:
        # Check for dependencies using shipping_rule_id (NOT shipping_method)
        # Use direct SQL count queries to avoid loading full objects which might reference old columns
        from sqlalchemy import text
        
        # Check pending_payments dependencies - use shipping_rule_id only
        pending_count = db.session.execute(
            text("SELECT COUNT(*) FROM pending_payments WHERE shipping_rule_id = :rule_id"),
            {"rule_id": rule_id}
        ).scalar()
        
        # Check orders dependencies - use shipping_rule_id only
        order_count = db.session.execute(
            text("SELECT COUNT(*) FROM \"order\" WHERE shipping_rule_id = :rule_id"),
            {"rule_id": rule_id}
        ).scalar()
        
        current_app.logger.info(
            f'Rule {rule_id} dependencies - PendingPayments: {pending_count}, Orders: {order_count}'
        )
        
        # If dependencies exist, deactivate instead of deleting
        if pending_count > 0 or order_count > 0:
            rule.active = False
            rule.updated_at = datetime.utcnow()
            db.session.commit()
            current_app.logger.info(f'Rule {rule_id} deactivated due to {pending_count + order_count} dependencies')
            flash(
                f'Shipping rule deactivated (not deleted) because it is used by {pending_count + order_count} order(s). '
                'You can delete it after those orders are completed.',
                'warning'
            )
        else:
            # No dependencies - safe to delete
            db.session.delete(rule)
            db.session.commit()
            current_app.logger.info(f'Rule {rule_id} deleted successfully (no dependencies)')
            flash('Shipping rule deleted successfully!', 'success')
            
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Error deleting shipping rule {rule_id}: {e}', exc_info=True)
        flash(f'Error deleting shipping rule: {str(e)}', 'error')
    
    return redirect(url_for('admin_shipping_rules'))

@app.route('/admin/shipping/<int:rule_id>/duplicate', methods=['POST'])
@login_required
@admin_required
def admin_duplicate_shipping_rule(rule_id):
    """Duplicate a shipping rule using NEW ShippingRule system."""
    from app.shipping.models import ShippingRule
    from app.shipping.service import ShippingService
    
    original_rule = ShippingRule.query.get_or_404(rule_id)
    
    try:
        # Create duplicate using ShippingService
        new_rule, error = ShippingService.create_rule(
            country_iso=original_rule.country_iso,
            shipping_mode_key=original_rule.shipping_mode_key,
            min_weight=float(original_rule.min_weight),
            max_weight=float(original_rule.max_weight),
            price_gmd=float(original_rule.price_gmd),
            delivery_time=original_rule.delivery_time,
            priority=original_rule.priority,
            notes=f"Copy of: {original_rule.notes}" if original_rule.notes else None,
            active=False  # Set to inactive by default
        )
        
        if error:
            flash(f'Error duplicating shipping rule: {error}', 'error')
            return redirect(url_for('admin_shipping_rules'))
        
        flash('Shipping rule duplicated successfully!', 'success')
        return redirect(url_for('admin_edit_shipping_rule', rule_id=new_rule.id))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Error duplicating shipping rule: {e}')
        flash(f'Error duplicating shipping rule: {str(e)}', 'error')
        return redirect(url_for('admin_shipping_rules'))

@app.route('/admin/shipping/<int:rule_id>/toggle-status', methods=['POST'])
@login_required
@admin_required
def admin_toggle_shipping_rule_status(rule_id):
    """Toggle shipping rule active/inactive status using NEW ShippingRule system."""
    from app.shipping.models import ShippingRule
    
    rule = ShippingRule.query.get_or_404(rule_id)
    
    try:
        rule.active = not rule.active
        rule.updated_at = datetime.utcnow()
        db.session.commit()
        
        status_text = 'activated' if rule.active else 'deactivated'
        flash(f'Shipping rule {status_text} successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Error toggling shipping rule status: {e}')
        flash(f'Error toggling shipping rule status: {str(e)}', 'error')
    
    return redirect(url_for('admin_shipping_rules'))

@app.route('/admin/shipping/bulk-delete', methods=['POST'])
@login_required
@admin_required
def admin_bulk_delete_shipping_rules():
    """Bulk delete shipping rules with dependency checking."""
    from app.shipping.models import ShippingRule
    from sqlalchemy import text
    
    try:
        rule_ids = request.form.getlist('rule_ids[]')
        if not rule_ids:
            flash('No rules selected for deletion.', 'warning')
            return redirect(url_for('admin_shipping_rules'))
        
        # Convert to integers
        try:
            rule_ids = [int(rid) for rid in rule_ids]
        except (ValueError, TypeError):
            flash('Invalid rule IDs provided.', 'error')
            return redirect(url_for('admin_shipping_rules'))
        
        current_app.logger.info(f'Bulk delete requested for {len(rule_ids)} rules: {rule_ids}')
        
        deleted_count = 0
        deactivated_count = 0
        failed_count = 0
        failed_ids = []
        
        for rule_id in rule_ids:
            try:
                rule = ShippingRule.query.get(rule_id)
                if not rule:
                    failed_count += 1
                    failed_ids.append(rule_id)
                    continue
                
                # Check dependencies using shipping_rule_id (NOT shipping_method)
                pending_count = db.session.execute(
                    text("SELECT COUNT(*) FROM pending_payments WHERE shipping_rule_id = :rule_id"),
                    {"rule_id": rule_id}
                ).scalar()
                
                order_count = db.session.execute(
                    text("SELECT COUNT(*) FROM \"order\" WHERE shipping_rule_id = :rule_id"),
                    {"rule_id": rule_id}
                ).scalar()
                
                if pending_count > 0 or order_count > 0:
                    # Deactivate instead of delete
                    rule.active = False
                    rule.updated_at = datetime.utcnow()
                    deactivated_count += 1
                    current_app.logger.info(
                        f'Rule {rule_id} deactivated (has {pending_count + order_count} dependencies)'
                    )
                else:
                    # Safe to delete
                    db.session.delete(rule)
                    deleted_count += 1
                    current_app.logger.info(f'Rule {rule_id} deleted (no dependencies)')
                    
            except Exception as e:
                db.session.rollback()
                failed_count += 1
                failed_ids.append(rule_id)
                current_app.logger.error(f'Error processing rule {rule_id} in bulk delete: {e}', exc_info=True)
        
        # Commit all changes
        db.session.commit()
        
        # Build success message
        messages = []
        if deleted_count > 0:
            messages.append(f'{deleted_count} rule(s) deleted')
        if deactivated_count > 0:
            messages.append(f'{deactivated_count} rule(s) deactivated (had dependencies)')
        if failed_count > 0:
            messages.append(f'{failed_count} rule(s) failed: {failed_ids}')
        
        if messages:
            flash(' | '.join(messages), 'success' if failed_count == 0 else 'warning')
        else:
            flash('No rules were processed.', 'warning')
            
        current_app.logger.info(
            f'Bulk delete completed - Deleted: {deleted_count}, Deactivated: {deactivated_count}, Failed: {failed_count}'
        )
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Error in bulk delete: {e}', exc_info=True)
        flash(f'Error during bulk delete: {str(e)}', 'error')
    
    return redirect(url_for('admin_shipping_rules'))

@app.route('/admin/shipping/export', methods=['GET'])
@login_required
@admin_required
def admin_export_shipping_rules():
    """Export shipping rules to CSV or JSON using NEW ShippingRule system."""
    from app.shipping.models import ShippingRule, ShippingMode
    
    export_format = request.args.get('format', 'csv')  # 'csv' or 'json'
    
    rules = ShippingRule.query.join(ShippingMode, ShippingRule.shipping_mode_key == ShippingMode.key).order_by(ShippingRule.id).all()
    
    if export_format == 'json':
        rules_data = [rule.to_dict() for rule in rules]
        response = make_response(jsonify(rules_data))
        response.headers['Content-Type'] = 'application/json'
        response.headers['Content-Disposition'] = 'attachment; filename=shipping_rules.json'
        return response
    else:  # CSV
        output = StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow([
            'ID', 'Rule Type', 'Country ISO', 'Country Name', 'Shipping Method', 'Shipping Method Label',
            'Min Weight (kg)', 'Max Weight (kg)', 'Price (GMD)', 'Delivery Time', 'Priority', 
            'Active', 'Notes', 'Created At', 'Updated At'
        ])
        
        # Write data
        for rule in rules:
            # Get country name if country_iso is not '*'
            country_name = ''
            if rule.country_iso != '*':
                country = Country.query.filter_by(code=rule.country_iso).first()
                country_name = country.name if country else rule.country_iso
            
            writer.writerow([
                rule.id,
                'global' if rule.country_iso == '*' else 'country',
                rule.country_iso,
                country_name if rule.country_iso != '*' else 'Global',
                rule.shipping_mode_key,
                rule.shipping_mode.label if rule.shipping_mode else '',
                float(rule.min_weight) if rule.min_weight else 0.0,
                float(rule.max_weight) if rule.max_weight else 0.0,
                float(rule.price_gmd) if rule.price_gmd else 0.0,
                rule.delivery_time or '',
                rule.priority,
                'Active' if rule.active else 'Inactive',
                rule.notes or '',
                rule.created_at.strftime('%Y-%m-%d %H:%M:%S') if rule.created_at else '',
                rule.updated_at.strftime('%Y-%m-%d %H:%M:%S') if rule.updated_at else ''
            ])
        
        output.seek(0)
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'text/csv'
        response.headers['Content-Disposition'] = 'attachment; filename=shipping_rules.csv'
        return response

@app.route('/admin/shipping/import', methods=['POST'])
@login_required
@admin_required
def admin_import_shipping_rules():
    """Import shipping rules from CSV using NEW ShippingRule system."""
    from app.shipping.models import ShippingRule, ShippingMode
    from app.shipping.service import ShippingService
    
    if 'file' not in request.files:
        flash('No file provided', 'error')
        return redirect(url_for('admin_shipping_rules'))
    
    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('admin_shipping_rules'))
    
    try:
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        elif file.filename.endswith('.xlsx'):
            df = pd.read_excel(file)
        else:
            flash('Invalid file type. Please upload CSV or XLSX files only.', 'error')
            return redirect(url_for('admin_shipping_rules'))
        
        # Normalize column names
        df.columns = df.columns.str.strip().str.lower().str.replace(' ', '_')
        
        success_count = 0
        error_count = 0
        errors = []
        
        for index, row in df.iterrows():
            try:
                # Get rule type
                rule_type = str(row.get('rule_type', 'country')).strip().lower()
                if rule_type not in ['country', 'global']:
                    rule_type = 'country'
                
                # Get country_iso
                country_iso = '*'
                if rule_type == 'country':
                    country_name = str(row.get('country', '') or row.get('country_name', '')).strip()
                    country_iso_col = str(row.get('country_iso', '') or row.get('country_code', '')).strip().upper()
                    
                    if country_iso_col and country_iso_col != '*':
                        country_iso = country_iso_col
                    elif country_name:
                        country = Country.query.filter_by(name=country_name).first()
                        if country:
                            country_iso = country.code.upper()
                        else:
                            errors.append(f"Row {index + 2}: Country '{country_name}' not found")
                            error_count += 1
                            continue
                    else:
                        errors.append(f"Row {index + 2}: Country required for country-specific rules")
                        error_count += 1
                        continue
                
                # Get shipping mode
                shipping_mode_key = str(row.get('shipping_mode_key', '')).strip()
                if not shipping_mode_key:
                    errors.append(f"Row {index + 2}: Shipping method is required")
                    error_count += 1
                    continue
                
                # Validate shipping mode exists
                mode = ShippingMode.query.filter_by(key=shipping_mode_key).first()
                if not mode:
                    errors.append(f"Row {index + 2}: Invalid shipping method '{shipping_mode_key}'")
                    error_count += 1
                    continue
                
                # Get other fields
                min_weight = float(row.get('min_weight', 0))
                max_weight = float(row.get('max_weight', 0))
                price_gmd = float(row.get('price_gmd', 0))
                delivery_time = str(row.get('delivery_time', '')).strip() or None
                priority = int(row.get('priority', 0)) if pd.notna(row.get('priority')) else 0
                active = str(row.get('active', 'active')).strip().lower() in ['active', 'true', '1', 'yes']
                notes = str(row.get('notes', '') or row.get('note', '')).strip() or None
                
                # Validation
                if min_weight < 0 or max_weight <= min_weight or price_gmd < 0:
                    errors.append(f"Row {index + 2}: Invalid weight or price values")
                    error_count += 1
                    continue
                
                # Create rule using ShippingService
                rule, error = ShippingService.create_rule(
                    country_iso=country_iso,
                    shipping_mode_key=shipping_mode_key,
                    min_weight=min_weight,
                    max_weight=max_weight,
                    price_gmd=price_gmd,
                    delivery_time=delivery_time,
                    priority=priority,
                    notes=notes,
                    active=active
                )
                
                if error:
                    errors.append(f"Row {index + 2}: {error}")
                    error_count += 1
                    continue
                
                success_count += 1
                
            except Exception as e:
                errors.append(f"Row {index + 2}: {str(e)}")
                error_count += 1
                continue
        
        db.session.commit()
        
        if success_count > 0:
            flash(f'Successfully imported {success_count} shipping rule(s)!', 'success')
        if error_count > 0:
            flash(f'Failed to import {error_count} rule(s). Check errors: {"; ".join(errors[:5])}', 'warning')
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Error importing shipping rules: {e}')
        flash(f'Error importing shipping rules: {str(e)}', 'error')
    
    return redirect(url_for('admin_shipping_rules'))

# ======================
# Admin Shipping Methods Management API
# ======================

@app.route('/admin/api/shipping-methods', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_api_shipping_methods():
    """API endpoint for managing shipping methods (add, list)."""
    from app.shipping.models import ShippingMode
    
    if request.method == 'GET':
        # Return list of all shipping methods (including inactive for management)
        include_inactive = request.args.get('include_inactive', 'false').lower() == 'true'
        
        query = ShippingMode.query
        if not include_inactive:
            query = query.filter_by(active=True)
        
        methods = query.order_by(ShippingMode.id).all()
        
        return jsonify({
            'success': True,
            'methods': [method.to_dict() for method in methods]
        })
    
    elif request.method == 'POST':
        # Create new shipping method
        try:
            data = request.get_json()
            if not data:
                return jsonify({'success': False, 'message': 'No data provided'}), 400
            
            key = data.get('key', '').strip()
            label = data.get('label', '').strip()
            delivery_time_range = data.get('delivery_time_range', '').strip() or None
            description = data.get('description', '').strip() or None
            icon = data.get('icon', '📦')
            color = data.get('color', 'blue')
            
            # Validation
            if not key:
                return jsonify({'success': False, 'message': 'Shipping method key is required'}), 400
            
            if not label:
                return jsonify({'success': False, 'message': 'Shipping method label is required'}), 400
            
            # Check if key already exists
            existing = ShippingMode.query.filter_by(key=key).first()
            if existing:
                return jsonify({'success': False, 'message': f'Shipping method with key "{key}" already exists'}), 400
            
            # Create new shipping method
            new_method = ShippingMode(
                key=key,
                label=label,
                description=description,
                delivery_time_range=delivery_time_range,
                icon=icon,
                color=color,
                active=True
            )
            
            db.session.add(new_method)
            db.session.commit()
            
            current_app.logger.info(f'New shipping method created: {key} - {label}')
            
            return jsonify({
                'success': True,
                'message': 'Shipping method created successfully',
                'method': new_method.to_dict()
            }), 201
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f'Error creating shipping method: {e}', exc_info=True)
            return jsonify({'success': False, 'message': f'Error creating shipping method: {str(e)}'}), 500

@app.route('/admin/api/shipping-methods/<string:method_key>', methods=['DELETE'])
@login_required
@admin_required
def admin_api_delete_shipping_method(method_key):
    """Delete (soft delete) a shipping method. Preserves historical orders."""
    from app.shipping.models import ShippingMode, ShippingRule
    from sqlalchemy import text, inspect
    
    try:
        method = ShippingMode.query.filter_by(key=method_key).first_or_404()
        
        # Check if method is used in any active shipping rules
        active_rules_count = ShippingRule.query.filter_by(
            shipping_mode_key=method_key,
            active=True
        ).count()
        
        # Check if tables exist and get their actual names
        inspector = inspect(db.engine)
        tables = inspector.get_table_names()
        order_table_name = None
        if 'order' in tables:
            order_table_name = 'order'
        elif 'orders' in tables:
            order_table_name = 'orders'
        
        pending_payments_table_exists = 'pending_payments' in tables
        
        # Check if method is used in any orders (for information only - we'll soft delete)
        order_count = 0
        if order_table_name:
            try:
                columns = [col['name'] for col in inspector.get_columns(order_table_name)]
                if 'shipping_mode_key' in columns:
                    order_count = db.session.execute(
                        text(f'SELECT COUNT(*) FROM "{order_table_name}" WHERE shipping_mode_key = :key'),
                        {"key": method_key}
                    ).scalar() or 0
            except Exception as e:
                current_app.logger.warning(f"Could not count orders for shipping method {method_key}: {e}")
                order_count = 0
        
        # Check if method is used in any pending payments
        pending_count = 0
        if pending_payments_table_exists:
            try:
                columns = [col['name'] for col in inspector.get_columns('pending_payments')]
                if 'shipping_mode_key' in columns:
                    pending_count = db.session.execute(
                        text("SELECT COUNT(*) FROM pending_payments WHERE shipping_mode_key = :key"),
                        {"key": method_key}
                    ).scalar() or 0
            except Exception as e:
                current_app.logger.warning(f"Could not count pending payments for shipping method {method_key}: {e}")
                pending_count = 0
        
        # Soft delete: Set active=False instead of actually deleting
        # This preserves the method for historical orders while hiding it from new selections
        method.active = False
        db.session.commit()
        
        current_app.logger.info(
            f'Shipping method "{method_key}" soft-deleted. '
            f'Active rules: {active_rules_count}, Orders: {order_count}, Pending: {pending_count}'
        )
        
        return jsonify({
            'success': True,
            'message': f'Shipping method "{method.label}" has been deactivated. '
                      f'It will not appear in new selections but existing orders will still show the method name.',
            'active_rules_count': active_rules_count,
            'order_count': order_count,
            'pending_count': pending_count
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Error deleting shipping method: {e}', exc_info=True)
        return jsonify({'success': False, 'message': f'Error deleting shipping method: {str(e)}'}), 500

@app.route('/admin/shipping-methods', methods=['GET'])
@login_required
@admin_required
def admin_shipping_methods():
    """Admin page for managing shipping methods (list and delete)."""
    from app.shipping.models import ShippingMode, ShippingRule
    from sqlalchemy import text, inspect
    
    # Get all shipping methods (including inactive)
    methods = ShippingMode.query.order_by(ShippingMode.active.desc(), ShippingMode.id).all()
    
    # Check if tables exist and get their actual names
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    order_table_name = None
    if 'order' in tables:
        order_table_name = 'order'
    elif 'orders' in tables:
        order_table_name = 'orders'
    
    pending_payments_table_exists = 'pending_payments' in tables
    
    # Get usage statistics for each method
    methods_with_stats = []
    for method in methods:
        # Count active rules
        active_rules = ShippingRule.query.filter_by(
            shipping_mode_key=method.key,
            active=True
        ).count()
        
        # Count orders using this method (if table exists)
        order_count = 0
        if order_table_name:
            try:
                # Check if shipping_mode_key column exists
                columns = [col['name'] for col in inspector.get_columns(order_table_name)]
                if 'shipping_mode_key' in columns:
                    order_count = db.session.execute(
                        text(f'SELECT COUNT(*) FROM "{order_table_name}" WHERE shipping_mode_key = :key'),
                        {"key": method.key}
                    ).scalar() or 0
            except Exception as e:
                current_app.logger.warning(f"Could not count orders for shipping method {method.key}: {e}")
                order_count = 0
        
        # Count pending payments (if table exists)
        pending_count = 0
        if pending_payments_table_exists:
            try:
                # Check if shipping_mode_key column exists
                columns = [col['name'] for col in inspector.get_columns('pending_payments')]
                if 'shipping_mode_key' in columns:
                    pending_count = db.session.execute(
                        text("SELECT COUNT(*) FROM pending_payments WHERE shipping_mode_key = :key"),
                        {"key": method.key}
                    ).scalar() or 0
            except Exception as e:
                current_app.logger.warning(f"Could not count pending payments for shipping method {method.key}: {e}")
                pending_count = 0
        
        methods_with_stats.append({
            'method': method,
            'active_rules_count': active_rules,
            'order_count': order_count,
            'pending_count': pending_count,
            'total_usage': active_rules + order_count + pending_count
        })
    
    return render_template('admin/admin/shipping_methods.html', methods_with_stats=methods_with_stats)

# ======================
# Admin Country Management Routes
# ======================

@app.route('/admin/countries', methods=['GET'])
@login_required
@admin_required
def admin_countries():
    """Admin page for managing countries with search, sorting, and pagination."""
    # Get query parameters
    search = request.args.get('search', '').strip()
    sort_by = request.args.get('sort', 'name')  # name, code, currency, language, status
    order = request.args.get('order', 'asc')  # asc or desc
    status_filter = request.args.get('status', 'all')  # all, active, inactive
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    
    # Build query
    query = Country.query
    
    # Apply search filter
    if search:
        query = query.filter(
            db.or_(
                Country.name.ilike(f'%{search}%'),
                Country.code.ilike(f'%{search}%'),
                Country.currency.ilike(f'%{search}%'),
                Country.language.ilike(f'%{search}%')
            )
        )
    
    # Apply status filter
    if status_filter == 'active':
        query = query.filter(Country.is_active == True)
    elif status_filter == 'inactive':
        query = query.filter(Country.is_active == False)
    
    # Apply sorting
    if sort_by == 'name':
        order_by = Country.name.asc() if order == 'asc' else Country.name.desc()
    elif sort_by == 'code':
        order_by = Country.code.asc() if order == 'asc' else Country.code.desc()
    elif sort_by == 'currency':
        order_by = Country.currency.asc() if order == 'asc' else Country.currency.desc()
    elif sort_by == 'language':
        order_by = Country.language.asc() if order == 'asc' else Country.language.desc()
    elif sort_by == 'status':
        order_by = Country.is_active.asc() if order == 'asc' else Country.is_active.desc()
    else:
        order_by = Country.name.asc()
    
    query = query.order_by(order_by)
    
    # Paginate
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    countries = pagination.items
    
    return render_template('admin/admin/countries.html', 
                         countries=countries,
                         pagination=pagination,
                         search=search,
                         sort_by=sort_by,
                         order=order,
                         status_filter=status_filter,
                         per_page=per_page)

@app.route('/admin/countries/add', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_add_country():
    """Add a new country."""
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            code = request.form.get('code', '').strip().upper()
            currency = request.form.get('currency', '').strip().upper()
            currency_symbol = request.form.get('currency_symbol', '').strip()
            language = request.form.get('language', '').strip().lower()
            is_active = request.form.get('is_active') == 'on'
            
            # Handle flag image upload
            flag_image_path = None
            if 'flag_image' in request.files:
                file = request.files['flag_image']
                if file and file.filename:
                    from .utils.cloudinary_utils import upload_image_to_cloudinary
                    try:
                        flag_image_path = upload_image_to_cloudinary(file, folder='country_flags')
                    except Exception as e:
                        current_app.logger.error(f"Error uploading flag image: {e}")
                        flash('Failed to upload flag image. Please try again.', 'error')
                        return redirect(url_for('admin_add_country'))
            
            # Validate required fields
            if not all([name, code, currency, language]):
                flash('Please fill in all required fields.', 'error')
                return redirect(url_for('admin_add_country'))
            
            # Check if country code already exists
            existing = Country.query.filter_by(code=code).first()
            if existing:
                flash(f'Country with code {code} already exists.', 'error')
                return redirect(url_for('admin_add_country'))
            
            country = Country(
                name=name,
                code=code,
                currency=currency,
                currency_symbol=currency_symbol,
                language=language,
                flag_image_path=flag_image_path,
                is_active=is_active
            )
            
            db.session.add(country)
            db.session.commit()
            
            flash(f'Country {name} added successfully!', 'success')
            return redirect(url_for('admin_countries'))
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error adding country: {e}")
            flash('Failed to add country. Please try again.', 'error')
            return redirect(url_for('admin_add_country'))
    
    return render_template('admin/admin/country_form.html', country=None)

@app.route('/admin/countries/<int:country_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_country(country_id):
    """Edit an existing country."""
    country = Country.query.get_or_404(country_id)
    
    if request.method == 'POST':
        try:
            country.name = request.form.get('name', '').strip()
            country.code = request.form.get('code', '').strip().upper()
            country.currency = request.form.get('currency', '').strip().upper()
            country.currency_symbol = request.form.get('currency_symbol', '').strip()
            country.language = request.form.get('language', '').strip().lower()
            country.is_active = request.form.get('is_active') == 'on'
            
            # Handle flag image upload
            if 'flag_image' in request.files:
                file = request.files['flag_image']
                if file and file.filename:
                    from .utils.cloudinary_utils import upload_image_to_cloudinary
                    try:
                        country.flag_image_path = upload_image_to_cloudinary(file, folder='country_flags')
                    except Exception as e:
                        current_app.logger.error(f"Error uploading flag image: {e}")
                        flash('Failed to upload flag image. Please try again.', 'error')
                        return redirect(url_for('admin_edit_country', country_id=country_id))
            
            db.session.commit()
            flash(f'Country {country.name} updated successfully!', 'success')
            return redirect(url_for('admin_countries'))
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating country: {e}")
            flash('Failed to update country. Please try again.', 'error')
            return redirect(url_for('admin_edit_country', country_id=country_id))
    
    return render_template('admin/admin/country_form.html', country=country)

@app.route('/admin/countries/<int:country_id>/toggle', methods=['POST'])
@login_required
@admin_required
def admin_toggle_country(country_id):
    """Toggle country active/inactive status."""
    country = Country.query.get_or_404(country_id)
    
    try:
        country.is_active = not country.is_active
        db.session.commit()
        status = "activated" if country.is_active else "deactivated"
        flash(f'Country {country.name} {status} successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error toggling country: {e}")
        flash('Failed to toggle country status. Please try again.', 'error')
    
    return redirect(url_for('admin_countries'))

@app.route('/admin/countries/export', methods=['GET'])
@login_required
@admin_required
def admin_export_countries():
    """Export countries as JSON or CSV."""
    format_type = request.args.get('format', 'json')  # json or csv
    
    countries = Country.query.order_by(Country.name).all()
    countries_data = [country.to_dict() for country in countries]
    
    if format_type == 'csv':
        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=['id', 'name', 'code', 'currency', 'currency_symbol', 'language', 'flag_image_path', 'is_active'])
        writer.writeheader()
        for country in countries_data:
            writer.writerow(country)
        
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'text/csv'
        response.headers['Content-Disposition'] = 'attachment; filename=countries_export.csv'
        return response
    else:
        # JSON export
        response = make_response(json.dumps(countries_data, indent=2))
        response.headers['Content-Type'] = 'application/json'
        response.headers['Content-Disposition'] = 'attachment; filename=countries_export.json'
        return response

@app.route('/admin/countries/import', methods=['POST'])
@login_required
@admin_required
def admin_import_countries():
    """Import countries from JSON or CSV file."""
    if 'file' not in request.files:
        flash('No file provided', 'error')
        return redirect(url_for('admin_countries'))
    
    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('admin_countries'))
    
    try:
        filename = file.filename.lower()
        if filename.endswith('.json'):
            data = json.load(file)
        elif filename.endswith('.csv'):
            csv_data = file.read().decode('utf-8')
            reader = csv.DictReader(StringIO(csv_data))
            data = list(reader)
        else:
            flash('Unsupported file format. Please use JSON or CSV.', 'error')
            return redirect(url_for('admin_countries'))
        
        imported = 0
        updated = 0
        errors = []
        
        for item in data:
            try:
                code = item.get('code', '').strip().upper()
                if not code:
                    errors.append(f"Missing code for item: {item.get('name', 'Unknown')}")
                    continue
                
                country = Country.query.filter_by(code=code).first()
                if country:
                    # Update existing
                    country.name = item.get('name', country.name)
                    country.currency = item.get('currency', country.currency)
                    country.currency_symbol = item.get('currency_symbol', country.currency_symbol)
                    country.language = item.get('language', country.language)
                    country.flag_image_path = item.get('flag_image_path', country.flag_image_path)
                    if 'is_active' in item:
                        country.is_active = bool(item['is_active'])
                    updated += 1
                else:
                    # Create new
                    country = Country(
                        name=item.get('name', ''),
                        code=code,
                        currency=item.get('currency', 'USD'),
                        currency_symbol=item.get('currency_symbol', ''),
                        language=item.get('language', 'en'),
                        flag_image_path=item.get('flag_image_path'),
                        is_active=bool(item.get('is_active', False))
                    )
                    db.session.add(country)
                    imported += 1
            except Exception as e:
                errors.append(f"Error processing {item.get('name', 'Unknown')}: {str(e)}")
        
        db.session.commit()
        
        message = f'Successfully imported {imported} countries and updated {updated} countries.'
        if errors:
            message += f' {len(errors)} errors occurred.'
        flash(message, 'success' if not errors else 'error')
        
        if errors:
            current_app.logger.warning(f"Import errors: {errors}")
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error importing countries: {e}")
        flash(f'Failed to import countries: {str(e)}', 'error')
    
    return redirect(url_for('admin_countries'))

@app.route('/admin/countries/<int:country_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_country(country_id):
    """Delete a country."""
    country = Country.query.get_or_404(country_id)
    
    try:
        # Check if any users are using this country
        user_count = User.query.filter_by(country_id=country_id).count()
        if user_count > 0:
            flash(f'Cannot delete {country.name}. {user_count} user(s) are using this country.', 'error')
            return redirect(url_for('admin_countries'))
        
        db.session.delete(country)
        db.session.commit()
        flash(f'Country {country.name} deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting country: {e}")
        flash('Failed to delete country. Please try again.', 'error')
    
    return redirect(url_for('admin_countries'))

# ==================== Profit Rules Admin Routes ====================

@app.route('/admin/profit-rules', methods=['GET'])
@login_required
@admin_required
def admin_profit_rules():
    """Admin page for managing profit rules with search, sorting, and pagination."""
    # Get filter parameters
    search = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '')  # 'active' or 'inactive'
    min_price_filter = request.args.get('min_price', type=float)
    max_price_filter = request.args.get('max_price', type=float)
    sort_by = request.args.get('sort', 'priority')  # 'priority', 'min_price', 'profit_amount'
    sort_order = request.args.get('order', 'desc')  # 'asc' or 'desc'
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Build query
    query = ProfitRule.query
    
    # Apply filters
    if search:
        query = query.filter(
            db.or_(
                ProfitRule.note.ilike(f'%{search}%')
            )
        )
    
    if status_filter == 'active':
        query = query.filter(ProfitRule.is_active == True)
    elif status_filter == 'inactive':
        query = query.filter(ProfitRule.is_active == False)
    
    if min_price_filter is not None:
        query = query.filter(ProfitRule.min_price >= min_price_filter)
    
    if max_price_filter is not None:
        query = query.filter(
            db.or_(
                ProfitRule.max_price <= max_price_filter,
                ProfitRule.max_price.is_(None)
            )
        )
    
    # Apply sorting
    if sort_by == 'min_price':
        if sort_order == 'asc':
            query = query.order_by(ProfitRule.min_price.asc())
        else:
            query = query.order_by(ProfitRule.min_price.desc())
    elif sort_by == 'profit_amount':
        if sort_order == 'asc':
            query = query.order_by(ProfitRule.profit_amount.asc())
        else:
            query = query.order_by(ProfitRule.profit_amount.desc())
    else:  # priority (default)
        if sort_order == 'asc':
            query = query.order_by(ProfitRule.priority.asc())
        else:
            query = query.order_by(ProfitRule.priority.desc())
    
    # Paginate
    rules = query.paginate(page=page, per_page=per_page, error_out=False)
    
    # Get statistics
    total_rules = ProfitRule.query.count()
    active_rules = ProfitRule.query.filter_by(is_active=True).count()
    
    return render_template('admin/admin/profit_rules.html',
                         rules=rules,
                         search=search,
                         status_filter=status_filter,
                         min_price_filter=min_price_filter,
                         max_price_filter=max_price_filter,
                         sort_by=sort_by,
                         sort_order=sort_order,
                         total_rules=total_rules,
                         active_rules=active_rules)

@app.route('/admin/profit-rules/new', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_new_profit_rule():
    """Create a new profit rule."""
    if request.method == 'POST':
        try:
            min_price = request.form.get('min_price')
            max_price = request.form.get('max_price', '').strip()
            profit_amount = request.form.get('profit_amount')
            priority = request.form.get('priority', 0)
            is_active = request.form.get('is_active') == 'on'
            note = request.form.get('note', '').strip()
            
            # Validation
            if not min_price or not profit_amount:
                flash('Min price and profit amount are required', 'error')
                return redirect(url_for('admin_new_profit_rule'))
            
            min_price = Decimal(str(min_price))
            max_price = Decimal(str(max_price)) if max_price else None
            profit_amount = Decimal(str(profit_amount))
            priority = int(priority) if priority else 0
            
            if min_price < 0:
                flash('Min price must be >= 0', 'error')
                return redirect(url_for('admin_new_profit_rule'))
            
            if max_price is not None and max_price <= min_price:
                flash('Max price must be greater than min price', 'error')
                return redirect(url_for('admin_new_profit_rule'))
            
            if profit_amount < 0:
                flash('Profit amount must be >= 0', 'error')
                return redirect(url_for('admin_new_profit_rule'))
            
            profit_rule = ProfitRule(
                min_price=min_price,
                max_price=max_price,
                profit_amount=profit_amount,
                priority=priority,
                is_active=is_active,
                note=note
            )
            
            db.session.add(profit_rule)
            db.session.commit()
            
            flash('Profit rule created successfully!', 'success')
            return redirect(url_for('admin_profit_rules'))
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error creating profit rule: {e}")
            flash(f'Failed to create profit rule: {str(e)}', 'error')
            return redirect(url_for('admin_new_profit_rule'))
    
    return render_template('admin/admin/profit_rule_form.html', profit_rule=None)

@app.route('/admin/profit-rules/<int:rule_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_profit_rule(rule_id):
    """Edit an existing profit rule."""
    profit_rule = ProfitRule.query.get_or_404(rule_id)
    
    if request.method == 'POST':
        try:
            min_price = request.form.get('min_price')
            max_price = request.form.get('max_price', '').strip()
            profit_amount = request.form.get('profit_amount')
            priority = request.form.get('priority', 0)
            is_active = request.form.get('is_active') == 'on'
            note = request.form.get('note', '').strip()
            
            # Validation
            if not min_price or not profit_amount:
                flash('Min price and profit amount are required', 'error')
                return redirect(url_for('admin_edit_profit_rule', rule_id=rule_id))
            
            min_price = Decimal(str(min_price))
            max_price = Decimal(str(max_price)) if max_price else None
            profit_amount = Decimal(str(profit_amount))
            priority = int(priority) if priority else 0
            
            if min_price < 0:
                flash('Min price must be >= 0', 'error')
                return redirect(url_for('admin_edit_profit_rule', rule_id=rule_id))
            
            if max_price is not None and max_price <= min_price:
                flash('Max price must be greater than min price', 'error')
                return redirect(url_for('admin_edit_profit_rule', rule_id=rule_id))
            
            if profit_amount < 0:
                flash('Profit amount must be >= 0', 'error')
                return redirect(url_for('admin_edit_profit_rule', rule_id=rule_id))
            
            profit_rule.min_price = min_price
            profit_rule.max_price = max_price
            profit_rule.profit_amount = profit_amount
            profit_rule.priority = priority
            profit_rule.is_active = is_active
            profit_rule.note = note
            profit_rule.updated_at = datetime.utcnow()
            
            db.session.commit()
            
            flash('Profit rule updated successfully!', 'success')
            return redirect(url_for('admin_profit_rules'))
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating profit rule: {e}")
            flash(f'Failed to update profit rule: {str(e)}', 'error')
            return redirect(url_for('admin_edit_profit_rule', rule_id=rule_id))
    
    return render_template('admin/admin/profit_rule_form.html', profit_rule=profit_rule)

@app.route('/admin/profit-rules/<int:rule_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_profit_rule(rule_id):
    """Delete a profit rule."""
    profit_rule = ProfitRule.query.get_or_404(rule_id)
    
    try:
        db.session.delete(profit_rule)
        db.session.commit()
        flash('Profit rule deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting profit rule: {e}")
        flash('Failed to delete profit rule. Please try again.', 'error')
    
    return redirect(url_for('admin_profit_rules'))

@app.route('/admin/profit-rules/<int:rule_id>/toggle', methods=['POST'])
@login_required
@admin_required
def admin_toggle_profit_rule(rule_id):
    """Toggle profit rule active status."""
    profit_rule = ProfitRule.query.get_or_404(rule_id)
    
    try:
        profit_rule.is_active = not profit_rule.is_active
        profit_rule.updated_at = datetime.utcnow()
        db.session.commit()
        status = "activated" if profit_rule.is_active else "deactivated"
        flash(f'Profit rule {status} successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error toggling profit rule: {e}")
        flash('Failed to toggle profit rule status. Please try again.', 'error')
    
    return redirect(url_for('admin_profit_rules'))

@app.route('/admin/profit-report', methods=['GET'])
@login_required
@admin_required
def admin_profit_report():
    """Admin page for viewing profit reports."""
    # Get date filter
    date_filter = request.args.get('date_filter', '30days')
    start_date = None
    end_date = None
    
    now = datetime.utcnow()
    
    if date_filter == 'today':
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = now
    elif date_filter == 'yesterday':
        yesterday = now - timedelta(days=1)
        start_date = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif date_filter == '7days':
        start_date = now - timedelta(days=7)
        end_date = now
    elif date_filter == '30days':
        start_date = now - timedelta(days=30)
        end_date = now
    elif date_filter == 'this_month':
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = now
    elif date_filter == 'last_month':
        first_day_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start_date = (first_day_this_month - timedelta(days=1)).replace(day=1)
        end_date = first_day_this_month - timedelta(seconds=1)
    elif date_filter == 'this_year':
        start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = now
    elif date_filter == 'all_time':
        start_date = None
        end_date = None
    
    # Build query for orders - only show paid orders (exclude cancelled and pending)
    query = Order.query.filter(
        Order.status.in_(['paid', 'completed']),
        Order.status != 'cancelled'
    )
    
    if start_date:
        query = query.filter(Order.created_at >= start_date)
    if end_date:
        query = query.filter(Order.created_at <= end_date)
    
    orders = query.all()
    
    # Calculate statistics
    total_orders = len(orders)
    total_revenue = sum(order.total_revenue_gmd or order.total or 0.0 for order in orders)
    total_profit = sum(order.total_profit_gmd or 0.0 for order in orders)
    profit_margin = (total_profit / total_revenue * 100) if total_revenue > 0 else 0.0
    
    # Daily breakdown
    daily_stats = {}
    for order in orders:
        date_key = order.created_at.date().isoformat()
        if date_key not in daily_stats:
            daily_stats[date_key] = {
                'date': order.created_at.date(),
                'orders': 0,
                'revenue': 0.0,
                'profit': 0.0
            }
        daily_stats[date_key]['orders'] += 1
        daily_stats[date_key]['revenue'] += order.total_revenue_gmd or order.total or 0.0
        daily_stats[date_key]['profit'] += order.total_profit_gmd or 0.0
    
    # Sort by date
    daily_breakdown = sorted(daily_stats.values(), key=lambda x: x['date'], reverse=True)
    
    # Calculate period summaries
    today = now.date()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)
    
    today_orders = Order.query.filter(
        Order.status.in_(['paid', 'completed']),
        Order.status != 'cancelled',
        db.func.date(Order.created_at) == today
    ).all()
    today_profit = sum(order.total_profit_gmd or 0.0 for order in today_orders)
    today_revenue = sum(order.total_revenue_gmd or order.total or 0.0 for order in today_orders)
    
    week_orders = Order.query.filter(
        Order.status.in_(['paid', 'completed']),
        Order.status != 'cancelled',
        db.func.date(Order.created_at) >= week_start
    ).all()
    week_profit = sum(order.total_profit_gmd or 0.0 for order in week_orders)
    week_revenue = sum(order.total_revenue_gmd or order.total or 0.0 for order in week_orders)
    
    month_orders = Order.query.filter(
        Order.status.in_(['paid', 'completed']),
        Order.status != 'cancelled',
        db.func.date(Order.created_at) >= month_start
    ).all()
    month_profit = sum(order.total_profit_gmd or 0.0 for order in month_orders)
    month_revenue = sum(order.total_revenue_gmd or order.total or 0.0 for order in month_orders)
    
    year_orders = Order.query.filter(
        Order.status.in_(['paid', 'completed']),
        Order.status != 'cancelled',
        db.func.date(Order.created_at) >= year_start
    ).all()
    year_profit = sum(order.total_profit_gmd or 0.0 for order in year_orders)
    year_revenue = sum(order.total_revenue_gmd or order.total or 0.0 for order in year_orders)
    
    all_time_orders = Order.query.filter(
        Order.status.in_(['paid', 'completed']),
        Order.status != 'cancelled'
    ).all()
    all_time_profit = sum(order.total_profit_gmd or 0.0 for order in all_time_orders)
    all_time_revenue = sum(order.total_revenue_gmd or order.total or 0.0 for order in all_time_orders)
    
    return render_template('admin/admin/profit_report.html',
                         date_filter=date_filter,
                         total_orders=total_orders,
                         total_revenue=total_revenue,
                         total_profit=total_profit,
                         profit_margin=profit_margin,
                         daily_breakdown=daily_breakdown,
                         today_profit=today_profit,
                         today_revenue=today_revenue,
                         week_profit=week_profit,
                         week_revenue=week_revenue,
                         month_profit=month_profit,
                         month_revenue=month_revenue,
                         year_profit=year_profit,
                         year_revenue=year_revenue,
                         all_time_profit=all_time_profit,
                         all_time_revenue=all_time_revenue)

# ==================== Currency Rates Admin Routes ====================

@app.route('/admin/currencies')
@login_required
@admin_required
def admin_currencies():
    """Admin page for managing currency conversion rates with search, sorting, and pagination."""
    # Get query parameters
    search = request.args.get('search', '').strip()
    sort_by = request.args.get('sort', 'from_currency')  # from_currency, to_currency, rate, last_updated, status
    order = request.args.get('order', 'asc')  # asc or desc
    status_filter = request.args.get('status', 'all')  # all, active, inactive
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    
    # Build query
    query = CurrencyRate.query
    
    # Apply search filter
    if search:
        query = query.filter(
            db.or_(
                CurrencyRate.from_currency.ilike(f'%{search}%'),
                CurrencyRate.to_currency.ilike(f'%{search}%'),
                CurrencyRate.notes.ilike(f'%{search}%')
            )
        )
    
    # Apply status filter
    if status_filter == 'active':
        query = query.filter(CurrencyRate.is_active == True)
    elif status_filter == 'inactive':
        query = query.filter(CurrencyRate.is_active == False)
    
    # Apply sorting
    if sort_by == 'from_currency':
        order_by = CurrencyRate.from_currency.asc() if order == 'asc' else CurrencyRate.from_currency.desc()
    elif sort_by == 'to_currency':
        order_by = CurrencyRate.to_currency.asc() if order == 'asc' else CurrencyRate.to_currency.desc()
    elif sort_by == 'rate':
        order_by = CurrencyRate.rate.asc() if order == 'asc' else CurrencyRate.rate.desc()
    elif sort_by == 'last_updated':
        order_by = CurrencyRate.last_updated.asc() if order == 'asc' else CurrencyRate.last_updated.desc()
    elif sort_by == 'status':
        order_by = CurrencyRate.is_active.asc() if order == 'asc' else CurrencyRate.is_active.desc()
    else:
        order_by = CurrencyRate.from_currency.asc()
    
    query = query.order_by(order_by)
    
    # Paginate
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    currency_rates = pagination.items
    
    return render_template('admin/admin/currencies.html',
                         currency_rates=currency_rates,
                         pagination=pagination,
                         search=search,
                         sort_by=sort_by,
                         order=order,
                         status_filter=status_filter,
                         per_page=per_page)

@app.route('/admin/currencies/add', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_add_currency_rate():
    """Add a new currency conversion rate."""
    if request.method == 'POST':
        try:
            from_currency = request.form.get('from_currency', '').strip().upper()
            to_currency = request.form.get('to_currency', '').strip().upper()
            rate_str = request.form.get('rate', '').strip()
            is_active = request.form.get('is_active') == 'on'
            notes = request.form.get('notes', '').strip()
            api_sync_enabled = request.form.get('api_sync_enabled') == 'on'
            api_provider = request.form.get('api_provider', '').strip() or None
            
            # Validate required fields
            if not all([from_currency, to_currency, rate_str]):
                flash('Please fill in all required fields (From Currency, To Currency, Rate).', 'error')
                return redirect(url_for('admin_add_currency_rate'))
            
            # Validate currency pair (cannot be same)
            if from_currency == to_currency:
                flash('From Currency and To Currency cannot be the same.', 'error')
                return redirect(url_for('admin_add_currency_rate'))
            
            # Validate and parse rate
            try:
                rate = Decimal(rate_str)
                if rate <= 0:
                    flash('Conversion rate must be a positive number.', 'error')
                    return redirect(url_for('admin_add_currency_rate'))
            except (ValueError, InvalidOperation):
                flash('Invalid conversion rate. Please enter a valid decimal number.', 'error')
                return redirect(url_for('admin_add_currency_rate'))
            
            # Check if currency pair already exists
            existing = CurrencyRate.query.filter_by(
                from_currency=from_currency,
                to_currency=to_currency
            ).first()
            
            if existing:
                flash(f'Currency pair {from_currency} → {to_currency} already exists. Please edit it instead.', 'error')
                return redirect(url_for('admin_edit_currency_rate', rate_id=existing.id))
            
            currency_rate = CurrencyRate(
                from_currency=from_currency,
                to_currency=to_currency,
                rate=rate,
                is_active=is_active,
                notes=notes,
                api_sync_enabled=api_sync_enabled,
                api_provider=api_provider,
                last_updated=datetime.utcnow()
            )
            
            db.session.add(currency_rate)
            db.session.commit()
            
            flash(f'Currency rate {from_currency} → {to_currency} added successfully!', 'success')
            return redirect(url_for('admin_currencies'))
            
        except IntegrityError:
            db.session.rollback()
            flash('Currency pair already exists.', 'error')
            return redirect(url_for('admin_add_currency_rate'))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error adding currency rate: {e}")
            flash('Failed to add currency rate. Please try again.', 'error')
            return redirect(url_for('admin_add_currency_rate'))
    
    return render_template('admin/admin/currency_rate_form.html', currency_rate=None)

@app.route('/admin/currencies/<int:rate_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_currency_rate(rate_id):
    """Edit an existing currency conversion rate."""
    currency_rate = CurrencyRate.query.get_or_404(rate_id)
    
    if request.method == 'POST':
        try:
            from_currency = request.form.get('from_currency', '').strip().upper()
            to_currency = request.form.get('to_currency', '').strip().upper()
            rate_str = request.form.get('rate', '').strip()
            is_active = request.form.get('is_active') == 'on'
            notes = request.form.get('notes', '').strip()
            api_sync_enabled = request.form.get('api_sync_enabled') == 'on'
            api_provider = request.form.get('api_provider', '').strip() or None
            
            # Validate required fields
            if not all([from_currency, to_currency, rate_str]):
                flash('Please fill in all required fields.', 'error')
                return redirect(url_for('admin_edit_currency_rate', rate_id=rate_id))
            
            # Validate currency pair
            if from_currency == to_currency:
                flash('From Currency and To Currency cannot be the same.', 'error')
                return redirect(url_for('admin_edit_currency_rate', rate_id=rate_id))
            
            # Validate and parse rate
            try:
                rate = Decimal(rate_str)
                if rate <= 0:
                    flash('Conversion rate must be a positive number.', 'error')
                    return redirect(url_for('admin_edit_currency_rate', rate_id=rate_id))
            except (ValueError, InvalidOperation):
                flash('Invalid conversion rate. Please enter a valid decimal number.', 'error')
                return redirect(url_for('admin_edit_currency_rate', rate_id=rate_id))
            
            # Check if currency pair already exists (and is not this one)
            existing = CurrencyRate.query.filter(
                CurrencyRate.from_currency == from_currency,
                CurrencyRate.to_currency == to_currency,
                CurrencyRate.id != rate_id
            ).first()
            
            if existing:
                flash(f'Currency pair {from_currency} → {to_currency} already exists.', 'error')
                return redirect(url_for('admin_edit_currency_rate', rate_id=rate_id))
            
            # Update currency rate
            currency_rate.from_currency = from_currency
            currency_rate.to_currency = to_currency
            currency_rate.rate = rate
            currency_rate.is_active = is_active
            currency_rate.notes = notes
            currency_rate.api_sync_enabled = api_sync_enabled
            currency_rate.api_provider = api_provider
            currency_rate.last_updated = datetime.utcnow()
            
            db.session.commit()
            flash(f'Currency rate {from_currency} → {to_currency} updated successfully!', 'success')
            return redirect(url_for('admin_currencies'))
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating currency rate: {e}")
            flash('Failed to update currency rate. Please try again.', 'error')
            return redirect(url_for('admin_edit_currency_rate', rate_id=rate_id))
    
    return render_template('admin/admin/currency_rate_form.html', currency_rate=currency_rate)

@app.route('/admin/currencies/<int:rate_id>/toggle', methods=['POST'])
@login_required
@admin_required
def admin_toggle_currency_rate(rate_id):
    """Toggle currency rate active/inactive status."""
    currency_rate = CurrencyRate.query.get_or_404(rate_id)
    
    try:
        currency_rate.is_active = not currency_rate.is_active
        db.session.commit()
        status = "activated" if currency_rate.is_active else "deactivated"
        flash(f'Currency rate {currency_rate.from_currency} → {currency_rate.to_currency} {status} successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error toggling currency rate: {e}")
        flash('Failed to toggle currency rate status. Please try again.', 'error')
    
    return redirect(url_for('admin_currencies'))

@app.route('/admin/currencies/<int:rate_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_delete_currency_rate(rate_id):
    """Delete a currency conversion rate."""
    currency_rate = CurrencyRate.query.get_or_404(rate_id)
    
    try:
        pair_name = f"{currency_rate.from_currency} → {currency_rate.to_currency}"
        db.session.delete(currency_rate)
        db.session.commit()
        flash(f'Currency rate {pair_name} deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting currency rate: {e}")
        flash('Failed to delete currency rate. Please try again.', 'error')
    
    return redirect(url_for('admin_currencies'))

@app.route('/admin/currencies/<int:rate_id>/refresh', methods=['POST'])
@login_required
@admin_required
def admin_refresh_currency_rate(rate_id):
    """Manually refresh a currency rate from API."""
    currency_rate = CurrencyRate.query.get_or_404(rate_id)
    
    try:
        from app.utils.currency_api import sync_currency_rate_from_api
        import os
        
        # Get API key from form or environment
        api_key = request.form.get('api_key', '').strip() or os.getenv(f'{currency_rate.api_provider.upper()}_API_KEY', '')
        provider = currency_rate.api_provider or None
        
        success, error, rate = sync_currency_rate_from_api(
            currency_rate.from_currency,
            currency_rate.to_currency,
            provider,
            api_key
        )
        
        if success and rate:
            currency_rate.rate = rate
            currency_rate.last_api_sync = datetime.utcnow()
            currency_rate.api_sync_error = None
            currency_rate.last_updated = datetime.utcnow()
            db.session.commit()
            flash(f'Currency rate {currency_rate.from_currency} → {currency_rate.to_currency} refreshed successfully! New rate: {rate}', 'success')
        else:
            currency_rate.api_sync_error = error or "Unknown error"
            db.session.commit()
            flash(f'Failed to refresh currency rate: {error or "Unknown error"}', 'error')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error refreshing currency rate: {e}")
        flash(f'Failed to refresh currency rate: {str(e)}', 'error')
    
    return redirect(url_for('admin_currencies'))

@app.route('/admin/currencies/bulk-refresh', methods=['POST'])
@login_required
@admin_required
def admin_bulk_refresh_currency_rates():
    """Bulk refresh all currency rates that have API sync enabled."""
    try:
        from app.utils.currency_api import sync_currency_rate_from_api
        import os
        
        # Get rates with API sync enabled
        rates_to_sync = CurrencyRate.query.filter_by(api_sync_enabled=True, is_active=True).all()
        
        if not rates_to_sync:
            flash('No currency rates have API sync enabled.', 'info')
            return redirect(url_for('admin_currencies'))
        
        updated = 0
        failed = 0
        errors = []
        
        for currency_rate in rates_to_sync:
            try:
                api_key = os.getenv(f'{currency_rate.api_provider.upper()}_API_KEY', '') if currency_rate.api_provider else None
                provider = currency_rate.api_provider or None
                
                success, error, rate = sync_currency_rate_from_api(
                    currency_rate.from_currency,
                    currency_rate.to_currency,
                    provider,
                    api_key
                )
                
                if success and rate:
                    currency_rate.rate = rate
                    currency_rate.last_api_sync = datetime.utcnow()
                    currency_rate.api_sync_error = None
                    currency_rate.last_updated = datetime.utcnow()
                    updated += 1
                else:
                    currency_rate.api_sync_error = error or "Unknown error"
                    failed += 1
                    errors.append(f"{currency_rate.from_currency} → {currency_rate.to_currency}: {error or 'Unknown error'}")
            except Exception as e:
                failed += 1
                error_msg = str(e)
                errors.append(f"{currency_rate.from_currency} → {currency_rate.to_currency}: {error_msg}")
                current_app.logger.error(f"Error refreshing {currency_rate.from_currency} → {currency_rate.to_currency}: {e}")
        
        db.session.commit()
        
        message = f'Bulk refresh completed: {updated} updated, {failed} failed.'
        if errors:
            message += f' Errors: {"; ".join(errors[:5])}'  # Show first 5 errors
            if len(errors) > 5:
                message += f' (and {len(errors) - 5} more...)'
        
        flash(message, 'success' if failed == 0 else 'error')
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in bulk refresh: {e}")
        flash(f'Bulk refresh failed: {str(e)}', 'error')
    
    return redirect(url_for('admin_currencies'))

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
        
        # Check FROM email domain (non-blocking - only warns, never blocks)
        # This ensures the system works with restricted API keys
        from_email = settings.resend_from_email if settings else os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev")
        if from_email:
            from app.utils.resend_domain import is_from_email_domain_verified
            is_verified, error_msg, can_verify = is_from_email_domain_verified(from_email, resend_api_key)
            if not is_verified:
                # Always allow sending - domain verification is optional
                if not can_verify:
                    log.warning(
                        f"admin_email_customers[POST]: Domain verification skipped due to API key restrictions. "
                        f"Email sending will proceed. {error_msg}"
                    )
                else:
                    log.warning(
                        f"admin_email_customers[POST]: FROM email domain not verified: {from_email} - {error_msg}. "
                        f"Email sending will proceed anyway."
                    )
                # Continue with email sending - never block on domain verification

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
        
        # Use new bulk email orchestrator
        from app.services.bulk_email_orchestrator import BulkEmailOrchestrator
        from app.utils.email_queue import queue_single_email  # Still use for test emails

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
            app_obj = app
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
            f"admin_email_customers[POST]: creating bulk email job for {estimated_total} customers",
            extra={"queued_for": estimated_total},
        )

        # Create job using new orchestrator
        job_id, error_message = BulkEmailOrchestrator.create_and_queue_bulk_email_job(
            subject=subject,
            body=body,
            from_email=from_email,
            recipients_query=base_query,
            metadata={"created_by": "admin_email_customers"},
        )

        if not job_id:
            log.error(f"admin_email_customers[POST]: Failed to create job: {error_message}")
            
            # Check if this is a missing table error
            error_msg = error_message or ""
            if "does not exist" in error_msg or "UndefinedTable" in error_msg:
                return jsonify(
                    {
                        "success": False,
                        "status": "error",
                        "message": (
                            "Database tables for bulk email system are missing. "
                            "Please run migrations: Go to Admin → Database Migration Manager "
                            "or run 'python -m alembic upgrade head' via Render Shell."
                        ),
                    }
                ), 500
            
            return jsonify(
                {
                    "success": False,
                    "status": "error",
                    "message": f"Failed to create email job: {error_message}",
                }
            ), 500

        response_payload = {
            "success": True,
            "status": "queued",
            "job_id": str(job_id),
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
    import uuid
    
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        return jsonify({
            "success": False,
            "status": "error",
            "message": "Invalid job_id format"
        }), 400
    
    from app.services.bulk_email_orchestrator import BulkEmailOrchestrator
    
    status = BulkEmailOrchestrator.get_job_status(job_uuid)
    
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
        "sent": status.get("sent_count", 0),
        "failed": status.get("failed_count", 0),
        "total": status.get("total_recipients", 0),
        "current_progress": status.get("current_progress", 0),
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
    from app.utils.whatsapp_token import get_whatsapp_token, get_token_status
    
    # Get token status (includes validation)
    token_status = get_token_status()
    
    # Check if WhatsApp is configured
    if not token_status['configured']:
        return render_template('admin/admin/whatsapp.html',
                               error="WhatsApp is not configured. Set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID in Settings.",
                               configured=False,
                               token_status=token_status,
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
                         message_logs=formatted_logs,
                         token_status=token_status), 200

@app.route('/admin/send_test_whatsapp', methods=['POST'])
@login_required
@admin_required
def admin_send_test_whatsapp():
    """Send a test WhatsApp message using hello_world template."""
    from app.utils.whatsapp_token import get_whatsapp_token, check_token_expiration_from_error
    
    # Get WhatsApp credentials dynamically (DB first, then .env)
    access_token, phone_number_id = get_whatsapp_token()
    
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
            # Parse error response for detailed information
            error_info = response_data.get('error', {}) if isinstance(response_data, dict) else {}
            error_code = error_info.get('code')
            error_subcode = error_info.get('error_subcode')
            error_message_text = error_info.get('message', str(response_data)[:200])
            error_type = error_info.get('type', 'UnknownError')
            
            # Check if token is expired
            error_response_dict = {
                'status_code': response.status_code,
                'error_code': error_code,
                'error_subcode': error_subcode,
                'message': error_message_text,
                'error_type': error_type
            }
            
            is_expired = check_token_expiration_from_error(error_response_dict)
            
            if is_expired:
                error_message = (
                    f'❌ WhatsApp access token has EXPIRED (HTTP {response.status_code}).\n\n'
                    f'Error Code: {error_code}\n'
                    f'Error Subcode: {error_subcode}\n'
                    f'Message: {error_message_text}\n\n'
                    f'Please generate a new token from Meta Developer Console:\n'
                    f'https://developers.facebook.com/apps → WhatsApp → API Setup → Generate Token'
                )
            else:
                error_message = (
                    f'❌ Failed to send test message (HTTP {response.status_code}).\n\n'
                    f'Error Code: {error_code}\n'
                    f'Error Subcode: {error_subcode}\n'
                    f'Error Type: {error_type}\n'
                    f'Message: {error_message_text}'
                )
            
            flash(error_message, 'error')
            current_app.logger.error(f"❌ Failed to send WhatsApp test message: HTTP {response.status_code} - {error_message_text}")
            
            # Store full error response for debugging
            session['whatsapp_error_response'] = {
                'status_code': response.status_code,
                'response': response_data,
                'request_payload': payload,
                'error_code': error_code,
                'error_subcode': error_subcode,
                'error_type': error_type,
                'is_expired': is_expired
            }
        
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
    from app.utils.whatsapp_token import get_whatsapp_token
    
    message = request.form.get('message', '').strip()
    
    # Validate required fields
    if not message:
        flash('Message text is required', 'error')
        return redirect(url_for('admin_whatsapp'))
    
    # Get WhatsApp credentials dynamically (DB first, then .env)
    access_token, phone_number_id = get_whatsapp_token()
    
    if not access_token or not phone_number_id:
        flash('WhatsApp is not configured. Please set WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID in Settings', 'error')
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
                welcome_message = f"Hi {user_name} 👋\nWelcome to buxin store! You'll now receive updates about our robotics and AI innovations. 🚀"
                
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
        return render_template('admin/admin/partials/_products_table.html', products=products, categories=categories)
        
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
    categories = Category.query.all()
    return render_template('admin/admin/partials/_products_table.html', products=products, categories=categories)

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

@app.route('/admin/products/bulk-update', methods=['POST'])
@login_required
@admin_required
def admin_bulk_update_products():
    """Bulk update multiple products"""
    try:
        if not request.is_json:
            return jsonify({
                'success': False,
                'message': 'Request must be JSON'
            }), 400
        
        data = request.get_json()
        updates = data.get('updates', [])
        
        if not updates:
            return jsonify({
                'success': False,
                'message': 'No updates provided'
            }), 400
        
        updated_count = 0
        errors = []
        
        for update in updates:
            product_id = update.get('product_id')
            if not product_id:
                errors.append(f'Missing product_id in update: {update}')
                continue
            
            try:
                product = Product.query.get(product_id)
                if not product:
                    errors.append(f'Product {product_id} not found')
                    continue
                
                # Update fields if provided
                if 'name' in update:
                    name = update['name'].strip()
                    if name:
                        product.name = name
                    else:
                        errors.append(f'Product {product_id}: Name cannot be empty')
                        continue
                
                if 'category_id' in update:
                    category_id = update['category_id']
                    category = Category.query.get(category_id)
                    if category:
                        product.category_id = category_id
                    else:
                        errors.append(f'Product {product_id}: Invalid category_id {category_id}')
                        continue
                
                if 'price' in update:
                    price = float(update['price'])
                    if price >= 0:
                        product.price = price
                    else:
                        errors.append(f'Product {product_id}: Price must be >= 0')
                        continue
                
                if 'stock' in update:
                    stock = int(update['stock'])
                    if stock >= 0:
                        product.stock = stock
                    else:
                        errors.append(f'Product {product_id}: Stock must be >= 0')
                        continue
                
                if 'available_in_gambia' in update:
                    product.available_in_gambia = bool(update['available_in_gambia'])
                
                if 'shipping_price' in update:
                    shipping_price = update['shipping_price']
                    if shipping_price is not None and shipping_price != '':
                        shipping_price = float(shipping_price)
                        if shipping_price >= 0:
                            product.shipping_price = shipping_price
                        else:
                            errors.append(f'Product {product_id}: Shipping price must be >= 0')
                            continue
                    else:
                        product.shipping_price = None
                
                if 'image' in update:
                    image_url = update['image'].strip() if update['image'] else None
                    if image_url:
                        # Validate URL
                        if image_url.startswith('http://') or image_url.startswith('https://'):
                            product.image = image_url
                        else:
                            errors.append(f'Product {product_id}: Image URL must be a valid HTTP/HTTPS URL')
                            continue
                    else:
                        product.image = None
                
                updated_count += 1
                
            except ValueError as e:
                errors.append(f'Product {product_id}: Invalid value - {str(e)}')
                continue
            except Exception as e:
                errors.append(f'Product {product_id}: Error - {str(e)}')
                app.logger.error(f"Error updating product {product_id}: {e}")
                continue
        
        if errors and updated_count == 0:
            # All updates failed
            db.session.rollback()
            return jsonify({
                'success': False,
                'message': 'All updates failed',
                'errors': errors
            }), 400
        
        # Commit all successful updates
        db.session.commit()
        
        response = {
            'success': True,
            'updated_count': updated_count,
            'message': f'Successfully updated {updated_count} product(s)'
        }
        
        if errors:
            response['errors'] = errors
            response['message'] += f' ({len(errors)} error(s))'
        
        return jsonify(response)
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error in bulk update: {e}")
        import traceback
        app.logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'message': f'Unexpected error: {str(e)}'
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
        
        # Weight (kg) is REQUIRED - form validation ensures it's present and valid
        weight_kg = form.weight_kg.data
        if weight_kg is None or weight_kg < 0.00001 or weight_kg > 500:
            flash('Weight must be between 0.00001 and 500 kg', 'error')
            return redirect(url_for('admin_add_product'))
        
        product = Product(
            name=form.name.data,
            description=form.description.data,
            price=form.price.data,
            stock=form.stock.data,
            category_id=form.category_id.data,
            weight_kg=weight_kg,  # REQUIRED
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
        
        # Weight (kg) is REQUIRED - form validation ensures it's present and valid
        weight_kg = form.weight_kg.data
        if weight_kg is None or weight_kg < 0.00001 or weight_kg > 500:
            flash('Weight must be between 0.00001 and 500 kg', 'error')
            return redirect(url_for('admin_edit_product', product_id=product_id))
        product.weight_kg = weight_kg  # REQUIRED
        
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
            # Get uploaded image files and create a filename mapping (case-insensitive)
            uploaded_images = request.files.getlist('images')
            image_files_dict = {}
            for img_file in uploaded_images:
                if img_file and img_file.filename:
                    # Store with lowercase key for case-insensitive matching
                    filename_lower = img_file.filename.lower()
                    image_files_dict[filename_lower] = img_file
            
            # Read the uploaded file
            if file.filename.endswith('.xlsx'):
                df = pd.read_excel(file)
            else:  # CSV
                df = pd.read_csv(file)
            
            # Convert column names to lowercase and strip whitespace
            df.columns = df.columns.str.strip().str.lower()
            required_columns = ['product name', 'category', 'weight (kg)']
            
            # Validate required columns
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                flash(f'Missing required columns: {", ".join(missing_columns)}. Weight (kg) is now required.', 'error')
                return redirect(request.url)
            
            results = []
            stats = {
                'cloudinary_images': 0,
                'url_images': 0,
                'no_images': 0,
                'errors': 0
            }
            
            # Import Cloudinary utilities
            from .utils.cloudinary_utils import upload_to_cloudinary, is_cloudinary_url, delete_from_cloudinary, get_public_id_from_url
            
            # Process each row
            for _, row in df.iterrows():
                try:
                    # Get or create category
                    category_name = str(row['category']).strip()
                    if not category_name:
                        raise ValueError('Category cannot be empty')
                    
                    category = Category.query.filter_by(name=category_name).first()
                    if not category:
                        category = Category(name=category_name)
                        db.session.add(category)
                        db.session.commit()
                    
                    # Check if product exists
                    product_name = str(row['product name']).strip()
                    if not product_name:
                        raise ValueError('Product name cannot be empty')
                    
                    product = Product.query.filter_by(name=product_name).first()
                    
                    # Prepare product data - support both old and new column names
                    price = 0
                    if 'price' in row and pd.notna(row['price']):
                        price = float(row['price'])
                    elif 'price (gmd)' in row and pd.notna(row['price (gmd)']):
                        price = float(row['price (gmd)'])
                    
                    stock = 0
                    if 'stock' in row and pd.notna(row['stock']):
                        stock = int(row['stock'])
                    elif 'stock quantity' in row and pd.notna(row['stock quantity']):
                        stock = int(row['stock quantity'])
                    
                    description = str(row.get('description', '')) if pd.notna(row.get('description')) else ''
                    
                    # Validate and process weight (kg) - REQUIRED
                    weight_kg = None
                    if 'weight (kg)' in row:
                        weight_value = row['weight (kg)']
                        if pd.isna(weight_value) or weight_value == '':
                            raise ValueError(f'Weight (kg) is required but missing for product "{product_name}"')
                        
                        try:
                            weight_kg = float(weight_value)
                            # Validate weight range
                            if weight_kg < 0.00001:
                                raise ValueError(f'Weight must be at least 0.00001 kg for product "{product_name}"')
                            if weight_kg > 500:
                                raise ValueError(f'Weight must not exceed 500 kg for product "{product_name}"')
                            if weight_kg < 0:
                                raise ValueError(f'Weight cannot be negative for product "{product_name}"')
                        except (ValueError, TypeError) as e:
                            if isinstance(e, ValueError) and 'Weight' in str(e):
                                raise  # Re-raise our validation errors
                            raise ValueError(f'Invalid weight value for product "{product_name}": {weight_value}. Must be a number between 0.00001 and 500 kg.')
                    else:
                        raise ValueError(f'Weight (kg) column is missing for product "{product_name}"')
                    
                    # Handle image with priority: Image Filename > Image URL > None
                    final_image_url = None
                    image_source = None
                    
                    # Priority 1: Check for Image Filename (uploaded file)
                    image_filename = None
                    if 'image filename' in row and pd.notna(row['image filename']):
                        image_filename = str(row['image filename']).strip()
                    
                    if image_filename:
                        # Try to find matching uploaded file (case-insensitive)
                        image_filename_lower = image_filename.lower()
                        matched_file = image_files_dict.get(image_filename_lower)
                        
                        if matched_file:
                            # Upload to Cloudinary
                            try:
                                upload_result = upload_to_cloudinary(matched_file, folder='products')
                                if upload_result and upload_result.get('url'):
                                    final_image_url = upload_result['url']
                                    image_source = 'cloudinary'
                                    stats['cloudinary_images'] += 1
                                    current_app.logger.info(f"✅ Uploaded image {image_filename} to Cloudinary for product {product_name}")
                                else:
                                    current_app.logger.warning(f"⚠️ Failed to upload {image_filename} to Cloudinary")
                            except Exception as e:
                                current_app.logger.error(f"❌ Error uploading {image_filename} to Cloudinary: {str(e)}")
                                # Continue to fallback to URL if available
                        else:
                            current_app.logger.warning(f"⚠️ Image filename '{image_filename}' not found in uploaded files for product {product_name}")
                    
                    # Priority 2: Fallback to Image URL if no uploaded file was used
                    if not final_image_url:
                        image_url = None
                        if 'image url' in row and pd.notna(row['image url']):
                            image_url = str(row['image url']).strip()
                        
                        if image_url:
                            # Validate URL format
                            if image_url.startswith(('http://', 'https://')):
                                try:
                                    # Upload URL image to Cloudinary
                                    final_image_url = save_image_from_url(image_url, product_name)
                                    if final_image_url:
                                        image_source = 'url'
                                        stats['url_images'] += 1
                                    else:
                                        current_app.logger.warning(f"⚠️ Failed to save image from URL for product {product_name}")
                                except Exception as e:
                                    current_app.logger.error(f"❌ Error saving image from URL for product {product_name}: {str(e)}")
                            else:
                                current_app.logger.warning(f"⚠️ Invalid image URL format for product {product_name}: {image_url}")
                    
                    # Priority 3: No image
                    if not final_image_url:
                        image_source = 'none'
                        stats['no_images'] += 1
                    
                    # Create or update product
                    if product:
                        # Update existing product
                        product.category_id = category.id
                        product.price = price if price > 0 else product.price
                        product.stock = stock
                        product.description = description or product.description
                        product.weight_kg = weight_kg  # REQUIRED - always update weight
                        
                        # Update image if we have a new one
                        if final_image_url:
                            # Delete old image from Cloudinary if it exists
                            if product.image:
                                try:
                                    if is_cloudinary_url(product.image):
                                        public_id = get_public_id_from_url(product.image)
                                        if public_id:
                                            delete_from_cloudinary(public_id)
                                except Exception as e:
                                    current_app.logger.warning(f"⚠️ Error deleting old image: {str(e)}")
                            product.image = final_image_url
                        
                        action = 'updated'
                    else:
                        # Create new product
                        product = Product(
                            name=product_name,
                            description=description,
                            price=price,
                            stock=stock,
                            category_id=category.id,
                            weight_kg=weight_kg,  # REQUIRED
                            image=final_image_url
                        )
                        db.session.add(product)
                        action = 'created'
                    
                    db.session.commit()
                    results.append({
                        'product': product_name,
                        'status': 'success',
                        'message': f'Successfully {action} product',
                        'action': action,
                        'image_source': image_source
                    })
                    
                except Exception as e:
                    db.session.rollback()
                    stats['errors'] += 1
                    error_msg = str(e)
                    current_app.logger.error(f"❌ Error processing product row: {error_msg}")
                    results.append({
                        'product': str(row.get('product name', 'Unknown')),
                        'status': 'error',
                        'message': error_msg,
                        'image_source': None
                    })
            
            flash(f'Successfully processed {len([r for r in results if r["status"] == "success"])} products', 'success')
            return render_template('admin/admin/bulk_upload_results.html', results=results, stats=stats)
            
        except Exception as e:
            current_app.logger.error(f"❌ Error processing bulk upload file: {str(e)}")
            import traceback
            current_app.logger.error(traceback.format_exc())
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
    return render_template('admin/admin/categories.html', categories_with_counts=categories_with_counts, all_categories=categories)

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

@app.route('/admin/categories/bulk-update', methods=['POST'])
@login_required
@admin_required
def admin_bulk_update_categories():
    """Bulk update multiple categories"""
    try:
        if not request.is_json:
            return jsonify({
                'success': False,
                'message': 'Request must be JSON'
            }), 400
        
        data = request.get_json()
        updates = data.get('updates', [])
        
        if not updates:
            return jsonify({
                'success': False,
                'message': 'No updates provided'
            }), 400
        
        updated_count = 0
        merged_count = 0
        errors = []
        
        for update in updates:
            category_id = update.get('category_id')
            if not category_id:
                errors.append(f'Missing category_id in update: {update}')
                continue
            
            try:
                category = Category.query.get(category_id)
                if not category:
                    errors.append(f'Category {category_id} not found')
                    continue
                
                # Update name if provided
                if 'name' in update:
                    name = update['name'].strip()
                    if name:
                        category.name = name
                    else:
                        errors.append(f'Category {category_id}: Name cannot be empty')
                        continue
                
                # Update image URL if provided
                if 'image_url' in update:
                    image_url = update['image_url'].strip() if update['image_url'] else None
                    if image_url:
                        # Validate URL
                        if image_url.startswith('https://'):
                            category.image = image_url
                        else:
                            errors.append(f'Category {category_id}: Image URL must be a valid HTTPS URL')
                            continue
                    else:
                        category.image = None
                
                # Handle merge if provided
                merge_to_category_id = update.get('merge_to_category_id')
                if merge_to_category_id:
                    merge_to_category = Category.query.get(merge_to_category_id)
                    if not merge_to_category:
                        errors.append(f'Category {category_id}: Target category {merge_to_category_id} not found')
                        continue
                    
                    if merge_to_category_id == category_id:
                        errors.append(f'Category {category_id}: Cannot merge category into itself')
                        continue
                    
                    # Move all products from this category to the target category
                    products = Product.query.filter_by(category_id=category_id).all()
                    for product in products:
                        product.category_id = merge_to_category_id
                    
                    merged_count += 1
                    app.logger.info(f'Merged category {category_id} into {merge_to_category_id}, moved {len(products)} products')
                
                updated_count += 1
                
            except ValueError as e:
                errors.append(f'Category {category_id}: Invalid value - {str(e)}')
                continue
            except Exception as e:
                errors.append(f'Category {category_id}: Error - {str(e)}')
                app.logger.error(f"Error updating category {category_id}: {e}")
                continue
        
        if errors and updated_count == 0:
            # All updates failed
            db.session.rollback()
            return jsonify({
                'success': False,
                'message': 'All updates failed',
                'errors': errors
            }), 400
        
        # Commit all successful updates
        db.session.commit()
        
        response = {
            'success': True,
            'updated_count': updated_count,
            'merged_count': merged_count,
            'message': f'Successfully updated {updated_count} category(ies)'
        }
        
        if merged_count > 0:
            response['message'] += f', merged {merged_count} category(ies)'
        
        if errors:
            response['errors'] = errors
            response['message'] += f' ({len(errors)} error(s))'
        
        return jsonify(response)
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error in bulk category update: {e}")
        import traceback
        app.logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'message': f'Unexpected error: {str(e)}'
        }), 500

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
    # Create a sample DataFrame with all required and optional columns
    data = {
        'Product Name': ['Sample Product 1', 'Sample Product 2', 'Sample Product 3'],
        'Category': ['Electronics', 'Clothing', 'Home & Garden'],
        'Price': [1000, 500, 750],
        'Stock': [10, 5, 20],
        'Description': ['Sample description for product 1', 'Another description for product 2', 'Description for product 3'],
        'Image URL': ['https://example.com/image1.jpg', '', 'https://example.com/image3.jpg'],
        'Image Filename': ['product1.jpg', 'product2.png', '']
    }
    
    # Create Excel file in memory
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df = pd.DataFrame(data)
        df.to_excel(writer, index=False, sheet_name='Products')
        
        # Get the worksheet to format it
        worksheet = writer.sheets['Products']
        
        # Auto-adjust column widths
        for idx, col in enumerate(df.columns):
            max_length = max(
                df[col].astype(str).map(len).max(),
                len(str(col))
            )
            worksheet.column_dimensions[chr(65 + idx)].width = min(max_length + 2, 50)
    
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
    country_filter = request.args.get('country', '')  # Country name filter
    page = request.args.get('page', 1, type=int)
    per_page = 20  # Number of orders per page
    
    # Base query - ONLY fully paid orders (status = 'paid' or 'completed')
    # Also include orders with completed payments via Payment table
    # Payment-only logic: no shipping_status logic
    from sqlalchemy import or_
    from app.payments.models import Payment
    
    # Subquery for orders with completed payments
    completed_payments_subquery = db.session.query(Payment.order_id).filter(
        Payment.status == 'completed'
    ).distinct()
    
    query = Order.query.filter(
        or_(
            Order.status.in_(['paid', 'completed']),
            Order.id.in_(completed_payments_subquery)
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
    elif date_filter == 'all':
        # Default to last 30 days when 'all' is selected
        date_start = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
        date_end = now
    
    # Always apply date range to query
    if date_start and date_end:
        query = query.filter(Order.created_at >= date_start, Order.created_at <= date_end)
    
    # Country filtering - filter by user profile country
    if country_filter:
        query = query.join(User, Order.user_id == User.id).join(UserProfile, User.id == UserProfile.user_id).filter(UserProfile.country == country_filter).distinct()
    
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
        
        # Write header (removed Shipping Status)
        writer.writerow(['Order ID', 'Date', 'Customer', 'Email', 'Phone', 'Country', 'Total', 'Status', 'Payment Method', 'Items'])
        
        # Write data
        for order in export_orders:
            items_str = '; '.join([f"{item.product.name if item.product else 'N/A'} (Qty: {item.quantity}, Price: D{item.price})" for item in order.items])
            customer_email = order.customer.email if order.customer else ''
            customer_phone = getattr(order, 'customer_phone', '') or ''
            order_country = order.customer.profile.country if (order.customer and order.customer.profile) else 'N/A'
            writer.writerow([
                order.id,
                order.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                order.customer.username if order.customer else 'N/A',
                customer_email,
                customer_phone,
                order_country,
                f"D{order.total:.2f}",
                order.status,
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
    
    # Get pagination object with eager loading (include UserProfile for country)
    orders = query.options(
        joinedload(Order.items).joinedload(OrderItem.product),
        joinedload(Order.customer).joinedload(User.profile)
    ).order_by(Order.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False)
    
    # Calculate totals for the filtered date range - Payment-only logic
    # Use optimized queries with COALESCE to avoid NULL
    # Include orders with status='paid'/'completed' OR orders with completed payments
    totals_query = Order.query.filter(
        or_(
            Order.status.in_(['paid', 'completed']),
            Order.id.in_(completed_payments_subquery)
        )
    )
    
    # Always apply date range to totals
    if date_start and date_end:
        totals_query = totals_query.filter(Order.created_at >= date_start, Order.created_at <= date_end)
    
    # Apply country filter to totals if set
    if country_filter:
        totals_query = totals_query.join(User, Order.user_id == User.id).join(UserProfile, User.id == UserProfile.user_id).filter(UserProfile.country == country_filter).distinct()
    
    # Total Sales - optimized using COALESCE
    total_sales_result = totals_query.with_entities(db.func.coalesce(db.func.sum(Order.total), 0)).scalar()
    total_sales = float(total_sales_result) if total_sales_result else 0.0
    
    # Total Paid Orders - optimized using COUNT
    total_orders_count = totals_query.count()
    
    # Average Order Value
    avg_order_value = (total_sales / total_orders_count) if total_orders_count > 0 else 0.0
    
    # Total Quantity Sold - optimized query
    quantity_query = db.session.query(db.func.coalesce(db.func.sum(OrderItem.quantity), 0)).join(
        Order, OrderItem.order_id == Order.id
    ).filter(
        or_(
            Order.status.in_(['paid', 'completed']),
            Order.id.in_(completed_payments_subquery)
        )
    )
    if date_start and date_end:
        quantity_query = quantity_query.filter(Order.created_at >= date_start, Order.created_at <= date_end)
    if country_filter:
        quantity_query = quantity_query.join(User, Order.user_id == User.id).join(UserProfile, User.id == UserProfile.user_id).filter(UserProfile.country == country_filter).distinct()
    total_quantity = int(quantity_query.scalar() or 0)
    
    # Total Unique Customers - optimized
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
            # Monthly aggregation for large ranges - Payment-only logic
            daily_sales_query = db.session.query(
                db.func.date_trunc('month', Order.created_at).label('period'),
                db.func.coalesce(db.func.sum(Order.total), 0).label('total')
            ).filter(
                Order.status.in_(['paid', 'completed']),
                Order.created_at >= chart_date_start,
                Order.created_at <= chart_date_end
            ).group_by(db.func.date_trunc('month', Order.created_at)).order_by(db.func.date_trunc('month', Order.created_at))
        else:
            # Daily aggregation (max 30 days) - Payment-only logic
            daily_sales_query = db.session.query(
                db.func.date(Order.created_at).label('date'),
                db.func.coalesce(db.func.sum(Order.total), 0).label('total')
            ).filter(
                Order.status.in_(['paid', 'completed']),
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
            # Monthly aggregation for large ranges - Payment-only logic
            orders_count_query = db.session.query(
                db.func.date_trunc('month', Order.created_at).label('period'),
                db.func.count(Order.id).label('count')
            ).filter(
                Order.status.in_(['paid', 'completed']),
                Order.created_at >= chart_date_start,
                Order.created_at <= chart_date_end
            ).group_by(db.func.date_trunc('month', Order.created_at)).order_by(db.func.date_trunc('month', Order.created_at))
        else:
            # Daily aggregation (max 30 days) - Payment-only logic
            orders_count_query = db.session.query(
                db.func.date(Order.created_at).label('date'),
                db.func.count(Order.id).label('count')
            ).filter(
                Order.status.in_(['paid', 'completed']),
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
    
    # Top Products Chart Data - Payment-only logic
    top_products_query = db.session.query(
        Product.id,
        Product.name,
        db.func.coalesce(db.func.sum(OrderItem.quantity), 0).label('total_quantity'),
        db.func.coalesce(db.func.sum(OrderItem.quantity * OrderItem.price), 0).label('total_revenue')
    ).join(
        OrderItem, Product.id == OrderItem.product_id
    ).join(
        Order, OrderItem.order_id == Order.id
    ).filter(
        Order.status.in_(['paid', 'completed'])
    )
    if date_start and date_end:
        top_products_query = top_products_query.filter(Order.created_at >= date_start, Order.created_at <= date_end)
    top_products_data = top_products_query.group_by(Product.id, Product.name).order_by(
        db.func.sum(OrderItem.quantity * OrderItem.price).desc()
    ).limit(10).all()
        
    # Get all active countries for filter dropdown
    countries = Country.query.filter_by(is_active=True).order_by(Country.name).all()
    countries_dict = []
    for country in countries:
        try:
            countries_dict.append(country.to_dict())
        except Exception as e:
            # Fallback if to_dict() fails for any reason
            current_app.logger.warning(f"Failed to convert country {country.id} to dict: {e}")
            countries_dict.append({
                'id': country.id,
                'name': country.name,
                'code': country.code or '',
                'currency': country.currency or '',
                'currency_symbol': country.currency_symbol or '',
                'language': country.language or 'en',
                'flag_image_path': country.flag_image_path or '',
                'flag_url': None,
                'is_active': country.is_active
            })
    
    return render_template('admin/admin/orders.html',
                         orders=orders, 
                         date_filter=date_filter,
                         start_date=start_date,
                         end_date=end_date,
                         country_filter=country_filter,
                         countries=countries,
                         countries_dict=countries_dict,
                         total_sales=total_sales,
                         total_orders_count=total_orders_count,
                         avg_order_value=avg_order_value,
                         total_quantity=total_quantity,
                         unique_customers=unique_customers,
                         daily_sales_data=daily_sales_data,
                         orders_count_data=orders_count_data,
                         top_products_data=top_products_data,
                         use_monthly_aggregation=use_monthly_aggregation)

@app.route('/admin/orders/reset-all', methods=['POST'])
@login_required
@admin_required
def admin_reset_all_orders():
    """
    Delete all orders and related data (for testing/reset purposes).
    WARNING: This permanently deletes all orders, order items, payments, and related records.
    """
    try:
        from app.payments.models import Payment, PaymentTransaction, ManualPayment, PendingPayment
        
        # Count orders before deletion
        total_orders = Order.query.count()
        
        # Delete in correct order to respect foreign key constraints:
        # 1. PaymentTransaction (references Payment)
        payment_transactions_deleted = db.session.query(PaymentTransaction).delete()
        
        # 2. OrderItem (references Order)
        order_items_deleted = db.session.query(OrderItem).delete()
        
        # 3. ManualPayment (references PendingPayment and Order)
        manual_payments_deleted = db.session.query(ManualPayment).delete()
        
        # 4. Payment (references Order and PendingPayment)
        payments_deleted = db.session.query(Payment).delete()
        
        # 5. PendingPayment (references User and ShippingRule)
        pending_payments_deleted = db.session.query(PendingPayment).delete()
        
        # 6. Order (last, as other tables reference it)
        orders_deleted = db.session.query(Order).delete()
        
        # Commit all deletions
        db.session.commit()
        
        current_app.logger.warning(f"Admin {current_user.id} ({current_user.username}) deleted ALL orders: {orders_deleted} orders, {order_items_deleted} order items, {payments_deleted} payments, {payment_transactions_deleted} payment transactions, {manual_payments_deleted} manual payments, {pending_payments_deleted} pending payments")
        
        return jsonify({
            'success': True,
            'message': f'Successfully deleted all orders and related data ({orders_deleted} orders, {order_items_deleted} order items, {payments_deleted} payments, {payment_transactions_deleted} payment transactions, {manual_payments_deleted} manual payments, {pending_payments_deleted} pending payments)',
            'deleted': {
                'orders': orders_deleted,
                'order_items': order_items_deleted,
                'payments': payments_deleted,
                'payment_transactions': payment_transactions_deleted,
                'manual_payments': manual_payments_deleted,
                'pending_payments': pending_payments_deleted
            }
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error resetting all orders: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'Error deleting orders: {str(e)}'
        }), 500

@app.route('/admin/order/<int:order_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_order_detail(order_id):
    order = Order.query.options(
        joinedload(Order.customer).joinedload(User.profile)
    ).get_or_404(order_id)
    
    if request.method == 'POST':
        new_status = request.form.get('status')
        if new_status in ['pending', 'processing', 'shipped', 'delivered', 'cancelled']:
            order.status = new_status
            db.session.commit()
            flash('Order status updated successfully!', 'success')
        else:
            flash('Invalid status', 'error')
        
        return redirect(url_for('admin_order_detail', order_id=order.id))
    
    # Get country for display
    order_country = None
    order_country_obj = None
    if order.customer and order.customer.profile:
        order_country = order.customer.profile.country
        if order_country:
            order_country_obj = Country.query.filter_by(name=order_country, is_active=True).first()
    
    # Load shipping rule with country relationship
    if order.shipping_rule_id:
        order.shipping_rule = LegacyShippingRule.query.options(
            joinedload(LegacyShippingRule.country)
        ).get(order.shipping_rule_id)
    
    # Get manual payment info if exists
    manual_payment = None
    manual_payment_details = None
    try:
        from app.payments.models import ManualPayment
        from app.payments.payment_details import get_payment_details
        manual_payment = ManualPayment.query.filter_by(order_id=order_id).first()
        if manual_payment:
            manual_payment_details = get_payment_details(manual_payment.payment_method)
    except Exception as e:
        current_app.logger.debug(f"Could not load manual payment info: {e}")
    
    return render_template('admin/admin/order_detail.html', 
                         order=order, 
                         order_country=order_country, 
                         order_country_obj=order_country_obj,
                         manual_payment=manual_payment,
                         manual_payment_details=manual_payment_details)

@app.route('/admin/pending-payments')
@login_required
@admin_required
def admin_pending_payments():
    """Admin page to view pending or failed payment attempts"""
    try:
        from app.payments.models import PendingPayment
        import json
        
        # Get filter parameters
        status_filter = request.args.get('status', 'all')  # all, waiting, failed
        page = request.args.get('page', 1, type=int)
        per_page = 20
        
        # Base query - only waiting or failed pending payments
        query = PendingPayment.query.filter(
            PendingPayment.status.in_(['waiting', 'failed'])
        )
        
        # Filter by status if specified
        if status_filter == 'waiting':
            query = query.filter(PendingPayment.status == 'waiting')
        elif status_filter == 'failed':
            query = query.filter(PendingPayment.status == 'failed')
        
        # Get paginated results with eager loading
        pending_payments = query.options(
            joinedload(PendingPayment.user)
        ).order_by(PendingPayment.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        # Parse cart items for each pending payment
        for pp in pending_payments.items:
            try:
                pp.parsed_cart_items = json.loads(pp.cart_items_json) if pp.cart_items_json else []
            except:
                pp.parsed_cart_items = []
        
        # Get totals
        total_waiting = PendingPayment.query.filter_by(status='waiting').count()
        total_failed = PendingPayment.query.filter_by(status='failed').count()
        total_amount_waiting = db.session.query(db.func.coalesce(db.func.sum(PendingPayment.amount), 0)).filter(
            PendingPayment.status == 'waiting'
        ).scalar() or 0.0
        total_amount_failed = db.session.query(db.func.coalesce(db.func.sum(PendingPayment.amount), 0)).filter(
            PendingPayment.status == 'failed'
        ).scalar() or 0.0
        
        return render_template('admin/admin/pending_payments.html',
                             pending_payments=pending_payments,
                             status_filter=status_filter,
                             total_waiting=total_waiting,
                             total_failed=total_failed,
                             total_amount_waiting=float(total_amount_waiting),
                             total_amount_failed=float(total_amount_failed))
    except Exception as e:
        current_app.logger.error(f"Error in admin_pending_payments: {str(e)}")
        flash('An error occurred while loading pending payments.', 'error')
        return redirect(url_for('admin_dashboard'))

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
    
    # Base query filter - only paid orders (exclude cancelled and pending)
    # Changed from 'delivered' to 'paid' to show all paid orders, not just delivered ones
    base_filter = db.and_(
        Order.status.in_(['paid', 'completed']),
        Order.status != 'cancelled'
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
    """Admin panel for full order management - Only shows orders with completed payments"""
    from app.payments.models import Payment
    from sqlalchemy import or_
    
    status = request.args.get('status', 'all')
    page = request.args.get('page', 1, type=int)
    per_page = 20
    sort_by = request.args.get('sort', 'newest')
    location_filter = request.args.get('location', 'all')
    customer_filter = request.args.get('customer', 'all')
    search_query = request.args.get('search', '')
    
    # Base query - ONLY orders with completed payments
    # Subquery for orders with completed payments
    completed_payments_subquery = db.session.query(Payment.order_id).filter(
        Payment.status == 'completed',
        Payment.order_id.isnot(None)
    ).distinct()
    
    # Only show orders that have completed payments OR have status='paid'/'completed'
    query = Order.query.filter(
        or_(
            Order.id.in_(completed_payments_subquery),
            Order.status.in_(['paid', 'completed'])
        )
    )
    
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
    
    # Dashboard stats - Only count orders with completed payments
    from app.payments.models import Payment
    from sqlalchemy import or_
    
    # Base query for stats - only orders with completed payments
    stats_base_query = Order.query.filter(
        or_(
            Order.id.in_(completed_payments_subquery),
            Order.status.in_(['paid', 'completed'])
        )
    )
    
    pending_count = stats_base_query.filter_by(shipping_status='pending').count()
    shipped_count = stats_base_query.filter_by(shipping_status='shipped').count()
    delivered_count = stats_base_query.filter_by(shipping_status='delivered').count()
    total_count = stats_base_query.count()
    
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
    """China Partner orders page - shows only non-shipped orders with completed payments"""
    from app.payments.models import Payment
    from sqlalchemy import or_
    
    status = request.args.get('status', 'all')
    country_filter = request.args.get('country', '')  # Country name filter
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Redirect pending status to all (Pending tab removed)
    if status == 'pending':
        return redirect(url_for('china_orders', status='all', page=page, country=country_filter))
    
    # Base query - ONLY orders with completed payments
    # Subquery for orders with completed payments
    completed_payments_subquery = db.session.query(Payment.order_id).filter(
        Payment.status == 'completed',
        Payment.order_id.isnot(None)
    ).distinct()
    
    # Base query - Only show orders that:
    # 1. Have completed payments (via Payment table)
    # 2. Have status='paid' or 'completed' (legacy support)
    # 3. Are not yet shipped (exclude "Shipped" status)
    query = Order.query.options(
        joinedload(Order.customer).joinedload(User.profile)
    ).filter(
        or_(
            Order.id.in_(completed_payments_subquery),
            Order.status.in_(['paid', 'completed'])
        ),
        Order.status != "Shipped"
    )
    
    # Apply country filter if provided
    if country_filter:
        # Join with User and UserProfile to filter by country
        # Explicitly specify the join condition to avoid ambiguity
        query = query.join(User, Order.user_id == User.id).join(UserProfile, User.id == UserProfile.user_id).filter(
            UserProfile.country == country_filter
        )
    
    # Get all active countries for filter dropdown
    countries = Country.query.filter_by(is_active=True).order_by(Country.name).all()
    countries_dict = []
    for country in countries:
        try:
            countries_dict.append(country.to_dict())
        except Exception as e:
            # Fallback if to_dict() fails for any reason
            current_app.logger.warning(f"Failed to convert country {country.id} to dict: {e}")
            countries_dict.append({
                'id': country.id,
                'name': country.name,
                'code': country.code or '',
                'currency': country.currency or '',
                'currency_symbol': country.currency_symbol or '',
                'language': country.language or 'en',
                'flag_image_path': country.flag_image_path or '',
                'flag_url': None,
                'is_active': country.is_active
            })
    
    orders = query.order_by(Order.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template('china/orders.html', 
                         orders=orders,
                         status='all',
                         country_filter=country_filter,
                         countries=countries,
                         countries_dict=countries_dict)

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

@app.route('/china/order/<int:order_id>')
@login_required
@china_partner_required
def china_order_detail(order_id):
    """China Partner order detail page"""
    order = Order.query.options(
        joinedload(Order.customer).joinedload(User.profile)
    ).get_or_404(order_id)
    
    # Get country for display
    order_country = None
    order_country_obj = None
    if order.customer and order.customer.profile:
        order_country = order.customer.profile.country
        if order_country:
            order_country_obj = Country.query.filter_by(name=order_country, is_active=True).first()
    
    return render_template('china/order_detail.html', order=order, order_country=order_country, order_country_obj=order_country_obj)

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

@app.route('/china/products', methods=['GET'])
@login_required
@china_partner_required
def china_products():
    """China Partner products upload page"""
    return render_template('china/products.html')

@app.route('/china/products/add', methods=['GET', 'POST'])
@login_required
@china_partner_required
def china_products_add():
    """Single product add page for China Partner"""
    if request.method == 'POST':
        try:
            from .utils.cloudinary_utils import upload_to_cloudinary
            
            # Get form data
            name = request.form.get('name', '').strip()
            category_name = request.form.get('category', '').strip()
            weight_kg = request.form.get('weight_kg', '').strip()
            price = request.form.get('price', '').strip()
            stock = request.form.get('stock', '0').strip()
            description = request.form.get('description', '').strip()
            
            # Validate required fields
            if not name:
                flash('Product name is required', 'error')
                return redirect(url_for('china_products_add'))
            if not category_name:
                flash('Category is required', 'error')
                return redirect(url_for('china_products_add'))
            if not weight_kg:
                flash('Weight is required', 'error')
                return redirect(url_for('china_products_add'))
            
            # Validate weight
            try:
                weight_kg = float(weight_kg)
                if weight_kg < 0.00001 or weight_kg > 500:
                    flash('Weight must be between 0.00001 and 500 kg', 'error')
                    return redirect(url_for('china_products_add'))
            except (ValueError, TypeError):
                flash('Invalid weight value', 'error')
                return redirect(url_for('china_products_add'))
            
            # Validate price (GMD)
            price_value = 0.0
            if price:
                try:
                    # Clean price string (remove currency symbols)
                    price_str = str(price).strip().upper()
                    price_str_clean = price_str.replace('$', '').replace('USD', '').replace('US', '').replace('GMD', '').replace('DALASI', '').replace('DALASIS', '').replace('D', '').strip()
                    if price_str_clean.startswith('D'):
                        price_str_clean = price_str_clean[1:].strip()
                    if price_str_clean.endswith('D'):
                        price_str_clean = price_str_clean[:-1].strip()
                    price_value = float(price_str_clean)
                    if price_value <= 0:
                        flash('Price must be greater than 0', 'error')
                        return redirect(url_for('china_products_add'))
                except (ValueError, TypeError):
                    flash('Invalid price value. Must be numeric GMD amount', 'error')
                    return redirect(url_for('china_products_add'))
            
            # Validate stock
            stock_value = 0
            if stock:
                try:
                    stock_value = int(stock)
                    if stock_value < 0:
                        stock_value = 0
                except (ValueError, TypeError):
                    stock_value = 0
            
            # Get or create category
            category = Category.query.filter_by(name=category_name).first()
            if not category:
                category = Category(name=category_name)
                db.session.add(category)
                db.session.flush()
            
            # Handle image upload
            final_image_url = None
            if 'image' in request.files:
                image_file = request.files['image']
                if image_file and image_file.filename:
                    upload_result = upload_to_cloudinary(image_file, folder='products')
                    if upload_result and upload_result.get('url'):
                        final_image_url = upload_result['url']
            
            # Create product
            product = Product(
                name=name,
                description=description or 'No description provided',
                price=price_value,  # Stored as GMD
                stock=stock_value,
                category_id=category.id,
                weight_kg=weight_kg,
                image=final_image_url,
                available_in_gambia=False,
                location='Outside The Gambia',  # Default for China products
                delivery_price=0.0,
                shipping_price=0.0
            )
            
            db.session.add(product)
            db.session.commit()
            
            flash(f'Product "{name}" added successfully!', 'success')
            return redirect(url_for('china_products'))
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error adding product: {str(e)}")
            flash(f'Error adding product: {str(e)}', 'error')
            return redirect(url_for('china_products_add'))
    
    # GET request - show form
    categories = Category.query.order_by(Category.name).all()
    return render_template('china/product_add.html', categories=categories)

@app.route('/china/products/upload', methods=['POST'])
@login_required
@china_partner_required
def china_products_upload():
    """Handle bulk product upload from CSV/XLSX"""
    try:
        from .utils.cloudinary_utils import upload_to_cloudinary
        
        # Check if file is present
        if 'file' not in request.files:
            flash('No file uploaded', 'error')
            return redirect(url_for('china_products'))
        
        file = request.files['file']
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(url_for('china_products'))
        
        # Check file extension
        filename = file.filename.lower()
        if not (filename.endswith('.csv') or filename.endswith('.xlsx') or filename.endswith('.xls')):
            flash('Invalid file type. Please upload CSV or XLSX file.', 'error')
            return redirect(url_for('china_products'))
        
        # Check file size (10MB max)
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        if file_size > 10 * 1024 * 1024:  # 10MB
            flash('File size exceeds 10MB limit', 'error')
            return redirect(url_for('china_products'))
        
        # Read file based on extension
        try:
            if filename.endswith('.csv'):
                df = pd.read_csv(file)
            else:  # .xlsx or .xls
                df = pd.read_excel(file, engine='openpyxl')
        except Exception as e:
            flash(f'Error reading file: {str(e)}', 'error')
            return redirect(url_for('china_products'))
        
        # Normalize column names (strip whitespace, lowercase)
        df.columns = df.columns.str.strip().str.lower()
        
        # Required columns
        required_cols = ['product name', 'category', 'weight (kg)']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            flash(f'Missing required columns: {", ".join(missing_cols)}', 'error')
            return redirect(url_for('china_products'))
        
        # Optional columns mapping
        col_mapping = {
            'product name': 'name',
            'category': 'category',
            'weight (kg)': 'weight_kg',
            'price': 'price',
            'price (gmd)': 'price',  # Accept both "Price" and "Price (GMD)"
            'stock': 'stock',
            'description': 'description',
            'image url': 'image_url',
            'image filename': 'image_filename'
        }
        
        # Process images if uploaded
        image_files = {}
        if 'images' in request.files:
            uploaded_images = request.files.getlist('images')
            for img_file in uploaded_images:
                if img_file.filename:
                    # Store image by filename for matching
                    image_files[img_file.filename.lower()] = img_file
        
        # Process each row
        success_count = 0
        error_count = 0
        errors = []
        
        for idx, row in df.iterrows():
            try:
                # Get required fields
                name = str(row['product name']).strip()
                category_name = str(row['category']).strip()
                weight_kg = row['weight (kg)']
                
                # Validate weight
                try:
                    weight_kg = float(weight_kg)
                    if weight_kg < 0.00001 or weight_kg > 500:
                        error_count += 1
                        errors.append(f"Row {idx + 2}: Weight {weight_kg} is out of range (0.00001-500 kg)")
                        continue
                except (ValueError, TypeError):
                    error_count += 1
                    errors.append(f"Row {idx + 2}: Invalid weight value")
                    continue
                
                # Get or create category
                category = Category.query.filter_by(name=category_name).first()
                if not category:
                    category = Category(name=category_name)
                    db.session.add(category)
                    db.session.flush()  # Get category ID
                
                # Get optional fields with defaults
                # Handle price - check both 'price' and 'price (gmd)' columns
                price_raw = row.get('price (gmd)') if pd.notna(row.get('price (gmd)')) else row.get('price')
                price = 0.0
                if pd.notna(price_raw):
                    try:
                        # Convert to string first to clean currency symbols
                        price_str = str(price_raw).strip().upper()
                        # Remove common currency symbols and parse (accept GMD, D, Dalasi, or plain numbers)
                        price_str_clean = price_str.replace('$', '').replace('USD', '').replace('US', '').replace('GMD', '').replace('DALASI', '').replace('DALASIS', '').replace('D', '').strip()
                        # Remove 'D' at start or end if present
                        if price_str_clean.startswith('D'):
                            price_str_clean = price_str_clean[1:].strip()
                        if price_str_clean.endswith('D'):
                            price_str_clean = price_str_clean[:-1].strip()
                        price = float(price_str_clean)
                        if price <= 0:
                            error_count += 1
                            errors.append(f"Row {idx + 2}: Price must be greater than 0")
                            continue
                    except (ValueError, TypeError):
                        error_count += 1
                        errors.append(f"Row {idx + 2}: Invalid price value. Must be numeric GMD amount")
                        continue
                
                stock = int(row.get('stock', 0)) if pd.notna(row.get('stock')) else 0
                description = str(row.get('description', '')).strip() if pd.notna(row.get('description')) else ''
                image_url = str(row.get('image url', '')).strip() if pd.notna(row.get('image url')) else None
                image_filename = str(row.get('image filename', '')).strip() if pd.notna(row.get('image filename')) else None
                
                # Handle image upload
                final_image_url = image_url
                if image_filename:
                    # Check if we have an uploaded file matching this filename
                    matching_file = image_files.get(image_filename.lower())
                    if matching_file:
                        # Upload to Cloudinary
                        upload_result = upload_to_cloudinary(matching_file, folder='products')
                        if upload_result and upload_result.get('url'):
                            final_image_url = upload_result['url']
                            current_app.logger.info(f"✅ Uploaded image {image_filename} to Cloudinary")
                
                # Create product
                # Price is stored in GMD (Gambian Dalasi)
                product = Product(
                    name=name,
                    description=description or 'No description provided',
                    price=price,  # Stored as GMD
                    stock=stock,
                    category_id=category.id,
                    weight_kg=weight_kg,
                    image=final_image_url,
                    available_in_gambia=False,
                    location='Outside The Gambia',  # Default for China products
                    delivery_price=0.0,  # Default delivery price
                    shipping_price=0.0  # Default shipping price
                )
                
                db.session.add(product)
                success_count += 1
                
            except Exception as e:
                error_count += 1
                errors.append(f"Row {idx + 2}: {str(e)}")
                current_app.logger.error(f"Error processing row {idx + 2}: {str(e)}")
                continue
        
        # Commit all products
        try:
            db.session.commit()
            if success_count > 0:
                flash(f'Successfully uploaded {success_count} product(s)!', 'success')
            if error_count > 0:
                error_msg = f'{error_count} row(s) failed. '
                if errors:
                    error_msg += 'First few errors: ' + '; '.join(errors[:5])
                    if len(errors) > 5:
                        error_msg += f' (and {len(errors) - 5} more...)'
                flash(error_msg, 'warning')
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving products: {str(e)}', 'error')
            current_app.logger.error(f"Error committing products: {str(e)}")
        
        return redirect(url_for('china_products'))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Upload failed: {str(e)}', 'error')
        current_app.logger.error(f"Bulk upload error: {str(e)}")
        return redirect(url_for('china_products'))

@app.route('/china/products/template', methods=['GET'])
@login_required
@china_partner_required
def china_products_template():
    """Download template CSV file for bulk product upload"""
    try:
        # Create template data
        template_data = {
            'Product Name': ['Example Product 1', 'Example Product 2'],
            'Category': ['Electronics', 'Clothing'],
            'Weight (kg)': [0.5, 0.2],
            'Price (GMD)': [100.00, 50.00],
            'Stock': [10, 20],
            'Description': ['Product description here', 'Another product description'],
            'Image URL': ['https://example.com/image1.jpg', ''],
            'Image Filename': ['product1.jpg', 'product2.jpg']
        }
        
        df = pd.DataFrame(template_data)
        
        # Create CSV in memory
        output = BytesIO()
        df.to_csv(output, index=False, encoding='utf-8-sig')  # utf-8-sig for Excel compatibility
        output.seek(0)
        
        return send_file(
            output,
            mimetype='text/csv',
            as_attachment=True,
            download_name='product_upload_template.csv'
        )
    except Exception as e:
        flash(f'Error generating template: {str(e)}', 'error')
        current_app.logger.error(f"Template generation error: {str(e)}")
        return redirect(url_for('china_products'))

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

@app.route('/api/checkout/update-pending-payment', methods=['POST'])
@login_required
def api_update_pending_payment():
    """Update pending payment with shipping method and recalculate totals."""
    try:
        from app.payments.models import PendingPayment
        import json
        
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        pending_payment_id = data.get('pending_payment_id')
        if not pending_payment_id:
            return jsonify({'success': False, 'message': 'pending_payment_id is required'}), 400
        
        pending_payment = PendingPayment.query.get(pending_payment_id)
        if not pending_payment:
            return jsonify({'success': False, 'message': 'PendingPayment not found'}), 404
        
        # Verify ownership
        if pending_payment.user_id != current_user.id and not current_user.is_admin:
            return jsonify({'success': False, 'message': 'Unauthorized'}), 403
        
        # Get shipping method
        shipping_mode_key = data.get('shipping_mode_key', '').strip() or None
        
        # Get country
        country = None
        country_id = None
        if data.get('country'):
            country = Country.query.filter_by(name=data['country'], is_active=True).first()
            if country:
                country_id = country.id
        
        # Get cart items
        cart_items = json.loads(pending_payment.cart_items_json) if pending_payment.cart_items_json else []
        
        # Calculate total cart weight
        total_weight = 0.0
        for item in cart_items:
            product = Product.query.get(item.get('id'))
            if product and product.weight_kg:
                total_weight += float(product.weight_kg) * item.get('quantity', 1)
        
        # CRITICAL FIX: Use cart shipping price from session instead of recalculating
        cart_shipping_price = session.get('cart_shipping_price')
        cart_total = session.get('cart_total')
        cart_subtotal = session.get('cart_subtotal')
        
        shipping_price_gmd = 0.0
        shipping_rule_id = None
        shipping_delivery_estimate = None
        total_cost = 0.0
        
        # Use cart shipping price if available
        if cart_shipping_price is not None and shipping_mode_key:
            # Use the shipping price from cart (already calculated correctly)
            from .utils.currency_rates import convert_price
            
            # cart_shipping_price is in display currency, convert to GMD if needed
            if country and country.currency != 'GMD':
                shipping_price_gmd = float(convert_price(cart_shipping_price, country.currency, 'GMD'))
            else:
                shipping_price_gmd = float(cart_shipping_price)
            
            # Get delivery time and rule info (optional, for record keeping)
            shipping_result = calculate_shipping_price(total_weight, country_id, shipping_mode_key, default_weight=0.0)
            if shipping_result and isinstance(shipping_result, dict) and shipping_result.get('available'):
                shipping_rule_id = shipping_result.get('rule_id') or (shipping_result.get('rule').id if shipping_result.get('rule') else None)
                shipping_delivery_estimate = shipping_result.get('delivery_time')
            
            # Use cart total if available
            if cart_total is not None:
                total_cost = float(cart_total)
                current_app.logger.info(
                    f'API update using cart total: {total_cost} (country={country.name if country else "None"}, '
                    f'currency={country.currency if country else "GMD"}), '
                    f'cart_shipping_price={cart_shipping_price}, shipping_price_gmd={shipping_price_gmd}'
                )
            else:
                # Fallback: Calculate total
                subtotal = cart_subtotal if cart_subtotal is not None else 0.0
                tax_rate = Decimal(str(current_app.config.get('CART_TAX_RATE', 0) or 0))
                tax = float(Decimal(str(subtotal)) * tax_rate)
                shipping_price_display = cart_shipping_price
                total_cost = subtotal + tax + shipping_price_display
        else:
            # Fallback: Recalculate if cart shipping price not available
            current_app.logger.warning('Cart shipping price not found in session, recalculating...')
            
            # Calculate shipping with selected method
            shipping_result = calculate_shipping_price(total_weight, country_id, shipping_mode_key, default_weight=0.0)
            
            if shipping_result and isinstance(shipping_result, dict) and shipping_result.get('available'):
                shipping_price_gmd = float(shipping_result.get('price_gmd', 0.0))
                shipping_rule_id = shipping_result.get('rule_id') or (shipping_result.get('rule').id if shipping_result.get('rule') else None)
                shipping_delivery_estimate = shipping_result.get('delivery_time')
            
            # Calculate subtotal
            subtotal = 0.0
            for item in cart_items:
                product = Product.query.get(item.get('id'))
                if product:
                    from app import get_product_price_with_profit
                    base_price = float(product.price)
                    final_price, _, _ = get_product_price_with_profit(base_price)
                    subtotal += final_price * item.get('quantity', 1)
            
            # Calculate tax
            tax_rate = Decimal(str(current_app.config.get('CART_TAX_RATE', 0) or 0))
            tax = float(Decimal(str(subtotal)) * tax_rate)
            
            # Convert shipping to display currency if needed
            from .utils.currency_rates import convert_price
            if country and country.currency != 'GMD':
                shipping_price_display = float(convert_price(shipping_price_gmd, 'GMD', country.currency))
            else:
                shipping_price_display = shipping_price_gmd
            
            # Calculate total
            total_cost = subtotal + tax + shipping_price_display
        
        # Update pending payment
        pending_payment.shipping_mode_key = shipping_mode_key
        pending_payment.shipping_price = shipping_price_gmd
        # CRITICAL FIX: Always set shipping_rule_id to None to avoid foreign key constraint errors
        # The database foreign key constraint points to 'shipping_rule' (old table) but we're using
        # 'shipping_rules' (new table), so we cannot reliably set this field
        pending_payment.shipping_rule_id = None
        pending_payment.shipping_delivery_estimate = shipping_delivery_estimate
        pending_payment.shipping_display_currency = country.currency if country else 'GMD'
        pending_payment.amount = total_cost
        pending_payment.total_cost = total_cost
        
        # Update address fields if provided
        if 'full_name' in data:
            pending_payment.customer_name = data['full_name'].strip()
        if 'phone' in data:
            pending_payment.customer_phone = data['phone'].strip()
        if 'email' in data:
            pending_payment.customer_email = data['email'].strip()
        if 'delivery_address' in data:
            pending_payment.delivery_address = data['delivery_address'].strip()
        if 'customer_photo_url' in data:
            pending_payment.customer_photo_url = data['customer_photo_url'].strip()
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'pending_payment_id': pending_payment.id,
            'amount': total_cost,
            'shipping_price': shipping_price_gmd,
            'shipping_mode_key': shipping_mode_key
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'Error updating pending payment: {e}', exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/checkout/save-address', methods=['POST'])
@login_required
def api_save_checkout_address():
    """Save checkout address to user profile."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        profile = ensure_user_profile(current_user)
        
        # Split full_name into first_name and last_name
        full_name = data.get('full_name', '').strip()
        if full_name:
            name_parts = full_name.split(' ', 1)
            profile.first_name = name_parts[0] if name_parts else ''
            profile.last_name = name_parts[1] if len(name_parts) > 1 else ''
        
        # Update address fields
        if 'phone' in data:
            profile.phone_number = data['phone'].strip()
        if 'email' in data:
            # Email is stored on User model, not profile
            current_user.email = data['email'].strip()
        if 'country' in data:
            profile.country = data['country'].strip()
        if 'city' in data:
            profile.city = data['city'].strip()
        if 'delivery_address' in data:
            profile.address = data['delivery_address'].strip()
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Address saved successfully',
            'profile': {
                'first_name': profile.first_name,
                'last_name': profile.last_name,
                'phone_number': profile.phone_number,
                'email': current_user.email,
                'country': profile.country,
                'city': profile.city,
                'address': profile.address
            }
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error saving checkout address: {e}")
        return jsonify({'success': False, 'message': 'Failed to save address'}), 500

@app.route('/api/checkout/upload-photo', methods=['POST'])
@login_required
def api_upload_customer_photo():
    """Upload customer photo during checkout."""
    try:
        from app.utils.cloudinary_utils import upload_to_cloudinary
        
        if 'photo' not in request.files:
            return jsonify({'success': False, 'message': 'No photo provided'}), 400
        
        photo_file = request.files['photo']
        if photo_file.filename == '':
            return jsonify({'success': False, 'message': 'No photo selected'}), 400
        
        # Upload to Cloudinary
        upload_result = upload_to_cloudinary(photo_file, folder='customer_photos')
        
        if upload_result and upload_result.get('url'):
            return jsonify({
                'success': True,
                'photo_url': upload_result['url']
            })
        else:
            return jsonify({'success': False, 'message': 'Failed to upload photo'}), 500
            
    except Exception as e:
        current_app.logger.error(f"Error uploading customer photo: {e}")
        return jsonify({'success': False, 'message': 'Failed to upload photo'}), 500

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
    
    # Check if user is in Gambia to determine product visibility
    user_in_gambia = is_user_in_gambia()
    
    # Get base query with country filtering
    query = get_product_base_query(include_gambia_products=user_in_gambia)
    
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
    
    # Get categories for the sidebar (filtered by country)
    categories_with_counts = get_categories_with_counts(include_gambia_products=user_in_gambia)
    # Convert to simple category objects for compatibility
    category_ids = [c['id'] for c in categories_with_counts]
    categories = Category.query.filter(Category.id.in_(category_ids)).order_by('name').all() if category_ids else []
    
    # Get filtered products
    products = query.order_by(Product.created_at.desc()).all()
    
    return render_template('products.html', 
                         products=products,
                         categories=categories,
                         current_category=category_id,
                         search_query=search_query)

@app.route('/favicon.ico')
def favicon():
    """Serve favicon.ico - use dynamic PWA favicon or fallback to Cloudinary logo"""
    from flask import redirect, send_file
    import os
    
    # Try to use PWA favicon from settings
    try:
        app_settings = AppSettings.query.first()
        if app_settings and hasattr(app_settings, 'pwa_favicon_path') and app_settings.pwa_favicon_path:
            favicon_path = os.path.join(app.static_folder, app_settings.pwa_favicon_path.lstrip('/static/'))
            if os.path.exists(favicon_path):
                return send_file(favicon_path, mimetype='image/x-icon'), 200, {'Cache-Control': 'public, max-age=31536000'}
    except Exception:
        pass
    
    # Fallback to Cloudinary logo
    return redirect('https://res.cloudinary.com/dfjffnmzf/image/upload/v1763781131/Gemini_Generated_Image_ufkia2ufkia2ufki_pcf2lq.png', code=302)

@app.route('/manifest.json')
def manifest_json():
    """Serve dynamic PWA manifest from database settings"""
    from flask import jsonify, url_for
    import os
    
    try:
        # Get AppSettings for PWA configuration
        app_settings = AppSettings.query.first()
        if not app_settings:
            app_settings = AppSettings()
            db.session.add(app_settings)
            db.session.commit()
        
        # Get PWA settings with fallback defaults
        pwa_app_name = getattr(app_settings, 'pwa_app_name', None) or 'buxin store'
        pwa_short_name = getattr(app_settings, 'pwa_short_name', None) or 'buxin store'
        pwa_description = getattr(app_settings, 'pwa_description', None) or 'buxin store - Your gateway to the future of technology. Explore robotics, coding, and artificial intelligence.'
        pwa_start_url = getattr(app_settings, 'pwa_start_url', None) or '/'
        pwa_display = getattr(app_settings, 'pwa_display', None) or 'standalone'
        pwa_theme_color = getattr(app_settings, 'pwa_theme_color', None) or '#ffffff'
        pwa_background_color = getattr(app_settings, 'pwa_background_color', None) or '#ffffff'
        pwa_logo_path = getattr(app_settings, 'pwa_logo_path', None)
        
        # Build icons array with multiple sizes for best home screen support
        icons = []
        
        # Determine the logo URL
        if pwa_logo_path:
            if pwa_logo_path.startswith('http://') or pwa_logo_path.startswith('https://'):
                logo_url = pwa_logo_path
            else:
                logo_url = url_for('static', filename=pwa_logo_path, _external=True)
        else:
            # Fallback to Cloudinary logo
            logo_url = "https://res.cloudinary.com/dfjffnmzf/image/upload/v1763781131/Gemini_Generated_Image_ufkia2ufkia2ufki_pcf2lq.png"
        
        # Add multiple icon sizes for best compatibility with home screen
        icon_sizes = ["72x72", "96x96", "128x128", "144x144", "152x152", "192x192", "256x256", "384x384", "512x512"]
        for size in icon_sizes:
            icons.append({
                "src": logo_url,
                "sizes": size,
                "type": "image/png",
                "purpose": "any maskable"
            })
        
        # Build manifest JSON
        manifest = {
            "name": pwa_app_name,
            "short_name": pwa_short_name,
            "description": pwa_description,
            "start_url": pwa_start_url,
            "display": pwa_display,
            "background_color": pwa_background_color,
            "theme_color": pwa_theme_color,
            "orientation": "portrait-primary",
            "scope": "/",
            "icons": icons,
            "categories": ["shopping", "education", "technology"],
            "screenshots": [],
            "shortcuts": [
                {
                    "name": "Shop",
                    "short_name": "Shop",
                    "description": "Browse products",
                    "url": "/products",
                    "icons": icons[:1] if icons else []
                },
                {
                    "name": "Cart",
                    "short_name": "Cart",
                    "description": "View cart",
                    "url": "/cart",
                    "icons": icons[:1] if icons else []
                }
            ]
        }
        
        return jsonify(manifest), 200, {
            'Content-Type': 'application/manifest+json',
            'Cache-Control': 'no-cache, max-age=0'  # Don't cache to pick up admin changes immediately
        }
    except Exception as e:
        current_app.logger.error(f"Error generating manifest.json: {str(e)}")
        # Return fallback manifest on error
        fallback_manifest = {
            "name": "Buxin Store",
            "short_name": "Buxin Store",
            "description": "Buxin Store - Your gateway to the future of technology.",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#ffffff",
            "icons": [{
                "src": "https://res.cloudinary.com/dfjffnmzf/image/upload/v1763781131/Gemini_Generated_Image_ufkia2ufkia2ufki_pcf2lq.png",
                "sizes": "512x512",
                "type": "image/png"
            }]
        }
        return jsonify(fallback_manifest), 200, {
            'Content-Type': 'application/manifest+json',
            'Cache-Control': 'public, max-age=3600'
        }

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
    """
    Homepage route.
    
    CRITICAL: For non-human requests (monitoring, bots, crawlers, HEAD requests),
    returns a simple database-free HTML response without any database queries.
    This ensures uptime checks, wake-ups, and automated probes never trigger
    Neon database connections, even when Neon is paused or out of compute.
    
    Real user requests proceed normally with full database access for products,
    categories, and personalized content.
    """
    # Check if this is a non-human/interactive request (monitoring, bot, crawler, HEAD)
    # If so, return a simple database-free response
    if is_non_human_request(request):
        # Return a minimal HTML response for monitoring/probes
        # This is database-free and works even when Neon is unavailable
        html_response = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>buxin store</title>
</head>
<body>
    <h1>buxin store</h1>
    <p>Your gateway to the future of technology.</p>
</body>
</html>"""
        return make_response(html_response, 200)
    
    # For real user requests, proceed with normal homepage functionality
    # Onboarding check is now handled by @app.before_request (force_onboarding_for_new_users)
    # This ensures ALL routes force onboarding, not just home
    
    search_query = request.args.get('q', '')
    
    # Check if user is in Gambia to determine product visibility
    user_in_gambia = is_user_in_gambia()
    
    # Get categories with product counts (filtered by country)
    categories_with_counts = get_categories_with_counts(include_gambia_products=user_in_gambia)
    
    # Get base product query (filtered by country)
    base_query = get_product_base_query(include_gambia_products=user_in_gambia)
    
    # If there's a search query, filter products by name or description
    if search_query:
        search = f"%{search_query}%"
        featured_products = base_query.filter(
            Product.stock > 0,
            (Product.name.ilike(search)) | (Product.description.ilike(search))
        ).order_by(Product.created_at.desc()).limit(20).all()
    else:
        # Only show products that are in stock and limit to 8
        featured_products = base_query.filter(Product.stock > 0).order_by(Product.created_at.desc()).limit(8).all()
    
    return render_template('index.html', 
                         categories_with_counts=categories_with_counts, 
                         featured_products=featured_products, 
                         search_query=search_query)

@app.route('/categories')
def all_categories():
    """Show all categories page"""
    # Check if user is in Gambia to determine category visibility
    user_in_gambia = is_user_in_gambia()
    
    # Get categories with product counts (filtered by country)
    categories_with_counts = get_categories_with_counts(include_gambia_products=user_in_gambia)
    
    # Sort by name
    categories_with_counts.sort(key=lambda x: x['name'])
    
    return render_template('categories.html', 
                         categories_with_counts=categories_with_counts)

@app.route('/category/<int:category_id>')
def category(category_id):
    category = Category.query.get_or_404(category_id)
    
    # Check if user is in Gambia
    user_in_gambia = is_user_in_gambia()
    
    # Get pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = 24  # Number of products per page
    
    # Get sorting parameter
    sort = request.args.get('sort', 'newest')
    
    # Build base query with country filtering
    if user_in_gambia:
        # User is in Gambia - show all products in this category
        base_query = Product.query.filter(
            Product.category_id == category_id,
            Product.stock > 0
        )
    else:
        # User is NOT in Gambia - exclude Gambia-only products
        base_query = Product.query.filter(
            Product.category_id == category_id,
            Product.stock > 0,
            Product.available_in_gambia == False
        )
    
    # Apply sorting
    if sort == 'price_low':
        base_query = base_query.order_by(Product.price.asc())
    elif sort == 'price_high':
        base_query = base_query.order_by(Product.price.desc())
    elif sort == 'popular':
        # Sort by number of times added to cart/orders (using stock as proxy for now)
        base_query = base_query.order_by(Product.stock.desc())
    elif sort == 'rating':
        # Sort by rating if available, otherwise by newest
        base_query = base_query.order_by(Product.created_at.desc())
    else:  # 'newest' is default
        base_query = base_query.order_by(Product.created_at.desc())
    
    # Get paginated products
    pagination = base_query.paginate(page=page, per_page=per_page, error_out=False)
    products = pagination.items
    
    # Check if no products found for non-Gambia users
    if not user_in_gambia and not products and page == 1:
        # Check if there are any Gambia products in this category
        gambia_products_count = Product.query.filter(
            Product.category_id == category_id,
            Product.available_in_gambia == True
        ).count()
        
        if gambia_products_count > 0:
            # Category only has Gambia products - redirect to categories page
            flash('This category is only available in The Gambia.', 'info')
            return redirect(url_for('all_categories'))
    
    return render_template('category.html', 
                         category=category, 
                         products=products,
                         pagination=pagination,
                         current_sort=sort)

@app.route('/product/<int:product_id>')
def product(product_id):
    product = Product.query.get_or_404(product_id)
    
    # Check if user is in Gambia
    user_in_gambia = is_user_in_gambia()
    
    # If product is only available in Gambia and user is not in Gambia, block access
    if product.available_in_gambia and not user_in_gambia:
        flash('This product is only available in The Gambia. Please select Gambia as your country to view it.', 'info')
        return redirect(url_for('home'))
    
    # If product is available in Gambia, skip all shipping calculations
    if product.available_in_gambia:
        shipping_fee = 0.0
        shipping_delivery_time = None
        shipping_methods_data = []
        final_price = product.price
        country = None
    else:
        # Calculate shipping fee using new shipping rules system
        # Get user's country (or use None to try global rules)
        country = get_current_country()
        country_id = country.id if country else None
        
        # Get product weight - REQUIRED (no default fallback)
        if not product.weight_kg or product.weight_kg <= 0:
            current_app.logger.error(
                f"Product {product_id} ({product.name}) has no valid weight. "
                f"Shipping cannot be calculated. Please set weight in admin."
            )
            flash('This product has no weight set. Shipping cannot be calculated. Please contact support.', 'error')
            product_weight = 0.0
        else:
            product_weight = float(product.weight_kg)
        
        # Get all shipping methods with prices for this product
        from app.shipping import get_all_shipping_methods
        shipping_methods_data = []
        all_methods = get_all_shipping_methods()
        
        # Map old method IDs to new shipping_mode_key values
        method_mapping = {
            'ecommerce': 'economy_plus',
            'express': 'express',
            'economy': 'economy'
        }
        
        for method in all_methods:
            method_id = method['id']
            # Map to shipping_mode_key (new system uses economy_plus instead of ecommerce)
            shipping_mode_key = method_mapping.get(method_id, method_id)
            
            # Check if a rule exists for this method, country, and weight
            shipping_result = calculate_shipping_price(product_weight, country_id, shipping_mode_key, default_weight=0.0)
            
            if shipping_result and isinstance(shipping_result, dict) and shipping_result.get('available'):
                price_gmd = shipping_result.get('price_gmd', 0.0)
                # Convert to display currency if needed
                if country and country.currency != 'GMD':
                    from .utils.currency_rates import convert_price
                    price_display = convert_price(price_gmd, 'GMD', country.currency)
                else:
                    price_display = price_gmd
                
                shipping_methods_data.append({
                    **method,
                    'price_gmd': price_gmd,
                    'price_display': price_display,
                    'delivery_time': shipping_result.get('delivery_time') or method.get('guarantee', ''),
                    'available': True
                })
            else:
                shipping_methods_data.append({
                    **method,
                    'price_gmd': None,
                    'price_display': None,
                    'delivery_time': method.get('guarantee', ''),
                    'available': False
                })
        
        # For backward compatibility, calculate default shipping (first available or 0)
        shipping_fee = 0.0
        shipping_delivery_time = None
        if shipping_methods_data and shipping_methods_data[0].get('available'):
            shipping_fee = shipping_methods_data[0]['price_display'] or 0.0
            shipping_delivery_time = shipping_methods_data[0]['delivery_time']
        
        final_price = product.price + shipping_fee
    
    # Note: Shipping fees come ONLY from Shipping Rules table - no product.delivery_price used
    
    category = Category.query.get(product.category_id)
    
    # Get related products (filtered by country)
    related_query = Product.query.filter(
        Product.category_id == product.category_id,
        Product.id != product.id
    )
    # If user is not in Gambia, exclude Gambia-only products from related products
    if not user_in_gambia:
        related_query = related_query.filter(Product.available_in_gambia == False)
    related_products = related_query.limit(4).all()
    
    return render_template('product.html', 
                         product=product, 
                         category=category,
                         related_products=related_products,
                         shipping_fee=shipping_fee,
                         shipping_delivery_time=shipping_delivery_time,
                         final_price=final_price,
                         shipping_methods=shipping_methods_data,
                         selected_country=country)

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

# ==================== Currency Rate Sync Scheduler ====================

CURRENCY_SYNC_JOB_ID = 'hourly_currency_rate_sync'
_currency_sync_scheduler = None
_currency_sync_scheduler_lock = threading.Lock()
_currency_sync_shutdown_registered = False


def _scheduled_currency_sync_job():
    """Background job to sync all currency rates with API sync enabled."""
    with app.app_context():
        try:
            from app.utils.currency_api import sync_currency_rate_from_api
            import os
            
            # Get all active rates with API sync enabled
            rates_to_sync = CurrencyRate.query.filter_by(
                api_sync_enabled=True,
                is_active=True
            ).all()
            
            if not rates_to_sync:
                app.logger.debug("No currency rates with API sync enabled.")
                return
            
            updated = 0
            failed = 0
            
            for currency_rate in rates_to_sync:
                try:
                    api_key = os.getenv(f'{currency_rate.api_provider.upper()}_API_KEY', '') if currency_rate.api_provider else None
                    provider = currency_rate.api_provider or None
                    
                    success, error, rate = sync_currency_rate_from_api(
                        currency_rate.from_currency,
                        currency_rate.to_currency,
                        provider,
                        api_key
                    )
                    
                    if success and rate:
                        currency_rate.rate = rate
                        currency_rate.last_api_sync = datetime.utcnow()
                        currency_rate.api_sync_error = None
                        currency_rate.last_updated = datetime.utcnow()
                        updated += 1
                    else:
                        currency_rate.api_sync_error = error or "Unknown error"
                        failed += 1
                        app.logger.warning(f"Failed to sync {currency_rate.from_currency} → {currency_rate.to_currency}: {error}")
                except Exception as e:
                    failed += 1
                    currency_rate.api_sync_error = str(e)
                    app.logger.error(f"Error syncing {currency_rate.from_currency} → {currency_rate.to_currency}: {e}")
            
            db.session.commit()
            app.logger.info(f"Currency sync completed: {updated} updated, {failed} failed")
            
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Error in scheduled currency sync job: {e}")


def schedule_currency_sync_job():
    """Schedule the currency rate sync job (runs hourly)."""
    global _currency_sync_scheduler, _currency_sync_shutdown_registered
    
    with _currency_sync_scheduler_lock:
        if not _currency_sync_scheduler:
            try:
                import pytz
                timezone = pytz.timezone('UTC')
            except ImportError:
                # Fallback if pytz not available
                from datetime import timezone as dt_timezone
                timezone = dt_timezone.utc
            _currency_sync_scheduler = BackgroundScheduler(timezone=timezone)
            _currency_sync_scheduler.start()
            app.config['CURRENCY_SYNC_SCHEDULER_STARTED'] = True
            if not _currency_sync_shutdown_registered:
                atexit.register(shutdown_currency_sync_scheduler)
                _currency_sync_shutdown_registered = True
        
        # Schedule to run every hour
        trigger = CronTrigger(minute=0, timezone=_currency_sync_scheduler.timezone)
        _currency_sync_scheduler.add_job(
            _scheduled_currency_sync_job,
            trigger=trigger,
            id=CURRENCY_SYNC_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600
        )
        app.logger.info(f"Currency rate sync scheduled to run hourly ({_currency_sync_scheduler.timezone})")


def shutdown_currency_sync_scheduler():
    """Shutdown the currency sync scheduler."""
    global _currency_sync_scheduler
    if _currency_sync_scheduler and _currency_sync_scheduler.running:
        _currency_sync_scheduler.shutdown(wait=False)
        _currency_sync_scheduler = None


def initialize_currency_sync_scheduler():
    """Start currency sync scheduler once per process."""
    if app.config.get('TESTING'):
        return
    if app.config.get('CURRENCY_SYNC_SCHEDULER_STARTED'):
        return
    if app.config.get('DEBUG') and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        # Avoid double-starting in debug reloader
        return
    schedule_currency_sync_job()
    app.config['CURRENCY_SYNC_SCHEDULER_STARTED'] = True


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
@app.route('/admin/manual-payments')
@login_required
@admin_required
def admin_manual_payments():
    """
    Admin page to view and manage manual payment requests.
    Shows all manual payments with filters for status.
    """
    try:
        from app.payments.models import ManualPayment
        from app.payments.payment_details import get_payment_details
        
        # Get filter parameters
        status_filter = request.args.get('status', 'all')  # all, pending, approved, rejected
        page = request.args.get('page', 1, type=int)
        per_page = 20
        
        # Build query
        query = ManualPayment.query
        
        if status_filter != 'all':
            query = query.filter(ManualPayment.status == status_filter)
        
        # Order by created_at descending (newest first)
        query = query.order_by(ManualPayment.created_at.desc())
        
        # Paginate
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        manual_payments = pagination.items
        
        # Get payment details for display
        payments_with_details = []
        for payment in manual_payments:
            details = get_payment_details(payment.payment_method)
            payments_with_details.append({
                'payment': payment,
                'details': details
            })
        
        return render_template(
            'admin/admin/manual_payments.html',
            payments_with_details=payments_with_details,
            pagination=pagination,
            status_filter=status_filter
        )
        
    except Exception as e:
        current_app.logger.error(f"Error loading manual payments: {str(e)}", exc_info=True)
        flash('An error occurred while loading manual payments.', 'error')
        return redirect(url_for('admin_dashboard'))


@app.route('/admin/manual-payment/<int:manual_payment_id>')
@login_required
@admin_required
def admin_manual_payment_detail(manual_payment_id):
    """
    Admin page to view manual payment details and approve/reject.
    """
    try:
        from app.payments.models import ManualPayment
        from app.payments.payment_details import get_payment_details
        from app.payments.services import PaymentService
        
        manual_payment = ManualPayment.query.get_or_404(manual_payment_id)
        payment_details = get_payment_details(manual_payment.payment_method)
        
        return render_template(
            'admin/admin/manual_payment_detail.html',
            manual_payment=manual_payment,
            payment_details=payment_details,
            pending_payment=manual_payment.pending_payment,
            user=manual_payment.user
        )
        
    except Exception as e:
        current_app.logger.error(f"Error loading manual payment detail: {str(e)}", exc_info=True)
        flash('An error occurred while loading payment details.', 'error')
        return redirect(url_for('admin_manual_payments'))


@app.route('/admin/manual-payment/<int:manual_payment_id>/approve', methods=['POST'])
@login_required
@admin_required
def admin_approve_manual_payment(manual_payment_id):
    """
    Approve a manual payment and create order.
    """
    try:
        from app.payments.models import ManualPayment
        from app.payments.services import PaymentService
        
        manual_payment = ManualPayment.query.get_or_404(manual_payment_id)
        
        if manual_payment.status != 'pending':
            flash(f'Payment is already {manual_payment.status}. Cannot approve.', 'error')
            return redirect(url_for('admin_manual_payment_detail', manual_payment_id=manual_payment_id))
        
        # Convert pending payment to order
        result = PaymentService.convert_pending_payment_to_order(manual_payment.pending_payment_id)
        
        if result.get('success'):
            # Update manual payment
            manual_payment.status = 'approved'
            manual_payment.approved_by = current_user.id
            manual_payment.approved_at = datetime.utcnow()
            manual_payment.order_id = result.get('order_id')
            
            # Update pending payment status
            manual_payment.pending_payment.status = 'completed'
            
            db.session.commit()
            
            current_app.logger.info(
                f'Manual payment {manual_payment_id} approved by admin {current_user.id}. '
                f'Order created: {manual_payment.order_id}'
            )
            
            flash('Payment approved and order created successfully!', 'success')
            return redirect(url_for('admin_manual_payment_detail', manual_payment_id=manual_payment_id))
        else:
            flash(f'Failed to create order: {result.get("message", "Unknown error")}', 'error')
            return redirect(url_for('admin_manual_payment_detail', manual_payment_id=manual_payment_id))
            
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error approving manual payment: {str(e)}", exc_info=True)
        flash('An error occurred while approving payment.', 'error')
        return redirect(url_for('admin_manual_payment_detail', manual_payment_id=manual_payment_id))


@app.route('/admin/manual-payment/<int:manual_payment_id>/reject', methods=['POST'])
@login_required
@admin_required
def admin_reject_manual_payment(manual_payment_id):
    """
    Reject a manual payment with reason.
    """
    try:
        from app.payments.models import ManualPayment
        
        manual_payment = ManualPayment.query.get_or_404(manual_payment_id)
        rejection_reason = request.form.get('rejection_reason', '').strip()
        
        if manual_payment.status != 'pending':
            flash(f'Payment is already {manual_payment.status}. Cannot reject.', 'error')
            return redirect(url_for('admin_manual_payment_detail', manual_payment_id=manual_payment_id))
        
        if not rejection_reason:
            flash('Please provide a reason for rejection.', 'error')
            return redirect(url_for('admin_manual_payment_detail', manual_payment_id=manual_payment_id))
        
        # Update manual payment
        manual_payment.status = 'rejected'
        manual_payment.approved_by = current_user.id
        manual_payment.approved_at = datetime.utcnow()
        manual_payment.rejection_reason = rejection_reason
        
        # Update pending payment status
        manual_payment.pending_payment.status = 'failed'
        
        db.session.commit()
        
        current_app.logger.info(
            f'Manual payment {manual_payment_id} rejected by admin {current_user.id}. '
            f'Reason: {rejection_reason}'
        )
        
        flash('Payment rejected successfully.', 'success')
        return redirect(url_for('admin_manual_payment_detail', manual_payment_id=manual_payment_id))
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error rejecting manual payment: {str(e)}", exc_info=True)
        flash('An error occurred while rejecting payment.', 'error')
        return redirect(url_for('admin_manual_payment_detail', manual_payment_id=manual_payment_id))


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
    from sqlalchemy.orm import joinedload
    # Eagerly load relationships to avoid N+1 queries
    # customer is a backref from User model
    order = Order.query.options(
        joinedload(Order.items).joinedload(OrderItem.product),
        joinedload(Order.customer)
    ).get_or_404(order_id)
    
    # Verify the order belongs to the current user
    if order.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    
    # Log that the confirmation page was viewed
    app.logger.info(f'Order {order_id} confirmation page viewed by user {current_user.id}')
    
    # Get Gambia contact settings for local products
    gambia_whatsapp = None
    gambia_phone = None
    try:
        app_settings = AppSettings.query.first()
        if app_settings:
            gambia_whatsapp = getattr(app_settings, 'gambia_whatsapp_number', None)
            gambia_phone = getattr(app_settings, 'gambia_phone_number', None)
    except Exception as e:
        app.logger.warning(f'Error fetching Gambia contact settings: {e}')
    
    return render_template('order_confirmation.html', 
                         order=order,
                         gambia_whatsapp=gambia_whatsapp,
                         gambia_phone=gambia_phone)

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
        
        # Initialize currency rate sync scheduler
        try:
            initialize_currency_sync_scheduler()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Failed to initialize currency sync scheduler: {str(e)}")

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