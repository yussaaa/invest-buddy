"""Central MCP-style tool registry.

All tools are registered here with their JSON schemas. Any agent can call any
tool via registry.execute(). The registry also exposes an MCP-compatible
schema list so the LLM sees consistent, versioned tool definitions.

Adding a new data source (e.g. Bloomberg) requires:
  1. Implement the tool function in the relevant module
  2. Register it here with register()
  3. Mention it in the relevant agent's prompt
— no restructuring of agents needed.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

import structlog

log = structlog.get_logger(__name__)


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict          # JSON Schema of input parameters
    fn: Callable              # The actual async function
    category: str = "general"
    cacheable: bool = True
    cache_ttl_seconds: int = 300


@dataclass
class ToolResult:
    success: bool
    data: Any
    error: str | None = None
    latency_ms: int = 0
    cache_hit: bool = False

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "cache_hit": self.cache_hit,
        }


class ToolRegistry:
    """Singleton registry for all agent tools."""

    _instance: ToolRegistry | None = None

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._cache: dict[str, tuple[Any, float]] = {}   # key → (result, expires_at)

    @classmethod
    def get(cls) -> "ToolRegistry":
        if cls._instance is None:
            cls._instance = ToolRegistry()
            cls._instance._register_all()
        return cls._instance

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool
        log.debug("tool_registered", name=tool.name, category=tool.category)

    def get_schema_for_llm(self, names: list[str] | None = None) -> list[dict]:
        """Return OpenAI/Anthropic function-calling schemas for the requested tools."""
        tools = (
            [self._tools[n] for n in names if n in self._tools]
            if names
            else list(self._tools.values())
        )
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    def get_mcp_schemas(self) -> list[dict]:
        """Return MCP-compatible tool list (for the MCP server)."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.parameters,
            }
            for t in self._tools.values()
        ]

    async def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        """Execute a tool by name, with Redis-style in-process caching."""
        if tool_name not in self._tools:
            return ToolResult(success=False, data=None, error=f"Unknown tool: {tool_name}")

        tool = self._tools[tool_name]

        # Check in-process cache
        if tool.cacheable:
            cache_key = f"{tool_name}:{sorted(arguments.items())}"
            cached = self._cache.get(cache_key)
            if cached and time.monotonic() < cached[1]:
                return ToolResult(success=True, data=cached[0], cache_hit=True)

        t0 = time.monotonic()
        try:
            result = await tool.fn(**arguments)
            latency_ms = int((time.monotonic() - t0) * 1000)

            # Store in cache
            if tool.cacheable:
                self._cache[cache_key] = (result, time.monotonic() + tool.cache_ttl_seconds)

            return ToolResult(success=True, data=result, latency_ms=latency_ms)
        except Exception as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            log.error("tool_error", tool=tool_name, error=str(exc))
            return ToolResult(success=False, data=None, error=str(exc), latency_ms=latency_ms)

    def _register_all(self) -> None:
        """Register every tool. Called once on first access."""
        from app.tools.market_data.yfinance_tool import (
            get_company_overview,
            get_earnings_calendar,
            get_price_history,
            get_income_statement,
            get_balance_sheet,
            get_cash_flow,
            get_analyst_ratings,
            get_options_chain,
        )
        from app.tools.calculation.technical_indicators import (
            compute_rsi,
            compute_macd,
            compute_bollinger_bands,
            compute_moving_averages,
            compute_support_resistance,
        )
        from app.tools.calculation.financial_ratios import compute_financial_ratios
        from app.tools.calculation.risk_metrics import (
            compute_historical_volatility,
            compute_var,
            compute_beta,
            compute_sharpe_sortino,
            compute_max_drawdown,
        )
        from app.tools.news.newsapi_tool import get_recent_news
        from app.tools.news.sec_edgar_tool import get_sec_filings
        from app.tools.web.search_tool import search_web

        self.register(ToolDefinition(
            name="get_company_overview",
            description="Get company profile, sector, description, market cap, and key stats.",
            parameters={"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]},
            fn=get_company_overview,
            category="market_data",
            cache_ttl_seconds=3600,
        ))
        self.register(ToolDefinition(
            name="get_price_history",
            description="Get historical OHLCV price data for a ticker.",
            parameters={"type": "object", "properties": {
                "ticker": {"type": "string"},
                "period": {"type": "string", "default": "1y", "description": "e.g. 1mo, 3mo, 1y, 5y"},
                "interval": {"type": "string", "default": "1d", "description": "e.g. 1d, 1wk, 1mo"},
            }, "required": ["ticker"]},
            fn=get_price_history,
            category="market_data",
            cache_ttl_seconds=300,
        ))
        self.register(ToolDefinition(
            name="get_income_statement",
            description="Get annual and quarterly income statements.",
            parameters={"type": "object", "properties": {
                "ticker": {"type": "string"},
                "quarterly": {"type": "boolean", "default": False},
            }, "required": ["ticker"]},
            fn=get_income_statement,
            category="fundamental",
            cache_ttl_seconds=86400,
        ))
        self.register(ToolDefinition(
            name="get_balance_sheet",
            description="Get balance sheet data.",
            parameters={"type": "object", "properties": {
                "ticker": {"type": "string"},
                "quarterly": {"type": "boolean", "default": False},
            }, "required": ["ticker"]},
            fn=get_balance_sheet,
            category="fundamental",
            cache_ttl_seconds=86400,
        ))
        self.register(ToolDefinition(
            name="get_cash_flow",
            description="Get cash flow statement data.",
            parameters={"type": "object", "properties": {
                "ticker": {"type": "string"},
                "quarterly": {"type": "boolean", "default": False},
            }, "required": ["ticker"]},
            fn=get_cash_flow,
            category="fundamental",
            cache_ttl_seconds=86400,
        ))
        self.register(ToolDefinition(
            name="compute_financial_ratios",
            description="Compute key financial ratios: P/E, P/B, EV/EBITDA, ROE, ROA, debt/equity, FCF yield, etc.",
            parameters={"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]},
            fn=compute_financial_ratios,
            category="fundamental",
            cache_ttl_seconds=3600,
        ))
        self.register(ToolDefinition(
            name="get_analyst_ratings",
            description="Get analyst buy/hold/sell recommendations and price targets.",
            parameters={"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]},
            fn=get_analyst_ratings,
            category="sentiment",
            cache_ttl_seconds=3600,
        ))
        self.register(ToolDefinition(
            name="get_options_chain",
            description="Get options chain data for put/call ratio and implied volatility skew.",
            parameters={"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]},
            fn=get_options_chain,
            category="sentiment",
            cache_ttl_seconds=900,
        ))
        self.register(ToolDefinition(
            name="compute_rsi",
            description="Compute Relative Strength Index (RSI) for a ticker.",
            parameters={"type": "object", "properties": {
                "ticker": {"type": "string"},
                "period": {"type": "integer", "default": 14},
            }, "required": ["ticker"]},
            fn=compute_rsi,
            category="technical",
            cache_ttl_seconds=300,
        ))
        self.register(ToolDefinition(
            name="compute_macd",
            description="Compute MACD line, signal line, and histogram.",
            parameters={"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]},
            fn=compute_macd,
            category="technical",
            cache_ttl_seconds=300,
        ))
        self.register(ToolDefinition(
            name="compute_bollinger_bands",
            description="Compute Bollinger Bands (upper, middle, lower) and %B.",
            parameters={"type": "object", "properties": {
                "ticker": {"type": "string"},
                "window": {"type": "integer", "default": 20},
            }, "required": ["ticker"]},
            fn=compute_bollinger_bands,
            category="technical",
            cache_ttl_seconds=300,
        ))
        self.register(ToolDefinition(
            name="compute_moving_averages",
            description="Compute SMA and EMA for multiple windows (20, 50, 200 day).",
            parameters={"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]},
            fn=compute_moving_averages,
            category="technical",
            cache_ttl_seconds=300,
        ))
        self.register(ToolDefinition(
            name="compute_support_resistance",
            description="Identify key support and resistance price levels.",
            parameters={"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]},
            fn=compute_support_resistance,
            category="technical",
            cache_ttl_seconds=3600,
        ))
        self.register(ToolDefinition(
            name="compute_historical_volatility",
            description="Compute annualised historical volatility.",
            parameters={"type": "object", "properties": {
                "ticker": {"type": "string"},
                "window": {"type": "integer", "default": 30},
            }, "required": ["ticker"]},
            fn=compute_historical_volatility,
            category="risk",
            cache_ttl_seconds=3600,
        ))
        self.register(ToolDefinition(
            name="compute_var",
            description="Compute Value-at-Risk using parametric, historical, and Monte Carlo methods.",
            parameters={"type": "object", "properties": {
                "ticker": {"type": "string"},
                "confidence": {"type": "number", "default": 0.95},
            }, "required": ["ticker"]},
            fn=compute_var,
            category="risk",
            cache_ttl_seconds=3600,
        ))
        self.register(ToolDefinition(
            name="compute_beta",
            description="Compute 1-year rolling beta against a benchmark (default SPY).",
            parameters={"type": "object", "properties": {
                "ticker": {"type": "string"},
                "benchmark": {"type": "string", "default": "SPY"},
            }, "required": ["ticker"]},
            fn=compute_beta,
            category="risk",
            cache_ttl_seconds=3600,
        ))
        self.register(ToolDefinition(
            name="compute_sharpe_sortino",
            description="Compute Sharpe ratio, Sortino ratio, and Calmar ratio.",
            parameters={"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]},
            fn=compute_sharpe_sortino,
            category="risk",
            cache_ttl_seconds=3600,
        ))
        self.register(ToolDefinition(
            name="compute_max_drawdown",
            description="Compute maximum drawdown, drawdown duration, and recovery time.",
            parameters={"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]},
            fn=compute_max_drawdown,
            category="risk",
            cache_ttl_seconds=3600,
        ))
        self.register(ToolDefinition(
            name="get_recent_news",
            description="Get recent news articles for a ticker from NewsAPI.",
            parameters={"type": "object", "properties": {
                "ticker": {"type": "string"},
                "days": {"type": "integer", "default": 7},
                "max_results": {"type": "integer", "default": 10},
            }, "required": ["ticker"]},
            fn=get_recent_news,
            category="news",
            cache_ttl_seconds=900,
        ))
        self.register(ToolDefinition(
            name="get_sec_filings",
            description="Download recent SEC filings (10-K, 10-Q, 8-K) for a company.",
            parameters={"type": "object", "properties": {
                "ticker": {"type": "string"},
                "filing_type": {"type": "string", "default": "10-K", "enum": ["10-K", "10-Q", "8-K"]},
                "limit": {"type": "integer", "default": 3},
            }, "required": ["ticker"]},
            fn=get_sec_filings,
            category="news",
            cache_ttl_seconds=86400,
        ))
        self.register(ToolDefinition(
            name="search_web",
            description="Search the web for recent financial news and developments using Tavily.",
            parameters={"type": "object", "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 5},
            }, "required": ["query"]},
            fn=search_web,
            category="web",
            cache_ttl_seconds=900,
        ))
        self.register(ToolDefinition(
            name="get_earnings_calendar",
            description="Get upcoming and historical earnings dates and EPS estimates.",
            parameters={"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]},
            fn=get_earnings_calendar,
            category="market_data",
            cache_ttl_seconds=3600,
        ))
