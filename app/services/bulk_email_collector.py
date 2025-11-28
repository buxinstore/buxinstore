"""
Recipient collector service for bulk email system.

Streams recipients from database query, validates emails,
deduplicates, and creates recipient records.
"""
import uuid
from typing import Set, Optional
from datetime import datetime

from flask import current_app
from sqlalchemy.orm import Query

from app.extensions import db
from app.models.bulk_email import (
    BulkEmailJob,
    BulkEmailJobStatus,
    BulkEmailRecipient,
    BulkEmailRecipientStatus,
)
from app.utils.bulk_email_validator import strict_validate_email, normalize_email


class BulkEmailRecipientCollector:
    """Collects and validates recipient emails from database queries."""
    
    BATCH_SIZE = 100  # Process users in batches
    
    @staticmethod
    def collect_recipients(
        job_id: uuid.UUID,
        query: Query,
    ) -> tuple[int, int]:
        """
        Collect recipients from a SQLAlchemy query.
        
        Streams results in batches, validates emails, deduplicates,
        and creates BulkEmailRecipient records.
        
        Args:
            job_id: UUID of the job
            query: SQLAlchemy query object (should return User-like objects with email attribute)
            
        Returns:
            Tuple of (valid_count, skipped_count)
        """
        log = current_app.logger
        
        try:
            # Load job
            job = db.session.query(BulkEmailJob).filter_by(id=job_id).first()
            if not job:
                log.error(f"BulkEmailRecipientCollector: Job {job_id} not found")
                return 0, 0
            
            # Verify job is in correct state
            if job.status != BulkEmailJobStatus.COLLECTING.value:
                log.warning(
                    f"BulkEmailRecipientCollector: Job {job_id} is not in COLLECTING status (current: {job.status})"
                )
                return 0, 0
            
            log.info(f"BulkEmailRecipientCollector: Starting collection for job {job_id}")
            
            # Track collected emails for deduplication
            collected_emails: Set[str] = set()
            valid_count = 0
            skipped_count = 0
            
            # Stream query results in batches
            if hasattr(query, 'yield_per'):
                # SQLAlchemy query - use yield_per for efficient streaming
                iterator = query.yield_per(BulkEmailRecipientCollector.BATCH_SIZE)
                
                for user in iterator:
                    try:
                        # Extract email
                        email = getattr(user, 'email', None)
                        
                        if not email:
                            skipped_count += 1
                            continue
                        
                        # Validate email
                        is_valid, error_msg = strict_validate_email(email)
                        if not is_valid:
                            log.debug(
                                f"BulkEmailRecipientCollector: Skipping invalid email {email}: {error_msg}"
                            )
                            skipped_count += 1
                            continue
                        
                        # Normalize email (lowercase, etc.)
                        normalized_email = normalize_email(email)
                        if not normalized_email:
                            skipped_count += 1
                            continue
                        
                        # Check for duplicates within this job
                        if normalized_email in collected_emails:
                            log.debug(
                                f"BulkEmailRecipientCollector: Skipping duplicate email {normalized_email}"
                            )
                            skipped_count += 1
                            continue
                        
                        # Check if recipient already exists (safety check)
                        existing = db.session.query(BulkEmailRecipient).filter_by(
                            job_id=job_id,
                            recipient_email=normalized_email
                        ).first()
                        
                        if existing:
                            log.debug(
                                f"BulkEmailRecipientCollector: Recipient {normalized_email} already exists"
                            )
                            collected_emails.add(normalized_email)
                            valid_count += 1
                            continue
                        
                        # Create recipient record
                        recipient = BulkEmailRecipient(
                            id=uuid.uuid4(),
                            job_id=job_id,
                            recipient_email=normalized_email,
                            status=BulkEmailRecipientStatus.PENDING.value,
                            created_at=datetime.utcnow(),
                        )
                        
                        db.session.add(recipient)
                        collected_emails.add(normalized_email)
                        valid_count += 1
                        
                        # Commit in batches to avoid long transactions
                        if valid_count % BulkEmailRecipientCollector.BATCH_SIZE == 0:
                            db.session.commit()
                            log.debug(
                                f"BulkEmailRecipientCollector: Collected {valid_count} recipients so far"
                            )
                    
                    except Exception as e:
                        log.warning(
                            f"BulkEmailRecipientCollector: Error processing user record: {e}",
                            exc_info=True
                        )
                        skipped_count += 1
                        continue
            
            else:
                # Fallback for non-SQLAlchemy iterables
                log.warning("BulkEmailRecipientCollector: Query doesn't support yield_per, using standard iteration")
                
                for item in query:
                    try:
                        email = item if isinstance(item, str) else getattr(item, 'email', None)
                        
                        if not email:
                            skipped_count += 1
                            continue
                        
                        is_valid, error_msg = strict_validate_email(email)
                        if not is_valid:
                            skipped_count += 1
                            continue
                        
                        normalized_email = normalize_email(email)
                        if not normalized_email or normalized_email in collected_emails:
                            skipped_count += 1
                            continue
                        
                        existing = db.session.query(BulkEmailRecipient).filter_by(
                            job_id=job_id,
                            recipient_email=normalized_email
                        ).first()
                        
                        if existing:
                            collected_emails.add(normalized_email)
                            valid_count += 1
                            continue
                        
                        recipient = BulkEmailRecipient(
                            id=uuid.uuid4(),
                            job_id=job_id,
                            recipient_email=normalized_email,
                            status=BulkEmailRecipientStatus.PENDING.value,
                            created_at=datetime.utcnow(),
                        )
                        
                        db.session.add(recipient)
                        collected_emails.add(normalized_email)
                        valid_count += 1
                        
                        if valid_count % BulkEmailRecipientCollector.BATCH_SIZE == 0:
                            db.session.commit()
                    
                    except Exception as e:
                        log.warning(
                            f"BulkEmailRecipientCollector: Error processing item: {e}",
                            exc_info=True
                        )
                        skipped_count += 1
                        continue
            
            # Final commit
            db.session.commit()
            
            # Update job with totals
            job.total_recipients = valid_count
            job.skipped_count = skipped_count
            job.status = BulkEmailJobStatus.RUNNING.value if valid_count > 0 else BulkEmailJobStatus.COMPLETED.value
            job.started_at = datetime.utcnow()
            
            if valid_count == 0:
                job.error_message = "No valid recipients found"
            
            db.session.commit()
            
            log.info(
                f"BulkEmailRecipientCollector: Collection complete for job {job_id}: "
                f"{valid_count} valid, {skipped_count} skipped"
            )
            
            return valid_count, skipped_count
        
        except Exception as e:
            db.session.rollback()
            log.error(
                f"BulkEmailRecipientCollector: Failed to collect recipients for job {job_id}: {e}",
                exc_info=True
            )
            
            # Mark job as failed
            try:
                job = db.session.query(BulkEmailJob).filter_by(id=job_id).first()
                if job:
                    job.status = BulkEmailJobStatus.FAILED.value
                    job.error_message = f"Collection failed: {str(e)}"
                    db.session.commit()
            except Exception:
                pass
            
            return 0, 0

