import os
import time
from typing import Any, Callable, Iterable, Optional

from flask import current_app, render_template


def _get_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _get_float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def start_batch_email_send(
    app,
    email_query: Any,
    subject: str,
    template_name: Optional[str],
    context_generator: Optional[Callable[[Any], Optional[dict]]] = None,
    base_context: Optional[dict] = None,
) -> None:
    """
    Stream and send emails in batches in a background-safe way.

    - email_query: SQLAlchemy Query or any iterable yielding recipient objects.
      Each recipient must expose an `email` attribute.
    - subject: Email subject line.
    - template_name: Jinja2 template name to render, or None for text-only emails.
    - context_generator: Optional callback(recipient) -> dict used to build
      per-recipient template context. If None, a shared template render is used.
    - base_context: Optional dict merged into every context (if used).
    """
    batch_size = _get_int_env("EMAIL_BATCH_SIZE", 50)
    batch_delay = _get_float_env("EMAIL_BATCH_DELAY_SECONDS", 1.0)

    with app.app_context():
        log = app.logger
        log.info(
            "email_worker.start_batch_email_send: starting job",
            extra={"batch_size": batch_size, "template": template_name},
        )

        from app.extensions import mail
        from flask_mail import Message

        def recipient_iter() -> Iterable[Any]:
            query_obj = email_query
            # Prefer streaming with yield_per when available
            if hasattr(query_obj, "yield_per"):
                try:
                    query_obj = query_obj.yield_per(max(batch_size, 100))
                except Exception:
                    # Fallback to plain iteration
                    pass
            for item in query_obj:
                yield item

        shared_html = None
        shared_text = None
        if template_name and context_generator is None:
            try:
                ctx = dict(base_context or {})
                shared_html = render_template(template_name, **ctx)
                shared_text = ctx.get("body_text")
                log.info(
                    "email_worker.start_batch_email_send: rendered shared template once",
                    extra={"template": template_name},
                )
            except Exception as exc:
                log.error(
                    f"email_worker.start_batch_email_send: failed to render shared template {template_name}: {exc}",
                    exc_info=True,
                )

        batch: list[Any] = []
        total_sent = 0
        total_failed = 0

        for recipient in recipient_iter():
            batch.append(recipient)
            if len(batch) >= batch_size:
                s, f = _send_batch(
                    app=app,
                    mail=mail,
                    Message=Message,
                    subject=subject,
                    recipients=batch,
                    template_name=template_name,
                    context_generator=context_generator,
                    base_context=base_context,
                    shared_html=shared_html,
                    shared_text=shared_text,
                )
                total_sent += s
                total_failed += f
                log.info(
                    "email_worker.start_batch_email_send: batch completed",
                    extra={"batch_sent": s, "batch_failed": f, "total_sent": total_sent, "total_failed": total_failed},
                )
                batch = []
                if batch_delay > 0:
                    time.sleep(batch_delay)

        if batch:
            s, f = _send_batch(
                app=app,
                mail=mail,
                Message=Message,
                subject=subject,
                recipients=batch,
                template_name=template_name,
                context_generator=context_generator,
                base_context=base_context,
                shared_html=shared_html,
                shared_text=shared_text,
            )
            total_sent += s
            total_failed += f
            log.info(
                "email_worker.start_batch_email_send: final batch completed",
                extra={"batch_sent": s, "batch_failed": f, "total_sent": total_sent, "total_failed": total_failed},
            )

        log.info(
            "email_worker.start_batch_email_send: job finished",
            extra={"total_sent": total_sent, "total_failed": total_failed},
        )


def _send_batch(
    app,
    mail,
    Message,
    subject: str,
    recipients: list[Any],
    template_name: Optional[str],
    context_generator: Optional[Callable[[Any], Optional[dict]]],
    base_context: Optional[dict],
    shared_html: Optional[str],
    shared_text: Optional[str],
) -> tuple[int, int]:
    log = app.logger
    sent = 0
    failed = 0

    for recipient in recipients:
        to_addr = getattr(recipient, "email", None)
        if not to_addr:
            continue

        try:
            html_body = shared_html
            text_body = shared_text

            if template_name and context_generator is not None:
                ctx = dict(base_context or {})
                try:
                    extra_ctx = context_generator(recipient) or {}
                    ctx.update(extra_ctx)
                except Exception as ctx_exc:
                    log.error(
                        f"email_worker._send_batch: context generation failed for {to_addr}: {ctx_exc}",
                        exc_info=True,
                    )
                html_body = render_template(template_name, **ctx)
                text_body = ctx.get("body_text")

            msg = Message(subject=subject, recipients=[to_addr])
            if html_body:
                msg.html = html_body
            if text_body:
                msg.body = text_body

            mail.send(msg)
            sent += 1
            log.info(
                "email_worker._send_batch: email sent",
                extra={"recipient": to_addr},
            )
        except Exception as exc:
            failed += 1
            log.error(
                f"email_worker._send_batch: failed to send email to {to_addr}: {exc}",
                exc_info=True,
            )

    return sent, failed


def send_email_safe(
    app,
    to: str,
    subject: str,
    body_html: Optional[str],
    body_text: Optional[str] = None,
) -> None:
    """
    Fire-and-forget safe single-email sender.
    Logs all failures and never raises.
    """
    if not to:
        return

    with app.app_context():
        log = app.logger
        log.info("email_worker.send_email_safe: preparing to send single email", extra={"recipient": to})

        try:
            from app.extensions import mail
            from flask_mail import Message

            msg = Message(subject=subject, recipients=[to])
            if body_html:
                msg.html = body_html
            if body_text:
                msg.body = body_text

            mail.send(msg)
            log.info("email_worker.send_email_safe: email sent", extra={"recipient": to})
        except Exception as exc:
            log.error(
                f"email_worker.send_email_safe: failed to send email to {to}: {exc}",
                exc_info=True,
            )


