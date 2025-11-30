"""add currency rates system

Revision ID: j99k012g3h4i
Revises: 8987a1cb18ec
Create Date: 2025-01-30 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'j99k012g3h4i'
down_revision: Union[str, None] = '8987a1cb18ec'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create currency_rate table
    op.create_table(
        'currency_rate',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('from_currency', sa.String(length=10), nullable=False),
        sa.Column('to_currency', sa.String(length=10), nullable=False),
        sa.Column('rate', sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('last_updated', sa.DateTime(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('api_sync_enabled', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('api_provider', sa.String(length=50), nullable=True),
        sa.Column('last_api_sync', sa.DateTime(), nullable=True),
        sa.Column('api_sync_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('from_currency', 'to_currency', name='unique_currency_pair')
    )
    
    # Create indexes
    op.create_index('ix_currency_rate_from_currency', 'currency_rate', ['from_currency'])
    op.create_index('ix_currency_rate_to_currency', 'currency_rate', ['to_currency'])
    op.create_index('ix_currency_rate_is_active', 'currency_rate', ['is_active'])
    op.create_index('idx_currency_pair', 'currency_rate', ['from_currency', 'to_currency'])
    op.create_index('idx_active_rates', 'currency_rate', ['is_active', 'from_currency', 'to_currency'])
    
    # Insert default rates from the hardcoded CURRENCY_RATES (using GMD as base)
    # This seeds common currency pairs with approximate rates
    from datetime import datetime
    from sqlalchemy import text
    
    now = datetime.utcnow()
    
    # Common rates (1 GMD = X target currency)
    default_rates = [
        ('GMD', 'XOF', 7.75, 'West African CFA franc'),
        ('GMD', 'XAF', 7.75, 'Central African CFA franc'),
        ('GMD', 'NGN', 28.5, 'Nigerian Naira'),
        ('GMD', 'GHS', 0.28, 'Ghanaian Cedi'),
        ('GMD', 'SLL', 2800.0, 'Sierra Leone Leone'),
        ('GMD', 'UGX', 38.0, 'Ugandan Shilling'),
        ('GMD', 'KES', 2.5, 'Kenyan Shilling'),
        ('GMD', 'USD', 0.019, 'US Dollar'),
        ('GMD', 'EUR', 0.017, 'Euro'),
        ('GMD', 'GBP', 0.015, 'British Pound'),
    ]
    
    # Insert default rates using parameterized queries
    connection = op.get_bind()
    for from_curr, to_curr, rate, note in default_rates:
        connection.execute(
            text("""
                INSERT INTO currency_rate (from_currency, to_currency, rate, is_active, last_updated, notes, created_at, updated_at)
                VALUES (:from_curr, :to_curr, :rate, true, :now, :note, :now, :now)
                ON CONFLICT (from_currency, to_currency) DO NOTHING
            """),
            {
                'from_curr': from_curr,
                'to_curr': to_curr,
                'rate': rate,
                'note': note,
                'now': now
            }
        )


def downgrade() -> None:
    # Drop indexes
    op.drop_index('idx_active_rates', table_name='currency_rate')
    op.drop_index('idx_currency_pair', table_name='currency_rate')
    op.drop_index('ix_currency_rate_is_active', table_name='currency_rate')
    op.drop_index('ix_currency_rate_to_currency', table_name='currency_rate')
    op.drop_index('ix_currency_rate_from_currency', table_name='currency_rate')
    
    # Drop currency_rate table
    op.drop_table('currency_rate')

