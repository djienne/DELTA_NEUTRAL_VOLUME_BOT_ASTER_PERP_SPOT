# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a **cross-exchange delta neutral hedging system** for cryptocurrency perpetual futures. It opens simultaneous long and short positions across two exchanges (EdgeX and Lighter) to capture funding rate arbitrage while maintaining market-neutral exposure.

## Core Architecture

The system consists of four main Python modules:
- **`lighter_edgex_hedge.py`**: Automated 24/7 rotation bot (production bot, imports functions from other modules)
- **`examples/hedge_cli.py`**: Manual trading CLI tool with all core exchange functions
- **`lighter_client.py`**: Lighter exchange helper functions (balance, positions, orders, closing)
- **`edgex_client.py`**: EdgeX exchange helper functions (balance, positions, orders, closing)
- **`emergency_close.py`**: Emergency position closer (uses lighter_client and edgex_client functions)

### Dependency Flow
```
lighter_edgex_hedge.py (production bot)
    ├── imports edgex_client.py (for EdgeX operations)
    ├── imports lighter_client.py (for Lighter operations)
    └── uses .env and bot_config.json

examples/hedge_cli.py (manual CLI)
    ├── imports edgex_client.py
    ├── imports lighter_client.py
    └── uses .env and hedge_config.json

emergency_close.py (emergency tool)
    ├── imports edgex_client.py
    ├── imports lighter_client.py
    └── uses .env only (independent of config files)
```

### Main Production Bot: `lighter_edgex_hedge.py`

**Primary 24/7 production bot** that fully automates the funding arbitrage cycle.

```bash
# Run the bot (uses bot_config.json)
python lighter_edgex_hedge.py

# Run with custom config
python lighter_edgex_hedge.py --config custom_config.json --state-file custom_state.json

# Docker service (recommended for 24/7 operation)
docker-compose up -d lighter_edgex_hedge
docker-compose logs -f lighter_edgex_hedge
```

**State Machine Architecture:**
1. **IDLE** → Waiting to start analysis
2. **ANALYZING** → Fetching funding rates and volumes, selecting best opportunity
3. **OPENING** → Executing delta-neutral position entry
4. **HOLDING** → Monitoring position health, collecting funding
5. **CLOSING** → Exiting both positions
6. **WAITING** → Cooldown period before next cycle
7. **ERROR** → Manual intervention required

**Volume-Based Position Selection:**
- Bot fetches 24h trading volume from both exchanges concurrently with funding rates
- Filters out symbols below `min_volume_usd` threshold (default: $250M combined)
- Funding rate comparison table displays volume in human-readable format ($2.4B, $495M, etc.)
- Volume check now enabled in monitoring (HOLDING) state for real-time opportunity assessment
- Skip startup scan when already HOLDING to conserve API quota (saves 24-36 calls)

**Persistent State (`logs/bot_state.json`):**
- `current_cycle`: Cycle counter (persists across restarts)
- `current_position`: Active position details (symbol, sizing, entry prices, PnL)
- `capital_status`: Real-time balance tracking on both exchanges
- `completed_cycles`: Historical cycle records
- `cumulative_stats`: Aggregate performance metrics

**Key Features:**
- Automatic recovery on restart (verifies actual positions match expected state)
- Stop-loss protection (auto-calculated as `(100/leverage) * 0.7`)
- Real-time PnL tracking for both exchanges
- Capital monitoring (EdgeX uses `totalEquity`, Lighter uses WebSocket)
- Graceful shutdown on CTRL+C

### Manual CLI Tool: `examples/hedge_cli.py`

The primary interface for manual trading and testing. Commands:

