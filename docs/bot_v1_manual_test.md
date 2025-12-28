# Bot v1 Manual Test Checklist (Handoff + FAQ)

Use this checklist to verify handoff and FAQ behaviors stay deterministic and do not derail FSM booking/price flows.

## Handoff rules
- **Complaint/Human:** Send messages like "I have a complaint" or "Need a human". Expect `fsmStep=handoff_check`, conversation status `handed_off`, and a case created with a human-connection reply.
- **Progressing FSM:** While on ask_* or confirm_lead with quick replies present, send ambiguous text. Expect no handoff; the bot should keep the current step and quick replies.
- **Low-confidence short pings:** Send "ok" or "???". Expect a clarification prompt with suggested quick replies (no case created).
- **Reschedule without time:** Say "Reschedule" without a time window. Expect handoff with a scheduling conflict reason and a case snapshot.

## FAQ behavior
- **Keyword matches:** Use `faq: pricing` or `faq: what's included`. Expect formatted FAQ answers (top matches only) with no case creation.
- **No match:** Use `faq: lorem ipsum`. Expect a clarification prompt plus quick replies (`Pricing`, `What's included`, `Booking`, `Human`); no case unless user explicitly asks for a human.
- **During flow:** Ask status/faq mid-booking. Expect the flow step to stay the same with quick replies intact and no handoff.

## Case payload sanity checks
- Verify cases include the last 10 messages (role/text/timestamp/metadata), extracted entities, FSM summary/progress, and the handoff reason/suggested action.
- Confirm metrics increment only when a handoff occurs (not for generic low-confidence clarifications).
