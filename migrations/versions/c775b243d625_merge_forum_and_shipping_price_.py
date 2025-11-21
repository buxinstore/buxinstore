"""merge forum and shipping price migrations

Revision ID: c775b243d625
Revises: 441e9e9c2468, b2c3d4e5f6a7
Create Date: 2025-11-21 16:16:08.594453

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c775b243d625'
down_revision: Union[str, None] = ('441e9e9c2468', 'b2c3d4e5f6a7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
