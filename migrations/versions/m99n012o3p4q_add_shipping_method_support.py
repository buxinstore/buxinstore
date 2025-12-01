"""add shipping method support

Revision ID: m99n012o3p4q
Revises: k00l123m4n5o
Create Date: 2025-01-20 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'm99n012o3p4q'
down_revision: Union[str, None] = 'k00l123m4n5o'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add shipping_method column to shipping_rule table
    op.add_column('shipping_rule', sa.Column('shipping_method', sa.String(length=20), nullable=True))
    
    # Add shipping_method column to order table
    op.add_column('order', sa.Column('shipping_method', sa.String(length=20), nullable=True))
    
    # Add shipping_method column to pending_payments table
    op.add_column('pending_payments', sa.Column('shipping_method', sa.String(length=20), nullable=True))
    
    # Create index for shipping_method in shipping_rule table for efficient queries
    op.create_index('ix_shipping_rule_shipping_method', 'shipping_rule', ['shipping_method'])


def downgrade() -> None:
    # Drop index
    op.drop_index('ix_shipping_rule_shipping_method', table_name='shipping_rule')
    
    # Drop columns
    op.drop_column('pending_payments', 'shipping_method')
    op.drop_column('order', 'shipping_method')
    op.drop_column('shipping_rule', 'shipping_method')

