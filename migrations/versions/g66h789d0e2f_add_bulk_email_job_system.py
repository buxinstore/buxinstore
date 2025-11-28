"""add bulk email job system

Revision ID: g66h789d0e2f
Revises: f55b6789c0d1
Create Date: 2025-01-XX XX:XX:XX.XXXXXX

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'g66h789d0e2f'
down_revision: Union[str, None] = 'f55b6789c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create bulk_email_job table
    op.create_table(
        'bulk_email_job',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('subject', sa.Text(), nullable=False),
        sa.Column('html_body', sa.Text(), nullable=False),
        sa.Column('from_email', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('total_recipients', sa.Integer(), nullable=True),
        sa.Column('sent_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('failed_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('skipped_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('current_progress', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('job_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('lock_token', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('lock_acquired_at', sa.DateTime(), nullable=True),
        sa.Column('lock_worker_id', sa.String(length=255), nullable=True),
        sa.Column('timeout_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for bulk_email_job
    op.create_index('ix_bulk_email_job_status', 'bulk_email_job', ['status'])
    op.create_index('ix_bulk_email_job_created_at', 'bulk_email_job', ['created_at'])
    op.create_index('ix_bulk_email_job_timeout_at', 'bulk_email_job', ['timeout_at'])
    op.create_index('idx_bulk_email_job_status_created', 'bulk_email_job', ['status', 'created_at'])
    op.create_index('idx_bulk_email_job_timeout', 'bulk_email_job', ['timeout_at'])
    
    # Create bulk_email_recipient table
    op.create_table(
        'bulk_email_recipient',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('recipient_email', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('send_attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_attempt_at', sa.DateTime(), nullable=True),
        sa.Column('next_retry_at', sa.DateTime(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('resend_email_id', sa.String(length=255), nullable=True),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['bulk_email_job.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('job_id', 'recipient_email', name='uq_job_recipient')
    )
    
    # Create indexes for bulk_email_recipient
    op.create_index('ix_bulk_email_recipient_job_id', 'bulk_email_recipient', ['job_id'])
    op.create_index('ix_bulk_email_recipient_recipient_email', 'bulk_email_recipient', ['recipient_email'])
    op.create_index('ix_bulk_email_recipient_status', 'bulk_email_recipient', ['status'])
    op.create_index('ix_bulk_email_recipient_next_retry_at', 'bulk_email_recipient', ['next_retry_at'])
    op.create_index('idx_bulk_email_recipient_job_status', 'bulk_email_recipient', ['job_id', 'status'])
    op.create_index('idx_bulk_email_recipient_retry', 'bulk_email_recipient', ['next_retry_at'])
    
    # Create bulk_email_job_lock table
    op.create_table(
        'bulk_email_job_lock',
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('worker_id', sa.String(length=255), nullable=False),
        sa.Column('acquired_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('heartbeat_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['bulk_email_job.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('job_id')
    )
    
    # Create index for bulk_email_job_lock
    op.create_index('ix_bulk_email_job_lock_expires_at', 'bulk_email_job_lock', ['expires_at'])


def downgrade() -> None:
    # Drop indexes first
    op.drop_index('ix_bulk_email_job_lock_expires_at', table_name='bulk_email_job_lock')
    op.drop_index('idx_bulk_email_recipient_retry', table_name='bulk_email_recipient')
    op.drop_index('idx_bulk_email_recipient_job_status', table_name='bulk_email_recipient')
    op.drop_index('ix_bulk_email_recipient_next_retry_at', table_name='bulk_email_recipient')
    op.drop_index('ix_bulk_email_recipient_status', table_name='bulk_email_recipient')
    op.drop_index('ix_bulk_email_recipient_recipient_email', table_name='bulk_email_recipient')
    op.drop_index('ix_bulk_email_recipient_job_id', table_name='bulk_email_recipient')
    op.drop_index('idx_bulk_email_job_timeout', table_name='bulk_email_job')
    op.drop_index('idx_bulk_email_job_status_created', table_name='bulk_email_job')
    op.drop_index('ix_bulk_email_job_timeout_at', table_name='bulk_email_job')
    op.drop_index('ix_bulk_email_job_created_at', table_name='bulk_email_job')
    op.drop_index('ix_bulk_email_job_status', table_name='bulk_email_job')
    
    # Drop tables
    op.drop_table('bulk_email_job_lock')
    op.drop_table('bulk_email_recipient')
    op.drop_table('bulk_email_job')

