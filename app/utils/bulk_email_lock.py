"""
Distributed lock manager for bulk email jobs.

Prevents concurrent execution of the same job across multiple workers
and enables job recovery after crashes.
"""
import uuid
import socket
import threading
from typing import Optional
from datetime import datetime, timedelta

from app.extensions import db
from app.models.bulk_email import BulkEmailJob, BulkEmailJobLock


class DistributedLockManager:
    """
    Manages distributed locks for bulk email jobs.
    
    Provides both job-level locks (in BulkEmailJob model) and
    additional table-level locks for extra safety.
    """
    
    def __init__(self, lock_timeout_minutes: int = 60, heartbeat_interval_minutes: int = 5):
        """
        Initialize lock manager.
        
        Args:
            lock_timeout_minutes: How long locks are valid before auto-expiry
            heartbeat_interval_minutes: How often to update heartbeat
        """
        self.lock_timeout_minutes = lock_timeout_minutes
        self.heartbeat_interval_minutes = heartbeat_interval_minutes
        self.worker_id = self._generate_worker_id()
        self._local_locks = {}  # Track locally acquired locks
        self._lock = threading.Lock()
    
    def _generate_worker_id(self) -> str:
        """Generate unique worker ID for this process."""
        hostname = socket.gethostname()
        process_id = str(uuid.uuid4())[:8]
        return f"{hostname}-{process_id}"
    
    def acquire_job_lock(self, job_id: uuid.UUID) -> Optional[uuid.UUID]:
        """
        Acquire a distributed lock on a job.
        
        Args:
            job_id: UUID of the job to lock
            
        Returns:
            Lock token if successful, None if already locked
        """
        try:
            # Load job
            job = db.session.query(BulkEmailJob).filter_by(id=job_id).first()
            if not job:
                return None
            
            # Try to acquire lock at job level
            lock_token = job.acquire_lock(self.worker_id, self.lock_timeout_minutes)
            if not lock_token:
                return None
            
            # Also create lock table entry for extra safety
            lock_entry = db.session.query(BulkEmailJobLock).filter_by(job_id=job_id).first()
            if lock_entry:
                # Check if expired
                if lock_entry.is_expired():
                    # Remove expired lock
                    db.session.delete(lock_entry)
                    db.session.flush()
                else:
                    # Lock is still valid
                    db.session.commit()
                    return None
            
            # Create new lock entry
            now = datetime.utcnow()
            lock_entry = BulkEmailJobLock(
                job_id=job_id,
                worker_id=self.worker_id,
                acquired_at=now,
                expires_at=now + timedelta(minutes=self.lock_timeout_minutes),
                heartbeat_at=now,
            )
            db.session.add(lock_entry)
            
            # Track locally
            with self._lock:
                self._local_locks[job_id] = {
                    "lock_token": lock_token,
                    "expires_at": lock_entry.expires_at,
                }
            
            db.session.commit()
            return lock_token
        
        except Exception:
            db.session.rollback()
            return None
    
    def release_job_lock(self, job_id: uuid.UUID, lock_token: uuid.UUID) -> bool:
        """
        Release a distributed lock on a job.
        
        Args:
            job_id: UUID of the job
            lock_token: Token received from acquire_job_lock
            
        Returns:
            True if released successfully, False otherwise
        """
        try:
            # Load job
            job = db.session.query(BulkEmailJob).filter_by(id=job_id).first()
            if not job:
                return False
            
            # Release job-level lock
            if not job.release_lock(lock_token):
                return False
            
            # Remove lock table entry
            lock_entry = db.session.query(BulkEmailJobLock).filter_by(job_id=job_id).first()
            if lock_entry:
                db.session.delete(lock_entry)
            
            # Remove from local tracking
            with self._lock:
                self._local_locks.pop(job_id, None)
            
            db.session.commit()
            return True
        
        except Exception:
            db.session.rollback()
            return False
    
    def extend_job_lock(self, job_id: uuid.UUID, lock_token: uuid.UUID) -> bool:
        """
        Extend the timeout of an existing lock.
        
        Args:
            job_id: UUID of the job
            lock_token: Token received from acquire_job_lock
            
        Returns:
            True if extended successfully, False otherwise
        """
        try:
            # Load job
            job = db.session.query(BulkEmailJob).filter_by(id=job_id).first()
            if not job:
                return False
            
            # Extend job-level lock
            if not job.extend_lock(lock_token, self.lock_timeout_minutes):
                return False
            
            # Update lock table entry
            lock_entry = db.session.query(BulkEmailJobLock).filter_by(job_id=job_id).first()
            if lock_entry:
                lock_entry.expires_at = datetime.utcnow() + timedelta(minutes=self.lock_timeout_minutes)
                lock_entry.update_heartbeat()
            
            # Update local tracking
            with self._lock:
                if job_id in self._local_locks:
                    self._local_locks[job_id]["expires_at"] = lock_entry.expires_at if lock_entry else datetime.utcnow()
            
            db.session.commit()
            return True
        
        except Exception:
            db.session.rollback()
            return False
    
    def cleanup_expired_locks(self):
        """Clean up expired locks from the database."""
        try:
            now = datetime.utcnow()
            
            # Find expired locks
            expired_jobs = db.session.query(BulkEmailJob).filter(
                BulkEmailJob.timeout_at < now,
                BulkEmailJob.lock_token.isnot(None)
            ).all()
            
            for job in expired_jobs:
                job.release_lock(job.lock_token)
            
            # Clean up lock table entries
            expired_locks = db.session.query(BulkEmailJobLock).filter(
                BulkEmailJobLock.expires_at < now
            ).all()
            
            for lock_entry in expired_locks:
                db.session.delete(lock_entry)
            
            db.session.commit()
        
        except Exception:
            db.session.rollback()
    
    def get_worker_id(self) -> str:
        """Get the worker ID for this process."""
        return self.worker_id

