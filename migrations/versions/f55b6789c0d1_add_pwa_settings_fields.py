"""add_pwa_settings_fields

Revision ID: f55b6789c0d1
Revises: f44a5b6789c0
Create Date: 2025-11-22 04:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f55b6789c0d1'
down_revision: Union[str, None] = 'f44a5b6789c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add PWA settings fields to app_settings table
    op.add_column('app_settings', sa.Column('pwa_app_name', sa.String(length=255), nullable=True))
    op.add_column('app_settings', sa.Column('pwa_short_name', sa.String(length=100), nullable=True))
    op.add_column('app_settings', sa.Column('pwa_theme_color', sa.String(length=20), nullable=True, server_default='#ffffff'))
    op.add_column('app_settings', sa.Column('pwa_background_color', sa.String(length=20), nullable=True, server_default='#ffffff'))
    op.add_column('app_settings', sa.Column('pwa_start_url', sa.String(length=255), nullable=True, server_default='/'))
    op.add_column('app_settings', sa.Column('pwa_display', sa.String(length=50), nullable=True, server_default='standalone'))
    op.add_column('app_settings', sa.Column('pwa_description', sa.Text(), nullable=True))
    op.add_column('app_settings', sa.Column('pwa_logo_path', sa.String(length=500), nullable=True))
    op.add_column('app_settings', sa.Column('pwa_favicon_path', sa.String(length=500), nullable=True))


def downgrade() -> None:
    # Remove PWA settings fields from app_settings table
    op.drop_column('app_settings', 'pwa_favicon_path')
    op.drop_column('app_settings', 'pwa_logo_path')
    op.drop_column('app_settings', 'pwa_description')
    op.drop_column('app_settings', 'pwa_display')
    op.drop_column('app_settings', 'pwa_start_url')
    op.drop_column('app_settings', 'pwa_background_color')
    op.drop_column('app_settings', 'pwa_theme_color')
    op.drop_column('app_settings', 'pwa_short_name')
    op.drop_column('app_settings', 'pwa_app_name')

