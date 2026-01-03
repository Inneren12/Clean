from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
import sqlalchemy as sa

from app.settings import settings


def test_alembic_has_single_head():
    config = Config("alembic.ini")
    script_directory = ScriptDirectory.from_config(config)

    heads = script_directory.get_heads()

    assert len(heads) == 1, f"Expected 1 Alembic head, found {heads}"


def test_alembic_upgrade_head(tmp_path):
    db_path = tmp_path / "test.db"
    config = Config("alembic.ini")
    original_database_url = settings.database_url
    try:
        settings.database_url = f"sqlite+aiosqlite:///{db_path}"
        command.upgrade(config, "head")
    finally:
        settings.database_url = original_database_url

    engine = create_engine(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    assert "leads" in tables
    assert "chat_sessions" in tables
    assert "teams" in tables
    assert "bookings" in tables
    assert "email_events" in tables
    assert "event_logs" in tables
    assert "referral_credits" in tables

    lead_columns = {col["name"] for col in inspector.get_columns("leads")}
    assert "referral_code" in lead_columns
    assert "referred_by_code" in lead_columns


def test_default_team_dedupe_is_fk_safe(tmp_path):
    db_path = tmp_path / "dupes.db"
    config = Config("alembic.ini")
    original_database_url = settings.database_url
    try:
        settings.database_url = f"sqlite+aiosqlite:///{db_path}"
        command.upgrade(config, "0007_referrals")

        engine = create_engine(f"sqlite:///{db_path}")
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM bookings"))
            conn.execute(sa.text("DELETE FROM teams"))
            conn.execute(sa.text("INSERT INTO teams (team_id, name) VALUES (:id, :name)"), {"id": 1, "name": "Default Team"})
            conn.execute(sa.text("INSERT INTO teams (team_id, name) VALUES (:id, :name)"), {"id": 2, "name": "Default Team"})
            conn.execute(sa.text("INSERT INTO teams (team_id, name) VALUES (:id, :name)"), {"id": 3, "name": "Crew A"})
            conn.execute(sa.text("INSERT INTO teams (team_id, name) VALUES (:id, :name)"), {"id": 4, "name": "Crew A"})
            conn.execute(
                sa.text(
                    "INSERT INTO bookings (booking_id, team_id, starts_at, duration_minutes, status) "
                    "VALUES (:id, :team_id, :starts_at, :duration, :status)"
                ),
                {
                    "id": "b1",
                    "team_id": 2,
                    "starts_at": "2025-01-01T00:00:00Z",
                    "duration": 60,
                    "status": "PENDING",
                },
            )
            conn.execute(
                sa.text(
                    "INSERT INTO bookings (booking_id, team_id, starts_at, duration_minutes, status) "
                    "VALUES (:id, :team_id, :starts_at, :duration, :status)"
                ),
                {
                    "id": "b2",
                    "team_id": 4,
                    "starts_at": "2025-01-02T00:00:00Z",
                    "duration": 60,
                    "status": "CONFIRMED",
                },
            )

        command.upgrade(config, "head")
    finally:
        settings.database_url = original_database_url

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        teams = conn.execute(sa.text("SELECT name, COUNT(*) AS c FROM teams GROUP BY name")).fetchall()
        team_map = {row.name: row.c for row in teams}
        assert team_map.get("Default Team") == 1
        assert team_map.get("Crew A") == 1

        booking_teams = conn.execute(sa.text("SELECT booking_id, team_id FROM bookings ORDER BY booking_id")).fetchall()
        assert booking_teams == [("b1", 1), ("b2", 3)]
