"""add country localization system

Revision ID: h77i890e1f3g
Revises: deb6a76fcc77
Create Date: 2025-01-XX XX:XX:XX.XXXXXX

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'h77i890e1f3g'
down_revision: Union[str, None] = 'deb6a76fcc77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create country table
    op.create_table(
        'country',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('code', sa.String(length=10), nullable=False),
        sa.Column('currency', sa.String(length=10), nullable=False),
        sa.Column('currency_symbol', sa.String(length=10), nullable=True, server_default=''),
        sa.Column('language', sa.String(length=10), nullable=False),
        sa.Column('flag_image_path', sa.String(length=255), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
        sa.UniqueConstraint('code')
    )
    
    # Create indexes for country
    op.create_index('ix_country_code', 'country', ['code'])
    op.create_index('ix_country_is_active', 'country', ['is_active'])
    
    # Add country_id to user table
    op.add_column('user', sa.Column('country_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_user_country', 'user', 'country', ['country_id'], ['id'])
    op.create_index('ix_user_country_id', 'user', ['country_id'])
    
    # Insert default countries
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    op.execute(f"""
        INSERT INTO country (name, code, currency, currency_symbol, language, is_active, created_at, updated_at)
        VALUES
        ('Senegal', 'SN', 'XOF', 'CFA', 'fr', true, '{now}', '{now}'),
        ('Côte d''Ivoire', 'CI', 'XOF', 'CFA', 'fr', true, '{now}', '{now}'),
        ('Gambia', 'GM', 'GMD', 'D', 'en', true, '{now}', '{now}'),
        ('Mali', 'ML', 'XOF', 'CFA', 'fr', true, '{now}', '{now}'),
        ('Burkina Faso', 'BF', 'XOF', 'CFA', 'fr', true, '{now}', '{now}'),
        ('Sierra Leone', 'SL', 'SLL', 'Le', 'en', true, '{now}', '{now}'),
        ('Uganda', 'UG', 'UGX', 'USh', 'en', true, '{now}', '{now}')
    """)


def downgrade() -> None:
    # Drop foreign key and index from user table
    op.drop_index('ix_user_country_id', table_name='user')
    op.drop_constraint('fk_user_country', 'user', type_='foreignkey')
    op.drop_column('user', 'country_id')
    
    # Drop indexes from country table
    op.drop_index('ix_country_is_active', table_name='country')
    op.drop_index('ix_country_code', table_name='country')
    
    # Drop country table
    op.drop_table('country')

