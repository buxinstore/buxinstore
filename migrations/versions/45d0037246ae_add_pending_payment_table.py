"""add_pending_payment_table

Revision ID: 45d0037246ae
Revises: f55b6789c0d1
Create Date: 2025-01-18 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '45d0037246ae'
down_revision: Union[str, None] = 'f55b6789c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create pending_payments table
    op.create_table('pending_payments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('amount', sa.Float(), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='waiting', nullable=False),
    sa.Column('modempay_transaction_id', sa.String(length=100), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.Column('payment_method', sa.String(length=50), nullable=True),
    sa.Column('delivery_address', sa.Text(), nullable=True),
    sa.Column('customer_name', sa.String(length=255), nullable=True),
    sa.Column('customer_phone', sa.String(length=50), nullable=True),
    sa.Column('customer_email', sa.String(length=255), nullable=True),
    sa.Column('shipping_price', sa.Float(), nullable=True),
    sa.Column('total_cost', sa.Float(), nullable=True),
    sa.Column('location', sa.String(length=50), nullable=True),
    sa.Column('cart_items_json', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    # Add index on status for faster queries
    op.create_index('ix_pending_payments_status', 'pending_payments', ['status'])
    op.create_index('ix_pending_payments_user_id', 'pending_payments', ['user_id'])
    op.create_index('ix_pending_payments_modempay_transaction_id', 'pending_payments', ['modempay_transaction_id'])
    
    # Add pending_payment_id column to payments table (nullable for backward compatibility)
    op.add_column('payments', sa.Column('pending_payment_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_payments_pending_payment_id', 'payments', 'pending_payments', ['pending_payment_id'], ['id'])
    op.create_index('ix_payments_pending_payment_id', 'payments', ['pending_payment_id'])
    
    # Make order_id nullable in payments table (for pending payments that haven't been converted yet)
    op.alter_column('payments', 'order_id', nullable=True)


def downgrade() -> None:
    # Drop indexes and foreign key for payments table
    op.drop_index('ix_payments_pending_payment_id', table_name='payments')
    op.drop_constraint('fk_payments_pending_payment_id', 'payments', type_='foreignkey')
    op.drop_column('payments', 'pending_payment_id')
    
    # Revert order_id to NOT NULL (may fail if there are NULL values)
    op.alter_column('payments', 'order_id', nullable=False)
    
    # Drop indexes
    op.drop_index('ix_pending_payments_modempay_transaction_id', table_name='pending_payments')
    op.drop_index('ix_pending_payments_user_id', table_name='pending_payments')
    op.drop_index('ix_pending_payments_status', table_name='pending_payments')
    # Drop table
    op.drop_table('pending_payments')

