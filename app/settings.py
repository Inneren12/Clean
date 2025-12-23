import json
from typing import List, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "cleaning-economy-bot"
    cors_origins: List[str] = Field(default_factory=list, env="CORS_ORIGINS")
    app_env: Literal["dev", "prod"] = Field("prod", env="APP_ENV")
    strict_cors: bool = Field(False, env="STRICT_CORS")
    rate_limit_per_minute: int = Field(30, env="RATE_LIMIT_PER_MINUTE")
    rate_limit_cleanup_minutes: int = Field(10, env="RATE_LIMIT_CLEANUP_MINUTES")
    trust_proxy_headers: bool = Field(False, env="TRUST_PROXY_HEADERS")
    trusted_proxy_ips: List[str] = Field(default_factory=list, env="TRUSTED_PROXY_IPS")
    trusted_proxy_cidrs: List[str] = Field(default_factory=list, env="TRUSTED_PROXY_CIDRS")
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
    export_webhook_allowed_hosts: List[str] = Field(default_factory=list, env="EXPORT_WEBHOOK_ALLOWED_HOSTS")
    export_webhook_allow_http: bool = Field(False, env="EXPORT_WEBHOOK_ALLOW_HTTP")
    export_webhook_block_private_ips: bool = Field(True, env="EXPORT_WEBHOOK_BLOCK_PRIVATE_IPS")

    model_config = SettingsConfigDict(env_file=".env", enable_decoding=False)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> List[str]:
        return cls._parse_csv_list(value)

    @field_validator("export_webhook_allowed_hosts", "trusted_proxy_ips", "trusted_proxy_cidrs", mode="before")
    @classmethod
    def parse_csv_lists(cls, value: object) -> List[str]:
        return cls._parse_csv_list(value)

    @staticmethod
    def _parse_csv_list(value: object) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("[") and stripped.endswith("]"):
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return [str(entry).strip() for entry in parsed if str(entry).strip()]
                return [str(parsed).strip()] if str(parsed).strip() else []
            entries = [entry.strip() for entry in stripped.split(",")]
            return [entry for entry in entries if entry]
        if isinstance(value, list):
            return [str(entry).strip() for entry in value if str(entry).strip()]
        return [str(value)]


settings = Settings()
