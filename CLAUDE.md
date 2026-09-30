# CLAUDE.md

Guidance for Claude Code (claude.ai/code) in this repository.

## What this is

A live-money delta-neutral funding carry bot on Aster DEX: long spot + short perp on the same coin, collecting the funding the short receives. It holds one position for weeks and rotates only when another pair pays clearly more. (It used to churn positions for airdrop volume; the file names are historical.)

## Files

- `aster_api_manager.py`: the API layer. v3 endpoints use an Ethereum signature (`_signed_request_v3`); v1 endpoints use HMAC (`_make_spot_request(signed=True)`). All HTTP goes through `_request` (30s timeout). It also holds market data (bulk funding, volumes, book tickers), the account snapshot `get_comprehensive_portfolio_data`, and `open_dn` / `close_dn`.
- `two_leg.py`: the shared two-leg safety primitive. **Byte-identical across the bot family: do not edit it here.** Per-bot behaviour goes in the `LegSpec` callables built in `open_dn`.
- `strategy_logic.py`: pure functions only (no API calls, no state): hedge detection, `funding_apr`, `safe_stop_loss_pct`, and the economic exit rule `close_reason`.
- `volume_farming_strategy.py`: `run()` loops over `_cycle()`. It also contains `_scan` (the funding table), `_check_position`, open and close, the pre-open reconcile `_adopt_or_halt`, and state.
- `emergency_exit.py`: writes `halt.json`, then calls `close_dn`.
- `check_funding_rates.py`: prints the bot's own `_scan`.
- `check_spot_perp_spreads.py` and `get_volume_24h.py`: diagnostics.
- `tests/test_offline.py`: offline checks against a fake exchange.

## Safety model (do not weaken)

1. **Legs are sequential and verified.** `open_dn` runs `two_leg.execute_two_leg` with the spot buy as pilot and the perp sell as hedge.
   - The hedge is sized from the spot actually received and rounded to the **nearest** perp step.
   - Each leg's `amount_tick` is its own lot step.
   - two_leg reads a fill of ≤ 2 ticks as "nothing moved", so the pilot must be ≥ 3 perp steps (guarded).
2. **Order outcomes are verified, not assumed.** HTTP ≥ 500, a dropped connection or a truncated body raise `ConnectionError`, which two_leg treats as UNKNOWN and verifies. Other exceptions count as rejections.
3. **Close perp first.** `close_dn` sells spot only after the perp reads flat. It sells `min(free, tracked spot_qty)`, so the user's own coins are never sold.
4. **Never open in a cycle that closed or tried to close.** A failed close keeps the position and retries next cycle.
5. **Never act on missing data.** An account-fetch failure means hold. A scan failure means only the safety checks run. Filters fail closed.
6. **The exchange is truth.** The monitor reads the legs every cycle. A tracked position is cleared only when both legs read flat (spot below $5 is dust). The bot's spot leg is `min(balance, tracked spot_qty)`.
7. **Untracked exposure blocks opening.** With nothing tracked, a single clean hedge is adopted; any other perp position makes `write_halt` fire.
8. **`halt.json`** (two_leg) blocks opening, never monitoring or closing. It is written when an unwind fails, when an open failed after the spot leg filled, on untracked exposure, and by `emergency_exit.py`. A human deletes it.

## Strategy rules

- **Selection.** Candidate filters: on both markets, two-sided spot and perp books (empty sides report `0.00000`, and several spot books are one-sided), interval published, predicted rate ≥ 0, 24h spot+perp volume ≥ $250M, |perp−spot mid basis| ≤ 0.15%.
  - Rank by `funding_avg_days` average APR: the last `days×24/interval − 1` settled rates plus the predicted rate.
  - Entry requires APR ≥ `min_funding_apr`.
- **Funding intervals vary per symbol (1h/4h/8h) and change over time.** Always use `fundingIntervalHours` from bulk `/fapi/v1/fundingInfo`, fetched every scan and never cached. Never assume 8h or `× 3`. Annualize with `DeltaNeutralLogic.funding_apr(rate, interval_hours)`.
- **Exit and rotation (`close_reason`):**
  - Close at any age if the held 7-day APR ≤ −`min_funding_apr`.
  - After `min_hold_days`, rotate only for a **different** symbol whose 7-day APR is ≥ `rotation_min_apr_gain` points higher.
  - The gain is absolute, not a ratio, because the switching cost is absolute: ~0.4% of notional, so 2 × 0.4% × 365/30 ≈ 10 points.
- **Stop-loss.** Measured as perp `unrealizedProfit / (|positionAmt| × entryPrice)`. The threshold is `safe_stop_loss_pct(position_leverage)`: 70% of the way to liquidation, rounded toward zero, giving 1x −69%, 2x −34%, 3x −22%. Always use the position's leverage, never the config's.
- **Leverage (1–3).** The split is perp 1/(L+1), spot L/(L+1), applied by `rebalance_usdt_by_leverage` before each open. A config change applies to the next position only. `open_dn` aborts if `set_leverage` fails.
- **PnL.**
  - Realized = USDT in both wallets after close − `entry_cash` (taken before the pre-open rebalance). It is logged as funding (income history since open) plus residual (fees, basis, slippage).
  - The portfolio line compares to a baseline captured on first run.
  - Both assume no deposits or withdrawals.

## Config (`config_volume_farming_strategy.json`)

`capital_fraction` 0.96, `min_funding_apr` 10, `funding_avg_days` 7, `min_hold_days` 7, `rotation_min_apr_gain` 10, `loop_interval_seconds` 300, `leverage` 1.

`load_config` reads known keys from any section. Unknown or legacy keys are ignored. Invalid JSON raises instead of silently falling back to defaults.

## Running

```bash
docker-compose up --build -d && docker-compose logs -f   # recommended
pip install -r requirements.txt && python volume_farming_strategy.py
python -m pytest tests -q                                # offline, no keys needed
```

Credentials live in `aster.env`, next to the code. It is git-ignored; the tracked template is `aster.env.example`. It needs all 5 variables: `API_USER`, `API_SIGNER`, `API_PRIVATE_KEY`, `APIV1_PUBLIC_KEY`, `APIV1_PRIVATE_KEY`.

- Build the API client with `AsterApiManager.from_env()`, the only place credentials are read. Never put keys in the JSON config.
- docker-compose injects the same file via `env_file: aster.env`. Variables already in the environment take precedence.

## Conventions

- **UTC everywhere.** Use `datetime.utcnow()` for naive UTC timestamps. When converting to epoch, use `.replace(tzinfo=timezone.utc)`. Label UTC in logs.
- **Quantities.** Always use `fmt_qty(qty, step, rounding)` (Decimal on the lot-step grid), never float truncation.
- **Colors (colorama).** Green = success/profit, red = error/loss, yellow = warning, cyan = info, magenta = values. Always end with `Style.RESET_ALL`.
- **Code style.** Keep the code small: delete before adding, and put pure logic in `strategy_logic.py`. Any non-trivial rule gets a check in `tests/test_offline.py`.
- **Commits.** Imperative titles under 72 characters. The body covers risk-control, API and config changes.

## Debugging

- **Bot not opening.** Check for `halt.json`. Otherwise run `python check_funding_rates.py`; most pairs fail the $250M volume filter, and the 7-day APR must reach `min_funding_apr`.
- **"untracked perp exposure" halt.** A perp position exists that isn't a clean hedge. Inspect it on the exchange and flatten it or fix it, then delete `halt.json`.
- **State lost.** A clean live hedge is re-adopted. Its realized PnL is logged as unknown and its age restarts.
