import logging
from threading import Thread
from typing import Dict

from flask import Flask, current_app
from flask_mail import Mail, Message


def send_test_email_background(recipient: str, smtp_config: Dict[str, str]) -> None:
    """
    Background worker that sends a single test email using an isolated Mail instance.

    NOTE: This module is kept for backward compatibility but is no longer used
    by the admin settings test-email route, which now sends synchronously.
    """
    app = current_app._get_current_object()
    log: logging.Logger = app.logger

    log.info(
        "email_background.send_test_email_background: starting",
        extra={
            "recipient": recipient,
            "smtp_server": smtp_config.get("server"),
            "smtp_port": smtp_config.get("port"),
        },
    )

    try:
        mail_app = Flask("email_sender")
        mail_app.config.update(
            MAIL_SERVER=smtp_config.get("server"),
            MAIL_PORT=smtp_config.get("port"),
            MAIL_USE_TLS=smtp_config.get("use_tls", True),
            MAIL_USERNAME=smtp_config.get("username"),
            MAIL_PASSWORD=smtp_config.get("password"),
            MAIL_DEFAULT_SENDER=smtp_config.get("username"),
        )

        mail = Mail(mail_app)

        with mail_app.app_context():
            msg = Message(
                subject="Test Email from BuXin Admin",
                sender=smtp_config.get("username"),
                recipients=[recipient],
                body=(
                    "Hello! This is a test email from your BuXin Admin Settings page. "
                    "Your email configuration is working correctly! ✅"
                ),
            )

            mail.send(msg)

        log.info(
            "email_background.send_test_email_background: email sent",
            extra={"recipient": recipient},
        )
    except Exception as exc:
        log.error(
            f"email_background.send_test_email_background: failed to send test email to {recipient}: {exc}",
            exc_info=True,
        )


def queue_test_email(app, recipient: str, smtp_config: Dict[str, str]) -> None:
    """
    Legacy helper preserved for compatibility. Prefer using email_queue for new code.
    """
    app_obj = app

    def _runner():
        with app_obj.app_context():
            send_test_email_background(recipient, smtp_config)

    Thread(target=_runner, daemon=True).start()


