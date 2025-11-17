"""add_contact_receiver_fields

Revision ID: c412400b3979
Revises: 77b5203fa3eb
Create Date: 2025-11-17 09:17:43.110274

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c412400b3979'
down_revision: Union[str, None] = '77b5203fa3eb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add contact receiver fields to app_settings table
    op.add_column('app_settings', sa.Column('contact_whatsapp_receiver', sa.String(length=50), nullable=True))
    op.add_column('app_settings', sa.Column('contact_email_receiver', sa.String(length=255), nullable=True))


def downgrade() -> None:
    # Remove contact receiver fields from app_settings table
    op.drop_column('app_settings', 'contact_email_receiver')
    op.drop_column('app_settings', 'contact_whatsapp_receiver')
