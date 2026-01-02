import importlib


def test_main_importable(monkeypatch):
    """Ensure the application can be imported without circular import errors."""
    # Force development/testing mode to avoid production-only validation during import.
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("TESTING", "true")

    module = importlib.import_module("app.main")
    assert getattr(module, "app", None) is not None
