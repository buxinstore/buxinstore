"""add_floating_contact_widget_fields

Revision ID: f44a5b6789c0
Revises: c775b243d625
Create Date: 2025-11-22 03:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f44a5b6789c0'
down_revision: Union[str, None] = 'c775b243d625'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add floating contact widget fields to app_settings table
    op.add_column('app_settings', sa.Column('floating_whatsapp_number', sa.String(length=50), nullable=True))
    op.add_column('app_settings', sa.Column('floating_support_email', sa.String(length=255), nullable=True))
    op.add_column('app_settings', sa.Column('floating_email_subject', sa.String(length=255), nullable=True, server_default='Support Request'))
    op.add_column('app_settings', sa.Column('floating_email_body', sa.Text(), nullable=True, server_default='Hello, I need help with ...'))


def downgrade() -> None:
    # Remove floating contact widget fields from app_settings table
    op.drop_column('app_settings', 'floating_email_body')
    op.drop_column('app_settings', 'floating_email_subject')
    op.drop_column('app_settings', 'floating_support_email')
    op.drop_column('app_settings', 'floating_whatsapp_number')

