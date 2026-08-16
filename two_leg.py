"""Canonical two-leg execution safety primitive.

THIS FILE IS COPIED BYTE-IDENTICAL INTO EVERY DELTA-NEUTRAL BOT IN THIS FAMILY.
Do not add bot-specific logic here. All per-bot variation belongs in the `LegSpec`
you construct (which callables, which settle delay, which venue is the pilot).
If you need to change behaviour, change it here and re-copy to every bot.

--------------------------------------------------------------------------------
The rule this module exists to enforce
--------------------------------------------------------------------------------

    SUBMITTING AN ORDER NEVER YIELDS "FILLED".

Submission can only distinguish *definitely dead* from *maybe live*. Only reading
the position back from the venue can promote a leg to FILLED.

Every bot in this family violated that rule, in the same way: it fired both legs
with `asyncio.gather(..., return_exceptions=True)` and then set `success = True`
without inspecting either result. The venue clients made this easy to get wrong,
because each signals failure differently:

    Lighter   -> returns None, or False
    EdgeX     -> returns a dict with code != "SUCCESS"
    Pacifica  -> catches internally and returns None
    Aster     -> raises

So a rejected leg was indistinguishable from a filled one, the bot logged
"Successfully opened position", and it held naked directional exposure it believed
was hedged. Several bots then wiped their own state, forgetting the live leg
permanently.

--------------------------------------------------------------------------------
Design decisions worth knowing before you change anything
--------------------------------------------------------------------------------

1. LEGS ARE SUBMITTED SEQUENTIALLY, NOT IN PARALLEL.
   Parallel submission maximises the window in which both legs are live and
   unverified, and makes the failure case undecidable: if both come back UNKNOWN
   you cannot tell which one to unwind. Sequential costs a few seconds of delta
   exposure. A naked leg costs an unbounded amount. This is a deliberate trade.

2. THE PILOT IS THE VENUE WHOSE ORDERS CAN REST SILENTLY.
   Lighter (GOOD_TILL_TIME) and EdgeX (GTC) can leave an unfilled remainder on the
   book that fills minutes or hours later. Submit those first, cancel, and verify
   before committing the other leg. The hedge leg is sized from what the pilot
   ACTUALLY filled, never from the original intent.

3. UNKNOWN IS NOT REJECTED.
   A timeout or dropped connection may or may not have filled. The only safe
   response is cancel-then-query-then-decide. Never blind-retry a submission: the
   venue clients generate a fresh order id per attempt, so a retry after a response
   timeout can double the leg.

4. A FAILED UNWIND IS TERMINAL AND LOUD.
   It writes a `halt.json` sentinel, and the bot refuses to trade while that file
   exists. The container stays up, idle and noisy. This replaces the previous
   behaviour where a failed unwind hit a `return`/`break`, the process exited, and
   `restart: unless-stopped` brought it straight back into the same failure with
   money live.

5. THIS MODULE NEVER IMPORTS A VENUE CLIENT.
   Everything arrives as a callable on `LegSpec`, so the whole module is testable
   with `AsyncMock` and no network. That matters here: these bots cannot be
   validated with real money, so unit tests are the only verification available.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


# ============================================================================
# Normalised leg result
# ============================================================================

class LegStatus(str, Enum):
    """Outcome of one leg.

    FILLED / PARTIAL are reachable ONLY via `verify_fill`. `classify_submission`
    can return REJECTED or UNKNOWN and nothing else -- that is the whole point.
    """
    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"
    UNKNOWN = "unknown"

    @property
    def is_terminal_dead(self) -> bool:
        """True when we are certain nothing is live on the venue for this leg."""
        return self is LegStatus.REJECTED

    @property
    def may_be_live(self) -> bool:
        """True when the venue may be holding size or a resting order for us."""
        return self in (LegStatus.FILLED, LegStatus.PARTIAL, LegStatus.UNKNOWN)


@dataclass(frozen=True)
class LegResult:
    venue: str
    symbol: str
    side: str                       # "buy" | "sell"
    intent_qty: float               # unsigned size we asked for
    status: LegStatus
    filled_qty: float = 0.0         # unsigned size actually observed filled
    order_ref: Optional[str] = None
    raw: Any = None
    error: Optional[str] = None

    @property
    def residual_qty(self) -> float:
        """Unsigned size still owed relative to intent. Never negative."""
        return max(0.0, self.intent_qty - self.filled_qty)


# Exceptions that mean "the request may or may not have landed".
# Anything else is treated as a definite rejection.
_AMBIGUOUS_EXCEPTIONS: Tuple[type, ...] = (
    asyncio.TimeoutError,
    asyncio.CancelledError,
    TimeoutError,
    ConnectionError,
    OSError,
)


def classify_submission(
    venue: str,
    raw: Any,
    *,
    intent_qty: float,
    symbol: str,
    side: str,
) -> LegResult:
    """Normalise a venue submission response into REJECTED or UNKNOWN.

    Deliberately never returns FILLED or PARTIAL. Submission tells you only
    whether the request is definitely dead, or possibly live.

    `raw` may be the client's return value OR the exception it raised -- callers
    should catch and pass the exception in rather than letting it propagate, so
    that an ambiguous network error is not mistaken for a clean rejection.
    """
    def _reject(err: str) -> LegResult:
        return LegResult(venue=venue, symbol=symbol, side=side, intent_qty=intent_qty,
                         status=LegStatus.REJECTED, raw=raw, error=err)

    def _unknown(ref: Optional[str], err: Optional[str] = None) -> LegResult:
        return LegResult(venue=venue, symbol=symbol, side=side, intent_qty=intent_qty,
                         status=LegStatus.UNKNOWN, order_ref=ref, raw=raw, error=err)

    # --- exceptions -------------------------------------------------------
    if isinstance(raw, BaseException):
        if isinstance(raw, _AMBIGUOUS_EXCEPTIONS):
            # Timed out / connection dropped: the order may well be resting.
            return _unknown(None, f"{raw.__class__.__name__}: {raw}")
        return _reject(f"{raw.__class__.__name__}: {raw}")

    # --- explicit failure sentinels --------------------------------------
    # Lighter returns None on error (lighter_client) and False from its close
    # helper. Both are unambiguous failures.
    if raw is None:
        return _reject("client returned None")
    if raw is False:
        return _reject("client returned False")
    if raw is True:
        # A bare True means "accepted", not "filled".
        return _unknown(None)

    # --- dict-shaped responses (EdgeX, Pacifica REST) ---------------------
    if isinstance(raw, dict):
        code = raw.get("code")
        if code is not None and str(code).upper() != "SUCCESS":
            return _reject(f"code={code!r} msg={raw.get('msg') or raw.get('message')!r}")
        if raw.get("success") is False:
            return _reject(f"success=False msg={raw.get('msg') or raw.get('message')!r}")
        if raw.get("error"):
            return _reject(f"error={raw['error']!r}")
        ref = raw.get("order_id") or raw.get("orderId") or raw.get("id")
        data = raw.get("data")
        if ref is None and isinstance(data, dict):
            ref = data.get("order_id") or data.get("orderId") or data.get("id")
        return _unknown(str(ref) if ref is not None else None)

    # --- order id / opaque truthy ----------------------------------------
    return _unknown(str(raw))


# ============================================================================
# Leg specification
# ============================================================================

# read_position() -> signed position size (positive long, negative short)
ReadPosition = Callable[[], Awaitable[float]]
# submit(qty) -> whatever the venue client returns
Submit = Callable[[float], Awaitable[Any]]
# close_market(qty, side) -> whatever the venue client returns
CloseMarket = Callable[[float, str], Awaitable[Any]]
# cancel_open() -> number of orders cancelled
CancelOpen = Callable[[], Awaitable[int]]


@dataclass
class LegSpec:
    """Everything the primitive needs to drive one venue.

    All venue coupling lives here. `two_leg.py` itself imports nothing venue-specific.
    """
    name: str
    symbol: str
    side: str                       # "buy" | "sell" for the OPEN direction
    intent_qty: float               # unsigned
    submit: Submit
    read_position: ReadPosition
    close_market: CloseMarket
    cancel_open: CancelOpen
    amount_tick: float
    # How long to wait before the first position read. Lighter needs ~3s for zk
    # batch inclusion; reading at zero delay is what made `_reconcile_legs` raise
    # spurious mismatches and emergency-close correctly-opened hedges.
    settle_delay_s: float = 1.0

    @property
    def intent_signed(self) -> float:
        return self.intent_qty if self.side == "buy" else -self.intent_qty

    @property
    def close_side(self) -> str:
        """Side that reduces an open position created by `side`."""
        return "sell" if self.side == "buy" else "buy"


@dataclass
class TwoLegOutcome:
    ok: bool
    pilot: Optional[LegResult] = None
    hedge: Optional[LegResult] = None
    halted: bool = False
    reason: str = ""
    # Unsigned size that is confirmed hedged on BOTH venues.
    hedged_qty: float = 0.0
    notes: list = field(default_factory=list)


# ============================================================================
# Fill verification
# ============================================================================

def _tolerance(intent_qty: float, amount_tick: float) -> float:
    """Fill tolerance: a couple of lot ticks, or 0.5% of intent, whichever is larger."""
    return max(2.0 * abs(amount_tick), 0.005 * abs(intent_qty))


async def verify_fill(
    read_position: ReadPosition,
    *,
    baseline_signed_qty: float,
    intent_signed_delta: float,
    amount_tick: float,
    settle_delay_s: float,
    poll_interval_s: float = 1.0,
    timeout_s: float = 20.0,
    stable_reads: int = 2,
) -> Tuple[LegStatus, float]:
    """Read the venue back and decide what actually happened.

    IMPORTANT: cancel resting orders BEFORE calling this. Otherwise a "stable zero
    fill" is not really zero -- an untouched GTT order can still fill later, and
    you will have concluded REJECTED about a leg that is about to go live.

    Returns (status, filled_signed_delta).

    A timeout yields UNKNOWN, never "mismatch". The difference matters: mismatch
    invites an emergency close, UNKNOWN invites another look.
    """
    tol = _tolerance(intent_signed_delta, amount_tick)

    await asyncio.sleep(max(0.0, settle_delay_s))

    deadline = asyncio.get_event_loop().time() + max(0.0, timeout_s)
    agree = 0
    last_delta: Optional[float] = None

    while True:
        try:
            actual = await read_position()
            delta = float(actual) - float(baseline_signed_qty)
        except Exception as exc:                        # noqa: BLE001 - see below
            # A failed read is not evidence of anything. Never fall through to 0.0
            # here: the ASTER_LIGHTER bot's "position read fails open to 0.0" bug is
            # exactly this, and it reported "closed successfully on both exchanges"
            # while a leg stayed open and was forgotten permanently.
            logger.warning("verify_fill: position read failed (%s). Retrying.", exc)
            delta = None  # type: ignore[assignment]

        if delta is not None:
            if last_delta is not None and abs(delta - last_delta) <= tol:
                agree += 1
            else:
                agree = 1
            last_delta = delta

            if agree >= stable_reads:
                return _classify_delta(delta, intent_signed_delta, tol), delta

        if asyncio.get_event_loop().time() >= deadline:
            logger.warning(
                "verify_fill: timed out after %.1fs without a stable read "
                "(last delta=%s, intent=%s). Reporting UNKNOWN.",
                timeout_s, last_delta, intent_signed_delta,
            )
            return LegStatus.UNKNOWN, (last_delta if last_delta is not None else 0.0)

        await asyncio.sleep(max(0.05, poll_interval_s))


def _classify_delta(delta: float, intent: float, tol: float) -> LegStatus:
    """Map an observed position change onto a status."""
    if abs(delta) <= tol:
        return LegStatus.REJECTED                   # nothing moved
    if intent != 0 and delta * intent < 0:
        # Moved the wrong way entirely -- do not call this a fill.
        return LegStatus.UNKNOWN
    if abs(delta) + tol >= abs(intent):
        return LegStatus.FILLED
    return LegStatus.PARTIAL


# ============================================================================
# Halt sentinel
# ============================================================================

HALT_FILENAME = "halt.json"


def write_halt(reason: str, *, symbol: str, venue: str, residual_qty: float,
               path: str = HALT_FILENAME, extra: Optional[dict] = None) -> None:
    """Record an unrecoverable state and refuse to trade until a human clears it.

    Written atomically so a crash mid-write cannot leave an unparseable sentinel
    that gets ignored on the next boot.
    """
    payload = {
        "reason": reason,
        "symbol": symbol,
        "venue": venue,
        "residual_qty": residual_qty,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        payload.update(extra)

    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".halt-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    logger.critical(
        "HALT written to %s: %s (%s %s residual=%s). "
        "The bot will refuse to trade until this file is removed. "
        "Check BOTH venues manually before clearing it.",
        path, reason, venue, symbol, residual_qty,
    )


def read_halt(path: str = HALT_FILENAME) -> Optional[dict]:
    """Return the halt payload, or None when not halted."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        # ValueError covers json.JSONDecodeError. An unreadable sentinel still
        # means halted -- failing open here would defeat the entire mechanism.
        logger.critical("Halt sentinel %s exists but is unreadable (%s). Treating as HALTED.",
                        path, exc)
        return {"reason": "unreadable halt sentinel", "error": str(exc)}


