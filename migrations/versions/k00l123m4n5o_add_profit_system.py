"""add profit system

Revision ID: k00l123m4n5o
Revises: j99k012g3h4i
Create Date: 2025-01-31 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'k00l123m4n5o'
down_revision: Union[str, None] = 'j99k012g3h4i'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create profit_rule table
    op.create_table(
        'profit_rule',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('min_price', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('max_price', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('profit_amount', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for profit_rule
    op.create_index('ix_profit_rule_is_active', 'profit_rule', ['is_active'])
    op.create_index('ix_profit_rule_priority', 'profit_rule', ['priority'])
    op.create_index('ix_profit_rule_min_price', 'profit_rule', ['min_price'])
    op.create_index('ix_profit_rule_max_price', 'profit_rule', ['max_price'])
    
    # Add profit fields to order_item table
    op.add_column('order_item', sa.Column('base_price', sa.Float(), nullable=True))
    op.add_column('order_item', sa.Column('profit_amount', sa.Float(), nullable=True))
    op.add_column('order_item', sa.Column('profit_rule_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_order_item_profit_rule', 'order_item', 'profit_rule', ['profit_rule_id'], ['id'])
    
    # Add profit fields to order table
    op.add_column('order', sa.Column('total_profit_gmd', sa.Float(), nullable=True))
    op.add_column('order', sa.Column('total_revenue_gmd', sa.Float(), nullable=True))
    
    # Insert some default profit rules
    op.execute("""
        INSERT INTO profit_rule (min_price, max_price, profit_amount, priority, is_active, note, created_at, updated_at)
        VALUES 
        (0, 50, 50, 1, true, 'Default rule for products 0-50 GMD', NOW(), NOW()),
        (51, 200, 100, 1, true, 'Default rule for products 51-200 GMD', NOW(), NOW()),
        (201, NULL, 200, 1, true, 'Default rule for products above 200 GMD', NOW(), NOW())
    """)


def downgrade() -> None:
    # Remove profit fields from order table
    op.drop_column('order', 'total_revenue_gmd')
    op.drop_column('order', 'total_profit_gmd')
    
    # Remove profit fields from order_item table
    op.drop_constraint('fk_order_item_profit_rule', 'order_item', type_='foreignkey')
    op.drop_column('order_item', 'profit_rule_id')
    op.drop_column('order_item', 'profit_amount')
    op.drop_column('order_item', 'base_price')
    
    # Drop profit_rule table
    op.drop_index('ix_profit_rule_max_price', table_name='profit_rule')
    op.drop_index('ix_profit_rule_min_price', table_name='profit_rule')
    op.drop_index('ix_profit_rule_priority', table_name='profit_rule')
    op.drop_index('ix_profit_rule_is_active', table_name='profit_rule')
    op.drop_table('profit_rule')

