# Bot storage abstraction

The bot uses a `BotStore` protocol that supports conversations, messages, leads, and cases. The current runtime and tests rely on the in-memory `InMemoryBotStore`, which keeps all state ephemeral and avoids external dependencies.

A Firestore-backed implementation (`FirestoreBotStore`) will be added in a future sprint. When it is introduced, documents should follow the collections described in `docs/bot_firestore_schema.md`, and the Firestore security rules (`firebase/firestore.rules`) will be revisited. Until then, Firestore assets should be treated as drafts.
