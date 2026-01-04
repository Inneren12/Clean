"""Tests for enhanced global search v2."""

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.postgres, pytest.mark.smoke]


@pytest.mark.asyncio
async def test_search_returns_results(async_client: AsyncClient, admin_credentials):
    """Test that search returns results."""
    response = await async_client.get(
        "/v1/admin/search?q=test",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_search_includes_relevance_score(async_client: AsyncClient, admin_credentials):
    """Test that search results include relevance scores."""
    response = await async_client.get(
        "/v1/admin/search?q=test",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    for item in data:
        assert "relevance_score" in item
        assert isinstance(item["relevance_score"], int)
        assert item["relevance_score"] >= 0


@pytest.mark.asyncio
async def test_search_includes_quick_actions(async_client: AsyncClient, admin_credentials):
    """Test that search results include quick actions."""
    response = await async_client.get(
        "/v1/admin/search?q=test",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    for item in data:
        assert "quick_actions" in item
        assert isinstance(item["quick_actions"], list)


@pytest.mark.asyncio
async def test_search_empty_query(async_client: AsyncClient, admin_credentials):
    """Test search with empty query returns empty results."""
    response = await async_client.get(
        "/v1/admin/search?q=",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    assert data == []


@pytest.mark.asyncio
async def test_search_respects_limit(async_client: AsyncClient, admin_credentials):
    """Test search respects limit parameter."""
    response = await async_client.get(
        "/v1/admin/search?q=test&limit=5",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) <= 5


@pytest.mark.asyncio
async def test_search_requires_auth(async_client: AsyncClient):
    """Test that search requires authentication."""
    response = await async_client.get("/v1/admin/search?q=test")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_search_result_structure(async_client: AsyncClient, admin_credentials):
    """Test search result has expected structure."""
    response = await async_client.get(
        "/v1/admin/search?q=test&limit=1",
        auth=admin_credentials,
    )
    assert response.status_code == 200
    data = response.json()
    if data:
        item = data[0]
        assert "kind" in item
        assert "ref" in item
        assert "label" in item
        assert "created_at" in item
        assert "relevance_score" in item
        assert "quick_actions" in item
