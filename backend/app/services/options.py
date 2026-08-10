"""Option chains for the charting page: fetch, normalise, screen, explain.

This is the I/O half of the options feature. The arithmetic lives in
options_math.py, the filtering rules in options_screen.py, and the volatility
history behind the provider seam in vol_provider.py — all three pure or
swappable, all three unit-tested offline. What is left here is the part that
touches the network: which expiries to pull, how to pull them without tripping
a rate limit, and what to return when the data is partly missing, which for
option chains is most of the time.

A note on what this data is and is not. Yahoo's chains are delayed, the book
empties outside regular hours (leaving a `lastPrice` that may be days old),
open interest is the previous session's close, and the published implied
volatility comes from an undisclosed model that emits obvious garbage on the
wings. The screener compensates where it can — solving IV from the mid,
flagging stale rows, dropping untradeable spreads — but the honest summary is
that this is a research and education screen, not an execution tool, and the
UI says so.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime

import structlog
import yfinance as yf

from app.config import get_settings
from app.models.factory import get_provider
from app.services import market_data, options_screen
from app.services.cache import cached_async
from app.services.llm_guard import has_llm_credentials, missing_credentials_reason
from app.services.options_screen import Contract, normalise, screen_all, select_expiries
from app.services.vol_provider import get_vol_provider

log = structlog.get_logger(__name__)

OPTIONS_CHAIN_TTL = 300.0     # quotes are already delayed; greeks aren't tick-sensitive
EXPLANATION_TTL = 900.0       # matches technicals.EXPLANATION_TTL

# Each expiry is a separate request to Yahoo. Three at a time keeps a five
# expiry fetch under a couple of seconds without looking like a scraper.
MAX_CONCURRENT_EXPIRIES = 3

# Used when the T-bill quote is unavailable. Being wrong by a percentage point
# moves a 30-day delta by well under one point, so this is not worth failing over.
DEFAULT_RISK_FREE = 0.04
RISK_FREE_SYMBOL = "^IRX"     # 13-week T-bill, quoted in percent

DISCLAIMER = (
    "Screening output is produced by a deterministic filter over public, delayed "
    "data. Probabilities are model estimates, not forecasts. This is not "
    "investment advice."
)

CAVEATS = [
    "A high probability of profit is not a high expected return. These screens rank "
    "contracts that win often and lose big.",
    "Probabilities are risk-neutral: they describe what the option is priced for, not "
    "what the stock is expected to do.",
    "Black-Scholes is a European model. Single-name equity options are American, and "
    "short puts on dividend payers can be assigned early.",
    "Quotes are delayed and the book empties outside market hours. Rows flagged "
    "last_price_only may be pricing off a stale print.",
]

EXPLAIN_SYSTEM_PROMPT = """You describe what an options screener has surfaced for one stock.

You will be given a volatility summary and a handful of contracts that passed a
liquidity and probability filter, for one or more of these strategies: cash-secured
puts, covered calls, long-dated (LEAPS) calls, and put credit spreads.

Write 3 short paragraphs:
1. Volatility — what the implied volatility is relative to how much the stock has
   actually been moving. Get the direction right, it is easy to invert: implied
   ABOVE realized means premium is comparatively expensive, and the seller of it is
   being paid more than the stock's recent behaviour would justify; implied BELOW
   realized means premium is comparatively cheap, which favours the buyer rather
   than the seller. Do not describe cheap premium as an opportunity to collect
   income — collecting a thin premium against a stock that has been moving more
   than that is the unfavourable side of the trade.
2. The candidates — what the screen surfaced, naming actual strikes, expiries,
   probabilities and annualised yields, and how the strategies differ in what they
   risk.
3. What the numbers do not capture.

Required phrasings — follow these exactly:
- Write entirely in the third person. Never use the word "you".
- Assignment: "assignment obligates the seller to purchase 100 shares at the strike",
  never "you would have to buy".
- Breakeven: "the breakeven at expiry is $X" or "the position retains its full credit
  above $X". Never "the stock will fall to $X" and never "a price target of $X".
