import json
import uuid
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "cleaning-economy-bot"
    cors_origins_raw: str | None = Field(None, validation_alias="cors_origins")
    app_env: Literal["dev", "prod"] = Field("prod")
    strict_cors: bool = Field(False)
    redis_url: str | None = Field(None)
    rate_limit_per_minute: int = Field(30)
    rate_limit_cleanup_minutes: int = Field(10)
    rate_limit_fail_open_seconds: int = Field(300)
    rate_limit_redis_probe_seconds: float = Field(5.0)
    time_overrun_reason_threshold: float = Field(1.2)
    trust_proxy_headers: bool = Field(False)
    trusted_proxy_ips_raw: str | None = Field(None, validation_alias="trusted_proxy_ips")
    trusted_proxy_cidrs_raw: str | None = Field(None, validation_alias="trusted_proxy_cidrs")
    pricing_config_path: str = Field("pricing/economy_v1.json")
    database_url: str = Field("postgresql+psycopg://postgres:postgres@postgres:5432/cleaning")
    database_pool_size: int = Field(5)
    database_max_overflow: int = Field(5)
    database_pool_timeout_seconds: float = Field(30.0)
    database_statement_timeout_ms: int = Field(5000)
    email_mode: Literal["off", "sendgrid", "smtp"] = Field("off")
    email_from: str | None = Field(None)
    email_from_name: str | None = Field(None)
    sendgrid_api_key: str | None = Field(None)
    smtp_host: str | None = Field(None)
    smtp_port: int | None = Field(None)
    smtp_username: str | None = Field(None)
    smtp_password: str | None = Field(None)
    smtp_use_tls: bool = Field(True)
    owner_basic_username: str | None = Field(None)
    owner_basic_password: str | None = Field(None)
    admin_basic_username: str | None = Field(None)
    admin_basic_password: str | None = Field(None)
    dispatcher_basic_username: str | None = Field(None)
    dispatcher_basic_password: str | None = Field(None)
    accountant_basic_username: str | None = Field(None)
    accountant_basic_password: str | None = Field(None)
    viewer_basic_username: str | None = Field(None)
    viewer_basic_password: str | None = Field(None)
    worker_basic_username: str | None = Field(None)
    worker_basic_password: str | None = Field(None)
    worker_team_id: int = Field(1)
    legacy_basic_auth_enabled: bool = Field(True)
    auth_secret_key: str = Field("dev-auth-secret")
    auth_token_ttl_minutes: int = Field(60 * 24)
    auth_access_token_ttl_minutes: int = Field(
        15, validation_alias=AliasChoices("auth_access_token_ttl_minutes", "auth_token_ttl_minutes")
    )
    auth_refresh_token_ttl_minutes: int = Field(60 * 24 * 14)
    auth_session_ttl_minutes: int = Field(60 * 24)
    password_hash_scheme: Literal["argon2id", "bcrypt"] = Field("argon2id")
    password_hash_argon2_time_cost: int = Field(3)
    password_hash_argon2_memory_cost: int = Field(65536)
    password_hash_argon2_parallelism: int = Field(2)
    password_hash_bcrypt_cost: int = Field(12)
    session_ttl_minutes_worker: int = Field(60 * 12)
    session_ttl_minutes_client: int = Field(60 * 24 * 7)
    session_rotation_grace_minutes: int = Field(5)
    admin_notification_email: str | None = Field(None)
    public_base_url: str | None = Field(None)
    invoice_public_token_secret: str | None = Field(None)
    export_mode: Literal["off", "webhook", "sheets"] = Field("off")
    export_webhook_url: str | None = Field(None)
    export_webhook_timeout_seconds: int = Field(5)
    export_webhook_max_retries: int = Field(3)
    export_webhook_backoff_seconds: float = Field(1.0)
    export_webhook_allowed_hosts_raw: str | None = Field(None, validation_alias="export_webhook_allowed_hosts")
    export_webhook_allow_http: bool = Field(False)
    export_webhook_block_private_ips: bool = Field(True)
    captcha_mode: Literal["off", "turnstile"] = Field("off")
    turnstile_secret_key: str | None = Field(None)
    retention_chat_days: int = Field(30)
    retention_lead_days: int = Field(365)
    retention_enable_leads: bool = Field(False)
    default_worker_hourly_rate_cents: int = Field(2500)
    slot_provider_mode: Literal["stub", "db"] = Field("db")
    stripe_secret_key: str | None = Field(None)
    stripe_webhook_secret: str | None = Field(None)
    stripe_success_url: str = Field("http://localhost:3000/deposit-success?session_id={CHECKOUT_SESSION_ID}")
    stripe_cancel_url: str = Field("http://localhost:3000/deposit-cancelled")
    stripe_invoice_success_url: str = Field("http://localhost:3000/invoice-success?session_id={CHECKOUT_SESSION_ID}")
    stripe_invoice_cancel_url: str = Field("http://localhost:3000/invoice-cancelled")
    stripe_billing_success_url: str = Field("http://localhost:3000/billing/success?session_id={CHECKOUT_SESSION_ID}")
    stripe_billing_cancel_url: str = Field("http://localhost:3000/billing/cancelled")
    stripe_billing_portal_return_url: str = Field("http://localhost:3000/billing")
    stripe_circuit_failure_threshold: int = Field(5)
    stripe_circuit_recovery_seconds: float = Field(30.0)
    stripe_circuit_window_seconds: float = Field(60.0)
    stripe_circuit_half_open_max_calls: int = Field(2)
    client_portal_secret: str = Field("dev-client-portal-secret")
    worker_portal_secret: str | None = Field(None)
    client_portal_token_ttl_minutes: int = Field(30)
    client_portal_base_url: str | None = Field(None)
    deposit_percent: float = Field(0.25)
    deposit_currency: str = Field("cad")
    order_upload_root: str = Field("var/uploads/orders")
    order_photo_max_bytes: int = Field(10 * 1024 * 1024)
    order_photo_allowed_mimes_raw: str = Field("image/jpeg,image/png,image/webp")
    order_storage_backend: Literal["local", "s3", "memory", "r2", "cloudflare_r2"] = Field("local")
    order_photo_signed_url_ttl_seconds: int = Field(600)
    order_photo_signing_secret: str | None = Field(None)
    s3_endpoint: str | None = Field(None)
    s3_bucket: str | None = Field(None)
    s3_access_key: str | None = Field(None)
    s3_secret_key: str | None = Field(None)
    s3_region: str | None = Field(None)
    r2_endpoint: str | None = Field(None)
    r2_bucket: str | None = Field(None)
    r2_access_key: str | None = Field(None)
    r2_secret_key: str | None = Field(None)
    r2_region: str | None = Field("auto")
    r2_public_base_url: str | None = Field(None)
    s3_connect_timeout_seconds: float = Field(3.0)
    s3_read_timeout_seconds: float = Field(10.0)
    s3_max_attempts: int = Field(4)
    s3_circuit_failure_threshold: int = Field(4)
    s3_circuit_recovery_seconds: float = Field(20.0)
    s3_circuit_window_seconds: float = Field(60.0)
    storage_delete_retry_interval_seconds: int = Field(30)
    storage_delete_max_attempts: int = Field(5)
    storage_delete_batch_size: int = Field(50)
    testing: bool = Field(False)
    deposits_enabled: bool = Field(True)
    metrics_enabled: bool = Field(True)
    metrics_token: str | None = Field(None)
    job_heartbeat_required: bool = Field(False)
    job_heartbeat_ttl_seconds: int = Field(300)
    email_max_retries: int = Field(3)
    email_retry_backoff_seconds: float = Field(60.0)
    email_http_max_attempts: int = Field(3)
    email_http_backoff_seconds: float = Field(1.0)
    email_http_backoff_max_seconds: float = Field(8.0)
    email_timeout_seconds: float = Field(10.0)
    smtp_timeout_seconds: float = Field(10.0)
    email_circuit_failure_threshold: int = Field(5)
    email_circuit_recovery_seconds: float = Field(30.0)
    email_unsubscribe_secret: str | None = Field(None)
    email_unsubscribe_ttl_minutes: int = Field(7 * 24 * 60)
    default_org_id: uuid.UUID = Field(uuid.UUID("00000000-0000-0000-0000-000000000001"))

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
