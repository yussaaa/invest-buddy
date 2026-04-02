"""Integration tests for watchlist CRUD endpoints.

Tests exercise the full HTTP → repository → PostgreSQL path.
Each test runs in a rolled-back transaction — no data persists between tests.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import ensure_user


@pytest.mark.integration
async def test_create_watchlist(client: AsyncClient):
    """POST /watchlist creates a new watchlist and returns it."""
    response = await client.post(
        "/api/v1/watchlist",
        json={"user_id": "user-wl-01", "name": "My Tech Picks", "tickers": ["AAPL", "MSFT"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "My Tech Picks"
    assert set(data["tickers"]) == {"AAPL", "MSFT"}
    assert "id" in data
    assert data["user_id"] == "user-wl-01"


@pytest.mark.integration
async def test_list_watchlists_empty(client: AsyncClient, db_session: AsyncSession):
    """GET /watchlist returns empty list for a new user."""
    await ensure_user(db_session, "user-wl-empty")
    await db_session.flush()

    response = await client.get("/api/v1/watchlist", params={"user_id": "user-wl-empty"})
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.integration
async def test_list_watchlists(client: AsyncClient):
    """After creating watchlists, GET /watchlist returns all of them."""
    await client.post(
        "/api/v1/watchlist",
        json={"user_id": "user-wl-list", "name": "Tech", "tickers": ["AAPL"]},
    )
    await client.post(
        "/api/v1/watchlist",
        json={"user_id": "user-wl-list", "name": "Energy", "tickers": ["XOM"]},
    )

    response = await client.get("/api/v1/watchlist", params={"user_id": "user-wl-list"})
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 2
    names = {item["name"] for item in items}
    assert names == {"Tech", "Energy"}


@pytest.mark.integration
async def test_update_watchlist(client: AsyncClient):
    """PUT /watchlist/{id} updates name and tickers."""
    create_resp = await client.post(
        "/api/v1/watchlist",
        json={"user_id": "user-wl-upd", "name": "Original", "tickers": ["AAPL"]},
    )
    assert create_resp.status_code == 200
    wl_id = create_resp.json()["id"]

    update_resp = await client.put(
        f"/api/v1/watchlist/{wl_id}",
        json={"name": "Updated", "tickers": ["GOOGL", "META"]},
    )
    assert update_resp.status_code == 200
    data = update_resp.json()
    assert data["name"] == "Updated"
    assert set(data["tickers"]) == {"GOOGL", "META"}


@pytest.mark.integration
async def test_update_watchlist_not_found(client: AsyncClient):
    """PUT /watchlist/{id} returns 404 for a non-existent watchlist."""
    response = await client.put(
        "/api/v1/watchlist/nonexistent-id-xyz",
        json={"name": "X"},
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_delete_watchlist(client: AsyncClient):
    """DELETE /watchlist/{id} removes the watchlist."""
    create_resp = await client.post(
        "/api/v1/watchlist",
        json={"user_id": "user-wl-del", "name": "ToDelete", "tickers": []},
    )
    wl_id = create_resp.json()["id"]

    delete_resp = await client.delete(f"/api/v1/watchlist/{wl_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] == wl_id

    # Confirm it's gone
    list_resp = await client.get("/api/v1/watchlist", params={"user_id": "user-wl-del"})
    ids = [item["id"] for item in list_resp.json()]
    assert wl_id not in ids


@pytest.mark.integration
async def test_delete_watchlist_not_found(client: AsyncClient):
    """DELETE /watchlist/{id} returns 404 for a non-existent watchlist."""
    response = await client.delete("/api/v1/watchlist/nonexistent-id-xyz")
    assert response.status_code == 404


@pytest.mark.integration
async def test_watchlist_tickers_are_uppercased(client: AsyncClient):
    """Tickers submitted in lowercase are stored and returned uppercased."""
    response = await client.post(
        "/api/v1/watchlist",
        json={"user_id": "user-wl-case", "name": "Case Test", "tickers": ["aapl", "msft"]},
    )
    assert response.status_code == 200
    tickers = response.json()["tickers"]
    assert "AAPL" in tickers
    assert "MSFT" in tickers
