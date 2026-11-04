"""The per-indicator series reach the chart panel but never the chat agent.

What the chat agent is shown is also what its figures are grounded against, so
a series in there would let a small invented number pass as quoted.
"""

from app.agents.base.guardrails import check_numeric_grounding
from app.services.technicals import SERIES_KEYS, without_series


def _payload() -> dict:
    return {
        "symbol": "AAPL",
        "rsi": {"current_rsi": 41.7, "zone": "neutral", "historical": [38.2, 40.1, 41.7]},
        "macd": {"macd": 0.961, "signal": 2.933, "histogram": -1.972,
                 "history": [-0.412, -1.105, -1.972]},
        "moving_averages": {"current_price": 331.9},
        "drawdown": {"from_high_percent": -5.6},
    }


def test_series_are_removed_and_readings_kept():
    stripped = without_series(_payload())

    assert "historical" not in stripped["rsi"]
    assert "history" not in stripped["macd"]
    assert stripped["rsi"]["current_rsi"] == 41.7
    assert stripped["macd"]["histogram"] == -1.972
    assert stripped["moving_averages"] == {"current_price": 331.9}


def test_input_is_not_mutated():
    payload = _payload()
    without_series(payload)
    assert payload["macd"]["history"] == [-0.412, -1.105, -1.972]


def test_errors_and_missing_indicators_pass_through():
    stripped = without_series({"rsi": {"error": "no data"}, "symbol": "X"})
    assert stripped == {"rsi": {"error": "no data"}, "symbol": "X"}


def test_a_series_value_no_longer_grounds_a_figure():
    text = "The histogram was -1.105 two sessions ago."
    assert check_numeric_grounding(text, _payload()) == []
    assert check_numeric_grounding(text, without_series(_payload())) == ["-1.105"]


def test_every_series_key_is_covered():
    # A new series added to an indicator should be added here too.
    assert SERIES_KEYS == {"rsi": ("historical",), "macd": ("history",)}
