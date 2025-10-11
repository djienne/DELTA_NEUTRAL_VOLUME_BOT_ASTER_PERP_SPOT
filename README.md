# Cross-Exchange Delta Neutral on Lighter and edgeX DEXes

**Automated 24/7 funding rate capture bot** for EdgeX and Lighter cryptocurrency perpetual futures exchanges.

This system continuously monitors multiple markets, executes delta-neutral positions to capture funding rate differences, and automatically rotates them to maximize profit while maintaining market-neutral exposure and farming volume at low risk.

Referral link to support this work and get fee rebates: https://pro.edgex.exchange/referral/FREQTRADE

## 🎯 Core Features

- 🤖 **Fully Automated 24/7 Trading**: The `lighter_edgex_hedge.py` runs continuously, requiring no manual intervention.
- 📈 **Intelligent Market Selection**: Analyzes a user-defined list of markets and always opens a position in the one with the highest net funding APR.
- 🔍 **Multi-Tier Market Filtering**: Automatically filters out unsuitable markets based on:
  - **Volume**: Minimum $250M 24h trading volume (configurable)
  - **Spread**: Maximum 0.15% cross-exchange price spread (configurable)
  - **APR**: Minimum 5% net funding APR (configurable)
- 🔄 **Automatic Position Rotation**: Opens a delta-neutral position, holds it for a configurable duration (e.g., 8 hours) to collect funding, then closes and rotates to the next best opportunity.
- 🛡️ **Stop-Loss Protection**: Automatically closes positions if a leg's loss exceeds a defined percentage of the notional value.
- 💥 **Crash Recovery & State Persistence**: Saves bot state, including cycle history and PnL. Can recover from restarts and reconcile existing positions.
- 🖥️ **Real-time Monitoring**: A clean terminal dashboard shows the current position, PnL, available capital, and top funding opportunities with spread and volume data.
- 🚨 **Emergency Close Tool**: Standalone script to immediately close all positions on both exchanges, bypassing normal workflows for critical situations.
- 🏗️ **Modular Architecture**: Clean separation between automation bot (`lighter_edgex_hedge.py`), exchange helpers (`lighter_client.py`, `edgex_client.py`), and emergency tools (`emergency_close.py`).

## ⚠️ Important: Manual Fund Rebalancing

This bot **cannot** automatically rebalance funds between your Lighter and EdgeX accounts. Due to the nature of hedging, one account will accumulate profits while the other incurs losses.

You must **manually rebalance** your capital between the two exchanges periodically. This should be done **when the bot is stopped** and involves withdrawing funds from one exchange and depositing to the other, a process that requires manual blockchain transactions.

## 🚀 Quick Start (Automated Bot)

### 1. Installation

```bash
# Navigate to the project directory
cd /path/to/CROSS_EXCHANGE_DELTA_NEUTRAL

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure API Credentials

Copy the example environment file and add your API keys.

```bash
cp .env.example .env
# Edit .env with your actual credentials
```

### 3. Configure the Rotation Bot

Edit `bot_config.json` to define your strategy.

```json
{
  "symbols_to_monitor": ["BTC", "ETH", "SOL", "PAXG", "HYPE", "XPL"],
  "quote": "USD",
  "leverage": 3,
  "notional_per_position": 320.0,
  "hold_duration_hours": 8.0,
  "min_net_apr_threshold": 5.0,
  "min_volume_usd": 250000000,
  "max_spread_pct": 0.15,
  "stop_loss_percent": 25.0
}
```
- `symbols_to_monitor`: More symbols provide more opportunities.
- `notional_per_position`: Max position size. The bot uses the lesser of this value or your available capital.
- `leverage`: Recommended: 3-5x.
- `min_volume_usd`: Minimum combined 24h volume in USD (default: $250M). Filters out low-liquidity pairs.
- `max_spread_pct`: Maximum cross-exchange mid price spread (default: 0.15%). Filters out pairs with excessive price discrepancy.
- `stop_loss_percent`: Safety threshold. Recommended: 25% for 3x leverage.

### 4. Run the Bot

```bash
# Start the bot directly
python lighter_edgex_hedge.py

# Or run with Docker for 24/7 operation (recommended)
docker-compose up -d lighter_edgex_hedge

