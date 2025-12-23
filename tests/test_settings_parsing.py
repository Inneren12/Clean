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
def test_cors_origins_parsing(monkeypatch, env_value, expected):
    if env_value is None:
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("CORS_ORIGINS", env_value)

    settings = Settings(_env_file=None)

    assert settings.cors_origins == expected
