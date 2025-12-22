from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class ChatSession:
    session_id: str
    state: Dict[str, object]


class InMemoryChatSessionStore:
    def __init__(self) -> None:
        self._sessions: Dict[str, ChatSession] = {}

    def get(self, session_id: str) -> Optional[ChatSession]:
        return self._sessions.get(session_id)

    def upsert(self, session_id: str, state: Dict[str, object]) -> ChatSession:
        session = ChatSession(session_id=session_id, state=state)
        self._sessions[session_id] = session
        return session
