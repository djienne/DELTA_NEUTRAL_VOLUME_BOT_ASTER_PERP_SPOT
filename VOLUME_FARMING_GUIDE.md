# Delta-Neutral Funding Rate Farming - Strategy Guide

## Overview

This automated strategy manages delta-neutral positions to farm perpetual funding rates while minimizing market risk. It uses configurable moving averages for stable funding rate signals and includes comprehensive position tracking, automatic rebalancing, and intelligent position management.

**Key Concept**: The bot maintains equal USD value in spot (long) and perpetual (short) positions. As funding payments accrue on the perpetual position, the bot captures these payments while market price movements largely cancel out between the two legs.

## Key Features

### 1. Automatic Position Discovery
- **Detects existing positions**: If you open a position manually, the bot will discover it on startup
- **Fetches funding history**: Retrieves actual funding payments received from API
- **Calculates position age**: Determines when the position was opened
- **Adopts position tracking**: Automatically starts monitoring the existing position

### 2. Funding Rate Analysis
- **10-period moving average** (default): Smooths volatility over ~3.3 days of funding payments
- **Real-time effective APR**: Calculates actual APR for 1x leverage
- **Standard deviation tracking**: Measures funding rate stability
- **Configurable periods**: Use `--ma-periods` to adjust (1-50 recommended)

### 3. State Persistence
- **Automatic saving**: State saved after every cycle and trade
- **Crash recovery**: Resumes from last saved state after restart
- **Position tracking**: Remembers open positions, fees paid, and funding received
- **Statistics tracking**: Cumulative P/L, positions opened/closed

### 4. Automatic USDT Rebalancing
- **Before opening positions**: Rebalances USDT 50/50 between spot and perp to maximize available capital
- **After closing positions**: Rebalances automatically to prepare for next position
- **Smart transfers**: Only transfers if difference > $1 to avoid micro-transfers
- **No manual intervention**: Uses your existing API keys to transfer between wallets
- **Maximizes capital efficiency**: Ensures you can deploy the maximum position size

### 5. Intelligent Exit Conditions
The bot closes positions when:
1. **Fees covered**: Funding payments >= fee_coverage_multiplier × (entry + exit fees)
2. **Better opportunity**: New pair with >10% APR improvement (min 4hr hold)
3. **Position age exceeded**: Default 24 hours maximum
4. **Health issues**: Imbalance >10% or critical position health
5. **Emergency stop loss**: Unrealized PnL < -10% (configurable)

## Usage

### Starting the Bot

The strategy is configured via `config_volume_farming_strategy.json` and runs continuously:

```bash
# Using Docker (recommended)
docker-compose up --build

# Direct Python execution
python volume_farming_strategy.py
```

### Configuration File

All parameters are set in `config_volume_farming_strategy.json`:

```json
{
  "capital_management": {
    "capital_fraction": 0.95
  },
  "funding_rate_strategy": {
    "min_funding_apr": 15.0,
    "use_funding_ma": true,
    "funding_ma_periods": 10
  },
  "position_management": {
    "fee_coverage_multiplier": 1.5,
    "max_position_age_hours": 24,
    "loop_interval_seconds": 300
  },
  "risk_management": {
    "emergency_stop_loss_pct": -10.0
  }
}
```

### Configuration Parameters Explained

**Capital Management:**
- `capital_fraction`: Percentage of available balance to use (0.95 = 95%)
- Higher values = larger positions, less reserve

**Funding Rate Strategy:**
- `min_funding_apr`: Minimum APR to consider (%)
  - Too low: Accept unprofitable opportunities
  - Too high: Miss valid opportunities
- `use_funding_ma`: Use moving average (recommended: true)
- `funding_ma_periods`: MA calculation window (default: 10)
  - Lower (3-5): Responsive but volatile
  - Default (10): Balanced
  - Higher (15-20): Stable but slower

