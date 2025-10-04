# Delta-Neutral Funding Rate Farming Bot on ASTER DEX Perp Spot

An automated, delta-neutral trading bot for the Aster DEX that operates on both spot and perpetual markets to capture funding payments while minimizing directional market risk.

> Referral link to support this work: https://www.asterdex.com/en/referral/164f81 . Earn 10% rebate on fees (I put maximum for you).

> This bot is ideal for the Stage 3 of Aster airdrop, as it will farm both perpetual and spot volume for you.

The bot continuously scans for profitable funding rate opportunities, opens positions, monitors them until fees are covered, and rotates to maximize returns.

## ⚙️ How It Works

The bot operates in a continuous loop:

1.  **Health Check**: Verifies account health, balances, and existing positions before any action.
2.  **Position Monitoring**: If a position is open, it's monitored for exit conditions:
    *   Funding payments cover entry/exit fees (configurable multiplier).
    *   A better funding rate opportunity is found.
    *   Maximum position age is reached.
    *   Emergency stop-loss is triggered.
3.  **Opportunity Scanning**: If no position is open, it scans all delta-neutral pairs for the most profitable and stable funding rate APR.
4.  **Open Position**: Automatically calculates position size, rebalances USDT between spot and perpetual wallets, and executes trades to open a new delta-neutral position (long spot, short perpetuals).
5.  **Repeat**: Saves its state and repeats the cycle.

## ✨ Features

-   **Fully Automated**: 24/7 operation for scanning, opening, monitoring, and closing positions.
-   **Delta-Neutral**: Minimizes directional risk with balanced spot and perpetual positions.
-   **Funding Rate Arbitrage**: Profits from collecting funding payments on perpetuals.
-   **MA Filtering**: Uses a funding rate moving average to avoid volatile, short-lived opportunities.
-   **Dynamic Pair Discovery**: Automatically finds all tradable delta-neutral pairs.
-   **State Persistence**: Resumes seamlessly from `volume_farming_state.json` after restarts.
-   **Configurable**: Tune all parameters via `config_volume_farming_strategy.json`.
-   **Risk Management**: Includes emergency stop-loss, health checks, and 1x leverage enforcement.
-   **Clean Architecture**: Modular code with a clear separation of concerns.
-   **Dockerized**: Easy to deploy with Docker and Docker Compose.

## 🏗️ Architecture

-   **`aster_api_manager.py`**: Handles all API interactions (spot, perpetual, auth, transfers).
-   **`strategy_logic.py`**: Contains the pure computational logic for calculations and risk assessment.
-   **`volume_farming_strategy.py`**: Implements the main strategy loop, state management, and decision-making.
-   **`utils.py`**: Provides shared utility functions.

## 📋 Prerequisites

> -   [Docker](https://www.docker.com/get-started) & [Docker Compose](https://docs.docker.com/compose/install/)
> -   Python 3.8+ (if not using Docker)
> -   Aster DEX API credentials (v1 and v3)

## 🛠️ Installation and Configuration

### 1. Clone the Repository

```bash
git clone <repository_url>
cd DELTA_NEUTRAL_VOLUME_BOT_ASTER
```

### 2. Set Up API Keys

Create a `.env` file from the example and add your API credentials.

```bash
cp .env.example .env
```

Edit `.env` with your Aster exchange API keys:

```env
# Aster API v3 Credentials
API_USER="your_eth_wallet_address"
API_SIGNER="your_api_signer_key"
API_PRIVATE_KEY="your_api_private_key"

# Aster API v1 Credentials
APIV1_PUBLIC_KEY="your_v1_public_key"
APIV1_PRIVATE_KEY="your_v1_private_key"
```

> **Note:** Never commit your `.env` file.

### 3. Configure the Strategy

Edit `config_volume_farming_strategy.json` to tune the bot's parameters.

| Parameter                 | Description                                                                 | Default |
| ------------------------- | --------------------------------------------------------------------------- | ------- |
| `capital_fraction`        | Percentage of available USDT to use per position.                           | `0.95`  |
| `min_funding_apr`         | Minimum annualized APR to consider for an opportunity.                      | `15`    |
| `use_funding_ma`          | Use a moving average of funding rates for stability.                        | `true`  |
| `funding_ma_periods`      | Number of periods for the funding rate moving average.                      | `10`    |
| `fee_coverage_multiplier` | Close when funding covers fees by this factor (e.g., 1.5 = 150%).           | `1.5`   |
| `max_position_age_hours`  | Maximum hours to hold a position before rotating.                           | `24`    |
| `loop_interval_seconds`   | Seconds to wait between each strategy cycle.                                | `300`   |
| `emergency_stop_loss_pct` | Hard stop-loss as a percentage of position value.                           | `-10`   |

## 🚀 Usage

### With Docker (Recommended)

```bash
# Start the bot
docker-compose up --build

# Run in the background
docker-compose up --build -d

# View logs
docker-compose logs -f

# Stop the bot
docker-compose down
```

### Without Docker

```bash
# Install dependencies
pip install -r requirements.txt

# Run the bot
python volume_farming_strategy.py
```

## 📊 Monitoring

-   **Logs**: All activity is logged to the console and `volume_farming.log`.
-   **State**: The bot's current state is saved in `volume_farming_state.json`. The bot resumes from this file on restart.

<img src="screen.png" width="800">

## 📌 Important Notes

> **Leverage**: This strategy **requires 1x leverage**. The bot automatically validates and sets leverage to 1x. Using higher leverage breaks the delta-neutral assumption.

> **Capital**: A minimum of **$50 USDT** is recommended ($25 spot, $25 perpetuals). The bot automatically rebalances USDT between wallets.

> **Fees**: Entry and exit fees are ~0.1% each. The bot ensures funding payments cover these fees before closing a position for profit.

## 🔍 Troubleshooting

-   **Position not tracked after restart?** Delete `volume_farming_state.json` and restart the bot. It will rediscover the position from the exchange.
-   **Insufficient balance errors?** Ensure you have USDT in both spot and perpetual wallets. The bot can auto-rebalance if the total balance is sufficient.
-   **No opportunities found?** Your `min_funding_apr` might be too high, or market conditions may not be favorable.

## 📚 Additional Documentation

For a detailed strategy explanation, see [VOLUME_FARMING_GUIDE.md](VOLUME_FARMING_GUIDE.md).

> ## ⚠️ Disclaimer
>
> **Trading cryptocurrencies involves significant risk.** This bot is provided as-is, without any warranty or guarantee of profitability. The authors are not responsible for any financial losses. Use at your own risk and only trade with capital you can afford to lose.
