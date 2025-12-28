from __future__ import annotations

import asyncio
import time
import uuid
from typing import Dict, List, Optional, Protocol

from app.domain.bot.schemas import (
    CasePayload,
    CaseRecord,
    ConversationCreate,
    ConversationRecord,
    ConversationState,
    ConversationStatus,
    LeadPayload,
    LeadRecord,
    MessagePayload,
    MessageRecord,
)


class BotStore(Protocol):
    async def create_conversation(self, payload: ConversationCreate) -> ConversationRecord: ...

    async def get_conversation(self, conversation_id: str) -> Optional[ConversationRecord]: ...

    async def list_conversations(self) -> List[ConversationRecord]: ...

    async def update_state(
        self,
        conversation_id: str,
        state: ConversationState,
        *,
        status: ConversationStatus | None = None,
    ) -> ConversationRecord: ...

    async def append_message(self, conversation_id: str, payload: MessagePayload) -> MessageRecord: ...

    async def list_messages(self, conversation_id: str) -> List[MessageRecord]: ...

    async def create_lead(self, payload: LeadPayload) -> LeadRecord: ...

    async def create_case(self, payload: CasePayload) -> CaseRecord: ...

    async def list_cases(self) -> List[CaseRecord]: ...


class InMemoryBotStore(BotStore):
    def __init__(self) -> None:
        self._conversations: Dict[str, ConversationRecord] = {}
        self._messages: Dict[str, List[MessageRecord]] = {}
        self._leads: Dict[str, LeadRecord] = {}
        self._cases: Dict[str, CaseRecord] = {}
        self._lock = asyncio.Lock()

    async def create_conversation(self, payload: ConversationCreate) -> ConversationRecord:
        async with self._lock:
            now = time.time()
            conversation_id = str(uuid.uuid4())
            record = ConversationRecord(
                conversation_id=conversation_id,
                channel=payload.channel,
                user_id=payload.user_id,
                anon_id=payload.anon_id,
                state=payload.state,
                status=ConversationStatus.active,
                created_at=now,
                updated_at=now,
            )
            self._conversations[conversation_id] = record
            self._messages[conversation_id] = []
            return record

    async def get_conversation(self, conversation_id: str) -> Optional[ConversationRecord]:
        async with self._lock:
            return self._conversations.get(conversation_id)

    async def list_conversations(self) -> List[ConversationRecord]:
        async with self._lock:
            return list(self._conversations.values())

    async def update_state(
        self,
        conversation_id: str,
        state: ConversationState,
        *,
        status: ConversationStatus | None = None,
    ) -> ConversationRecord:
        async with self._lock:
            record = self._conversations[conversation_id]
            record.state = state
            if status is not None:
                record.status = status
            record.updated_at = time.time()
            self._conversations[conversation_id] = record
            return record

    async def append_message(self, conversation_id: str, payload: MessagePayload) -> MessageRecord:
        async with self._lock:
            message_id = str(uuid.uuid4())
            record = MessageRecord(
                message_id=message_id,
                conversation_id=conversation_id,
                role=payload.role,
                text=payload.text,
                intent=payload.intent,
                confidence=payload.confidence,
                extracted_entities=payload.extracted_entities,
                reasons=payload.reasons,
                metadata=payload.metadata,
                created_at=time.time(),
            )
            self._messages.setdefault(conversation_id, []).append(record)
            return record

    async def list_messages(self, conversation_id: str) -> List[MessageRecord]:
        async with self._lock:
            return list(self._messages.get(conversation_id, []))

    async def create_lead(self, payload: LeadPayload) -> LeadRecord:
        async with self._lock:
            lead_id = str(uuid.uuid4())
            record = LeadRecord(**payload.model_dump(), lead_id=lead_id, created_at=time.time())
            self._leads[lead_id] = record
            return record

    async def create_case(self, payload: CasePayload) -> CaseRecord:
        async with self._lock:
            case_id = str(uuid.uuid4())
            record = CaseRecord(**payload.model_dump(), case_id=case_id, created_at=time.time())
            self._cases[case_id] = record
            return record

    async def list_cases(self) -> List[CaseRecord]:
        async with self._lock:
            return list(self._cases.values())

