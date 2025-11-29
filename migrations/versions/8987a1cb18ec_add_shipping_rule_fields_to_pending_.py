"""add_shipping_rule_fields_to_pending_payments

Revision ID: 8987a1cb18ec
Revises: i88j901f2g4h
Create Date: 2025-11-29 21:11:48.323991

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8987a1cb18ec'
down_revision: Union[str, None] = 'i88j901f2g4h'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add shipping rule fields to pending_payments table
    op.add_column('pending_payments', sa.Column('shipping_rule_id', sa.Integer(), nullable=True))
    op.add_column('pending_payments', sa.Column('shipping_delivery_estimate', sa.String(length=100), nullable=True))
    op.add_column('pending_payments', sa.Column('shipping_display_currency', sa.String(length=10), nullable=True))
    
    # Add foreign key constraint for shipping_rule_id
    op.create_foreign_key('fk_pending_payment_shipping_rule', 'pending_payments', 'shipping_rule', ['shipping_rule_id'], ['id'])
    
    # Create index for shipping_rule_id in pending_payments table
    op.create_index('ix_pending_payments_shipping_rule_id', 'pending_payments', ['shipping_rule_id'])


def downgrade() -> None:
    # Drop index
    op.drop_index('ix_pending_payments_shipping_rule_id', table_name='pending_payments')
    
    # Drop foreign key constraint
    op.drop_constraint('fk_pending_payment_shipping_rule', 'pending_payments', type_='foreignkey')
    
    # Drop columns from pending_payments table
    op.drop_column('pending_payments', 'shipping_display_currency')
    op.drop_column('pending_payments', 'shipping_delivery_estimate')
    op.drop_column('pending_payments', 'shipping_rule_id')
