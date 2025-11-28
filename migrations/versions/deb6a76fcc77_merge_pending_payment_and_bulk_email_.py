"""merge pending payment and bulk email migrations

Revision ID: deb6a76fcc77
Revises: 45d0037246ae, g66h789d0e2f
Create Date: 2025-11-28 18:41:48.157426

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'deb6a76fcc77'
down_revision: Union[str, None] = ('45d0037246ae', 'g66h789d0e2f')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
