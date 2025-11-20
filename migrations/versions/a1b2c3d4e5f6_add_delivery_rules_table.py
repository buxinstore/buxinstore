"""add_delivery_rules_table

Revision ID: a1b2c3d4e5f6
Revises: d22be032641d
Create Date: 2025-01-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'd22be032641d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create delivery_rules table
    op.create_table('delivery_rule',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('min_amount', sa.Float(), nullable=False),
    sa.Column('max_amount', sa.Float(), nullable=True),
    sa.Column('fee', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['product_id'], ['product.id'], ),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    # Drop delivery_rules table
    op.drop_table('delivery_rule')