**Position Management:**
- `fee_coverage_multiplier`: When to close (1.5 = 150% of fees)
  - 1.2x: Aggressive, more rotation
  - 1.5x: Balanced
  - 2.0x+: Conservative, ensure profit
- `max_position_age_hours`: Force close after this duration
- `loop_interval_seconds`: Time between checks (300 = 5 minutes)

**Risk Management:**
- `emergency_stop_loss_pct`: Hard stop if PnL drops below this (%)

## Position Discovery & Reconciliation Flow

When you start the bot, it performs intelligent reconciliation between state file and exchange:

### Startup Reconciliation

**Exchange is ALWAYS the source of truth.**

The bot checks 4 possible scenarios:

#### 1. Position in State BUT NOT on Exchange
```
Tracked: BTCUSDT
Exchange: (none)
→ Action: Clear state, position was closed externally
```

#### 2. Position on Exchange BUT NOT in State
```
Tracked: (none)
Exchange: BTCUSDT
→ Action: Discover and adopt exchange position
```

#### 3. Position in BOTH but Different
```
Tracked: BTCUSDT
Exchange: ETHUSDT
→ Action: Clear state, adopt exchange position
```

#### 4. Position in BOTH and Matches
```
Tracked: BTCUSDT
Exchange: BTCUSDT
→ Action: Update funding data from exchange
→ Action: Update position value from exchange
→ Action: Synchronize state file
```

### What Gets Updated from Exchange

When positions match, these values are updated from exchange:
- **Total funding received**: Latest from API
- **Position value**: Current USD value
- **Quantities**: Spot and perp quantities
- **Funding payment count**: Actual payments received

### Example Output

**Case 1 - Clearing stale state:**
```
Reconciling position state with exchange...
Position BTCUSDT tracked in state but not found on exchange
Position was likely closed externally. Clearing state.
```

**Case 2 - Discovering new position:**
```
Reconciling position state with exchange...
Exchange has delta-neutral position but not tracked in state
Checking for existing delta-neutral positions...
Discovered existing position: ETHUSDT
  Position opened: 2025-01-15 14:30:22
  Funding received: $0.0856
Successfully adopted existing position
```

**Case 3 - Symbol mismatch:**
```
Reconciling position state with exchange...
Tracked position BTCUSDT not found on exchange
Exchange has: ETHUSDT
Adopting the exchange position...
```

**Case 4 - Successful sync:**
```
Reconciling position state with exchange...
Position BTCUSDT confirmed on exchange
  Updating funding from exchange: $0.0856 -> $0.1234
  Updating position value: $250.00 -> $252.34
```

### Continuous Monitoring

After startup reconciliation, the bot:
1. Evaluates position every 5 minutes
2. Checks funding coverage progress
3. Monitors for better opportunities
4. Tracks health and PnL
5. Updates state file after every cycle

## State File Format

The bot saves state to `volume_farming_state.json`:

```json
{
  "current_position": {
    "symbol": "BTCUSDT",
    "capital": 250.0,
    "funding_rate": 0.0001234,
    "effective_apr": 45.32,
    "spot_qty": 0.00362,
    "perp_qty": 0.00362,
    "entry_price": 69000.0
  },
  "position_opened_at": "2025-01-15T14:30:22.123456",
  "total_funding_received": 0.0856,
  "entry_fees_paid": 0.50,
  "cycle_count": 47,
  "total_profit_loss": 15.67,
  "total_positions_opened": 8,
  "total_positions_closed": 7,
  "last_updated": "2025-01-15T18:45:10.789012"
}
```

## Monitoring

The bot logs to both console and `volume_farming.log`:

### Startup
```
Volume Farming Strategy initialized (Dry Run: False)
Capital Range: $50.0 - $500.0
Min Funding APR: 15.0%
Fee Coverage Multiplier: 1.5x
Funding Rate Mode: Moving Average (10 periods)

Checking for existing delta-neutral positions...
Discovered existing position: BTCUSDT
  Spot balance: 0.003620
  Perp position: -0.003620
  Position value: $250.00
  Position opened: 2025-01-15 14:30:22
  Funding received: $0.0856
  Funding payments: 4
Successfully adopted existing position
  Current funding rate: 0.0123% (MA)
  Effective APR: 45.32%
```

