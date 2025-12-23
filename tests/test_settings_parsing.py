import pytest

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
