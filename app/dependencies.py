from functools import lru_cache

from fastapi import Request

from app.domain.pricing.config_loader import PricingConfig, load_pricing_config
from app.infra.bot_store import BotStore, InMemoryBotStore
from app.infra.db import get_db_session
from app.settings import settings


@lru_cache
def get_pricing_config() -> PricingConfig:
    return load_pricing_config(settings.pricing_config_path)


def get_bot_store(request: Request) -> BotStore:
    store = getattr(request.app.state, "bot_store", None)
    if store is None:
        store = InMemoryBotStore()
        request.app.state.bot_store = store
    return store