### Monitoring Cycle
```
================================================================================
CYCLE #12 - 2025-01-15 18:45:10
================================================================================
Performing health check...
Spot USDT: $1250.00
Perp USDT: $1180.00
Health check passed (Existing DN positions: 1)

Evaluating position on BTCUSDT...
  Unrealized PnL: $2.34 (0.94%)
  Position age: 4.25 hours
  Funding periods: 0.53
  Estimated funding received: $0.0856
  Total fees (entry + exit): $0.75
  Fees coverage ratio: 0.11x (target: 1.5x)
Position healthy, continuing to hold...
```

### Position Closing & Rebalancing
```
Fees covered! Ready to close and rotate.
Closing position on BTCUSDT...
Position closed successfully!
  Total funding received: $1.2345
  Total fees paid: $0.75
  Net profit (this position): $0.4845
  Cumulative P/L: $15.67
  Total positions closed: 8

Rebalancing USDT between spot and perp wallets...
Rebalanced $150.25 USDT (PERP_TO_SPOT)
  Spot USDT: $500.00 -> $825.12
  Perp USDT: $1150.25 -> $825.12

Scanning for best funding rate opportunity (MA 10 periods)...
Best opportunity: ETHUSDT
  Funding Rate: 0.0156% (MA)
  Effective APR (1x): 56.94%
  MA Periods: 10
  MA StDev: 0.0012%
  Next Funding: 2025-01-15T20:00:00

Opening position on ETHUSDT...
Rebalancing USDT before opening position...
USDT wallets already balanced (difference < $1)
```

## Best Practices

### Capital Management
- **Start small**: Test with $50-100 before scaling up
- **Automatic rebalancing**: Bot handles USDT distribution between wallets
- **Capital efficiency**: Uses 95% of available balance (configurable)
- **Minimum requirement**: $50 total ($25 spot + $25 perpetual after rebalance)
- **Reserve buffer**: Keep 5% reserve for fees and slippage

### Moving Average Selection
- **3-5 periods**: Responsive, captures short-term opportunities
  - Pros: Quick to react to rate changes
  - Cons: More susceptible to volatility
  - Best for: Active traders monitoring frequently
- **10 periods (default)**: Balanced stability and responsiveness
  - Pros: Filters noise while staying current
  - Cons: May miss very short-term spikes
  - Best for: Automated 24/7 operation
- **15-20 periods**: Maximum stability
  - Pros: Very stable, ignores short-term volatility
  - Cons: Slower to detect opportunity changes
  - Best for: Conservative long-term farming

### Fee Coverage Strategy
- **1.2x**: Aggressive rotation
  - More trading volume
  - Faster capital turnover
  - Higher risk of unprofitable closes
- **1.5x (default)**: Balanced approach
  - Reasonable profit margin
  - Moderate rotation frequency
- **2.0x+**: Conservative guarantee
  - Ensures significant profit
  - Longer hold times
  - May miss better opportunities

### Position Age Limits
- **12 hours**: Maximum rotation
  - Best for: High-volatility markets
  - Risk: May close profitable positions early
- **24 hours (default)**: Balanced
  - Allows multiple funding payments
  - Prevents indefinite holds
- **48+ hours**: Patient strategy
  - Best for: Stable funding rate environments
  - Risk: May hold during rate degradation

### Leverage Requirements
- **Always use 1x leverage**
- Bot automatically validates and sets leverage
- Higher leverage breaks delta-neutral assumption
- Higher leverage = higher liquidation risk

## Troubleshooting

