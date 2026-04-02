"""Unit tests for shared Pydantic schemas."""

import pytest
from datetime import datetime

from app.agents.base.schemas import (
    AgentResult,
    Citation,
    DataPoint,
    FinalReport,
    GuardrailFlag,
    QueryClassification,
    ToolCallRecord,
    UserPreferences,
)


def test_agent_result_defaults():
    result = AgentResult(agent_name="test", findings="Some findings.", confidence=0.8)
    assert result.agent_name == "test"
    assert result.confidence == 0.8
    assert result.citations == []
    assert result.caveats == []
    assert result.tool_calls == []


def test_agent_result_confidence_bounds():
    """Pydantic enforces confidence is between 0 and 1."""
    with pytest.raises(Exception):
        AgentResult(agent_name="test", findings="x", confidence=1.5)
    with pytest.raises(Exception):
        AgentResult(agent_name="test", findings="x", confidence=-0.1)


def test_citation_model():
    c = Citation(source_type="market_data", title="yfinance data", ticker="AAPL")
    assert c.ticker == "AAPL"
    assert c.url is None


def test_data_point():
    dp = DataPoint(label="P/E Ratio", value=28.5, unit="x")
    assert dp.label == "P/E Ratio"
    assert dp.value == 28.5


def test_query_classification():
    qc = QueryClassification(
        tickers=["AAPL"],
        primary_ticker="AAPL",
        required_agents=["fundamental", "technical"],
        analysis_depth="standard",
        user_intent="Analyse Apple stock",
    )
    assert "fundamental" in qc.required_agents
    assert qc.primary_ticker == "AAPL"


def test_user_preferences_defaults():
    prefs = UserPreferences(user_id="user_123")
    assert prefs.risk_tolerance == "moderate"
    assert prefs.analysis_depth == "standard"
    assert prefs.recent_tickers == []


def test_final_report_has_disclaimer():
    report = FinalReport(
        summary="Test summary",
        detailed_analysis="Test analysis",
        overall_confidence=0.7,
    )
    assert "financial advice" in report.disclaimer.lower()


def test_guardrail_flag():
    flag = GuardrailFlag(
        flag_type="hallucination",
        agent="fundamental",
        detail="Claim not grounded in retrieved data",
    )
    assert flag.severity == "warning"
    assert flag.flag_type == "hallucination"
