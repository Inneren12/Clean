from app.domain.chat.models import Intent


INTENT_KEYWORDS = {
    Intent.quote: ["quote", "estimate", "price", "cost"],
    Intent.book: ["book", "schedule", "appointment", "reserve"],
    Intent.faq: ["hours", "availability", "services", "service area"],
    Intent.change_cancel: ["change", "cancel", "reschedule"],
    Intent.complaint: ["complaint", "refund", "issue", "problem"],
}


def detect_intent(message: str) -> Intent:
    lowered = message.lower()
    for intent, keywords in INTENT_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return intent
    return Intent.other
