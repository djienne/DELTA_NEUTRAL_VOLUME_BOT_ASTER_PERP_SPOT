"""Funding-interval resolution and the fee-aware entry gate.

THIS FILE IS COPIED BYTE-IDENTICAL INTO EVERY DELTA-NEUTRAL BOT IN THIS FAMILY,
exactly like two_leg.py. Per-bot variation belongs in the config you pass in, not here.

--------------------------------------------------------------------------------
Why this module exists
--------------------------------------------------------------------------------

Two independent defects, both of which silently selected losing trades:

1. HARDCODED FUNDING INTERVALS.
   Aster runs 1h, 4h AND 8h intervals SIMULTANEOUSLY across symbols -- verified from
   raw nextFundingTime values in these repos' own logs (ASTERUSDT/FARTCOIN/PUMP/XPL
   = 4h; BTC/ETH/BNB/DOGE/LINK/LTC/SOL/XRP = 8h; ATUSDT = 1h). Bots that hardcoded
   `rate * 3 * 365` understated a 4h symbol by 2x and a 1h symbol by 8x. EdgeX is
   likewise per-contract configurable (fundingRateIntervalMin, observed 240).

2. GROSS-APR ENTRY GATES WITH NO FEE TERM.
   Three bots compared gross funding APR against a 5% threshold with no cost model
   anywhere. Verified break-evens: HL/Pacifica 186% APR at an 8h hold, Aster/Lighter
   87.6%, Extended/Pacifica 49.5%. HL_PAC's own state file recorded 11 "successful"
   cycles totalling -$4.46.

   The key relationship, and the reason a bigger threshold alone does not fix it:

       break_even_apr = roundtrip_fee_pct * (8760 / hold_hours)

   Break-even scales as 1/hold_hours. At an 8h hold with HL+Pacifica taker fees the
   bot needs 186% APR just to break even -- a population that is essentially empty,
   so raising the threshold would simply stop it trading. Lengthening the hold is
   the lever that works; the threshold is a derived safety margin on top.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

HOURS_PER_YEAR = 24 * 365  # 8760


# ============================================================================
# Funding interval resolution
# ============================================================================

class IntervalResolutionError(RuntimeError):
    """Raised when a symbol's funding interval cannot be established.

    Callers MUST skip the symbol. Trading it on a guessed constant is how a 4h
    Aster symbol gets its APR halved and ranked below a worse opportunity.
    """


@dataclass(frozen=True)
class FundingInterval:
    hours: float
    source: str          # "api_field" | "empirical" | "constant"
    resolved_at: float

    @property
    def periods_per_day(self) -> float:
        return 24.0 / self.hours

    @property
    def periods_per_year(self) -> float:
        return HOURS_PER_YEAR / self.hours


def annualize(rate_decimal: float, iv: FundingInterval) -> float:
    """Per-interval DECIMAL rate -> APR as a percentage.

    Note `rate_decimal` must be the rate for ONE interval, as a decimal
    (0.0001 = 0.01%). Lighter and Hyperliquid both publish hourly decimals; see
    the cross-venue verification note in funding.py.
    """
    return rate_decimal * iv.periods_per_year * 100.0


# Venues whose interval is uniform and well established. Kept as constants rather
# than fetched, but audited at startup (see audit_constant_interval).
CONSTANT_INTERVALS: Dict[str, float] = {
    "hyperliquid": 1.0,   # 12,061 of 12,062 recorded gaps were exactly 1.0h
    "pacifica": 1.0,      # /api/v1/info funding_rate = "paid in the past funding epoch (hour)"
    "extended": 1.0,      # pays hourly despite an 8h "realization period"
    "lighter": 1.0,       # verified cross-venue against hyperliquid: median ratio 0.9600
}


class FundingIntervalResolver:
    """Resolves and caches per-symbol funding intervals.

    Cache policy that matters: successes are cached with a TTL, failures are NEVER
    cached. The existing bug in DELTA_NEUTRAL_VOLUME_BOT_ASTER's
    detect_funding_interval was that its `except` branch stored the fallback value
    of 3/day, so one transient API error pinned a symbol at the wrong interval for
    the entire life of the container.
    """

    def __init__(self, ttl_s: float = 6 * 3600.0):
        self.ttl_s = ttl_s
        self._cache: Dict[Tuple[str, str], FundingInterval] = {}

    def _get_cached(self, venue: str, symbol: str) -> Optional[FundingInterval]:
        iv = self._cache.get((venue, symbol))
        if iv is None:
            return None
        if (time.time() - iv.resolved_at) > self.ttl_s:
            return None
        return iv

    def constant(self, venue: str) -> FundingInterval:
        """Interval for a venue with a uniform, established cadence."""
        key = venue.lower()
        if key not in CONSTANT_INTERVALS:
            raise IntervalResolutionError(
                f"{venue} has no established constant interval; resolve it per symbol"
            )
        return FundingInterval(CONSTANT_INTERVALS[key], "constant", time.time())

    async def resolve_from_api_field(
        self,
        venue: str,
        symbol: str,
        fetch_interval_hours: Callable[[str], Awaitable[Optional[float]]],
    ) -> FundingInterval:
        """Preferred path: the venue publishes its interval.

        Aster exposes `fundingIntervalHours` on /fapi/v1/fundingInfo; EdgeX exposes
        `fundingRateIntervalMin` in its metadata. Read it -- do not assume.
        """
        cached = self._get_cached(venue, symbol)
        if cached is not None:
            return cached
        try:
            hours = await fetch_interval_hours(symbol)
        except Exception as e:                          # noqa: BLE001
            raise IntervalResolutionError(
                f"{venue}/{symbol}: interval lookup failed ({e})"
            ) from e
        if not hours or hours <= 0:
            raise IntervalResolutionError(
                f"{venue}/{symbol}: venue returned no usable funding interval"
            )
        iv = FundingInterval(float(hours), "api_field", time.time())
        self._cache[(venue, symbol)] = iv
        return iv

    async def resolve_empirically(
        self,
        venue: str,
        symbol: str,
        fetch_funding_times_ms: Callable[[str], Awaitable[Sequence[int]]],
        min_samples: int = 8,
        min_agreement: float = 0.75,
    ) -> FundingInterval:
        """Fallback: derive the interval from consecutive settlement timestamps.

        Requires `min_samples` records and `min_agreement` of the gaps to agree.
        Two records is NOT enough -- one irregular gap (a venue outage, a listing)
        would silently produce the wrong constant for every subsequent APR.
        """
        cached = self._get_cached(venue, symbol)
        if cached is not None:
            return cached
        try:
            times = sorted(set(int(t) for t in await fetch_funding_times_ms(symbol)))
        except Exception as e:                          # noqa: BLE001
            raise IntervalResolutionError(
                f"{venue}/{symbol}: funding history fetch failed ({e})"
            ) from e

        if len(times) < min_samples:
            raise IntervalResolutionError(
                f"{venue}/{symbol}: only {len(times)} funding timestamps, need "
                f"{min_samples} to infer an interval"
            )

        gaps = [round((b - a) / 3_600_000.0, 3) for a, b in zip(times, times[1:])]
        gaps = [g for g in gaps if g > 0]
        if not gaps:
            raise IntervalResolutionError(f"{venue}/{symbol}: no positive gaps")

        mode, count = Counter(gaps).most_common(1)[0]
        agreement = count / len(gaps)
        if agreement < min_agreement:
            raise IntervalResolutionError(
                f"{venue}/{symbol}: funding gaps do not agree "
                f"({agreement:.0%} at {mode}h, need {min_agreement:.0%}). "
                f"Refusing to guess."
            )

        iv = FundingInterval(float(mode), "empirical", time.time())
        self._cache[(venue, symbol)] = iv
        logger.info("%s/%s: resolved %sh funding interval empirically (%.0f%% agreement)",
                    venue, symbol, mode, agreement * 100)
        return iv


async def audit_constant_interval(
    venue: str,
    symbol: str,
    fetch_funding_times_ms: Callable[[str], Awaitable[Sequence[int]]],
) -> None:
    """Startup sanity check for venues we treat as a constant.

    Warns rather than raising: a venue changing cadence is rare, and a failed audit
    must not stop a bot that is otherwise healthy. But it must be VISIBLE, because
    the constant would otherwise be wrong forever.
    """
    expected = CONSTANT_INTERVALS.get(venue.lower())
    if expected is None:
        return
    try:
        resolver = FundingIntervalResolver()
        observed = await resolver.resolve_empirically(venue, symbol, fetch_funding_times_ms)
    except IntervalResolutionError as e:
        logger.info("Interval audit for %s/%s inconclusive (%s). Keeping constant %sh.",
                    venue, symbol, e, expected)
        return
    if abs(observed.hours - expected) > 0.01:
        logger.warning(
            "INTERVAL DRIFT: %s/%s observed %sh but this bot assumes %sh. Every APR "
            "for this venue is off by %.2fx. Update CONSTANT_INTERVALS.",
            venue, symbol, observed.hours, expected, expected / observed.hours,
        )


# ============================================================================
# Fee-aware entry gate
# ============================================================================

@dataclass(frozen=True)
class VenueCosts:
    """Per-venue round-trip cost inputs, in basis points."""
    name: str
    taker_bps: float
    slippage_bps: float = 0.0
    source: str = "config"

    @property
    def one_way_bps(self) -> float:
        return self.taker_bps + self.slippage_bps


@dataclass(frozen=True)
class TradeCostModel:
    legs: Tuple[VenueCosts, ...]

    def roundtrip_bps(self) -> float:
        """Entry + exit on every leg."""
        return sum(2.0 * leg.one_way_bps for leg in self.legs)

    def roundtrip_pct(self) -> float:
        return self.roundtrip_bps() / 100.0


def break_even_apr_pct(cost: TradeCostModel, hold_hours: float) -> float:
    """Gross APR at which a cycle exactly pays for itself.

        break_even = roundtrip_pct * (8760 / hold_hours)

    Break-even scales as 1/hold_hours, which is why lengthening the hold is the
    effective lever and raising the threshold alone often is not: at an 8h hold on
    HL+Pacifica fees the break-even is 186% APR, and that population is empty.
    """
    if hold_hours <= 0:
        raise ValueError("hold_hours must be > 0")
    return cost.roundtrip_pct() * (HOURS_PER_YEAR / hold_hours)


@dataclass(frozen=True)
class EntryDecision:
    accept: bool
    reason: str
    gross_apr_pct: float
    expected_apr_pct: float
    break_even_apr_pct: float
    margin_ratio: float
    expected_funding_usd: float
    expected_cost_usd: float
    expected_net_usd: float


def evaluate_entry(
    *,
    symbol: str,
    gross_net_apr_pct: float,
    notional_usd: float,
    hold_hours: float,
    cost: TradeCostModel,
    min_margin_ratio: float = 2.0,
    min_net_usd: float = 0.0,
    apr_haircut_pct: float = 0.30,
) -> EntryDecision:
    """Decide whether a cycle is worth opening, net of cost.

    Replaces `net_apr = abs(a - b) >= threshold`, where "net" only ever meant net of
    the OTHER LEG'S FUNDING -- never net of trading cost.

    `apr_haircut_pct` discounts the spot funding rate because forward funding decays:
    the rate observed at entry is not the mean realised over the hold. 30% is
    deliberately conservative; it is cheaper to skip a marginal trade than to pay
    four taker fills for a spread that evaporated.

    Returns a decision even on reject, so the opportunity table can show break-even
    and margin per row instead of a bare gross number.
    """
    be = break_even_apr_pct(cost, hold_hours)
    expected_apr = gross_net_apr_pct * (1.0 - apr_haircut_pct)

    hold_fraction = hold_hours / HOURS_PER_YEAR
    expected_funding = (expected_apr / 100.0) * hold_fraction * notional_usd
    expected_cost = cost.roundtrip_pct() / 100.0 * notional_usd
    expected_net = expected_funding - expected_cost

    margin_ratio = (expected_apr / be) if be > 0 else float("inf")

    if expected_net < min_net_usd:
        return EntryDecision(
            False,
            f"net ${expected_net:.2f} < min ${min_net_usd:.2f} "
            f"(funding ${expected_funding:.2f} - cost ${expected_cost:.2f})",
            gross_net_apr_pct, expected_apr, be, margin_ratio,
            expected_funding, expected_cost, expected_net,
        )

    if margin_ratio < min_margin_ratio:
        return EntryDecision(
            False,
            f"margin {margin_ratio:.2f}x < required {min_margin_ratio:.2f}x "
            f"(expected {expected_apr:.1f}% vs break-even {be:.1f}%)",
            gross_net_apr_pct, expected_apr, be, margin_ratio,
            expected_funding, expected_cost, expected_net,
        )

    return EntryDecision(
        True,
        f"accept: {expected_apr:.1f}% vs break-even {be:.1f}% "
        f"({margin_ratio:.2f}x), net ${expected_net:.2f}",
        gross_net_apr_pct, expected_apr, be, margin_ratio,
        expected_funding, expected_cost, expected_net,
    )


def recommended_hold_hours(cost: TradeCostModel, target_break_even_apr_pct: float) -> float:
    """Hold length needed to bring break-even down to a target APR.

    Inverts break_even_apr_pct. Use it to answer "how long must I hold for a 20% APR
    opportunity to be worth taking?" rather than guessing at a config value.
    """
    if target_break_even_apr_pct <= 0:
        raise ValueError("target must be > 0")
    return cost.roundtrip_pct() * HOURS_PER_YEAR / target_break_even_apr_pct


# Verified taker fees, 2026-08-16, from each venue's published fee page.
# Slippage is NOT included here -- add it per bot from the configured cross-spread
# allowance, or better, from the measured EWMA of realised slippage.
VERIFIED_TAKER_BPS: Dict[str, float] = {
    "hyperliquid": 4.5,
    "pacifica": 4.0,
    "aster": 4.0,
    "extended": 2.5,
    "lighter": 0.0,     # genuinely zero-fee on the standard account
    "edgex": 4.0,       # not independently verified; treated as peer-equivalent
}
