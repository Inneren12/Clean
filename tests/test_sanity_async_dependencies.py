import importlib.metadata

import anyio
import aiosqlite
import httpx
import pytest

EXPECTED_ANYIO = "4.12.0"
EXPECTED_AIOSQLITE = "0.20.0"


@pytest.mark.sanity
def test_anyio_and_aiosqlite_versions_locked():
    # Guard against resolver drift that previously caused CI install failures.
    assert importlib.metadata.version("anyio").startswith(EXPECTED_ANYIO)
    assert importlib.metadata.version("aiosqlite") == EXPECTED_AIOSQLITE


@pytest.mark.sanity
def test_anyio_and_aiosqlite_can_share_event_loop():
    async def round_trip():
        async with aiosqlite.connect(":memory:") as db:
            await db.execute("CREATE TABLE demo (val TEXT)")
            await db.execute("INSERT INTO demo (val) VALUES (?)", ("ok",))
            await db.commit()
            async with db.execute("SELECT val FROM demo") as cursor:
                row = await cursor.fetchone()
            return row[0]

    assert anyio.run(round_trip) == "ok"


@pytest.mark.sanity
def test_httpx_mock_transport_runs_under_anyio():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok"}))

    async def fetch():
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get("http://test/health")
            return response.json()

    assert anyio.run(fetch) == {"status": "ok"}
