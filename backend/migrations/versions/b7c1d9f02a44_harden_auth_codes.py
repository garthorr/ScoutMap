"""Harden auth codes: store hashes, track failed attempts

Revision ID: b7c1d9f02a44
Revises: e662a51ab537
Create Date: 2026-06-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7c1d9f02a44'
down_revision: Union[str, None] = 'e662a51ab537'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Codes are now stored as PBKDF2 hashes ("salt:hash" hex), not 6-digit plaintext
    op.alter_column('auth_codes', 'code',
                    existing_type=sa.String(length=6),
                    type_=sa.String(length=200),
                    existing_nullable=False)
    op.add_column('auth_codes', sa.Column('attempts', sa.Integer(), nullable=True, server_default='0'))
    # Any pre-existing plaintext codes are unverifiable against the new scheme
    op.execute("UPDATE auth_codes SET used = true WHERE used = false")
    # Session tokens are now stored hashed; existing plaintext tokens can never
    # match a hashed lookup, so clear them out rather than leave dead rows.
    op.execute("DELETE FROM auth_sessions")


def downgrade() -> None:
    op.drop_column('auth_codes', 'attempts')
    op.alter_column('auth_codes', 'code',
                    existing_type=sa.String(length=200),
                    type_=sa.String(length=6),
                    existing_nullable=False)