# View live logs
docker-compose logs -f lighter_edgex_hedge
```
The bot will start, reconcile any existing state, and begin its analysis-trade-rotate cycle.

### 5. Monitor the Bot

<img src="rotation_bot.png" alt="Rotation Bot Terminal Output" width="800">

The dashboard displays the current cycle, PnL, capital, top funding opportunities, and time until the next rotation.

### 6. Emergency Close (Optional Safety Tool)

If you ever need to immediately exit all positions:

**Linux/macOS:**
```bash
python emergency_close.py --dry-run    # Check positions
python emergency_close.py               # Close all positions
```

**Windows (MUST use Docker):**
```bash
docker-compose run emergency_close --dry-run    # Check positions
docker-compose run emergency_close               # Close all positions
```

**Note:** The Lighter SDK only works on Linux/macOS. Windows users must use Docker for all trading operations.

Use this tool for:
- Emergency exits during extreme volatility
- Quick recovery from bot errors
- Liquidation risk mitigation
- When normal close commands fail

---

## 📁 Code Structure

The system consists of three main Python modules:

- **`lighter_edgex_hedge.py`** - Automated rotation bot
  - 24/7 automated funding rate capture bot
  - Imports and reuses functions from exchange client modules
  - State machine with persistent state in `logs/bot_state.json`

- **`lighter_client.py`** - Lighter exchange helper functions
  - Reusable functions for Lighter operations (balance, positions, orders, closing)
  - Used by `lighter_edgex_hedge.py` and `emergency_close.py`
  - WebSocket-based balance and price fetching

- **`edgex_client.py`** - EdgeX exchange helper functions
  - Reusable functions for EdgeX operations (balance, positions, orders, closing)
  - Used by `lighter_edgex_hedge.py` and `emergency_close.py`

- **`emergency_close.py`** - Emergency position closer
  - Independent tool to close ALL positions immediately
  - Uses `lighter_client.py` functions for Lighter operations
  - Works even if other scripts are stuck or failing

**Configuration Files:**
- `.env` - API credentials for both exchanges
- `bot_config.json` - Configuration for automated bot

**Examples Directory:**
- `examples/liquidation_monitor.py` - Optional margin monitoring service
- `examples/edgex_trading_bot.py` - EdgeX market maker bot
- `examples/market_maker_v2.py` - Advanced Lighter market maker
- Other data collection and analysis utilities

---

## 🔧 Advanced Usage & Details

<details>
<summary><b>⚙️ Full Configuration Details</b></summary>

### `bot_config.json`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `symbols_to_monitor` | array | `["BTC", "ETH", ...]` | List of symbols to analyze for funding opportunities |
| `quote` | string | `"USD"` | Quote currency for all markets |
| `leverage` | number | `3` | Leverage to use on both exchanges for all positions |
| `notional_per_position` | number | `320.0` | Maximum position size in USD (bot adjusts to actual available capital) |
| `hold_duration_hours` | number | `8.0` | How long to hold each position before closing (hours) |
| `wait_between_cycles_minutes` | number | `5.0` | Cooldown period between closing one position and opening the next (minutes) |
| `check_interval_seconds` | number | `300` | How often to check position health while holding (seconds, default: 5 minutes) |
| `min_net_apr_threshold` | number | `5.0` | Minimum net APR required to open a position (%) |
| `min_volume_usd` | number | `250000000` | Minimum combined 24h trading volume in USD (default: $250M) to filter low-liquidity pairs |
| `max_spread_pct` | number | `0.15` | Maximum allowed cross-exchange spread (%) between mid prices. Filters out pairs with excessive price discrepancy |
| `stop_loss_percent` | number | `25.0` | Stop-loss threshold as % of position notional (triggers on either leg) |
| `enable_stop_loss` | boolean | `true` | Enable automatic stop-loss protection |

### `.env` Environment Variables

- **EdgeX**: `EDGEX_BASE_URL`, `EDGEX_WS_URL`, `EDGEX_ACCOUNT_ID`, `EDGEX_STARK_PRIVATE_KEY`
- **Lighter**: `LIGHTER_BASE_URL`, `LIGHTER_WS_URL`, `API_KEY_PRIVATE_KEY`, `ACCOUNT_INDEX`, `API_KEY_INDEX`

**Note:** Margin mode is hardcoded to "cross" for delta-neutral hedging.

</details>

<details>
<summary><b>🐳 Docker Details</b></summary>

The `docker-compose.yml` is the easiest way to run the bot 24/7.

**Primary Service:**
```bash
# Start the automated bot in the background
docker-compose up -d lighter_edgex_hedge

# View live logs
docker-compose logs -f lighter_edgex_hedge

