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
    """Configure Resend API key from database settings or environment."""
    try:
        from app import AppSettings
        settings = AppSettings.query.first()
        if settings and settings.resend_api_key:
            resend.api_key = settings.resend_api_key
            return
    except Exception:
        pass
    # Fallback to environment variable
    api_key = os.getenv("RESEND_API_KEY")
    if api_key:
        resend.api_key = api_key


def _get_from_email() -> str:
    """Get from_email from database settings, with fallback to environment variable."""
    try:
        from app import AppSettings
        settings = AppSettings.query.first()
        if settings and settings.resend_from_email:
            return settings.resend_from_email
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
            log.info(f"email_queue._send_bulk_email_job: Resend configured, from_email={from_email}")

            sent = 0
            failed = 0
            total = 0
            skipped = 0
            valid_emails = []

            # Helper function to validate email
            def is_valid_email(email: str) -> bool:
                """Check if email is valid: not None, not empty, contains @"""
                if not email:
                    return False
                if not isinstance(email, str):
                    return False
                email = email.strip()
                if not email:
                    return False
                if "@" not in email:
                    return False
                return True

            # Stream recipients and collect valid emails
            log.info("email_queue._send_bulk_email_job: collecting valid customer emails")
            if hasattr(recipients_source, "yield_per"):
                iterator = recipients_source.yield_per(100)
                for user in iterator:
                    addr = getattr(user, "email", None)
                    if is_valid_email(addr):
                        valid_emails.append(addr)
                    else:
                        skipped += 1
                        log.warning(f"email_queue._send_bulk_email_job: skipping invalid email: {addr}")
            else:
                for addr in recipients_source:
                    if is_valid_email(addr):
                        valid_emails.append(addr)
                    else:
                        skipped += 1
                        log.warning(f"email_queue._send_bulk_email_job: skipping invalid email: {addr}")

            total_valid = len(valid_emails)
            log.info(f"email_queue._send_bulk_email_job: Valid customer emails: {total_valid}, Skipped: {skipped}")

            # Send emails to all valid addresses - NEVER break the loop on errors
            for email in valid_emails:
                total += 1
                log.info(f"email_queue._send_bulk_email_job: Sending to: {email} ({total}/{total_valid})")
                try:
                    payload: Dict[str, Any] = {
                        "from": from_email,
                        "to": email,
                        "subject": subject,
                        "html": html_body or "",
                    }
                    if metadata:
                        payload["headers"] = {"X-Metadata": str(metadata)}

                    r = resend.Emails.send(payload)
                    sent += 1
                    log.info(
                        f"email_queue._send_bulk_email_job: Successfully sent to: {email}",
                        extra={"job_id": job_id, "recipient": email, "thread": thread_name},
                    )
                except Exception as e:
                    failed += 1
                    error_msg = str(e)
                    log.error(
                        f"email_queue._send_bulk_email_job: Error sending to: {email} - {error_msg}",
                        exc_info=True,
                        extra={"job_id": job_id, "recipient": email, "thread": thread_name},
                    )
                    # Continue to next email - DO NOT break the loop
                    continue

                # Update status after each attempt
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
                f"email_queue._send_bulk_email_job: finished - Total: {total}, Sent: {sent}, Failed: {failed}, Skipped: {skipped}",
                extra={
                    "job_id": job_id,
                    "thread": thread_name,
                    "sent": sent,
                    "failed": failed,
                    "total": total,
                    "skipped": skipped,
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


