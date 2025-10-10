# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an automated delta-neutral trading bot for Aster DEX that captures funding rate payments while maintaining market-neutral exposure. The bot continuously rotates positions across spot and perpetual markets to maximize funding rate collection and trading volume.

**Key Features:**
- Delta-neutral positions (long spot + short perpetuals)
- Configurable leverage (1x-3x) with automatic capital allocation
- Moving average funding rate filtering for stability
- Automatic position discovery and state recovery
- Comprehensive health checks and risk management
- Long-term portfolio PnL tracking with automatic asset valuation
- Colorful terminal output with intuitive color-coded messages

## Architecture

The codebase follows a clean separation of concerns across four core modules:

### Core Modules

1. **`aster_api_manager.py`** - API Layer
   - Handles ALL external API communications (spot, perpetual, transfers)
   - Implements both v1 (HMAC-SHA256) and v3 (Ethereum signature) authentication
   - Provides formatted order parameters respecting exchange precision filters
   - Key methods: `get_perp_leverage()`, `set_perp_leverage()`, `rebalance_usdt_by_leverage()`, `prepare_and_execute_dn_position()`

2. **`strategy_logic.py`** - Pure Business Logic
   - Contains stateless, pure functions for calculations and analysis
   - NO API calls, NO state mutations, fully testable
   - Core logic: position sizing, funding rate analysis, risk assessment, portfolio health checks
   - All methods are static in `DeltaNeutralLogic` class

3. **`volume_farming_strategy.py`** - Strategy Orchestration
   - Main entry point and event loop
   - State management via `volume_farming_state.json`
   - Decision-making logic (when to open/close positions)
   - Implements the continuous monitoring and rotation cycle
   - **Critical**: Position leverage is tracked separately from config leverage

4. **`utils.py`** - Shared Utilities
   - Small, reusable helper functions
   - Currently contains `truncate()` for precision handling

### Data Flow

```
volume_farming_strategy.py (Orchestrator)
    ↓ calls
aster_api_manager.py (API Layer) ←→ Aster DEX Exchange
    ↓ returns data
strategy_logic.py (Pure Logic) - processes data
    ↓ returns analysis
volume_farming_strategy.py - makes decisions
```

### State Management

**State File: `volume_farming_state.json`**
- Persists current position, leverage, funding received, entry fees, timestamps, **entry price**
- **Important**: `position_leverage` tracks the leverage used when position was opened (separate from config)
- **Important**: `entry_price` is saved for accurate spot PnL calculations
- **Important**: `initial_portfolio_value_usdt` and `initial_portfolio_timestamp` store baseline for long-term PnL tracking
- Bot automatically reconciles state with exchange on startup
- Delete this file to force rediscovery of existing positions **and reset portfolio PnL baseline**

**Config File: `config_volume_farming_strategy.json`**
- User-facing configuration for all strategy parameters
- Nested structure: `capital_management`, `funding_rate_strategy`, `position_management`, `leverage_settings`
- Changes to leverage only apply to NEW positions, never mid-position
- **Note**: Old `risk_management` section renamed to `leverage_settings` (backward compatible)

## Leverage System

**Critical Implementation Detail**: The bot supports configurable leverage (1x-3x) with sophisticated position tracking:

### Leverage Split Formula
```
perp_allocation = 1 / (leverage + 1)
spot_allocation = leverage / (leverage + 1)

Examples:
- 1x: 50% perp / 50% spot
- 2x: 33.3% perp / 66.7% spot
- 3x: 25% perp / 75% spot
```

### Position Leverage Preservation
- Each position tracks its own `position_leverage` (separate from config `leverage`)
- When position opens: `position_leverage = config.leverage`
- Config changes do NOT affect open positions
- On startup: Bot detects leverage from exchange via `get_perp_leverage()`
- Leverage transitions happen only between positions (during rebalancing)

