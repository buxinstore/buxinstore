"""add shipping rules system

Revision ID: i88j901f2g4h
Revises: g66h789d0e2f
Create Date: 2025-11-29 20:07:52.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'i88j901f2g4h'
down_revision: Union[str, None] = ('g66h789d0e2f', 'seed_world_countries_001')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create shipping_rule table
    op.create_table('shipping_rule',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('rule_type', sa.String(length=20), nullable=False, server_default='country'),
        sa.Column('country_id', sa.Integer(), nullable=True),
        sa.Column('min_weight', sa.Numeric(10, 6), nullable=False),
        sa.Column('max_weight', sa.Numeric(10, 6), nullable=False),
        sa.Column('price_gmd', sa.Numeric(10, 2), nullable=False),
        sa.Column('delivery_time', sa.String(length=100), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('status', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['country_id'], ['country.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indices for efficient queries
    op.create_index('ix_shipping_rule_country_id', 'shipping_rule', ['country_id'])
    op.create_index('ix_shipping_rule_status', 'shipping_rule', ['status'])
    op.create_index('ix_shipping_rule_rule_type', 'shipping_rule', ['rule_type'])
    op.create_index('ix_shipping_rule_priority', 'shipping_rule', ['priority'])
    op.create_index('ix_shipping_rule_weight_range', 'shipping_rule', ['min_weight', 'max_weight'])
    
    # Add weight_kg column to product table
    op.add_column('product', sa.Column('weight_kg', sa.Numeric(10, 6), nullable=True))
    
    # Add shipping rule fields to order table
    op.add_column('order', sa.Column('shipping_rule_id', sa.Integer(), nullable=True))
    op.add_column('order', sa.Column('shipping_delivery_estimate', sa.String(length=100), nullable=True))
    op.add_column('order', sa.Column('shipping_display_currency', sa.String(length=10), nullable=True))
    
    # Add foreign key constraint for shipping_rule_id
    op.create_foreign_key('fk_order_shipping_rule', 'order', 'shipping_rule', ['shipping_rule_id'], ['id'])
    
    # Create index for shipping_rule_id in order table
    op.create_index('ix_order_shipping_rule_id', 'order', ['shipping_rule_id'])
    
    # Seed sample global shipping rule
    op.execute("""
        INSERT INTO shipping_rule (rule_type, country_id, min_weight, max_weight, price_gmd, delivery_time, priority, status, note, created_at, updated_at)
        VALUES ('global', NULL, 0.0, 10.0, 500.00, '7-30 days', 0, true, 'Default global shipping rule for orders up to 10kg', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """)


def downgrade() -> None:
    # Drop indices
    op.drop_index('ix_pending_payments_shipping_rule_id', table_name='pending_payments')
    op.drop_index('ix_order_shipping_rule_id', table_name='order')
    op.drop_index('ix_shipping_rule_weight_range', table_name='shipping_rule')
    op.drop_index('ix_shipping_rule_priority', table_name='shipping_rule')
    op.drop_index('ix_shipping_rule_rule_type', table_name='shipping_rule')
    op.drop_index('ix_shipping_rule_status', table_name='shipping_rule')
    op.drop_index('ix_shipping_rule_country_id', table_name='shipping_rule')
    
    # Drop foreign key constraints
    op.drop_constraint('fk_pending_payment_shipping_rule', 'pending_payments', type_='foreignkey')
    op.drop_constraint('fk_order_shipping_rule', 'order', type_='foreignkey')
    
    # Drop columns from pending_payments table
    op.drop_column('pending_payments', 'shipping_display_currency')
    op.drop_column('pending_payments', 'shipping_delivery_estimate')
    op.drop_column('pending_payments', 'shipping_rule_id')
    
    # Drop columns from order table
    op.drop_column('order', 'shipping_display_currency')
    op.drop_column('order', 'shipping_delivery_estimate')
    op.drop_column('order', 'shipping_rule_id')
    
    # Drop column from product table
    op.drop_column('product', 'weight_kg')
    
    # Drop shipping_rule table
    op.drop_table('shipping_rule')

