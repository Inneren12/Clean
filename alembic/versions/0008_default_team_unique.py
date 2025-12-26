"""make team name unique and seed default

Revision ID: 0008_default_team_unique
Revises: 0007_referrals
Create Date: 2025-03-08 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import bindparam


revision = "0008_default_team_unique"
down_revision = "0007_referrals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    duplicate_names = bind.execute(
        sa.text("SELECT name FROM teams GROUP BY name HAVING COUNT(*) > 1")
    ).fetchall()
    for row in duplicate_names:
        teams = bind.execute(
            sa.text("SELECT team_id FROM teams WHERE name = :name ORDER BY team_id"),
            {"name": row.name},
        ).fetchall()
        if not teams:
            continue

        keep_id = teams[0].team_id
        duplicate_ids = [team.team_id for team in teams[1:]]
        if duplicate_ids:
            bind.execute(
                sa.text(
                    "UPDATE bookings SET team_id = :keep_id WHERE team_id IN :dupes"
                ).bindparams(bindparam("dupes", expanding=True)),
                {"keep_id": keep_id, "dupes": duplicate_ids},
            )
            bind.execute(
                sa.text("DELETE FROM teams WHERE team_id IN :dupes").bindparams(
                    bindparam("dupes", expanding=True)
                ),
                {"dupes": duplicate_ids},
            )

    existing_default = bind.execute(
        sa.text("SELECT team_id FROM teams WHERE name = :name"), {"name": "Default Team"}
    ).fetchone()
    if existing_default is None:
        bind.execute(sa.text("INSERT INTO teams (name) VALUES (:name)"), {"name": "Default Team"})

    with op.batch_alter_table("teams") as batch:
        batch.create_unique_constraint("uq_teams_name", ["name"])


def downgrade() -> None:
    with op.batch_alter_table("teams") as batch:
        batch.drop_constraint("uq_teams_name", type_="unique")
