# Complete Guide: Delta-Neutral Trading Bot on ASTER DEX

## 📚 Table of Contents

1. [General Introduction](#general-introduction)
2. [Fundamental Concepts](#fundamental-concepts)
3. [Detailed Technical Architecture](#detailed-technical-architecture)
4. [Trading Strategy Explained](#trading-strategy-explained)
5. [Leverage System and Capital Allocation](#leverage-system-and-capital-allocation)
6. [Risk Management](#risk-management)
7. [Profit/Loss Calculations and Tracking](#profitloss-calculations-and-tracking)
8. [Trading Pair Filtering](#trading-pair-filtering)
9. [Configuration and Deployment](#configuration-and-deployment)
10. [Utility Scripts](#utility-scripts)
11. [Monitoring and Debugging](#monitoring-and-debugging)
12. [Real-World Examples and Use Cases](#real-world-examples-and-use-cases)
13. [Frequently Asked Questions](#frequently-asked-questions)

---

## General Introduction

### What is This Project?

This project is an **automated delta-neutral trading bot** specifically designed for the ASTER DEX decentralized exchange. It's a sophisticated system that captures **funding rate payments** from perpetual contracts while maintaining **market-neutral exposure**.

### Main Objectives

1. **Generate stable profits** by collecting funding rates without taking directional risk
2. **Maximize trading volume** on ASTER DEX (useful for Stage 3 airdrop)
3. **Continuous rotation** of positions to optimize returns
4. **Full automation** operating 24/7 without human intervention

### Why is This Bot Unique?

- ✅ **Delta-neutral**: No exposure to market price movements
- ✅ **Multi-leverage**: Support for 1x to 3x with automatic transitions
- ✅ **Intelligent filtering**: 4 levels of filters to select only the best opportunities
- ✅ **Complete risk management**: Automatic stop-loss, health checks, state recovery
- ✅ **Advanced PnL tracking**: Real-time tracking of full portfolio and individual positions
- ✅ **Clean architecture**: Clear separation between business logic, API, and orchestration

---

## Fundamental Concepts

### What is Delta-Neutral Trading?

**Delta-neutral trading** is a strategy that aims to eliminate exposure to price movements (the "delta" in options Greeks terminology). In the context of this bot:

**Delta-Neutral Position = Long Spot Position + Short Perpetual Position**

#### Concrete Example

Let's say you want to capture the funding rate on BTC/USDT:

1. **You buy 0.1 BTC on the spot market** at 50,000 USDT
2. **You short 0.1 BTC on the perpetual market** at 50,000 USDT

**Result**:
- If the price rises to 55,000 USDT:
  - Your spot position gains: +5,000 USDT
  - Your perp position loses: -5,000 USDT
  - **Net profit from price movement: 0 USDT** ✓

- If the price drops to 45,000 USDT:
  - Your spot position loses: -5,000 USDT
  - Your perp position gains: +5,000 USDT
  - **Net profit from price movement: 0 USDT** ✓

**You are protected against price movements in both directions!**

### What is the Funding Rate?

**Funding rates** are periodic payments between long and short traders on perpetual contract markets.

#### Mechanism

- **Positive rate**: Longs pay shorts → You **receive** payments by being short
- **Negative rate**: Shorts pay longs → You **pay** by being short (to avoid!)
- **Frequency**: Every 8 hours (00:00, 08:00, 16:00 UTC on ASTER DEX)

#### Why Do Funding Rates Exist?

Funding rates serve to keep the perpetual contract price aligned with the spot price:

- **Bull market**: Many traders want to be long → High positive rate → Incentivizes shorts
- **Bear market**: Many traders want to be short → Negative rate → Incentivizes longs

#### Annualized Return (APR) Calculation

The bot calculates APR from the instantaneous funding rate:

```
APR (%) = Funding Rate × 3 (payments/day) × 365 (days) × 100
```

**Example**:
- Funding rate: 0.01% (0.0001)
- APR = 0.0001 × 3 × 365 × 100 = **10.95% per year**

On a 10,000 USDT position, this represents ~1,095 USDT annual profit just from collecting funding rates!

### Why is This Strategy Profitable?

**Profit Sources**:
1. **Positive funding rates**: Regular income every 8 hours
2. **Position rotation**: Capturing best opportunities by switching pairs
3. **Leverage effect**: Maximizes capital usage (up to 3x)

**Costs to Cover**:
1. **Entry fees**: ~0.1% on spot + ~0.05% on perp = 0.15% total
2. **Exit fees**: ~0.1% on spot + ~0.05% on perp = 0.15% total
3. **Total fees**: ~0.30% per complete cycle

**Breakeven Threshold**:
The bot waits until collected funding rates cover fees × multiplier (default: 1.8x) before closing a position, thus guaranteeing profitability for each cycle.

---

## Detailed Technical Architecture

### Architecture Overview

The bot follows a modular architecture with **strict separation of responsibilities**:

```
┌─────────────────────────────────────────────────────────────┐
│                 volume_farming_strategy.py                  │
│                    (Main Orchestrator)                      │
│  • Main strategy loop                                      │
│  • State management (volume_farming_state.json)           │
│  • Decision logic (when to open/close)                    │
│  • Monitoring and health checks                           │
└────────────┬──────────────────────────────┬────────────────┘
             │                              │
             ▼                              ▼
┌────────────────────────┐      ┌──────────────────────────┐
│ aster_api_manager.py   │      │   strategy_logic.py      │
│   (API Layer)          │      │   (Pure Logic)           │
│                        │      │                          │
│ • Auth v1 (HMAC-SHA256)│      │ • Stateless calculations │
│ • Auth v3 (ETH sig)    │      │ • Funding rate analysis  │
│ • Spot/perp orders     │      │ • Position sizing        │
│ • USDT transfers       │      │ • Health checks          │
│ • Leverage management  │      │ • PnL calculations       │
└────────────┬───────────┘      └──────────────────────────┘
             │
             ▼
┌────────────────────────┐
│     ASTER DEX API      │
│  • Spot Markets (v1)   │
│  • Perpetual (v3)      │
│  • Account Info        │
└────────────────────────┘
```

### Module 1: `aster_api_manager.py` - API Layer

#### Responsibilities

This module is the **only interface** with the ASTER DEX exchange. It handles:
- All HTTP requests to the API
- Two distinct authentication systems
- Order parameter formatting
- API error handling

#### Dual Authentication (v1 + v3)

ASTER DEX uses **two different authentication systems**:

##### **API v1 (HMAC-SHA256)** - For Spot and Some Perp Functions

```python
# Endpoints using v1:
- GET /fapi/v1/leverageBracket  # Get leverage
- POST /fapi/v1/leverage        # Set leverage
- GET /fapi/v1/income           # Funding rate history
- GET /fapi/v1/userTrades       # Trade history
```

**v1 Authentication Process**:
1. Create query string with timestamp: `symbol=BTCUSDT&timestamp=1696800000000`
2. Sign with HMAC-SHA256: `signature = hmac(query_string, APIV1_PRIVATE_KEY)`
3. Add signature to query string
4. Send with header: `X-MBX-APIKEY: APIV1_PUBLIC_KEY`

##### **API v3 (Ethereum Signature)** - For Orders and Positions

```python
# Endpoints using v3:
- POST /v3/order         # Place order
- GET /v3/account        # Account info
- GET /v3/openOrders     # Open orders
- GET /v3/positionRisk   # Perpetual positions
```

**v3 Authentication Process**:
1. Create JSON payload of parameters
2. Hash with keccak256: `message_hash = keccak256(json.dumps(params))`
3. Sign with Ethereum private key: `signature = eth_account.sign(message_hash)`
4. Send with headers:
   - `aster-user-address: API_USER` (your ETH wallet)
   - `aster-signer-address: API_SIGNER` (signer generated by ASTER)
   - `aster-signature: signature`

#### Key API Manager Methods

##### `get_perp_leverage(symbol: str) -> int`
Detects the current leverage on the exchange for a given symbol.

```python
# Returns: 1, 2, or 3 (or None if error)
current_leverage = await api_manager.get_perp_leverage("BTCUSDT")
```

##### `set_perp_leverage(symbol: str, leverage: int) -> bool`
Sets leverage on the exchange (1x, 2x, or 3x).

```python
success = await api_manager.set_perp_leverage("BTCUSDT", 3)
```

##### `rebalance_usdt_by_leverage(leverage: int) -> bool`
Redistributes USDT between spot and perp wallets according to leverage.

**Allocation Formula**:
```python
perp_allocation = 1 / (leverage + 1)
spot_allocation = leverage / (leverage + 1)

# Examples:
# 1x: 50% perp / 50% spot
# 2x: 33.3% perp / 66.7% spot
# 3x: 25% perp / 75% spot
```

##### `prepare_and_execute_dn_position(symbol, capital_usdt, leverage)`
Prepares and executes a complete delta-neutral position:

1. Calculates spot and perp quantities
2. Formats parameters with correct precision
3. Places spot order (market buy)
4. Places perp order (market short)
5. Verifies execution of both orders
6. Returns complete position details

### Module 2: `strategy_logic.py` - Pure Logic

#### Design Principle

This module contains **only pure functions**:
- ✅ No API calls
- ✅ No state mutations
- ✅ Inputs → Calculations → Outputs
- ✅ Easy to test

All methods are **static** in the `DeltaNeutralLogic` class.

#### Main Methods

##### `calculate_position_sizes(capital_usdt, spot_price, leverage)`
Calculates position sizes for both legs.

```python
# Inputs
capital_usdt = 1000  # Total capital to deploy
spot_price = 50000   # BTC price
leverage = 3         # 3x leverage

# Outputs
{
    'spot_qty': 0.015,        # BTC quantity to buy on spot
    'perp_qty': 0.015,        # BTC quantity to short on perp
    'spot_value': 750,        # Value in USDT (75% of capital)
    'perp_value': 250,        # Margin in USDT (25% of capital)
    'total_position_value': 750  # Notional value
}
```

##### `calculate_funding_rate_ma(income_history, periods=10)`
Calculates moving average of funding rates to smooth volatility.

```python
# Input: Funding rate history
income_history = [
    {'income': '0.50', 'time': 1696800000000},  # $0.50 received
    {'income': '0.45', 'time': 1696771200000},  # $0.45 received
    # ... 10 periods
]

# Output: Average APR
{
    'effective_apr': 12.5,           # Average APR over 10 periods
    'periods_analyzed': 10,          # Number of periods used
    'latest_funding_rate': 0.0001    # Latest rate
}
```

##### `assess_health(position_data, config)`
Evaluates position health and detects issues.

**Checks performed**:
1. **Valid leverage**: 1 ≤ leverage ≤ 3
2. **Imbalance**: |spot_qty - perp_qty| / spot_qty ≤ 10%
3. **Minimum value**: position_value > $5

```python
{
    'is_healthy': True,
    'critical_issues': [],         # Blocking problems
    'warnings': [],                # Warnings
    'metrics': {
        'imbalance_pct': 2.5,      # 2.5% imbalance
        'leverage': 3,
        'position_value': 1000
    }
}
```

### Module 3: `volume_farming_strategy.py` - Main Orchestrator

This is the **heart of the bot**. It orchestrates the entire system.

#### `VolumeFarmingStrategy` Class Structure

```python
class VolumeFarmingStrategy:
    def __init__(self, config_path, state_path):
        self.api_manager = AsterApiManager(...)
        self.config = load_config()
        self.state = load_state()
        self.check_iteration = 0  # Check counter
```

#### Main Loop: `run()`

The `run()` method is an infinite loop that executes the strategy cycle:

```python
async def run(self):
    while True:
        self.check_iteration += 1

        # 1. Health check
        is_healthy = await self._perform_health_check()
        if not is_healthy:
            await asyncio.sleep(loop_interval)
            continue

        # 2. If position open: evaluate
        if self.state.get('position_open'):
            await self._evaluate_existing_position()

        # 3. If no position: find opportunity
        else:
            await self._find_and_open_position()

        # 4. Save state
        self._save_state()

        # 5. Wait for next cycle
        await asyncio.sleep(loop_interval)  # Default: 900s (15min)
```

#### State Management: `volume_farming_state.json`

The state file persists all critical information:

```json
{
  "position_open": true,
  "symbol": "BTCUSDT",
  "position_leverage": 3,              // Leverage used for this position
  "capital_allocated_usdt": 1000.0,
  "entry_price": 50000.0,              // Entry price saved
  "spot_qty": 0.015,
  "perp_qty": 0.015,
  "funding_received_usdt": 2.50,       // Funding collected
  "entry_fees_usdt": 3.0,              // Entry fees
  "position_opened_at": "2025-10-12T10:00:00",
  "cycle_count": 5,                     // Trading cycles completed
  "initial_portfolio_value_usdt": 5000.0,  // Baseline for total PnL
  "initial_portfolio_timestamp": "2025-10-08T12:00:00",
  "last_updated": "2025-10-12T11:30:00"
}
```

**Important Points**:
- `position_leverage` ≠ `config.leverage`: Position leverage is independent of config
- `cycle_count`: Incremented **only** when a position is closed (not every check)
- `initial_portfolio_value_usdt`: Captured once on first launch
- Deleting this file forces rediscovery and resets PnL baseline

#### State Reconciliation on Startup

At startup, the bot **reconciles** its state with the exchange:

##### **Case 1: Saved state but no position on exchange**
```
Local state: position_open = true
Exchange: No position

→ Action: Clear state (position closed externally)
→ Log: "Position was closed externally"
```

##### **Case 2: No state but position on exchange**
```
Local state: Nothing or position_open = false
Exchange: BTCUSDT position detected

→ Action: Call _discover_existing_position()
→ Detect leverage from exchange
→ Rebuild state from API data
→ Log: "Discovered existing position"
```

##### **Case 3: State and exchange synchronized**
```
→ Continue normally
```

#### Method: `_find_best_funding_opportunity()`

This complex method finds the best trading opportunity in 4 steps:

##### **Step 1: Delta-Neutral Pair Discovery**

```python
# Find all pairs with both spot AND perp
spot_symbols = {s['symbol'] for s in await get_spot_exchange_info()}
perp_symbols = {s['symbol'] for s in await get_perp_exchange_info()}
dn_pairs = spot_symbols & perp_symbols  # Intersection
```

##### **Step 2: Volume Filtering (≥ $250M)**

```python
volume_data = await fetch_24h_ticker()
filtered = [
    pair for pair in dn_pairs
    if volume_data[pair]['quoteVolume'] >= 250_000_000
]
```

**Why $250M?**
- Sufficient liquidity for execution without slippage
- More stable funding rates
- Less risk of manipulation

##### **Step 3: Negative Rate Filtering**

```python
funding_rates = await fetch_current_funding_rates()
filtered = [
    pair for pair in filtered
    if funding_rates[pair] > 0  # Only positive rates
]
```

**Critical**: The filter uses the **current** rate, not the MA rate!
- Even if MA is positive, if current rate is negative → Exclusion
- Avoids entering positions as they turn negative

##### **Step 4: Spot-Perp Spread Filtering (≤ 0.15%)**

```python
spot_prices = await fetch_spot_book_tickers()
perp_prices = await fetch_perp_book_tickers()

for pair in filtered:
    spot_mid = (spot_prices[pair]['bid'] + spot_prices[pair]['ask']) / 2
    perp_mid = (perp_prices[pair]['bid'] + perp_prices[pair]['ask']) / 2
    spread_pct = abs((perp_mid - spot_mid) / spot_mid * 100)

    if spread_pct > 0.15:
        # Exclude this pair
```

**Why 0.15%?**
- Too large spread = risk of slippage on execution
- Indicates liquidity issues or market inefficiencies
- For a DN position, large spread can unbalance entry

##### **Step 5: Best Opportunity Selection**

```python
# MA mode: Calculate MA for each remaining pair
for pair in filtered:
    income_history = await fetch_income_history(pair)
    ma_apr = calculate_funding_rate_ma(income_history, periods=10)

    if ma_apr >= min_funding_apr:
        opportunities[pair] = ma_apr

# Select highest APR
best_pair = max(opportunities, key=opportunities.get)
```

#### Method: `_open_position(symbol, capital_usdt)`

Opens a new delta-neutral position in several steps:

```python
async def _open_position(self, symbol, capital_usdt):
    # 1. Get current price
    spot_price = await self.api_manager.get_spot_ticker_price(symbol)

    # 2. Set leverage on exchange
    leverage = self.config['leverage_settings']['leverage']
    await self.api_manager.set_perp_leverage(symbol, leverage)

    # 3. Rebalance USDT between wallets
    await self.api_manager.rebalance_usdt_by_leverage(leverage)

    # 4. Execute orders (spot + perp)
    result = await self.api_manager.prepare_and_execute_dn_position(
        symbol, capital_usdt, leverage
    )

    # 5. Save state
    self.state['position_open'] = True
    self.state['symbol'] = symbol
    self.state['position_leverage'] = leverage  # Important!
    self.state['entry_price'] = result['entry_price']
    self.state['spot_qty'] = result['spot_qty']
    self.state['perp_qty'] = result['perp_qty']
    self.state['funding_received_usdt'] = 0.0
    self.state['entry_fees_usdt'] = result['fees']
    self.state['position_opened_at'] = datetime.utcnow().isoformat()

    self._save_state()
```

#### Method: `_evaluate_existing_position()`

Evaluates an open position and decides if it should be closed:

```python
async def _evaluate_existing_position(self):
    # 1. Get current data
    current_price = await api_manager.get_spot_ticker_price(symbol)
    perp_position = await api_manager.get_perp_positions()
    funding_history = await api_manager.get_income_history(symbol)

    # 2. Calculate PnLs
    spot_pnl = spot_qty * (current_price - entry_price)
    perp_pnl = perp_position['unrealizedProfit']
    funding_received = sum(funding_history since opened)

    # 3. Combined DN PnL (net)
    combined_pnl = spot_pnl + perp_pnl + funding_received - entry_fees

    # 4. Check closing conditions

    # Condition 1: Stop-loss (perp PnL only)
    stop_loss = self._calculate_safe_stoploss(position_leverage)
    if perp_pnl <= stop_loss * perp_value:
        await self._close_current_position("Emergency stop-loss")
        return

    # Condition 2: Funding covers fees
    total_fees = entry_fees + estimated_exit_fees
    if funding_received >= total_fees * fee_coverage_multiplier:
        await self._close_current_position("Funding covered fees")
        return

    # Condition 3: Position too old
    age_hours = (now - position_opened_at).total_seconds() / 3600
    if age_hours >= max_position_age_hours:
        await self._close_current_position("Max age reached")
        return

    # Condition 4: Better opportunity elsewhere
    best_opportunity = await self._find_best_funding_opportunity()
    if best_opportunity['apr'] > current_apr * 1.5:  # 50% better
        await self._close_current_position("Better opportunity found")
        return

    # Otherwise: Keep position open
    logger.info("Position maintained")
```

#### Method: `_close_current_position(reason)`

Closes the current position and updates state:

```python
async def _close_current_position(self, reason: str):
    logger.info(f"Closing position: {reason}")

    # 1. Close spot leg (market sell)
    spot_result = await api_manager.place_spot_order(
        symbol=symbol,
        side='SELL',
        type='MARKET',
        quantity=spot_qty
    )

    # 2. Close perp position
    perp_result = await api_manager.close_perp_position(symbol)

    # 3. Calculate final PnL
    final_pnl = calculate_final_pnl(...)

    # 4. Increment COMPLETED cycle counter
    self.state['cycle_count'] += 1  # Only here!

    # 5. Clear state
    self.state['position_open'] = False
    self.state['symbol'] = None
    # ... reset all position fields

    self._save_state()

    logger.info(f"Position closed. Final PnL: ${final_pnl:.2f}")
```

---

## Trading Strategy Explained

### Complete Decision Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    BOT STARTUP                              │
│  • Load config & state                                      │
│  • Reconcile with exchange                                 │
│  • Capture portfolio baseline (if first time)              │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│              CYCLE START (every 15min)                      │
│  check_iteration += 1                                       │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
             ┌──────────────────────┐
             │   HEALTH CHECK       │
             │  • USDT balances     │
             │  • API connectivity  │
             │  • State coherent    │
             └──────────┬───────────┘
                        │
                ┌───────┴────────┐
                │   Healthy?     │
                └───────┬────────┘
                    No  │  Yes
              ┌─────────┴──────────┐
              │                    ▼
              │         ┌───────────────────┐
              │         │ Position open?    │
              │         └─────┬─────────┬───┘
              │           Yes │         │ No
              │               ▼         ▼
              │    ┌──────────────┐  ┌────────────────────┐
              │    │   EVALUATE   │  │ FIND OPPORTUNITY   │
              │    │   POSITION   │  │                    │
              │    │              │  │ 1. Volume ≥ $250M  │
              │    │ Calculate PnL│  │ 2. Rate > 0%       │
              │    │ Check:       │  │ 3. Spread ≤ 0.15%  │
              │    │ • Stop-loss  │  │ 4. APR ≥ min       │
              │    │ • Funding OK │  └─────────┬──────────┘
              │    │ • Age limit  │            │
              │    │ • Better     │            │
              │    │   opportunity│            │
              │    └──────┬───────┘            │
              │           │                    │
              │      ┌────┴─────┐         ┌────┴─────┐
              │      │  Close?  │         │  Found?  │
              │      └────┬─────┘         └────┬─────┘
              │       Yes │ No               Yes│ No
              │           ▼                    ▼
              │    ┌────────────┐        ┌──────────┐
              │    │   CLOSE    │        │   OPEN   │
              │    │  POSITION  │        │ POSITION │
              │    │            │        │          │
              │    │ • Sell spot│        │• Set lev │
              │    │ • Close prp│        │• Rebalance│
              │    │ • cycle++  │        │• Buy spot│
              │    └──────┬─────┘        │• Short prp│
              │           │              └─────┬────┘
              │           ▼                    │
              │    ┌─────────────────────────┐│
              │    │   SAVE STATE            ││
              └────►  volume_farming_state.json│
                   └────────────┬─────────────┘
                                │
                                ▼
                   ┌─────────────────────────┐
                   │  WAIT 15 MINUTES        │
                   │  (loop_interval_seconds)│
                   └────────────┬────────────┘
                                │
                                └──────► REPEAT
```

### Pair Selection Criteria

The bot applies **4 successive filters** to guarantee opportunity quality:

#### Filter 1: Minimum Volume ($250M)

**Objective**: Ensure sufficient liquidity

**Implementation**:
```python
volume_threshold = 250_000_000  # $250M in USDT

ticker_24h = await api_manager.fetch_24h_ticker()
eligible_pairs = [
    pair for pair in delta_neutral_pairs
    if ticker_24h[pair]['quoteVolume'] >= volume_threshold
]
```

**Reason**:
- Low volume pairs → high slippage risk
- Unstable funding rates on low volumes
- Difficulty executing large orders

**Example**:
- ✅ BTCUSDT: $500M volume → Eligible
- ✅ ETHUSDT: $300M volume → Eligible
- ❌ OBSCURECOIN: $50M volume → Filtered

#### Filter 2: Positive Funding Rate

**Objective**: Avoid paying funding instead of receiving it

**Implementation**:
```python
current_funding_rates = await api_manager.get_premium_index()

eligible_pairs = [
    pair for pair in eligible_pairs
    if current_funding_rates[pair] > 0
]
```

**Important**: The filter uses the **instantaneous current** rate, not the MA!

**Critical Scenario**:
```
Pair: XYZUSDT
MA over 10 periods: +0.01% (positive)
Current rate: -0.005% (negative)

→ Bot excludes XYZUSDT despite positive MA
→ Avoids entering as market has turned
```

**Logging**:
```
[2025-10-12 11:30:00] Negative rate filter: 2 pair(s) excluded:
  BTCUSDT (-0.0050%), ETHUSDT (-0.0023%)
```

#### Filter 3: Spot-Perp Spread (≤ 0.15%)

**Objective**: Guarantee price alignment between spot and perp

**Implementation**:
```python
spot_tickers = await api_manager.get_spot_book_tickers()
perp_tickers = await api_manager.get_perp_book_tickers()

for pair in eligible_pairs:
    spot_mid = (spot_tickers[pair]['bidPrice'] + spot_tickers[pair]['askPrice']) / 2
    perp_mid = (perp_tickers[pair]['bidPrice'] + perp_tickers[pair]['askPrice']) / 2

    spread_pct = abs((perp_mid - spot_mid) / spot_mid * 100)

    if spread_pct > 0.15:
        # Filter this pair
```

**Spread Calculation**:
```
Example:
Spot mid price: 50,000 USDT
Perp mid price: 50,100 USDT

Absolute spread = |50,100 - 50,000| = 100 USDT
Spread % = 100 / 50,000 × 100 = 0.20%

→ 0.20% > 0.15% → Pair filtered!
```

**Why 0.15%?**
- Normal spread on liquid markets: 0.01% - 0.05%
- Spread > 0.15% indicates:
  - Insufficient liquidity
  - Market inefficiency
  - Unresolved arbitrage risk
- For a DN strategy, large spread = risk of imbalance at opening

**Logging**:
```
[2025-10-12 11:30:05] Spread filter: 1 pair(s) excluded (spread > 0.15%):
  GIGGLEUSDT (7.7996%)
```

#### Filter 4: Minimum APR

**Objective**: Minimum profitability threshold

**Implementation**:
```python
min_funding_apr = config['funding_rate_strategy']['min_funding_apr']  # Default: 7%

# MA mode
for pair in eligible_pairs:
    income_history = await api_manager.get_income_history(pair)
    ma_result = DeltaNeutralLogic.calculate_funding_rate_ma(
        income_history,
        periods=10
    )

    if ma_result['effective_apr'] >= min_funding_apr:
        opportunities[pair] = ma_result['effective_apr']
```

**Why 7%?**
```
Capital: 10,000 USDT
Fees per cycle: ~30 USDT (0.3%)
Average duration: 3-5 days

Minimum APR for profitability:
7% APR ≈ 0.019% per day
Over 5 days: 0.095% = 9.5 USDT funding

With fee_coverage_multiplier = 1.8:
30 × 1.8 = 54 USDT needed
7% APR over 5 days: ~9.5 USDT ❌ Not enough!

In reality, the bot waits until collected funding
reaches threshold before closing, so even at 7% APR,
position can stay open 15-20 days if necessary.
```

### Moving Average vs Instantaneous Mode

The bot supports two modes for evaluating funding rates:

#### Moving Average Mode (Recommended)

**Configuration**:
```json
{
  "use_funding_ma": true,
  "funding_ma_periods": 10
}
```

**Advantages**:
- ✅ Smooths funding rate volatility
- ✅ Avoids ephemeral opportunities (spikes)
- ✅ More stable over time
- ✅ Reduces unnecessary rotations

**Process**:
1. Retrieves last 10 funding payments
2. Calculates average rate
3. Extrapolates to APR: `average × 3 × 365`
4. Compares with threshold

**Display**:
```
┌──────────────────────────────────────────────────────────┐
│ Symbol     │ MA APR % │ Curr APR % │ Next Funding       │
├────────────┼──────────┼────────────┼────────────────────┤
│ BTCUSDT    │   12.50  │    15.30   │ 2025-10-12 16:00   │
│ ETHUSDT    │   10.20  │     8.50   │ 2025-10-12 16:00   │
└──────────────────────────────────────────────────────────┘

MA APR: Moving average (used for selection)
Curr APR: Current instantaneous rate (for comparison)
```

#### Instantaneous Mode

**Configuration**:
```json
{
  "use_funding_ma": false
}
```

**Characteristics**:
- Uses current funding rate directly
- More reactive to changes
- Risk of "chasing" temporary spikes
- Can lead to more rotations

### Position Closing Conditions

The bot closes a position if **one of 4 conditions** is met:

#### Condition 1: Emergency Stop-Loss

**Trigger**: Perp PnL ≤ Stop-Loss Threshold

**Important**: Uses **only perp PnL**, not combined DN PnL!

**Reason**:
- Perp is more volatile (leverage effect)
- Spot is a hedge, but not perfect in real-time
- Protects against liquidation

**Stop-Loss Calculation** (see dedicated section below)

**Example**:
```
Leverage: 3x
Auto-calculated stop-loss: -24%
Perp value: 250 USDT
Current perp PnL: -65 USDT (-26%)

→ -26% < -24% → CLOSE IMMEDIATELY
```

#### Condition 2: Funding Covers Fees

**Trigger**: `funding_received ≥ total_fees × fee_coverage_multiplier`

**Calculation**:
```python
entry_fees = 3.0 USDT
estimated_exit_fees = 3.0 USDT
total_fees = 6.0 USDT

fee_coverage_multiplier = 1.8  # Config

threshold = 6.0 × 1.8 = 10.8 USDT

if funding_received >= 10.8:
    close_position("Funding covered fees")
```

**Why 1.8x?**
- 1.0x = Break-even (no profit)
- 1.8x = 80% profit above fees
- Balances profitability and rotation

**Example Timeline**:
```
T+0h: Position opened, funding_received = 0
T+8h: +$2.50 funding → Total = $2.50
T+16h: +$2.40 funding → Total = $4.90
T+24h: +$2.30 funding → Total = $7.20
T+32h: +$2.10 funding → Total = $9.30
T+40h: +$2.00 funding → Total = $11.30 ≥ $10.80 ✓

→ Position closed after 40h (5 funding payments)
```

#### Condition 3: Maximum Age

**Trigger**: `position_age ≥ max_position_age_hours`

**Configuration**:
```json
{
  "max_position_age_hours": 336  // 14 days
}
```

**Reason**:
- Force rotation even if funding is low
- Avoid staying stuck on low-yield pair
- Opportunity to capture better pairs

**Example**:
```
Position opened: 2025-10-01 10:00 UTC
Now: 2025-10-15 10:00 UTC
Age: 336 hours (14 days)

→ max_position_age_hours = 336 → CLOSE
```

#### Condition 4: Better Opportunity

**Trigger**: New opportunity with significantly higher APR

**Implementation**:
```python
current_symbol_apr = 10.5  # Current position APR

# Find best opportunity
best_opportunity = await self._find_best_funding_opportunity()

if best_opportunity is None:
    return  # No other opportunity

# Threshold: 50% better
if best_opportunity['apr'] > current_symbol_apr * 1.5:
    await self._close_current_position("Better opportunity found")
```

**Example**:
```
Current position: BTCUSDT at 10% APR
New opportunity: ETHUSDT at 16% APR

16% > 10% × 1.5 (15%) ✓

→ Close BTCUSDT, open ETHUSDT
```

**Note**: This 1.5x threshold avoids too frequent rotations for small improvements.

---

## Leverage System and Capital Allocation

### Understanding Leverage in This Bot

The bot supports **configurable leverage from 1x to 3x** on perpetual contracts. This is an advanced feature that improves capital efficiency.

### Capital Allocation Formula

For a delta-neutral strategy with leverage L:

```
Perp Allocation (margin) = 1 / (L + 1)
Spot Allocation = L / (L + 1)
```

**Mathematical Proof**:

To maintain delta-neutral with leverage L:
- Spot notional value = Perp notional value
- Spot capital = S
- Perp capital = P
- S × 1 = P × L (perp has leverage effect)

Therefore: S = P × L

Total capital: S + P = P × L + P = P × (L + 1)

Solve for P:
```
P = Total Capital / (L + 1)
S = Total Capital × L / (L + 1)
```

### Allocation Examples

#### Leverage 1x

```
Total capital: 1,000 USDT

Perp: 1,000 / (1 + 1) = 500 USDT (50%)
Spot: 1,000 × 1 / (1 + 1) = 500 USDT (50%)

Position:
- Buy 500 USDT of BTC on spot
- Short 500 USDT of BTC on perp with 500 USDT margin (1x)

Exposure: 500 long + 500 short = Delta-neutral ✓
```

#### Leverage 2x

```
Total capital: 1,000 USDT

Perp: 1,000 / (2 + 1) = 333.33 USDT (33.3%)
Spot: 1,000 × 2 / (2 + 1) = 666.67 USDT (66.7%)

Position:
- Buy 666.67 USDT of BTC on spot
- Short 666.67 USDT of BTC on perp with 333.33 USDT margin (2x)

Exposure: 666.67 long + 666.67 short = Delta-neutral ✓
```

#### Leverage 3x

```
Total capital: 1,000 USDT

Perp: 1,000 / (3 + 1) = 250 USDT (25%)
Spot: 1,000 × 3 / (3 + 1) = 750 USDT (75%)

Position:
- Buy 750 USDT of BTC on spot
- Short 750 USDT of BTC on perp with 250 USDT margin (3x)

Exposure: 750 long + 750 short = Delta-neutral ✓
```

### Higher Leverage Advantages

**Capital Efficiency**:
```
Scenario: 10,000 USDT capital, funding rate 0.01% (10.95% APR)

Leverage 1x:
- Notional position: 5,000 USDT
- Funding received per payment: 5,000 × 0.01% = 0.50 USDT
- Per day: 1.50 USDT
- Per year: ~547.50 USDT → 5.5% on total capital

Leverage 3x:
- Notional position: 7,500 USDT
- Funding received per payment: 7,500 × 0.01% = 0.75 USDT
- Per day: 2.25 USDT
- Per year: ~821.25 USDT → 8.2% on total capital

Improvement: +50% returns! 🚀
```

### Higher Leverage Risks

**Closer Liquidation**:
```
Leverage 1x: Liquidation at ~-50% movement
Leverage 3x: Liquidation at ~-33% movement

→ This is why the bot automatically adjusts stop-loss!
```

### Position Leverage Preservation

**Critical Principle**: The leverage of an open position **never changes** until closure.

#### Config vs Position Separation

```python
# Configuration
config['leverage_settings']['leverage'] = 3

# Position state
state['position_leverage'] = 2  # Can be different!
```

**Why This Separation?**
- User can change config while a position is open
- Changing leverage mid-position would unbalance the delta-neutral position
- New leverage applies **only to the next position**

#### Leverage Lifecycle

```
Sequence 1: Position with 2x Leverage
─────────────────────────────────────
1. Config: leverage = 2
2. Open position → position_leverage = 2
3. User changes config: leverage = 3
4. Open position maintains position_leverage = 2 ✓
5. Close position
6. Rebalance USDT for leverage = 3
7. Open new position → position_leverage = 3
```

#### Leverage Detection on Startup

During startup, if the bot detects an existing position:

```python
async def _reconcile_position_state(self):
    # Get leverage from exchange
    exchange_leverage = await self.api_manager.get_perp_leverage(symbol)

    if exchange_leverage:
        self.state['position_leverage'] = exchange_leverage
        logger.info(f"[LEVERAGE] Detected: {exchange_leverage}x")
    else:
        # Fallback to config
        self.state['position_leverage'] = self.config['leverage_settings']['leverage']
        logger.warning("[LEVERAGE] Could not detect, using config")
```

#### Mismatch Warning

If `position_leverage != config.leverage`:

```
╔══════════════════════════════════════════════════════════╗
║            ⚠️  LEVERAGE MISMATCH DETECTED                ║
╟──────────────────────────────────────────────────────────╢
║  Position Leverage : 2x                                  ║
║  Config Leverage   : 3x                                  ║
║                                                          ║
║  The position will maintain 2x leverage until closed.    ║
║  New positions will use 3x leverage from config.         ║
╚══════════════════════════════════════════════════════════╝
```

### USDT Rebalancing

Before opening a position, the bot **rebalances USDT** between spot and perp wallets:

```python
async def rebalance_usdt_by_leverage(self, leverage: int) -> bool:
    # 1. Get current balances
    spot_balance = await self.get_spot_balance('USDT')
    perp_balance = await self.get_perp_balance('USDT')
    total_usdt = spot_balance + perp_balance

    # 2. Calculate target allocations
    target_perp = total_usdt / (leverage + 1)
    target_spot = total_usdt * leverage / (leverage + 1)

    # 3. Transfer if necessary
    if spot_balance < target_spot:
        # Transfer perp → spot
        amount = target_spot - spot_balance
        await self.transfer_usdt('PERP_TO_SPOT', amount)

    elif perp_balance < target_perp:
        # Transfer spot → perp
        amount = target_perp - perp_balance
        await self.transfer_usdt('SPOT_TO_PERP', amount)

    return True
```

**Example**:
```
Before rebalancing (leverage = 3):
- Spot: 300 USDT
- Perp: 700 USDT
- Total: 1,000 USDT

Target:
- Spot: 1,000 × 3/4 = 750 USDT
- Perp: 1,000 × 1/4 = 250 USDT

Action:
- Transfer 450 USDT from Perp to Spot

After rebalancing:
- Spot: 750 USDT ✓
- Perp: 250 USDT ✓
```

---

## Risk Management

### Automatic Stop-Loss Calculation

**Principle**: Stop-loss is **automatically calculated** for each leverage, not a manual parameter.

### Calculation Formula

```python
def _calculate_safe_stoploss(self, leverage: int) -> float:
    """
    Calculate safe stop-loss based on leverage.

    Formula:
    SL = [(1 + 1/L) / (1 + m) - 1 - b] × [L / (L + 1)]

    Where:
    L = leverage
    m = maintenance_margin (0.005 = 0.5%)
    b = safety_buffer (0.007 = 0.7%)
    """
    maintenance_margin = 0.005  # 0.5% (ASTER DEX rule)
    safety_buffer = 0.007       # 0.7% (fees + slippage + volatility)

    perp_fraction = leverage / (leverage + 1)
    liquidation_price_ratio = (1 + 1/leverage) / (1 + maintenance_margin)
    safe_price_ratio = liquidation_price_ratio - 1 - safety_buffer

    stop_loss_pct = safe_price_ratio * perp_fraction

    return stop_loss_pct
```

### Stop-Loss Values

| Leverage | Stop-Loss | Distance to Liquidation |
|----------|-----------|-------------------------|
| 1x       | -50.0%    | ~50%                    |
| 2x       | -33.0%    | ~33%                    |
| 3x       | -24.0%    | ~25%                    |

### Safety Buffer Explanation (0.7%)

The safety buffer includes:

1. **Trading fees**: ~0.1%
   - Spot closure: ~0.1%
   - Perp closure: ~0.05%

2. **Slippage**: ~0.2%
   - Market orders during emergency
   - Less liquidity on large orders

3. **Volatility**: ~0.4%
   - Price movement between detection and execution
   - Network latency

**Total: 0.7%** → Comfortable safety margin

### Calculation Example (Leverage 3x)

```
Inputs:
- Leverage (L) = 3
- Maintenance Margin (m) = 0.5%
- Safety Buffer (b) = 0.7%

Step 1: Perp Fraction
perp_fraction = 3 / (3 + 1) = 0.75 (75% of capital in perp notional)

Step 2: Liquidation Price Ratio
liquidation_ratio = (1 + 1/3) / (1 + 0.005)
                  = 1.333 / 1.005
                  = 1.326

Step 3: Safe Price Ratio
safe_ratio = 1.326 - 1 - 0.007
           = 0.319

Step 4: Stop-Loss
stop_loss = 0.319 × 0.75
          = 0.239 = 23.9% ≈ 24%
```

### Stop-Loss Application

**Important**: Stop-loss applies to **Perp PnL**, not combined DN PnL!

```python
# In _evaluate_existing_position()

perp_position = await api_manager.get_perp_positions(symbol)
perp_pnl = float(perp_position['unrealizedProfit'])

# Perp position value
perp_value = capital_allocated_usdt * perp_fraction

# Stop-loss in USDT
stop_loss_pct = self._calculate_safe_stoploss(position_leverage)
stop_loss_usdt = perp_value * stop_loss_pct  # Negative

# Check
if perp_pnl <= stop_loss_usdt:
    logger.error(f"STOP-LOSS TRIGGERED! Perp PnL: ${perp_pnl:.2f} ≤ ${stop_loss_usdt:.2f}")
    await self._close_current_position("Emergency stop-loss")
```

**Numerical Example**:
```
Position:
- Total capital: 1,000 USDT
- Leverage: 3x
- Perp fraction: 25% (250 USDT margin)
- Stop-loss: -24%

Calculation:
Stop-loss USDT = 250 × (-0.24) = -60 USDT

Scenario:
Current perp PnL: -65 USDT

-65 ≤ -60? YES → CLOSE IMMEDIATELY ⚠️
```

### Continuous Health Checks

At each cycle, the bot performs health checks:

#### Check 1: USDT Balances

```python
spot_usdt = await api_manager.get_spot_balance('USDT')
perp_usdt = await api_manager.get_perp_balance('USDT')

if spot_usdt < 10 and perp_usdt < 10:
    logger.error("Insufficient USDT balance in both wallets")
    return False
```

#### Check 2: Valid Leverage

```python
if not (1 <= position_leverage <= 3):
    logger.critical(f"Invalid leverage: {position_leverage}")
    return False
```

#### Check 3: Position Imbalance

```python
imbalance_pct = abs(spot_qty - perp_qty) / spot_qty * 100

if imbalance_pct > 10:
    logger.critical(f"Critical imbalance: {imbalance_pct:.2f}%")
    return False

if imbalance_pct > 5:
    logger.warning(f"Warning: imbalance {imbalance_pct:.2f}%")
```

**Why is Imbalance Important?**
```
Example of imbalance:
- Spot: 0.100 BTC
- Perp: 0.085 BTC
- Imbalance: 15%

If BTC rises by 10%:
- Spot PnL: +10% × 0.100 = +0.010 BTC
- Perp PnL: -10% × 0.085 = -0.0085 BTC
- Net: +0.0015 BTC → Directional exposure!

→ No longer delta-neutral ❌
```

#### Check 4: Minimum Value

```python
if position_value < 5:
    logger.error("Position value too small (< $5)")
    return False
```

### Manual Emergency Exit

The `emergency_exit.py` script allows immediate manual closure:

```bash
$ python emergency_exit.py

╔══════════════════════════════════════════════════════════╗
║              EMERGENCY POSITION EXIT                     ║
╚══════════════════════════════════════════════════════════╝

Current Position:
  Symbol    : BTCUSDT
  Leverage  : 3x
  Capital   : 1,000.00 USDT
  Entry Price: 50,000.00 USDT
  Opened    : 2025-10-10 14:00:00 UTC (2 days ago)

Current PnL:
  Perp PnL  : -15.50 USDT
  Spot PnL  : +12.30 USDT
  Funding   : +8.20 USDT
  Fees      : -6.00 USDT
  ─────────────────────────
  Net DN PnL: -1.00 USDT

⚠️  WARNING: This will close both spot and perp positions
    immediately using MARKET orders (potential slippage).

Type 'CONFIRM' to proceed: _
```

---

## Profit/Loss Calculations and Tracking

### Three PnL Levels

The bot calculates **3 types of PnL**:

1. **Perp Unrealized PnL**: Perpetual position PnL (from exchange)
2. **Spot Unrealized PnL**: Spot position PnL (calculated)
3. **Combined DN PnL (net)**: Total DN strategy PnL including funding and fees

### 1. Perp Unrealized PnL

**Source**: Directly from exchange via API

```python
perp_positions = await api_manager.get_perp_positions()
perp_pnl = float(perp_positions[0]['unrealizedProfit'])
```

**Exchange Calculation**:
```
Perp PnL = Position Size × (Entry Price - Mark Price) × Direction

For a SHORT:
PnL = Quantity × (Entry Price - Current Price)
```

**Example**:
```
Position:
- Type: SHORT
- Quantity: 0.015 BTC
- Entry: 50,000 USDT
- Current: 49,000 USDT

PnL = 0.015 × (50,000 - 49,000) = 0.015 × 1,000 = +15 USDT
```

**Usage**: This PnL is used for **stop-loss trigger** as it's the most volatile.

### 2. Spot Unrealized PnL

**Manual Calculation**:
```python
spot_pnl = spot_qty × (current_price - entry_price)
```

**Why Manual?**
- Spot exchange doesn't calculate unrealized PnL
- We must track entry price in state

**Important**: `entry_price` is saved in `volume_farming_state.json`

**Example**:
```
Position:
- Type: LONG (spot)
- Quantity: 0.015 BTC
- Entry: 50,000 USDT
- Current: 49,000 USDT

Spot PnL = 0.015 × (49,000 - 50,000) = 0.015 × (-1,000) = -15 USDT
```

**Fallback**: If `entry_price` is missing from state, bot uses `perp_position['entryPrice']` as approximation.

### 3. Combined DN PnL (Net)

**Complete Formula**:
```
Combined DN PnL = Spot PnL + Perp PnL + Funding Received - Entry Fees - Exit Fees (estimated)
```

**Components**:

1. **Spot PnL**: Calculated as above
2. **Perp PnL**: From exchange
3. **Funding Received**: Sum of all payments since opening
4. **Entry Fees**: Saved at opening
5. **Exit Fees**: Estimated at ~0.15% of position

**Implementation Code**:
```python
def _calculate_combined_pnl(self, current_price):
    # 1. Spot PnL
    entry_price = self.state.get('entry_price', current_price)
    spot_qty = self.state['spot_qty']
    spot_pnl = spot_qty * (current_price - entry_price)

    # 2. Perp PnL
    perp_position = await api_manager.get_perp_positions(symbol)
    perp_pnl = float(perp_position['unrealizedProfit'])

    # 3. Funding Received
    funding_received = self.state['funding_received_usdt']

    # 4. Fees
    entry_fees = self.state['entry_fees_usdt']
    position_value = self.state['capital_allocated_usdt']
    exit_fees_estimate = position_value * 0.0015  # 0.15%

    # 5. Combined
    combined_pnl = spot_pnl + perp_pnl + funding_received - entry_fees - exit_fees_estimate

    return {
        'spot_pnl': spot_pnl,
        'perp_pnl': perp_pnl,
        'funding_received': funding_received,
        'entry_fees': entry_fees,
        'exit_fees_estimate': exit_fees_estimate,
        'combined_pnl': combined_pnl
    }
```

**Complete Example**:
```
Position: BTCUSDT, 1,000 USDT capital, 3x leverage

Current State:
- Entry price: 50,000 USDT
- Current price: 50,500 USDT (+1%)
- Spot qty: 0.015 BTC
- Perp qty: 0.015 BTC

Calculations:
1. Spot PnL = 0.015 × (50,500 - 50,000) = 0.015 × 500 = +7.50 USDT
2. Perp PnL = 0.015 × (50,000 - 50,500) = -7.50 USDT (exchange value)
3. Funding Received = 12.50 USDT (3 payments)
4. Entry Fees = 3.00 USDT
5. Exit Fees (estimated) = 1,000 × 0.0015 = 1.50 USDT

Combined DN PnL = 7.50 - 7.50 + 12.50 - 3.00 - 1.50 = +8.00 USDT ✅
```

**Interpretation**:
- Spot and Perp cancel out (delta-neutral functioning)
- Profit comes from funding (+12.50)
- After fees, net profit: +8.00 USDT

### 4. Total Portfolio PnL

The bot also tracks **total portfolio PnL** since the beginning:

#### Initial Baseline Capture

**Once only**, on first launch:

```python
async def _capture_initial_portfolio(self):
    if 'initial_portfolio_value_usdt' in self.state:
        return  # Already captured

    # Calculate current total value
    current_value = await self._get_current_portfolio_value()

    # Save as baseline
    self.state['initial_portfolio_value_usdt'] = current_value
    self.state['initial_portfolio_timestamp'] = datetime.utcnow().isoformat()

    logger.info(f"📊 Initial portfolio baseline: ${current_value:.2f}")
```

#### Current Portfolio Value Calculation

**Includes ALL assets**, not just USDT:

```python
async def _get_current_portfolio_value(self) -> float:
    # 1. Spot Value (all assets)
    spot_balances = await api_manager.get_spot_balances()
    spot_total_usdt = 0.0

    for asset, balance in spot_balances.items():
        if balance > 0:
            if asset == 'USDT':
                spot_total_usdt += balance
            else:
                # Get current price
                symbol = f"{asset}USDT"
                price = await api_manager.get_spot_ticker_price(symbol)
                spot_total_usdt += balance * price

    # 2. Perp Wallet (USDT)
    perp_wallet = await api_manager.get_perp_balance('USDT')

    # 3. Perp Unrealized PnL
    perp_positions = await api_manager.get_perp_positions()
    perp_unrealized = sum(float(pos['unrealizedProfit']) for pos in perp_positions)

    # 4. Total
    total_value = spot_total_usdt + perp_wallet + perp_unrealized

    return total_value
```

**Example**:
```
Balances:
- Spot USDT: 2,000
- Spot BTC: 0.05 @ 50,000 = 2,500
- Spot ETH: 1.2 @ 3,000 = 3,600
- Perp Wallet: 1,500
- Perp Unrealized PnL: -50

Total = 2,000 + 2,500 + 3,600 + 1,500 - 50 = 9,550 USDT
```

#### Total PnL Calculation

```python
async def _calculate_total_portfolio_pnl(self):
    if 'initial_portfolio_value_usdt' not in self.state:
        return None

    initial_value = self.state['initial_portfolio_value_usdt']
    current_value = await self._get_current_portfolio_value()

    pnl_usdt = current_value - initial_value
    pnl_pct = (pnl_usdt / initial_value) * 100

    return {
        'initial_value': initial_value,
        'current_value': current_value,
        'pnl_usdt': pnl_usdt,
        'pnl_pct': pnl_pct,
        'since': self.state['initial_portfolio_timestamp']
    }
```

#### Display in Cycle Header

```
╔══════════════════════════════════════════════════════════════════╗
║         CHECK #42 | Trading Cycles Completed: 5                  ║
╟──────────────────────────────────────────────────────────────────╢
║  📊 Portfolio: $9,550.32 | PnL: +$550.32 (+6.11%)                ║
║      Since: 2025-10-08 12:00 UTC                                 ║
╚══════════════════════════════════════════════════════════════════╝
```

**Colors**:
- Positive PnL: Green
- Negative PnL: Red

### PnL Display

The bot displays PnL clearly with colors:

```
═══════════════════════════════════════════════════════════
                    POSITION EVALUATION
═══════════════════════════════════════════════════════════

Symbol          : BTCUSDT
Position Age    : 2 days, 5 hours
Capital         : 1,000.00 USDT
Leverage        : 3x

───────────────────────────────────────────────────────────
                       CURRENT PNL
───────────────────────────────────────────────────────────

Perp Unrealized PnL    : -15.50 USDT (-6.2%)
Spot Unrealized PnL    : +12.30 USDT (+1.6%)
Funding Received       : +8.20 USDT
Entry Fees             : -6.00 USDT
Exit Fees (est.)       : -1.50 USDT
───────────────────────────────────────────────────────────
Combined DN PnL (net)  : -2.50 USDT (-0.25%) ⚠️

═══════════════════════════════════════════════════════════
```

---

## Trading Pair Filtering

The bot implements a **4-level filtering system** to guarantee quality:

### Filtering Pipeline

```
All pairs (spot ∩ perp)
          ↓
    ┌─────────────────────┐
    │  Filter 1: Volume   │
    │    ≥ $250M 24h      │
    └─────────┬───────────┘
              ↓
    ┌─────────────────────┐
    │  Filter 2: Rate     │
    │   Current > 0%      │
    └─────────┬───────────┘
              ↓
    ┌─────────────────────┐
    │  Filter 3: Spread   │
    │    ≤ 0.15%          │
    └─────────┬───────────┘
              ↓
    ┌─────────────────────┐
    │  Filter 4: Min APR  │
    │    ≥ min_apr        │
    └─────────┬───────────┘
              ↓
      Eligible pairs
```

### Filter Logs

The bot displays colored summaries for each filter:

```
[2025-10-12 11:30:00] Volume filter: 35 pair(s) meet ≥$250M requirement

[2025-10-12 11:30:01] Negative rate filter: 3 pair(s) excluded:
  BTCUSDT (-0.0050%), ETHUSDT (-0.0023%), SOLUSDT (-0.0012%)

[2025-10-12 11:30:02] Spread filter: 2 pair(s) excluded (spread > 0.15%):
  GIGGLEUSDT (7.7996%), NEWCOINUSDT (0.2500%)

[2025-10-12 11:30:03] APR filter: 28 pair(s) meet minimum APR threshold

[2025-10-12 11:30:04] ✅ Best opportunity found: AVAXUSDT (MA APR: 15.30%)
```

### Using Verification Scripts

#### `check_funding_rates.py`

Displays funding rates and volume filtering:

```bash
$ python check_funding_rates.py

╔══════════════════════════════════════════════════════════════════╗
║         ASTER DEX - FUNDING RATE ANALYSIS (DELTA-NEUTRAL)        ║
╚══════════════════════════════════════════════════════════════════╝

════════════════════════════════════════════════════════════════════
           ELIGIBLE PAIRS (≥$250M Volume + Positive Rate)
════════════════════════════════════════════════════════════════════

┌────────────┬──────────────┬──────────────┬──────────────────────┐
│ Symbol     │ Current APR  │ 24h Volume   │ Next Funding         │
├────────────┼──────────────┼──────────────┼──────────────────────┤
│ AVAXUSDT   │   15.30%     │  $320.5M     │ 2025-10-12 16:00 UTC │
│ MATICUSDT  │   12.80%     │  $285.2M     │ 2025-10-12 16:00 UTC │
│ OPUSDT     │   10.95%     │  $265.8M     │ 2025-10-12 16:00 UTC │
└────────────┴──────────────┴──────────────┴──────────────────────┘

════════════════════════════════════════════════════════════════════
              FILTERED PAIRS (Low Volume or Negative Rate)
════════════════════════════════════════════════════════════════════

┌────────────┬──────────────┬──────────────┬─────────────────┐
│ Symbol     │ Current APR  │ 24h Volume   │ Exclusion Reason│
├────────────┼──────────────┼──────────────┼─────────────────┤
│ BTCUSDT    │   -0.05%     │  $1.2B       │ Negative rate   │
│ LOWVOLCOIN │   20.00%     │  $50M        │ Low volume      │
└────────────┴──────────────┴──────────────┴─────────────────┘

════════════════════════════════════════════════════════════════════
                            SUMMARY
════════════════════════════════════════════════════════════════════

Total Delta-Neutral Pairs    : 45
Eligible Pairs               : 28 (62.2%)
Filtered Pairs               : 17 (37.8%)
  • Low Volume (<$250M)      : 12
  • Negative Funding Rate    : 5

Best Opportunity             : AVAXUSDT (15.30% APR)
```

#### `check_spot_perp_spreads.py`

Analyzes price spreads:

```bash
$ python check_spot_perp_spreads.py

╔══════════════════════════════════════════════════════════════════╗
║        ASTER DEX - SPOT-PERP PRICE SPREAD ANALYSIS               ║
╚══════════════════════════════════════════════════════════════════╝

════════════════════════════════════════════════════════════════════
                      PRICE SPREAD ANALYSIS
════════════════════════════════════════════════════════════════════

┌─────────┬────────────┬────────────┬─────────┬─────────┬────────┐
│ Symbol  │ Spot Mid   │ Perp Mid   │ Abs Diff│ Spread %│ Status │
├─────────┼────────────┼────────────┼─────────┼─────────┼────────┤
│ BTCUSDT │ 50,000.00  │ 50,005.00  │   5.00  │  0.01%  │   ✅   │
│ ETHUSDT │  3,000.00  │  3,001.50  │   1.50  │  0.05%  │   ✅   │
│ AVAXUSDT│    35.20   │    35.25   │   0.05  │  0.14%  │   ✅   │
│ GIGGLE  │    10.00   │    10.78   │   0.78  │  7.80%  │   ❌   │
└─────────┴────────────┴────────────┴─────────┴─────────┴────────┘

Legend:
  ✅ Green  : Spread < 0.05% (excellent)
  🟡 Yellow : Spread 0.05-0.1% (acceptable)
  🟠 Orange : Spread 0.1-0.15% (limit)
  ❌ Red    : Spread > 0.15% (filtered)

════════════════════════════════════════════════════════════════════
                            SUMMARY
════════════════════════════════════════════════════════════════════

Total Pairs Analyzed         : 45
Pairs Passing Filter (≤0.15%): 43 (95.6%)
Pairs Filtered (>0.15%)      : 2 (4.4%)

Average Spread               : 0.08%
Largest Spread               : 7.80% (GIGGLEUSDT)
Smallest Spread              : 0.01% (BTCUSDT)

Perp Premium Count           : 38 (84.4%)
Perp Discount Count          : 7 (15.6%)
```

---

## Configuration and Deployment

### Configuration File Structure

`config_volume_farming_strategy.json`:

```json
{
  "capital_management": {
    "capital_fraction": 0.98
  },
  "funding_rate_strategy": {
    "min_funding_apr": 5.4,
    "use_funding_ma": true,
    "funding_ma_periods": 10
  },
  "position_management": {
    "fee_coverage_multiplier": 1.1,
    "max_position_age_hours": 336,
    "loop_interval_seconds": 900
  },
  "leverage_settings": {
    "leverage": 3
  }
}
```

### Detailed Parameters

#### capital_management

**`capital_fraction`** (float, 0-1)
- Fraction of total USDT capital to use per position
- Default: 0.98 (98%)
- Leaves 2% in reserve for fees and variations

**Example**:
```
Total available USDT: 10,000
capital_fraction: 0.98

Allocated capital = 10,000 × 0.98 = 9,800 USDT
Reserve = 200 USDT
```

#### funding_rate_strategy

**`min_funding_apr`** (float, %)
- Minimum APR to consider an opportunity
- Default: 5.4%
- Lower = more opportunities, less profitability
- Higher = fewer opportunities, better profitability

**`use_funding_ma`** (boolean)
- true: Use moving average of funding rates (recommended)
- false: Use current instantaneous rate
- Default: true

**`funding_ma_periods`** (int)
- Number of periods for MA
- Default: 10 (= 10 × 8h = 80 hours ≈ 3.3 days)
- Higher = smoother, less reactive
- Lower = less smooth, more reactive

#### position_management

**`fee_coverage_multiplier`** (float)
- Multiplier factor for fees before closing
- Default: 1.1 (110%)
- 1.0 = break-even
- 1.5 = 50% profit above fees
- 2.0 = 100% profit above fees

**Recommendation**:
- Aggressive trading: 1.1 - 1.3
- Balanced trading: 1.5 - 1.8
- Conservative trading: 2.0+

**`max_position_age_hours`** (int, hours)
- Maximum duration to hold a position
- Default: 336 hours (14 days)
- Forces rotation even if low funding

**`loop_interval_seconds`** (int, seconds)
- Interval between each check cycle
- Default: 900 seconds (15 minutes)
- Shorter = more reactive, more API requests
- Longer = less reactive, fewer API requests

#### leverage_settings

**`leverage`** (int, 1-3)
- Leverage for perpetual positions
- Default: 3
- 1x: Less risky, less efficient
- 2x: Balanced
- 3x: More efficient, closer to liquidation

**Important**:
- Stop-loss is automatically calculated (no parameter)
- Changes apply to NEW positions only

### Environment Variables (.env)

```env
# API v3 (Perpetual - Pro API)
API_USER=0xYourEthereumWalletAddress
API_SIGNER=0xYourApiSignerAddress
API_PRIVATE_KEY=0xYourPrivateKey

# API v1 (Spot - API)
APIV1_PUBLIC_KEY=your_public_key_here
APIV1_PRIVATE_KEY=your_private_key_here
```

**Getting Keys**: See API Authentication section in CLAUDE.md

### Docker Deployment

#### docker-compose.yml

```yaml
version: '3.8'

networks:
  default:
    driver: bridge
    ipam:
      config:
        - subnet: 172.8.144.0/22

services:
  dn_bot:
    build: .
    container_name: dn_farming_bot
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: '512M'
    env_file:
      - .env
    restart: unless-stopped
    stdin_open: true
    tty: true
    volumes:
      - ./:/app/
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

#### Docker Commands

**Start bot**:
```bash
docker-compose up --build
```

**Background**:
```bash
docker-compose up --build -d
```

**View logs**:
```bash
docker-compose logs -f
```

**Stop bot**:
```bash
docker-compose down
```

**Restart**:
```bash
docker-compose restart
```

### Local Deployment

**Prerequisites**: Python 3.8+ (3.10+ recommended)

```bash
# 1. Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure .env
cp .env.example .env
# Edit .env with your API keys

# 4. Launch bot
python volume_farming_strategy.py
```

### First Launch

On first launch, the bot:

1. **Loads configuration**
2. **Connects to API**
3. **Checks balances**
4. **Captures portfolio baseline**
5. **Checks existing positions**
6. **Starts trading cycle**

**Typical logs**:
```
[2025-10-12 10:00:00] INFO - Bot starting...
[2025-10-12 10:00:01] INFO - Config loaded: leverage=3x, min_apr=5.4%
[2025-10-12 10:00:02] INFO - 📊 Initial portfolio baseline: $10,000.00
[2025-10-12 10:00:03] INFO - No existing position found
[2025-10-12 10:00:04] INFO - [LEVERAGE] Auto-calculated stop-loss: -24.0%
[2025-10-12 10:00:05] INFO - Starting main strategy loop...
```

---

## Utility Scripts

### `check_funding_rates.py`

**Usage**: Analyze funding rates without launching the bot

```bash
python check_funding_rates.py
```

**Features**:
- Lists all delta-neutral pairs
- Displays current funding rates in APR
- Applies filters ($250M volume, positive rate)
- Identifies best opportunity
- Displays summary statistics

**Use cases**:
- Check opportunities before starting bot
- Debug why certain pairs are excluded
- Analyze market trends

### `check_spot_perp_spreads.py`

**Usage**: Analyze spot-perp price spreads

```bash
python check_spot_perp_spreads.py
```

**Features**:
- Retrieves spot and perp mid prices
- Calculates absolute and percentage spread
- Color-codes by spread level
- Identifies problematic pairs
- Statistics (average, min, max, premium/discount)

**Use cases**:
- Identify liquidity issues
- Debug spread exclusions
- Detect arbitrage opportunities

### `emergency_exit.py`

**Usage**: Manually close a position immediately

```bash
python emergency_exit.py
```

**Features**:
- Reads position from state
- Displays full details (symbol, leverage, capital, PnL)
- Requires explicit confirmation
- Closes both legs simultaneously (market orders)
- Updates state file

**Use cases**:
- Emergency (major market event)
- Manual intervention needed
- Test closure without waiting for bot

**⚠️ Warnings**:
- Uses market orders (slippage risk)
- Immediate closure (not optimal timing)
- Use only when necessary

### `calculate_safe_stoploss.py`

**Usage**: Validate stop-loss calculations

```bash
python calculate_safe_stoploss.py
```

**Output**:
```
╔══════════════════════════════════════════════════════════╗
║        SAFE STOP-LOSS CALCULATIONS                       ║
╚══════════════════════════════════════════════════════════╝

Parameters:
  Maintenance Margin  : 0.50%
  Safety Buffer       : 0.70%

═══════════════════════════════════════════════════════════

Leverage 1x:
  Perp Fraction       : 50.0%
  Liquidation Distance: ~50.0%
  Safe Stop-Loss      : -50.0%
  Safety Margin       : 0.7%

Leverage 2x:
  Perp Fraction       : 33.3%
  Liquidation Distance: ~33.3%
  Safe Stop-Loss      : -33.0%
  Safety Margin       : 0.7%

Leverage 3x:
  Perp Fraction       : 25.0%
  Liquidation Distance: ~25.0%
  Safe Stop-Loss      : -24.0%
  Safety Margin       : 0.7%

═══════════════════════════════════════════════════════════
```

**Use cases**:
- Understand stop-loss calculations
- Verify safety margins
- Validate formula modifications

### `get_volume_24h.py`

**Usage**: Get 24h volume for a specific pair

```bash
python get_volume_24h.py BTCUSDT
```

**Output**:
```
BTCUSDT 24h Volume: $1,250,500,000 (1.25B)
Status: ✅ Passes $250M filter
```

**Use cases**:
- Quickly check pair volume
- Confirm pair eligibility
- Monitor volume evolution

---

## Monitoring and Debugging

### Log Files

**`volume_farming.log`**
- All bot events
- Automatic rotation: 10 MB max, 3 files kept
- Levels: DEBUG, INFO, WARNING, ERROR, CRITICAL

**Log Format**:
```
[2025-10-12 11:30:00] INFO - Message here
[2025-10-12 11:30:01] WARNING - Warning message
[2025-10-12 11:30:02] ERROR - Error message
```

### Filter Logs

**Leverage logs**:
```bash
grep "\[LEVERAGE\]" volume_farming.log
```

**Error logs**:
```bash
grep "ERROR" volume_farming.log
```

**Position closure logs**:
```bash
grep "Closing position" volume_farming.log
```

**Last 50 lines**:
```bash
tail -50 volume_farming.log
```

**Real-time monitoring**:
```bash
tail -f volume_farming.log
```

### State File

**`volume_farming_state.json`**

**View state**:
```bash
cat volume_farming_state.json | python -m json.tool
```

**Check leverage**:
```bash
cat volume_farming_state.json | grep position_leverage
```

**Check PnL baseline**:
```bash
cat volume_farming_state.json | grep initial_portfolio
```

### Common Issues

#### Issue: "Leverage mismatch detected"

**Symptom**:
```
⚠️  LEVERAGE MISMATCH DETECTED
Position Leverage: 2x
Config Leverage: 3x
```

**Cause**: Config modified while position is open

**Solution**: Normal! Position will maintain 2x until closure. Next position will use 3x.

**Action**: None (unless you want to force closure with `emergency_exit.py`)

---

#### Issue: "Could not detect leverage"

**Symptom**:
```
[LEVERAGE] Could not detect leverage from exchange, using config: 3x
```

**Cause**:
- Temporary API error
- No perp position on exchange
- Connection issue

**Solution**: Bot fallbacks to config, verify manually:
```bash
python tests/test_leverage_detection.py
```

---

#### Issue: Spot PnL showing $0.00

**Symptom**: Spot PnL displayed at $0.00 despite open position

**Cause**: `entry_price` missing from state file

**Solution**:
1. Bot auto-corrects using `perp_position['entryPrice']`
2. Wait for next evaluation cycle
3. Or restart bot (it will reconcile state)

---

#### Issue: Portfolio value too low

**Symptom**: Portfolio value seems to only count USDT

**Cause**: Bug in `_get_current_portfolio_value()` - doesn't fetch prices for other assets

**Solution**: Verify code fetches prices for BTC, ETH, etc.

```python
# Should look like this:
for asset, balance in spot_balances.items():
    if asset != 'USDT' and balance > 0:
        symbol = f"{asset}USDT"
        price = await api_manager.get_spot_ticker_price(symbol)
        spot_total_usdt += balance * price
```

---

#### Issue: Bot not trading certain pairs

**Symptom**: Bot ignores pairs with good funding rate

**Diagnosis**:
```bash
# 1. Check volume and funding rate
python check_funding_rates.py

# 2. Check spot-perp spread
python check_spot_perp_spreads.py
```

**Possible causes**:
- Volume < $250M
- Current rate negative (even if MA positive)
- Spread > 0.15%
- APR < min_funding_apr

---

#### Issue: "Insufficient USDT balance"

**Symptom**:
```
ERROR - Insufficient USDT balance in both wallets
```

**Cause**: Not enough USDT to open a position

**Solution**:
1. Deposit more USDT
2. Reduce `capital_fraction` in config
3. Check if USDT locked in open orders

---

#### Issue: API errors / Rate limiting

**Symptom**:
```
ERROR - API request failed: 429 Too Many Requests
```

**Cause**: Too many API requests

**Solution**:
- Increase `loop_interval_seconds` (e.g., 1800 = 30 min)
- Verify no multiple bots on same API keys
- Wait for rate limit to reset

---

### Performance Metrics

The bot displays metrics in each cycle:

```
═══════════════════════════════════════════════════════════
               PERFORMANCE METRICS
═══════════════════════════════════════════════════════════

Total Trading Cycles    : 5
Successful Closures     : 5 (100%)
Emergency Exits         : 0
Average Position Age    : 4.2 days
Total Funding Collected : $125.50
Total Fees Paid         : $90.00
Net Profit              : +$35.50

Portfolio Performance:
  Initial Value         : $10,000.00
  Current Value         : $10,035.50
  Total PnL             : +$35.50 (+0.36%)
  Duration              : 4 days

Annualized Return       : ~32.9% APR
```

---

## Real-World Examples and Use Cases

### Scenario 1: Typical Profitable Position

**Configuration**:
- Capital: 1,000 USDT
- Leverage: 3x
- Pair: AVAXUSDT
- MA APR: 15.30%

**Timeline**:

**T+0h (Opening)**:
```
AVAX Price: 35.00 USDT

Allocation:
- Spot: 750 USDT → 21.43 AVAX
- Perp: 250 USDT margin, short 21.43 AVAX @ 3x

Entry fees: 3.00 USDT

State:
- Position open ✓
- Funding received: 0
- Combined PnL: -3.00 (fees)
```

**T+8h (1st funding)**:
```
AVAX Price: 35.20 (+0.57%)

PnL:
- Spot: 21.43 × (35.20 - 35.00) = +4.29 USDT
- Perp: -4.25 USDT (approximation)
- Funding received: +2.80 USDT
- Combined PnL: +4.29 - 4.25 + 2.80 - 3.00 = -0.16 USDT

Decision: MAINTAIN (insufficient funding)
```

**T+16h (2nd funding)**:
```
AVAX Price: 34.80 (-0.57%)

PnL:
- Spot: 21.43 × (34.80 - 35.00) = -4.29 USDT
- Perp: +4.25 USDT
- Funding received: +2.80 + 2.75 = +5.55 USDT
- Combined PnL: -4.29 + 4.25 + 5.55 - 3.00 = +2.51 USDT

Decision: MAINTAIN (need ~10.80 for 1.8x fees)
```

**T+24h to T+48h**:
```
Funding collection continues...
T+24h: +8.20 USDT
T+32h: +10.80 USDT
T+40h: +13.20 USDT ✓
```

**T+40h (Closure)**:
```
AVAX Price: 35.10 (+0.29%)

Final PnL:
- Spot: 21.43 × (35.10 - 35.00) = +2.14 USDT
- Perp: -2.10 USDT
- Total funding: +13.20 USDT
- Entry fees: -3.00 USDT
- Exit fees: -1.50 USDT
─────────────────────────
Net combined PnL: +8.74 USDT (+0.87%)

Decision: CLOSE (funding 13.20 > 10.80 threshold)

Duration: 40 hours (1.67 days)
ROI: 0.87% in 1.67 days → ~190% APR 🎉
```

### Scenario 2: Stop-Loss Triggered

**Configuration**:
- Capital: 1,000 USDT
- Leverage: 3x
- Pair: VOLATILUSDT
- Stop-loss: -24% (auto-calculated)

**Timeline**:

**T+0h (Opening)**:
```
Price: 100.00 USDT
Position: 7.5 VOLATIL (spot) + short 7.5 (perp)
Perp margin: 250 USDT
```

**T+4h (Violent movement)**:
```
Price: 92.00 USDT (-8%)

PnL:
- Spot: 7.5 × (92 - 100) = -60 USDT
- Perp: 7.5 × (100 - 92) × 3 = +180 USDT (with leverage)
  → Unrealized perp PnL via API: ~+58 USDT (net of fees/funding)

🤔 Perp PnL positive, but...
```

**T+5h (Violent reversal)**:
```
Price: 108.00 USDT (+8% from entry)

PnL:
- Spot: 7.5 × (108 - 100) = +60 USDT
- Perp: 7.5 × (100 - 108) × 3 = -180 USDT
  → Unrealized perp PnL: ~-62 USDT

Stop-loss threshold: 250 × (-0.24) = -60 USDT

Perp PnL: -62 USDT < -60 USDT ❌

⚠️ STOP-LOSS TRIGGERED!

Immediate closure:
- Spot: +60 USDT
- Perp: -62 USDT
- Funding: +0.50 USDT (1 payment)
- Fees: -4.50 USDT
─────────────────────────
Net PnL: -6.00 USDT (-0.6%)

Protection: Avoids larger loss if movement continues
```

### Scenario 3: Rotation for Better Opportunity

**Current Position**:
- OPUSDT @ 10% APR
- Opened 2 days ago
- Funding received: 5.50 USDT (not yet at threshold)

**New Opportunity Detected**:
- AVAXUSDT @ 18% APR
- 18% > 10% × 1.5 (15%) ✓

**Action**:
```
[2025-10-12 11:30:00] INFO - Better opportunity found: AVAXUSDT (18% vs 10%)

Close OPUSDT:
- Combined PnL: +2.00 USDT (small profit)

Open AVAXUSDT:
- Capital: 1,000 USDT
- MA APR: 18%

Benefit: 80% more funding rate!
```

### Scenario 4: First Launch and Portfolio Tracking

**Initial Baseline**:
```
[2025-10-08 12:00:00] Bot startup

Balances:
- Spot USDT: 5,000
- Spot BTC: 0.05 @ 50,000 = 2,500
- Spot ETH: 1.0 @ 3,000 = 3,000
- Perp USDT: 2,000

Total Portfolio: 5,000 + 2,500 + 3,000 + 2,000 = 12,500 USDT

Saved state:
{
  "initial_portfolio_value_usdt": 12500.0,
  "initial_portfolio_timestamp": "2025-10-08T12:00:00"
}
```

**After 7 Days of Trading**:
```
[2025-10-15 12:00:00] Cycle #150

Current Balances:
- Spot USDT: 5,100
- Spot BTC: 0.048 @ 52,000 = 2,496
- Spot ETH: 1.05 @ 3,100 = 3,255
- Perp USDT: 2,150
- Perp Unrealized: +50

Total Portfolio: 5,100 + 2,496 + 3,255 + 2,150 + 50 = 13,051 USDT

PnL Calculation:
- Initial: 12,500 USDT
- Current: 13,051 USDT
- PnL: +551 USDT (+4.41%)

Annualized ROI: 4.41% / 7 days × 365 = ~229% APR 🚀

Display:
╔══════════════════════════════════════════════════════════════════╗
║  📊 Portfolio: $13,051.00 | PnL: +$551.00 (+4.41%)               ║
║      Since: 2025-10-08 12:00 UTC (7 days)                        ║
╚══════════════════════════════════════════════════════════════════╝
```

---

## Frequently Asked Questions

### Q1: Can I change leverage while a position is open?

**A**: You can change the config, but it won't affect the current position. New leverage applies only to the next position.

```
Config: leverage = 2 → Change to 3
Current position: Stays at 2x until closure
Next position: Will open at 3x
```

---

### Q2: What happens if I delete the state file?

**A**: The bot will:
1. Check exchange for existing positions
2. If position found: rediscover and rebuild state
3. If no position: start fresh
4. **Important**: PnL baseline will be reset

---

### Q3: Can the bot handle multiple positions simultaneously?

**A**: No, the bot maintains **one delta-neutral position at a time**. This is by design for:
- Simplified management
- Reduced risk
- Easier monitoring

---

### Q4: How does the bot handle Internet outages/restarts?

**A**: Thanks to state persistence:
1. State is saved after every change
2. On restart: bot loads state
3. Reconciles with exchange
4. Continues normally

**No data lost** ✓

---

### Q5: Is it really delta-neutral? No price risk?

**A**: In theory yes, in practice there are slight risks:

**Residual Risk Sources**:
1. **Temporal imbalance**: Spot and perp orders don't execute at exact same time
2. **Slippage**: Execution price ≠ expected price
3. **Fees**: Transaction costs
4. **Negative funding**: If rate becomes negative before closure

**Mitigations**:
- Spread filter (≤ 0.15%)
- Health checks (imbalance ≤ 10%)
- Automatic stop-loss
- Negative rate filter

---

### Q6: How much minimum capital is recommended?

**A**:
- **Technical minimum**: ~$100
- **Practical minimum**: $1,000+
- **Optimal**: $5,000+

**Reason**: Fees (0.3% per cycle) are fixed. On small capital, they eat a larger share of profit.

**Example**:
```
$100 Capital:
Fees per cycle: $0.30
Funding at 10% APR over 3 days: ~$0.08
Net: -$0.22 ❌ Loss

$5,000 Capital:
Fees per cycle: $15
Funding at 10% APR over 3 days: ~$41
Net: +$26 ✓ Profit
```

---

### Q7: What's the difference between "cycle count" and "check iteration"?

**A**:
- **Check Iteration**: Number of times bot executed its loop (every 15 min)
- **Cycle Count**: Number of **completed** trading cycles (opened → held → closed)

```
Timeline:
T+0: Check #1 → Opens position → cycle_count = 0
T+15min: Check #2 → Evaluates position → cycle_count = 0
T+30min: Check #3 → Evaluates position → cycle_count = 0
...
T+40h: Check #160 → Closes position → cycle_count = 1 ✓
T+40h15min: Check #161 → Opens new position → cycle_count = 1
...
T+80h: Check #320 → Closes position → cycle_count = 2 ✓
```

---

### Q8: How can I increase profitability?

**Options**:

1. **Increase leverage** (2x → 3x)
   - +50% funding rate collected
   - But stop-loss closer

2. **Reduce fee_coverage_multiplier** (1.8 → 1.3)
   - Faster closure
   - More rotations
   - Risk: less profit per cycle

3. **Reduce min_funding_apr** (7% → 5%)
   - More opportunities
   - Risk: less profitable

4. **Increase capital_fraction** (0.98 → 0.99)
   - Uses more capital
   - Risk: less buffer

**⚠️ Caution**: Any optimization for more profit increases risk!

---

### Q9: Does the bot support other exchanges?

**A**: No, it's specifically designed for ASTER DEX:
- Uses ASTER's unique authentication (v1 + v3)
- Adapted to specific endpoints
- Optimized for ASTER's funding schedule (8h)

**Porting to another exchange would require**:
- Rewriting `aster_api_manager.py`
- Adapting authentication
- Adjusting endpoints
- Modifying fee calculations

---

### Q10: How much APR can I expect on average?

**A**: It depends heavily on market conditions:

**Bull Market**:
- High funding rates: 10-30% APR
- Many opportunities
- Effective APR after fees: **15-25%**

**Sideways Market**:
- Moderate funding rates: 5-15% APR
- Average opportunities
- Effective APR after fees: **5-12%**

**Bear Market**:
- Often negative funding rates
- Few opportunities
- Effective APR: **0-5%** (or negative)

**Realistic long-term average: 8-15% APR**

---

## Conclusion

This delta-neutral trading bot on ASTER DEX is a sophisticated system that combines:
- **Quantitative strategy**: Funding rate capture
- **Risk management**: Stop-loss, health checks, multi-level filtering
- **Automation**: 24/7 without intervention
- **Efficiency**: Configurable leverage up to 3x
- **Monitoring**: Complete and colorful PnL tracking

**Key Points to Remember**:

1. ✅ **Delta-neutral** protects against price movements
2. ✅ **Funding rates** are the profit source
3. ✅ **4 filters** guarantee quality (volume, rate, spread, APR)
4. ✅ **Leverage** maximizes efficiency (but increases risk)
5. ✅ **Automatic stop-loss** protects against liquidation
6. ✅ **Clean architecture** facilitates maintenance and extension

**Recommendations**:

- Start with **2x leverage** to familiarize
- Use **MA mode** (more stable)
- Monitor logs regularly
- Test with **small capital** first
- Use utility scripts to understand the market

**Resources**:
- CLAUDE.md: Detailed technical documentation
- README.md: User guide
- Utility scripts: Analysis and debugging

**Disclaimer**: Crypto trading involves risks. This bot does not guarantee profits. Only use capital you can afford to lose.

---

*Document created on 2025-10-12 | Version 1.0 | For ASTER DEX Delta-Neutral Trading Bot*
