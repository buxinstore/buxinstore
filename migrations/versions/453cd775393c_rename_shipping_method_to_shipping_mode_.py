"""rename shipping_method to shipping_mode_key

Revision ID: 453cd775393c
Revises: n00o123p4q5r
Create Date: 2025-12-02 12:54:30.899983

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '453cd775393c'
down_revision: Union[str, None] = 'n00o123p4q5r'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename shipping_method to shipping_mode_key in order table
    op.alter_column('order', 'shipping_method',
                    new_column_name='shipping_mode_key',
                    existing_type=sa.String(length=20),
                    existing_nullable=True)
    
    # Rename shipping_method to shipping_mode_key in pending_payments table
    op.alter_column('pending_payments', 'shipping_method',
                    new_column_name='shipping_mode_key',
                    existing_type=sa.String(length=20),
                    existing_nullable=True)
    
    # Rename shipping_method to shipping_mode_key in shipping_rule table (LegacyShippingRule)
    op.alter_column('shipping_rule', 'shipping_method',
                    new_column_name='shipping_mode_key',
                    existing_type=sa.String(length=20),
                    existing_nullable=True)
    
    # Rename the index if it exists (PostgreSQL)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_indexes 
                WHERE indexname = 'ix_shipping_rule_shipping_method' 
                AND tablename = 'shipping_rule'
            ) THEN
                ALTER INDEX ix_shipping_rule_shipping_method 
                RENAME TO ix_shipping_rule_shipping_mode_key;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    # Rename shipping_mode_key back to shipping_method in order table
    op.alter_column('order', 'shipping_mode_key',
                    new_column_name='shipping_method',
                    existing_type=sa.String(length=20),
                    existing_nullable=True)
    
    # Rename shipping_mode_key back to shipping_method in pending_payments table
    op.alter_column('pending_payments', 'shipping_mode_key',
                    new_column_name='shipping_method',
                    existing_type=sa.String(length=20),
                    existing_nullable=True)
    
    # Rename shipping_mode_key back to shipping_method in shipping_rule table
    op.alter_column('shipping_rule', 'shipping_mode_key',
                    new_column_name='shipping_method',
                    existing_type=sa.String(length=20),
                    existing_nullable=True)
    
    # Rename the index back (PostgreSQL)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_indexes 
                WHERE indexname = 'ix_shipping_rule_shipping_mode_key' 
                AND tablename = 'shipping_rule'
            ) THEN
                ALTER INDEX ix_shipping_rule_shipping_mode_key 
                RENAME TO ix_shipping_rule_shipping_method;
            END IF;
        END $$;
    """)
