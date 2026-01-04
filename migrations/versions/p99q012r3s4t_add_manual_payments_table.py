"""Add manual_payments table

Revision ID: p99q012r3s4t
Revises: seed_all_world_countries
Create Date: 2026-01-04 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'p99q012r3s4t'
down_revision = ('o11p234q5r6s', 'seed_world_countries_001')  # Merge point: both branches
branch_labels = None
depends_on = None


def upgrade():
    # Create manual_payments table
    op.create_table(
        'manual_payments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('pending_payment_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=True),
        sa.Column('payment_method', sa.String(length=50), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('receipt_url', sa.Text(), nullable=True),
        sa.Column('receipt_public_id', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=20), server_default='pending', nullable=True),
        sa.Column('approved_by', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['pending_payment_id'], ['pending_payments.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.ForeignKeyConstraint(['order_id'], ['order.id'], ),
        sa.ForeignKeyConstraint(['approved_by'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for better query performance
    op.create_index('ix_manual_payments_user_id', 'manual_payments', ['user_id'])
    op.create_index('ix_manual_payments_status', 'manual_payments', ['status'])
    op.create_index('ix_manual_payments_pending_payment_id', 'manual_payments', ['pending_payment_id'])
    op.create_index('ix_manual_payments_order_id', 'manual_payments', ['order_id'])


def downgrade():
    # Drop indexes
    op.drop_index('ix_manual_payments_order_id', table_name='manual_payments')
    op.drop_index('ix_manual_payments_pending_payment_id', table_name='manual_payments')
    op.drop_index('ix_manual_payments_status', table_name='manual_payments')
    op.drop_index('ix_manual_payments_user_id', table_name='manual_payments')
    
    # Drop table
    op.drop_table('manual_payments')