def assert_not_halted(path: str = HALT_FILENAME) -> None:
    """Raise if a halt sentinel is present. Call this at boot and before every open."""
    halt = read_halt(path)
    if halt is not None:
        raise HaltedError(
            f"Refusing to trade: halt sentinel present at {path}. "
            f"reason={halt.get('reason')!r} venue={halt.get('venue')!r} "
            f"symbol={halt.get('symbol')!r} residual={halt.get('residual_qty')!r} "
            f"written_at={halt.get('written_at')!r}"
        )


class HaltedError(RuntimeError):
    """Raised when a halt sentinel blocks trading."""


# ============================================================================
# Unwind
# ============================================================================

async def unwind_leg(
    leg: LegSpec,
    *,
    baseline_signed_qty: float,
    attempts: int = 3,
    halt_path: str = HALT_FILENAME,
    backoff_base_s: float = 1.0,
) -> bool:
    """Flatten `leg` back to `baseline_signed_qty`. Returns True on success.

    On exhaustion this writes a halt sentinel. That is intentional: an unwind that
    cannot complete means real, unhedged, unmanaged exposure, and continuing to
    trade on top of it is strictly worse than stopping.
    """
    for attempt in range(1, attempts + 1):
        try:
            await leg.cancel_open()
        except Exception as exc:                        # noqa: BLE001
            logger.warning("unwind %s: cancel_open failed (%s)", leg.name, exc)

        try:
            actual = await leg.read_position()
        except Exception as exc:                        # noqa: BLE001
            logger.warning("unwind %s: position read failed (%s); attempt %d/%d",
                           leg.name, exc, attempt, attempts)
            await asyncio.sleep(min(9.0, backoff_base_s * (3 ** (attempt - 1))))
            continue

        residual = float(actual) - float(baseline_signed_qty)
        tol = _tolerance(leg.intent_qty, leg.amount_tick)
        if abs(residual) <= tol:
            logger.info("unwind %s: flat (residual=%.10g within tol=%.10g)",
                        leg.name, residual, tol)
            return True

        close_side = "sell" if residual > 0 else "buy"
        logger.warning("unwind %s: closing residual %.10g via %s (attempt %d/%d)",
                       leg.name, residual, close_side, attempt, attempts)
        try:
            await leg.close_market(abs(residual), close_side)
        except Exception as exc:                        # noqa: BLE001
            logger.warning("unwind %s: close_market raised (%s)", leg.name, exc)

        await asyncio.sleep(min(9.0, backoff_base_s * (3 ** (attempt - 1))))

    # Final read so the sentinel records the true residual, not the last guess.
    try:
        actual = await leg.read_position()
        residual = float(actual) - float(baseline_signed_qty)
    except Exception:                                   # noqa: BLE001
        residual = float("nan")

    write_halt(
        f"unwind failed after {attempts} attempts",
        symbol=leg.symbol, venue=leg.name, residual_qty=residual, path=halt_path,
    )
    return False