- Ranking: "the highest-ranked candidate by annualised yield times probability of
  profit is ...". Never "the best trade", "recommend", "opportunity", or "should".
- A screened contract is "a contract that passes the filters", never a trade to place.
- Attribute every probability: "the model-implied probability of finishing above the
  breakeven is 82% under a risk-neutral lognormal assumption".

Rules:
- Describe what the screen shows. Do NOT recommend opening, closing, buying or
  selling anything, do not predict prices, and do not suggest position sizes.
- State plainly that a high probability of profit comes with a small credit against
  a large tail loss.
- Name the actual numbers you are describing.
- Plain prose, no markdown headings or bullets. Under 250 words total."""


async def get_options_chain(symbol: str, horizon: str = "both") -> dict:
    """Normalised contracts, expiry summaries and volatility context."""
    symbol = symbol.upper()
    horizon = horizon if horizon in ("short", "leaps", "both") else "both"

    async def fetch() -> dict:
        return await _build_chain(symbol, horizon)

    try:
        return await cached_async(
            f"optchain:{symbol}:{horizon}",
            OPTIONS_CHAIN_TTL,
            fetch,
            should_cache=lambda r: bool(r.get("expiries")),
            # An expensive multi-request fan-out, so it is worth paying for a
            # cross-replica lock to make sure only one worker does it.
            lock=True,
        )
    except Exception as e:
        log.error("options_chain_failed", symbol=symbol, error=str(e))
        return {"symbol": symbol, "expiries": [], "contracts": [], "error": str(e)}


async def get_option_strategies(
    symbol: str, strategy: str = "all", limit: int = 15
) -> dict:
    """Ranked strategy candidates over the cached chain.

    The screen itself is not cached: it runs in microseconds over an already
    cached chain, so caching it would only multiply keys and make a changed
    filter miss.
    """
    symbol = symbol.upper()
    horizon = "leaps" if strategy == "leaps_call" else "both"
    chain = await get_options_chain(symbol, horizon)

    if chain.get("error") and not chain.get("contracts"):
        return {
            "symbol": symbol,
            "strategy": strategy,
            "candidates": [],
            "volatility": chain.get("volatility"),
            "error": chain["error"],
        }

    spot = chain.get("spot") or 0.0
    contracts = [_rehydrate(row) for row in chain.get("contracts", [])]
    next_earnings = _parse_date(chain.get("next_earnings"))

    screened = screen_all(
        [c for c in contracts if c],
        spot=spot,
        r=chain.get("risk_free_rate") or DEFAULT_RISK_FREE,
        q=chain.get("dividend_yield") or 0.0,
        strategy=strategy,
        limit=limit,
        next_earnings=next_earnings,
    )

    return {
        "symbol": symbol,
        "spot": spot,
        "strategy": strategy,
        "as_of": chain.get("as_of"),
        "volatility": chain.get("volatility"),
        # Carried through so the panel can show chain-level open interest
        # without a second round trip for data already in hand.
        "expiries": chain.get("expiries", []),
        "next_earnings": chain.get("next_earnings"),
        "probability_model": "risk_neutral_lognormal",
        "caveats": CAVEATS,
        "disclaimer": DISCLAIMER,
        "warnings": chain.get("warnings", []),
        **screened,
    }


async def explain_options(symbol: str, strategy: str = "all") -> dict:
    """Ask the fast model to describe what the screen surfaced."""
    symbol = symbol.upper()
    settings = get_settings()

    if not has_llm_credentials(settings):
        return {
            "symbol": symbol,
            "strategy": strategy,
            "available": False,
            "reason": missing_credentials_reason(settings),
        }

    screened = await get_option_strategies(symbol, strategy, limit=5)

    async def fetch() -> dict:
        payload = json.dumps(
            {
                "ticker": symbol,
                "spot": screened.get("spot"),
                "volatility": screened.get("volatility"),
                "next_earnings": screened.get("next_earnings"),
                "candidates": options_screen.compact_rows(
                    screened.get("candidates", []), limit=8
                ),
                "universe_counts": screened.get("universe_counts"),
            },
            default=str,
        )

        response = await get_provider().complete(
            messages=[
                {"role": "system", "content": EXPLAIN_SYSTEM_PROMPT},
                {"role": "user", "content": f"Screen output for {symbol}:\n\n{payload}"},
            ],
            model=settings.fast_model,
            temperature=0.2,
            max_tokens=800,
        )

        return {
            "symbol": symbol,
            "strategy": strategy,
            "available": True,
            "explanation": response.content.strip(),
            "model": response.model,
            "disclaimer": DISCLAIMER,
            "as_of": datetime.now(UTC).isoformat(),
        }

    try:
        return await cached_async(
            f"options_explain:{symbol}:{strategy}",
            EXPLANATION_TTL,
            fetch,
            should_cache=lambda r: bool(r.get("explanation")),
        )
    except Exception as e:
        log.error("options_explain_failed", symbol=symbol, error=str(e))
        return {"symbol": symbol, "strategy": strategy, "available": False, "reason": str(e)}


# ── Chain assembly ──────────────────────────────────────────────────────────


async def _build_chain(symbol: str, horizon: str) -> dict:
    spot, dividend_yield = await _spot_and_dividend(symbol)
    if not spot:
        return {
            "symbol": symbol,
            "expiries": [],
            "contracts": [],
            "error": "No spot price available for this symbol.",
        }

    risk_free = await _risk_free_rate()
    today = date.today()

    expiry_strings = await asyncio.to_thread(_list_expiries, symbol)
    expiries = select_expiries([d for d in map(_parse_date, expiry_strings) if d], today, horizon)
    if not expiries:
        return {
            "symbol": symbol,
            "spot": spot,
            "expiries": [],
            "contracts": [],
            "error": "No expiries available in the screened horizons.",
        }

    raw_chains = await _fetch_expiries(symbol, expiries)

    contracts: list[Contract] = []
    summaries: list[dict] = []
    partial: list[dict] = []

    for expiry, result in zip(expiries, raw_chains):
        if isinstance(result, Exception):
            partial.append({"expiry": expiry.isoformat(), "reason": str(result)})
            continue
        calls, puts = result
        parsed = _normalise_expiry(
            symbol, expiry, today, calls, puts, spot, risk_free, dividend_yield
        )
        contracts.extend(parsed)
        summaries.append(_summarise_expiry(expiry, today, parsed, calls, puts))

    contracts = _cap(contracts, spot)
    atm_iv = _atm_iv_near_30d(summaries)
    volatility = await get_vol_provider().vol_context(symbol, atm_iv)

    warnings = []
    if partial:
        warnings.append(f"{len(partial)} expiry/expiries could not be read.")
    if not contracts:
        warnings.append("No contracts passed the liquidity filters.")
    if contracts and not any(c.open_interest for c in contracts):
        warnings.append(
            "Open interest is unreported for this chain; liquidity was judged on volume alone."
        )
    if contracts and all("last_price_only" in c.quality for c in contracts):
        warnings.append(
            "The book is empty (no bid/ask), so every premium is a last-traded price "
            "that may be stale. This is normal outside market hours."
        )

    return {
        "symbol": symbol,
        "spot": spot,
        "risk_free_rate": risk_free,
        "dividend_yield": dividend_yield,
        "expiries": summaries,
        "contracts": [_serialise(c) for c in contracts],
        "volatility": volatility,
        "next_earnings": await _next_earnings(symbol),
        "truncated": len(contracts) >= options_screen.MAX_CONTRACTS,
        "partial_expiries": partial,
        "warnings": warnings,
        "as_of": datetime.now(UTC).isoformat(),
    }


def _list_expiries(symbol: str) -> list[str]:
    try:
        return list(yf.Ticker(symbol).options or [])
    except Exception as e:
        log.warning("options_expiries_failed", symbol=symbol, error=str(e))
        return []


async def _fetch_expiries(symbol: str, expiries: list[date]) -> list:
    """Pull each expiry's chain concurrently, bounded.

    Yahoo has no batch endpoint for chains, so this is one request per expiry
    whichever way it is written. The semaphore is what keeps five of them from
    arriving at once and earning a rate limit.
    """
    gate = asyncio.Semaphore(MAX_CONCURRENT_EXPIRIES)

    async def one(expiry: date):
        async with gate:
            return await asyncio.to_thread(_fetch_chain, symbol, expiry)

    return await asyncio.gather(*(one(e) for e in expiries), return_exceptions=True)


def _fetch_chain(symbol: str, expiry: date) -> tuple[list[dict], list[dict]]:
    chain = yf.Ticker(symbol).option_chain(expiry.isoformat())
    calls = chain.calls.to_dict("records") if chain.calls is not None else []
    puts = chain.puts.to_dict("records") if chain.puts is not None else []
    return calls, puts


def _normalise_expiry(
    symbol: str, expiry: date, today: date, calls: list[dict], puts: list[dict],
    spot: float, risk_free: float, dividend_yield: float,
) -> list[Contract]:
    # Yahoo routinely returns openInterest 0 for every row in an expiry while
    # reporting real volume. That is an unpopulated field, not a dead chain, so
    # the OI floor is skipped rather than dropping the whole expiry.
    oi_available = any(_int(r.get("openInterest")) > 0 for r in (*calls, *puts))

    out: list[Contract] = []
    for rows, kind in ((calls, "call"), (puts, "put")):
        for row in rows:
            if not options_screen.within_strike_window(_float(row.get("strike")), spot):
                continue
            contract = normalise(
                row,
                underlying=symbol,
                expiry=expiry,
                today=today,
                kind=kind,
                spot=spot,
                r=risk_free,
                q=dividend_yield,
                oi_available=oi_available,
            )
            if contract:
                out.append(contract)
    return out


def _summarise_expiry(
    expiry: date, today: date, parsed: list[Contract], calls: list[dict], puts: list[dict]
) -> dict:
    call_oi = sum(_int(r.get("openInterest")) for r in calls)
    put_oi = sum(_int(r.get("openInterest")) for r in puts)
    atm_call = _atm_iv(parsed, "call")
    atm_put = _atm_iv(parsed, "put")
    both = [v for v in (atm_call, atm_put) if v is not None]

    return {
        "expiry": expiry.isoformat(),
        "dte": (expiry - today).days,
        "call_count": len(calls),
        "put_count": len(puts),
        "atm_iv_call": atm_call,
        "atm_iv_put": atm_put,
        "atm_iv": sum(both) / len(both) if both else None,
        "total_call_oi": call_oi,
        "total_put_oi": put_oi,
        "put_call_oi_ratio": round(put_oi / call_oi, 4) if call_oi else None,
    }


def _atm_iv(contracts: list[Contract], kind: str) -> float | None:
    """IV of the strike nearest the money, which is the least noisy point."""
    usable = [c for c in contracts if c.kind == kind and c.iv is not None]
    if not usable:
        return None
    # The contracts were already windowed around spot, so the median strike is
    # a good enough stand-in for the money without re-passing spot around.
    strikes = sorted(c.strike for c in usable)
    middle = strikes[len(strikes) // 2]
    nearest = min(usable, key=lambda c: abs(c.strike - middle))
    return nearest.iv


def _atm_iv_near_30d(summaries: list[dict]) -> float | None:
    """The 30-day ATM IV, which is the conventional quote for 'implied vol'."""
    usable = [s for s in summaries if s.get("atm_iv") is not None]
    if not usable:
        return None
    return min(usable, key=lambda s: abs(s["dte"] - 30))["atm_iv"]


def _cap(contracts: list[Contract], spot: float) -> list[Contract]:
    """Keep the strikes closest to the money when a chain is enormous."""
    if len(contracts) <= options_screen.MAX_CONTRACTS:
        return contracts
    ordered = sorted(contracts, key=lambda c: abs(c.strike - spot))
    return ordered[: options_screen.MAX_CONTRACTS]


# ── Inputs from elsewhere ───────────────────────────────────────────────────


async def _spot_and_dividend(symbol: str) -> tuple[float, float]:
    spot = 0.0
    dividend = 0.0

    try:
        quotes = await market_data.get_quotes([symbol])
        if quotes and quotes[0].get("last"):
            spot = float(quotes[0]["last"])
    except Exception as e:
        log.warning("options_spot_failed", symbol=symbol, error=str(e))

    try:
        profile = await market_data.get_profile(symbol)
        dividend = _normalise_dividend_yield(profile.get("dividend_yield"))
    except Exception as e:
        log.warning("options_profile_failed", symbol=symbol, error=str(e))

    return spot, dividend


# yfinance publishes dividendYield in *percent* units: MSFT comes back as 0.74
# for a 0.74% yield, which its own trailingAnnualDividendYield confirms as
# 0.0074. The charting page renders the same field with a literal "%" suffix,
# so percent is the house interpretation too.
#
# This is worth spelling out because getting it wrong is silent and severe. A
# naive "values above 1 must be percent" rule reads 0.74 as 74%, and a 74%
# dividend yield drags e^(-qT) far enough to cap a one-year call's delta around
# 0.6 — which quietly empties the entire LEAPS screen, since nothing can reach
# the 0.70 threshold. Nothing errors; the table is just always blank.
_DECIMAL_YIELD_CEILING = 0.02


def _normalise_dividend_yield(raw) -> float:
    """Coerce a dividend yield to a decimal rate.

    Treats the value as percent, which is what current yfinance returns, while
    tolerating the decimal form older versions used. The two are only ambiguous
    below 0.02, where the reading is either a 2% yield or a 0.02% one — a
    difference too small to move a greek.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if value <= 0:
        return 0.0
    if value > _DECIMAL_YIELD_CEILING:
        value /= 100.0
    # A double-digit "yield" that survives conversion is a data error, not a
    # dividend. Clamp so one bad profile can't distort a whole board.
    return min(value, 0.25)


