"""seed all world countries

Revision ID: seed_world_countries_001
Revises: h77i890e1f3g
Create Date: 2025-01-29 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from datetime import datetime

# revision identifiers, used by Alembic.
revision: str = 'seed_world_countries_001'
down_revision: Union[str, None] = 'h77i890e1f3g'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Seed all world countries into the country table."""
    # Import the world countries data directly without importing the app
    import sys
    import os
    import importlib.util
    
    # Get the path to world_countries.py
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    world_countries_path = os.path.join(base_dir, 'app', 'data', 'world_countries.py')
    
    # Load the module without importing the app
    spec = importlib.util.spec_from_file_location("world_countries", world_countries_path)
    world_countries_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(world_countries_module)
    
    WORLD_COUNTRIES = world_countries_module.WORLD_COUNTRIES
    get_flag_url = world_countries_module.get_flag_url
    
    now = datetime.utcnow()
    
    # Get existing countries to avoid duplicates
    connection = op.get_bind()
    existing_codes = set()
    try:
        result = connection.execute(text("SELECT code FROM country"))
        for row in result:
            existing_codes.add(row[0])
    except Exception:
        # Table might not exist yet, that's okay
        pass
    
    # Insert all world countries
    countries_to_insert = []
    for country_data in WORLD_COUNTRIES:
        if country_data['code'] not in existing_codes:
            # Generate flag URL if not provided
            flag_url = get_flag_url(country_data['code'])
            
            countries_to_insert.append({
                'name': country_data['name'],
                'code': country_data['code'],
                'currency': country_data['currency'],
                'currency_symbol': country_data.get('currency_symbol', ''),
                'language': country_data['language'],
                'flag_image_path': flag_url,
                'is_active': country_data.get('is_active', False),
                'created_at': now,
                'updated_at': now
            })
    
    if countries_to_insert:
        # Insert countries in batches
        for country in countries_to_insert:
            try:
                connection.execute(
                    text("""
                        INSERT INTO country (name, code, currency, currency_symbol, language, flag_image_path, is_active, created_at, updated_at)
                        VALUES (:name, :code, :currency, :currency_symbol, :language, :flag_image_path, :is_active, :created_at, :updated_at)
                    """),
                    {
                        'name': country['name'],
                        'code': country['code'],
                        'currency': country['currency'],
                        'currency_symbol': country['currency_symbol'],
                        'language': country['language'],
                        'flag_image_path': country['flag_image_path'],
                        'is_active': country['is_active'],
                        'created_at': country['created_at'],
                        'updated_at': country['updated_at']
                    }
                )
            except Exception as e:
                # Skip duplicates or other errors, continue with next country
                print(f"Warning: Could not insert {country['name']}: {e}")
                continue


def downgrade() -> None:
    """Remove all seeded countries, keeping only the original 7 countries."""
    connection = op.get_bind()
    
    # Keep only the original 7 countries
    original_codes = ['SN', 'CI', 'GM', 'ML', 'BF', 'SL', 'UG']
    
    # Delete all countries not in the original list
    connection.execute(
        text("DELETE FROM country WHERE code NOT IN :codes"),
        {"codes": tuple(original_codes)}
    )

