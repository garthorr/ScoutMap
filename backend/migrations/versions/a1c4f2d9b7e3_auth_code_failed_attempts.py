"""auth_code failed_attempts

Revision ID: a1c4f2d9b7e3
Revises: e662a51ab537
Create Date: 2026-10-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c4f2d9b7e3'
down_revision: Union[str, None] = 'e662a51ab537'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('auth_codes', sa.Column('failed_attempts', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('auth_codes', 'failed_attempts')
