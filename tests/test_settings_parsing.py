import pytest
from pydantic import ValidationError

from app.settings import Settings


@pytest.mark.parametrize(
    "env_value, expected",
    [
        (None, []),
        ("https://example.com", ["https://example.com"]),
        ("https://a.com, https://b.com", ["https://a.com", "https://b.com"]),
        ('["https://a.com","https://b.com"]', ["https://a.com", "https://b.com"]),
    ],
)
@pytest.mark.parametrize(
    "env_name, attr_name",
    [
        ("CORS_ORIGINS", "cors_origins"),
        ("EXPORT_WEBHOOK_ALLOWED_HOSTS", "export_webhook_allowed_hosts"),
        ("TRUSTED_PROXY_IPS", "trusted_proxy_ips"),
        ("TRUSTED_PROXY_CIDRS", "trusted_proxy_cidrs"),
    ],
)
def test_list_env_parsing(monkeypatch, env_name, attr_name, env_value, expected):
    if env_value is None:
        monkeypatch.delenv(env_name, raising=False)
    else:
        monkeypatch.setenv(env_name, env_value)

    settings = Settings(_env_file=None)

    assert getattr(settings, attr_name) == expected


def test_dev_defaults_allow_placeholders():
    settings = Settings(app_env="dev", _env_file=None)

    assert settings.auth_secret_key == "dev-auth-secret"


def test_prod_requires_non_default_secrets():
    with pytest.raises(ValidationError, match="AUTH_SECRET_KEY"):
        Settings(app_env="prod", _env_file=None)


def test_prod_requires_metrics_token_when_enabled():
    with pytest.raises(ValidationError, match="METRICS_TOKEN"):
        Settings(
            app_env="prod",
            auth_secret_key="super-secret",
            client_portal_secret="client-secret",
            worker_portal_secret="worker-secret",
            metrics_enabled=True,
            metrics_token=None,
            _env_file=None,
        )


def test_prod_rejects_wildcard_cors_with_strict_mode():
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        Settings(
            app_env="prod",
            auth_secret_key="super-secret",
            client_portal_secret="client-secret",
            worker_portal_secret="worker-secret",
            strict_cors=True,
            cors_origins=["*"],
            _env_file=None,
        )


def test_prod_validates_admin_allowlist_cidrs():
    with pytest.raises(ValidationError, match="ADMIN_IP_ALLOWLIST_CIDRS"):
        Settings(
            app_env="prod",
            auth_secret_key="super-secret",
            client_portal_secret="client-secret",
            worker_portal_secret="worker-secret",
            metrics_enabled=False,
            admin_ip_allowlist_cidrs="not-a-cidr",
            _env_file=None,
        )


def test_prod_rejects_testing_mode_override():
    with pytest.raises(ValidationError, match="X-Test-Org"):
        Settings(
            app_env="prod",
            auth_secret_key="super-secret",
            client_portal_secret="client-secret",
            worker_portal_secret="worker-secret",
            metrics_enabled=False,
            testing=True,
            _env_file=None,
        )


def test_prod_accepts_valid_configuration():
    settings = Settings(
        app_env="prod",
        auth_secret_key="super-secret",
        client_portal_secret="client-secret",
        worker_portal_secret="worker-secret",
        strict_cors=True,
        cors_origins=["https://example.com"],
        metrics_enabled=True,
        metrics_token="metrics-token",
        admin_ip_allowlist_cidrs="10.0.0.0/8",
        _env_file=None,
    )

    assert settings.admin_ip_allowlist_cidrs == ["10.0.0.0/8"]
