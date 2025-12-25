from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.settings import settings


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
