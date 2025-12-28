# Bot v1 manual test checklist

Run these quick smoke tests before release to confirm the bot always responds and escalates properly.

## Setup
- Start the API (`make run-api` or `uvicorn app.main:app --reload`).
- Use a REST client pointing at `http://localhost:8000/api`.

## Flows
1. **Price flow**
   - POST `/bot/session` to get a `conversationId`.
   - POST `/bot/message` with "I need a price quote for a deep clean".
   - Expect intent `price`, progress counters, and quick replies asking for service details.

2. **Booking flow**
   - POST `/bot/message` with details like "Book cleaning for 2 bed 2 bath tomorrow evening in Brooklyn".
   - Expect captured entities in the response summary and a follow-up question for the next missing detail.

3. **FAQ**
   - POST `/bot/message` with "What do you include in a standard clean?".
   - Expect a direct FAQ answer (no handoff) and at most three answer lines.

4. **Complaint handoff**
   - POST `/bot/message` with "I have a complaint about my last service".
   - Expect a handoff note, empty quick replies, and a new case under `/api/cases` containing the last 10 messages.

5. **Low-confidence handoff**
   - POST `/bot/message` with nonsensical text (e.g., "???" or "abc" repeated).
   - Expect a handoff note and a case with `reason=low_confidence`.

## Case payload expectations
- Every handoff case payload includes:
  - Last 10 messages with role and timestamps.
  - Extracted entities.
  - Summary/progress snapshot.
  - Reason and suggested next action for the human teammate.
