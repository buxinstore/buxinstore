"""add_shipping_price_to_product

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2025-01-20 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add shipping_price column to product table
    op.add_column('product', sa.Column('shipping_price', sa.Float(), nullable=True))


def downgrade() -> None:
    # Remove shipping_price column from product table
    op.drop_column('product', 'shipping_price')

