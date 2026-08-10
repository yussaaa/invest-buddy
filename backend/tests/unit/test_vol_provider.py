"""Unit tests for the volatility provider seam — no DB, no network.

The point of these tests is the envelope contract. The frontend renders one
label when `iv_rank_available` is true and another when it is false, reading a
different field in each branch; if two providers ever return different key
sets, that branch reads undefined and the panel shows a blank where a number
should be. So the first test here is the one that matters most.
"""

from __future__ import annotations

import math

import pytest

from app.config import Settings
from app.services import vol_provider
from app.services.vol_provider import (
    MarketDataAppProvider,
    RealizedVolProxyProvider,
    VolatilityProvider,
    _default_provider,
    empty_context,
    get_vol_provider,
)


class FakeRankProvider:
    """Stands in for a paid provider that does have IV history."""

    name = "fake_rank"

    async def vol_context(self, symbol: str, atm_iv_30d: float | None) -> dict:
        context = empty_context()
        context.update(
            {
                "atm_iv_30d": atm_iv_30d,
                "hv_20": 0.22,
                "iv_rank": 64.0,
                "iv_percentile_252": 71.0,
                "iv_rank_available": True,
                "method": "vendor_iv_history",
                "note": "Real rank.",
            }
        )
        return context


class FakeBar:
    def __init__(self, close: float) -> None:
        self.close = close


def patch_bars(monkeypatch, closes: list[float]) -> None:
    """Feed the proxy a synthetic price series instead of the bar store."""

    async def fake_daily_bars_for(symbol, start, end=None, session=None, adjusted=False, span="2y"):
        assert adjusted is True, "vol history must use the split-adjusted series"
        return [FakeBar(c) for c in closes]

    monkeypatch.setattr(vol_provider.bar_store, "daily_bars_for", fake_daily_bars_for)


def wobbly_series(n: int = 400, sigma: float = 0.012) -> list[float]:
    """A deterministic series with real day-to-day variation."""
    closes = [100.0]
    for i in range(n):
        step = sigma * math.sin(i * 1.7) + sigma * 0.4 * math.cos(i * 0.31)
        closes.append(closes[-1] * math.exp(step))
    return closes


# ── The envelope contract ───────────────────────────────────────────────────


async def test_all_providers_return_an_identical_key_set(monkeypatch):
    """The frontend branches on iv_rank_available and reads a different field
    in each branch. If the envelopes diverge, that branch reads undefined."""
    patch_bars(monkeypatch, wobbly_series())

    proxy = await RealizedVolProxyProvider().vol_context("TEST", 0.30)
    vendor = await FakeRankProvider().vol_context("TEST", 0.30)

    assert set(proxy) == set(empty_context())
    assert set(vendor) == set(empty_context())
    assert set(proxy) == set(vendor)


async def test_empty_context_has_every_key_present_and_none(monkeypatch):
    context = empty_context("no data")

    assert context["iv_rank_available"] is False
    assert context["note"] == "no data"
    assert all(
        context[k] is None
        for k in context
        if k not in ("iv_rank_available", "method", "note")
    )


async def test_providers_satisfy_the_protocol():
    assert isinstance(RealizedVolProxyProvider(), VolatilityProvider)
    assert isinstance(FakeRankProvider(), VolatilityProvider)
    assert isinstance(MarketDataAppProvider(api_key="x"), VolatilityProvider)


# ── The proxy's honesty guarantees ──────────────────────────────────────────


async def test_proxy_never_claims_an_iv_rank(monkeypatch):
    patch_bars(monkeypatch, wobbly_series())

    context = await RealizedVolProxyProvider().vol_context("TEST", 0.30)

    assert context["iv_rank_available"] is False
    assert context["iv_rank"] is None
    assert context["iv_percentile_252"] is None
    assert context["method"] == "realized_vol_proxy"
    assert "not an IV rank" in context["note"]


async def test_proxy_measures_iv_against_realized_vol(monkeypatch):
    patch_bars(monkeypatch, wobbly_series())

    context = await RealizedVolProxyProvider().vol_context("TEST", 0.30)

    assert context["hv_20"] is not None
    assert context["hv_60"] is not None
    assert context["hv_252"] is not None
    assert context["iv_hv_ratio"] == pytest.approx(0.30 / context["hv_20"], abs=1e-12)
    assert context["iv_hv_spread"] == pytest.approx(0.30 - context["hv_20"], abs=1e-12)
    assert 0.0 <= context["iv_percentile_vs_realized"] <= 100.0


async def test_expensive_options_land_high_in_the_distribution(monkeypatch):
    patch_bars(monkeypatch, wobbly_series())

    cheap = await RealizedVolProxyProvider().vol_context("TEST", 0.02)
    rich = await RealizedVolProxyProvider().vol_context("TEST", 3.0)

    assert cheap["iv_percentile_vs_realized"] == 0.0
    assert rich["iv_percentile_vs_realized"] == 100.0


async def test_proxy_without_an_atm_iv_still_reports_realized_vol(monkeypatch):
    patch_bars(monkeypatch, wobbly_series())

    context = await RealizedVolProxyProvider().vol_context("TEST", None)

    assert context["hv_20"] is not None
    assert context["iv_percentile_vs_realized"] is None
    assert context["iv_hv_ratio"] is None


async def test_proxy_degrades_when_the_store_is_empty(monkeypatch):
    patch_bars(monkeypatch, [])

    context = await RealizedVolProxyProvider().vol_context("TEST", 0.30)

    assert context["method"] == "unavailable"
    assert context["hv_20"] is None
    assert set(context) == set(empty_context())


async def test_proxy_degrades_when_the_store_raises(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(vol_provider.bar_store, "daily_bars_for", boom)

    context = await RealizedVolProxyProvider().vol_context("TEST", 0.30)

    assert context["method"] == "unavailable", "a dead database costs the block, not the page"


async def test_short_history_yields_nulls_not_a_confident_number(monkeypatch):
    patch_bars(monkeypatch, [100.0, 101.0, 100.5])

    context = await RealizedVolProxyProvider().vol_context("TEST", 0.30)

    assert context["hv_20"] is None
    assert context["iv_percentile_vs_realized"] is None


# ── The factory ─────────────────────────────────────────────────────────────


def build_settings(**overrides) -> Settings:
    return Settings(openai_api_key="test-key", **overrides)


def test_factory_defaults_to_the_free_proxy():
    _default_provider.cache_clear()

    provider = get_vol_provider(build_settings())

    assert isinstance(provider, RealizedVolProxyProvider)


def test_factory_falls_back_when_a_paid_provider_has_no_key():
    """A missing optional key should cost a feature, not the page."""
    _default_provider.cache_clear()

    provider = get_vol_provider(
        build_settings(vol_data_provider="marketdata", marketdata_api_key="")
    )

    assert isinstance(provider, RealizedVolProxyProvider)


def test_factory_returns_the_paid_provider_when_configured():
    _default_provider.cache_clear()

    provider = get_vol_provider(
        build_settings(vol_data_provider="marketdata", marketdata_api_key="secret")
    )

    assert isinstance(provider, MarketDataAppProvider)
    assert provider.name == "marketdata"


async def test_unimplemented_provider_fails_loudly_rather_than_silently():
    with pytest.raises(NotImplementedError):
        await MarketDataAppProvider(api_key="secret").vol_context("TEST", 0.30)
