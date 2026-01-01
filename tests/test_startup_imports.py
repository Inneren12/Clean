import importlib
import os


def test_main_importable(monkeypatch):
    """Ensure the application can be imported without circular import errors."""
    # Use dev defaults to avoid production-only validation during import.
    monkeypatch.setenv("APP_ENV", os.getenv("APP_ENV", "dev"))

    module = importlib.import_module("app.main")
    assert getattr(module, "app", None) is not None
