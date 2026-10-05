"""Store session tokens and email login codes hashed

Revision ID: e5f7a9c1d3b8
Revises: d2a6b8c4e1f3
Create Date: 2026-10-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5f7a9c1d3b8'
down_revision: Union[str, None] = 'd2a6b8c4e1f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Codes become "salt:hash" hex strings instead of 6 plaintext digits
    with op.batch_alter_table('auth_codes') as batch:
        batch.alter_column('code', existing_type=sa.String(length=6),
                           type_=sa.String(length=200), existing_nullable=False)
    # Existing plaintext codes and tokens can never match a hashed lookup;
    # clear them so everyone simply signs in once more.
    op.execute("UPDATE auth_codes SET used = true WHERE used = false")
    op.execute("DELETE FROM auth_sessions")


def downgrade() -> None:
    op.execute("DELETE FROM auth_codes")
    op.execute("DELETE FROM auth_sessions")
    with op.batch_alter_table('auth_codes') as batch:
        batch.alter_column('code', existing_type=sa.String(length=200),
                           type_=sa.String(length=6), existing_nullable=False)
