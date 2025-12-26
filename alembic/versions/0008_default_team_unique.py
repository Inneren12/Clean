"""make team name unique and seed default

Revision ID: 0008_default_team_unique
Revises: 0007_referrals
Create Date: 2025-03-08 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_default_team_unique"
down_revision = "0007_referrals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    duplicates = bind.execute(
        sa.text(
            "SELECT name, MIN(team_id) AS keep_id FROM teams "
            "GROUP BY name HAVING COUNT(*) > 1"
        )
    ).fetchall()
    for row in duplicates:
        bind.execute(
            sa.text("DELETE FROM teams WHERE name = :name AND team_id != :keep_id"),
            {"name": row.name, "keep_id": row.keep_id},
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