async def _risk_free_rate() -> float:
    """Short rate from the 13-week T-bill, via the shared quote cache."""
    try:
        quotes = await market_data.get_quotes([RISK_FREE_SYMBOL])
    except Exception:
        return DEFAULT_RISK_FREE
    if not quotes:
        return DEFAULT_RISK_FREE
    last = quotes[0].get("last")
    if last is None:
        return DEFAULT_RISK_FREE
    rate = float(last) / 100.0
    return rate if 0.0 <= rate <= 0.25 else DEFAULT_RISK_FREE


async def _next_earnings(symbol: str) -> str | None:
    """Next earnings date, so contracts spanning it can be flagged.

    An earnings print inside the holding period is the largest unmodelled risk
    in short-dated premium selling, and it costs one cached call to know.
    """
    try:
        event = await asyncio.to_thread(market_data._earnings_for, symbol)
    except Exception:
        return None
    return event.get("date") if event else None


# ── Serialisation ───────────────────────────────────────────────────────────


def _serialise(c: Contract) -> dict:
    return {
        "symbol": c.symbol,
        "expiry": c.expiry.isoformat(),
        "dte": c.dte,
        "strike": c.strike,
        "kind": c.kind,
        "mid": c.mid,
        "bid": c.bid,
        "ask": c.ask,
        "last": c.last,
        "iv": c.iv,
        "open_interest": c.open_interest,
        "volume": c.volume,
        "spread_pct": c.spread_pct,
        "quality": list(c.quality),
    }


def _rehydrate(row: dict) -> Contract | None:
    """Rebuild a Contract from its cached dict form."""
    expiry = _parse_date(row.get("expiry"))
    if expiry is None:
        return None
    return Contract(
        symbol=row.get("symbol", ""),
        expiry=expiry,
        dte=row.get("dte", 0),
        strike=row.get("strike", 0.0),
        kind=row.get("kind", "put"),
        mid=row.get("mid", 0.0),
        bid=row.get("bid"),
        ask=row.get("ask"),
        last=row.get("last"),
        iv=row.get("iv"),
        open_interest=row.get("open_interest", 0),
        volume=row.get("volume", 0),
        spread_pct=row.get("spread_pct"),
        quality=tuple(row.get("quality", ())),
    )


def _parse_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value) -> int:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return 0
    return out if out > 0 else 0
