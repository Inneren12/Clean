import importlib.util
from pathlib import Path


def test_import_0034_migration_module() -> None:
    module_path = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "0034_org_id_uuid_and_default_org.py"
    spec = importlib.util.spec_from_file_location("migration_0034", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
