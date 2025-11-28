"""
Database models for the persistent bulk email job system.

These models provide full job persistence, progress tracking, and recovery
capabilities for the bulk email sending system.
"""
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from sqlalchemy import Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.extensions import db


class BulkEmailJobStatus(Enum):
    """Status enumeration for bulk email jobs."""
    QUEUED = "queued"
    COLLECTING = "collecting"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BulkEmailRecipientStatus(Enum):
    """Status enumeration for individual email recipients."""
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class BulkEmailJob(db.Model):
    """
    Main job record for bulk email sending.
    
    Tracks the overall job status, progress, and metadata.
    All state is persisted in the database for recovery after restarts.
    """
    __tablename__ = "bulk_email_job"
    
    # Primary key
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Job status
    status = db.Column(
        db.String(20),
        nullable=False,
        default=BulkEmailJobStatus.QUEUED.value,
        index=True
    )
    
    # Email content
    subject = db.Column(db.Text, nullable=False)
    html_body = db.Column(db.Text, nullable=False)
    from_email = db.Column(db.String(255), nullable=False)
    
    # Timestamps
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    
    # Progress tracking
    total_recipients = db.Column(db.Integer, nullable=True)
    sent_count = db.Column(db.Integer, nullable=False, default=0)
    failed_count = db.Column(db.Integer, nullable=False, default=0)
    skipped_count = db.Column(db.Integer, nullable=False, default=0)
    current_progress = db.Column(db.Integer, nullable=False, default=0)
    
    # Error handling
    error_message = db.Column(db.Text, nullable=True)
    
    # Metadata (JSON) - using job_metadata for both attribute and column name (metadata is SQLAlchemy reserved)
    job_metadata = db.Column(JSONB, nullable=True)
    
    # Distributed locking
    lock_token = db.Column(UUID(as_uuid=True), nullable=True)
    lock_acquired_at = db.Column(db.DateTime, nullable=True)
    lock_worker_id = db.Column(db.String(255), nullable=True)
    timeout_at = db.Column(db.DateTime, nullable=True, index=True)
    
    # Relationships
    recipients = db.relationship(
        "BulkEmailRecipient",
        backref="job",
        lazy="dynamic",
        cascade="all, delete-orphan"
    )
    
    # Indexes for performance
    __table_args__ = (
        Index("idx_bulk_email_job_status_created", "status", "created_at"),
        Index("idx_bulk_email_job_timeout", "timeout_at"),
    )
    
    def __repr__(self):
        return f"<BulkEmailJob {self.id} {self.status}>"
    
    def is_locked(self) -> bool:
        """Check if job is currently locked by a worker."""
        if not self.lock_token or not self.lock_acquired_at:
            return False
        
        # Check if lock has expired
        if self.timeout_at and datetime.utcnow() > self.timeout_at:
            return False
        
        return True
    
    def acquire_lock(self, worker_id: str, timeout_minutes: int = 60) -> Optional[uuid.UUID]:
        """
        Acquire a lock on this job for distributed execution.
        
        Returns lock_token if successful, None if already locked.
        """
        if self.is_locked():
            return None
        
        lock_token = uuid.uuid4()
        now = datetime.utcnow()
        
        self.lock_token = lock_token
        self.lock_acquired_at = now
        self.lock_worker_id = worker_id
        self.timeout_at = now + timedelta(minutes=timeout_minutes)
        
        return lock_token
    
    def release_lock(self, lock_token: uuid.UUID) -> bool:
        """Release the lock if token matches."""
        if self.lock_token != lock_token:
            return False
        
        self.lock_token = None
        self.lock_acquired_at = None
        self.lock_worker_id = None
        self.timeout_at = None
        
        return True
    
    def extend_lock(self, lock_token: uuid.UUID, timeout_minutes: int = 60) -> bool:
        """Extend the lock timeout if token matches."""
        if self.lock_token != lock_token:
            return False
        
        self.timeout_at = datetime.utcnow() + timedelta(minutes=timeout_minutes)
        return True
    
    def to_dict(self) -> dict:
        """Convert job to dictionary for API responses."""
        return {
            "id": str(self.id),
            "status": self.status,
            "subject": self.subject,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "total_recipients": self.total_recipients,
            "sent_count": self.sent_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "current_progress": self.current_progress,
            "error_message": self.error_message,
        }


class BulkEmailRecipient(db.Model):
    """
    Individual recipient record for bulk email jobs.
    
    Tracks the status of each email send attempt with retry logic.
    """
    __tablename__ = "bulk_email_recipient"
    
    # Primary key
    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Foreign key to job
    job_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("bulk_email_job.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    # Recipient email
    recipient_email = db.Column(db.String(255), nullable=False, index=True)
    
    # Status
    status = db.Column(
        db.String(20),
        nullable=False,
        default=BulkEmailRecipientStatus.PENDING.value,
        index=True
    )
    
    # Retry tracking
    send_attempts = db.Column(db.Integer, nullable=False, default=0)
    last_attempt_at = db.Column(db.DateTime, nullable=True)
    next_retry_at = db.Column(db.DateTime, nullable=True, index=True)
    
    # Error handling
    error_message = db.Column(db.Text, nullable=True)
    
    # Resend API response
    resend_email_id = db.Column(db.String(255), nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    # Indexes for performance
    __table_args__ = (
        Index("idx_bulk_email_recipient_job_status", "job_id", "status"),
        Index("idx_bulk_email_recipient_retry", "next_retry_at"),
        UniqueConstraint("job_id", "recipient_email", name="uq_job_recipient"),
    )
    
    def __repr__(self):
        return f"<BulkEmailRecipient {self.recipient_email} {self.status}>"


class BulkEmailJobLock(db.Model):
    """
    Distributed lock table for bulk email jobs.
    
    Provides additional locking mechanism beyond job-level locks
    for extra safety in multi-worker environments.
    """
    __tablename__ = "bulk_email_job_lock"
    
    # Primary key (job_id)
    job_id = db.Column(
        UUID(as_uuid=True),
        db.ForeignKey("bulk_email_job.id", ondelete="CASCADE"),
        primary_key=True
    )
    
    # Lock metadata
    worker_id = db.Column(db.String(255), nullable=False)
    acquired_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    heartbeat_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    def __repr__(self):
        return f"<BulkEmailJobLock {self.job_id} worker={self.worker_id}>"
    
    def is_expired(self) -> bool:
        """Check if lock has expired."""
        return datetime.utcnow() > self.expires_at
    
    def update_heartbeat(self):
        """Update heartbeat timestamp."""
        self.heartbeat_at = datetime.utcnow()

