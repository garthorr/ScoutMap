"""scout sign-in codes become 8 digits

Replaces any scout code that isn't 8 digits (for databases that already
ran the 6-digit version of the previous migration). Those scouts need
new sign-in cards.

Revision ID: c4e8f1a2b6d9
Revises: b7d3e9a1c5f2
Create Date: 2026-10-05 00:00:00.000000

"""
import secrets
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e8f1a2b6d9'
down_revision: Union[str, None] = 'b7d3e9a1c5f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _replace_codes(length: int) -> None:
    """Give every scout whose code isn't `length` digits a new unique one."""
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, login_code FROM scout_roster")).fetchall()
    used = {code for _, code in rows if code and len(code) == length}
    for scout_id, code in rows:
        if code and len(code) == length:
            continue
        new = f"{secrets.randbelow(10**length):0{length}d}"
        while new in used:
            new = f"{secrets.randbelow(10**length):0{length}d}"
        used.add(new)
        conn.execute(sa.text("UPDATE scout_roster SET login_code = :code WHERE id = :id"),
                     {"code": new, "id": scout_id})


def upgrade() -> None:
    _replace_codes(8)


def downgrade() -> None:
    _replace_codes(6)
