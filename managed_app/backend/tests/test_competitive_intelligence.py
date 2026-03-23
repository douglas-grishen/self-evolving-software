"""Tests for the stable Competitive Intelligence app contract."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_competitive_intelligence_statistics_returns_empty_state(client: AsyncClient):
    response = await client.get("/api/v1/competitive-intelligence/statistics")
    assert response.status_code == 200
    assert response.json() == {
        "total_companies": 0,
        "verified_companies": 0,
        "industries_count": 0,
        "countries_count": 0,
        "avg_confidence_score": 0.0,
        "recent_discoveries": 0,
    }


@pytest.mark.asyncio
async def test_competitive_intelligence_search_returns_empty_result_set(client: AsyncClient):
    response = await client.post(
        "/api/v1/competitive-intelligence/companies/search",
        json={"filters": {"search_query": "nike"}, "page": 2, "page_size": 25},
    )
    assert response.status_code == 200
    assert response.json() == {
        "companies": [],
        "total": 0,
        "page": 2,
        "page_size": 25,
    }
