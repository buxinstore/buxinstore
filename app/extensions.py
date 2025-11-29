from flask_login import LoginManager
from flask_mail import Mail
from flask_migrate import Migrate
from flask_migrate.cli import db as flask_migrate_cli
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from flask_babel import Babel

# Initialize SQLAlchemy with default session options
# Engine options will be set in init_extensions after app config is loaded
db = SQLAlchemy(session_options={"expire_on_commit": False})
login_manager = LoginManager()
migrate = Migrate()
mail = Mail()
csrf = CSRFProtect()
babel = Babel()


def init_extensions(app):
    """Initialize all extensions with the given Flask app."""
    # Initialize SQLAlchemy normally - keep it simple and safe
    # This is the standard way that works with Flask-SQLAlchemy 3.x
    db.init_app(app)
    
    # Note: Connection pool configuration is handled by the error handler
    # in app/__init__.py which will catch and handle connection errors gracefully

    migrate.init_app(app, db)
    if "db" not in app.cli.commands:
        app.cli.add_command(flask_migrate_cli)

    login_manager.init_app(app)
    login_manager.login_view = "login"

    csrf.init_app(app)
    
    # Initialize Babel with locale selector (Flask-Babel 3.0+ requires passing selector functions)
    # The get_locale function will be defined in app/__init__.py before this is called
    # We'll initialize it after the app is created and get_locale is defined

    # Flask-Mail removed - using Resend API instead
    app.logger.info("✅ Email system: Using Resend API (Flask-Mail disabled)")

    return app
