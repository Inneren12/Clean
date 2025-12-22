from pydantic import BaseSettings, Field
from typing import List


class Settings(BaseSettings):
    app_name: str = "cleaning-economy-bot"
    cors_origins: List[str] = Field(default_factory=list, env="CORS_ORIGINS")
    rate_limit_per_minute: int = Field(30, env="RATE_LIMIT_PER_MINUTE")
    pricing_config_path: str = Field("pricing/economy_v1.json", env="PRICING_CONFIG_PATH")

    class Config:
        env_file = ".env"


settings = Settings()
