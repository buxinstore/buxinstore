"""
Retry engine for bulk email sending with exponential backoff.

Handles transient errors, rate limits, and network failures
with intelligent retry logic.
"""
import time
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
import resend


class RetryableError(Exception):
    """Error that can be retried (transient failure)."""
    pass


class PermanentError(Exception):
    """Error that should not be retried (permanent failure)."""
    pass


class RateLimitError(RetryableError):
    """Rate limit error from Resend API."""
    pass


class NetworkError(RetryableError):
    """Network/connection error."""
    pass


class AuthenticationError(PermanentError):
    """Authentication error (API key invalid)."""
    pass


def classify_resend_error(error: Exception) -> Tuple[bool, bool]:
    """
    Classify a Resend API error as retryable or permanent.
    
    Args:
        error: Exception raised by Resend API
        
    Returns:
        Tuple of (is_retryable: bool, is_rate_limit: bool)
    """
    error_str = str(error).lower()
    error_type = type(error).__name__
    
    # Rate limit errors
    if "429" in error_str or "rate limit" in error_str or "too many requests" in error_str:
        return True, True
    
    # Authentication errors (permanent)
    if "401" in error_str or "403" in error_str or "unauthorized" in error_str or "forbidden" in error_str:
        return False, False
    
    # Network errors (retryable)
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True, False
    
    # Resend SDK specific errors
    if hasattr(error, 'status_code'):
        status_code = error.status_code
        if status_code == 429:
            return True, True
        if status_code in [401, 403]:
            return False, False
        if status_code >= 500:  # Server errors are retryable
            return True, False
    
    # Default: treat as retryable unless clearly permanent
    if "400" in error_str and "invalid" in error_str:
        return False, False
    
    # Most other errors are retryable
    return True, False


def calculate_backoff(attempt: int, base_delay: float = 60.0, max_delay: float = 300.0) -> float:
    """
    Calculate exponential backoff delay.
    
    Args:
        attempt: Retry attempt number (0-indexed)
        base_delay: Base delay in seconds
        max_delay: Maximum delay in seconds
        
    Returns:
        Delay in seconds
    """
    # Exponential backoff: base_delay * 2^attempt
    delay = base_delay * (2.0 ** attempt)
    
    # Cap at max_delay
    return min(delay, max_delay)


def calculate_retry_time(attempt: int, base_delay: float = 60.0) -> datetime:
    """
    Calculate when to retry next.
    
    Args:
        attempt: Current attempt number (0-indexed)
        base_delay: Base delay in seconds
        
    Returns:
        datetime when retry should occur
    """
    delay_seconds = calculate_backoff(attempt, base_delay)
    return datetime.utcnow() + timedelta(seconds=delay_seconds)


class EmailSendResult:
    """Result of an email send attempt."""
    
    def __init__(
        self,
        success: bool,
        email_id: Optional[str] = None,
        error: Optional[str] = None,
        is_retryable: bool = False,
    ):
        self.success = success
        self.email_id = email_id
        self.error = error
        self.is_retryable = is_retryable


def send_email_with_retry(
    recipient: str,
    subject: str,
    html_body: str,
    from_email: str,
    max_retries: int = 3,
    base_backoff: float = 60.0,
) -> EmailSendResult:
    """
    Send an email with automatic retry logic.
    
    Args:
        recipient: Recipient email address
        subject: Email subject
        html_body: HTML email body
        from_email: FROM email address
        max_retries: Maximum number of retry attempts
        base_backoff: Base backoff delay in seconds
        
    Returns:
        EmailSendResult with success status and details
    """
    attempt = 0
    
    while attempt <= max_retries:
        try:
            # Build payload
            payload = {
                "from": from_email,
                "to": [recipient],
                "subject": subject,
                "html": html_body,
            }
            
            # Send via Resend API
            response = resend.Emails.send(payload)
            
            # Validate response
            if response is None:
                raise Exception("Resend API returned None response")
            
            # Extract email ID from response
            email_id = None
            if hasattr(response, 'id'):
                email_id = response.id
            elif isinstance(response, dict):
                email_id = response.get('id')
            elif hasattr(response, 'data'):
                data = response.data
                if isinstance(data, dict):
                    email_id = data.get('id')
            
            if email_id:
                return EmailSendResult(success=True, email_id=email_id)
            else:
                # Response doesn't have expected ID, but no exception
                # Log warning but consider it success (Resend may have sent it)
                return EmailSendResult(
                    success=True,
                    email_id=None,
                    error="Response missing email ID, but no error raised"
                )
        
        except Exception as e:
            # Classify error
            is_retryable, is_rate_limit = classify_resend_error(e)
            
            # Permanent errors - don't retry
            if not is_retryable:
                return EmailSendResult(
                    success=False,
                    error=str(e),
                    is_retryable=False
                )
            
            # Rate limit errors - use longer backoff
            if is_rate_limit:
                backoff = calculate_backoff(attempt, base_delay=120.0, max_delay=600.0)
            else:
                backoff = calculate_backoff(attempt, base_delay=base_backoff)
            
            # Check if we have retries left
            if attempt < max_retries:
                # Wait before retry
                time.sleep(min(backoff, 300.0))  # Cap at 5 minutes
                attempt += 1
                continue
            else:
                # Max retries exceeded
                return EmailSendResult(
                    success=False,
                    error=f"Max retries exceeded: {str(e)}",
                    is_retryable=True
                )
    
    # Shouldn't reach here, but handle anyway
    return EmailSendResult(
        success=False,
        error="Unexpected retry loop exit",
        is_retryable=False
    )

