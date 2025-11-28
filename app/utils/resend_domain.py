"""
Utility functions for Resend domain verification.

This module provides functions to check if email domains are verified in Resend.
Only the FROM email domain needs to be verified - recipient domains can be anything.
"""
import os
from typing import Optional, Tuple
from flask import current_app
import resend


def extract_domain_from_email(email: str) -> Optional[str]:
    """
    Extract domain from an email address.
    
    Args:
        email: Email address (e.g., "user@example.com")
        
    Returns:
        Domain name (e.g., "example.com") or None if invalid
    """
    if not email or "@" not in email:
        return None
    try:
        return email.split("@", 1)[1].strip().lower()
    except (IndexError, AttributeError):
        return None


def is_domain_verified_in_resend(domain: str, api_key: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """
    Check if a domain is verified in Resend using the /domains API.
    
    Args:
        domain: Domain name to check (e.g., "example.com")
        api_key: Optional Resend API key. If not provided, will try to get from settings.
        
    Returns:
        Tuple of (is_verified: bool, error_message: Optional[str])
        - If verified: (True, None)
        - If not verified: (False, "Domain not verified")
        - If error: (False, error_message)
    """
    if not domain:
        return False, "Domain is required"
    
    # Get API key if not provided
    if not api_key:
        try:
            from app import AppSettings
            settings = AppSettings.query.first()
            if settings and settings.resend_api_key:
                api_key = settings.resend_api_key
            else:
                api_key = os.getenv("RESEND_API_KEY")
        except Exception:
            api_key = os.getenv("RESEND_API_KEY")
    
    if not api_key:
        return False, "Resend API key is not configured"
    
    try:
        # Configure Resend
        resend.api_key = api_key
        
        # List all domains from Resend
        domains_response = resend.Domains.list()
        
        # Handle different response formats
        domains_data = None
        if hasattr(domains_response, 'data'):
            domains_data = domains_response.data
        elif isinstance(domains_response, dict) and 'data' in domains_response:
            domains_data = domains_response['data']
        elif isinstance(domains_response, list):
            domains_data = domains_response
        
        # Check if the domain exists and is verified
        if domains_data:
            for domain_obj in domains_data:
                # Domain object might be a dict or object with 'name' or 'domain' attribute
                if isinstance(domain_obj, dict):
                    domain_name = domain_obj.get('name') or domain_obj.get('domain')
                    status = domain_obj.get('status') or (domain_obj.get('verification', {}) or {}).get('status')
                else:
                    domain_name = getattr(domain_obj, 'name', None) or getattr(domain_obj, 'domain', None)
                    status = getattr(domain_obj, 'status', None)
                    if not status:
                        verification = getattr(domain_obj, 'verification', None)
                        if verification:
                            if isinstance(verification, dict):
                                status = verification.get('status')
                            else:
                                status = getattr(verification, 'status', None)
                
                if domain_name and domain_name.lower() == domain.lower():
                    # Check verification status - Resend uses 'verified' or 'success' status
                    if status in ['verified', 'success']:
                        return True, None
                    else:
                        return False, f"Domain {domain} exists but is not verified (status: {status or 'unknown'})"
        
        # Domain not found in verified domains
        return False, f"Domain {domain} is not verified in Resend"
        
    except Exception as e:
        error_msg = str(e)
        current_app.logger.error(
            f"Error checking Resend domain verification for {domain}: {error_msg}",
            exc_info=True
        )
        # If API call fails, we can't verify, but don't block the user
        # Return False with error message
        return False, f"Could not verify domain: {error_msg}"


def is_from_email_domain_verified(from_email: str, api_key: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """
    Check if the FROM email's domain is verified in Resend.
    
    Args:
        from_email: FROM email address (e.g., "no-reply@techbuxin.com")
        api_key: Optional Resend API key
        
    Returns:
        Tuple of (is_verified: bool, error_message: Optional[str])
    """
    domain = extract_domain_from_email(from_email)
    if not domain:
        return False, f"Invalid FROM email address: {from_email}"
    
    return is_domain_verified_in_resend(domain, api_key)

