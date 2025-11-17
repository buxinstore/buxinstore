from flask_login import LoginManager
from flask_mail import Mail
from flask_migrate import Migrate
from flask_migrate.cli import db as flask_migrate_cli
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy.exc import OperationalError, DisconnectionError
from sqlalchemy import event
import logging

logger = logging.getLogger(__name__)

# Initialize SQLAlchemy with default session options
# Engine options will be set in init_extensions after app config is loaded
db = SQLAlchemy(session_options={"expire_on_commit": False})
login_manager = LoginManager()
migrate = Migrate()
mail = Mail()
csrf = CSRFProtect()


def init_extensions(app):
    """Initialize all extensions with the given Flask app."""
    # Initialize SQLAlchemy first
    db.init_app(app)
    
    # Get engine options from config
    engine_options = app.config.get("SQLALCHEMY_ENGINE_OPTIONS", {})
    
    # Flask-SQLAlchemy 3.x: Configure engine after initialization
    # Since init_app() doesn't support engine_options, we recreate the engine
    if engine_options:
        with app.app_context():
            # Get the database URI
            database_uri = app.config.get("SQLALCHEMY_DATABASE_URI")
            if database_uri:
                # Import create_engine to recreate with options
                from sqlalchemy import create_engine
                
                # Dispose of the old engine
                try:
                    db.engine.dispose(close=True)
                except Exception:
                    pass
                
                # Create a new engine with the options
                new_engine = create_engine(
                    database_uri,
                    **engine_options
                )
                
                # Replace the engine
                db.engine = new_engine
                
                app.logger.info(
                    f"✅ Database connection pool configured: "
                    f"pool_pre_ping={engine_options.get('pool_pre_ping')}, "
                    f"pool_recycle={engine_options.get('pool_recycle')}, "
                    f"pool_size={engine_options.get('pool_size')}"
                )
    
    # Add connection pool event listeners for better error handling
    @event.listens_for(db.engine, "connect")
    def set_connection_pragmas(dbapi_conn, connection_record):
        """Set connection-level settings when a connection is created."""
        try:
            # Connection is already established, just log
            logger.debug("New database connection established")
        except Exception as e:
            logger.warning(f"Could not set connection pragmas: {e}")

    @event.listens_for(db.engine, "checkout")
    def receive_checkout(dbapi_conn, connection_record, connection_proxy):
        """Handle connection checkout - connection health is already checked by pool_pre_ping."""
        # pool_pre_ping handles connection health checks automatically
        # This listener is here for logging purposes
        logger.debug("Connection checked out from pool")

    migrate.init_app(app, db)
    if "db" not in app.cli.commands:
        app.cli.add_command(flask_migrate_cli)

    login_manager.init_app(app)
    login_manager.login_view = "login"

    csrf.init_app(app)

    # Flask-Mail removed - using Resend API instead
    app.logger.info("✅ Email system: Using Resend API (Flask-Mail disabled)")

    return app
