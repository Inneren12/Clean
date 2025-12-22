import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass(frozen=True)
class PricingConfig:
    pricing_config_id: str
    pricing_config_version: int
    config_hash: str
    data: Dict[str, Any]


def _canonical_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def load_pricing_config(path: str) -> PricingConfig:
    content = Path(path).read_text(encoding="utf-8")
    data = json.loads(content)
    canonical = _canonical_json(data)
    config_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return PricingConfig(
        pricing_config_id=data["pricing_config_id"],
        pricing_config_version=int(data["pricing_config_version"]),
        config_hash=config_hash,
        data=data,
    )
