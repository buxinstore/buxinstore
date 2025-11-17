"""add_resend_email_fields

Revision ID: 0b415d5c1766
Revises: c412400b3979
Create Date: 2025-11-17 09:45:11.127649

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0b415d5c1766'
down_revision: Union[str, None] = 'c412400b3979'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add Resend email settings fields to app_settings table
    op.add_column('app_settings', sa.Column('from_email', sa.String(length=255), nullable=True))
    op.add_column('app_settings', sa.Column('contact_email', sa.String(length=255), nullable=True))
    op.add_column('app_settings', sa.Column('default_subject_prefix', sa.String(length=100), nullable=True))


def downgrade() -> None:
    # Remove Resend email settings fields from app_settings table
    op.drop_column('app_settings', 'default_subject_prefix')
    op.drop_column('app_settings', 'contact_email')
    op.drop_column('app_settings', 'from_email')