### Key Methods
- `AsterApiManager.rebalance_usdt_by_leverage(leverage)` - Rebalances USDT between wallets
- `AsterApiManager.set_perp_leverage(symbol, leverage)` - Sets leverage on exchange
- `VolumeFarmingStrategy._reconcile_position_state()` - Detects and syncs leverage from exchange
- `VolumeFarmingStrategy._calculate_safe_stoploss(leverage)` - Auto-calculates liquidation-safe stop-loss
- `VolumeFarmingStrategy._capture_initial_portfolio()` - Captures baseline portfolio value (called once)
- `VolumeFarmingStrategy._get_current_portfolio_value()` - Calculates total portfolio value including all assets
- `VolumeFarmingStrategy._calculate_total_portfolio_pnl()` - Calculates PnL vs initial baseline

## Running the Bot

### Docker (Recommended)
```bash
# Start bot
docker-compose up --build

# Run in background
docker-compose up --build -d

# View logs
docker-compose logs -f

# Stop bot
docker-compose down
```

### Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Run bot
python volume_farming_strategy.py
```

### Utility Scripts
```bash
# Check funding rates and volume filtering (NEW)
python check_funding_rates.py

# Calculate safe stop-loss for current leverage
python calculate_safe_stoploss.py

# Get 24h volume for specific pair
python get_volume_24h.py
```

### Testing Tools
```bash
# Test setting leverage on BTC
python test_leverage.py

# Test rebalancing for different leverages
python test_leverage_rebalance.py