# ============================================================================
# The primitive
# ============================================================================

async def execute_two_leg(
    pilot: LegSpec,
    hedge: LegSpec,
    *,
    unwind_attempts: int = 3,
    hedge_topup_attempts: int = 2,
    min_notional_qty: float = 0.0,
    halt_path: str = HALT_FILENAME,
    verify_timeout_s: float = 20.0,
    unwind_backoff_base_s: float = 1.0,
    verify_poll_interval_s: float = 1.0,
) -> TwoLegOutcome:
    """Open a hedged pair, or leave nothing behind.

    Order of operations, per leg: sweep -> submit -> cancel -> verify -> decide.
    The hedge leg is sized from the pilot's ACTUAL fill.

    Raises HaltedError immediately if a halt sentinel is already present.
    """
    assert_not_halted(halt_path)

    notes: list = []

    # Baselines. Everything downstream is measured as a delta from these, so a
    # pre-existing position cannot be mistaken for our own fill.
    try:
        pilot_baseline = float(await pilot.read_position())
        hedge_baseline = float(await hedge.read_position())
    except Exception as exc:                            # noqa: BLE001
        return TwoLegOutcome(ok=False, reason=f"baseline position read failed: {exc}")

    # Clear anything resting from a previous cycle before we add to the book.
    for leg in (pilot, hedge):
        try:
            n = await leg.cancel_open()
            if n:
                notes.append(f"swept {n} stale order(s) on {leg.name}")
        except Exception as exc:                        # noqa: BLE001
            logger.warning("pre-open sweep failed on %s: %s", leg.name, exc)

    # ---- pilot leg -------------------------------------------------------
    pilot_res = await _submit_and_verify(
        pilot, baseline=pilot_baseline, timeout_s=verify_timeout_s,
        poll_interval_s=verify_poll_interval_s,
    )

    if pilot_res.status is LegStatus.REJECTED:
        return TwoLegOutcome(ok=False, pilot=pilot_res, reason="pilot rejected; nothing live",
                             notes=notes)

    if pilot_res.status is LegStatus.UNKNOWN:
        # Cancel + re-verify already happened inside _submit_and_verify. Still
        # unknown means we genuinely cannot tell -- unwind whatever is there.
        ok = await unwind_leg(pilot, baseline_signed_qty=pilot_baseline,
                              attempts=unwind_attempts, halt_path=halt_path,
                              backoff_base_s=unwind_backoff_base_s)
        return TwoLegOutcome(ok=False, pilot=pilot_res, halted=not ok,
                             reason="pilot outcome unknown; unwound" if ok
                                    else "pilot outcome unknown; UNWIND FAILED",
                             notes=notes)

    if pilot_res.status is LegStatus.PARTIAL and pilot_res.filled_qty < min_notional_qty:
        ok = await unwind_leg(pilot, baseline_signed_qty=pilot_baseline,
                              attempts=unwind_attempts, halt_path=halt_path,
                              backoff_base_s=unwind_backoff_base_s)
        return TwoLegOutcome(ok=False, pilot=pilot_res, halted=not ok,
                             reason="pilot fill below min notional; unwound" if ok
                                    else "pilot fill below min notional; UNWIND FAILED",
                             notes=notes)

    # Size the hedge from what actually filled, never from the original intent.
    hedge_qty = pilot_res.filled_qty
    if abs(hedge_qty - hedge.intent_qty) > _tolerance(hedge.intent_qty, hedge.amount_tick):
        notes.append(f"hedge resized {hedge.intent_qty:.10g} -> {hedge_qty:.10g} "
                     f"to match pilot fill")
    hedge = _with_qty(hedge, hedge_qty)

    # ---- hedge leg -------------------------------------------------------
    hedge_res = await _submit_and_verify(
        hedge, baseline=hedge_baseline, timeout_s=verify_timeout_s,
        poll_interval_s=verify_poll_interval_s,
    )

    # Top up a partial hedge before giving up on the pair.
    topups = 0
    while (hedge_res.status is LegStatus.PARTIAL and topups < hedge_topup_attempts):
        residual = hedge_res.residual_qty
        if residual <= _tolerance(hedge.intent_qty, hedge.amount_tick):
            break
        topups += 1
        notes.append(f"hedge top-up {topups}/{hedge_topup_attempts} for {residual:.10g}")
        topup = _with_qty(hedge, residual)
        # Baseline moves as the position grows.
        try:
            current = float(await hedge.read_position())
        except Exception as exc:                        # noqa: BLE001
            notes.append(f"top-up aborted, position read failed: {exc}")
            break
        top_res = await _submit_and_verify(topup, baseline=current, timeout_s=verify_timeout_s,
                                          poll_interval_s=verify_poll_interval_s)
        total_filled = hedge_res.filled_qty + top_res.filled_qty
        hedge_res = LegResult(
            venue=hedge_res.venue, symbol=hedge_res.symbol, side=hedge_res.side,
            intent_qty=hedge_res.intent_qty,
            status=_classify_delta(
                total_filled if hedge.side == "buy" else -total_filled,
                hedge.intent_signed,
                _tolerance(hedge.intent_qty, hedge.amount_tick),
            ),
            filled_qty=total_filled, order_ref=top_res.order_ref, raw=top_res.raw,
        )

    # ---- decision table --------------------------------------------------
    if hedge_res.status is LegStatus.FILLED:
        hedged = min(pilot_res.filled_qty, hedge_res.filled_qty)
        # Any leftover imbalance is real, unhedged delta. Trim the longer leg.
        imbalance = abs(pilot_res.filled_qty - hedge_res.filled_qty)
        if imbalance > _tolerance(pilot_res.intent_qty, pilot.amount_tick):
            notes.append(f"legs imbalanced by {imbalance:.10g}; trimming to {hedged:.10g}")
            longer, longer_base = ((pilot, pilot_baseline)
                                   if pilot_res.filled_qty > hedge_res.filled_qty
                                   else (hedge, hedge_baseline))
            target = longer_base + (hedged if longer.side == "buy" else -hedged)
            if not await unwind_leg(_with_qty(longer, imbalance),
                                    baseline_signed_qty=target,
                                    attempts=unwind_attempts, halt_path=halt_path,
                              backoff_base_s=unwind_backoff_base_s):
                return TwoLegOutcome(ok=False, pilot=pilot_res, hedge=hedge_res, halted=True,
                                     reason="imbalance trim FAILED", notes=notes)
        return TwoLegOutcome(ok=True, pilot=pilot_res, hedge=hedge_res,
                             hedged_qty=hedged, reason="both legs filled", notes=notes)

    # Hedge did not complete -> the pilot is naked. Unwind it.
    reason_prefix = {
        LegStatus.REJECTED: "hedge rejected",
        LegStatus.UNKNOWN: "hedge outcome unknown",
        LegStatus.PARTIAL: "hedge only partially filled",
    }[hedge_res.status]

    # Unwind the hedge remainder first (if any), then the pilot entirely.
    if hedge_res.status is not LegStatus.REJECTED:
        if not await unwind_leg(hedge, baseline_signed_qty=hedge_baseline,
                                attempts=unwind_attempts, halt_path=halt_path,
                              backoff_base_s=unwind_backoff_base_s):
            return TwoLegOutcome(ok=False, pilot=pilot_res, hedge=hedge_res, halted=True,
                                 reason=f"{reason_prefix}; hedge UNWIND FAILED", notes=notes)

    if not await unwind_leg(pilot, baseline_signed_qty=pilot_baseline,
                            attempts=unwind_attempts, halt_path=halt_path,
                              backoff_base_s=unwind_backoff_base_s):
        return TwoLegOutcome(ok=False, pilot=pilot_res, hedge=hedge_res, halted=True,
                             reason=f"{reason_prefix}; pilot UNWIND FAILED", notes=notes)

    return TwoLegOutcome(ok=False, pilot=pilot_res, hedge=hedge_res,
                         reason=f"{reason_prefix}; both legs unwound", notes=notes)


