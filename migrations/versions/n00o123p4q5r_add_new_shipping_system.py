"""add new shipping system with modes and rules

Revision ID: n00o123p4q5r
Revises: m99n012o3p4q
Create Date: 2025-01-21 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'n00o123p4q5r'
down_revision: Union[str, None] = 'm99n012o3p4q'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create shipping_modes table
    op.create_table('shipping_modes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=50), nullable=False),
        sa.Column('label', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('delivery_time_range', sa.String(length=100), nullable=True),
        sa.Column('icon', sa.String(length=50), nullable=True),
        sa.Column('color', sa.String(length=50), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key')
    )
    
    # Create shipping_rules table (new schema)
    op.create_table('shipping_rules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('country_iso', sa.String(length=3), nullable=False),
        sa.Column('shipping_mode_key', sa.String(length=50), nullable=False),
        sa.Column('min_weight', sa.Numeric(10, 3), nullable=False),
        sa.Column('max_weight', sa.Numeric(10, 3), nullable=False),
        sa.Column('price_gmd', sa.Numeric(10, 2), nullable=False),
        sa.Column('delivery_time', sa.String(length=100), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['shipping_mode_key'], ['shipping_modes.key'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('min_weight < max_weight', name='check_min_max_weight'),
        sa.CheckConstraint('price_gmd >= 0', name='check_price_non_negative')
    )
    
    # Create indices for efficient queries
    op.create_index('ix_shipping_rules_country_iso', 'shipping_rules', ['country_iso'])
    op.create_index('ix_shipping_rules_shipping_mode_key', 'shipping_rules', ['shipping_mode_key'])
    op.create_index('ix_shipping_rules_priority', 'shipping_rules', ['priority'])
    op.create_index('idx_country_mode_weight', 'shipping_rules', ['country_iso', 'shipping_mode_key', 'min_weight', 'max_weight'])
    
    # Seed shipping modes
    op.execute("""
        INSERT INTO shipping_modes (key, label, description, delivery_time_range, icon, color, active, created_at, updated_at)
        VALUES 
        ('express', 'DHL Express / FedEx International (Fast, 3–7 days)', 'Fastest delivery. Fully tracked from China to your location.', '3–7 days', '🚀', 'red', true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
        ('economy_plus', 'DHL eCommerce / DHL Global Forwarding (Medium, 10–20 days)', 'Reliable shipping with tracking. Delivered by DHL partner or Post Office in your country.', '10–20 days', '📦', 'yellow', true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
        ('economy', 'AliExpress Economy Mail (Slow, 20–60 days)', 'Low-cost shipping. Parcel will be sent to your local Post Office for pickup.', '20–60 days', '📮', 'green', true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """)


def downgrade() -> None:
    # Drop indices
    op.drop_index('idx_country_mode_weight', table_name='shipping_rules')
    op.drop_index('ix_shipping_rules_priority', table_name='shipping_rules')
    op.drop_index('ix_shipping_rules_shipping_mode_key', table_name='shipping_rules')
    op.drop_index('ix_shipping_rules_country_iso', table_name='shipping_rules')
    
    # Drop tables
    op.drop_table('shipping_rules')
    op.drop_table('shipping_modes')

