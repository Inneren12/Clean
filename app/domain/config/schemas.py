from pydantic import BaseModel


class FeatureFlag(BaseModel):
    key: str
    enabled: bool
    description: str
    rollout: str | None = None


class FeatureFlagResponse(BaseModel):
    flags: list[FeatureFlag]


class ConfigEntry(BaseModel):
    key: str
    value: object | None
    redacted: bool = False
    source: str = "settings"


class ConfigViewerResponse(BaseModel):
    entries: list[ConfigEntry]
