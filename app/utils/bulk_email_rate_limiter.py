"""
Rate limiter for bulk email sending.

Implements token bucket algorithm to control sending rate and
prevent hitting Resend API rate limits.
"""
import time
import threading
from typing import Optional
from collections import deque
from datetime import datetime, timedelta


class TokenBucket:
    """
    Token bucket rate limiter implementation.
    
    Allows bursts up to capacity, refills at a steady rate.
    Thread-safe for use in multi-threaded environments.
    """
    
    def __init__(self, capacity: float, refill_rate: float):
        """
        Initialize token bucket.
        
        Args:
            capacity: Maximum number of tokens (allows bursts)
            refill_rate: Tokens per second to refill
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = capacity
        self.last_refill = time.time()
        self._lock = threading.Lock()
    
    def consume(self, tokens: float = 1.0) -> bool:
        """
        Try to consume tokens from the bucket.
        
        Args:
            tokens: Number of tokens to consume (default: 1)
            
        Returns:
            True if tokens were consumed, False if insufficient tokens
        """
        with self._lock:
            # Refill tokens based on time elapsed
            now = time.time()
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last_refill = now
            
            # Check if we have enough tokens
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False
    
    def time_until_next_token(self) -> float:
        """
        Calculate time until next token is available.
        
        Returns:
            Seconds until next token (0 if tokens available)
        """
        with self._lock:
            if self.tokens >= 1.0:
                return 0.0
            
            # Calculate how long to wait for one token
            tokens_needed = 1.0 - self.tokens
            return tokens_needed / self.refill_rate


class BulkEmailRateLimiter:
    """
    Rate limiter for bulk email sending with per-minute and per-hour limits.
    
    Uses token bucket algorithm to smooth out sending rate and prevent
    hitting Resend API rate limits.
    """
    
    def __init__(
        self,
        emails_per_minute: int = 10,
        emails_per_hour: int = 1000,
    ):
        """
        Initialize rate limiter.
        
        Args:
            emails_per_minute: Maximum emails per minute
            emails_per_hour: Maximum emails per hour
        """
        # Token bucket for per-minute rate (allows small bursts)
        self.per_minute_bucket = TokenBucket(
            capacity=emails_per_minute,
            refill_rate=emails_per_minute / 60.0  # tokens per second
        )
        
        # Token bucket for per-hour rate (allows larger bursts)
        self.per_hour_bucket = TokenBucket(
            capacity=emails_per_hour,
            refill_rate=emails_per_hour / 3600.0  # tokens per second
        )
        
        self._lock = threading.Lock()
    
    def wait_if_needed(self) -> None:
        """
        Wait if necessary to comply with rate limits.
        
        Blocks until tokens are available from both buckets.
        """
        while True:
            with self._lock:
                # Check both buckets
                minute_available = self.per_minute_bucket.consume(1.0)
                hour_available = self.per_hour_bucket.consume(1.0)
                
                if minute_available and hour_available:
                    # Both buckets have tokens, we can proceed
                    return
                
                # Calculate wait time (use the longer wait)
                minute_wait = self.per_minute_bucket.time_until_next_token()
                hour_wait = self.per_hour_bucket.time_until_next_token()
                wait_time = max(minute_wait, hour_wait)
                
                if wait_time > 0:
                    # Sleep outside the lock to avoid blocking other threads
                    time.sleep(min(wait_time, 60.0))  # Cap at 60 seconds
                else:
                    # Shouldn't happen, but break just in case
                    return
    
    def handle_rate_limit_error(self, retry_count: int) -> float:
        """
        Calculate backoff time after hitting a rate limit.
        
        Args:
            retry_count: Current retry attempt number (0-indexed)
            
        Returns:
            Seconds to wait before retry (exponential backoff, capped at 5 minutes)
        """
        # Exponential backoff: 2^retry_count seconds, capped at 300 seconds (5 minutes)
        backoff_seconds = min(300.0, 2.0 ** retry_count)
        
        # Wait the calculated time
        time.sleep(backoff_seconds)
        
        return backoff_seconds

