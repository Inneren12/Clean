# Bot Firestore schema (v1 draft)

> Status: **DRAFT** — the current implementation uses an in-memory `InMemoryBotStore` (see `docs/bot_storage.md`). Firestore collections and rules will be wired in a future sprint once the Firestore-backed store exists.

This document captures the intended Firestore layout for the bot, leads, handoff, FAQ, and pricing rules. The goal is to keep the layout simple and extensible so the rule-based bot can capture state while remaining compatible with authenticated and anonymous users.

## Collections

### `conversations/{conversationId}`

- `channel`: `web | telegram | sms`
- `userId`: authenticated UID (optional)
- `anonId`: fallback identifier when auth is not present
- `status`: `active | completed | handed_off`
- `state`
  - `currentIntent`: latest intent label (enum-backed)
  - `fsmStep`: textual FSM step for the conversation (enum-backed)
  - `filledFields`: map of structured data gathered so far
  - `confidence`: numeric confidence for the current intent
- `createdAt`: timestamp
- `updatedAt`: timestamp

#### `conversations/{conversationId}/messages/{messageId}`

- `role`: `user | bot | system`
- `text`: message text
- `intent`: optional intent per message
- `confidence`: optional confidence
- `extractedEntities`: map of entities pulled from the text
- `createdAt`: timestamp

### `leads/{leadId}`

- `serviceType`
- `propertyType`
- `size`
- `condition`
- `extras`: array
- `area`
- `preferredTimeWindow`
- `contact`: `{ phone, email }`
- `priceEstimate`: `{ min, max, currency }`
- `durationEstimateMin`
- `sourceConversationId`
- `status`
- `createdAt`

### `cases/{caseId}`

- `reason`: `low_confidence | complaint | custom_request | conflict`
- `summary`
- `payload`: conversation snapshot / context
- `status`
- `sourceConversationId`
- `createdAt`

### `faq/{faqId}`

- `title`
- `tags`: array
- `answerMarkdown`
- `updatedAt`

### `pricing_rules/{ruleId}`

- `version`
- `baseRates`
- `sizeMultipliers`
- `conditionMultipliers`
- `extrasPrices`
- `zoneAdjustments`
- `updatedAt`

## Security rules (draft)

- The ruleset is **not active** yet; see `firebase/firestore.rules` for the current draft.
- Authenticated users will read/write only their own `conversations` and `leads`.
- Admins (custom claim `role == 'admin'`) will read/write `cases`, `pricing_rules`, and `faq` entries.
- Anonymous users identified by `anonId` may create `conversations` and append messages; reads will be limited to their own conversations.
- `cases`, `pricing_rules`, and `faq` remain admin-only writes.
- External API naming uses `camelCase` while internal Python remains `snake_case`; Firestore document fields should align with the external `camelCase` names once the Firestore store is introduced.

The concrete draft rule file lives at `firebase/firestore.rules` and is intentionally conservative until the Firestore store is implemented.
