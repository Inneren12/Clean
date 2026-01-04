import uuid
from datetime import datetime, timezone

import pytest

from app.domain.outbox import service as outbox_service
from app.infra.logging import LOG_CONTEXT, clear_log_context, update_log_context
from app.jobs import run


class _DummySession:
    def __init__(self):
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def get(self, model, key):
        return None

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        return None


def _session_factory():
    return _DummySession()


@pytest.mark.anyio
async def test_run_job_clears_log_context():
    clear_log_context()

    async def _runner(session):
        update_log_context(job="dummy", value="one")
        return {"sent": 1}

    await run._run_job("dummy", _session_factory, _runner)
    assert LOG_CONTEXT.get({}) == {}


class _ScalarResult:
    def __init__(self, events):
        self._events = events

    def scalars(self):
        return self

    def all(self):
        return self._events


class _OutboxSession:
    def __init__(self, events):
        self.events = events
        self.committed = False

    async def execute(self, *args, **kwargs):
        return _ScalarResult(self.events)

    async def commit(self):
        self.committed = True


@pytest.mark.anyio
async def test_outbox_processing_clears_log_context(monkeypatch):
    clear_log_context()
    event = outbox_service.OutboxEvent(
        org_id=uuid.uuid4(),
        kind="email",
        payload_json={},
        dedupe_key="d",
        status="pending",
        next_attempt_at=datetime.now(timezone.utc),
    )
    session = _OutboxSession([event])

    async def _fake_deliver(evt, adapters):
        update_log_context(event_id=evt.event_id)
        return True, None

    monkeypatch.setattr(outbox_service, "_deliver_event", _fake_deliver)
    adapters = outbox_service.OutboxAdapters()

    result = await outbox_service.process_outbox(session, adapters)
    assert result["sent"] == 1
    assert LOG_CONTEXT.get({}) == {}
    assert session.committed is True