```bash
# Analysis Commands
python examples/hedge_cli.py capacity                      # Check available capital
python examples/hedge_cli.py funding                       # Show funding rates (auto-updates config)
python examples/hedge_cli.py funding_all                   # Compare multiple markets
python examples/hedge_cli.py check_leverage                # Show leverage info for multiple markets
python examples/hedge_cli.py status                        # Check current position status

# Trading Commands
python examples/hedge_cli.py open                          # Open position using config notional
python examples/hedge_cli.py open --size-base 0.05         # Open position (base units)
python examples/hedge_cli.py open --size-quote 100         # Open position (quote units)
python examples/hedge_cli.py close                         # Close both positions
python examples/hedge_cli.py close --cross-ticks 5         # Close with aggressive fills

# Testing Commands
python examples/hedge_cli.py test_leverage                 # Test leverage setup (no trading)
python examples/hedge_cli.py test --notional 20            # Test open+close cycle ($20)
python examples/hedge_cli.py test_auto --notional 20       # Test with auto-close after 5s

# Note: All commands use hedge_config.json by default
# Use --config BEFORE the command if using a different file:
python examples/hedge_cli.py --config my_config.json open --size-quote 100
```

**Cross-ticks parameter** (`--cross-ticks N`): Controls how aggressively orders cross the spread. Higher values = more aggressive fills but worse pricing. Default is 100 ticks for near-instant execution.

### Configuration System

**bot_config.json** (for automated bot):
- `symbols_to_monitor`: List of symbols to analyze for funding opportunities
- `leverage`: Leverage to apply on both exchanges (recommend 3-5x)
- `notional_per_position`: Maximum position size in USD (bot adjusts to actual capital)
- `hold_duration_hours`: How long to hold each position before closing
- `min_net_apr_threshold`: Minimum net APR required to open a position (%)
- `min_volume_usd`: Minimum combined 24h trading volume in USD (default: $250M) - filters out low-liquidity pairs
- `max_spread_pct`: Maximum cross-exchange mid price spread (default: 0.15%) - filters out pairs with excessive price discrepancy
- `enable_stop_loss`: Enable automatic stop-loss (auto-calculated from leverage)

**hedge_config.json** (for manual CLI):
- `symbol`: Base asset (e.g., "PAXG")
- `quote`: Quote currency (default "USD")
- `long_exchange`: Which exchange takes the long position ("edgex" or "lighter")
- `short_exchange`: Which exchange takes the short position ("edgex" or "lighter")
- `leverage`: Leverage to apply on both exchanges
- `notional`: Default notional size in quote currency for `open` command

**.env file** contains all exchange credentials (use `.env.example` as a template):

