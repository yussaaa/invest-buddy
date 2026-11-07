"""Deployment-wide settings: the SEC contact, its validation, and its API.

No database: app_settings' store is patched to its in-process mirror.
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1 import news as news_api
from app.config import get_settings
from app.services import app_settings, news


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    stored: dict[str, str] = {}
    monkeypatch.setattr(app_settings, "_values", stored)

    async def nothing(force=False):
        return None

    async def save(key, value):
        if value:
            stored[key] = value
        else:
            stored.pop(key, None)

    monkeypatch.setattr(app_settings, "refresh", nothing)
    monkeypatch.setattr(app_settings, "save", save)
    monkeypatch.setattr(get_settings(), "sec_user_agent", "")
    monkeypatch.setattr(news.EDGAR, "last_error", None)
    return stored


# ── validation ────────────────────────────────────────────────────────────────


def test_a_contact_is_normalised_into_one_header_line():
    assert app_settings.validate_sec_contact("  Jane Doe\r\nX-Evil: 1 jane@example.com ") == \
        "Jane Doe X-Evil: 1 jane@example.com"
    assert app_settings.validate_sec_contact("   ") == ""          # clears it


@pytest.mark.parametrize("raw, reason", [
    ("Jane Doe", "email"),
    ("jane@localhost", "email"),
    ("José Núñez jose@example.com", "ASCII"),
    ("x" * 200 + " jane@example.com", "200"),
])
def test_an_unusable_contact_is_refused_with_a_reason(raw, reason):
    with pytest.raises(ValueError, match=reason):
        app_settings.validate_sec_contact(raw)


# ── precedence ────────────────────────────────────────────────────────────────


def test_the_settings_page_overrides_env_which_is_the_fallback(isolated, monkeypatch):
    assert app_settings.sec_contact() == ("", None)
    monkeypatch.setattr(get_settings(), "sec_user_agent", "Env Person env@example.com")
    assert app_settings.sec_contact() == ("Env Person env@example.com", "env")
    isolated[app_settings.SEC_CONTACT] = "Page Person page@example.com"
    assert app_settings.sec_contact() == ("Page Person page@example.com", "settings")


def test_edgar_sends_the_contact_in_force_and_points_at_settings_without_one(isolated):
    assert "Settings" in news.EDGAR.missing_config()
    isolated[app_settings.SEC_CONTACT] = "Page Person page@example.com"
    assert news.EDGAR.missing_config() is None
    assert news.EDGAR._headers() == {"User-Agent": "Page Person page@example.com"}


# ── API ───────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(news_api.router, prefix="/news")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_saving_a_contact_turns_edgar_on(client):
    news.EDGAR.last_error = "EDGAR refused the request (403)."
    before = (await client.get("/news/settings")).json()
    assert before["sec_contact"] == {"value": None, "source": None}
    assert before["edgar"]["state"] == "not_configured"

    response = await client.put("/news/settings", json={"sec_contact": "Jane jane@example.com"})
    after = response.json()
    assert after["sec_contact"] == {"value": "Jane jane@example.com", "source": "settings"}
    assert after["edgar"]["state"] == "live"          # the old refusal is forgotten


async def test_clearing_falls_back_to_env_without_echoing_it(client, isolated, monkeypatch):
    monkeypatch.setattr(get_settings(), "sec_user_agent", "Operator ops@example.com")
    isolated[app_settings.SEC_CONTACT] = "Jane jane@example.com"
    body = (await client.put("/news/settings", json={"sec_contact": ""})).json()
    assert body["sec_contact"] == {"value": None, "source": "env"}
    assert body["edgar"]["state"] == "live"


async def test_an_invalid_contact_is_a_422_and_nothing_is_stored(client, isolated):
    response = await client.put("/news/settings", json={"sec_contact": "no email here"})
    assert response.status_code == 422 and "email" in response.json()["detail"]
    assert isolated == {}


async def test_a_database_failure_on_save_is_a_503(client, monkeypatch):
    async def broken(key, value):
        raise ConnectionRefusedError("db down")

    monkeypatch.setattr(app_settings, "save", broken)
    response = await client.put("/news/settings", json={"sec_contact": "Jane jane@example.com"})
    assert response.status_code == 503
