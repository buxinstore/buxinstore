from flask_login import LoginManager
from flask_mail import Mail
from flask_migrate import Migrate
from flask_migrate.cli import db as flask_migrate_cli
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect

db = SQLAlchemy(session_options={"expire_on_commit": False})
login_manager = LoginManager()
migrate = Migrate()
mail = Mail()
csrf = CSRFProtect()


def init_extensions(app):
    """Initialize all extensions with the given Flask app."""
    db.init_app(app)

    migrate.init_app(app, db)
    if "db" not in app.cli.commands:
        app.cli.add_command(flask_migrate_cli)

    login_manager.init_app(app)
    login_manager.login_view = "login"

    csrf.init_app(app)

    # Flask-Mail removed - using Resend API instead
    app.logger.info("✅ Email system: Using Resend API (Flask-Mail disabled)")

    return app
