"""
Strict email validation for bulk email system.

Replaces simplistic validation with robust RFC-aware validation
that properly handles edge cases and prevents invalid emails from
wasting API calls.
"""
import re
from typing import Tuple, Optional
from email_validator import validate_email as email_validator_validate, EmailNotValidError


class EmailValidationError(Exception):
    """Raised when email validation fails."""
    pass


def strict_validate_email(email: str) -> Tuple[bool, Optional[str]]:
    """
    Strictly validate an email address using RFC-compliant rules.
    
    Args:
        email: Email address string to validate
        
    Returns:
        Tuple of (is_valid: bool, error_message: Optional[str])
        - (True, None) if email is valid
        - (False, error_message) if email is invalid
    """
    if not email:
        return False, "Email is empty or None"
    
    if not isinstance(email, str):
        return False, f"Email must be a string, got {type(email).__name__}"
    
    # Strip whitespace
    email = email.strip()
    
    if not email:
        return False, "Email is empty after stripping whitespace"
    
    # Basic format check - must contain @
    if "@" not in email:
        return False, "Email must contain @ symbol"
    
    # Check for obvious invalid patterns
    if email.startswith("@") or email.endswith("@"):
        return False, "Email cannot start or end with @"
    
    if email.count("@") > 1:
        return False, "Email cannot contain multiple @ symbols"
    
    # Split into local and domain parts
    parts = email.split("@", 1)
    local_part = parts[0]
    domain_part = parts[1]
    
    if not local_part or not domain_part:
        return False, "Email must have both local and domain parts"
    
    # Check for suspicious patterns that might indicate SQL injection or other attacks
    suspicious_patterns = [
        r"[<>\"']",  # HTML/script tags or quotes
        r"\.\.",     # Path traversal
        r"javascript:",  # XSS attempts
        r"data:",    # Data URIs
    ]
    
    for pattern in suspicious_patterns:
        if re.search(pattern, email, re.IGNORECASE):
            return False, f"Email contains suspicious pattern: {pattern}"
    
    # Check domain part has a TLD
    if "." not in domain_part:
        return False, "Domain must contain at least one dot (e.g., example.com)"
    
    # Use email-validator library for comprehensive RFC validation
    try:
        # This validates against RFC 5322 rules
        validated = email_validator_validate(
            email,
            check_deliverability=False,  # Don't check if domain actually accepts mail
            allow_smtputf8=False,  # Disable SMTPUTF8 for simplicity
        )
        # Normalize email (lowercase domain)
        normalized_email = validated.normalized
        
        # Additional sanity checks
        if len(normalized_email) > 254:  # RFC 5321 limit
            return False, "Email exceeds maximum length of 254 characters"
        
        if len(local_part) > 64:  # RFC 5321 limit
            return False, "Local part exceeds maximum length of 64 characters"
        
        if len(domain_part) > 255:  # RFC 5321 limit
            return False, "Domain exceeds maximum length of 255 characters"
        
        # Check for common typos or invalid patterns
        if domain_part.startswith(".") or domain_part.endswith("."):
            return False, "Domain cannot start or end with a dot"
        
        if ".." in domain_part:
            return False, "Domain cannot contain consecutive dots"
        
        return True, None
        
    except EmailNotValidError as e:
        return False, str(e)
    except Exception as e:
        # Fallback error
        return False, f"Email validation error: {str(e)}"


def is_valid_email(email: str) -> bool:
    """
    Simple boolean check for email validity.
    
    Args:
        email: Email address to check
        
    Returns:
        True if email is valid, False otherwise
    """
    is_valid, _ = strict_validate_email(email)
    return is_valid


def normalize_email(email: str) -> Optional[str]:
    """
    Normalize and validate an email address.
    
    Args:
        email: Email address to normalize
        
    Returns:
        Normalized email address if valid, None otherwise
    """
    is_valid, _ = strict_validate_email(email)
    if not is_valid:
        return None
    
    try:
        validated = email_validator_validate(
            email,
            check_deliverability=False,
            allow_smtputf8=False,
        )
        return validated.normalized
    except Exception:
        # Fallback: lowercase and strip
        return email.strip().lower()

