"""admin visit entry: who entered a visit, which scout, offline save id

Also credits visits made with the old admin "Visit" pop-up (which saved the
scout as a volunteer) to the matching roster scout, so they show up in
Scout Data.

Revision ID: d2a6b8c4e1f3
Revises: c4e8f1a2b6d9
Create Date: 2026-10-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2a6b8c4e1f3'
down_revision: Union[str, None] = 'c4e8f1a2b6d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('visits', sa.Column('scout_roster_id', sa.UUID(), nullable=True))
    op.add_column('visits', sa.Column('entered_by', sa.String(length=320), nullable=True))
    op.add_column('visits', sa.Column('client_id', sa.String(length=64), nullable=True))
    op.create_index('ix_visits_scout_roster_id', 'visits', ['scout_roster_id'])
    op.create_index('ix_visits_client_id', 'visits', ['client_id'], unique=True)

    # Old admin pop-up visits: volunteer name that matches a roster scout -> that scout
    conn = op.get_bind()
    roster = {name.strip().lower(): (rid, sid, name) for rid, name, sid in
              conn.execute(sa.text("SELECT id, name, scout_id FROM scout_roster")).fetchall() if name}
    rows = conn.execute(sa.text(
        "SELECT id, volunteer_name FROM visits WHERE scout_name IS NULL AND volunteer_name IS NOT NULL"
    )).fetchall()
    for visit_id, volunteer in rows:
        match = roster.get(volunteer.strip().lower())
        if match:
            conn.execute(sa.text(
                "UPDATE visits SET scout_name = :name, scout_id = :sid, scout_roster_id = :rid, "
                "entered_by = 'admin' WHERE id = :id"),
                {"name": match[2], "sid": match[1], "rid": match[0], "id": visit_id})


def downgrade() -> None:
    op.drop_index('ix_visits_client_id', table_name='visits')
    op.drop_index('ix_visits_scout_roster_id', table_name='visits')
    with op.batch_alter_table('visits') as batch:
        batch.drop_column('client_id')
        batch.drop_column('entered_by')
        batch.drop_column('scout_roster_id')
