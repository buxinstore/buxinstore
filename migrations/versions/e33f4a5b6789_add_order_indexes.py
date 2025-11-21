"""add_order_indexes

Revision ID: e33f4a5b6789
Revises: d22be032641d
Create Date: 2025-01-20 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e33f4a5b6789'
down_revision: Union[str, None] = 'd22be032641d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add indexes for optimized queries on Order table
    # Index on status column for filtering paid/completed orders
    op.create_index('idx_orders_status', 'order', ['status'], unique=False)
    
    # Index on created_at column for date range filtering
    op.create_index('idx_orders_created_at', 'order', ['created_at'], unique=False)
    
    # Composite index on status and created_at for combined filtering
    op.create_index('idx_orders_status_created', 'order', ['status', 'created_at'], unique=False)


def downgrade() -> None:
    # Remove indexes
    op.drop_index('idx_orders_status_created', table_name='order')
    op.drop_index('idx_orders_created_at', table_name='order')
    op.drop_index('idx_orders_status', table_name='order')

