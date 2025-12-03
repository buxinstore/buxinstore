"""Add Gambia contact settings fields

Revision ID: o11p234q5r6s
Revises: n00o123p4q5r
Create Date: 2024-12-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'o11p234q5r6s'
down_revision = '453cd775393c'
branch_labels = None
depends_on = None


def upgrade():
    """Add Gambia contact settings columns to app_settings table."""
    # Add gambia_whatsapp_number column
    try:
        op.add_column('app_settings', sa.Column('gambia_whatsapp_number', sa.String(50), nullable=True))
    except Exception as e:
        print(f"Column gambia_whatsapp_number may already exist: {e}")
    
    # Add gambia_phone_number column
    try:
        op.add_column('app_settings', sa.Column('gambia_phone_number', sa.String(50), nullable=True))
    except Exception as e:
        print(f"Column gambia_phone_number may already exist: {e}")


def downgrade():
    """Remove Gambia contact settings columns from app_settings table."""
    try:
        op.drop_column('app_settings', 'gambia_phone_number')
    except Exception as e:
        print(f"Error dropping gambia_phone_number: {e}")
    
    try:
        op.drop_column('app_settings', 'gambia_whatsapp_number')
    except Exception as e:
        print(f"Error dropping gambia_whatsapp_number: {e}")

