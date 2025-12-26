import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.leads.db_models import Lead, ReferralCredit, generate_referral_code

logger = logging.getLogger(__name__)


async def ensure_unique_referral_code(
    session: AsyncSession, lead: Lead, max_attempts: int = 10
) -> None:
    attempts = 0
    while attempts < max_attempts:
        savepoint = await session.begin_nested()
        try:
            await session.flush()
        except IntegrityError as exc:
            await savepoint.rollback()
            message = str(getattr(exc.orig, "diag", None) or exc.orig or exc).lower()
            if "referral" not in message and "code" not in message:
                raise
            lead.referral_code = generate_referral_code()
            attempts += 1
            continue
        else:
            await savepoint.commit()
            return

    raise RuntimeError("Unable to allocate referral code")


async def grant_referral_credit(session: AsyncSession, referred_lead: Lead | None) -> None:
    """Grant a referral credit for the given lead if applicable.

    Idempotent: unique constraint on ``ReferralCredit.referred_lead_id``
    prevents duplicate credits when the booking is confirmed multiple times
    or the webhook is retried.
    """

    if referred_lead is None:
        return

    if not referred_lead.referred_by_code:
        return

    result = await session.execute(
        select(Lead).where(Lead.referral_code == referred_lead.referred_by_code)
    )
    referrer = result.scalar_one_or_none()
    if referrer is None:
        logger.warning(
            "referral_referrer_missing",
            extra={"extra": {"referred_lead_id": referred_lead.lead_id}},
        )
        return

    credit = ReferralCredit(
        referrer_lead_id=referrer.lead_id,
        referred_lead_id=referred_lead.lead_id,
        applied_code=referrer.referral_code,
    )

    savepoint = await session.begin_nested()
    try:
        session.add(credit)
        await session.flush()
    except IntegrityError:
        await savepoint.rollback()
        return
    else:
        await savepoint.commit()
    logger.info("referral_credit_granted", extra={"extra": {"credit_id": credit.credit_id}})
    logger.debug(
        "referral_credit_details",
        extra={
            "extra": {
                "referrer_lead_id": referrer.lead_id,
                "referred_lead_id": referred_lead.lead_id,
            }
        },
    )
