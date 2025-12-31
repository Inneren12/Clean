import json
import uuid
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "cleaning-economy-bot"
    cors_origins_raw: str | None = Field(None, env="CORS_ORIGINS", validation_alias="cors_origins")
    app_env: Literal["dev", "prod"] = Field("prod", env="APP_ENV")
    strict_cors: bool = Field(False, env="STRICT_CORS")
    redis_url: str | None = Field(None, env="REDIS_URL")
    rate_limit_per_minute: int = Field(30, env="RATE_LIMIT_PER_MINUTE")
    rate_limit_cleanup_minutes: int = Field(10, env="RATE_LIMIT_CLEANUP_MINUTES")
    time_overrun_reason_threshold: float = Field(1.2, env="TIME_OVERRUN_REASON_THRESHOLD")
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
    email_mode: Literal["off", "sendgrid", "smtp"] = Field("off", env="EMAIL_MODE")
    email_from: str | None = Field(None, env="EMAIL_FROM")
    email_from_name: str | None = Field(None, env="EMAIL_FROM_NAME")
    sendgrid_api_key: str | None = Field(None, env="SENDGRID_API_KEY")
    smtp_host: str | None = Field(None, env="SMTP_HOST")
    smtp_port: int | None = Field(None, env="SMTP_PORT")
    smtp_username: str | None = Field(None, env="SMTP_USERNAME")
    smtp_password: str | None = Field(None, env="SMTP_PASSWORD")
    smtp_use_tls: bool = Field(True, env="SMTP_USE_TLS")
    owner_basic_username: str | None = Field(None, env="OWNER_BASIC_USERNAME")
    owner_basic_password: str | None = Field(None, env="OWNER_BASIC_PASSWORD")
    admin_basic_username: str | None = Field(None, env="ADMIN_BASIC_USERNAME")
    admin_basic_password: str | None = Field(None, env="ADMIN_BASIC_PASSWORD")
    dispatcher_basic_username: str | None = Field(None, env="DISPATCHER_BASIC_USERNAME")
    dispatcher_basic_password: str | None = Field(None, env="DISPATCHER_BASIC_PASSWORD")
    accountant_basic_username: str | None = Field(None, env="ACCOUNTANT_BASIC_USERNAME")
    accountant_basic_password: str | None = Field(None, env="ACCOUNTANT_BASIC_PASSWORD")
    viewer_basic_username: str | None = Field(None, env="VIEWER_BASIC_USERNAME")
    viewer_basic_password: str | None = Field(None, env="VIEWER_BASIC_PASSWORD")
    worker_basic_username: str | None = Field(None, env="WORKER_BASIC_USERNAME")
    worker_basic_password: str | None = Field(None, env="WORKER_BASIC_PASSWORD")
    worker_team_id: int = Field(1, env="WORKER_TEAM_ID")
    legacy_basic_auth_enabled: bool = Field(True, env="LEGACY_BASIC_AUTH_ENABLED")
    auth_secret_key: str = Field("dev-auth-secret", env="AUTH_SECRET_KEY")
    auth_token_ttl_minutes: int = Field(60 * 24, env="AUTH_TOKEN_TTL_MINUTES")
    admin_notification_email: str | None = Field(None, env="ADMIN_NOTIFICATION_EMAIL")
    public_base_url: str | None = Field(None, env="PUBLIC_BASE_URL")
    invoice_public_token_secret: str | None = Field(None, env="INVOICE_PUBLIC_TOKEN_SECRET")
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
    captcha_mode: Literal["off", "turnstile"] = Field("off", env="CAPTCHA_MODE")
    turnstile_secret_key: str | None = Field(None, env="TURNSTILE_SECRET_KEY")
    retention_chat_days: int = Field(30, env="RETENTION_CHAT_DAYS")
    retention_lead_days: int = Field(365, env="RETENTION_LEAD_DAYS")
    retention_enable_leads: bool = Field(False, env="RETENTION_ENABLE_LEADS")
    default_worker_hourly_rate_cents: int = Field(2500, env="DEFAULT_WORKER_HOURLY_RATE_CENTS")
    slot_provider_mode: Literal["stub", "db"] = Field("db", env="SLOT_PROVIDER_MODE")
    stripe_secret_key: str | None = Field(None, env="STRIPE_SECRET_KEY")
    stripe_webhook_secret: str | None = Field(None, env="STRIPE_WEBHOOK_SECRET")
    stripe_success_url: str = Field("http://localhost:3000/deposit-success?session_id={CHECKOUT_SESSION_ID}", env="STRIPE_SUCCESS_URL")
    stripe_cancel_url: str = Field("http://localhost:3000/deposit-cancelled", env="STRIPE_CANCEL_URL")
    stripe_invoice_success_url: str = Field(
        "http://localhost:3000/invoice-success?session_id={CHECKOUT_SESSION_ID}",
        env="STRIPE_INVOICE_SUCCESS_URL",
    )
    stripe_invoice_cancel_url: str = Field(
        "http://localhost:3000/invoice-cancelled",
        env="STRIPE_INVOICE_CANCEL_URL",
    )
    stripe_billing_success_url: str = Field(
        "http://localhost:3000/billing/success?session_id={CHECKOUT_SESSION_ID}",
        env="STRIPE_BILLING_SUCCESS_URL",
    )
    stripe_billing_cancel_url: str = Field(
        "http://localhost:3000/billing/cancelled",
        env="STRIPE_BILLING_CANCEL_URL",
    )
    stripe_billing_portal_return_url: str = Field(
        "http://localhost:3000/billing",
        env="STRIPE_BILLING_PORTAL_RETURN_URL",
    )
    client_portal_secret: str = Field("dev-client-portal-secret", env="CLIENT_PORTAL_SECRET")
    worker_portal_secret: str | None = Field(None, env="WORKER_PORTAL_SECRET")
    client_portal_token_ttl_minutes: int = Field(30, env="CLIENT_PORTAL_TOKEN_TTL_MINUTES")
    client_portal_base_url: str | None = Field(None, env="CLIENT_PORTAL_BASE_URL")
    deposit_percent: float = Field(0.25, env="DEPOSIT_PERCENT")
    deposit_currency: str = Field("cad", env="DEPOSIT_CURRENCY")
    order_upload_root: str = Field("var/uploads/orders", env="ORDER_UPLOAD_ROOT")
    order_photo_max_bytes: int = Field(10 * 1024 * 1024, env="ORDER_PHOTO_MAX_BYTES")
    order_photo_allowed_mimes_raw: str = Field(
        "image/jpeg,image/png,image/webp", env="ORDER_PHOTO_ALLOWED_MIMES"
    )
    order_storage_backend: Literal["local", "s3", "memory"] = Field("local", env="ORDER_STORAGE_BACKEND")
    order_photo_signed_url_ttl_seconds: int = Field(600, env="ORDER_PHOTO_SIGNED_URL_TTL")
    order_photo_signing_secret: str | None = Field(None, env="ORDER_PHOTO_SIGNING_SECRET")
    s3_endpoint: str | None = Field(None, env="S3_ENDPOINT")
    s3_bucket: str | None = Field(None, env="S3_BUCKET")
    s3_access_key: str | None = Field(None, env="S3_ACCESS_KEY")
    s3_secret_key: str | None = Field(None, env="S3_SECRET_KEY")
    s3_region: str | None = Field(None, env="S3_REGION")
    testing: bool = Field(False, env="TESTING")
    deposits_enabled: bool = Field(True, env="DEPOSITS_ENABLED")
    metrics_enabled: bool = Field(True, env="METRICS_ENABLED")
    metrics_token: str | None = Field(None, env="METRICS_TOKEN")
    job_heartbeat_required: bool = Field(False, env="JOB_HEARTBEAT_REQUIRED")
    job_heartbeat_ttl_seconds: int = Field(300, env="JOB_HEARTBEAT_TTL_SECONDS")
    default_org_id: uuid.UUID = Field(
        uuid.UUID("00000000-0000-0000-0000-000000000001"), env="DEFAULT_ORG_ID"
    )

    model_config = SettingsConfigDict(env_file=".env", enable_decoding=False)

    @field_validator(
        "cors_origins_raw",
        "trusted_proxy_ips_raw",
        "trusted_proxy_cidrs_raw",
        "export_webhook_allowed_hosts_raw",
        "order_photo_allowed_mimes_raw",
        mode="before",
    )
    @classmethod
    def normalize_list_raw(cls, value: object) -> str | None:
        return cls._normalize_raw_list(value)

    @field_validator("deposit_percent")
    @classmethod
    def validate_deposit_percent(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("deposit_percent must be between 0 and 1")
        return value

    @field_validator("time_overrun_reason_threshold")
    @classmethod
    def validate_time_threshold(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("time_overrun_reason_threshold must be positive")
        return value

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

    @property
    def email_sender(self) -> str | None:
        return self.email_from

    @email_sender.setter
    def email_sender(self, value: str | None) -> None:
        self.email_from = value

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

    @property
    def order_photo_allowed_mimes(self) -> list[str]:
        parsed = self._parse_list(self.order_photo_allowed_mimes_raw)
        if parsed:
            return parsed
        return ["image/jpeg", "image/png", "image/webp"]


settings = Settings()
