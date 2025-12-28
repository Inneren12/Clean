"""
S2-C Flow Orchestrator: Price → Confirm → Contact → Created

Manages the UX flow stages with smart microcopy, editable summary, and fallback handling.
"""
from typing import Optional, Tuple, List
from enum import Enum

from app.domain.chat.models import (
    ChatTurnResponse,
    ParsedFields,
    Intent,
    Choice,
    ChoicesConfig,
    StepInfo,
    SummaryField,
    SummaryPatch,
    UIHint,
)
from app.domain.pricing.models import EstimateResponse, CleaningType, Frequency


class FlowStage(str, Enum):
    """Flow stages for S2-C UX flow."""
    COLLECT = "collect"  # Collecting property details
    PRICE = "price"  # Showing price estimate
    CONFIRM = "confirm"  # User confirming price
    CONTACT = "contact"  # Collecting contact details
    CREATED = "created"  # Booking created / success


def _get_flow_stage(
    fields: ParsedFields,
    missing_fields: List[str],
    estimate: Optional[EstimateResponse],
    user_confirmed: bool = False,
) -> FlowStage:
    """Determine current flow stage based on conversation state."""
    if missing_fields:
        return FlowStage.COLLECT
    if estimate and not user_confirmed:
        return FlowStage.PRICE
    if user_confirmed:
        return FlowStage.CONTACT
    return FlowStage.COLLECT


def _build_choices_for_collection(
    fields: ParsedFields, missing_fields: List[str]
) -> Optional[ChoicesConfig]:
    """Build chip choices for property detail collection."""
    if not missing_fields:
        return None

    first_missing = missing_fields[0]

    if first_missing == "beds":
        return ChoicesConfig(
            items=[
                Choice(id="1bed", label="1 bedroom", value="1"),
                Choice(id="2bed", label="2 bedrooms", value="2"),
                Choice(id="3bed", label="3 bedrooms", value="3"),
                Choice(id="4bed", label="4+ bedrooms", value="4"),
            ],
            multi_select=False,
            selection_type="chip",
        )

    if first_missing == "baths":
        return ChoicesConfig(
            items=[
                Choice(id="1bath", label="1 bath", value="1"),
                Choice(id="1.5bath", label="1.5 baths", value="1.5"),
                Choice(id="2bath", label="2 baths", value="2"),
                Choice(id="2.5bath", label="2.5+ baths", value="2.5"),
            ],
            multi_select=False,
            selection_type="chip",
        )

    # Cleaning type options
    if first_missing == "cleaning_type":
        return ChoicesConfig(
            items=[
                Choice(id="standard", label="Standard clean", value="standard"),
                Choice(id="deep", label="Deep clean", value="deep"),
                Choice(id="moveout", label="Move-out clean", value="moveout"),
            ],
            multi_select=False,
            selection_type="chip",
        )

    return None


def _build_choices_for_price() -> ChoicesConfig:
    """Build chip choices for price confirmation."""
    return ChoicesConfig(
        items=[
            Choice(id="confirm", label="✓ Confirm price", value="confirm"),
            Choice(id="why", label="Why this price?", value="explain"),
        ],
        multi_select=False,
        selection_type="button",
    )


