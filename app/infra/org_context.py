import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_current_org_id: ContextVar[uuid.UUID | None] = ContextVar("current_org_id", default=None)


def set_current_org_id(org_id: uuid.UUID | None) -> None:
    _current_org_id.set(org_id)


def get_current_org_id() -> uuid.UUID | None:
    try:
        return _current_org_id.get()
    except LookupError:
        return None


@contextmanager
def org_id_context(org_id: uuid.UUID | None) -> Iterator[None]:
    token = _current_org_id.set(org_id)
    try:
        yield
    finally:
        _current_org_id.reset(token)