**EdgeX credentials:**
- `EDGEX_BASE_URL` (default: https://pro.edgex.exchange)
- `EDGEX_WS_URL` (default: wss://quote.edgex.exchange)
- `EDGEX_ACCOUNT_ID` (**CRITICAL: Must be integer, not string**)
- `EDGEX_STARK_PRIVATE_KEY`

**Lighter credentials:**
- `LIGHTER_BASE_URL` or `BASE_URL` (default: https://mainnet.zklighter.elliot.ai)
- `LIGHTER_WS_URL` or `WEBSOCKET_URL` (default: wss://mainnet.zklighter.elliot.ai/stream)
- `API_KEY_PRIVATE_KEY` or `LIGHTER_PRIVATE_KEY`
- `ACCOUNT_INDEX` or `LIGHTER_ACCOUNT_INDEX` (default: 0)
- `API_KEY_INDEX` or `LIGHTER_API_KEY_INDEX` (default: 0)

**Note:** Margin mode is hardcoded to "cross" for delta-neutral hedging.

### Exchange Integration

**EdgeX (edgex-python-sdk)**:
- Contract identification by symbol+quote (e.g., "PAXGUSD")
- Leverage setting via internal authenticated endpoint
- Position closing via offsetting aggressive limit orders
- Capital retrieval from `get_account_asset()` endpoint
- Funding rates from quote API and historical endpoint
- Volume data from `quote.get_24_hour_quote()` API (`value` field for USD volume)
- **Helper module:** `edgex_client.py` contains reusable functions
- **CRITICAL:** `account_id` must be passed as `int`, not string (SDK uses bitwise operations)
- **CRITICAL:** `contract_id` must be passed as `str` in `CreateOrderParams`

**Lighter (lighter-python SDK)**:
- Market identification by symbol
- Leverage setting via `update_leverage()` with margin mode
- Position closing via dual reduce-only orders (buy + sell)
- Capital retrieval via WebSocket `user_stats/{account_index}` channel
- Funding rates from candlestick API
- Volume data from `OrderApi.exchange_stats()` API (`daily_quote_token_volume`)
- **Helper module:** `lighter_client.py` contains reusable functions:
  - `get_lighter_balance()`: Fetch balance via WebSocket
  - `get_lighter_market_details()`: Get market_id and tick sizes
  - `get_lighter_best_bid_ask()`: Fetch prices via WebSocket
  - `get_lighter_open_size()`: Get position size for a market
  - `get_lighter_position_details()`: Full position info with PnL
  - `get_all_lighter_positions()`: Fetch all non-zero positions
  - `lighter_close_position()`: Close position with reduce-only order
  - `lighter_place_aggressive_order()`: Place market-crossing limit order
  - `cross_price()`: Calculate aggressive price crossing the spread

### Order Execution Strategy

Both exchanges use **aggressive limit orders** that cross the spread:
- Buy orders: priced at `best_ask + (cross_ticks * tick_size)`
- Sell orders: priced at `best_bid - (cross_ticks * tick_size)`
- Default `cross_ticks`: 100 (for very fast, near-instant execution)
- This ensures immediate fills while avoiding market order unpredictability

**Position opening**: Places both legs concurrently using `asyncio.gather()`

**Position closing**:
- Lighter: Sends dual reduce-only orders (only the offsetting side executes)
- EdgeX: Detects current position size and sends offsetting order

### Capital Management

The `capacity` command calculates maximum delta-neutral position size:
1. Fetches available USD on both exchanges
2. Applies safety margin (1%) and fee buffer (0.1%)
3. Calculates per-venue capacity: `available_usd * (1 - buffers) * leverage / mid_price`
4. Max size = minimum of long and short venue capacities
5. Rounds conservatively using both exchanges' tick sizes

### Utility Scripts

**`check_spread.py`** - Cross-exchange spread checker:
```bash
# Check spread for current position (reads from bot_state.json)
python check_spread.py
```
- Fetches real-time mid prices from both exchanges for the active position
- Calculates individual exchange spreads (bid-ask) and cross-exchange spread
- Shows price changes since position entry
- Useful for monitoring price convergence/divergence

**`check_all_spreads.py`** - Multi-symbol spread analysis:
```bash
# Check spreads for all symbols in bot_config.json
python check_all_spreads.py
```
- Fetches mid prices for all monitored symbols
- Displays sorted table with spread percentages
- Highlights which exchange has higher price
- Shows average, max, and min spreads across all symbols
- Staggered API requests (1 second delay for Lighter) to avoid rate limits

**`check_volume.py`** - Volume comparison utility:
```bash
# Check 24h trading volume across both exchanges
python check_volume.py

# Show debug information about volume data fields
python check_volume.py --debug
```
- Displays 24h trading volume for all symbols in `bot_config.json`
- Shows EdgeX volume, Lighter volume, and combined total
- Volume data used by bot for liquidity filtering
- EdgeX volume from `quote.get_24_hour_quote()` API (`value` field)
- Lighter volume from `OrderApi.exchange_stats()` API (`daily_quote_token_volume` field)

**`test_funding_comparison.py`** - Funding rate analysis:
```bash
# Compare funding rates using symbols from bot_config.json
python test_funding_comparison.py

# Compare specific symbols
python test_funding_comparison.py --symbols BTC ETH SOL

# Custom quote currency
python test_funding_comparison.py --symbols BTC ETH --quote USD
```
- Fetches and compares funding rates from both exchanges
- Calculates net APR spread (profit opportunity)
- Displays optimal strategy (which exchange to long/short)
- Sorts results by best opportunities
- Shows funding payment frequencies (EdgeX: 4h/6x daily, Lighter: hourly/24x daily)

### Emergency Close: `emergency_close.py`

**Critical safety tool** for immediately closing all open positions on both exchanges, regardless of configuration files or normal workflow.

**⚠️ WINDOWS USERS:** This script ONLY works on Linux/macOS due to Lighter SDK limitations. On Windows, you **MUST** use Docker:

```bash
# LINUX/MACOS - Direct execution
python emergency_close.py --dry-run          # Check positions
python emergency_close.py                     # Close (requires 'CLOSE')
python emergency_close.py --cross-ticks 200  # Ultra-aggressive

# WINDOWS - Must use Docker
docker-compose run emergency_close --dry-run              # Check positions
docker-compose run emergency_close                        # Interactive mode
echo CLOSE | docker-compose run emergency_close          # Auto-confirm
docker-compose run emergency_close --cross-ticks 200     # Ultra-aggressive
```

**Key features:**
- Independent of config files - closes ANY open positions
- Requires typing 'CLOSE' to confirm (safety measure)
- Dry-run mode to inspect positions first
- Configurable aggressiveness via `--cross-ticks` (default: 100)
- Uses `lighter_client.py` and `edgex_client.py` functions for position closing
- Works even if other scripts are stuck

## Environment Variables

The system supports flexible environment variable naming for Lighter exchange:

**Primary names** (recommended):
- `LIGHTER_BASE_URL` (default: https://mainnet.zklighter.elliot.ai)
- `LIGHTER_WS_URL` (default: wss://mainnet.zklighter.elliot.ai/stream)
- `LIGHTER_PRIVATE_KEY`
- `LIGHTER_ACCOUNT_INDEX` (default: 0)
- `LIGHTER_API_KEY_INDEX` (default: 0)

**Legacy fallback names** (still supported):
- `BASE_URL` → `LIGHTER_BASE_URL`
- `WEBSOCKET_URL` → `LIGHTER_WS_URL`
- `API_KEY_PRIVATE_KEY` → `LIGHTER_PRIVATE_KEY`
- `ACCOUNT_INDEX` → `LIGHTER_ACCOUNT_INDEX`
- `API_KEY_INDEX` → `LIGHTER_API_KEY_INDEX`

**Auto-rotation bot specific**:
- `BOT_STATE_FILE` - Override default state file location (default: `logs/bot_state.json`)
- `PYTHONUNBUFFERED=1` - For real-time Docker logs

## Dependencies

```bash
pip install -r requirements.txt
```

Key packages:
- `edgex-python-sdk`: EdgeX REST/WebSocket SDK (imports as `edgex_sdk`)
- `lighter-python`: Lighter SDK (installed from GitHub)
- `python-dotenv`: Environment variable management
- `websockets`: WebSocket client for Lighter capital queries

**Note:** The EdgeX package installs as `edgex-python-sdk` but imports as `edgex_sdk`:
```python
from edgex_sdk import Client as EdgeXClient
```

## Development & Testing Workflow

### Local Development
```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure credentials
cp .env.example .env
# Edit .env with your API keys

# 3. Test funding rates (no trading)
python examples/hedge_cli.py funding_all

# 4. Check available capital
python examples/hedge_cli.py capacity

# 5. Run small test trade
python examples/hedge_cli.py test --notional 20

# 6. Monitor existing positions
python examples/hedge_cli.py status
```

### Testing Commands (Safe)
These commands are safe for testing and development:

**Analysis only (no execution):**
- `funding` / `funding_all` - Check funding rates
- `capacity` - Calculate max position size
- `check_leverage` - Verify leverage settings
- `status` - Check current positions

**Test with minimal capital:**
- `test --notional 20` - Full cycle with $20 position
- `test_auto --notional 20` - Auto-closing test
- `test_leverage` - Verify leverage without trading

**Emergency commands:**
- Linux/macOS: `python emergency_close.py --dry-run` - Check all positions
- Linux/macOS: `python emergency_close.py` - Emergency close all positions
- **Windows: `docker-compose run emergency_close --dry-run`** - MUST use Docker
- **Windows: `docker-compose run emergency_close`** - MUST use Docker

### Production Deployment
```bash
# 1. Edit production config
nano bot_config.json

# 2. Test in dry-run mode first (if available)

# 3. Run small position for first real cycle
# (temporarily set notional_per_position to 50-100)

# 4. Deploy with Docker for 24/7 operation
docker-compose up -d lighter_edgex_hedge

# 5. Monitor logs
docker-compose logs -f lighter_edgex_hedge

# 6. Scale up notional after confirming success
```

## Docker Support

All CLI commands are available as Docker services (most are commented out in docker-compose.yml):

```bash
# Analysis commands
docker-compose run capacity
docker-compose run funding
docker-compose run funding_all
docker-compose run status
docker-compose run check_leverage

# Trading commands
docker-compose run open
docker-compose run close

# Testing commands
docker-compose run test_leverage
docker-compose run test
docker-compose run test_auto
```

You can override command arguments:
```bash
docker-compose run open --size-quote 100
docker-compose run test --notional 50
```

## Key Design Patterns

### Code Architecture Principles

1. **Modular architecture with function reuse**:
   - `edgex_client.py` contains all EdgeX-specific helper functions
   - `lighter_client.py` contains all Lighter-specific helper functions
   - `examples/hedge_cli.py` imports and uses both client modules for manual trading
   - `lighter_edgex_hedge.py` imports and uses both client modules for automation
   - `emergency_close.py` imports and uses both client modules for emergency operations
   - **Dual-client pattern**: Each operation initializes both exchange clients, performs actions, then closes connections
   - Clean separation: CLI logic vs bot state machine logic vs exchange helpers

2. **Tick-aware rounding**: All sizes/prices rounded to exchange-specific tick sizes using `_round_to_tick()`, `_ceil_to_tick()`, `_floor_to_tick()` with Decimal arithmetic to avoid floating-point precision errors
   - Critical for delta-neutral hedging: sizes must be IDENTICAL
   - Solution: Use coarser tick size (max of both exchanges) and floor to ensure both round the same
   - Example: EdgeX tick=0.001, Lighter tick=0.01 → use 0.01 and floor

3. **Fallback pricing**: If best bid/ask unavailable, uses last price with synthetic spread (EdgeX) or falls back to available side
   - Prevents order placement failures due to missing orderbook data
   - Synthetic spread: `last_price ± (last_price * 0.0001)` for bid/ask

4. **Concurrent execution**: Position opening/closing uses `asyncio.gather()` for simultaneous exchange actions
   - Minimizes timing risk between exchanges
   - Both legs execute at nearly the same time for true delta-neutral entry/exit

5. **Environment flexibility**: Supports multiple naming conventions for environment variables (LIGHTER_* vs original names)
   - Backwards compatibility with legacy configs
   - Code checks `LIGHTER_*` first, falls back to original names

6. **State persistence pattern** (lighter_edgex_hedge.py):
   - Every state change saves to JSON immediately
   - On restart: verify actual positions match expected state
   - Crash recovery: can resume HOLDING state if hedge still valid

7. **Volume filtering pattern** (lighter_edgex_hedge.py):
   - `fetch_symbol_volume(symbol, quote, env)`: Concurrent fetch from both exchanges
   - `fetch_symbol_funding()`: Now fetches both funding rates AND volume data
   - Strategic `check_volume` parameter: enabled for position selection and monitoring, disabled only for startup when HOLDING
   - Volume data cached and passed to `display_funding_table()` for user visibility
   - Retry logic with exponential backoff for both EdgeX and Lighter volume fetches
   - WARNING-level logging for volume fetch failures to aid debugging

8. **Rate limit handling pattern** (lighter_edgex_hedge.py):
   - **Global semaphore (`LIGHTER_API_SEMAPHORE`)**: Limits max 2 concurrent Lighter API calls system-wide
     - All Lighter API calls must acquire semaphore before executing
     - Prevents overwhelming API even with many concurrent symbol fetches
     - Applies to funding rates, volume data, and spread calculations
   - `RateLimitError` exception class for specific rate limit error detection
   - `is_rate_limit_error()`: Detects HTTP 429, "Too Many Requests", code 23000, or "rate limit" in error messages
   - `retry_with_backoff()`: Generic async retry function with exponential backoff and jitter
     - Configurable max_retries, initial_delay, backoff_factor, max_delay
     - Random jitter prevents thundering herd problem
     - Re-raises RateLimitError after all retries exhausted
   - Staggered delays (1.0s) between symbol fetches to prevent concurrent API bombardment
   - EdgeX calls remain concurrent (no rate limits), only Lighter calls are throttled
   - Smart startup optimization: skip funding scan when bot is already HOLDING

### Critical Implementation Details

**EdgeX specifics:**
- Contract name = symbol + quote (no separator): "PAXGUSD"
- Leverage set via internal REST endpoint (not public SDK method)
- Capital query: Use `totalEquity` from `collateralAssetModelList` (includes position value)
- PnL calculation: Manual computation using `(current_price × size) - open_value`
- Position close: Detect size, send offsetting aggressive limit order
- **CRITICAL BUG FIX (Jan 2025):** `account_id` must be `int(env["EDGEX_ACCOUNT_ID"])`, NOT string
  - EdgeX SDK uses bitwise operations that fail with strings
  - All EdgeXClient instantiations MUST use `int()` conversion
- **CRITICAL:** In `CreateOrderParams`, `contract_id` must be `str(contract_id)` for the SDK

**Lighter specifics:**
- Market identification: Symbol only (e.g., "PAXG")
- Leverage set via `update_leverage(leverage, margin_mode='cross')`
- Capital query: WebSocket channel `user_stats/{account_index}` (via `get_lighter_balance()`)
- PnL: Provided directly by API (no manual calculation)
- Position close: Dual reduce-only orders (buy + sell), only offsetting side executes (via `lighter_close_position()`)
- Position attributes: Use `pos.position` (unsigned size) with `pos.sign` (1=long, -1=short), NOT `pos.size`
- Entry price attribute: `pos.avg_entry_price` (NOT `pos.entry_price`)

**Rounding functions (using Decimal for precision):**
```python
from decimal import Decimal, ROUND_DOWN, ROUND_UP, ROUND_HALF_UP

def _round_to_tick(value: float, tick_size: float) -> float:
    """Round to nearest tick (banker's rounding)"""

def _floor_to_tick(value: float, tick_size: float) -> float:
    """Round down to tick boundary"""

def _ceil_to_tick(value: float, tick_size: float) -> float:
    """Round up to tick boundary"""
```

**Aggressive limit order pricing:**
```python
# Buy: cross the spread upward
buy_price = best_ask + (cross_ticks * tick_size)

# Sell: cross the spread downward
sell_price = best_bid - (cross_ticks * tick_size)
```
This ensures immediate fills without true market orders' unpredictability.

## Contract Naming

- **EdgeX**: Symbol + Quote concatenated (e.g., PAXG + USD = "PAXGUSD")
- **Lighter**: Symbol only (e.g., "PAXG")

## Important Notes

- The system uses aggressive limit orders (not true market orders) for better control
- **Leverage is set on both exchanges before opening positions** with verification via `configure_leverage()`
- **Position sizing ensures IDENTICAL sizes** on both exchanges by using the coarser tick size and flooring
- Position size verification happens after opening to confirm both legs executed correctly
- WebSocket connections are ephemeral - created per-query for capital checks, not persistent
- Funding rate arbitrage is the primary profit mechanism for this delta-neutral strategy
- **⚠️ WINDOWS LIMITATION:** The Lighter SDK only supports Linux/macOS. On Windows, ALL commands must be run via Docker

## Troubleshooting

### Common Issues

**"Position size mismatch" error:**
- Caused by different tick sizes between exchanges
- Solution: Bot automatically uses coarser tick size and floors the value
- Check logs for detailed size calculations

**"Leverage setup failed" warning:**
- EdgeX leverage may not be verifiable until position exists
- Warning is informational, proceed with caution
- Run `test_leverage` command to verify setup

**WebSocket connection errors (Lighter capital query):**
- Ephemeral connections created per-query, not persistent
- Retry logic built-in for transient failures
- Check network connectivity and `LIGHTER_WS_URL` setting

**Unhedged position detected:**
- One leg failed to execute or was manually closed
- Bot enters ERROR state requiring manual intervention
- **Quick fix:** Run `python emergency_close.py` to close all positions
- Alternative: Check both exchanges manually and close remaining position
- Delete or fix `logs/bot_state.json` before restart

**Auto-rotation bot stuck in ANALYZING:**
- No symbols meet `min_net_apr_threshold`
- Lower threshold or wait for better market conditions
- Add more symbols to `symbols_to_monitor`

**TypeError: unsupported operand type(s) for +: 'int' and 'str':**
- This means `account_id` is being passed as a string to EdgeXClient
- **FIX:** Always use `int(env["EDGEX_ACCOUNT_ID"])` when creating EdgeXClient
- This was a critical bug fixed in January 2025

**API rate limit errors (HTTP 429 / "Too Many Requests"):**
- Bot now uses **global semaphore** limiting max 2 concurrent Lighter API calls
- Automatically retries with exponential backoff (up to 3 times for funding, 2 times for volume)
- Staggered delays (1.0s between symbols) prevent concurrent rate limit hits
- EdgeX calls remain concurrent (no rate limits), only Lighter calls are throttled
- If seeing persistent rate limits (should be extremely rare now):
  - Check WARNING-level logs for specific failure patterns
  - Verify API quotas haven't been exceeded on exchange side
  - Consider reducing number of `symbols_to_monitor` temporarily
- Volume showing N/A: Usually transient rate limit issue, will retry automatically
- Bot skips startup scan when HOLDING to conserve API quota

### Logging & Debugging

**Console output:**
- `examples/hedge_cli.py`: WARNING level and above (clean output)
- `lighter_edgex_hedge.py`: INFO level, color-coded status

**Log files:**
- `hedge_cli.log`: DEBUG level, all hedge_cli operations
- `logs/lighter_edgex_hedge.log`: DEBUG level, full bot activity
- `logs/liquidation_monitor.log`: DEBUG level, position monitoring (if running)

**State inspection:**
```bash
# View current bot state
cat logs/bot_state.json | python -m json.tool

# Monitor bot logs live
tail -f logs/lighter_edgex_hedge.log

# Check hedge CLI debug output
tail -f hedge_cli.log
```

## Recent Improvements (2025)

### Cross-Exchange Spread Filtering (January 2025)
- **New spread monitoring feature**: Bot calculates mid price spread between exchanges for each symbol
- **Configurable filtering**: `max_spread_pct` in bot_config.json (default: 0.15%)
- **Three-tier filtering system**:
  1. Volume threshold (min $250M combined volume)
  2. Spread threshold (max 0.15% price discrepancy)
  3. Net APR threshold (min 5% funding rate difference)
- **Implementation details**:
  - `fetch_symbol_spread()`: Calculates spread percentage between exchange mid prices
  - Spread data included in funding rate tables with new "Spread" column
  - Excluded symbols show "✗ EXCLUDED: Spread too wide: X.XXX% > 0.15%"
- **Why it matters**: Prevents trading on pairs with pricing inefficiencies that could lead to poor execution

### Rate Limit Handling & API Optimization (January 2025)
- **Global concurrency limiting**: `LIGHTER_API_SEMAPHORE` limits max 2 concurrent Lighter API calls system-wide
  - All Lighter API calls (funding, volume, spread) must acquire semaphore before executing
  - Prevents overwhelming Lighter's API with too many simultaneous requests
  - Combined with staggered delays ensures smooth, rate-limit-free operation
  - EdgeX calls remain concurrent (no rate limits), only Lighter calls are throttled
- **Intelligent retry logic with exponential backoff**: Automatic recovery from API rate limits (HTTP 429)
  - `retry_with_backoff()` function with configurable max retries, initial delay, backoff factor, and jitter
  - Funding rate fetches: 3 retries, 2s initial delay
  - Volume data fetches: 2 retries, 1s initial delay
  - Exponential backoff with random jitter prevents thundering herd problem
  - Specific `RateLimitError` exception class for clear error handling
  - Errors logged at WARNING level for visibility
- **Staggered API requests**: 1.0-second delay between symbol fetches
  - Spreads 12 symbols over ~12 seconds instead of concurrent bombardment
  - Implemented in both `open_best_position()` (ANALYZING) and monitoring (HOLDING)
  - Combined with global semaphore ensures maximum 2 Lighter API calls at any time
- **Smart startup optimization**: Skips initial funding scan when bot is already HOLDING
  - Saves 24-36 API calls on restart (2-3 calls per symbol × 12 symbols)
  - Only performs startup scan when in IDLE/WAITING states
  - Displays: "Already holding position, skipping initial funding scan to conserve API quota"
- **Enhanced error visibility**: Volume fetch failures logged at WARNING level
  - EdgeX contract not found warnings
  - Rate limit errors after retries
  - Generic fetch failures
- **Data validation**: Prevents trading when volume data unavailable
  - `fetch_symbol_funding()` validates `total_volume is not None` when `check_volume=True`
  - Returns `available: False` with reason "Volume data unavailable" for N/A volumes
  - Display shows "✗ EXCLUDED: Volume N/A" for symbols without volume confirmation
- **Monitoring table volume display**: Shows 24h volume even in HOLDING state
  - Changed from `check_volume=False` to `check_volume=True` for monitoring display
  - Real-time volume data with retry logic and staggered delays
  - Helps user assess if better opportunities exist with sufficient liquidity

### Critical Bug Fixes (January 2025)
- **Fixed EdgeX position closing bug**: `account_id` must be converted to `int` for EdgeX SDK
  - Updated `emergency_close.py` to properly cast `account_id` to integer
  - Updated `edgex_client.py` to ensure `contract_id` is passed as string in `CreateOrderParams`
  - All EdgeXClient instantiations now correctly use `int(env["EDGEX_ACCOUNT_ID"])`
- **Verified position closing consistency**: All systems use identical Lighter closing logic

### Position Size Consistency
- Uses coarser tick size (larger of the two exchanges) to ensure identical position sizes
- Verifies both exchanges will round to the same value before execution
- Displays tick sizes and scaled units in output for transparency
- Prevents unhedged exposure from rounding mismatches

### Leverage Management
- `configure_leverage()` function sets leverage on both exchanges before opening
- EdgeX leverage verification via positions API (when position exists)
- `test_leverage` command to verify leverage setup without trading
- Clear warnings if leverage setup fails, but allows proceeding with caution

### DateTime Handling (2025)
- All datetime operations use timezone-aware UTC objects (`timezone.utc`)
- Proper ISO timestamp formatting and parsing with helper functions:
  - `utc_now()`: Returns timezone-aware UTC datetime
  - `utc_now_iso()`: Returns ISO 8601 timestamp with Z suffix
  - `to_iso_z()`: Converts datetime to ISO string with Z suffix
  - `from_iso_z()`: Parses ISO timestamps, handling malformed formats gracefully
- Eliminated all timezone-naive datetime operations
- Compatible with Python 3.7+ (uses `timezone.utc` instead of `datetime.UTC`)

### Volume Filtering (January 2025)
- **Automatic liquidity filtering**: Bot checks 24h trading volume before selecting positions
- **Configurable threshold**: Default minimum of $250M combined volume (EdgeX + Lighter)
- **Real-time volume display**: Funding rate tables show current 24h volume for all symbols
  - Displayed during startup (if not HOLDING), position selection (ANALYZING), and monitoring (HOLDING)
  - Human-readable format: `$2.4B`, `$495M`, `$150M`, etc.
- **Smart filtering**: Volume check enabled during position selection, with retry logic for failed fetches
- **Customizable via config**: Set `min_volume_usd` in `bot_config.json` to your preferred threshold
- Prevents positions in low-liquidity pairs that could have wide spreads or execution issues
- Volume data retrieved from:
  - EdgeX: `quote.get_24_hour_quote()` API (`value` field for USD volume)
  - Lighter: `OrderApi.exchange_stats()` API (`daily_quote_token_volume` field)

### Execution Improvements
- Default `cross_ticks` set to 100 for near-instant order fills
- Minimizes timing risk between exchanges (critical for delta-neutral hedging)
- Prioritizes execution speed over price improvement
- User can still override with `--cross-ticks N` if needed
