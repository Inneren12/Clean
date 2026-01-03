from app.domain.outbox.service import OutboxAdapters, process_outbox
from app.infra.email import EmailAdapter
from app.settings import settings


async def run_outbox_delivery(session, adapter: EmailAdapter | None) -> dict[str, int]:
    adapters = OutboxAdapters(email_adapter=adapter)
    return await process_outbox(session, adapters, limit=settings.job_outbox_batch_size)