# Test leverage detection on existing positions
python test_leverage_detection.py
```

## Volume Filtering

**CRITICAL**: The bot implements **$250M 24h volume threshold** for pair filtering.

- **Location**: `volume_farming_strategy.py:937` (hardcoded)
- **Purpose**: Ensures sufficient liquidity and stable funding rates
- **Implementation**: `_find_best_funding_opportunity()` filters pairs by combined 24h volume
- **Check utility**: Use `check_funding_rates.py` to see which pairs pass/fail this filter
- **Rationale**: Prevents trading low-liquidity pairs with execution risks despite attractive funding rates

## Configuration

All configuration in `config_volume_farming_strategy.json`:

**Capital Management:**
- `capital_fraction`: Fraction of total USDT to deploy (default: 0.50)

**Funding Rate Strategy:**
- `min_funding_apr`: Minimum APR threshold (default: 7.0%)
- `use_funding_ma`: Use moving average for stability (default: true)
- `funding_ma_periods`: MA periods (default: 10)

**Position Management:**
- `fee_coverage_multiplier`: Close when funding ≥ fees × multiplier (default: 1.8)
- `max_position_age_hours`: Max hold time (default: 336 hours)
- `loop_interval_seconds`: Cycle interval (default: 900 seconds)

**Leverage Settings:**
- `leverage`: Perpetual leverage 1-3 (default: 3)
- **Note**: `emergency_stop_loss_pct` is **automatically calculated** based on leverage (NOT in config)
- **Note**: Section renamed from `risk_management` to `leverage_settings` (backward compatible)

## Development Guidelines

### When Modifying Portfolio PnL Tracking

**CRITICAL**: Portfolio value calculation must include ALL assets, not just USDT.

1. **Portfolio value calculation**:
   - Spot side: Sum of (asset_quantity × current_price) for ALL spot holdings (USDT, BTC, ETH, etc.)
   - Perp side: Wallet balance (includes realized PnL) + Unrealized PnL
   - Total: Spot total value + Perp wallet + Perp unrealized PnL
2. **Baseline capture** (`_capture_initial_portfolio()`):
   - Called ONCE when bot starts fresh (no baseline in state)
   - Uses same calculation as current portfolio value
   - Stores in `initial_portfolio_value_usdt` and `initial_portfolio_timestamp`
3. **Current value calculation** (`_get_current_portfolio_value()`):
   - Fetches prices for all non-USDT spot assets from exchange
   - Loops through all spot balances and converts to USDT value
   - Must handle assets with zero balance gracefully
4. **PnL calculation** (`_calculate_total_portfolio_pnl()`):
   - PnL USD = Current Value - Initial Baseline
   - PnL % = (PnL USD / Initial Baseline) × 100
5. **Important notes**:
   - Assumes no external deposits/withdrawals during bot operation
   - To reset baseline: delete state file
   - Display in cycle header with color-coding (green/red based on profit/loss)

### When Modifying Position PnL Calculations

1. **Entry price is critical** - Always save `entry_price` when opening positions
2. **Fallback to perp entry price** - If `entry_price` missing from state, use `perp_pos.get('entryPrice')`
3. **Spot PnL formula**: `spot_qty × (current_price - entry_price)`
4. **Combined PnL includes everything**: Spot + Perp + Funding - Entry Fees - Exit Fees
5. **Stop-loss uses Perp PnL only** - Not combined PnL (perp is more volatile)

### When Modifying Leverage Logic

1. **Never change position leverage mid-position** - Only during `_open_position()` and after `_close_current_position()`
2. **Always detect leverage on reconciliation** - See `_reconcile_position_state()` around line 436
3. **Save position_leverage to state** - After detection and after opening positions
4. **Log with `[LEVERAGE]` prefix** - For easy debugging in logs
5. **Print terminal warnings** - For leverage mismatches (see lines 134-140, 447-453)

### When Modifying Stop-Loss Logic

**CRITICAL**: Stop-loss is auto-calculated - do NOT add manual parameter to config.

1. **Calculation is automatic** - See `_calculate_safe_stoploss()` in `volume_farming_strategy.py:162-205`
2. **Formula accounts for**:
   - Exchange maintenance margin (0.5%)
   - Safety buffer (0.7%): fees + slippage + volatility
   - Delta-neutral capital allocation: `perp_fraction = L/(L+1)`
3. **Stop-loss values**:
   - 1x leverage: -50%
   - 2x leverage: -33%
   - 3x leverage: -24%
4. **Testing**: Use `calculate_safe_stoploss.py` to validate calculations
5. **Modification**: Only change `maintenance_margin` or `safety_buffer` parameters in the function

### When Modifying Funding Rate Display

1. **Calculate next funding time dynamically** - Based on current UTC time, not historical data
2. **Funding schedule**: Every 8 hours at 00:00, 08:00, 16:00 UTC
3. **Use `datetime.utcfromtimestamp()`** - Not `fromtimestamp()` to avoid timezone issues
4. **Format with UTC suffix** - `strftime('%Y-%m-%d %H:%M UTC')`
5. **Convert milliseconds to seconds** - Exchange timestamps are in ms: `timestamp / 1000`

### When Working with Timestamps

**Critical**: All timestamps in this codebase use UTC, not local time.

1. **Always use UTC functions**:
   - `datetime.utcnow()` - NOT `datetime.now()`
   - `datetime.utcfromtimestamp()` - NOT `datetime.fromtimestamp()`
2. **Always label UTC in logs** - Add " UTC" suffix to formatted timestamps
3. **Key UTC timestamps**:
   - `position_opened_at` - When position was opened
   - Cycle timestamps - Start of each strategy cycle
   - State file `last_updated` - When state was saved
   - Next funding time - Calculated future funding timestamp
4. **Time calculations** - Use `datetime.utcnow()` for all time elapsed calculations

### When Adding API Methods

1. **Add to `aster_api_manager.py`** - Keep API layer isolated
2. **Use appropriate auth method**:
   - v3 endpoints: `_signed_request_v3()` (Ethereum signature)
   - v1 endpoints: `_make_spot_request()` with `signed=True` (HMAC-SHA256)
3. **Format parameters** - Use `_get_formatted_order_params()` for precision
4. **Handle errors gracefully** - Return structured error dicts

### When Adding Strategy Logic

1. **Add to `strategy_logic.py`** as static method
2. **Keep it pure** - No API calls, no state mutations
3. **Return structured data** - Dicts or tuples with clear keys
4. **Add constants** - At top of file for easy tuning

### When Creating Utility Scripts

**Example**: `check_funding_rates.py` provides standalone funding rate analysis.

1. **API Manager initialization** - Use correct parameter names:
   - `apiv1_public` and `apiv1_private` (NOT `apiv1_public_key`/`apiv1_private_key`)
   - All 5 credentials required: `api_user`, `api_signer`, `api_private_key`, `apiv1_public`, `apiv1_private`
2. **Volume data source** - Fetch from `/fapi/v1/ticker/24hr` endpoint
   - Use `quoteVolume` field for USDT volume
   - Apply same $250M threshold as main bot
3. **Funding rate calculation** - Use same formula as bot:
   - `funding_rate * 3 * 365 * 100` (3x daily, 365 days, as percentage)
4. **Color-coded output** - Follow bot's colorama scheme
5. **Async/await** - All API calls should be async with proper session management
6. **Error handling** - Use `return_exceptions=True` in `asyncio.gather()` for resilience

### When Working with Terminal Output Colors

**Color Scheme** (using `colorama` library):
- `Fore.GREEN` - Success messages, profits, positive PnL, confirmations
- `Fore.RED` - Errors, losses, negative PnL, stop-loss triggers
- `Fore.YELLOW` - Warnings, fees, important notices, leverage mismatches
- `Fore.CYAN` - Informational messages, cycle headers, general info
- `Fore.MAGENTA` - Important numeric values (leverage, amounts, symbols, cycle numbers)
- `Style.RESET_ALL` - Always reset colors after colored text

**Best Practices**:
1. **Dynamic coloring** - Use conditionals for PnL (green if ≥0, red if <0)
2. **Consistent usage** - Keep color meanings consistent throughout
3. **Always reset** - End colored strings with `Style.RESET_ALL`
4. **Progress bars** - Color based on completion (cyan → yellow → green)
5. **Borders** - Use colored `===` lines for section separation
6. **Symbols** - Use ✓ for success, ⚠️ for warnings (with color)

**Example**:
```python
pnl_color = Fore.GREEN if pnl >= 0 else Fore.RED
logger.info(f"PnL: {pnl_color}${pnl:.2f}{Style.RESET_ALL}")
```

### Code Quality

- All Python files include comprehensive docstrings
- Type hints used throughout for clarity
- Async/await pattern for all I/O operations
- Extensive logging with INFO, WARNING, ERROR, DEBUG levels
- State persistence prevents data loss on crashes

## Important Files

**Documentation:**
- `README.md` - User-facing guide
- `LEVERAGE_FEATURE.md` - Complete leverage documentation
- `LEVERAGE_IMPLEMENTATION_SUMMARY.md` - Technical implementation details
- `AUTOMATIC_STOPLOSS_IMPLEMENTATION.md` - Stop-loss calculation details
- `VOLUME_FARMING_GUIDE.md` - Strategy deep-dive

**Utility Scripts:**
- `check_funding_rates.py` - Displays funding rates and volume filtering analysis for all delta-neutral pairs
- `calculate_safe_stoploss.py` - Validates stop-loss calculations for all leverage levels
- `get_volume_24h.py` - Fetches 24h volume for specific pairs

**Logs:**
- `volume_farming.log` - All bot activity (rotated, 10MB max, 3 files)
- Look for `[LEVERAGE]` prefix for leverage-related operations

**Environment:**
- `.env` - API credentials (NEVER commit this file)
- `.env.example` - Template for credentials

## Critical Behaviors

### Position Discovery
On startup, if state file exists but position not found on exchange:
- Clears stale state
- Logs warning about external closure

On startup, if position found on exchange but not in state:
- Calls `_discover_existing_position()`
- Detects leverage from exchange via `get_perp_leverage()`
- Retrieves entry price from perp position's `entryPrice` field
- Reconstructs state from API data including funding history

### Leverage Mismatch Handling
When `position_leverage != config.leverage`:
1. Logs warning at INFO level
2. Prints terminal warning box with ⚠️ symbol
3. Preserves position leverage until closure
4. After closure, rebalances USDT for new config leverage
5. Next position opens with new leverage

### Portfolio PnL Tracking
The bot tracks long-term portfolio performance:

1. **Total Portfolio Value**: Calculated each cycle
   - Includes ALL spot asset holdings (USDT + BTC + ETH + etc.) at current market prices
   - Plus perp wallet balance (includes all realized PnL)
   - Plus perp unrealized PnL from open positions
2. **Baseline**: Captured once on first run (or after state file deletion)
   - Stored in `initial_portfolio_value_usdt` and `initial_portfolio_timestamp`
   - Persists across bot restarts
3. **Total PnL Display**: Shown in cycle header
   - Formula: `Current Portfolio Value - Initial Baseline`
   - Displayed as both $ and % with color-coding (green/red)
   - Example: `📊 Portfolio: $1,245.32 | PnL: +$45.32 (+3.78%) | Since: 2025-10-08 12:00 UTC`

### Position PnL Calculation
The bot tracks three types of position PnL:

1. **Perp Unrealized PnL**: Direct from exchange (`unrealizedProfit` field)
   - Used for emergency stop-loss trigger
2. **Spot Unrealized PnL**: Calculated as `spot_qty × (current_price - entry_price)`
   - Entry price retrieved from position state or perp's `entryPrice` field
3. **Combined DN PnL (net)**: The true strategy profit/loss for current position
   - Formula: `Spot PnL + Perp PnL + Funding Received - Entry Fees - Exit Fees`
   - This is what matters for evaluating current position performance

### Next Funding Time Calculation
- Funding occurs every 8 hours at 00:00, 08:00, 16:00 UTC
- Calculated dynamically based on current UTC time (not from historical data)
- Displayed in `YYYY-MM-DD HH:MM UTC` format in funding rate tables
- Same for all symbols since funding is synchronized across perpetuals

### Health Check Validation
- Leverage must be in valid range 1x-3x (not hardcoded to 1x)
- Imbalance threshold: Critical if >10%, warning if >5%
- Position value must be >$5 to avoid incomplete trades

### Emergency Conditions
- **Stop Loss**: Closes position if **Perp PnL** ≤ auto-calculated stop-loss (not combined PnL)
  - Stop-loss automatically calculated: 1x=-50%, 2x=-33%, 3x=-24%
  - Uses perp PnL (more volatile) not combined DN PnL
  - Includes 0.7% safety buffer from liquidation
- **Health Check Failures**: Skips cycle, logs warning, retries next cycle
- **API Errors**: Logged but bot continues (unless critical)

## Debugging

**Check Logs for Leverage Issues:**
```bash
grep "\[LEVERAGE\]" volume_farming.log
```

**Verify State:**
```bash
cat volume_farming_state.json | grep position_leverage
```

**Check Exchange Leverage:**
```python
python test_leverage_detection.py
```

**Validate Stop-Loss Calculation:**
```bash
python calculate_safe_stoploss.py
```

**Common Issues:**
- Leverage mismatch at startup → Normal if config changed, position will switch after close
- "Could not detect leverage" → Falls back to config leverage, verify manually
- State file corruption → Delete `volume_farming_state.json`, bot will rediscover
- Spot PnL showing $0.00 → Entry price missing from state, will auto-fix on next evaluation cycle
- Next funding time in the past → Check system UTC time, should calculate future time dynamically
- Stop-loss concerns → Run `calculate_safe_stoploss.py` to see calculations with safety buffer
- Portfolio PnL incorrect → Check if external deposits/withdrawals occurred; delete state file to reset baseline
- Portfolio value too low → Likely only counting USDT, not spot asset holdings; check `_get_current_portfolio_value()`
- Bot not trading certain pairs → Run `check_funding_rates.py` to verify they meet $250M volume requirement
- API parameter errors in utilities → Ensure using `apiv1_public`/`apiv1_private` (not `_key` suffix)

## Recent Improvements (2025-10)

### Funding Rate Analysis Utility (NEW)
- **New script**: `check_funding_rates.py` for standalone analysis
- **Displays**: Current APR for all delta-neutral pairs with color-coded output
- **Volume filtering**: Shows which pairs pass/fail $250M volume requirement
- **Two tables**: Eligible pairs (≥$250M) and filtered pairs (<$250M)
- **Summary stats**: Total pairs, eligible count, filtered count, best opportunity
- **Use case**: Pre-trading analysis and debugging why certain pairs aren't traded
- **Implementation notes**:
  - Uses correct API manager parameter names (`apiv1_public`/`apiv1_private`)
  - Fetches volume from `/fapi/v1/ticker/24hr` endpoint (`quoteVolume` field)
  - Applies same $250M threshold as main bot (`volume_farming_strategy.py:937`)
  - Async/await pattern with proper error handling

### Long-term Portfolio PnL Tracking
- **Automatic baseline capture**: Captures initial portfolio value on first run
- **Comprehensive asset valuation**: Includes ALL spot holdings (USDT + BTC + ETH + etc.) at current prices
- **Real-time calculation**: Fetches current prices for all assets each cycle
- **Persistent tracking**: Baseline stored in state file, survives restarts
- **Display format**: `📊 Portfolio: $X,XXX.XX | PnL: ±$XX.XX (±X.XX%) | Since: YYYY-MM-DD HH:MM UTC`
- **Color-coded**: Green for profits, red for losses
- **Key methods**:
  - `_capture_initial_portfolio()` - Captures baseline (called once)
  - `_get_current_portfolio_value()` - Calculates current total value
  - `_calculate_total_portfolio_pnl()` - Computes PnL vs baseline
- **Important**: Assumes no external deposits/withdrawals; delete state file to reset

### Enhanced Colorful Terminal Output (NEW)
- **Comprehensive color scheme**: Green (success/profit), Red (error/loss), Yellow (warning), Cyan (info), Magenta (values)
- **Dynamic coloring**: PnL colors change based on profit/loss status
- **Progress bars**: Color-coded based on completion percentage
- **Visual borders**: Colored `===` separators for section clarity
- **Symbols**: ✓ for success, ⚠️ for warnings, 📊 for portfolio stats
- **Consistent usage**: All terminal output follows same color conventions
- **Implementation**: Uses `colorama` library with `Fore` and `Style` classes

### Automatic Stop-Loss Calculation
- **Removed manual parameter**: `emergency_stop_loss_pct` no longer in config
- **Auto-calculated based on leverage**: Uses liquidation math with 0.7% safety buffer
- **Mathematically optimal**: Maximum safe stop-loss for each leverage level
- **Formula**: `[(1+1/L)/(1+m)-1-b] × L/(L+1)` where L=leverage, m=maintenance margin, b=buffer
- **Results**: 1x=-50%, 2x=-33%, 3x=-24%
- **Safety buffer includes**: Trading fees (0.1%) + slippage (0.2%) + volatility (0.4%)
- **Location**: `volume_farming_strategy.py:162-205`
- **Testing tool**: `calculate_safe_stoploss.py`

### Position PnL Calculation Enhancements
- **Added entry price tracking**: Now saved in position state for accurate spot PnL calculations
- **Automatic entry price recovery**: Falls back to perp's `entryPrice` if missing from state
- **Three-tier PnL display**: Shows Perp PnL, Spot PnL, and Combined DN PnL separately
- **Net strategy PnL**: Combined DN PnL now includes funding received and subtracts all fees

### Funding Time Display Fixes
- **Dynamic calculation**: Next funding time calculated from current UTC (not historical data)
- **Correct timezone**: Uses `utcfromtimestamp()` instead of `fromtimestamp()`
- **Human-readable format**: Displays as `YYYY-MM-DD HH:MM UTC` instead of millisecond timestamp
- **Synchronized across symbols**: All pairs show same next funding time (as expected)

### Health Check Improvements
- **Leverage validation**: Now accepts 1x-3x instead of hardcoded 1x check
- **Prevents false alarms**: No longer flags valid 2x/3x leverage as critical issues

### Logging Improvements
- **PnL breakdown**: Separate logs for perp, spot, and combined PnL
- **Funding visibility**: Combined PnL shows impact of funding payments and fees
- **UTC timestamps**: All timestamps use UTC consistently (position opened, cycles, funding times)
- **Clear time labels**: All displayed times include " UTC" suffix for clarity
- **Stop-loss logging**: Shows auto-calculated value on startup with safety buffer info

## API Authentication

The bot uses TWO authentication methods:

1. **v3 API (Ethereum Signature)**: For account info, orders, positions
   - Signs with keccak256 hash of JSON params
   - Requires: `API_USER`, `API_SIGNER`, `API_PRIVATE_KEY`

2. **v1 API (HMAC-SHA256)**: For leverage, income history, user trades
   - Signs with HMAC-SHA256 of query string
   - Requires: `APIV1_PUBLIC_KEY`, `APIV1_PRIVATE_KEY`

Both are required. Missing either will cause bot failures.
