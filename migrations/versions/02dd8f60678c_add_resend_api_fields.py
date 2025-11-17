"""add_resend_api_fields

Revision ID: 02dd8f60678c
Revises: 0b415d5c1766
Create Date: 2025-11-17 10:16:25.118405

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '02dd8f60678c'
down_revision: Union[str, None] = '0b415d5c1766'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new Resend API fields to app_settings table
    op.add_column('app_settings', sa.Column('resend_api_key', sa.String(length=255), nullable=True))
    op.add_column('app_settings', sa.Column('resend_from_email', sa.String(length=255), nullable=True))
    op.add_column('app_settings', sa.Column('resend_default_recipient', sa.String(length=255), nullable=True))
    op.add_column('app_settings', sa.Column('resend_enabled', sa.Boolean(), nullable=True, server_default='true'))
    
    # Migrate data from old from_email to resend_from_email if it exists
    # Note: This assumes from_email column exists (from previous migration)
    op.execute("""
        UPDATE app_settings 
        SET resend_from_email = from_email 
        WHERE from_email IS NOT NULL AND resend_from_email IS NULL
    """)


def downgrade() -> None:
    # Remove Resend API fields from app_settings table
    op.drop_column('app_settings', 'resend_enabled')
    op.drop_column('app_settings', 'resend_default_recipient')
    op.drop_column('app_settings', 'resend_from_email')
    op.drop_column('app_settings', 'resend_api_key')
