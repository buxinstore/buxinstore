"""
Main orchestrator service for bulk email system.

Coordinates job creation, recipient collection, and email sending.
This is the main entry point for the bulk email system.
"""
import uuid
from typing import Optional, Tuple
from sqlalchemy.orm import Query

from flask import current_app

from app.extensions import db
from app.models.bulk_email import BulkEmailJob, BulkEmailJobStatus
from app.services.bulk_email_job_creator import BulkEmailJobCreator
from app.services.bulk_email_collector import BulkEmailRecipientCollector
from app.services.bulk_email_sender import BulkEmailSender


class BulkEmailOrchestrator:
    """
    Main orchestrator for bulk email jobs.
    
    Coordinates the three-phase pipeline:
    1. Job creation
    2. Recipient collection
    3. Email sending
    """
    
    @staticmethod
    def create_and_queue_bulk_email_job(
        subject: str,
        body: str,
        from_email: str,
        recipients_query: Query,
        metadata: Optional[dict] = None,
    ) -> Tuple[Optional[uuid.UUID], Optional[str]]:
        """
        Create a bulk email job and queue it for processing.
        
        This is the main entry point for creating bulk email jobs.
        It:
        1. Creates the job record
        2. Queues recipient collection
        3. Returns job ID for tracking
        
        Args:
            subject: Email subject line
            body: Email body text
            from_email: FROM email address
            recipients_query: SQLAlchemy query that returns users/recipients
            metadata: Optional metadata dictionary
            
        Returns:
            Tuple of (job_id, error_message)
            - If successful: (job_id, None)
            - If failed: (None, error_message)
        """
        log = current_app.logger
        
        try:
            # Phase 1: Create job
            job = BulkEmailJobCreator.create_job(
                subject=subject,
                body=body,
                from_email=from_email,
                metadata=metadata,
            )
            
            job_id = job.id
            
            log.info(
                f"BulkEmailOrchestrator: Created job {job_id}, queueing collection",
                extra={"job_id": str(job_id)}
            )
            
            # Phase 2: Queue recipient collection
            # Transition to collecting status
            BulkEmailJobCreator.transition_to_collecting(job_id)
            
            # Queue collection task
            app_obj = current_app._get_current_object()
            BulkEmailOrchestrator._queue_collection(job_id, recipients_query, app_obj)
            
            return job_id, None
        
        except Exception as e:
            log.error(
                f"BulkEmailOrchestrator: Failed to create job: {e}",
                exc_info=True
            )
            return None, str(e)
    
    @staticmethod
    def _queue_collection(job_id: uuid.UUID, query: Query, app):
        """Queue recipient collection task in background thread."""
        from concurrent.futures import ThreadPoolExecutor
        import os
        
        # Get thread pool executor
        max_workers = int(os.getenv("EMAIL_WORKERS", "10") or "10")
        if not hasattr(BulkEmailOrchestrator, "_executor"):
            BulkEmailOrchestrator._executor = ThreadPoolExecutor(max_workers=max_workers)
        
        def collect_with_context():
            with app.app_context():
                log = current_app.logger
                
                try:
                    valid_count, skipped_count = BulkEmailRecipientCollector.collect_recipients(
                        job_id=job_id,
                        query=query,
                    )
                    
                    log.info(
                        f"BulkEmailOrchestrator: Collection complete for job {job_id}: "
                        f"{valid_count} valid, {skipped_count} skipped"
                    )
                    
                    # If we have valid recipients, queue sending
                    if valid_count > 0:
                        # Reload job to get updated status
                        job = db.session.query(BulkEmailJob).filter_by(id=job_id).first()
                        if job and job.status == BulkEmailJobStatus.RUNNING.value:
                            BulkEmailSender.queue_job_sending(job_id, app)
                        else:
                            log.warning(
                                f"BulkEmailOrchestrator: Job {job_id} is not in RUNNING status after collection "
                                f"(status: {job.status if job else 'None'})"
                            )
                    else:
                        log.warning(
                            f"BulkEmailOrchestrator: No valid recipients for job {job_id}"
                        )
                
                except Exception as e:
                    log.error(
                        f"BulkEmailOrchestrator: Error in collection task for job {job_id}: {e}",
                        exc_info=True
                    )
        
        BulkEmailOrchestrator._executor.submit(collect_with_context)
        
        current_app.logger.info(
            f"BulkEmailOrchestrator: Queued collection task for job {job_id}"
        )
    
    @staticmethod
    def get_job_status(job_id: uuid.UUID) -> Optional[dict]:
        """
        Get current status of a bulk email job.
        
        Args:
            job_id: UUID of the job
            
        Returns:
            Dictionary with job status information, or None if job not found
        """
        try:
            job = db.session.query(BulkEmailJob).filter_by(id=job_id).first()
            if not job:
                return None
            
            return job.to_dict()
        
        except Exception as e:
            current_app.logger.error(
                f"BulkEmailOrchestrator: Error getting job status for {job_id}: {e}",
                exc_info=True
            )
            return None
    
    @staticmethod
    def cancel_job(job_id: uuid.UUID) -> bool:
        """
        Cancel a running bulk email job.
        
        Args:
            job_id: UUID of the job to cancel
            
        Returns:
            True if cancelled successfully, False otherwise
        """
        log = current_app.logger
        
        try:
            job = db.session.query(BulkEmailJob).filter_by(id=job_id).first()
            if not job:
                log.error(f"BulkEmailOrchestrator: Job {job_id} not found for cancellation")
                return False
            
            # Only cancel jobs that are in progress
            if job.status in [
                BulkEmailJobStatus.QUEUED.value,
                BulkEmailJobStatus.COLLECTING.value,
                BulkEmailJobStatus.RUNNING.value,
                BulkEmailJobStatus.PAUSED.value,
            ]:
                job.status = BulkEmailJobStatus.CANCELLED.value
                db.session.commit()
                
                log.info(f"BulkEmailOrchestrator: Cancelled job {job_id}")
                return True
            else:
                log.warning(
                    f"BulkEmailOrchestrator: Cannot cancel job {job_id} in status {job.status}"
                )
                return False
        
        except Exception as e:
            db.session.rollback()
            log.error(
                f"BulkEmailOrchestrator: Error cancelling job {job_id}: {e}",
                exc_info=True
            )
            return False