### Bot doesn't find my position
- Check if position is truly delta-neutral (balanced spot + short perp)
- Ensure position has funding payment history
- Check logs for "Could not fetch funding history" warnings

### Position not closing
- Verify fees coverage ratio in logs
- Check if there are health issues preventing closure
- Ensure funding rate is stable (check MA stdev)

### State file corrupted
- Delete `volume_farming_state.json`
- Restart bot - it will discover existing positions
- Funding history will be fetched from API

## Safety Features

1. **Health checks**: Runs before every trade
2. **Balance validation**: Ensures sufficient funds
3. **Leverage enforcement**: Confirms 1x leverage
4. **Position limits**: One position at a time
5. **Emergency stop loss**: Protects against large losses
6. **Imbalance detection**: Monitors position drift
7. **Critical issue detection**: Stops trading on severe problems

## Performance Monitoring

Track these metrics in logs:
- **Fees coverage ratio**: Progress toward profitability
- **Funding periods**: How many 8hr periods elapsed
- **Position age**: Time held
- **Unrealized PnL**: Current profit/loss
- **Cumulative P/L**: Total strategy performance
- **Positions opened/closed**: Activity count

## Shutdown

Press `Ctrl+C` to gracefully shutdown:
- Saves final state
- Keeps position open (restart to continue)
- Closes API connections
- Shows final statistics

To force-close position on shutdown, modify `_shutdown()` method in code.

## Codebase Architecture

### Module Overview

The codebase is organized for maximum clarity and testability:

**`aster_api_manager.py` (49KB)**
- All API interactions (spot, perpetual, transfers)
- Integrated Ethereum signature authentication
- Funding rate moving average fetching
- Position management and health checks
- Exchange info caching and precision handling

**`strategy_logic.py` (23KB)**
- Pure computational logic (no API calls)
- Delta-neutral calculations
- Funding rate MA calculations
- Risk assessment and position health
- Position sizing and rebalancing logic

**`volume_farming_strategy.py` (52KB)**
- Main strategy loop and orchestration
- Position monitoring and state management
- Opportunity scanning and decision making
- State persistence and recovery

**`utils.py` (711 bytes)**
- Shared utility functions
- Precision truncation for order sizing

### Key Design Principles

1. **Separation of Concerns**: API, logic, and strategy are completely separate
2. **Testability**: Pure functions in `strategy_logic.py` are easily testable
3. **Stateless Logic**: All strategy logic is stateless and deterministic
4. **Single Responsibility**: Each module has one clear purpose
5. **No Duplicate Code**: Funding rate MA integrated, no standalone scripts

### Recent Improvements

- Integrated `api_client.py` into `aster_api_manager.py`
- Removed `funding_rate_moving_average.py` (functionality now in API manager)
- Fixed parameter name bug in position opening
- Added leverage validation for safety
- Comprehensive error handling and edge case protection

## Advanced Topics

### Custom Strategy Development

To build custom strategies on this framework:

1. **Use `strategy_logic.py` functions**: All calculations are available as static methods
2. **Extend `aster_api_manager.py`**: Add new API methods as needed
3. **Create new strategy class**: Similar to `VolumeFarmingStrategy`
4. **Reuse state management**: `volume_farming_state.json` pattern works well

### Integration with Other Tools

The modular architecture allows easy integration:
- Import `strategy_logic.py` for calculations in other scripts
- Use `aster_api_manager.py` as standalone API client
- Parse `volume_farming_state.json` for external monitoring

### Performance Optimization

For high-frequency operation:
- Reduce `loop_interval_seconds` (minimum 60 recommended)
- Use shorter MA periods (3-5) for faster response
- Lower `fee_coverage_multiplier` for more aggressive rotation
- Consider multiple bot instances for different strategies

## Support and Contributing

- **Issues**: Report bugs via GitHub Issues
- **Documentation**: Keep README and this guide updated
- **Testing**: Always test changes with small capital first
- **Logs**: Include relevant logs when reporting issues