import asyncio

import sqlalchemy as sa

from app.domain.bookings.db_models import Team
from app.domain.bookings.service import DEFAULT_TEAM_NAME, ensure_default_team


def test_ensure_default_team_created_when_missing(async_session_maker):
    async def _run() -> None:
        async with async_session_maker() as session:
            await session.execute(sa.delete(Team))
            await session.commit()

            team = await ensure_default_team(session)
            teams = (await session.execute(sa.select(Team))).scalars().all()

            assert team.name == DEFAULT_TEAM_NAME
            assert len(teams) == 1
            assert teams[0].name == DEFAULT_TEAM_NAME

    asyncio.run(_run())


def test_ensure_default_team_handles_competing_inserts(async_session_maker):
    async def _run() -> None:
        async with async_session_maker() as cleanup:
            await cleanup.execute(sa.delete(Team))
            await cleanup.commit()

        async with async_session_maker() as session_one, async_session_maker() as session_two:
            team_one = await ensure_default_team(session_one)
            team_two = await ensure_default_team(session_two)

            await session_one.commit()
            await session_two.commit()

        async with async_session_maker() as verify:
            teams = (await verify.execute(sa.select(Team))).scalars().all()

            assert len(teams) == 1
            assert teams[0].name == DEFAULT_TEAM_NAME
            assert {team_one.team_id, team_two.team_id} == {teams[0].team_id}

    asyncio.run(_run())
