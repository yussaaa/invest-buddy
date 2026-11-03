"""How often does a made-up price pass the numeric grounding check by chance?

The figures in the docs (26.6%, then 32.3% once the trend summary joined the
evidence) were measured once and never written down as a test. This is that
measurement, so a change to the tolerance shows up as a number.

The evidence set is shaped like the chat agent's real one for a ~$250 stock:
the quote, five moving averages and their distances, bands, levels, RSI and
MACD readings, 52-week range, trend summary, strengths and window lengths.
The candidates are uniform random prices — every one of them a false claim.
"""

from __future__ import annotations

import random

import pytest

from app.agents.base.guardrails import check_numeric_grounding


def _evidence(seed: int = 11) -> list[float]:
    r = random.Random(seed)
    p = 250.0
    nums = [p, p * 0.99, p * 1.004]
    nums += [round(p * r.uniform(0.85, 1.12), 2) for _ in range(5)]
    nums += [round(r.uniform(-15, 15), 2) for _ in range(5)]
    nums += [round(r.uniform(-3, 3), 3) for _ in range(5)]
    nums += [round(p * r.uniform(0.9, 1.1), 2) for _ in range(3)]
    nums += [round(r.uniform(0, 1.2), 3), round(r.uniform(0.02, 0.2), 4)]
    nums += [round(r.uniform(25, 75), 2) for _ in range(10)]
    nums += [round(r.uniform(-4, 4), 4) for _ in range(3)]
    nums += [round(p * r.uniform(0.8, 1.2), 2) for _ in range(8)]
    nums += [round(p * 1.18, 2), round(p * 0.72, 2), round(r.uniform(-30, 0), 2)]
    nums += [round(r.uniform(-3, 3), 2) for _ in range(4)]
    nums += [round(r.uniform(0, 1), 2) for _ in range(8)]
    nums += [14, 30, 70, 20, 50, 200, 250, 26, 12, 9]
    return nums[:67]


def _coincidental_rate(decimals: int, samples: int = 5000) -> float:
    evidence = _evidence()
    r = random.Random(3)
    passed = 0
    for _ in range(samples):
        price = round(r.uniform(100, 400), decimals)
        if not check_numeric_grounding(f"It trades at ${price:.{decimals}f}.", evidence):
            passed += 1
    return passed / samples


@pytest.mark.parametrize(
    "decimals, ceiling",
    [
        (2, 0.01),   # measured 0.09%; was 30.6% under the ±2% tolerance
        (1, 0.03),   # measured 0.9%
        # Measured 8%. A whole-dollar figure honestly covers $1.50 of range —
        # "$250" is a fair quote of 249.50 through 250.99 — so this is the
        # floor of the method, not slack in it.
        (0, 0.12),
    ],
)
def test_a_random_price_rarely_passes_as_grounded(decimals, ceiling):
    assert _coincidental_rate(decimals) < ceiling