def _with_qty(leg: LegSpec, qty: float) -> LegSpec:
    """Copy of `leg` with a different intent size."""
    return LegSpec(
        name=leg.name, symbol=leg.symbol, side=leg.side, intent_qty=qty,
        submit=leg.submit, read_position=leg.read_position,
        close_market=leg.close_market, cancel_open=leg.cancel_open,
        amount_tick=leg.amount_tick, settle_delay_s=leg.settle_delay_s,
    )


async def _submit_and_verify(leg: LegSpec, *, baseline: float,
                             timeout_s: float,
                             poll_interval_s: float = 1.0) -> LegResult:
    """submit -> cancel resting remainder -> verify against the venue."""
    try:
        raw: Any = await leg.submit(leg.intent_qty)
    except BaseException as exc:                        # noqa: BLE001
        raw = exc

    submitted = classify_submission(
        leg.name, raw, intent_qty=leg.intent_qty, symbol=leg.symbol, side=leg.side,
    )

    if submitted.status is LegStatus.REJECTED:
        # Definitely dead. Still sweep: a prior partial may be resting.
        try:
            await leg.cancel_open()
        except Exception as exc:                        # noqa: BLE001
            logger.warning("%s: post-reject sweep failed (%s)", leg.name, exc)
        return submitted

    # Possibly live. Cancel the remainder BEFORE reading, so that a zero reading
    # genuinely means zero rather than "hasn't filled yet".
    try:
        await leg.cancel_open()
    except Exception as exc:                            # noqa: BLE001
        logger.warning("%s: pre-verify cancel failed (%s); "
                       "a resting remainder may still fill", leg.name, exc)

    status, delta = await verify_fill(
        leg.read_position,
        baseline_signed_qty=baseline,
        intent_signed_delta=leg.intent_signed,
        amount_tick=leg.amount_tick,
        settle_delay_s=leg.settle_delay_s,
        poll_interval_s=poll_interval_s,
        timeout_s=timeout_s,
    )

    return LegResult(
        venue=leg.name, symbol=leg.symbol, side=leg.side, intent_qty=leg.intent_qty,
        status=status, filled_qty=abs(delta), order_ref=submitted.order_ref,
        raw=submitted.raw, error=submitted.error,
    )


