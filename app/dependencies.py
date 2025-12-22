from functools import lru_cache

from app.domain.pricing.config_loader import PricingConfig, load_pricing_config
from app.infra.db import InMemoryChatSessionStore
from app.settings import settings


@lru_cache
def get_pricing_config() -> PricingConfig:
    return load_pricing_config(settings.pricing_config_path)


@lru_cache
def get_chat_store() -> InMemoryChatSessionStore:
    return InMemoryChatSessionStore()
