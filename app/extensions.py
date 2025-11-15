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

    try:
        if app.config.get("MAIL_SERVER"):
            mail.init_app(app)
            app.logger.info(
                "✅ Flask-Mail initialized: %s:%s",
                app.config.get("MAIL_SERVER"),
                app.config.get("MAIL_PORT"),
            )
        else:
            app.logger.warning(
                "⚠️ Flask-Mail not initialized: MAIL_SERVER not configured"
            )
    except Exception as exc:  # pragma: no cover - defensive logging
        app.logger.error(f"❌ Failed to initialize Flask-Mail: {exc}", exc_info=True)

    return app
