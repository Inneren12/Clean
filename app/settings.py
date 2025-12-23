import json
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "cleaning-economy-bot"
    cors_origins_raw: str | None = Field(None, env="CORS_ORIGINS", validation_alias="cors_origins")
    app_env: Literal["dev", "prod"] = Field("prod", env="APP_ENV")
    strict_cors: bool = Field(False, env="STRICT_CORS")
    rate_limit_per_minute: int = Field(30, env="RATE_LIMIT_PER_MINUTE")
    rate_limit_cleanup_minutes: int = Field(10, env="RATE_LIMIT_CLEANUP_MINUTES")
    trust_proxy_headers: bool = Field(False, env="TRUST_PROXY_HEADERS")
    trusted_proxy_ips_raw: str | None = Field(
        None,
        env="TRUSTED_PROXY_IPS",
        validation_alias="trusted_proxy_ips",
    )
    trusted_proxy_cidrs_raw: str | None = Field(
        None,
        env="TRUSTED_PROXY_CIDRS",
        validation_alias="trusted_proxy_cidrs",
    )
    pricing_config_path: str = Field("pricing/economy_v1.json", env="PRICING_CONFIG_PATH")
    database_url: str = Field(
        "postgresql+psycopg://postgres:postgres@postgres:5432/cleaning",
        env="DATABASE_URL",
    )
    export_mode: Literal["off", "webhook", "sheets"] = Field("off", env="EXPORT_MODE")
    export_webhook_url: str | None = Field(None, env="EXPORT_WEBHOOK_URL")
    export_webhook_timeout_seconds: int = Field(5, env="EXPORT_WEBHOOK_TIMEOUT_SECONDS")
    export_webhook_max_retries: int = Field(3, env="EXPORT_WEBHOOK_MAX_RETRIES")
    export_webhook_backoff_seconds: float = Field(1.0, env="EXPORT_WEBHOOK_BACKOFF_SECONDS")
    export_webhook_allowed_hosts_raw: str | None = Field(
        None,
        env="EXPORT_WEBHOOK_ALLOWED_HOSTS",
        validation_alias="export_webhook_allowed_hosts",
    )
    export_webhook_allow_http: bool = Field(False, env="EXPORT_WEBHOOK_ALLOW_HTTP")
    export_webhook_block_private_ips: bool = Field(True, env="EXPORT_WEBHOOK_BLOCK_PRIVATE_IPS")

    model_config = SettingsConfigDict(env_file=".env", enable_decoding=False)

    @field_validator(
        "cors_origins_raw",
        "trusted_proxy_ips_raw",
        "trusted_proxy_cidrs_raw",
        "export_webhook_allowed_hosts_raw",
        mode="before",
    )
    @classmethod
    def normalize_list_raw(cls, value: object) -> str | None:
        return cls._normalize_raw_list(value)

    @property
    def cors_origins(self) -> list[str]:
        return self._parse_list(self.cors_origins_raw)

    @cors_origins.setter
    def cors_origins(self, value: list[str] | str | None) -> None:
        self.cors_origins_raw = self._normalize_raw_list(value)

    @property
    def trusted_proxy_ips(self) -> list[str]:
        return self._parse_list(self.trusted_proxy_ips_raw)

    @trusted_proxy_ips.setter
    def trusted_proxy_ips(self, value: list[str] | str | None) -> None:
        self.trusted_proxy_ips_raw = self._normalize_raw_list(value)

    @property
    def trusted_proxy_cidrs(self) -> list[str]:
        return self._parse_list(self.trusted_proxy_cidrs_raw)

    @trusted_proxy_cidrs.setter
    def trusted_proxy_cidrs(self, value: list[str] | str | None) -> None:
        self.trusted_proxy_cidrs_raw = self._normalize_raw_list(value)

    @property
    def export_webhook_allowed_hosts(self) -> list[str]:
        return self._parse_list(self.export_webhook_allowed_hosts_raw)

    @export_webhook_allowed_hosts.setter
    def export_webhook_allowed_hosts(self, value: list[str] | str | None) -> None:
        self.export_webhook_allowed_hosts_raw = self._normalize_raw_list(value)

    @staticmethod
    def _normalize_raw_list(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return json.dumps(value)
        return str(value)

    @staticmethod
    def _parse_list(raw: str | None) -> list[str]:
        if raw is None:
            return []
        stripped = raw.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return [str(entry).strip() for entry in parsed if str(entry).strip()]
            return [str(parsed).strip()] if str(parsed).strip() else []
        entries = [entry.strip() for entry in stripped.split(",")]
        return [entry for entry in entries if entry]


settings = Settings()
