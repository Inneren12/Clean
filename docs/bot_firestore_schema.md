# Bot Firestore schema (v1)

This document captures the initial Firestore layout for the bot, leads, handoff, FAQ, and pricing rules. The goal is to keep the layout simple and extensible so the rule-based bot can capture state while remaining compatible with authenticated and anonymous users.

## Collections

### `conversations/{conversationId}`

- `channel`: `web | telegram | sms`
- `userId`: authenticated UID (optional)
- `anonId`: fallback identifier when auth is not present
- `status`: `active | completed | handed_off`
- `state`
  - `currentIntent`: latest intent label
  - `fsmStep`: textual FSM step for the conversation
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

## Security rules (MVP)

- Authenticated users can read and write only their own `conversations` and `leads`.
- Admins (checked via a custom claim `role == 'admin'`) can read `cases`, `leads`, `pricing_rules`, and `faq` entries.
- Anonymous users identified by `anonId` may write new `conversations` and append messages; read access is restricted to their own conversation documents via `anonId` matching.
- `cases`, `pricing_rules`, and `faq` are write-protected except for admins.
- The backend models use snake_case field names (`user_id`, `anon_id`), and the Firestore rules allow either snake_case or camelCase identifiers to keep compatibility.

The concrete rule file lives at `firebase/firestore.rules`.
