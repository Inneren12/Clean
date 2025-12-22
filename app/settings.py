from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "cleaning-economy-bot"
    cors_origins: List[str] = Field(default_factory=list, env="CORS_ORIGINS")
    rate_limit_per_minute: int = Field(30, env="RATE_LIMIT_PER_MINUTE")
    pricing_config_path: str = Field("pricing/economy_v1.json", env="PRICING_CONFIG_PATH")
    database_url: str = Field(
        "postgresql+psycopg://postgres:postgres@postgres:5432/cleaning",
        env="DATABASE_URL",
    )

    model_config = SettingsConfigDict(env_file=".env")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            origins = [origin.strip() for origin in value.split(",")]
            return [origin for origin in origins if origin]
        if isinstance(value, list):
            return value
        return [str(value)]


settings = Settings()
