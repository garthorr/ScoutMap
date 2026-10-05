"""scout login codes replace scout passwords

Scouts now sign in with a unique code that admins can see, print and
export. Old password hashes can't be turned back into codes, so every
existing scout gets a fresh code here.

Revision ID: b7d3e9a1c5f2
Revises: a1c4f2d9b7e3
Create Date: 2026-10-05 00:00:00.000000

"""
import secrets
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d3e9a1c5f2'
down_revision: Union[str, None] = 'a1c4f2d9b7e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('scout_roster', sa.Column('login_code', sa.String(length=8), nullable=True))

    conn = op.get_bind()
    used = set()
    for (scout_id,) in conn.execute(sa.text("SELECT id FROM scout_roster")).fetchall():
        code = f"{secrets.randbelow(10**8):08d}"
        while code in used:
            code = f"{secrets.randbelow(10**8):08d}"
        used.add(code)
        conn.execute(sa.text("UPDATE scout_roster SET login_code = :code WHERE id = :id"),
                     {"code": code, "id": scout_id})

    op.create_index('ix_scout_roster_login_code', 'scout_roster', ['login_code'], unique=True)
    with op.batch_alter_table('scout_roster') as batch:
        batch.drop_column('password_hash')


def downgrade() -> None:
    op.drop_index('ix_scout_roster_login_code', table_name='scout_roster')
    with op.batch_alter_table('scout_roster') as batch:
        batch.drop_column('login_code')
        batch.add_column(sa.Column('password_hash', sa.String(length=200), nullable=True))
