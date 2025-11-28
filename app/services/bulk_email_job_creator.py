"""
Job creator service for bulk email system.

Handles job creation, validation, and initial queueing.
"""
import uuid
from datetime import datetime
from typing import Optional, Dict, Any

from flask import current_app, render_template

from app.extensions import db
from app.models.bulk_email import BulkEmailJob, BulkEmailJobStatus


class BulkEmailJobCreator:
    """Creates and initializes bulk email jobs."""
    
    @staticmethod
    def create_job(
        subject: str,
        body: str,
        from_email: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> BulkEmailJob:
        """
        Create a new bulk email job.
        
        Args:
            subject: Email subject line
            body: Email body text (will be rendered as HTML)
            from_email: FROM email address
            metadata: Optional metadata dictionary
            
        Returns:
            Created BulkEmailJob instance
        """
        log = current_app.logger
        
        # Validate inputs
        if not subject or not subject.strip():
            raise ValueError("Subject is required and cannot be empty")
        
        if not body or not body.strip():
            raise ValueError("Body is required and cannot be empty")
        
        if not from_email or not from_email.strip():
            raise ValueError("FROM email is required and cannot be empty")
        
        # Render HTML body from template
        try:
            html_body = render_template(
                "emails/admin_broadcast_email.html",
                subject=subject,
                body_text=body
            )
        except Exception as e:
            log.warning(f"Failed to render email template, using plain text: {e}")
            # Fallback to simple HTML
            html_body = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6;">
                <h2>{subject}</h2>
                <div style="white-space: pre-wrap;">{body}</div>
            </body>
            </html>
            """
        
        # Create job record
        job = BulkEmailJob(
            id=uuid.uuid4(),
            status=BulkEmailJobStatus.QUEUED.value,
            subject=subject.strip(),
            html_body=html_body,
            from_email=from_email.strip(),
            created_at=datetime.utcnow(),
            job_metadata=metadata or {},
        )
        
        try:
            db.session.add(job)
            db.session.commit()
            
            log.info(
                f"BulkEmailJobCreator: Created job {job.id}",
                extra={"job_id": str(job.id), "subject": subject}
            )
            
            return job
        
        except Exception as e:
            db.session.rollback()
            log.error(
                f"BulkEmailJobCreator: Failed to create job: {e}",
                exc_info=True
            )
            raise
    
    @staticmethod
    def transition_to_collecting(job_id: uuid.UUID) -> bool:
        """
        Transition job status to COLLECTING.
        
        Args:
            job_id: UUID of the job
            
        Returns:
            True if transitioned successfully, False otherwise
        """
        try:
            job = db.session.query(BulkEmailJob).filter_by(id=job_id).first()
            if not job:
                return False
            
            if job.status != BulkEmailJobStatus.QUEUED.value:
                current_app.logger.warning(
                    f"Job {job_id} is not in QUEUED status (current: {job.status})"
                )
                return False
            
            job.status = BulkEmailJobStatus.COLLECTING.value
            db.session.commit()
            
            current_app.logger.info(
                f"BulkEmailJobCreator: Job {job_id} transitioned to COLLECTING"
            )
            return True
        
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(
                f"BulkEmailJobCreator: Failed to transition job {job_id}: {e}",
                exc_info=True
            )
            return False