def _build_summary_patch(
    fields: ParsedFields, stage: FlowStage
) -> Optional[SummaryPatch]:
    """Build editable summary based on current fields and stage."""
    summary_fields = []

    if fields.beds is not None:
        summary_fields.append(
            SummaryField(
                key="beds",
                label="Bedrooms",
                value=fields.beds,
                editable=True,
                field_type="number",
            )
        )

    if fields.baths is not None:
        summary_fields.append(
            SummaryField(
                key="baths",
                label="Bathrooms",
                value=fields.baths,
                editable=True,
                field_type="number",
            )
        )

    if fields.cleaning_type:
        cleaning_type_labels = {
            CleaningType.standard: "Standard",
            CleaningType.deep: "Deep",
            CleaningType.moveout: "Move-out",
        }
        summary_fields.append(
            SummaryField(
                key="cleaning_type",
                label="Type",
                value=cleaning_type_labels.get(fields.cleaning_type, "Standard"),
                editable=True,
                field_type="select",
                options=[
                    Choice(id="standard", label="Standard", value="standard"),
                    Choice(id="deep", label="Deep", value="deep"),
                    Choice(id="moveout", label="Move-out", value="moveout"),
                ],
            )
        )

    # Add-ons
    if fields.add_ons.windows:
        summary_fields.append(
            SummaryField(
                key="windows",
                label="Windows",
                value=True,
                editable=True,
                field_type="boolean",
            )
        )

    if fields.add_ons.fridge:
        summary_fields.append(
            SummaryField(
                key="fridge",
                label="Fridge",
                value=True,
                editable=True,
                field_type="boolean",
            )
        )

    if fields.add_ons.oven:
        summary_fields.append(
            SummaryField(
                key="oven",
                label="Oven",
                value=True,
                editable=True,
                field_type="boolean",
            )
        )

    if fields.heavy_grease:
        summary_fields.append(
            SummaryField(
                key="heavy_grease",
                label="Heavy grease",
                value=True,
                editable=True,
                field_type="boolean",
            )
        )

    if not summary_fields:
        return None

    title = "Your details" if stage == FlowStage.COLLECT else "Review details"
    return SummaryPatch(title=title, fields=summary_fields)


def _build_step_info(stage: FlowStage, missing_fields: List[str]) -> StepInfo:
    """Build step progress information."""
    stage_map = {
        FlowStage.COLLECT: (1, 4, "Property details"),
        FlowStage.PRICE: (2, 4, "Price estimate"),
        FlowStage.CONFIRM: (2, 4, "Confirm price"),
        FlowStage.CONTACT: (3, 4, "Contact info"),
        FlowStage.CREATED: (4, 4, "Complete"),
    }

    current, total, label = stage_map.get(stage, (1, 4, "Details"))
    remaining = len(missing_fields) if stage == FlowStage.COLLECT else 0

    return StepInfo(
        current_step=current,
        total_steps=total,
        step_label=label,
        remaining_questions=remaining if remaining > 0 else None,
    )


def _build_price_explanation(estimate: EstimateResponse) -> str:
    """Build 'Why this price?' explanation with 2-3 bullet reasons."""
    breakdown = estimate.breakdown
    reasons = []

    # Reason 1: Size/Type
    size_msg = f"{breakdown.beds}BR/{breakdown.baths}BA"
    type_msg = breakdown.cleaning_type.value.replace("_", "-")
    reasons.append(f"• {size_msg} {type_msg} clean")

    # Reason 2: Depth/Complexity
    complexity_parts = []
    if breakdown.time_on_site_hours >= 4:
        complexity_parts.append(f"{breakdown.time_on_site_hours:.1f}h job")
    if breakdown.team_size > 1:
        complexity_parts.append(f"{breakdown.team_size}-person team")
    if complexity_parts:
        reasons.append(f"• {', '.join(complexity_parts)}")

    # Reason 3: Extras/Urgency
    extras = []
    if breakdown.add_ons_cost > 0:
        extras.append(f"add-ons ${breakdown.add_ons_cost:.0f}")
    if breakdown.discount_amount > 0:
        extras.append(f"discount -${breakdown.discount_amount:.0f}")
    if extras:
        reasons.append(f"• {', '.join(extras)}")

    return "\n".join(reasons[:3])


def _build_microcopy_for_collection(missing_fields: List[str]) -> str:
    """Build short, friendly question for collection stage."""
    if not missing_fields:
        return "Got it!"

    first_missing = missing_fields[0]

    microcopy = {
        "beds": "Bedrooms?",
        "baths": "Bathrooms?",
        "cleaning_type": "Type of clean?",
    }

    return microcopy.get(first_missing, "Tell me more?")


def _detect_uncertainty(fields: ParsedFields, confidence: float) -> Tuple[bool, Optional[str]]:
    """
    Detect if bot is uncertain and needs clarification.
    Returns: (is_uncertain, clarifying_question)
    """
    # Low confidence from NLU
    if confidence < 0.5:
        # Ask about cleaning type if unclear
        if fields.cleaning_type is None or fields.cleaning_type == CleaningType.standard:
            return True, "Is this a standard, deep, or move-out clean?"

    # Time-related ambiguity (e.g., "Friday morning", "after 6")
    # This would be detected by checking for time mentions without specific booking flow
    # For now, we return False as this is handled in booking stage

    return False, None


