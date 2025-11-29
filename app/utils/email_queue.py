import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, List, Optional

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
    """Get from_email from database settings, with fallback to environment variable.
    
    Formats the FROM email as "Store <email@domain.com>" if business name is available.
    """
    try:
        from app import AppSettings
        settings = AppSettings.query.first()
        if settings:
            from_email = settings.resend_from_email
            if from_email:
                # Format with business name if available
                business_name = getattr(settings, 'business_name', None) or 'Store'
                if business_name and '<' not in from_email:
                    return f"{business_name} <{from_email}>"
                return from_email
    except Exception:
        pass
    # Fallback to environment variable, then default
    from_email = os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev")
    business_name = os.getenv("BUSINESS_NAME", "Store")
    if '<' not in from_email:
        return f"{business_name} <{from_email}>"
    return from_email


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

            # Use official Resend API format: "to" must be a list
            payload: Dict[str, Any] = {
                "from": from_email,
                "to": [recipient],  # Resend API requires "to" as a list
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
            try:
                if hasattr(recipients_source, "yield_per"):
                    # SQLAlchemy query - use yield_per for efficient streaming
                    log.info("email_queue._send_bulk_email_job: Using SQLAlchemy query with yield_per")
                    iterator = recipients_source.yield_per(100)
                    for user in iterator:
                        try:
                            addr = getattr(user, "email", None)
                            if is_valid_email(addr):
                                valid_emails.append(addr)
                            else:
                                skipped += 1
                                log.warning(f"email_queue._send_bulk_email_job: skipping invalid email: {addr}")
                        except Exception as user_exc:
                            skipped += 1
                            log.warning(
                                f"email_queue._send_bulk_email_job: error processing user record: {user_exc}",
                                exc_info=True,
                            )
                else:
                    # List or other iterable of email addresses
                    log.info("email_queue._send_bulk_email_job: Iterating over email address list")
                    for addr in recipients_source:
                        try:
                            if is_valid_email(addr):
                                valid_emails.append(addr)
                            else:
                                skipped += 1
                                log.warning(f"email_queue._send_bulk_email_job: skipping invalid email: {addr}")
                        except Exception as addr_exc:
                            skipped += 1
                            log.warning(
                                f"email_queue._send_bulk_email_job: error processing email address: {addr_exc}",
                                exc_info=True,
                            )
            except Exception as collection_exc:
                log.error(
                    f"email_queue._send_bulk_email_job: failed to collect recipient emails: {collection_exc}",
                    exc_info=True,
                    extra={"job_id": job_id, "thread": thread_name},
                )
                email_status[job_id] = {
                    "status": "failed",
                    "sent": 0,
                    "failed": 0,
                    "total": 0,
                }
                return

            total_valid = len(valid_emails)
            log.info(f"email_queue._send_bulk_email_job: Valid customer emails: {total_valid}, Skipped: {skipped}")

            if total_valid == 0:
                log.warning("email_queue._send_bulk_email_job: No valid emails to send")
                email_status[job_id] = {
                    "status": "completed",
                    "sent": 0,
                    "failed": 0,
                    "total": 0,
                }
                return

            # Update status with total count before starting
            email_status[job_id] = {
                "status": "running",
                "sent": 0,
                "failed": 0,
                "total": total_valid,
            }

            # Send emails individually to each customer (privacy requirement)
            # This works with restricted Resend API keys and ensures individual delivery
            log.info(f"email_queue._send_bulk_email_job: Sending {total_valid} emails individually to customers")
            
            for idx, email in enumerate(valid_emails, start=1):
                total += 1
                try:
                    # Ensure Resend is configured for each email (in case of long-running job)
                    _ensure_resend_config()
                    
                    # Build payload for individual email
                    payload: Dict[str, Any] = {
                        "from": from_email,
                        "to": [email],  # Resend API requires "to" as a list
                        "subject": subject,
                        "html": html_body or "",
                    }
                    if metadata:
                        payload["headers"] = {"X-Metadata": str(metadata)}

                    # Send email using Resend API
                    response = resend.Emails.send(payload)
                    
                    # Check if send was successful
                    # Resend API returns an object with 'id' field on success
                    if response and (hasattr(response, 'id') or (isinstance(response, dict) and response.get('id'))):
                        sent += 1
                        log.info(
                            f"email_queue._send_bulk_email_job: Successfully sent to: {email} ({idx}/{total_valid})",
                            extra={
                                "job_id": job_id,
                                "recipient": email,
                                "thread": thread_name,
                                "progress": f"{idx}/{total_valid}",
                            },
                        )
                    else:
                        # Response doesn't indicate success
                        failed += 1
                        log.error(
                            f"email_queue._send_bulk_email_job: Unexpected response when sending to: {email} - {response}",
                            extra={"job_id": job_id, "recipient": email, "thread": thread_name},
                        )
                        
                except Exception as individual_error:
                    failed += 1
                    error_msg = str(individual_error)
                    log.error(
                        f"email_queue._send_bulk_email_job: Error sending to: {email} ({idx}/{total_valid}) - {error_msg}",
                        exc_info=True,
                        extra={
                            "job_id": job_id,
                            "recipient": email,
                            "thread": thread_name,
                            "progress": f"{idx}/{total_valid}",
                        },
                    )
                    # Continue to next email - don't let individual failures block others
                
                # Update status periodically (every 10 emails or at the end)
                if total % 10 == 0 or idx == total_valid:
                    email_status[job_id] = {
                        "status": "running",
                        "sent": sent,
                        "failed": failed,
                        "total": total,
                    }
                    log.info(
                        f"email_queue._send_bulk_email_job: Progress update - Sent: {sent}, Failed: {failed}, Total: {total}/{total_valid}",
                        extra={
                            "job_id": job_id,
                            "sent": sent,
                            "failed": failed,
                            "total": total,
                            "progress": f"{idx}/{total_valid}",
                        },
                    )

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
    """Queue a bulk email job for background execution.
    
    Args:
        app: Flask application instance
        recipients: SQLAlchemy query or list of email addresses
        subject: Email subject line
        html_body: HTML email body
        metadata: Optional metadata dict to include in email headers
        
    Returns:
        job_id: Unique identifier for tracking this email job
    """
    app_obj = app
    job_id = str(uuid.uuid4())
    
    # Initialize job status before submitting
    email_status[job_id] = {
        "status": "queued",
        "sent": 0,
        "failed": 0,
        "total": 0,
    }
    
    try:
        # Submit job to thread pool executor
        future = EXECUTOR.submit(_send_bulk_email_job, app_obj, recipients, subject, html_body, metadata, job_id)
        
        # Log job submission (don't wait for result)
        try:
            log = app.logger
            log.info(
                f"email_queue.queue_bulk_email: Job {job_id} queued successfully",
                extra={"job_id": job_id},
            )
        except Exception:
            pass  # Logger might not be available in all contexts
        
        return job_id
    except Exception as exc:
        # If job submission fails, mark as failed immediately
        email_status[job_id] = {
            "status": "failed",
            "sent": 0,
            "failed": 0,
            "total": 0,
            "error": str(exc),
        }
        try:
            log = app.logger
            log.error(
                f"email_queue.queue_bulk_email: Failed to queue job {job_id}: {exc}",
                exc_info=True,
                extra={"job_id": job_id},
            )
        except Exception:
            pass
        raise


def get_email_job_status(job_id: str) -> Optional[Dict[str, Any]]:
    return email_status.get(job_id)


