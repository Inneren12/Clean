from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Dict, List, Sequence

from app.bot.nlu.models import Entities, Intent
from app.bot.rules.config import CHECKLISTS, DEFAULT_CHECKLIST, UPSOLD_EXTRAS, ChecklistConfig, UpsellRule
from app.domain.bot.schemas import FsmStep


def _has_keyword(normalized: str, keyword: str) -> bool:
    if keyword.replace(" ", "").replace("-", "").isalnum():
        return bool(re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", normalized))
    return keyword in normalized


@dataclass
class UpsellResult:
    filled_fields: Dict[str, object]
    added: List[str]
    reasons: List[str]


class RulesEngine:
    def __init__(
        self,
        *,
        upsell_rules: Dict[str, UpsellRule] | None = None,
        checklist_overrides: Dict[str, ChecklistConfig] | None = None,
        default_checklist: ChecklistConfig = DEFAULT_CHECKLIST,
    ) -> None:
        self.upsell_rules = upsell_rules or UPSOLD_EXTRAS
        self.checklist_overrides = checklist_overrides or CHECKLISTS
        self.default_checklist = default_checklist

    def apply_upsells(self, message_text: str, filled_fields: Dict[str, object]) -> UpsellResult:
        normalized = message_text.lower()
        extras = set(filled_fields.get("extras", []) or [])
        added: List[str] = []
        reasons: List[str] = []

        for rule in self.upsell_rules.values():
            if any(_has_keyword(normalized, keyword) for keyword in rule.keywords):
                if rule.extra not in extras:
                    extras.add(rule.extra)
                    added.append(rule.extra)
                reasons.append(rule.reason)

        updated_fields = dict(filled_fields)
        if added:
            updated_fields["extras"] = sorted(extras)

        return UpsellResult(filled_fields=updated_fields, added=added, reasons=reasons)

    def _resolve_checklist(self, service_type: str | None) -> ChecklistConfig:
        if service_type and service_type in self.checklist_overrides:
            return self.checklist_overrides[service_type]
        return self.default_checklist

    def steps_for_intent(self, intent: Intent, filled_fields: Dict[str, object], fast_path: bool = False) -> List[FsmStep]:
        checklist = self._resolve_checklist(str(filled_fields.get("service_type")) if filled_fields.get("service_type") else None)
        return checklist.sequence(intent=intent, has_service=bool(filled_fields.get("service_type")), fast_path=fast_path)

    def prep_instructions(self, filled_fields: Dict[str, object]) -> Sequence[str]:
        service_type = str(filled_fields.get("service_type")) if filled_fields.get("service_type") else None
        checklist = self._resolve_checklist(service_type)
        return checklist.prep_instructions

    def is_fast_path(self, entities: Entities) -> bool:
        has_service = bool(entities.service_type)
        has_size = bool(entities.beds or entities.baths or entities.square_feet or entities.square_meters or entities.size_label)
        return has_service and has_size
