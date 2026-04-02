"""Unit tests for tool implementations — uses real yfinance data (no mocks)."""

import pytest


@pytest.mark.asyncio
async def test_get_company_overview():
    from app.tools.market_data.yfinance_tool import get_company_overview
    result = await get_company_overview("AAPL")
    assert result["ticker"] == "AAPL"
    assert result["name"] is not None
    assert "sector" in result


@pytest.mark.asyncio
async def test_get_price_history():
    from app.tools.market_data.yfinance_tool import get_price_history
    result = await get_price_history("AAPL", period="1mo", interval="1d")
    assert result["ticker"] == "AAPL"
    assert len(result["data"]) > 0
    assert "close" in result["data"][0]


@pytest.mark.asyncio
async def test_compute_rsi():
    from app.tools.calculation.technical_indicators import compute_rsi
    result = await compute_rsi("AAPL")
    assert "current_rsi" in result
    assert 0 <= result["current_rsi"] <= 100
    assert result["zone"] in ("overbought", "oversold", "neutral")


@pytest.mark.asyncio
async def test_compute_macd():
    from app.tools.calculation.technical_indicators import compute_macd
    result = await compute_macd("AAPL")
    assert "macd" in result
    assert "signal" in result
    assert "trend" in result


@pytest.mark.asyncio
async def test_compute_bollinger_bands():
    from app.tools.calculation.technical_indicators import compute_bollinger_bands
    result = await compute_bollinger_bands("AAPL")
    assert "upper_band" in result
    assert "lower_band" in result
    assert result["upper_band"] > result["lower_band"]


@pytest.mark.asyncio
async def test_compute_financial_ratios():
    from app.tools.calculation.financial_ratios import compute_financial_ratios
    result = await compute_financial_ratios("AAPL")
    assert result["ticker"] == "AAPL"
    assert "pe_ratio_ttm" in result
    assert "roe" in result


@pytest.mark.asyncio
async def test_compute_beta():
    from app.tools.calculation.risk_metrics import compute_beta
    result = await compute_beta("AAPL")
    assert "beta" in result
    assert result["beta"] is not None
    assert 0 < result["beta"] < 5  # sanity check for AAPL beta


@pytest.mark.asyncio
async def test_tool_registry_execute():
    from app.tools.registry import ToolRegistry
    registry = ToolRegistry.get()
    result = await registry.execute("get_company_overview", {"ticker": "MSFT"})
    assert result.success
    assert result.data["ticker"] == "MSFT"


@pytest.mark.asyncio
async def test_tool_registry_unknown_tool():
    from app.tools.registry import ToolRegistry
    registry = ToolRegistry.get()
    result = await registry.execute("nonexistent_tool", {})
    assert not result.success
    assert "Unknown tool" in result.error


@pytest.mark.asyncio
async def test_tool_registry_caching():
    from app.tools.registry import ToolRegistry
    registry = ToolRegistry.get()
    # First call — cache miss
    result1 = await registry.execute("get_company_overview", {"ticker": "GOOGL"})
    assert result1.success
    # Second call — should be cache hit
    result2 = await registry.execute("get_company_overview", {"ticker": "GOOGL"})
    assert result2.cache_hit
