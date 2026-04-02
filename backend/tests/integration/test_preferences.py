"""Integration tests for user preferences endpoints."""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.integration
async def test_get_preferences_creates_defaults(client: AsyncClient):
    """GET /preferences creates default preferences on first access."""
    response = await client.get("/api/v1/preferences", params={"user_id": "user-pref-01"})
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == "user-pref-01"
    assert data["risk_tolerance"] == "moderate"
    assert data["investment_horizon"] == "medium"
    assert data["analysis_depth"] == "standard"
    assert data["preferred_sectors"] == []
    assert data["preferred_metrics"] == []


@pytest.mark.integration
async def test_update_risk_tolerance(client: AsyncClient):
    """PUT /preferences updates risk_tolerance."""
    response = await client.put(
        "/api/v1/preferences",
        params={"user_id": "user-pref-02"},
        json={"risk_tolerance": "aggressive"},
    )
    assert response.status_code == 200
    assert response.json()["risk_tolerance"] == "aggressive"


@pytest.mark.integration
async def test_update_preferred_sectors(client: AsyncClient):
    """PUT /preferences updates preferred_sectors list."""
    response = await client.put(
        "/api/v1/preferences",
        params={"user_id": "user-pref-03"},
        json={"preferred_sectors": ["Technology", "Healthcare"]},
    )
    assert response.status_code == 200
    assert set(response.json()["preferred_sectors"]) == {"Technology", "Healthcare"}


@pytest.mark.integration
async def test_partial_update_preserves_other_fields(client: AsyncClient):
    """Partial update changes only specified fields; others stay at defaults."""
    # Establish defaults
    await client.get("/api/v1/preferences", params={"user_id": "user-pref-04"})

    # Partial update — only risk_tolerance
    await client.put(
        "/api/v1/preferences",
        params={"user_id": "user-pref-04"},
        json={"risk_tolerance": "conservative"},
    )

    get_resp = await client.get("/api/v1/preferences", params={"user_id": "user-pref-04"})
    data = get_resp.json()
    assert data["risk_tolerance"] == "conservative"
    assert data["investment_horizon"] == "medium"  # unchanged


@pytest.mark.integration
async def test_preferences_persist_within_test(client: AsyncClient):
    """Preferences set in one request are visible in the next request."""
    await client.put(
        "/api/v1/preferences",
        params={"user_id": "user-pref-05"},
        json={"analysis_depth": "deep", "preferred_metrics": ["pe_ratio", "roe"]},
    )

    get_resp = await client.get("/api/v1/preferences", params={"user_id": "user-pref-05"})
    data = get_resp.json()
    assert data["analysis_depth"] == "deep"
    assert "pe_ratio" in data["preferred_metrics"]
    assert "roe" in data["preferred_metrics"]