def orchestrate_flow(
    fields: ParsedFields,
    missing_fields: List[str],
    estimate: Optional[EstimateResponse],
    confidence: float,
    user_message: str,
) -> Tuple[str, List[str], Optional[ChoicesConfig], StepInfo, Optional[SummaryPatch], UIHint]:
    """
    Orchestrate the S2-C UX flow and build UI contract components.

    Returns: (reply_text, proposed_questions, choices, step_info, summary_patch, ui_hint)
    """
    # Check for user requesting price explanation
    if "why" in user_message.lower() and "price" in user_message.lower():
        if estimate:
            explanation = _build_price_explanation(estimate)
            reply_text = f"Here's why:\n{explanation}"
            choices = _build_choices_for_price()
            stage = FlowStage.PRICE
            step_info = _build_step_info(stage, missing_fields)
            summary_patch = _build_summary_patch(fields, stage)
            ui_hint = UIHint(
                show_summary=True,
                show_confirm=True,
                show_choices=True,
                show_progress=True,
                minimize_text=False,
            )
            return reply_text, [], choices, step_info, summary_patch, ui_hint

    # Check for confirmation
    user_confirmed = "confirm" in user_message.lower() or "yes" in user_message.lower()

    # Determine current stage
    stage = _get_flow_stage(fields, missing_fields, estimate, user_confirmed)

    # Handle uncertainty (ask 1 clarifying question)
    is_uncertain, clarifying_q = _detect_uncertainty(fields, confidence)
    if is_uncertain and clarifying_q and stage == FlowStage.COLLECT:
        choices = _build_choices_for_collection(fields, ["cleaning_type"])
        step_info = _build_step_info(stage, missing_fields)
        summary_patch = _build_summary_patch(fields, stage)
        ui_hint = UIHint(
            show_summary=True,
            show_choices=True,
            show_progress=True,
            minimize_text=True,
        )
        return clarifying_q, [], choices, step_info, summary_patch, ui_hint

    # COLLECT stage
    if stage == FlowStage.COLLECT:
        reply_text = _build_microcopy_for_collection(missing_fields)
        choices = _build_choices_for_collection(fields, missing_fields)
        step_info = _build_step_info(stage, missing_fields)
        summary_patch = _build_summary_patch(fields, stage)
        ui_hint = UIHint(
            show_summary=True,
            show_choices=True,
            show_progress=True,
            minimize_text=True,
        )
        return reply_text, [], choices, step_info, summary_patch, ui_hint

    # PRICE stage
    if stage == FlowStage.PRICE and estimate:
        breakdown = estimate.breakdown
        reply_text = f"${breakdown.total_before_tax:.0f} ({breakdown.time_on_site_hours:.1f}h, {breakdown.team_size} person team)"
        choices = _build_choices_for_price()
        step_info = _build_step_info(stage, missing_fields)
        summary_patch = _build_summary_patch(fields, stage)
        ui_hint = UIHint(
            show_summary=True,
            show_confirm=True,
            show_choices=True,
            show_progress=True,
            minimize_text=True,
        )
        return reply_text, [], choices, step_info, summary_patch, ui_hint

    # CONTACT stage
    if stage == FlowStage.CONTACT or user_confirmed:
        reply_text = "Perfect! Name and phone?"
        step_info = _build_step_info(FlowStage.CONTACT, [])
        summary_patch = _build_summary_patch(fields, FlowStage.CONTACT)
        ui_hint = UIHint(
            show_summary=True,
            show_progress=True,
            minimize_text=True,
        )
        return reply_text, [], None, step_info, summary_patch, ui_hint

    # Default fallback
    reply_text = "Got it! Tell me more."
    step_info = _build_step_info(FlowStage.COLLECT, missing_fields)
    summary_patch = _build_summary_patch(fields, FlowStage.COLLECT)
    ui_hint = UIHint(show_summary=True, show_progress=True)
    return reply_text, [], None, step_info, summary_patch, ui_hint
