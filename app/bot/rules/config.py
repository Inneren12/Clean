from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from app.bot.nlu.models import Intent
from app.domain.bot.schemas import FsmStep


@dataclass
class UpsellRule:
    name: str
    keywords: Sequence[str]
    extra: str
    reason: str


UPSOLD_EXTRAS: Dict[str, UpsellRule] = {
    "windows": UpsellRule(
        name="windows",
        keywords=["window", "windows"],
        extra="windows",
        reason="Added windows for glass and sill detailing",
    ),
    "oven": UpsellRule(
        name="oven",
        keywords=["oven"],
        extra="oven",
        reason="Oven cleaning keeps appliances hygienic",
    ),
    "fridge": UpsellRule(
        name="fridge",
        keywords=["fridge", "refrigerator"],
        extra="fridge",
        reason="Fridge interior wipe-down prevents odors",
    ),
    "pets": UpsellRule(
        name="pets",
        keywords=["pet", "pets", "dog", "dogs", "cat", "cats"],
        extra="pets",
        reason="Pet hair/odor treatment included",
    ),
}


@dataclass
class ChecklistConfig:
    service_type: str | None
    steps: Sequence[FsmStep]
    prep_instructions: Sequence[str] = field(default_factory=list)
    fast_path_steps: Sequence[FsmStep] | None = None

    def sequence(self, intent: Intent, has_service: bool, fast_path: bool = False) -> list[FsmStep]:
        chosen_steps = list(self.fast_path_steps) if fast_path and self.fast_path_steps else list(self.steps)

        if not has_service and FsmStep.ask_service_type not in chosen_steps:
            chosen_steps = [FsmStep.ask_service_type, *chosen_steps]

        if intent in {Intent.price, Intent.scope}:
            chosen_steps = [
                step
                for step in chosen_steps
                if step not in {FsmStep.ask_contact, FsmStep.confirm_lead}
            ]

        return list(chosen_steps)


DEFAULT_CHECKLIST = ChecklistConfig(
    service_type=None,
    steps=[
        FsmStep.ask_service_type,
        FsmStep.ask_property_type,
        FsmStep.ask_size,
        FsmStep.ask_condition,
        FsmStep.ask_extras,
        FsmStep.ask_area,
        FsmStep.ask_preferred_time,
        FsmStep.ask_contact,
        FsmStep.confirm_lead,
    ],
    fast_path_steps=[
        FsmStep.ask_area,
        FsmStep.ask_preferred_time,
        FsmStep.ask_contact,
        FsmStep.confirm_lead,
    ],
)


CHECKLISTS: Dict[str, ChecklistConfig] = {
    "move_out": ChecklistConfig(
        service_type="move_out",
        steps=[
            FsmStep.ask_service_type,
            FsmStep.ask_property_type,
            FsmStep.ask_size,
            FsmStep.ask_area,
            FsmStep.ask_preferred_time,
            FsmStep.ask_contact,
            FsmStep.confirm_lead,
        ],
        prep_instructions=[
            "Empty fridge/freezer and cupboards",
            "Bag personal items and trash",
            "Unlock cabinets/closets",
        ],
        fast_path_steps=[
            FsmStep.ask_area,
            FsmStep.ask_preferred_time,
            FsmStep.ask_contact,
            FsmStep.confirm_lead,
        ],
    ),
    "deep_clean": ChecklistConfig(
        service_type="deep_clean",
        steps=[
            FsmStep.ask_service_type,
            FsmStep.ask_property_type,
            FsmStep.ask_size,
            FsmStep.ask_area,
            FsmStep.ask_preferred_time,
            FsmStep.ask_contact,
            FsmStep.confirm_lead,
        ],
        prep_instructions=[
            "Tidy countertops for detailed scrubbing",
            "Secure pets during the visit",
            "Clear sink and dish rack",
        ],
        fast_path_steps=[
            FsmStep.ask_area,
            FsmStep.ask_preferred_time,
            FsmStep.ask_contact,
            FsmStep.confirm_lead,
        ],
    ),
    "post_renovation": ChecklistConfig(
        service_type="post_renovation",
        steps=[
            FsmStep.ask_service_type,
            FsmStep.ask_property_type,
            FsmStep.ask_size,
            FsmStep.ask_area,
            FsmStep.ask_preferred_time,
            FsmStep.ask_contact,
            FsmStep.confirm_lead,
        ],
        prep_instructions=[
            "Remove construction debris",
            "Confirm utilities are on",
            "Protect delicate fixtures",
        ],
        fast_path_steps=[
            FsmStep.ask_area,
            FsmStep.ask_preferred_time,
            FsmStep.ask_contact,
            FsmStep.confirm_lead,
        ],
    ),
}