# ============================================================================
# Boot reconciliation
# ============================================================================

@dataclass
class BootDecision:
    flat: bool
    positions: dict                  # {venue_name: {symbol: signed_qty}}
    conflicts: list = field(default_factory=list)
    reason: str = ""


async def boot_reconcile(
    venues: Sequence[Tuple[str, Callable[[], Awaitable[dict]]]],
    *,
    state_symbols: Sequence[str] = (),
    configured_symbols: Sequence[str] = (),
    halt_path: str = HALT_FILENAME,
) -> BootDecision:
    """Establish ground truth from the venues before trusting any state file.

    `venues` is a sequence of (name, list_all_positions) where the callable
    returns {symbol: signed_qty} for EVERY symbol with a non-zero position --
    an account-level listing, not a per-symbol loop over the config.

    That distinction is the entire point. Recovery that only scans
    `symbols_to_monitor` cannot see a position in a symbol that has since been
    removed from the config. In this codebase that already happened: a live
    FARTCOIN hedge was invisible to a scan configured for BTC/ETH/SOL/BNB/
    ASTER/DOGE, so the bot logged "No existing positions found", wiped its state,
    and would have opened a second position on top of an abandoned one.

    Venue truth outranks the state file unconditionally. The state file
    contributes metadata (opened_at, entry balances) and nothing else.
    """
    assert_not_halted(halt_path)

    positions: dict = {}
    conflicts: list = []

    for name, list_positions in venues:
        try:
            found = await list_positions()
        except Exception as exc:                        # noqa: BLE001
            # A failed venue listing is NOT "no positions". Refusing to proceed is
            # the only safe response -- assuming flat here is how a live leg gets
            # forgotten and then double-opened.
            raise BootReconcileError(
                f"{name}: account-level position listing failed ({exc}). "
                f"Refusing to reconcile against an unknown venue state."
            ) from exc
        positions[name] = {s: q for s, q in (found or {}).items() if q}

    universe = set(configured_symbols) | set(state_symbols)
    for venue_positions in positions.values():
        universe |= set(venue_positions)

    for sym in sorted(universe):
        legs = {v: p.get(sym, 0.0) for v, p in positions.items()}
        live = {v: q for v, q in legs.items() if q}
        if not live:
            continue
        if len(live) < len(positions):
            conflicts.append({
                "symbol": sym, "kind": "one_legged", "legs": legs,
                "detail": "position on some venues but not all -- unhedged exposure",
            })
        elif sym not in set(state_symbols):
            conflicts.append({
                "symbol": sym, "kind": "untracked", "legs": legs,
                "detail": "hedged on all venues but absent from the state file",
            })

    any_live = any(p for p in positions.values())
    return BootDecision(
        flat=not any_live,
        positions=positions,
        conflicts=conflicts,
        reason=("no positions on any venue" if not any_live
                else f"{sum(len(p) for p in positions.values())} live leg(s) found"),
    )


class BootReconcileError(RuntimeError):
    """Raised when venue state could not be established at boot."""


# ============================================================================
# Orphan order sweep
# ============================================================================

async def sweep_orphan_orders(legs: Sequence[LegSpec]) -> int:
    """Cancel resting orders across all legs. Returns the count cancelled.

    Call at boot, before every open, after each leg verification, and in the
    `finally` of every cycle. Nothing in this bot family cancelled anything, so a
    GOOD_TILL_TIME remainder could fill hours after the bot had closed and moved
    on -- re-opening a naked leg with no state to describe it.
    """
    total = 0
    for leg in legs:
        try:
            n = await leg.cancel_open()
            total += int(n or 0)
            if n:
                logger.info("swept %d resting order(s) on %s", n, leg.name)
        except Exception as exc:                        # noqa: BLE001
            logger.warning("orphan sweep failed on %s: %s", leg.name, exc)
    return total
