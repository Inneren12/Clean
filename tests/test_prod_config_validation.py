import types

import pytest

from app import main


def _settings(**overrides):
    class Dummy:
        app_env = "prod"
        testing = False
        metrics_enabled = True
        metrics_token = "t" * 32
        auth_secret_key = "a" * 32
        client_portal_secret = "c" * 32
        worker_portal_secret = "w" * 32
        owner_basic_username = "owner"
        owner_basic_password = "password"
        admin_basic_username = None
        admin_basic_password = None
        dispatcher_basic_username = None
        dispatcher_basic_password = None
        accountant_basic_username = None
        accountant_basic_password = None
        viewer_basic_username = None
        viewer_basic_password = None

    settings_obj = Dummy()
    for key, value in overrides.items():
        setattr(settings_obj, key, value)
    return settings_obj


def _disable_pytest_shortcuts(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(main, "sys", types.SimpleNamespace(argv=["app.py"]))


def test_validate_prod_config_rejects_weak_secrets(monkeypatch):
    settings_obj = _settings(
        auth_secret_key="dev-auth-secret",
        client_portal_secret="dev-client-portal-secret",
        worker_portal_secret="short",
        metrics_token=None,
    )
    _disable_pytest_shortcuts(monkeypatch)

    with pytest.raises(RuntimeError) as excinfo:
        main._validate_prod_config(settings_obj)

    message = str(excinfo.value)
    assert "AUTH_SECRET_KEY" in message
    assert "CLIENT_PORTAL_SECRET" in message
    assert "WORKER_PORTAL_SECRET" in message
    assert "METRICS_TOKEN" in message


def test_validate_prod_config_accepts_strong_secrets(monkeypatch):
    settings_obj = _settings()
    _disable_pytest_shortcuts(monkeypatch)

    main._validate_prod_config(settings_obj)