# Stop the bot
docker-compose stop lighter_edgex_hedge
```

The `liquidation_monitor` service is included but commented out in `docker-compose.yml`. Uncomment it to use it via `docker-compose run liquidation_monitor`.

</details>

<details>
<summary><b>🎓 How It Works (Technical Summary)</b></summary>

### Strategy
- **Funding Rate Capture**: The bot shorts the exchange with a higher funding rate and longs the one with a lower rate, profiting from the difference while remaining price-neutral.
- **Market Neutral**: Long and short positions cancel out price exposure - you profit from funding rates regardless of price movement.

### Position Sizing
- Automatically calculates the largest possible identical position size that respects the tick size rules of both exchanges
- Uses the coarser tick size (larger of the two exchanges) and floors the value to ensure both exchanges round identically
- Prevents unhedged exposure from rounding mismatches

### Order Execution
- Uses **aggressive limit orders** that cross the spread to ensure immediate execution
- Buy orders: `best_ask + (cross_ticks × tick_size)`
- Sell orders: `best_bid - (cross_ticks × tick_size)`
- Default `cross_ticks`: 100 for near-instant fills (configurable via `--cross-ticks`)
- Orders placed concurrently using `asyncio.gather()` to minimize timing risk between exchanges

### Exchange-Specific Details

**EdgeX:**
- Contract format: Symbol + Quote (e.g., "PAXGUSD")
- Position closing: Detects size and sends offsetting aggressive limit order
- Capital tracking: Uses `totalEquity` from account API (includes position value)

**Lighter:**
- Contract format: Symbol only (e.g., "PAXG")
- Position closing: Dual reduce-only orders (buy + sell), only offsetting side executes
- Capital tracking: WebSocket `user_stats` channel via `lighter_client.py`
- Helper functions in `lighter_client.py` for reusable Lighter operations

</details>

<details>
<summary><b>🛡️ Optional Liquidation Monitor</b></summary>

An optional, standalone service (`examples/liquidation_monitor.py`) can run alongside the main bot to provide an extra layer of safety.

- Monitors margin ratios on both exchanges every N seconds
- Automatically closes positions if the margin ratio exceeds a safety threshold (default: 80%)
- Detects and flags unhedged (one-sided) positions
- Colored console output (green/yellow/red) based on position health
- Logs to `logs/liquidation_monitor.log`

**Run via Python:**
```bash
python examples/liquidation_monitor.py --interval 60 --margin-threshold 80.0
```

**Run via Docker:**
```bash
# First, uncomment the 'liquidation_monitor' service in docker-compose.yml
docker-compose up -d liquidation_monitor
```

**Note:** This is a complementary safety tool - the main bot (`lighter_edgex_hedge.py`) already includes built-in stop-loss protection.

</details>

## ⚠️ Risk Management

- ⚠️ **Start small.** Test the system with a small amount of capital ($50-100) that you are willing to lose.
- ⚠️ **Monitor actively.** Especially during the first few trading cycles.
- ⚠️ **Leverage is risky.** It amplifies both gains and losses.
- ⚠️ **Network failures can happen.** The bot is designed to detect if one leg of a trade fails, but you should be prepared to intervene manually.
- ⚠️ **Maintain a margin buffer.** Keep extra capital in your accounts (>20%) to avoid liquidation during normal price fluctuations.

---

## 🆕 Recent Improvements

**Cross-Exchange Spread Filtering (January 2025)**
- **Automatic spread monitoring**: Bot now calculates the mid price spread between EdgeX and Lighter for each symbol
- **Configurable threshold**: Default maximum spread of 0.15% prevents trading pairs with excessive price discrepancy
- **Real-time spread display**: Funding rate tables show current spread percentage for all symbols
  - Available symbols display spread in the new "Spread" column
  - Excluded symbols show "✗ EXCLUDED: Spread too wide: X.XXX% > 0.15%" status
- **Why it matters**: Large spreads indicate pricing inefficiencies that could lead to poor execution or slippage
  - Prevents entering positions where exchanges have significantly different valuations
  - Reduces risk of immediate losses from price convergence
  - Ensures better fill prices on both legs of the delta-neutral position
- **Customizable via config**: Set `max_spread_pct` in `bot_config.json` (e.g., 0.20 for 0.20% max spread)
- **Three-tier filtering system**: Bot now filters opportunities by:
  1. Volume threshold (min $250M default)
  2. Spread threshold (max 0.15% default)
  3. Net APR threshold (min 5% default)

**Rate Limit Handling & API Optimization (January 2025)**
- **Global concurrency limiting**: Global semaphore limits max 2 concurrent Lighter API calls system-wide
  - Prevents overwhelming Lighter's API with too many simultaneous requests
  - All Lighter API calls (funding, volume, spread) serialized through this bottleneck
  - Combined with staggered delays ensures smooth, rate-limit-free operation
- **Intelligent retry logic**: Automatic exponential backoff with jitter when hitting API rate limits (HTTP 429)
  - Retries up to 3 times for funding rate fetches, 2 times for volume data
  - Initial delay: 1-2 seconds, increases exponentially with random jitter
  - Prevents rate limit cascades and improves reliability
- **Staggered API requests**: 1.0-second delay between symbol fetches (spread 12 symbols over ~12 seconds)
  - Significantly reduces rate limit risk compared to concurrent requests
  - Applied to both position selection and monitoring phases
  - EdgeX calls remain concurrent (no rate limits), only Lighter calls are throttled
- **Smart startup optimization**: Skips initial funding scan when bot restarts in HOLDING state
  - Saves 24-36 API calls on restart while holding a position
  - Only scans on startup when in IDLE/WAITING states (when needed)
- **Enhanced error visibility**: Volume fetch failures now logged at WARNING level for easier debugging
- **Data validation**: Bot prevents opening positions when volume data is unavailable (N/A)
  - Shows "✗ EXCLUDED: Volume N/A" status for symbols with missing volume data
  - Ensures all trades have verified liquidity before execution

**Volume Filtering (January 2025)**
- **Automatic liquidity filtering**: Bot now checks 24h trading volume from both exchanges before selecting positions
- **Configurable threshold**: Default minimum of $250M combined volume (EdgeX + Lighter) to avoid illiquid markets
- **Real-time volume display**: Funding rate tables show current 24h volume for all symbols
  - Displayed during startup (if not HOLDING), position selection (ANALYZING), and monitoring (HOLDING)
  - Human-readable format: `$2.4B`, `$495M`, `$150M`, etc.
- **Smart filtering**: Volume check enabled during position selection, with retry logic for failed fetches
- **Customizable via config**: Set `min_volume_usd` in `bot_config.json` to your preferred threshold
- Prevents positions in low-liquidity pairs that could have wide spreads or execution issues

**Critical Bug Fixes (January 2025)**
- **Fixed EdgeX position closing bug**: `account_id` must be converted to `int` for EdgeX SDK
  - Updated `emergency_close.py` to properly cast `account_id` to integer
  - Updated `edgex_client.py` to ensure `contract_id` is passed as string in `CreateOrderParams`
  - All EdgeXClient instantiations now correctly use `int(env["EDGEX_ACCOUNT_ID"])`
  - Emergency close tool now works reliably for closing EdgeX positions
- **Verified position closing consistency**: All three systems (`emergency_close.py`, `lighter_edgex_hedge.py`, `lighter_client.py`) use identical logic for Lighter position closing
  - Consistent side determination (Long→sell, Short→buy)
  - Consistent reference pricing (bid for sell, ask for buy)
  - All use reduce-only orders via `lighter_client.lighter_close_position()`

**DateTime Handling (January 2025)**
- All datetime operations now use timezone-aware UTC objects for consistency
- Proper ISO timestamp formatting with helper functions (`to_iso_z()`, `from_iso_z()`)
- Eliminated all deprecation warnings from `datetime.utcnow()`
- Gracefully handles malformed timestamps from older state files
- Compatible with Python 3.7+ (uses `timezone.utc` instead of `datetime.UTC`)

**Enhanced Funding Rate Display (January 2025)**
- Funding rate comparison table now shown at three key moments:
  1. **Startup** - Initial scan of all symbols showing market landscape
  2. **Before opening position** - Full comparison before selecting best opportunity
  3. **During holding** - Real-time updates with current position highlighted
- Comprehensive table with Symbol, EdgeX APR, Lighter APR, Net APR, **24h Volume**, and Long exchange
- Volume displayed in user-friendly format: `$2.4B`, `$495M`, `$150M`, etc.
- Color-coded markers: ★ BEST for highest APR, ◀ CURRENT for active position
- Top 10 opportunities displayed for better market visibility

**Modular Architecture (2025)**
- Extracted Lighter exchange operations into `lighter_client.py` for code reuse
- `emergency_close.py` now uses `lighter_client.py` functions for cleaner, more maintainable code
- Better separation of concerns: exchange helpers vs CLI tools vs automation bot

**Enhanced Position Closing**
- Emergency close tool now directly uses exchange-specific functions
- Faster execution with fewer dependencies
- Works independently even if other components fail

**Precision & Reliability**
- All rounding uses Python's `Decimal` type to eliminate floating-point errors
- Position size consistency ensures identical sizes on both exchanges
- Aggressive limit orders (cross-ticks=100 default) for near-instant fills

**Configuration Simplification**
- `.env.example` template provided for easy setup
- Clear documentation of all environment variables
- Support for both legacy and new variable naming conventions

