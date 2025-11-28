"""
Email sending utility using Resend with database-backed settings.

This module provides a simple sendEmail() function that uses Resend
and loads email settings from the database (AppSettings model).
"""
import os
from typing import Optional
from flask import current_app
import resend


def sendEmail(to: str, subject: str, html: str) -> bool:
    """
    Send an email using Resend with settings from the database.
    
    Args:
        to: Recipient email address
        subject: Email subject line
        html: HTML email body
        
    Returns:
        bool: True if email was sent successfully, False otherwise
        
    Example:
        sendEmail(
            to="customer@example.com",
            subject="Order Confirmation",
            html="<p>Thank you for your order!</p>"
        )
    """
    try:
        # Get app context
        app = current_app._get_current_object()
        
        # Get settings from database
        from app import AppSettings, db
        with app.app_context():
            settings = AppSettings.query.first()
            if not settings:
                current_app.logger.error("sendEmail: AppSettings not found in database")
                return False
            
            # Get API key from database settings or environment
            api_key = settings.resend_api_key or os.getenv("RESEND_API_KEY")
            if not api_key:
                current_app.logger.error("sendEmail: RESEND_API_KEY not configured in environment or database")
                return False
            
            # Check if Resend is enabled
            if settings.resend_enabled is False:
                current_app.logger.warning("sendEmail: Resend email is disabled in settings")
                return False
            
            # Get from_email from database, fallback to environment, then default
            from_email = (
                settings.resend_from_email or 
                os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev")
            )
            
            # Format FROM email with business name if available
            business_name = getattr(settings, 'business_name', None) or os.getenv("BUSINESS_NAME", "Store")
            if business_name and '<' not in from_email:
                from_email = f"{business_name} <{from_email}>"
            
            # Configure Resend
            resend.api_key = api_key
            
            # Send email using official Resend API format: "to" must be a list
            r = resend.Emails.send({
                "from": from_email,
                "to": [to],  # Resend API requires "to" as a list
                "subject": subject,
                "html": html
            })
            
            current_app.logger.info(
                f"sendEmail: Email sent successfully to {to}",
                extra={"to": to, "subject": subject, "from": from_email}
            )
            return True
            
    except Exception as e:
        current_app.logger.error(
            f"sendEmail: Failed to send email to {to}: {str(e)}",
            exc_info=True
        )
        return False

