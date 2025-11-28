"""
Email sender worker service for bulk email system.

Handles the actual sending of emails with retry logic, rate limiting,
progress tracking, and recovery capabilities.
"""
import uuid
from datetime import datetime, timedelta
from typing import Optional

from flask import current_app

from app.extensions import db
from app.models.bulk_email import (
    BulkEmailJob,
    BulkEmailJobStatus,
    BulkEmailRecipient,
    BulkEmailRecipientStatus,
)
from app.utils.bulk_email_lock import DistributedLockManager
from app.utils.bulk_email_rate_limiter import BulkEmailRateLimiter
from app.utils.bulk_email_retry import send_email_with_retry
from concurrent.futures import ThreadPoolExecutor


class BulkEmailSender:
    """
    Main email sender worker for bulk email jobs.
    
    Handles:
    - Distributed locking to prevent concurrent execution
    - Rate limiting to avoid API throttling
    - Retry logic for transient failures
    - Progress tracking and updates
    - Job recovery after crashes
    """
    
    # Configuration
    BATCH_SIZE = 50  # Process recipients in batches
    MAX_RETRIES = 3  # Maximum retry attempts per email
    LOCK_TIMEOUT_MINUTES = 60  # Lock timeout duration
    HEARTBEAT_INTERVAL = 10  # Progress update interval (every N emails)
    
    def __init__(
        self,
        emails_per_minute: int = 10,
        emails_per_hour: int = 1000,
    ):
        """
        Initialize email sender.
        
        Args:
            emails_per_minute: Maximum emails per minute
            emails_per_hour: Maximum emails per hour
        """
        self.lock_manager = DistributedLockManager(
            lock_timeout_minutes=self.LOCK_TIMEOUT_MINUTES,
            heartbeat_interval_minutes=5
        )
        self.rate_limiter = BulkEmailRateLimiter(
            emails_per_minute=emails_per_minute,
            emails_per_hour=emails_per_hour
        )
        self.worker_id = self.lock_manager.get_worker_id()
    
    def send_job_emails(self, job_id: uuid.UUID) -> bool:
        """
        Send emails for a bulk email job.
        
        This method handles the complete sending workflow including:
        - Acquiring distributed lock
        - Loading pending recipients
        - Sending emails with rate limiting and retry
        - Updating progress
        - Releasing lock
        
        Args:
            job_id: UUID of the job to process
            
        Returns:
            True if processing completed (successfully or failed), False if lock couldn't be acquired
        """
        log = current_app.logger
        
        log.info(
            f"BulkEmailSender: Starting email sending for job {job_id}",
            extra={"job_id": str(job_id), "worker_id": self.worker_id}
        )
        
        # Acquire distributed lock
        lock_token = self.lock_manager.acquire_job_lock(job_id)
        if not lock_token:
            log.info(
                f"BulkEmailSender: Job {job_id} is already being processed by another worker"
            )
            return False
        
        log.info(
            f"BulkEmailSender: Acquired lock for job {job_id}",
            extra={"lock_token": str(lock_token)}
        )
        
        try:
            # Load job
            job = db.session.query(BulkEmailJob).filter_by(id=job_id).first()
            if not job:
                log.error(f"BulkEmailSender: Job {job_id} not found")
                self.lock_manager.release_job_lock(job_id, lock_token)
                return False
            
            # Verify job is in correct state
            if job.status not in [
                BulkEmailJobStatus.RUNNING.value,
                BulkEmailJobStatus.PAUSED.value
            ]:
                log.warning(
                    f"BulkEmailSender: Job {job_id} is not in RUNNING/PAUSED status (current: {job.status})"
                )
                self.lock_manager.release_job_lock(job_id, lock_token)
                return True
            
            # Resume if paused
            if job.status == BulkEmailJobStatus.PAUSED.value:
                job.status = BulkEmailJobStatus.RUNNING.value
                db.session.commit()
            
            # Load pending recipients (status='pending' or retryable failures)
            pending_query = db.session.query(BulkEmailRecipient).filter(
                BulkEmailRecipient.job_id == job_id,
                db.or_(
                    BulkEmailRecipient.status == BulkEmailRecipientStatus.PENDING.value,
                    db.and_(
                        BulkEmailRecipient.status == BulkEmailRecipientStatus.FAILED.value,
                        BulkEmailRecipient.send_attempts < self.MAX_RETRIES,
                        db.or_(
                            BulkEmailRecipient.next_retry_at.is_(None),
                            BulkEmailRecipient.next_retry_at <= datetime.utcnow()
                        )
                    )
                )
            ).order_by(BulkEmailRecipient.created_at)
            
            # Process in batches
            processed = 0
            batch_count = 0
            
            while True:
                # Check if job was cancelled
                db.session.refresh(job)
                if job.status == BulkEmailJobStatus.CANCELLED.value:
                    log.info(f"BulkEmailSender: Job {job_id} was cancelled, stopping")
                    break
                
                # Load next batch
                batch = pending_query.limit(self.BATCH_SIZE).all()
                
                if not batch:
                    # No more recipients to process
                    break
                
                batch_count += 1
                log.debug(
                    f"BulkEmailSender: Processing batch {batch_count} with {len(batch)} recipients"
                )
                
                # Process each recipient in the batch
                for recipient in batch:
                    # Check job status again
                    if job.status == BulkEmailJobStatus.CANCELLED.value:
                        break
                    
                    try:
                        # Apply rate limiting
                        self.rate_limiter.wait_if_needed()
                        
                        # Send email with retry logic
                        result = send_email_with_retry(
                            recipient=recipient.recipient_email,
                            subject=job.subject,
                            html_body=job.html_body,
                            from_email=job.from_email,
                            max_retries=self.MAX_RETRIES,
                            base_backoff=60.0,
                        )
                        
                        # Update recipient status
                        if result.success:
                            recipient.status = BulkEmailRecipientStatus.SENT.value
                            recipient.sent_at = datetime.utcnow()
                            recipient.resend_email_id = result.email_id
                            recipient.send_attempts += 1
                            recipient.last_attempt_at = datetime.utcnow()
                            recipient.next_retry_at = None
                            recipient.error_message = None
                            
                            job.sent_count += 1
                            
                            log.debug(
                                f"BulkEmailSender: Sent email to {recipient.recipient_email}",
                                extra={"job_id": str(job_id), "recipient": recipient.recipient_email}
                            )
                        
                        elif result.is_retryable and recipient.send_attempts < self.MAX_RETRIES:
                            # Retryable error - schedule retry
                            recipient.status = BulkEmailRecipientStatus.PENDING.value
                            recipient.send_attempts += 1
                            recipient.last_attempt_at = datetime.utcnow()
                            recipient.next_retry_at = datetime.utcnow() + timedelta(
                                seconds=60 * (2 ** recipient.send_attempts)
                            )
                            recipient.error_message = result.error
                            
                            log.warning(
                                f"BulkEmailSender: Retryable error for {recipient.recipient_email}, "
                                f"will retry later (attempt {recipient.send_attempts}/{self.MAX_RETRIES}): {result.error}",
                                extra={"job_id": str(job_id), "recipient": recipient.recipient_email}
                            )
                        
                        else:
                            # Permanent failure or max retries exceeded
                            recipient.status = BulkEmailRecipientStatus.FAILED.value
                            recipient.send_attempts += 1
                            recipient.last_attempt_at = datetime.utcnow()
                            recipient.error_message = result.error
                            recipient.next_retry_at = None
                            
                            job.failed_count += 1
                            
                            log.error(
                                f"BulkEmailSender: Failed to send email to {recipient.recipient_email}: {result.error}",
                                extra={"job_id": str(job_id), "recipient": recipient.recipient_email}
                            )
                        
                        # Update progress
                        job.current_progress += 1
                        processed += 1
                        
                        # Save recipient update
                        db.session.add(recipient)
                        
                        # Update job progress periodically
                        if processed % self.HEARTBEAT_INTERVAL == 0:
                            db.session.add(job)
                            db.session.commit()
                            
                            # Extend lock to prevent expiry
                            self.lock_manager.extend_job_lock(job_id, lock_token)
                            
                            log.info(
                                f"BulkEmailSender: Progress update for job {job_id}: "
                                f"{job.current_progress}/{job.total_recipients} processed, "
                                f"{job.sent_count} sent, {job.failed_count} failed"
                            )
                    
                    except Exception as e:
                        # Error processing individual recipient - mark as failed but continue
                        log.error(
                            f"BulkEmailSender: Unexpected error processing recipient {recipient.recipient_email}: {e}",
                            exc_info=True,
                            extra={"job_id": str(job_id), "recipient": recipient.recipient_email}
                        )
                        
                        recipient.status = BulkEmailRecipientStatus.FAILED.value
                        recipient.send_attempts += 1
                        recipient.last_attempt_at = datetime.utcnow()
                        recipient.error_message = f"Unexpected error: {str(e)}"
                        recipient.next_retry_at = None
                        
                        job.failed_count += 1
                        job.current_progress += 1
                        processed += 1
                        
                        db.session.add(recipient)
                        continue
                
                # Commit batch
                db.session.add(job)
                db.session.commit()
                
                # Extend lock after each batch
                self.lock_manager.extend_job_lock(job_id, lock_token)
                
                log.debug(
                    f"BulkEmailSender: Completed batch {batch_count}, processed {processed} recipients so far"
                )
            
            # Final status update
            db.session.refresh(job)
            
            # Check if job is complete
            remaining_pending = db.session.query(BulkEmailRecipient).filter(
                BulkEmailRecipient.job_id == job_id,
                BulkEmailRecipient.status.in_([
                    BulkEmailRecipientStatus.PENDING.value
                ])
            ).count()
            
            if remaining_pending == 0:
                # No more pending recipients - job is complete
                job.status = BulkEmailJobStatus.COMPLETED.value
                job.completed_at = datetime.utcnow()
                
                log.info(
                    f"BulkEmailSender: Job {job_id} completed: "
                    f"{job.sent_count} sent, {job.failed_count} failed, {job.skipped_count} skipped"
                )
            else:
                # More recipients to process - will be picked up by next batch
                log.info(
                    f"BulkEmailSender: Job {job_id} batch complete, {remaining_pending} recipients still pending"
                )
            
            db.session.commit()
            
            return True
        
        except Exception as e:
            db.session.rollback()
            log.error(
                f"BulkEmailSender: Unexpected error processing job {job_id}: {e}",
                exc_info=True,
                extra={"job_id": str(job_id)}
            )
            
            # Mark job as failed
            try:
                job = db.session.query(BulkEmailJob).filter_by(id=job_id).first()
                if job:
                    job.status = BulkEmailJobStatus.FAILED.value
                    job.error_message = f"Unexpected error: {str(e)}"
                    db.session.commit()
            except Exception:
                pass
            
            return True
        
        finally:
            # Always release lock
            try:
                self.lock_manager.release_job_lock(job_id, lock_token)
                log.info(f"BulkEmailSender: Released lock for job {job_id}")
            except Exception as e:
                log.error(
                    f"BulkEmailSender: Error releasing lock for job {job_id}: {e}",
                    exc_info=True
                )
    
    @staticmethod
    def queue_job_sending(job_id: uuid.UUID, app):
        """
        Queue job sending in background thread.
        
        This method is called to start/resume email sending for a job.
        It submits the job to a thread pool executor.
        """
        from concurrent.futures import ThreadPoolExecutor
        import os
        
        # Get thread pool executor (singleton pattern)
        max_workers = int(os.getenv("EMAIL_WORKERS", "10") or "10")
        if not hasattr(BulkEmailSender, "_executor"):
            BulkEmailSender._executor = ThreadPoolExecutor(max_workers=max_workers)
        
        # Create sender instance
        emails_per_minute = int(os.getenv("BULK_EMAIL_RATE_PER_MINUTE", "10") or "10")
        emails_per_hour = int(os.getenv("BULK_EMAIL_RATE_PER_HOUR", "1000") or "1000")
        
        sender = BulkEmailSender(
            emails_per_minute=emails_per_minute,
            emails_per_hour=emails_per_hour
        )
        
        # Submit job to executor
        def send_with_context():
            with app.app_context():
                sender.send_job_emails(job_id)
        
        BulkEmailSender._executor.submit(send_with_context)
        
        current_app.logger.info(
            f"BulkEmailSender: Queued job {job_id} for sending"
        )

