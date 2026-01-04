"""add customer photo and email fields

Revision ID: q00r123s4t5u
Revises: p99q012r3s4t
Create Date: 2026-01-04 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'q00r123s4t5u'
down_revision: Union[str, None] = 'p99q012r3s4t'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add customer_photo_url to pending_payments table
    op.add_column('pending_payments', sa.Column('customer_photo_url', sa.String(length=500), nullable=True))
    
    # Add customer_email to pending_payments table (if not exists)
    try:
        op.add_column('pending_payments', sa.Column('customer_email', sa.String(length=255), nullable=True))
    except Exception:
        # Column might already exist
        pass
    
    # Add customer_photo_url to order table
    op.add_column('order', sa.Column('customer_photo_url', sa.String(length=500), nullable=True))
    
    # Add customer_email to order table (if not exists)
    try:
        op.add_column('order', sa.Column('customer_email', sa.String(length=255), nullable=True))
    except Exception:
        # Column might already exist
        pass


def downgrade() -> None:
    # Remove customer_photo_url from order table
    op.drop_column('order', 'customer_photo_url')
    
    # Remove customer_email from order table
    try:
        op.drop_column('order', 'customer_email')
    except Exception:
        pass
    
    # Remove customer_photo_url from pending_payments table
    op.drop_column('pending_payments', 'customer_photo_url')
    
    # Remove customer_email from pending_payments table
    try:
        op.drop_column('pending_payments', 'customer_email')
    except Exception:
        pass

