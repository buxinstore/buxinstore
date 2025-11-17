"""add_default_communication_receivers

Revision ID: d22be032641d
Revises: 02dd8f60678c
Create Date: 2025-11-17 11:44:31.134222

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd22be032641d'
down_revision: Union[str, None] = '02dd8f60678c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add default communication receiver fields to app_settings table
    op.add_column('app_settings', sa.Column('whatsapp_receiver', sa.String(length=50), nullable=True, server_default='+2200000000'))
    op.add_column('app_settings', sa.Column('email_receiver', sa.String(length=255), nullable=True, server_default='buxinstore9@gmail.com'))
    
    # Migrate data from old contact receivers to new default receivers if they exist
    op.execute("""
        UPDATE app_settings 
        SET whatsapp_receiver = COALESCE(whatsapp_receiver, contact_whatsapp_receiver, '+2200000000')
        WHERE whatsapp_receiver IS NULL
    """)
    
    op.execute("""
        UPDATE app_settings 
        SET email_receiver = COALESCE(email_receiver, contact_email_receiver, 'buxinstore9@gmail.com')
        WHERE email_receiver IS NULL
    """)


def downgrade() -> None:
    # Remove default communication receiver fields from app_settings table
    op.drop_column('app_settings', 'email_receiver')
    op.drop_column('app_settings', 'whatsapp_receiver')
