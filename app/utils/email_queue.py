import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, Optional

from flask import current_app
import resend


_DEFAULT_WORKERS = int(os.getenv("EMAIL_WORKERS", "10") or "10")
EXECUTOR = ThreadPoolExecutor(max_workers=_DEFAULT_WORKERS)

# Simple in-memory status tracking for bulk jobs
email_status: Dict[str, Dict[str, Any]] = {}


def _ensure_resend_config() -> None:
    api_key = os.getenv("RESEND_API_KEY")
    if api_key:
        resend.api_key = api_key


def _get_from_email() -> str:
    """Get from_email from database settings, with fallback to environment variable."""
    try:
        from app import AppSettings
        settings = AppSettings.query.first()
        if settings and settings.from_email:
            return settings.from_email
    except Exception:
        pass
    # Fallback to environment variable, then default
    return os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev")


def _send_single_email_job(app, recipient: str, subject: str, html_body: str, metadata: Optional[Dict[str, Any]]) -> None:
    with app.app_context():
        log = current_app.logger
        thread_name = threading.current_thread().name
        log.info(
            "email_queue._send_single_email_job: start",
            extra={
                "recipient": recipient,
                "thread": thread_name,
            },
        )

        try:
            _ensure_resend_config()
            from_email = _get_from_email()

            payload: Dict[str, Any] = {
                "from": from_email,
                "to": recipient,
                "subject": subject,
                "html": html_body or "",
            }
            if metadata:
                payload["headers"] = {"X-Metadata": str(metadata)}

            resend.Emails.send(payload)

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
    html_body: str,
    metadata: Optional[Dict[str, Any]],
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
            },
        )

        try:
            _ensure_resend_config()
            from_email = _get_from_email()

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
                    payload: Dict[str, Any] = {
                        "from": from_email,
                        "to": addr,
                        "subject": subject,
                        "html": html_body or "",
                    }
                    if metadata:
                        payload["headers"] = {"X-Metadata": str(metadata)}

                    resend.Emails.send(payload)
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


def queue_single_email(
    app,
    recipient: str,
    subject: str,
    html_body: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    app_obj = app
    EXECUTOR.submit(_send_single_email_job, app_obj, recipient, subject, html_body, metadata)


def queue_bulk_email(
    app,
    recipients: Any,
    subject: str,
    html_body: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    app_obj = app
    job_id = str(uuid.uuid4())
    EXECUTOR.submit(_send_bulk_email_job, app_obj, recipients, subject, html_body, metadata, job_id)
    return job_id


def get_email_job_status(job_id: str) -> Optional[Dict[str, Any]]:
    return email_status.get(job_id)


