import uuid

import pytest
import sqlalchemy as sa

from app.domain.bookings import db_models as booking_db_models
from app.domain.saas import db_models as saas_db_models
from app.domain.saas.service import ensure_default_org_and_team
from app.settings import settings


@pytest.mark.anyio
async def test_default_org_and_team_seed_idempotent(async_session_maker):
    async with async_session_maker() as session:
        # Start from a clean slate
        await session.execute(sa.delete(booking_db_models.TeamWorkingHours))
        await session.execute(sa.delete(booking_db_models.Team))
        await session.execute(sa.delete(saas_db_models.Organization))
        await session.commit()

        await ensure_default_org_and_team(session)
        await ensure_default_org_and_team(session)
        await session.commit()

        org_count = await session.scalar(sa.select(sa.func.count()).select_from(saas_db_models.Organization))
        team_count = await session.scalar(sa.select(sa.func.count()).select_from(booking_db_models.Team))
        org = await session.get(saas_db_models.Organization, settings.default_org_id)
        team = await session.get(booking_db_models.Team, 1)

        assert org_count == 1
        assert team_count == 1
        assert org is not None
        assert team is not None
        assert team.org_id == settings.default_org_id


@pytest.mark.anyio
async def test_default_seed_handles_preexisting_org(async_session_maker):
    existing_org_id = uuid.uuid4()
    async with async_session_maker() as session:
        await session.execute(sa.delete(booking_db_models.TeamWorkingHours))
        await session.execute(sa.delete(booking_db_models.Team))
        await session.execute(sa.delete(saas_db_models.Organization))
        session.add(saas_db_models.Organization(org_id=existing_org_id, name="Other"))
        await session.commit()

        await ensure_default_org_and_team(session)
        await session.commit()

        org = await session.get(saas_db_models.Organization, settings.default_org_id)
        team = await session.get(booking_db_models.Team, 1)

        assert org is not None
        assert team is not None
        assert team.org_id == settings.default_org_id
