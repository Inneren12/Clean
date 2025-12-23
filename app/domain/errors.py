from dataclasses import dataclass
from typing import List


@dataclass
class DomainError(Exception):
    detail: str
    title: str = "Domain Error"
    type: str = "https://example.com/problems/domain-error"
    errors: List[dict] | None = None
