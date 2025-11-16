import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict

from flask import Flask, current_app
from flask_mail import Mail, Message


_DEFAULT_WORKERS = int(os.getenv("EMAIL_WORKERS", "10") or "10")
EXECUTOR = ThreadPoolExecutor(max_workers=_DEFAULT_WORKERS)

# Simple in-memory status tracking for bulk jobs
email_status: Dict[str, Dict[str, Any]] = {}


def _build_mail_app(smtp_config: Dict[str, Any]) -> Flask:
    mail_app = Flask("email_sender")
    mail_app.config.update(
        MAIL_SERVER=smtp_config.get("server"),
        MAIL_PORT=int(smtp_config.get("port") or 587),
        MAIL_USE_TLS=bool(smtp_config.get("use_tls", True)),
        MAIL_USERNAME=smtp_config.get("username"),
        MAIL_PASSWORD=smtp_config.get("password"),
        MAIL_DEFAULT_SENDER=smtp_config.get("username"),
    )
    return mail_app


def _send_single_email_job(app, recipient: str, subject: str, body: str, smtp_config: Dict[str, Any]) -> None:
    with app.app_context():
        log = current_app.logger
        thread_name = threading.current_thread().name
        log.info(
            "email_queue._send_single_email_job: start",
            extra={
                "recipient": recipient,
                "smtp_server": smtp_config.get("server"),
                "smtp_port": smtp_config.get("port"),
                "thread": thread_name,
            },
        )

        try:
            mail_app = _build_mail_app(smtp_config)
            mail = Mail(mail_app)

            with mail_app.app_context():
                msg = Message(subject=subject, recipients=[recipient])
                if "<" in (body or "") and ">" in (body or ""):
                    msg.html = body
                else:
                    msg.body = body

                mail.send(msg)

            log.info(
                "email_queue._send_single_email_job: success",
                extra={"recipient": recipient, "thread": thread_name},
            )
        except Exception as exc:
            log.error(
                f"email_queue._send_single_email_job: failed to send email to {recipient}: {exc}",
                exc_info=True,
            )


def _send_bulk_email_job(
    app,
    recipients_source: Any,
    subject: str,
    body: str,
    smtp_config: Dict[str, Any],
    job_id: str,
) -> None:
    with app.app_context():
        log = current_app.logger
        thread_name = threading.current_thread().name

        email_status[job_id] = {
            "status": "running",
            "sent": 0,
            "failed": 0,
            "total": 0,
        }

        log.info(
            "email_queue._send_bulk_email_job: start",
            extra={
                "job_id": job_id,
                "thread": thread_name,
                "smtp_server": smtp_config.get("server"),
                "smtp_port": smtp_config.get("port"),
            },
        )

        try:
            mail_app = _build_mail_app(smtp_config)
            mail = Mail(mail_app)

            with mail_app.app_context():
                sent = 0
                failed = 0
                total = 0

                # Stream recipients; do NOT materialize full list
                if hasattr(recipients_source, "yield_per"):
                    iterator = recipients_source.yield_per(100)
                    def iter_emails():
                        for user in iterator:
                            addr = getattr(user, "email", None)
                            if addr:
                                yield addr
                else:
                    def iter_emails():
                        for addr in recipients_source:
                            if addr:
                                yield addr

                for addr in iter_emails():
                    total += 1
                    try:
                        msg = Message(subject=subject, recipients=[addr])
                        if "<" in (body or "") and ">" in (body or ""):
                            msg.html = body
                        else:
                            msg.body = body

                        mail.send(msg)
                        sent += 1
                        log.info(
                            "email_queue._send_bulk_email_job: email sent",
                            extra={"job_id": job_id, "recipient": addr, "thread": thread_name},
                        )
                    except Exception as exc_send:
                        failed += 1
                        log.error(
                            f"email_queue._send_bulk_email_job: failed to send to {addr}: {exc_send}",
                            exc_info=True,
                        )

                    email_status[job_id] = {
                        "status": "running",
                        "sent": sent,
                        "failed": failed,
                        "total": total,
                    }

                email_status[job_id] = {
                    "status": "completed",
                    "sent": sent,
                    "failed": failed,
                    "total": total,
                }

                log.info(
                    "email_queue._send_bulk_email_job: finished",
                    extra={
                        "job_id": job_id,
                        "thread": thread_name,
                        "sent": sent,
                        "failed": failed,
                        "total": total,
                    },
                )
        except Exception as exc:
            current = email_status.get(job_id, {"sent": 0, "failed": 0, "total": 0})
            email_status[job_id] = {
                "status": "failed",
                "sent": current.get("sent", 0),
                "failed": current.get("failed", 0),
                "total": current.get("total", 0),
            }
            log.error(
                f"email_queue._send_bulk_email_job: unrecoverable error for job {job_id}: {exc}",
                exc_info=True,
            )


def queue_single_email(app, recipient: str, subject: str, body: str, smtp_config: Dict[str, Any]) -> None:
    app_obj = app
    EXECUTOR.submit(_send_single_email_job, app_obj, recipient, subject, body, smtp_config)


def queue_bulk_email(app, recipients: Any, subject: str, body: str, smtp_config: Dict[str, Any]) -> str:
    app_obj = app
    job_id = str(uuid.uuid4())
    EXECUTOR.submit(_send_bulk_email_job, app_obj, recipients, subject, body, smtp_config, job_id)
    return job_id


