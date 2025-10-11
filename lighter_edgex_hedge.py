#!/usr/bin/env python3
"""
auto_rotation_bot.py
--------------------
Automated delta-neutral position rotation bot.

This bot continuously:
1. Analyzes funding rates across multiple symbols
2. Opens the best delta-neutral position
3. Holds for 8 hours collecting funding
4. Closes the position
5. Waits briefly and repeats

Features:
- Persistent state across restarts
- Automatic recovery from crashes
- Comprehensive PnL tracking (trading, funding, fees)
- Health monitoring during hold period
- Graceful shutdown handling

Usage:
    python auto_rotation_bot.py
    python auto_rotation_bot.py --state-file custom_state.json
"""

import asyncio
import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, asdict

from dotenv import load_dotenv
import websockets

# EdgeX and Lighter SDKs
from edgex_sdk import Client as EdgeXClient
import lighter

import edgex_client
import lighter_client

# ANSI color codes for console output
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    GRAY = '\033[90m'

class BalanceFetchError(Exception):
    """Raised when balance retrieval fails."""
    pass


class RateLimitError(Exception):
    """Raised when API rate limit is hit."""
    pass


# ==================== Rate Limit Handling ====================

def is_rate_limit_error(exc: Exception) -> bool:
    """Check if an exception is a rate limit error (HTTP 429)."""
    error_str = str(exc)
    return (
        "429" in error_str or
        "Too Many Requests" in error_str or
        "code\":23000" in error_str or
        "rate limit" in error_str.lower()
    )


async def retry_with_backoff(
    func,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 30.0,
    jitter: bool = True
):
    """
    Retry an async function with exponential backoff on rate limit errors.

    Args:
        func: Async function to retry
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        backoff_factor: Multiplier for each retry (exponential backoff)
        max_delay: Maximum delay between retries
        jitter: Add random jitter to prevent thundering herd
    """
    import random

    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return await func()
        except Exception as exc:
            last_exception = exc

            # Check if it's a rate limit error
            if not is_rate_limit_error(exc):
                # Not a rate limit error, raise immediately
                raise

            # If this was the last attempt, raise
            if attempt >= max_retries:
                logger.error(f"Rate limit retry exhausted after {max_retries} attempts")
                raise RateLimitError(f"Rate limit exceeded after {max_retries} retries: {exc}") from exc

            # Calculate delay with exponential backoff
            delay = min(initial_delay * (backoff_factor ** attempt), max_delay)

            # Add jitter (randomize ±25% to prevent synchronized retries)
            if jitter:
                jitter_range = delay * 0.25
                delay = delay + random.uniform(-jitter_range, jitter_range)

            logger.warning(
                f"Rate limit hit (attempt {attempt + 1}/{max_retries}), "
                f"retrying in {delay:.1f}s... Error: {str(exc)[:100]}"
            )

            await asyncio.sleep(delay)

    # Should never reach here, but just in case
    if last_exception:
        raise last_exception


# ==================== Global Rate Limiting ====================

# Global semaphore to limit concurrent Lighter API calls
# This prevents overwhelming Lighter's API with too many concurrent requests
LIGHTER_API_SEMAPHORE = asyncio.Semaphore(2)  # Max 2 concurrent Lighter API calls

# ==================== Logging Setup ====================

os.makedirs('logs', exist_ok=True)

# File handler - DEBUG level (mode='w' clears log on each start)
file_handler = logging.FileHandler('logs/auto_rotation_bot.log', mode='w')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s'))

# Console handler - INFO level with explicit stdout
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))

# Root logger
logging.basicConfig(level=logging.DEBUG, handlers=[file_handler, console_handler], force=True)
logger = logging.getLogger(__name__)

# Force flush on every log
for handler in logging.getLogger().handlers:
    handler.flush = lambda: None  # Will auto-flush with PYTHONUNBUFFERED

# Silence noisy third-party loggers
logging.getLogger('websockets').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('lighter').setLevel(logging.WARNING)
logging.getLogger('lighter_client').setLevel(logging.WARNING)
logging.getLogger('edgex_client').setLevel(logging.WARNING)

# ==================== Constants & Environment ====================

DEFAULT_SYMBOLS: List[str] = [
    "BTC",
    "ETH",
    "SOL",
    "BNB",
    "ASTER",
    "PAXG",
    "DOGE",
    "XRP",
    "LINK",
    "HYPE",
    "XPL",
    "TRUMP",
    "LTC",
    "PUMP",
    "FARTCOIN",
]


def load_env() -> dict:
    """Load required environment variables for both exchanges."""
    load_dotenv()
    env: Dict[str, object] = {}

    env["EDGEX_BASE_URL"] = os.getenv("EDGEX_BASE_URL", "https://pro.edgex.exchange")
    env["EDGEX_WS_URL"] = os.getenv("EDGEX_WS_URL", "wss://quote.edgex.exchange")
    env["EDGEX_ACCOUNT_ID"] = os.getenv("EDGEX_ACCOUNT_ID")
    env["EDGEX_STARK_PRIVATE_KEY"] = os.getenv("EDGEX_STARK_PRIVATE_KEY")

    env["LIGHTER_BASE_URL"] = os.getenv("LIGHTER_BASE_URL", os.getenv("BASE_URL", "https://mainnet.zklighter.elliot.ai"))
    env["LIGHTER_WS_URL"] = os.getenv("LIGHTER_WS_URL", os.getenv("WEBSOCKET_URL", "wss://mainnet.zklighter.elliot.ai/stream"))
    env["API_KEY_PRIVATE_KEY"] = os.getenv("API_KEY_PRIVATE_KEY") or os.getenv("LIGHTER_PRIVATE_KEY")
    env["ACCOUNT_INDEX"] = int(os.getenv("ACCOUNT_INDEX", os.getenv("LIGHTER_ACCOUNT_INDEX", "0")))
    env["API_KEY_INDEX"] = int(os.getenv("API_KEY_INDEX", os.getenv("LIGHTER_API_KEY_INDEX", "0")))
    env["MARGIN_MODE"] = "cross"

    missing = [key for key in ("EDGEX_ACCOUNT_ID", "EDGEX_STARK_PRIVATE_KEY", "API_KEY_PRIVATE_KEY") if not env.get(key)]
    if missing:
        logger.warning("Missing env vars: %s. Trading may fail.", missing)

    return env

# ==================== State Management ====================

class BotState:
    """State machine for the rotation bot."""
    IDLE = "IDLE"
    ANALYZING = "ANALYZING"
    OPENING = "OPENING"
    HOLDING = "HOLDING"
    CLOSING = "CLOSING"
    WAITING = "WAITING"
    ERROR = "ERROR"
    SHUTDOWN = "SHUTDOWN"


@dataclass
class BotConfig:
    """Bot configuration parameters."""
    symbols_to_monitor: List[str]
    quote: str = "USD"
    leverage: int = 3
    notional_per_position: float = 100.0
    hold_duration_hours: float = 8.0
    wait_between_cycles_minutes: float = 5.0
    check_interval_seconds: int = 60
    min_net_apr_threshold: float = 5.0
    min_volume_usd: float = 250_000_000  # Minimum 24h combined volume in USD (default: $250M)
    max_spread_pct: float = 0.15  # Maximum cross-exchange spread percentage (default: 0.15%)
    enable_stop_loss: bool = True
    enable_pnl_tracking: bool = True
    enable_health_monitoring: bool = True

    @staticmethod
    def load_from_file(config_file: str) -> 'BotConfig':
        """Load configuration from JSON file."""
        try:
            with open(config_file, 'r') as f:
                data = json.load(f)

            # Remove comment fields (any key starting with 'comment')
            data = {k: v for k, v in data.items() if not k.startswith('comment')}

            # Provide defaults for any missing fields (backward compatibility)
            defaults = {
                'symbols_to_monitor': DEFAULT_SYMBOLS,
                'quote': 'USD',
                'leverage': 3,
                'notional_per_position': 100.0,
                'hold_duration_hours': 8.0,
                'wait_between_cycles_minutes': 5.0,
                'check_interval_seconds': 60,
                'min_net_apr_threshold': 5.0,
                'min_volume_usd': 250_000_000,
                'max_spread_pct': 0.15,
                'enable_stop_loss': True,
                'enable_pnl_tracking': True,
                'enable_health_monitoring': True
            }

            for key, default_value in defaults.items():
                if key not in data:
                    data[key] = default_value
                    logger.info(f"Using default value for {key}: {default_value}")

            return BotConfig(**data)
        except FileNotFoundError:
            logger.warning(f"Config file {config_file} not found, using defaults")
            return BotConfig(symbols_to_monitor=DEFAULT_SYMBOLS)
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            return BotConfig(symbols_to_monitor=DEFAULT_SYMBOLS)


# ==================== Exchange Helper Functions ====================

def _round_to_tick(value: float, tick: float) -> float:
    """Round `value` to the nearest multiple of `tick`."""
    if not tick or tick <= 0:
        return value
    d_value = Decimal(str(value))
    d_tick = Decimal(str(tick))
    return float((d_value / d_tick).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * d_tick)


def _ceil_to_tick(value: float, tick: float) -> float:
    """Round `value` up to the nearest multiple of `tick`."""
    if not tick or tick <= 0:
        return value
    d_value = Decimal(str(value))
    d_tick = Decimal(str(tick))
    return float((d_value / d_tick).quantize(Decimal('1'), rounding=ROUND_UP) * d_tick)


def _floor_to_tick(value: float, tick: float) -> float:
    """Round `value` down to the nearest multiple of `tick`."""
    if not tick or tick <= 0:
        return value
    d_value = Decimal(str(value))
    d_tick = Decimal(str(tick))
    return float((d_value / d_tick).quantize(Decimal('1'), rounding=ROUND_DOWN) * d_tick)


def compute_base_size_from_quote(avg_mid: float, size_quote: float) -> float:
    """Convert quote notional into base size using the average mid price."""
    if avg_mid <= 0:
        raise ValueError("Invalid mid price to compute base size.")
    return size_quote / avg_mid


def get_avg_mid(
    lighter_bid: Optional[float],
    lighter_ask: Optional[float],
    edgex_bid: Optional[float],
    edgex_ask: Optional[float],
) -> float:
    """Average mid price between both exchanges, falling back gracefully."""
    mids: List[float] = []
    if lighter_bid and lighter_ask:
        mids.append((lighter_bid + lighter_ask) / 2.0)
    if edgex_bid and edgex_ask:
        mids.append((edgex_bid + edgex_ask) / 2.0)

    if mids:
        return sum(mids) / len(mids)

    if lighter_bid and lighter_ask:
        return (lighter_bid + lighter_ask) / 2.0
    if edgex_bid and edgex_ask:
        return (edgex_bid + edgex_ask) / 2.0
    if lighter_bid and edgex_ask:
        return (lighter_bid + edgex_ask) / 2.0
    if edgex_bid and lighter_ask:
        return (edgex_bid + lighter_ask) / 2.0

    raise RuntimeError("No usable prices from either venue.")


def _calculate_apr(rate: float, periods_per_day: int) -> float:
    """Convert a per-period funding rate (decimal form) into annualized percentage."""
    return rate * periods_per_day * 365 * 100.0


def utc_now() -> datetime:
    """Return a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return an ISO 8601 timestamp suffixed with Z for UTC."""
    return utc_now().isoformat().replace("+00:00", "Z")


def to_iso_z(dt_obj: datetime) -> str:
    """Convert datetime to ISO string with Z suffix, adding UTC if naive."""
    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=timezone.utc)
    return dt_obj.isoformat().replace("+00:00", "Z")


def from_iso_z(iso_string: str) -> datetime:
    """Parse ISO timestamp with Z or +00:00 suffix, handling malformed formats."""
    # Handle malformed timestamps like '...+00:00Z' or '...+00:00+00:00'
    # Clean up by removing 'Z' first, then any duplicate timezone info
    cleaned = iso_string.rstrip('Z')
    # If we have duplicate +00:00, keep only the first one
    if cleaned.count('+00:00') > 1:
        parts = cleaned.split('+00:00')
        cleaned = parts[0] + '+00:00'
    # If we don't have timezone info, add it
    elif not ('+' in cleaned or '-' in cleaned[-6:]):
        cleaned = cleaned + '+00:00'

    return datetime.fromisoformat(cleaned)


async def configure_leverage(
    leverage: float,
    env: dict,
    edgex: EdgeXClient,
    lighter_signer: lighter.SignerClient,
    edgex_contract_id: str,
    lighter_market_id: int,
    verify: bool = True,
) -> Tuple[bool, bool]:
    """
    Attempt to configure leverage on both exchanges. Returns tuple of booleans
    indicating success for (EdgeX, Lighter).
    """
    edgex_success = False
    lighter_success = False

    logger.info("Setting leverage to %sx on both exchanges...", leverage)
    print(f"\nSetting leverage to {leverage}x on both exchanges...")

    try:
        await lighter_client.lighter_set_leverage(
            lighter_signer,
            lighter_market_id,
            int(leverage),
            env.get("MARGIN_MODE", "cross"),
        )
        lighter_success = True
        print(f"  ✓ Lighter: Set to {leverage}x ({env.get('MARGIN_MODE', 'cross')} margin)")
    except Exception as exc:
        print(f"  ✗ Lighter: Failed to set leverage - {exc}")
        logger.error("Lighter leverage set failed: %s", exc)

    try:
        await edgex_client.set_edgex_leverage(
            edgex,
            int(env["EDGEX_ACCOUNT_ID"]),
            edgex_contract_id,
            leverage,
        )
        edgex_success = True
        print(f"  ✓ EdgeX: Set to {leverage}x")
    except Exception as exc:
        print(f"  ✗ EdgeX: Failed to set leverage - {exc}")
        logger.error("EdgeX leverage set failed: %s", exc)

    if verify and edgex_success:
        try:
            current_leverage = await edgex_client.get_edgex_leverage(edgex, edgex_contract_id)
            if current_leverage:
                if abs(current_leverage - leverage) < 0.1:
                    print(f"  ✓ EdgeX: Verified at {current_leverage}x")
                else:
                    print(f"  ⚠ EdgeX: Set to {leverage}x but reads as {current_leverage}x")
            else:
                print("  ⚠ EdgeX: Could not verify (no open position)")
        except Exception as exc:
            logger.debug("Could not verify EdgeX leverage: %s", exc)

    if lighter_success and verify:
        print("  ℹ Lighter: Verification not available (applies on next order)")

    if not (edgex_success and lighter_success):
        print("\n⚠️  WARNING: Leverage setting failed on one or more exchanges!")
        print("  This may result in unexpected margin usage.")
        return edgex_success, lighter_success

    print("✓ Leverage configured on both exchanges\n")
    return edgex_success, lighter_success


async def fetch_symbol_spread(symbol: str, quote: str, env: dict) -> Optional[float]:
    """
    Fetch mid prices from both exchanges and calculate cross-exchange spread percentage.

    Returns:
        Spread percentage (e.g., 0.15 for 0.15%), or None if unavailable
    """

    async def fetch_edgex_mid() -> Optional[float]:
        client = None
        try:
            client = EdgeXClient(
                base_url=env["EDGEX_BASE_URL"],
                account_id=int(env["EDGEX_ACCOUNT_ID"]) if env.get("EDGEX_ACCOUNT_ID") else 0,
                stark_private_key=env.get("EDGEX_STARK_PRIVATE_KEY", ""),
            )
            contract_name = f"{symbol.upper()}{quote.upper()}"
            contract_id, _, _ = await edgex_client.get_edgex_contract_details(client, contract_name)
            best_bid, best_ask = await edgex_client.get_edgex_best_bid_ask(client, contract_id)

            if best_bid and best_ask:
                return (best_bid + best_ask) / 2
            elif best_bid or best_ask:
                return best_bid if best_bid else best_ask
            return None
        except Exception as e:
            logger.debug(f"Error fetching EdgeX mid price for {symbol}: {e}")
            return None
        finally:
            if client and hasattr(client, 'internal_client'):
                try:
                    await client.internal_client.close()
                except Exception:
                    pass

    async def fetch_lighter_mid() -> Optional[float]:
        api_client = None
        try:
            # Use global semaphore to limit concurrent Lighter API calls
            async with LIGHTER_API_SEMAPHORE:
                api_client = lighter.ApiClient(configuration=lighter.Configuration(host=env["LIGHTER_BASE_URL"]))
                order_api = lighter.OrderApi(api_client)
                market_id, _, _ = await lighter_client.get_lighter_market_details(order_api, symbol)
                best_bid, best_ask = await lighter_client.get_lighter_best_bid_ask(order_api, symbol, market_id, timeout=10.0)

                if best_bid and best_ask:
                    return (best_bid + best_ask) / 2
                elif best_bid or best_ask:
                    return best_bid if best_bid else best_ask
                return None
        except Exception as e:
            logger.debug(f"Error fetching Lighter mid price for {symbol}: {e}")
            return None
        finally:
            if api_client:
                try:
                    await api_client.close()
                except Exception:
                    pass

    edgex_mid, lighter_mid = await asyncio.gather(fetch_edgex_mid(), fetch_lighter_mid())

    if edgex_mid is None or lighter_mid is None:
        return None

    # Calculate cross-exchange spread percentage
    price_diff = abs(edgex_mid - lighter_mid)
    avg_mid = (edgex_mid + lighter_mid) / 2
    spread_pct = (price_diff / avg_mid) * 100

    return spread_pct


async def fetch_symbol_volume(symbol: str, quote: str, env: dict) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Fetch 24h trading volume for a symbol from both exchanges.

    Returns:
        Tuple of (edgex_volume, lighter_volume, total_volume) in USD, or (None, None, None) if unavailable
    """

    async def fetch_edgex_volume() -> Optional[float]:
        client = None
        try:
            client = EdgeXClient(
                base_url=env["EDGEX_BASE_URL"],
                account_id=int(env["EDGEX_ACCOUNT_ID"]) if env.get("EDGEX_ACCOUNT_ID") else 0,
                stark_private_key=env.get("EDGEX_STARK_PRIVATE_KEY", ""),
            )
            contract_name = f"{symbol.upper()}{quote.upper()}"
            metadata = await client.get_metadata()
            contracts = metadata.get("data", {}).get("contractList", [])

            contract_id = None
            for contract in contracts:
                if contract.get("contractName") == contract_name:
                    contract_id = contract.get("contractId")
                    break

            if not contract_id:
                logger.warning(f"EdgeX contract not found for {symbol}{quote}")
                return None

            # Wrap API call with retry logic
            async def _fetch():
                quote_data = await client.quote.get_24_hour_quote(contract_id)
                if quote_data.get("code") == "SUCCESS" and quote_data.get("data"):
                    record = quote_data["data"][0]
                    # EdgeX uses 'value' field for 24h volume in quote currency
                    for field in ["value", "volume24h", "volume", "volume24H", "quoteVolume"]:
                        if field in record and record[field]:
                            return float(record[field])
                return None

            return await retry_with_backoff(_fetch, max_retries=2, initial_delay=1.0)
        except RateLimitError as e:
            logger.warning(f"EdgeX volume rate limit for {symbol} after retries: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error fetching EdgeX volume for {symbol}: {e}")
            return None
        finally:
            if client and hasattr(client, 'internal_client'):
                try:
                    await client.internal_client.close()
                except Exception:
                    pass

    async def fetch_lighter_volume() -> Optional[float]:
        api_client = None
        try:
            # Use global semaphore to limit concurrent Lighter API calls
            async with LIGHTER_API_SEMAPHORE:
                api_client = lighter.ApiClient(configuration=lighter.Configuration(host=env["LIGHTER_BASE_URL"]))
                order_api = lighter.OrderApi(api_client)

                # Wrap API call with retry logic
                async def _fetch():
                    stats_response = await order_api.exchange_stats()
                    # Find the market by symbol
                    for market_stats in stats_response.order_book_stats:
                        if market_stats.symbol.upper() == symbol.upper():
                            return float(market_stats.daily_quote_token_volume)
                    return None

                return await retry_with_backoff(_fetch, max_retries=2, initial_delay=1.0)
        except RateLimitError as e:
            logger.warning(f"Lighter volume rate limit for {symbol} after retries: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error fetching Lighter volume for {symbol}: {e}")
            return None
        finally:
            if api_client:
                try:
                    await api_client.close()
                except Exception:
                    pass

    edgex_vol, lighter_vol = await asyncio.gather(fetch_edgex_volume(), fetch_lighter_volume())

    total_vol = None
    if edgex_vol is not None and lighter_vol is not None:
        total_vol = edgex_vol + lighter_vol
    elif edgex_vol is not None:
        total_vol = edgex_vol
    elif lighter_vol is not None:
        total_vol = lighter_vol

    return edgex_vol, lighter_vol, total_vol


async def fetch_symbol_funding(symbol: str, quote: str, env: dict, check_volume: bool = True, min_volume_usd: float = 250_000_000, max_spread_pct: float = 0.15) -> dict:
    """
    Fetch funding rates and volume for a single symbol across both venues.

    Args:
        symbol: Trading symbol (e.g., "BTC")
        quote: Quote currency (e.g., "USD")
        env: Environment variables
        check_volume: Whether to check volume threshold (default: True)
        min_volume_usd: Minimum combined 24h volume in USD (default: $250M)
        max_spread_pct: Maximum cross-exchange spread percentage (default: 0.15%)
    """
    logger.info("Checking funding for %s...", symbol)

    edgex_rate_decimal: Optional[float] = None
    lighter_rate_decimal: Optional[float] = None
    edgex_apr: Optional[float] = None
    lighter_apr: Optional[float] = None

    async def fetch_edgex_rate() -> Optional[float]:
        edgex = None
        try:
            edgex = EdgeXClient(
                base_url=env["EDGEX_BASE_URL"],
                account_id=int(env["EDGEX_ACCOUNT_ID"]) if env.get("EDGEX_ACCOUNT_ID") else 0,
                stark_private_key=env.get("EDGEX_STARK_PRIVATE_KEY", ""),
            )
            contract_name = f"{symbol.upper()}{quote.upper()}"
            contract_id, _, _ = await edgex_client.get_edgex_contract_details(edgex, contract_name)
            rate = await edgex_client.get_edgex_funding_rate(edgex, contract_id)
            return rate
        except Exception as exc:
            logger.error("Error fetching EdgeX funding for %s: %s", symbol, exc)
            return None
        finally:
            if edgex:
                await edgex.close()

    async def fetch_lighter_rate() -> Optional[float]:
        api_client = None
        try:
            # Use global semaphore to limit concurrent Lighter API calls
            async with LIGHTER_API_SEMAPHORE:
                api_client = lighter.ApiClient(configuration=lighter.Configuration(host=env["LIGHTER_BASE_URL"]))
                order_api = lighter.OrderApi(api_client)
                market_id, _, _ = await lighter_client.get_lighter_market_details(order_api, symbol)
                funding_api = lighter.FundingApi(api_client)

                # Wrap the actual API call with retry logic
                async def _fetch():
                    return await lighter_client.get_lighter_funding_rate(funding_api, market_id)

                return await retry_with_backoff(_fetch, max_retries=3, initial_delay=2.0)
        except RateLimitError as exc:
            logger.error("Lighter rate limit exceeded for %s after retries: %s", symbol, exc)
            return None
        except Exception as exc:
            logger.error("Error fetching Lighter funding for %s: %s", symbol, exc)
            return None
        finally:
            if api_client:
                await api_client.close()

    # Fetch funding rates, volume, and spread
    # EdgeX calls can run concurrently (no rate limits)
    # Lighter calls are serialized via global LIGHTER_API_SEMAPHORE
    if check_volume:
        edgex_rate_decimal, lighter_rate_decimal, volume_data, spread_pct = await asyncio.gather(
            fetch_edgex_rate(),
            fetch_lighter_rate(),
            fetch_symbol_volume(symbol, quote, env),
            fetch_symbol_spread(symbol, quote, env)
        )
        edgex_volume, lighter_volume, total_volume = volume_data
    else:
        edgex_rate_decimal, lighter_rate_decimal, spread_pct = await asyncio.gather(
            fetch_edgex_rate(),
            fetch_lighter_rate(),
            fetch_symbol_spread(symbol, quote, env)
        )
        edgex_volume, lighter_volume, total_volume = None, None, None

    if edgex_rate_decimal is None or lighter_rate_decimal is None:
        missing = []
        if edgex_rate_decimal is None:
            missing.append("EdgeX")
        if lighter_rate_decimal is None:
            missing.append("Lighter")
        return {
            "symbol": symbol,
            "available": False,
            "missing_on": missing or ["Data unavailable"],
            "edgex_volume": edgex_volume,
            "lighter_volume": lighter_volume,
            "total_volume": total_volume,
            "spread_pct": spread_pct,
        }

    # Check volume data availability if volume check is enabled
    if check_volume:
        if total_volume is None:
            logger.info(f"{symbol}: Volume data unavailable (N/A)")
            return {
                "symbol": symbol,
                "available": False,
                "missing_on": ["Volume data unavailable"],
                "edgex_volume": edgex_volume,
                "lighter_volume": lighter_volume,
                "total_volume": total_volume,
                "spread_pct": spread_pct,
            }

        # Check volume threshold
        if total_volume < min_volume_usd:
            logger.info(f"{symbol}: Volume ${total_volume/1e6:.1f}M below threshold ${min_volume_usd/1e6:.0f}M")
            return {
                "symbol": symbol,
                "available": False,
                "missing_on": [f"Volume too low: ${total_volume/1e6:.1f}M < ${min_volume_usd/1e6:.0f}M"],
                "edgex_volume": edgex_volume,
                "lighter_volume": lighter_volume,
                "total_volume": total_volume,
                "spread_pct": spread_pct,
            }

        # Check spread threshold
        if spread_pct is not None and spread_pct > max_spread_pct:
            logger.info(f"{symbol}: Spread {spread_pct:.3f}% exceeds {max_spread_pct:.2f}% threshold")
            return {
                "symbol": symbol,
                "available": False,
                "missing_on": [f"Spread too wide: {spread_pct:.3f}% > {max_spread_pct:.2f}%"],
                "edgex_volume": edgex_volume,
                "lighter_volume": lighter_volume,
                "total_volume": total_volume,
                "spread_pct": spread_pct,
            }

    edgex_apr = _calculate_apr(edgex_rate_decimal, 6)
    lighter_apr = _calculate_apr(lighter_rate_decimal, 3)

    long_edgex_short_lighter = lighter_apr - edgex_apr
    long_lighter_short_edgex = edgex_apr - lighter_apr

    if long_edgex_short_lighter >= long_lighter_short_edgex:
        long_exch = "EdgeX"
        short_exch = "Lighter"
        net_apr = long_edgex_short_lighter
    else:
        long_exch = "Lighter"
        short_exch = "EdgeX"
        net_apr = long_lighter_short_edgex

    return {
        "symbol": symbol,
        "available": True,
        "edgex_rate": edgex_rate_decimal * 100 if edgex_rate_decimal is not None else None,
        "edgex_apr": edgex_apr,
        "lighter_rate": lighter_rate_decimal * 100 if lighter_rate_decimal is not None else None,
        "lighter_apr": lighter_apr,
        "long_exch": long_exch,
        "short_exch": short_exch,
        "net_apr": net_apr,
        "edgex_volume": edgex_volume,
        "lighter_volume": lighter_volume,
        "total_volume": total_volume,
        "spread_pct": spread_pct,
    }


async def open_delta_neutral_position(
    env: dict,
    symbol: str,
    quote: str,
    long_exchange: str,
    short_exchange: str,
    leverage: float,
    notional_quote: float,
    cross_ticks: int = 100,
) -> Dict[str, object]:
    """
    Open a delta-neutral position across Lighter and EdgeX using aggressive limit orders.
    Returns metadata describing the trade (contract IDs, ticks, pricing).
    """
    # Build clients
    api_client = lighter.ApiClient(configuration=lighter.Configuration(host=env["LIGHTER_BASE_URL"]))
    order_api = lighter.OrderApi(api_client)
    signer = lighter.SignerClient(
        url=env["LIGHTER_BASE_URL"],
        private_key=env["API_KEY_PRIVATE_KEY"],
        account_index=env["ACCOUNT_INDEX"],
        api_key_index=env["API_KEY_INDEX"],
    )
    err = signer.check_client()
    if err:
        await api_client.close()
        raise RuntimeError(f"Lighter check_client error: {err}")

    edgex = EdgeXClient(
        base_url=env["EDGEX_BASE_URL"],
        account_id=int(env["EDGEX_ACCOUNT_ID"]) if env.get("EDGEX_ACCOUNT_ID") else 0,
        stark_private_key=env.get("EDGEX_STARK_PRIVATE_KEY", ""),
    )

    try:
        l_market_id, l_price_tick, l_amount_tick = await lighter_client.get_lighter_market_details(order_api, symbol)
        lighter_bid, lighter_ask = await lighter_client.get_lighter_best_bid_ask(order_api, symbol, l_market_id)

        contract_name = f"{symbol.upper()}{quote.upper()}"
        e_contract_id, e_tick_price, e_step_size = await edgex_client.get_edgex_contract_details(edgex, contract_name)
        edgex_bid, edgex_ask = await edgex_client.get_edgex_best_bid_ask(edgex, e_contract_id)

        if not any([lighter_bid, lighter_ask, edgex_bid, edgex_ask]):
            raise RuntimeError("Could not fetch quotes from either venue.")
        if not (lighter_bid or lighter_ask):
            raise RuntimeError(
                f"Could not fetch any prices from Lighter for {symbol}. "
                "The order book may be empty or the market inactive."
            )
        if not (edgex_bid or edgex_ask):
            raise RuntimeError(f"Could not fetch any prices from EdgeX for {contract_name}")
    except Exception:
        await signer.close()
        await api_client.close()
        await edgex.close()
        raise

    # Configure leverage
    await configure_leverage(leverage, env, edgex, signer, e_contract_id, l_market_id, verify=True)

    avg_mid = get_avg_mid(lighter_bid, lighter_ask, edgex_bid, edgex_ask)
    size_base = compute_base_size_from_quote(avg_mid, float(notional_quote))

    coarser_tick = max(l_amount_tick, e_step_size)
    size_base = _floor_to_tick(size_base, coarser_tick)

    lighter_rounded = _round_to_tick(size_base, l_amount_tick)
    edgex_rounded = _round_to_tick(size_base, e_step_size)
    if abs(lighter_rounded - edgex_rounded) > min(l_amount_tick, e_step_size):
        size_base = _floor_to_tick(size_base, coarser_tick)
        logger.warning("Adjusted size to %s to ensure same size on both exchanges", size_base)

    if size_base <= 0:
        await signer.close()
        await api_client.close()
        await edgex.close()
        raise RuntimeError("Computed size rounds to zero. Increase notional.")

    metadata = await edgex.get_metadata()
    contracts = metadata.get("data", {}).get("contractList", [])
    edgex_min_size = None
    for contract in contracts:
        if contract.get("contractId") == e_contract_id:
            edgex_min_size = float(contract.get("minOrderSize", "0"))
            break

    avg_mid = get_avg_mid(lighter_bid, lighter_ask, edgex_bid, edgex_ask)
    min_errors: List[str] = []
    if edgex_min_size and size_base < edgex_min_size:
        min_usd = edgex_min_size * avg_mid
        min_errors.append(f"EdgeX minimum: {edgex_min_size} {symbol} (${min_usd:.2f} USD)")

    lighter_min_size = l_amount_tick * 10
    if size_base < lighter_min_size:
        lighter_min_usd = lighter_min_size * avg_mid
        min_errors.append(f"Lighter estimated minimum: {lighter_min_size} {symbol} (${lighter_min_usd:.2f} USD)")

    if min_errors:
        await signer.close()
        await api_client.close()
        await edgex.close()
        raise RuntimeError(
            "Order size too small. Requirements:\n" + "\n".join(f"  - {err}" for err in min_errors)
        )

    long_leg = long_exchange.lower()
    short_leg = short_exchange.lower()
    if long_leg == short_leg:
        await signer.close()
        await api_client.close()
        await edgex.close()
        raise RuntimeError("Long and short exchanges cannot be identical.")

    tasks = []
    leg_names: List[str] = []

    if long_leg == "lighter":
        ref_price = lighter_ask if lighter_ask else lighter_bid
        if ref_price is None:
            raise RuntimeError("Lighter: No reference price available for long leg.")
        tasks.append(
            lighter_client.lighter_place_aggressive_order(
                signer,
                l_market_id,
                l_price_tick,
                l_amount_tick,
                "buy",
                size_base,
                ref_price,
                cross_ticks=cross_ticks,
            )
        )
        leg_names.append("Lighter (LONG)")
    elif long_leg == "edgex":
        ref_price = edgex_ask if edgex_ask else edgex_bid
        if ref_price is None:
            raise RuntimeError("EdgeX: No reference price available for long leg.")
        tasks.append(
            edgex_client.place_aggressive_order(
                edgex,
                e_contract_id,
                e_tick_price,
                e_step_size,
                "buy",
                size_base,
                ref_price,
                cross_ticks=cross_ticks,
            )
        )
        leg_names.append("EdgeX (LONG)")
    else:
        raise RuntimeError(f"Unsupported long exchange: {long_exchange}")

    if short_leg == "lighter":
        ref_price = lighter_bid if lighter_bid else lighter_ask
        if ref_price is None:
            raise RuntimeError("Lighter: No reference price available for short leg.")
        tasks.append(
            lighter_client.lighter_place_aggressive_order(
                signer,
                l_market_id,
                l_price_tick,
                l_amount_tick,
                "sell",
                size_base,
                ref_price,
                cross_ticks=cross_ticks,
            )
        )
        leg_names.append("Lighter (SHORT)")
    elif short_leg == "edgex":
        ref_price = edgex_bid if edgex_bid else edgex_ask
        if ref_price is None:
            raise RuntimeError("EdgeX: No reference price available for short leg.")
        tasks.append(
            edgex_client.place_aggressive_order(
                edgex,
                e_contract_id,
                e_tick_price,
                e_step_size,
                "sell",
                size_base,
                ref_price,
                cross_ticks=cross_ticks,
            )
        )
        leg_names.append("EdgeX (SHORT)")
    else:
        await signer.close()
        await api_client.close()
        await edgex.close()
        raise RuntimeError(f"Unsupported short exchange: {short_exchange}")

    results = await asyncio.gather(*tasks, return_exceptions=True)
    errors = [(idx, res) for idx, res in enumerate(results) if isinstance(res, Exception)]

    if errors:
        print("\n❌ ERROR: One or more open orders failed!")
        for idx, err_obj in errors:
            print(f"   - {leg_names[idx]}: {err_obj}")
        successful = [leg_names[i] for i in range(len(results)) if not isinstance(results[i], Exception)]
        if successful:
            print("\n⚠️  CRITICAL: Partial fill detected!")
            print(f"   Successfully opened on: {', '.join(successful)}")
        await signer.close()
        await api_client.close()
        await edgex.close()
        raise RuntimeError("Delta-neutral order placement failed on at least one exchange.")

    print("✓ Both orders placed successfully")
    logger.info("Opened hedge: size_base=%s %s. Legs placed concurrently.", size_base, symbol)

    await asyncio.sleep(2)  # allow exchanges time to process

    edgex_size = await edgex_client.get_edgex_open_size(edgex, e_contract_id)
    print("\nVerifying positions...")
    print(f"  EdgeX position:  {edgex_size:+.6f} {symbol}")
    print("  Lighter position: (verification via API not available)")
    print(
        f"\n✓ Hedge opened successfully!\n"
        f"  Total exposure: {size_base:.6f} {symbol} on each exchange\n"
        f"  Delta-neutral: LONG {long_exchange.capitalize()}, SHORT {short_exchange.capitalize()}\n"
    )

    await signer.close()
    await api_client.close()
    await edgex.close()

    return {
        "edgex_contract_id": e_contract_id,
        "lighter_market_id": l_market_id,
        "lighter_price_tick": l_price_tick,
        "lighter_amount_tick": l_amount_tick,
        "edgex_tick_price": e_tick_price,
        "edgex_step_size": e_step_size,
        "edgex_bid": edgex_bid,
        "edgex_ask": edgex_ask,
        "lighter_bid": lighter_bid,
        "lighter_ask": lighter_ask,
        "size_base": size_base,
        "avg_mid": avg_mid,
    }


async def close_delta_neutral_position(
    env: dict,
    symbol: str,
    quote: str,
    cross_ticks: int = 100,
) -> None:
    """Close positions on both exchanges for the specified symbol."""
    api_client = lighter.ApiClient(configuration=lighter.Configuration(host=env["LIGHTER_BASE_URL"]))
    order_api = lighter.OrderApi(api_client)
    account_api = lighter.AccountApi(api_client)
    signer = lighter.SignerClient(
        url=env["LIGHTER_BASE_URL"],
        private_key=env["API_KEY_PRIVATE_KEY"],
        account_index=env["ACCOUNT_INDEX"],
        api_key_index=env["API_KEY_INDEX"],
    )
    err = signer.check_client()
    if err:
        await api_client.close()
        raise RuntimeError(f"Lighter check_client error: {err}")

    edgex = EdgeXClient(
        base_url=env["EDGEX_BASE_URL"],
        account_id=int(env["EDGEX_ACCOUNT_ID"]) if env.get("EDGEX_ACCOUNT_ID") else 0,
        stark_private_key=env.get("EDGEX_STARK_PRIVATE_KEY", ""),
    )

    l_market_id, l_price_tick, l_amount_tick = await lighter_client.get_lighter_market_details(order_api, symbol)
    lighter_bid, lighter_ask = await lighter_client.get_lighter_best_bid_ask(order_api, symbol, l_market_id)

    contract_name = f"{symbol.upper()}{quote.upper()}"
    e_contract_id, e_tick_price, e_step_size = await edgex_client.get_edgex_contract_details(edgex, contract_name)

    print(f"\n┌{'─' * 66}┐")
    print(f"│{'Closing Delta-Neutral Hedge':^66}│")
    print(f"├{'─' * 66}┤")
    print(f"│  Symbol: {symbol}/{quote:<4}                                              │")
    print(f"└{'─' * 66}┘\n")

    print("Checking current positions...")
    edgex_size = await edgex_client.get_edgex_open_size(edgex, e_contract_id)
    print(f"  EdgeX position:  {edgex_size:+.6f} {symbol}")

    lighter_size = await lighter_client.get_lighter_open_size(account_api, env["ACCOUNT_INDEX"], l_market_id)
    print(f"  Lighter position: {lighter_size:+.6f} {symbol}")

    print("\nClosing positions on both exchanges...")
    tasks = []

    if abs(lighter_size) > l_amount_tick:
        lighter_close_side = "sell" if lighter_size > 0 else "buy"
        ref_price = lighter_bid if lighter_close_side == "sell" else lighter_ask
        if ref_price:
            tasks.append(
                lighter_client.lighter_close_position(
                    signer,
                    l_market_id,
                    l_price_tick,
                    l_amount_tick,
                    lighter_close_side,
                    abs(lighter_size),
                    ref_price,
                    cross_ticks=cross_ticks,
                )
            )
        else:
            logger.warning("Lighter: No reference price available to close position.")
            print("  Lighter: No reference price available, cannot send close order.")
    else:
        print("  Lighter: Position already flat or below minimum tick.")

    tasks.append(
        edgex_client.close_position(
            edgex,
            e_contract_id,
            e_tick_price,
            e_step_size,
            cross_ticks=cross_ticks,
        )
    )

    results = await asyncio.gather(*tasks, return_exceptions=True)
    errors = [res for res in results if isinstance(res, Exception)]
    if errors:
        print("\n❌ ERROR: One or more close orders failed!")
        for err_obj in errors:
            print(f"   - {err_obj}")
        print("\n⚠️  WARNING: Please verify positions manually.")
        await signer.close()
        await api_client.close()
        await edgex.close()
        raise RuntimeError("Failed to close positions on one or more venues.")

    print("✓ Close orders sent to both exchanges")

    await asyncio.sleep(2)

    print("\nVerifying closure...")
    edgex_size_after = await edgex_client.get_edgex_open_size(edgex, e_contract_id)
    print(f"  EdgeX position:  {edgex_size_after:+.6f} {symbol}")
    lighter_size_after = await lighter_client.get_lighter_open_size(account_api, env["ACCOUNT_INDEX"], l_market_id)
    print(f"  Lighter position: {lighter_size_after:+.6f} {symbol}")

    edgex_closed = abs(edgex_size_after) < e_step_size
    lighter_closed = abs(lighter_size_after) < l_amount_tick

    if edgex_closed and lighter_closed:
        print("\n✓ Hedge closed successfully on both exchanges!")
    else:
        print("\n⚠️  WARNING: One or more positions not fully closed.")
        if not edgex_closed:
            print(f"  EdgeX position remaining: {edgex_size_after:+.6f} {symbol}")
        if not lighter_closed:
            print(f"  Lighter position remaining: {lighter_size_after:+.6f} {symbol}")
        print("  Please check both exchanges manually.\n")

    await signer.close()
    await api_client.close()
    await edgex.close()


class StateManager:
    """Manages bot state persistence and recovery."""

    def __init__(self, state_file: str = "bot_state.json"):
        self.state_file = state_file
        self.state = {
            "version": "1.0",
            "state": BotState.IDLE,
            "current_cycle": 0,  # Current cycle number (increments when position opens)
            "current_position": None,
            "capital_status": {
                "edgex_total": 0.0,
                "edgex_available": 0.0,
                "lighter_total": 0.0,
                "lighter_available": 0.0,
                "total_capital": 0.0,
                "total_available": 0.0,
                "max_position_notional": 0.0,
                "limiting_exchange": None,
                "last_updated": None,
                "initial_total_capital": None  # Set once on first capital refresh, never changes
            },
            "completed_cycles": [],
            "cumulative_stats": {
                "total_cycles": 0,
                "successful_cycles": 0,
                "failed_cycles": 0,
                "total_realized_pnl": 0.0,
                "total_trading_pnl": 0.0,
                "total_funding_pnl": 0.0,
                "total_fees_paid": 0.0,
                "best_cycle_pnl": 0.0,
                "worst_cycle_pnl": 0.0,
                "total_volume_traded": 0.0,
                "total_hold_time_hours": 0.0,
                "by_symbol": {},
                "last_error": None,
                "last_error_at": None
            },
            "config": None,
            "last_updated": utc_now_iso()
        }

    def load(self) -> bool:
        """Load state from file. Returns True if loaded successfully."""
        if not os.path.exists(self.state_file):
            logger.info(f"No state file found at {self.state_file}, starting fresh")
            return False

        try:
            with open(self.state_file, 'r') as f:
                content = f.read().strip()

            # Handle empty file
            if not content:
                logger.info(f"State file {self.state_file} is empty, starting fresh")
                return False

            loaded_state = json.loads(content)

            # Merge with default state to handle new fields (backward compatibility)
            self.state.update(loaded_state)

            # Ensure capital_status exists (for older state files)
            if "capital_status" not in self.state:
                self.state["capital_status"] = {
                    "edgex_total": 0.0,
                    "edgex_available": 0.0,
                    "lighter_total": 0.0,
                    "lighter_available": 0.0,
                    "total_capital": 0.0,
                    "total_available": 0.0,
                    "max_position_notional": 0.0,
                    "limiting_exchange": None,
                    "last_updated": None,
                    "initial_total_capital": None
                }

            # Ensure initial_total_capital field exists (for older state files)
            if "initial_total_capital" not in self.state["capital_status"]:
                self.state["capital_status"]["initial_total_capital"] = None

            logger.info(f"Loaded state from {self.state_file}")
            logger.info(f"Current state: {self.state['state']}")
            return True
        except json.JSONDecodeError as e:
            logger.warning(f"State file {self.state_file} is corrupted or invalid JSON: {e}")
            logger.info("Starting fresh with new state")
            return False
        except Exception as e:
            logger.warning(f"Could not load state file: {e}")
            logger.info("Starting fresh with new state")
            return False

    def save(self):
        """Save current state to file with retry logic for Windows Docker volumes."""
        import time
        self.state["last_updated"] = utc_now_iso()

        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Write to temp file first, then atomic rename
                temp_file = self.state_file + ".tmp"
                with open(temp_file, 'w') as f:
                    json.dump(self.state, f, indent=2)
                os.replace(temp_file, self.state_file)
                logger.debug(f"Saved state to {self.state_file}")
                return  # Success
            except OSError as e:
                if e.errno == 16 and attempt < max_retries - 1:  # Device or resource busy
                    time.sleep(0.1 * (attempt + 1))  # Exponential backoff
                    continue
                elif attempt == max_retries - 1:
                    logger.debug(f"Failed to save state after {max_retries} attempts: {e}")
                else:
                    logger.error(f"Failed to save state: {e}")
                    break
            except Exception as e:
                logger.error(f"Failed to save state: {e}")
                break

    def set_state(self, new_state: str):
        """Update bot state."""
        logger.info(f"State transition: {self.state['state']} → {new_state}")
        self.state["state"] = new_state
        self.save()

    def get_state(self) -> str:
        """Get current bot state."""
        return self.state["state"]

    def set_config(self, config: BotConfig):
        """Set bot configuration."""
        self.state["config"] = asdict(config)
        self.save()

    def get_config(self) -> Optional[BotConfig]:
        """Get bot configuration."""
        if self.state["config"]:
            return BotConfig(**self.state["config"])
        return None


# ==================== Balance & Position Helpers ====================

async def get_edgex_balance(env: dict) -> Tuple[float, float]:
    """Get EdgeX total and available USD balance."""
    edgex = None
    try:
        edgex = EdgeXClient(
            base_url=env["EDGEX_BASE_URL"],
            account_id=int(env["EDGEX_ACCOUNT_ID"]),
            stark_private_key=env["EDGEX_STARK_PRIVATE_KEY"],
        )
        total, available = await edgex_client.get_edgex_balance(edgex)
        logger.info("EdgeX balance: total=%s, available=%s", total, available)
        return total, available
    except Exception as exc:
        logger.error("Error fetching EdgeX balance: %s", exc, exc_info=True)
        raise BalanceFetchError(f"EdgeX balance fetch failed: {exc}") from exc
    finally:
        if edgex:
            await edgex.close()


async def get_lighter_balance(env: dict) -> Tuple[float, float]:
    """Get Lighter total and available USD balance via WebSocket."""
    try:
        account_index = int(env.get("ACCOUNT_INDEX", env.get("LIGHTER_ACCOUNT_INDEX", "0")))
        ws_url = env["LIGHTER_WS_URL"]
        available, portfolio_value = await lighter_client.get_lighter_balance(ws_url, account_index, timeout=10.0)
        if available is None or portfolio_value is None:
            raise BalanceFetchError("Lighter WebSocket returned None values")
        logger.info("Lighter balance: total=%s, available=%s", portfolio_value, available)
        return portfolio_value, available
    except BalanceFetchError:
        raise
    except Exception as exc:
        logger.error("Error fetching Lighter balance: %s: %s", type(exc).__name__, exc, exc_info=True)
        raise BalanceFetchError(f"Lighter balance fetch failed: {type(exc).__name__}: {exc}") from exc


async def get_position_prices(env: dict, symbol: str, quote: str,
                              long_exchange: str, short_exchange: str,
                              e_contract_id: str, l_market_id: int) -> Dict:
    """Get current position prices from both exchanges."""
    prices = {
        "edgex_mark": None,
        "lighter_mark": None,
        "timestamp": utc_now_iso()
    }

    async def fetch_edgex_price():
        edgex_client = None
        try:
            edgex_client = EdgeXClient(
                base_url=env["EDGEX_BASE_URL"],
                account_id=int(env["EDGEX_ACCOUNT_ID"]),
                stark_private_key=env["EDGEX_STARK_PRIVATE_KEY"]
            )
            quote_resp = await edgex_client.quote.get_24_hour_quote(e_contract_id)
            if quote_resp.get("code") == "SUCCESS" and quote_resp.get("data"):
                quote_data = quote_resp["data"][0]
                prices["edgex_mark"] = float(quote_data.get("lastPrice", "0") or "0")
        except Exception as e:
            logger.error(f"Error fetching EdgeX price: {e}")
        finally:
            if edgex_client:
                try:
                    await edgex_client.close()
                except Exception:
                    pass

    async def fetch_lighter_price():
        lighter_api_client = None
        try:
            lighter_api_client = lighter.ApiClient(configuration=lighter.Configuration(host=env["LIGHTER_BASE_URL"]))
            order_api = lighter.OrderApi(lighter_api_client)
            bid, ask = await lighter_client.get_lighter_best_bid_ask(order_api, symbol, int(l_market_id), timeout=5.0)

            if bid is not None and ask is not None:
                prices["lighter_mark"] = (float(bid) + float(ask)) / 2
            elif bid is not None or ask is not None:
                mid = float(bid or ask)
                prices["lighter_mark"] = mid
        except Exception as e:
            logger.error(f"Error fetching Lighter price: {e}")
        finally:
            if lighter_api_client:
                try:
                    await lighter_api_client.close()
                except Exception:
                    pass

    await asyncio.gather(fetch_edgex_price(), fetch_lighter_price())

    return prices


async def get_position_pnl(env: dict, symbol: str, quote: str,
                          long_exchange: str, short_exchange: str,
                          e_contract_id: str, l_market_id: int) -> Dict:
    """Get unrealized PnL from both exchanges."""
    pnl_data = {
        "edgex_unrealized_pnl": 0.0,
        "lighter_unrealized_pnl": 0.0,
        "total_unrealized_pnl": 0.0,
        "timestamp": utc_now_iso()
    }

    # EdgeX PnL - calculate manually to mirror CLI behavior
    edgex = None
    try:
        edgex = EdgeXClient(
            base_url=env["EDGEX_BASE_URL"],
            account_id=int(env["EDGEX_ACCOUNT_ID"]),
            stark_private_key=env["EDGEX_STARK_PRIVATE_KEY"]
        )
        positions_resp = await edgex.get_account_positions()
        positions = positions_resp.get("data", {}).get("positionList", [])

        for pos in positions:
            if pos.get("contractId") == e_contract_id:
                # Get current market price for EdgeX using quote API
                quote_resp = await edgex.quote.get_24_hour_quote(e_contract_id)
                data_list = quote_resp.get("data", [])

                current_price = 0.0
                if isinstance(data_list, list) and data_list:
                    d = data_list[0]
                    # Prefer last price, fall back to mark price, then mid of bid/ask
                    if d.get("lastPrice"):
                        current_price = float(d["lastPrice"])
                    elif d.get("markPrice"):
                        current_price = float(d["markPrice"])
                    elif d.get("bestBid") and d.get("bestAsk"):
                        current_price = (float(d["bestBid"]) + float(d["bestAsk"])) / 2.0

                # Calculate PnL manually to avoid relying on exchange-side fields
                size = float(pos.get("openSize", "0"))
                side = pos.get("side") or pos.get("positionSide")
                if side and str(side).lower().startswith("short"):
                    size = -abs(size)

                open_value = float(pos.get("openValue", "0"))
                if abs(open_value) > 0 and size != 0 and current_price > 0:
                    current_value = current_price * size
                    upnl = current_value - open_value
                    pnl_data["edgex_unrealized_pnl"] = upnl
                break
    except Exception as e:
        logger.error(f"Error fetching EdgeX PnL: {e}")
    finally:
        if edgex:
            try:
                await edgex.close()
            except:
                pass

    # Lighter PnL
    lighter_api_client = None
    try:
        account_index = int(env.get("ACCOUNT_INDEX", env.get("LIGHTER_ACCOUNT_INDEX", "0")))
        lighter_api_client = lighter.ApiClient(configuration=lighter.Configuration(host=env["LIGHTER_BASE_URL"]))
        account_api = lighter.AccountApi(lighter_api_client)
        account_details = await account_api.account(by="index", value=str(account_index))

        if account_details and account_details.accounts:
            acc = account_details.accounts[0]
            if acc.positions:
                for pos in acc.positions:
                    if pos.market_id == int(l_market_id):
                        pnl_data["lighter_unrealized_pnl"] = float(pos.unrealized_pnl or "0")
                        break
    except Exception as e:
        logger.error(f"Error fetching Lighter PnL: {e}")
    finally:
        if lighter_api_client:
            try:
                await lighter_api_client.close()
            except:
                pass

    pnl_data["total_unrealized_pnl"] = pnl_data["edgex_unrealized_pnl"] + pnl_data["lighter_unrealized_pnl"]
    return pnl_data


async def build_recovered_position_state(env: dict, config: BotConfig, symbol: str,
                                         edgex_size: float, lighter_size: float,
                                         edgex_contract_id: str, lighter_market_id: int) -> dict:
    """Construct a full current_position payload from live exchange balances."""

    edgex_side = "long" if edgex_size > 0 else "short"
    lighter_side = "long" if lighter_size > 0 else "short"
    long_exchange = "edgex" if edgex_side == "long" else "lighter"
    short_exchange = "lighter" if long_exchange == "edgex" else "edgex"

    edgex_balance_task = asyncio.create_task(get_edgex_balance(env))
    lighter_balance_task = asyncio.create_task(get_lighter_balance(env))
    prices_task = asyncio.create_task(
        get_position_prices(
            env,
            symbol,
            config.quote,
            long_exchange,
            short_exchange,
            edgex_contract_id,
            lighter_market_id
        )
    )
    funding_task = asyncio.create_task(
        compute_expected_funding(env, config, symbol, long_exchange)
    )

    try:
        edgex_balance_before, edgex_available_before = await edgex_balance_task
    except BalanceFetchError:
        edgex_balance_before, edgex_available_before = None, None

    try:
        lighter_balance_before, lighter_available_before = await lighter_balance_task
    except BalanceFetchError:
        lighter_balance_before, lighter_available_before = None, None

    try:
        prices = await prices_task
    except Exception as e:
        logger.debug(f"Price lookup failed during recovery for {symbol}: {e}")
        prices = {"edgex_mark": None, "lighter_mark": None}

    expected_funding = await funding_task

    edgex_mark = prices.get("edgex_mark") or 0.0
    lighter_mark = prices.get("lighter_mark") or 0.0
    size_base = abs(edgex_size)

    if edgex_mark and lighter_mark:
        actual_notional = size_base * (edgex_mark + lighter_mark) / 2
    elif edgex_mark:
        actual_notional = size_base * edgex_mark
    elif lighter_mark:
        actual_notional = size_base * lighter_mark
    else:
        actual_notional = size_base

    opened_at = utc_now()
    target_close_at = opened_at + timedelta(hours=config.hold_duration_hours)

    return {
        "symbol": symbol,
        "quote": config.quote,
        "long_exchange": long_exchange,
        "short_exchange": short_exchange,
        "leverage": config.leverage,
        "opened_at": to_iso_z(opened_at),
        "target_close_at": to_iso_z(target_close_at),
        "position_sizing": {
            "configured_notional": config.notional_per_position,
            "actual_notional": actual_notional,
            "max_edgex_notional": actual_notional,
            "max_lighter_notional": actual_notional,
            "limiting_exchange": "unknown",
            "was_capital_limited": False,
            "edgex_available_at_open": edgex_available_before,
            "lighter_available_at_open": lighter_available_before
        },
        "entry": {
            "edgex_contract_id": edgex_contract_id,
            "lighter_market_id": str(lighter_market_id),
            "edgex_entry_price": edgex_mark if edgex_mark else None,
            "lighter_entry_price": lighter_mark if lighter_mark else None,
            "size_base": size_base,
            "edgex_size": edgex_size,
            "lighter_size": lighter_size,
            "edgex_balance_before": edgex_balance_before,
            "lighter_balance_before": lighter_balance_before,
            "edgex_side": edgex_side,
            "lighter_side": lighter_side,
            "timestamp": to_iso_z(opened_at)
        },
        "expected_funding": {
            "edgex_rate_per_period": expected_funding.get("edgex_rate_per_period"),
            "lighter_rate_per_period": expected_funding.get("lighter_rate_per_period"),
            "net_apr": expected_funding.get("net_apr"),
            "expected_duration_hours": expected_funding.get("expected_duration_hours", config.hold_duration_hours)
        },
        "recovered": True
    }


async def scan_symbols_for_positions(env: dict, config: BotConfig, symbols: List[str], concurrency: int = 5) -> List[dict]:
    """Scan multiple symbols in parallel for open positions on both exchanges."""

    if not symbols:
        return []

    unique_symbols = list(dict.fromkeys(symbols))
    concurrency = max(1, min(concurrency, len(unique_symbols)))

    edgex = None
    lighter_api_client = None

    try:
        edgex = EdgeXClient(
            base_url=env["EDGEX_BASE_URL"],
            account_id=int(env["EDGEX_ACCOUNT_ID"]),
            stark_private_key=env["EDGEX_STARK_PRIVATE_KEY"]
        )

        lighter_api_client = lighter.ApiClient(configuration=lighter.Configuration(host=env["LIGHTER_BASE_URL"]))
        order_api = lighter.OrderApi(lighter_api_client)
        account_api = lighter.AccountApi(lighter_api_client)
        account_index = int(env.get("ACCOUNT_INDEX", env.get("LIGHTER_ACCOUNT_INDEX", "0")))

        # Pre-fetch market metadata so we do not request it per symbol repeatedly
        market_lookup = {}
        try:
            markets_resp = await order_api.order_books()
            for ob in getattr(markets_resp, "order_books", []):
                market_lookup[ob.symbol.upper()] = (
                    ob.market_id,
                    10 ** -ob.supported_price_decimals,
                    10 ** -ob.supported_size_decimals,
                )
        except Exception as e:
            logger.debug(f"Could not pre-fetch Lighter market metadata: {e}")

        sem = asyncio.Semaphore(concurrency)

        async def scan(symbol: str) -> Optional[dict]:
            async with sem:
                try:
                    contract_name = f"{symbol.upper()}{config.quote.upper()}"
                    edgex_contract_id, _, _ = await edgex_client.get_edgex_contract_details(edgex, contract_name)

                    if symbol.upper() in market_lookup:
                        lighter_market_id, price_tick, amount_tick = market_lookup[symbol.upper()]
                    else:
                        lighter_market_id, price_tick, amount_tick = await lighter_client.get_lighter_market_details(order_api, symbol)

                    edgex_size, lighter_size = await asyncio.gather(
                        edgex_client.get_edgex_open_size(edgex, edgex_contract_id),
                        lighter_client.get_lighter_open_size(account_api, account_index, lighter_market_id)
                    )

                    if edgex_size != 0 or lighter_size != 0:
                        return {
                            "symbol": symbol,
                            "edgex_size": edgex_size,
                            "lighter_size": lighter_size,
                            "edgex_contract_id": edgex_contract_id,
                            "lighter_market_id": lighter_market_id,
                            "lighter_price_tick": price_tick,
                            "lighter_amount_tick": amount_tick,
                        }
                except Exception as e:
                    logger.debug(f"Could not check {symbol}: {e}")
                return None

        tasks = [asyncio.create_task(scan(symbol)) for symbol in unique_symbols]
        results = await asyncio.gather(*tasks)
        return [res for res in results if res]

    finally:
        if edgex:
            try:
                await edgex.close()
            except Exception:
                pass
        if lighter_api_client:
            try:
                await lighter_api_client.close()
            except Exception:
                pass


async def compute_expected_funding(env: dict, config: BotConfig, symbol: str, long_exchange: str) -> dict:
    """Compute expected funding metrics for a given symbol and hedge orientation."""

    edgex_rate = None
    lighter_rate = None
    net_apr = None

    try:
        # Disable volume checking for expected funding calculation (used in position recovery)
        funding_info = await fetch_symbol_funding(symbol, config.quote, env, check_volume=False)
    except Exception as e:
        logger.debug(f"Funding lookup failed for {symbol}: {e}")
    else:
        if funding_info.get("available"):
            edgex_rate = funding_info.get("edgex_rate")
            lighter_rate = funding_info.get("lighter_rate")
            edgex_apr = funding_info.get("edgex_apr")
            lighter_apr = funding_info.get("lighter_apr")

            if edgex_apr is not None and lighter_apr is not None:
                if (long_exchange or "").lower() == "edgex":
                    net_apr = lighter_apr - edgex_apr
                else:
                    net_apr = edgex_apr - lighter_apr

    return {
        "edgex_rate_per_period": edgex_rate,
        "lighter_rate_per_period": lighter_rate,
        "net_apr": net_apr,
        "expected_duration_hours": config.hold_duration_hours
    }


async def refresh_capital_status(state_mgr: StateManager, env: dict, config: BotConfig) -> None:
    """Refresh aggregated capital status and update state manager."""

    try:
        capital_info = await get_available_capital_and_max_position(env, config)
    except BalanceFetchError as e:
        logger.warning(f"Unable to refresh capital status: {e}")
    else:
        # Preserve initial_total_capital if already set
        existing_initial = state_mgr.state.get("capital_status", {}).get("initial_total_capital")

        # Don't convert 0.0 to None - keep the actual values
        state_mgr.state["capital_status"] = capital_info

        # Set initial_total_capital ONCE (on first successful refresh)
        if existing_initial is None and capital_info["total_capital"] > 0:
            state_mgr.state["capital_status"]["initial_total_capital"] = capital_info["total_capital"]
            logger.info(f"Initial total capital recorded: ${capital_info['total_capital']:.2f}")
        else:
            # Restore existing initial value (don't let it change)
            state_mgr.state["capital_status"]["initial_total_capital"] = existing_initial


def calculate_stop_loss_percent(leverage: float, maintenance_margin: float = 0.005,
                                buffer: float = 0.006, safety_multiplier: float = 0.7) -> float:
    """
    Calculate automatic stop-loss percentage based on leverage.

    Uses the formula from calculate_stoploss_by_leverage.py to determine
    the maximum safe stop-loss distance that keeps away from liquidation.

    Args:
        leverage: Leverage multiplier (e.g., 3 for 3x)
        maintenance_margin: Maintenance margin rate (default: 0.005 for 0.5%)
        buffer: Safety buffer from liquidation (default: 0.006 for 0.6%)
        safety_multiplier: Additional safety factor (default: 0.7 = use 70% of max stop)

    Returns:
        Stop-loss percentage (e.g., 19.32 for 19.32%)
    """
    # Calculate max stop for long position
    s_max_long = (1 - (1 - 1/leverage) / (1 - maintenance_margin)) - buffer
    s_max_long_safe = s_max_long * safety_multiplier

    # Calculate max stop for short position
    s_max_short = ((1 + 1/leverage) / (1 + maintenance_margin) - 1) - buffer
    s_max_short_safe = s_max_short * safety_multiplier

    # Use average of long and short
    avg_stop = (s_max_long_safe + s_max_short_safe) / 2

    return round(avg_stop * 100, 2)


def check_stop_loss(pnl_data: Dict, notional: float, leverage: float) -> Tuple[bool, str, float]:
    """
    Check if stop-loss condition is triggered.

    Stop-loss logic: If EITHER leg has negative PnL exceeding the threshold, trigger stop-loss.
    We check the WORST performing leg (most negative PnL).

    Args:
        pnl_data: Dictionary with edgex_unrealized_pnl and lighter_unrealized_pnl
        notional: Position notional size in USD
        leverage: Leverage used for the position (used to calculate stop-loss %)

    Returns:
        Tuple of (triggered: bool, reason: str, stop_loss_percent: float)
    """
    # Calculate stop-loss percent automatically based on leverage
    stop_loss_percent = calculate_stop_loss_percent(leverage)

    edgex_pnl = pnl_data["edgex_unrealized_pnl"]
    lighter_pnl = pnl_data["lighter_unrealized_pnl"]

    # Calculate stop-loss threshold in absolute USD
    stop_loss_threshold = -abs(notional * (stop_loss_percent / 100.0))

    # Find the worst performing leg (most negative)
    worst_pnl = min(edgex_pnl, lighter_pnl)
    worst_exchange = "EdgeX" if edgex_pnl < lighter_pnl else "Lighter"

    # Trigger if worst leg exceeds threshold
    if worst_pnl < stop_loss_threshold:
        worst_pnl_percent = (worst_pnl / notional) * 100 if notional > 0 else 0
        reason = (f"{worst_exchange} leg hit stop-loss: "
                 f"${worst_pnl:.2f} ({worst_pnl_percent:.2f}%) "
                 f"exceeds threshold of ${stop_loss_threshold:.2f} ({-stop_loss_percent:.2f}%)")
        return True, reason, stop_loss_percent

    return False, "", stop_loss_percent


async def get_available_capital_and_max_position(env: dict, config: BotConfig) -> Dict:
    """
    Get available capital on both exchanges and calculate maximum openable position.

    Returns:
        Dictionary with capital info and max position size
    """
    capital_info = {
        "edgex_total": 0.0,
        "edgex_available": 0.0,
        "lighter_total": 0.0,
        "lighter_available": 0.0,
        "total_capital": 0.0,
        "total_available": 0.0,
        "max_position_notional": 0.0,
        "limiting_exchange": None,
        "timestamp": utc_now_iso(),
        "last_updated": None
    }

    edgex_result, lighter_result = await asyncio.gather(
        get_edgex_balance(env),
        get_lighter_balance(env),
        return_exceptions=True
    )

    if isinstance(edgex_result, Exception):
        raise BalanceFetchError(f"EdgeX balance unavailable: {edgex_result}") from edgex_result
    if isinstance(lighter_result, Exception):
        raise BalanceFetchError(f"Lighter balance unavailable: {lighter_result}") from lighter_result

    edgex_total, edgex_avail = edgex_result
    lighter_total, lighter_avail = lighter_result

    capital_info["edgex_total"] = edgex_total
    capital_info["edgex_available"] = edgex_avail
    capital_info["lighter_total"] = lighter_total
    capital_info["lighter_available"] = lighter_avail

    # Calculate totals
    capital_info["total_capital"] = edgex_total + lighter_total
    capital_info["total_available"] = edgex_avail + lighter_avail

    # Calculate max position size (minimum of both exchanges, accounting for leverage)
    # Max position = min(available_on_each_exchange) * leverage
    # We take the minimum because we need to open positions on BOTH exchanges
    max_edgex_position = edgex_avail * config.leverage if edgex_avail > 0 else 0
    max_lighter_position = lighter_avail * config.leverage if lighter_avail > 0 else 0

    capital_info["max_position_notional"] = min(max_edgex_position, max_lighter_position)
    capital_info["limiting_exchange"] = "EdgeX" if max_edgex_position < max_lighter_position else "Lighter"
    capital_info["last_updated"] = capital_info["timestamp"]

    return capital_info


# ==================== Position Opening/Closing ====================

async def open_best_position(state_mgr: StateManager, env: dict, config: BotConfig):
    """Analyze funding rates and open the best delta-neutral position."""

    logger.info(f"Analyzing funding rates for {len(config.symbols_to_monitor)} symbols...")
    state_mgr.set_state(BotState.ANALYZING)

    # Fetch funding rates for all symbols with per-symbol timeouts and staggered delays
    async def fetch_with_timeout(symbol: str, delay: float = 0.0, timeout: float = 90.0):
        """Fetch funding for a symbol with individual timeout and optional delay."""
        # Add stagger delay to prevent rate limit hits
        if delay > 0:
            await asyncio.sleep(delay)

        try:
            return await asyncio.wait_for(
                fetch_symbol_funding(symbol, config.quote, env, check_volume=True, min_volume_usd=config.min_volume_usd, max_spread_pct=config.max_spread_pct),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.warning(f"{symbol}: Funding rate fetch timed out after {timeout}s")
            return {"symbol": symbol, "available": False, "error": "timeout"}
        except RateLimitError as e:
            logger.warning(f"{symbol}: Rate limit exceeded even after retries - {str(e)[:50]}")
            return {"symbol": symbol, "available": False, "error": "rate_limit"}
        except Exception as e:
            logger.warning(f"{symbol}: Error fetching funding - {str(e)[:50]}")
            return {"symbol": symbol, "available": False, "error": str(e)[:50]}

    # Add staggered delays: 1.0s between each request to avoid rate limits
    # Combined with global LIGHTER_API_SEMAPHORE (max 2 concurrent), this ensures
    # we never overwhelm Lighter's API with too many concurrent requests
    # Example: 12 symbols over 12 seconds with max 2 concurrent Lighter calls
    stagger_delay = 1.0
    results = await asyncio.gather(*[
        fetch_with_timeout(symbol, delay=idx * stagger_delay)
        for idx, symbol in enumerate(config.symbols_to_monitor)
    ], return_exceptions=True)

    # Separate available and unavailable symbols
    available = [r for r in results if isinstance(r, dict) and r.get("available", False)]
    unavailable = [r for r in results if isinstance(r, dict) and not r.get("available", False)]

    if not available:
        logger.error("No symbols available on both exchanges!")
        state_mgr.set_state(BotState.ERROR)
        state_mgr.state["cumulative_stats"]["last_error"] = "No symbols available"
        state_mgr.state["cumulative_stats"]["last_error_at"] = utc_now_iso()
        state_mgr.save()
        return False

    # Sort available by net APR descending
    available.sort(key=lambda x: x["net_apr"], reverse=True)

    # Display funding rates table (show both available and excluded symbols)
    display_funding_table(available, unavailable, current_symbol=None, limit=10)

    # Filter by minimum APR threshold
    candidates = [r for r in available if r["net_apr"] >= config.min_net_apr_threshold]

    if not candidates:
        best_available = available[0] if available else None
        if best_available:
            logger.warning(f"Best opportunity ({best_available['symbol']}: {best_available['net_apr']:.2f}%) below threshold ({config.min_net_apr_threshold}%)")
        logger.info("Waiting for better opportunities...")
        state_mgr.set_state(BotState.WAITING)
        return False

    # Get balances once before trying candidates
    logger.info("\nCapturing balance snapshots...")
    try:
        (edgex_total_before, edgex_avail_before), (lighter_total_before, lighter_avail_before) = await asyncio.gather(
            get_edgex_balance(env),
            get_lighter_balance(env)
        )
    except BalanceFetchError as e:
        logger.error(f"Failed to capture balance snapshots: {e}")
        state_mgr.set_state(BotState.WAITING)
        return False

    logger.info(f"EdgeX balance: ${edgex_total_before:.2f} (available: ${edgex_avail_before:.2f})")
    logger.info(f"Lighter balance: ${lighter_total_before:.2f} (available: ${lighter_avail_before:.2f})")

    # Try candidates in order of best APR until one succeeds
    for idx, candidate in enumerate(candidates):
        logger.info(f"\n{Colors.CYAN}{'═' * 70}")
        logger.info(f"TRYING OPPORTUNITY #{idx + 1}: {candidate['symbol']}")
        logger.info(f"{'═' * 70}{Colors.RESET}")
        logger.info(f"Net APR: {Colors.GREEN}{candidate['net_apr']:.2f}%{Colors.RESET}")
        logger.info(f"Setup: Long {candidate['long_exch']}, Short {candidate['short_exch']}")
        logger.info(f"EdgeX APR: {candidate['edgex_apr']:.2f}%, Lighter APR: {candidate['lighter_apr']:.2f}%")

        # Try to open this candidate
        result = await _try_open_position(state_mgr, env, config, candidate, edgex_total_before, edgex_avail_before, lighter_total_before, lighter_avail_before)
        if result:
            return True  # Success!

        # This candidate failed (insufficient capital or minimum size), try next
        if idx < len(candidates) - 1:
            logger.info(f"{Colors.YELLOW}Trying next candidate...{Colors.RESET}\n")

    # All candidates failed
    logger.error(f"{Colors.RED}All {len(candidates)} candidates failed to open. Insufficient capital or minimum size issues.{Colors.RESET}")
    state_mgr.set_state(BotState.WAITING)
    return False


async def _try_open_position(state_mgr: StateManager, env: dict, config: BotConfig, best: dict,
                             edgex_total_before: float, edgex_avail_before: float,
                             lighter_total_before: float, lighter_avail_before: float) -> bool:
    """Try to open a position for a specific symbol. Returns True if successful, False if should try next."""

    # Calculate maximum position size based on available capital with leverage
    # We need to use the MINIMUM of both exchanges to maintain delta-neutral
    max_edgex_notional = edgex_avail_before * config.leverage
    max_lighter_notional = lighter_avail_before * config.leverage
    max_available_notional = min(max_edgex_notional, max_lighter_notional)

    # Use the smaller of configured notional or max available (with 5% safety buffer)
    actual_notional = min(config.notional_per_position, max_available_notional * 0.95)

    # Check if we have enough capital to open a meaningful position
    if actual_notional < 10.0:  # Minimum $10 position
        logger.warning(f"{Colors.YELLOW}Insufficient capital to open position!{Colors.RESET}")
        logger.warning(f"Max available notional: ${max_available_notional:.2f}")
        logger.warning(f"EdgeX max: ${max_edgex_notional:.2f}, Lighter max: ${max_lighter_notional:.2f}")
        logger.info("Waiting for capital to recover...")
        # Don't set ERROR state - this is temporary, just wait and retry
        return False

    # Log position sizing info
    if actual_notional < config.notional_per_position:
        logger.warning(f"{Colors.YELLOW}Reducing position size due to available capital:{Colors.RESET}")
        logger.warning(f"  Configured: ${config.notional_per_position:.2f}")
        logger.warning(f"  Actual:     ${actual_notional:.2f}")
        limiting_exchange = "EdgeX" if max_edgex_notional < max_lighter_notional else "Lighter"
        logger.warning(f"  Limited by: {limiting_exchange}")
    else:
        logger.info(f"Position size: ${actual_notional:.2f}")

    logger.info(f"\n{Colors.CYAN}Opening delta-neutral position...{Colors.RESET}")
    state_mgr.set_state(BotState.OPENING)

    try:
        open_meta = await open_delta_neutral_position(
            env=env,
            symbol=best['symbol'],
            quote=config.quote,
            long_exchange=best['long_exch'].lower(),
            short_exchange=best['short_exch'].lower(),
            leverage=config.leverage,
            notional_quote=actual_notional,
            cross_ticks=100,
        )
    except Exception as exc:
        logger.error(f"{Colors.RED}Failed to open position: {exc}{Colors.RESET}")
        state_mgr.set_state(BotState.ANALYZING)
        return False

    edgex_contract_id = open_meta["edgex_contract_id"]
    lighter_market_id = open_meta["lighter_market_id"]

    edgex_mid = None
    if open_meta.get("edgex_bid") and open_meta.get("edgex_ask"):
        edgex_mid = (open_meta["edgex_bid"] + open_meta["edgex_ask"]) / 2
    elif open_meta.get("avg_mid"):
        edgex_mid = open_meta["avg_mid"]

    lighter_mid = None
    if open_meta.get("lighter_bid") and open_meta.get("lighter_ask"):
        lighter_mid = (open_meta["lighter_bid"] + open_meta["lighter_ask"]) / 2
    elif open_meta.get("avg_mid"):
        lighter_mid = open_meta["avg_mid"]

    edgex_size = 0.0
    edgex_post = EdgeXClient(
        base_url=env["EDGEX_BASE_URL"],
        account_id=int(env["EDGEX_ACCOUNT_ID"]),
        stark_private_key=env["EDGEX_STARK_PRIVATE_KEY"],
    )
    try:
        edgex_size = await edgex_client.get_edgex_open_size(edgex_post, edgex_contract_id)
    finally:
        await edgex_post.close()

    account_index = int(env.get("ACCOUNT_INDEX", env.get("LIGHTER_ACCOUNT_INDEX", "0")))
    lighter_post = lighter.ApiClient(configuration=lighter.Configuration(host=env["LIGHTER_BASE_URL"]))
    try:
        account_api = lighter.AccountApi(lighter_post)
        lighter_size = await lighter_client.get_lighter_open_size(account_api, account_index, int(lighter_market_id))
    finally:
        await lighter_post.close()

    logger.info(f"{Colors.GREEN}Position opened successfully!{Colors.RESET}")
    logger.info(f"EdgeX size: {edgex_size}")
    logger.info(f"Lighter size: {lighter_size}")

    opened_at = utc_now()
    target_close_at = opened_at + timedelta(hours=config.hold_duration_hours)

    state_mgr.state["current_cycle"] = state_mgr.state.get("current_cycle", 0) + 1

    state_mgr.state["current_position"] = {
        "symbol": best['symbol'],
        "quote": config.quote,
        "long_exchange": best['long_exch'].lower(),
        "short_exchange": best['short_exch'].lower(),
        "leverage": config.leverage,
        "opened_at": to_iso_z(opened_at),
        "target_close_at": to_iso_z(target_close_at),
        "position_sizing": {
            "configured_notional": config.notional_per_position,
            "actual_notional": actual_notional,
            "max_edgex_notional": max_edgex_notional,
            "max_lighter_notional": max_lighter_notional,
            "limiting_exchange": "EdgeX" if max_edgex_notional < max_lighter_notional else "Lighter",
            "was_capital_limited": actual_notional < config.notional_per_position,
            "edgex_available_at_open": edgex_avail_before,
            "lighter_available_at_open": lighter_avail_before,
        },
        "entry": {
            "edgex_contract_id": edgex_contract_id,
            "lighter_market_id": str(lighter_market_id),
            "edgex_entry_price": edgex_mid,
            "lighter_entry_price": lighter_mid,
            "size_base": open_meta["size_base"],
            "edgex_size": edgex_size,
            "lighter_size": lighter_size,
            "edgex_balance_before": edgex_total_before,
            "lighter_balance_before": lighter_total_before,
            "timestamp": to_iso_z(opened_at),
        },
        "expected_funding": {
            "edgex_rate_per_period": best['edgex_rate'],
            "lighter_rate_per_period": best['lighter_rate'],
            "net_apr": best['net_apr'],
            "expected_duration_hours": config.hold_duration_hours,
        },
        "current_pnl": {
            "edgex_unrealized_pnl": 0.0,
            "lighter_unrealized_pnl": 0.0,
            "total_unrealized_pnl": 0.0,
            "last_updated": to_iso_z(opened_at),
        },
    }

    state_mgr.set_state(BotState.HOLDING)
    logger.info(f"Now holding until {target_close_at.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    return True



async def close_current_position(state_mgr: StateManager, env: dict):
    """Close the current delta-neutral position and record PnL."""

    pos = state_mgr.state["current_position"]
    if not pos:
        logger.warning("No current position to close")
        return False

    logger.info(f"\n{Colors.CYAN}{'═' * 70}")
    logger.info(f"CLOSING POSITION: {pos['symbol']}")
    logger.info(f"{'═' * 70}{Colors.RESET}")

    state_mgr.set_state(BotState.CLOSING)

    try:
        # Get balances and prices before closing
        edgex_total_before_close, _ = await get_edgex_balance(env)
        lighter_total_before_close, _ = await get_lighter_balance(env)

        prices_before = await get_position_prices(
            env, pos['symbol'], pos['quote'],
            pos['long_exchange'], pos['short_exchange'],
            pos['entry']['edgex_contract_id'],
            pos['entry']['lighter_market_id']
        )

        # Get PnL immediately before closing for most accurate realized PnL
        pnl_before = await get_position_pnl(
            env, pos['symbol'], pos['quote'],
            pos['long_exchange'], pos['short_exchange'],
            pos['entry']['edgex_contract_id'],
            pos['entry']['lighter_market_id']
        )

        logger.info(f"Unrealized PnL before close:")
        logger.info(f"  EdgeX: ${pnl_before['edgex_unrealized_pnl']:.4f}")
        logger.info(f"  Lighter: ${pnl_before['lighter_unrealized_pnl']:.4f}")
        logger.info(f"  Total: ${pnl_before['total_unrealized_pnl']:.4f}")

        # Close position
        await close_delta_neutral_position(env, pos['symbol'], pos['quote'], cross_ticks=100)

        # Wait for settlement
        await asyncio.sleep(2)

        # Get balances after closing
        edgex_total_after, _ = await get_edgex_balance(env)
        lighter_total_after, _ = await get_lighter_balance(env)

        closed_at = utc_now()

        logger.info(f"{Colors.GREEN}Position closed successfully!{Colors.RESET}")

        # Calculate PnL breakdown
        # Use last unrealized PnL for EdgeX (more accurate than balance change due to totalEquity quirks)
        edgex_balance_change = pnl_before['edgex_unrealized_pnl']
        # Use balance change for Lighter (more reliable for realized PnL)
        lighter_balance_change = lighter_total_after - pos['entry']['lighter_balance_before']
        total_balance_change = edgex_balance_change + lighter_balance_change

        logger.info(f"\nRealized PnL:")
        logger.info(f"  EdgeX: ${edgex_balance_change:+.4f} (from last unrealized PnL)")
        logger.info(f"  Lighter: ${lighter_balance_change:+.4f} (from balance change)")
        logger.info(f"  Total: {Colors.GREEN if total_balance_change >= 0 else Colors.RED}${total_balance_change:+.4f}{Colors.RESET}")

        # Calculate hold duration
        opened_at = from_iso_z(pos['opened_at'])
        hold_duration = (closed_at - opened_at).total_seconds() / 3600

        # Record completed cycle
        cycle_record = {
            "cycle_number": state_mgr.state["cumulative_stats"]["total_cycles"] + 1,
            "symbol": pos['symbol'],
            "opened_at": pos['opened_at'],
            "closed_at": to_iso_z(closed_at),
            "duration_hours": hold_duration,

            "entry": {
                "long_exchange": pos['long_exchange'],
                "short_exchange": pos['short_exchange'],
                "edgex_price": pos['entry']['edgex_entry_price'],
                "lighter_price": pos['entry']['lighter_entry_price'],
                "size_base": pos['entry']['size_base'],
                "edgex_balance_before": pos['entry']['edgex_balance_before'],
                "lighter_balance_before": pos['entry']['lighter_balance_before']
            },

            "exit": {
                "edgex_price": prices_before['edgex_mark'],
                "lighter_price": prices_before['lighter_mark'],
                "edgex_balance_after": edgex_total_after,
                "lighter_balance_after": lighter_total_after,
                "timestamp": to_iso_z(closed_at)
            },

            "pnl_breakdown": {
                "edgex_balance_change": edgex_balance_change,
                "lighter_balance_change": lighter_balance_change,
                "total_realized_pnl": total_balance_change,
                "realized_pnl_percent": (total_balance_change / (pos['entry']['edgex_balance_before'] + pos['entry']['lighter_balance_before'])) * 100 if (pos['entry']['edgex_balance_before'] + pos['entry']['lighter_balance_before']) > 0 else 0
            },

            "funding_stats": {
                "expected_net_apr": pos['expected_funding']['net_apr'],
                "hold_duration_hours": hold_duration
            },

            "position_sizing": pos.get('position_sizing', {
                "configured_notional": 0,
                "actual_notional": 0,
                "was_capital_limited": False
            }),

            "stop_loss_triggered": pos.get('stop_loss_triggered', False),
            "stop_loss_reason": pos.get('stop_loss_reason', None)
        }

        # Update cumulative stats
        stats = state_mgr.state["cumulative_stats"]
        stats["total_cycles"] += 1
        stats["successful_cycles"] += 1
        stats["total_realized_pnl"] += total_balance_change
        stats["total_volume_traded"] += pos['entry']['size_base'] * 2  # Both legs
        stats["total_hold_time_hours"] += hold_duration

        # Update best/worst cycle PnL (check if first cycle or if value is better/worse)
        if stats["total_cycles"] == 1 or total_balance_change > stats["best_cycle_pnl"]:
            stats["best_cycle_pnl"] = total_balance_change
        if stats["total_cycles"] == 1 or total_balance_change < stats["worst_cycle_pnl"]:
            stats["worst_cycle_pnl"] = total_balance_change

        # Update by-symbol stats
        if pos['symbol'] not in stats["by_symbol"]:
            stats["by_symbol"][pos['symbol']] = {
                "cycles": 0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0
            }

        symbol_stats = stats["by_symbol"][pos['symbol']]
        symbol_stats["cycles"] += 1
        symbol_stats["total_pnl"] += total_balance_change
        symbol_stats["avg_pnl"] = symbol_stats["total_pnl"] / symbol_stats["cycles"]

        # Add to completed cycles
        state_mgr.state["completed_cycles"].append(cycle_record)

        # Keep only last 100 cycles to prevent file from growing too large
        if len(state_mgr.state["completed_cycles"]) > 100:
            state_mgr.state["completed_cycles"] = state_mgr.state["completed_cycles"][-100:]

        # Clear current position
        state_mgr.state["current_position"] = None
        state_mgr.save()

        # Display summary
        display_cycle_summary(cycle_record, stats)

        return True

    except (Exception, SystemExit) as e:
        logger.error(f"{Colors.RED}Failed to close position: {e}{Colors.RESET}", exc_info=True)
        state_mgr.set_state(BotState.ERROR)
        state_mgr.state["cumulative_stats"]["last_error"] = str(e)
        state_mgr.state["cumulative_stats"]["last_error_at"] = utc_now_iso()
        state_mgr.state["cumulative_stats"]["failed_cycles"] += 1
        state_mgr.save()
        return False


# ==================== Display Functions ====================

def display_funding_table(funding_data: List[dict], excluded_data: List[dict] = None, current_symbol: Optional[str] = None, limit: int = 10):
    """Display funding rates comparison table with volume information and excluded symbols."""
    if not funding_data and not excluded_data:
        logger.info(f"{Colors.GRAY}No funding data available{Colors.RESET}")
        return

    logger.info(f"\n{Colors.BOLD}📊 Funding Rates Overview{Colors.RESET}")
    logger.info(f"{Colors.CYAN}{'─' * 120}{Colors.RESET}")
    logger.info(f"{'Symbol':<10} {'EdgeX APR':>10} {'Lighter APR':>12} {'Net APR':>10} {'24h Volume':>14} {'Spread':>9} {'Long':>8} {'Status':<25}")
    logger.info(f"{Colors.GRAY}{'-' * 120}{Colors.RESET}")

    best_symbol = funding_data[0].get('symbol') if funding_data else None

    # Display available symbols (sorted by net APR)
    for idx, data in enumerate(funding_data[:limit]):
        symbol = data.get('symbol', 'N/A')
        edgex_apr = data.get('edgex_apr', 0)
        lighter_apr = data.get('lighter_apr', 0)
        net_apr = data.get('net_apr', 0)
        long_exch = data.get('long_exch', 'N/A')
        total_volume = data.get('total_volume')
        spread_pct = data.get('spread_pct')

        # Format volume
        if total_volume is not None:
            if total_volume >= 1_000_000_000:
                volume_str = f"${total_volume/1e9:.2f}B"
            elif total_volume >= 1_000_000:
                volume_str = f"${total_volume/1e6:.0f}M"
            else:
                volume_str = f"${total_volume/1e3:.0f}K"
        else:
            volume_str = "N/A"

        # Format spread
        if spread_pct is not None:
            spread_str = f"{spread_pct:.3f}%"
        else:
            spread_str = "N/A"

        # Color code based on status
        status = ""
        color = Colors.RESET
        if symbol == current_symbol:
            status = "◀ CURRENT"
            color = Colors.CYAN
        elif idx == 0:
            status = "★ BEST"
            color = Colors.GREEN

        logger.info(f"{color}{symbol:<10} {edgex_apr:>9.2f}% {lighter_apr:>11.2f}% {net_apr:>9.2f}% {volume_str:>14} {spread_str:>9} {long_exch:>8} {status:<25}{Colors.RESET}")

    # Display excluded symbols (grayed out)
    if excluded_data:
        logger.info(f"{Colors.GRAY}{'-' * 120}{Colors.RESET}")
        for data in excluded_data:
            symbol = data.get('symbol', 'N/A')
            total_volume = data.get('total_volume')
            spread_pct = data.get('spread_pct')
            missing_on = data.get('missing_on', [])

            # Format volume
            if total_volume is not None:
                if total_volume >= 1_000_000_000:
                    volume_str = f"${total_volume/1e9:.2f}B"
                elif total_volume >= 1_000_000:
                    volume_str = f"${total_volume/1e6:.0f}M"
                else:
                    volume_str = f"${total_volume/1e3:.0f}K"
            else:
                volume_str = "N/A"

            # Format spread
            if spread_pct is not None:
                spread_str = f"{spread_pct:.3f}%"
            else:
                spread_str = "N/A"

            # Determine exclusion reason
            if missing_on and isinstance(missing_on, list) and len(missing_on) > 0:
                reason = missing_on[0]
                if "Volume data unavailable" in reason:
                    status = f"✗ EXCLUDED: Volume N/A"
                elif "Volume too low" in reason or "volume" in reason.lower():
                    status = f"✗ EXCLUDED: {reason}"
                elif "Spread too wide" in reason or "spread" in reason.lower():
                    status = f"✗ EXCLUDED: {reason}"
                else:
                    status = f"✗ UNAVAILABLE: {', '.join(missing_on)}"
            else:
                status = "✗ UNAVAILABLE"

            # Show excluded symbols in gray with N/A for rates
            logger.info(f"{Colors.GRAY}{symbol:<10} {'N/A':>10} {'N/A':>12} {'N/A':>10} {volume_str:>14} {spread_str:>9} {'N/A':>8} {status:<25}{Colors.RESET}")

    logger.info(f"{Colors.GRAY}{'-' * 120}{Colors.RESET}")

    # Show comparison if current is not best
    if current_symbol and best_symbol and current_symbol != best_symbol:
        current_data = next((d for d in funding_data if d.get('symbol') == current_symbol), None)
        if current_data:
            current_apr = current_data.get('net_apr', 0)
            best_apr = funding_data[0].get('net_apr', 0)
            diff = best_apr - current_apr
            logger.info(f"{Colors.YELLOW}Note: {best_symbol} currently has +{diff:.2f}% better APR{Colors.RESET}")

    logger.info("")


def display_cycle_summary(cycle: dict, cumulative_stats: dict):
    """Display summary of completed cycle."""
    logger.info(f"\n{Colors.CYAN}{'═' * 70}")
    logger.info(f"CYCLE #{cycle['cycle_number']} COMPLETE - {cycle['symbol']}")
    logger.info(f"{'═' * 70}{Colors.RESET}")

    pnl = cycle['pnl_breakdown']['total_realized_pnl']
    color = Colors.GREEN if pnl >= 0 else Colors.RED

    logger.info(f"Duration: {cycle['duration_hours']:.2f} hours")
    logger.info(f"Realized PnL: {color}${pnl:+.4f} ({cycle['pnl_breakdown']['realized_pnl_percent']:+.3f}%){Colors.RESET}")
    logger.info(f"  EdgeX: ${cycle['pnl_breakdown']['edgex_balance_change']:+.4f}")
    logger.info(f"  Lighter: ${cycle['pnl_breakdown']['lighter_balance_change']:+.4f}")

    logger.info(f"\n{Colors.CYAN}Cumulative Stats:{Colors.RESET}")
    logger.info(f"  Total Cycles: {cumulative_stats['total_cycles']}")
    logger.info(f"  Success Rate: {(cumulative_stats['successful_cycles'] / cumulative_stats['total_cycles'] * 100):.1f}%")

    total_pnl_color = Colors.GREEN if cumulative_stats['total_realized_pnl'] >= 0 else Colors.RED
    logger.info(f"  Total PnL: {total_pnl_color}${cumulative_stats['total_realized_pnl']:+.2f}{Colors.RESET}")
    logger.info(f"  Best Cycle: ${cumulative_stats['best_cycle_pnl']:+.4f}")
    logger.info(f"  Worst Cycle: ${cumulative_stats['worst_cycle_pnl']:+.4f}")
    logger.info(f"  Avg PnL/Cycle: ${(cumulative_stats['total_realized_pnl'] / cumulative_stats['total_cycles']):+.4f}")
    logger.info(f"{'═' * 70}\n")


def display_status(state_mgr: StateManager):
    """Display current bot status."""
    state = state_mgr.get_state()
    pos = state_mgr.state.get("current_position")
    stats = state_mgr.state["cumulative_stats"]
    capital = state_mgr.state.get("capital_status", {})

    logger.info(f"\n{Colors.BOLD}{'═' * 70}")
    logger.info(f"BOT STATUS")
    logger.info(f"{'═' * 70}{Colors.RESET}")
    logger.info(f"State: {Colors.CYAN}{state}{Colors.RESET}")

    # Display capital status
    if capital and capital.get("last_updated"):
        logger.info(f"\nCapital Status:")
        logger.info(f"  EdgeX:   ${capital['edgex_total']:.2f} total, ${capital['edgex_available']:.2f} available")
        logger.info(f"  Lighter: ${capital['lighter_total']:.2f} total, ${capital['lighter_available']:.2f} available")
        logger.info(f"  {Colors.BOLD}Total:   ${capital['total_capital']:.2f} total, ${capital['total_available']:.2f} available{Colors.RESET}")

        # Display long-term PnL if initial capital is available
        initial_capital = capital.get('initial_total_capital')
        current_capital = capital.get('total_capital', 0)
        if initial_capital is not None and initial_capital > 0:
            long_term_pnl_dollars = current_capital - initial_capital
            long_term_pnl_percent = (long_term_pnl_dollars / initial_capital) * 100
            pnl_color = Colors.GREEN if long_term_pnl_dollars >= 0 else Colors.RED
            logger.info(f"  {Colors.BOLD}Long-term PnL: {pnl_color}{long_term_pnl_percent:+.2f}%{Colors.RESET} ({pnl_color}${long_term_pnl_dollars:+.2f}{Colors.RESET} from ${initial_capital:.2f})")

        max_pos_color = Colors.GREEN if capital['max_position_notional'] > 100 else Colors.YELLOW
        logger.info(f"  {max_pos_color}Max Position: ${capital['max_position_notional']:.2f} (limited by {capital['limiting_exchange']}){Colors.RESET}")

    if pos:
        logger.info(f"\nCurrent Position:")
        logger.info(f"  Symbol: {pos['symbol']}")
        logger.info(f"  Setup: Long {pos['long_exchange']}, Short {pos['short_exchange']}")
        logger.info(f"  Opened: {pos['opened_at']}")
        logger.info(f"  Target Close: {pos['target_close_at']}")

        # Get timing info from state or calculate fresh
        if 'time_elapsed_hours' in pos and 'time_remaining_hours' in pos:
            elapsed = pos['time_elapsed_hours']
            remaining = pos['time_remaining_hours']
            progress = pos.get('progress_percent', 0)
        else:
            opened = from_iso_z(pos['opened_at'])
            target = from_iso_z(pos['target_close_at'])
            now = utc_now()
            elapsed = (now - opened).total_seconds() / 3600
            remaining = (target - now).total_seconds() / 3600
            total_duration = elapsed + remaining
            progress = (elapsed / total_duration * 100) if total_duration > 0 else 0

        logger.info(f"  Time Elapsed: {elapsed:.2f}h ({progress:.1f}%)")
        logger.info(f"  Time Remaining: {remaining:.2f}h")

        # Show position sizing info
        sizing = pos.get('position_sizing', {})
        if sizing:
            configured = sizing.get('configured_notional', 0)
            actual = sizing.get('actual_notional', 0)
            was_limited = sizing.get('was_capital_limited', False)

            if was_limited:
                logger.info(f"  Position Size: {Colors.YELLOW}${actual:.2f}{Colors.RESET} (limited from ${configured:.2f})")
                logger.info(f"  Limited by: {Colors.YELLOW}{sizing.get('limiting_exchange', 'N/A')}{Colors.RESET}")
            else:
                logger.info(f"  Position Size: ${actual:.2f}")

        if pos.get('current_pnl'):
            pnl = pos['current_pnl']['total_unrealized_pnl']
            color = Colors.GREEN if pnl >= 0 else Colors.RED
            logger.info(f"  Unrealized PnL: {color}${pnl:+.4f}{Colors.RESET}")

    logger.info(f"\nCumulative Stats:")
    logger.info(f"  Total Cycles: {stats['total_cycles']}")
    if stats['total_cycles'] > 0:
        logger.info(f"  Success Rate: {(stats['successful_cycles'] / stats['total_cycles'] * 100):.1f}%")

        total_pnl_color = Colors.GREEN if stats['total_realized_pnl'] >= 0 else Colors.RED
        logger.info(f"  Total PnL: {total_pnl_color}${stats['total_realized_pnl']:+.2f}{Colors.RESET}")
        logger.info(f"  Avg PnL/Cycle: ${(stats['total_realized_pnl'] / stats['total_cycles']):+.4f}")

    logger.info(f"{'═' * 70}\n")


# ==================== State Recovery ====================

async def recover_state(state_mgr: StateManager, env: dict) -> bool:
    """Recover bot state by checking actual positions on exchanges."""

    logger.info(f"{Colors.YELLOW}Performing state recovery...{Colors.RESET}")

    state = state_mgr.get_state()
    pos = state_mgr.state.get("current_position")

    # If state is IDLE, scan for any existing positions on monitored symbols
    if state == BotState.IDLE:
        logger.info("State is IDLE, scanning for existing positions on monitored symbols...")

        # Load config to get symbols_to_monitor
        try:
            config = BotConfig.load_from_file(state_mgr.state.get("config_file", "bot_config.json"))
        except:
            logger.warning("Could not load config, using default symbol list")
            config = BotConfig.load_from_file("bot_config.json")

        try:
            positions_found = await scan_symbols_for_positions(env, config, config.symbols_to_monitor)

            if positions_found:
                logger.warning(f"{Colors.YELLOW}Found existing open positions!{Colors.RESET}")
                for pos in positions_found:
                    if pos["edgex_size"] != 0:
                        logger.warning(f"  EdgeX {pos['symbol']}: {pos['edgex_size']}")
                    if pos["lighter_size"] != 0:
                        logger.warning(f"  Lighter {pos['symbol']}: {pos['lighter_size']}")

                # Check if it's a single delta-neutral position
                if len(positions_found) == 1:
                    pos = positions_found[0]
                    edgex_size = pos["edgex_size"]
                    lighter_size = pos["lighter_size"]

                    # Check if sizes match and are opposite sides (delta-neutral)
                    if abs(abs(edgex_size) - abs(lighter_size)) < 0.1 and (edgex_size * lighter_size) < 0:
                        logger.info(f"{Colors.GREEN}Detected delta-neutral position on {pos['symbol']}!{Colors.RESET}")
                        logger.info("Bot will recover this position and manage it")

                        position_state = await build_recovered_position_state(
                            env,
                            config,
                            pos['symbol'],
                            edgex_size,
                            lighter_size,
                            pos['edgex_contract_id'],
                            pos['lighter_market_id']
                        )

                        state_mgr.state["current_position"] = position_state
                        state_mgr.state["cumulative_stats"]["last_error"] = None
                        state_mgr.state["cumulative_stats"]["last_error_at"] = None
                        state_mgr.set_state(BotState.HOLDING)
                        await refresh_capital_status(state_mgr, env, config)
                        state_mgr.save()

                        logger.info(f"{Colors.GREEN}Position recovered successfully - now in HOLDING state{Colors.RESET}")
                        return True
                    else:
                        logger.error(f"{Colors.RED}Position sizes don't match or not delta-neutral!{Colors.RESET}")
                        logger.error(f"  EdgeX: {edgex_size}, Lighter: {lighter_size}")
                        logger.error("Please close positions manually and restart")
                        state_mgr.set_state(BotState.ERROR)
                        state_mgr.state["cumulative_stats"]["last_error"] = "Unhedged or mismatched positions detected"
                        state_mgr.state["cumulative_stats"]["last_error_at"] = utc_now_iso()
                        state_mgr.save()
                        return False
                else:
                    logger.error(f"{Colors.RED}Multiple positions found on {len(positions_found)} symbols!{Colors.RESET}")
                    logger.error("Bot can only recover a single delta-neutral position")
                    logger.error("Please close all positions manually and restart")
                    state_mgr.set_state(BotState.ERROR)
                    state_mgr.state["cumulative_stats"]["last_error"] = f"Multiple positions detected on {len(positions_found)} symbols"
                    state_mgr.state["cumulative_stats"]["last_error_at"] = utc_now_iso()
                    state_mgr.save()
                    return False

            logger.info(f"{Colors.GREEN}No existing positions found - ready to start trading{Colors.RESET}")
            return True

        except Exception as e:
            logger.error(f"Error scanning for positions: {e}", exc_info=True)
            logger.warning(f"{Colors.YELLOW}Could not verify positions - proceeding with caution{Colors.RESET}")
            return True  # Allow bot to continue even if scan fails

    # If state is HOLDING, verify positions exist
    if state == BotState.HOLDING and pos:
        logger.info(f"State is HOLDING for {pos['symbol']}, verifying positions...")

        try:
            config = BotConfig.load_from_file(state_mgr.state.get("config_file", "bot_config.json"))
        except:
            config = BotConfig.load_from_file("bot_config.json")

        edgex = None
        lighter_api_client = None
        try:
            # Check EdgeX
            edgex = EdgeXClient(
                base_url=env["EDGEX_BASE_URL"],
                account_id=int(env["EDGEX_ACCOUNT_ID"]),
                stark_private_key=env["EDGEX_STARK_PRIVATE_KEY"]
            )
            lighter_api_client = lighter.ApiClient(configuration=lighter.Configuration(host=env["LIGHTER_BASE_URL"]))
            account_api = lighter.AccountApi(lighter_api_client)
            account_index = int(env.get("ACCOUNT_INDEX", env.get("LIGHTER_ACCOUNT_INDEX", "0")))

            edgex_size, lighter_size = await asyncio.gather(
                edgex_client.get_edgex_open_size(edgex, pos['entry']['edgex_contract_id']),
                lighter_client.get_lighter_open_size(account_api, account_index, int(pos['entry']['lighter_market_id']))
            )

            logger.info(f"Found positions: EdgeX={edgex_size}, Lighter={lighter_size}")

            # Verify hedge is still valid
            if abs(abs(edgex_size) - abs(lighter_size)) > 0.01:
                logger.error(f"{Colors.RED}Position size mismatch detected! Hedge may be broken.{Colors.RESET}")
                state_mgr.set_state(BotState.ERROR)
                state_mgr.state["cumulative_stats"]["last_error"] = f"Position size mismatch: EdgeX={edgex_size}, Lighter={lighter_size}"
                state_mgr.state["cumulative_stats"]["last_error_at"] = utc_now_iso()
                state_mgr.save()
                return False

            if edgex_size == 0 and lighter_size == 0:
                logger.warning(f"{Colors.YELLOW}State says HOLDING but no positions found. Setting to IDLE.{Colors.RESET}")
                state_mgr.state["current_position"] = None
                state_mgr.set_state(BotState.IDLE)
                return True

            logger.info(f"{Colors.GREEN}Recovery complete: Positions verified{Colors.RESET}")
            state_mgr.state["cumulative_stats"]["last_error"] = None
            state_mgr.state["cumulative_stats"]["last_error_at"] = None
            current_pos = state_mgr.state.get("current_position") or {}
            expected = current_pos.get("expected_funding") or {}
            if not expected or expected.get("net_apr") in (None, 0.0):
                updated_funding = await compute_expected_funding(env, config, current_pos.get('symbol', pos['symbol']), current_pos.get('long_exchange'))
                current_pos['expected_funding'] = updated_funding
                state_mgr.state["current_position"] = current_pos
                await refresh_capital_status(state_mgr, env, config)
                state_mgr.save()
            return True

        except Exception as e:
            logger.error(f"Error during recovery: {e}")
            state_mgr.set_state(BotState.ERROR)
            state_mgr.state["cumulative_stats"]["last_error"] = f"Recovery failed: {str(e)}"
            state_mgr.state["cumulative_stats"]["last_error_at"] = utc_now_iso()
            state_mgr.save()
            return False
        finally:
            if edgex:
                try:
                    await edgex.close()
                except:
                    pass
            if lighter_api_client:
                try:
                    await lighter_api_client.close()
                except:
                    pass

    # If state is OPENING or CLOSING, require manual intervention
    if state in [BotState.OPENING, BotState.CLOSING]:
        logger.error(f"{Colors.RED}Bot is in {state} state. Manual intervention required.{Colors.RESET}")
        logger.error("Please check positions on both exchanges and either:")
        logger.error("  1. Manually close any open positions and delete bot_state.json")
        logger.error("  2. Fix the state file if positions are correct")
        return False

    # For WAITING, ANALYZING, or other unknown states: scan for positions before resetting to IDLE
    if state in [BotState.WAITING, BotState.ANALYZING]:
        logger.warning(f"{Colors.YELLOW}Bot in {state} state. Scanning for existing positions on monitored symbols...{Colors.RESET}")
    elif state == BotState.ERROR:
        logger.warning(f"{Colors.YELLOW}Bot previously entered ERROR state. Attempting automatic recovery by scanning positions...{Colors.RESET}")
    else:
        logger.warning(f"{Colors.YELLOW}Unknown state '{state}' during recovery. Scanning for positions...{Colors.RESET}")

    # Load config to get symbols_to_monitor
    try:
        config = BotConfig.load_from_file(state_mgr.state.get("config_file", "bot_config.json"))
    except:
        logger.warning("Could not load config, using default symbol list")
        config = BotConfig.load_from_file("bot_config.json")

    try:
        positions_found = await scan_symbols_for_positions(env, config, config.symbols_to_monitor)

        if positions_found:
            logger.warning(f"{Colors.YELLOW}Found existing open positions!{Colors.RESET}")
            for pos in positions_found:
                if pos["edgex_size"] != 0:
                    logger.warning(f"  EdgeX {pos['symbol']}: {pos['edgex_size']}")
                if pos["lighter_size"] != 0:
                    logger.warning(f"  Lighter {pos['symbol']}: {pos['lighter_size']}")

            # Check if it's a single delta-neutral position
            if len(positions_found) == 1:
                pos = positions_found[0]
                edgex_size = pos["edgex_size"]
                lighter_size = pos["lighter_size"]

                # Check if sizes match and are opposite sides (delta-neutral)
                if abs(abs(edgex_size) - abs(lighter_size)) < 0.1 and (edgex_size * lighter_size) < 0:
                    logger.info(f"{Colors.GREEN}Detected delta-neutral position on {pos['symbol']}!{Colors.RESET}")
                    logger.info("Bot will recover this position and manage it")

                    position_state = await build_recovered_position_state(
                        env,
                        config,
                        pos['symbol'],
                        edgex_size,
                        lighter_size,
                        pos['edgex_contract_id'],
                        pos['lighter_market_id']
                    )

                    state_mgr.state["current_position"] = position_state
                    state_mgr.state["cumulative_stats"]["last_error"] = None
                    state_mgr.state["cumulative_stats"]["last_error_at"] = None
                    state_mgr.set_state(BotState.HOLDING)
                    await refresh_capital_status(state_mgr, env, config)
                    state_mgr.save()

                    logger.info(f"{Colors.GREEN}Position recovered successfully - now in HOLDING state{Colors.RESET}")
                    return True
                else:
                    logger.error(f"{Colors.RED}Position sizes don't match or not delta-neutral!{Colors.RESET}")
                    logger.error(f"  EdgeX: {edgex_size}, Lighter: {lighter_size}")
                    logger.error("Please close positions manually and restart")
                    state_mgr.set_state(BotState.ERROR)
                    state_mgr.state["cumulative_stats"]["last_error"] = "Unhedged or mismatched positions detected"
                    state_mgr.state["cumulative_stats"]["last_error_at"] = utc_now_iso()
                    state_mgr.save()
                    return False
            else:
                logger.error(f"{Colors.RED}Multiple positions found on {len(positions_found)} symbols!{Colors.RESET}")
                logger.error("Bot can only recover a single delta-neutral position")
                logger.error("Please close all positions manually and restart")
                state_mgr.set_state(BotState.ERROR)
                state_mgr.state["cumulative_stats"]["last_error"] = f"Multiple positions detected on {len(positions_found)} symbols"
                state_mgr.state["cumulative_stats"]["last_error_at"] = utc_now_iso()
                state_mgr.save()
                return False

        logger.info(f"{Colors.GREEN}No existing positions found. Resetting to IDLE.{Colors.RESET}")
        state_mgr.set_state(BotState.IDLE)
        state_mgr.state["current_position"] = None
        state_mgr.save()
        return True

    except Exception as e:
        logger.error(f"Error scanning for positions: {e}", exc_info=True)
        logger.warning(f"{Colors.YELLOW}Could not verify positions - resetting to IDLE{Colors.RESET}")
        state_mgr.set_state(BotState.IDLE)
        state_mgr.state["current_position"] = None
        state_mgr.save()
        return True  # Allow bot to continue


# ==================== Main Bot Loop ====================

class RotationBot:
    """Main rotation bot class."""

    def __init__(self, state_file: str = "bot_state.json", config_file: str = "bot_config.json"):
        self.state_mgr = StateManager(state_file)
        self.config_file = config_file
        self.env = load_env()
        self.shutdown_requested = False
        self._shutdown_message_printed = False

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        if not self._shutdown_message_printed:
            logger.info(f"\n{Colors.YELLOW}Shutdown signal received. Stopping after current operation...{Colors.RESET}")
            self._shutdown_message_printed = True
        self.shutdown_requested = True

    async def _interruptible_sleep(self, seconds: float):
        """Sleep in small chunks, checking for shutdown requests."""
        elapsed = 0.0
        chunk_size = 1.0  # Check every second
        while elapsed < seconds and not self.shutdown_requested:
            sleep_time = min(chunk_size, seconds - elapsed)
            await asyncio.sleep(sleep_time)
            elapsed += sleep_time

    async def run(self):
        """Main bot loop."""

        logger.info(f"{Colors.BOLD}{Colors.CYAN}{'═' * 70}")
        logger.info(f"AUTOMATED DELTA-NEUTRAL ROTATION BOT")
        logger.info(f"{'═' * 70}{Colors.RESET}")

        # Load or create state
        self.state_mgr.load()

        # Load config from file
        config = BotConfig.load_from_file(self.config_file)
        logger.info(f"Loaded configuration from {self.config_file}")

        logger.info(f"Monitoring symbols: {', '.join(config.symbols_to_monitor)}")
        logger.info(f"Max position size: ${config.notional_per_position} (auto-adjusts to available capital)")
        logger.info(f"Hold duration: {config.hold_duration_hours} hours")
        logger.info(f"Leverage: {config.leverage}x\n")

        # Recover state
        if not await recover_state(self.state_mgr, self.env):
            logger.error(f"{Colors.RED}State recovery failed. Exiting.{Colors.RESET}")
            return

        # Skip initial funding scan if already HOLDING a position (saves API quota)
        current_state = self.state_mgr.get_state()
        if current_state == BotState.HOLDING:
            logger.info(f"\n{Colors.CYAN}Already holding position, skipping initial funding scan to conserve API quota{Colors.RESET}\n")
        else:
            # Display initial funding rates at startup
            logger.info(f"\n{Colors.CYAN}{'═' * 70}")
            logger.info(f"INITIAL FUNDING RATE SCAN")
            logger.info(f"{'═' * 70}{Colors.RESET}\n")

            # Fetch funding rates for startup display
            async def fetch_with_timeout(symbol: str, timeout: float = 90.0):
                """Fetch funding for a symbol with individual timeout."""
                try:
                    return await asyncio.wait_for(
                        # Disable volume checking for informational display
                        fetch_symbol_funding(symbol, config.quote, self.env, check_volume=False),
                        timeout=timeout
                    )
                except asyncio.TimeoutError:
                    return {"symbol": symbol, "available": False, "error": "timeout"}
                except Exception:
                    return {"symbol": symbol, "available": False}

            startup_results = await asyncio.gather(*[
                fetch_with_timeout(symbol)
                for symbol in config.symbols_to_monitor
            ], return_exceptions=True)

            startup_available = [r for r in startup_results if isinstance(r, dict) and r.get("available", False)]
            startup_excluded = [r for r in startup_results if isinstance(r, dict) and not r.get("available", False)]

            if startup_available:
                startup_available.sort(key=lambda x: x.get("net_apr", 0), reverse=True)
                display_funding_table(startup_available, startup_excluded, current_symbol=None, limit=10)
            elif startup_excluded:
                # Show excluded symbols even if no available ones
                display_funding_table([], startup_excluded, current_symbol=None, limit=10)
            else:
                logger.warning("Unable to fetch funding rates at startup")

            logger.info(f"{Colors.CYAN}{'═' * 70}{Colors.RESET}\n")

        # Main loop
        while not self.shutdown_requested:
            try:
                state = self.state_mgr.get_state()

                # IDLE -> Start new cycle
                if state == BotState.IDLE:
                    # Update capital status before displaying
                    try:
                        capital_info = await get_available_capital_and_max_position(self.env, config)
                    except BalanceFetchError as e:
                        logger.error(f"Unable to refresh balances: {e}. Retrying in 60 seconds...")
                        await self._interruptible_sleep(60)
                        continue
                    self.state_mgr.state["capital_status"] = capital_info
                    self.state_mgr.save()

                    # Reload config before opening position to pick up any changes
                    try:
                        config = BotConfig.load_from_file(self.config_file)
                        logger.info(f"Reloaded configuration from {self.config_file}")
                    except Exception as e:
                        logger.warning(f"Failed to reload config, using existing: {e}")

                    display_status(self.state_mgr)
                    success = await open_best_position(self.state_mgr, self.env, config)
                    if not success:
                        logger.error("Failed to open position. Waiting 5 minutes before retry...")
                        await self._interruptible_sleep(300)
                        continue

                # HOLDING -> Monitor position
                elif state == BotState.HOLDING:
                    pos = self.state_mgr.state["current_position"]

                    # Safety check: if no position exists, return to IDLE
                    if not pos:
                        logger.warning(f"{Colors.YELLOW}State is HOLDING but no position found. Returning to IDLE.{Colors.RESET}")
                        self.state_mgr.set_state(BotState.IDLE)
                        continue

                    # Check if it's time to close
                    target_close = from_iso_z(pos['target_close_at'])
                    now = utc_now()

                    if now >= target_close:
                        # Time to close
                        success = await close_current_position(self.state_mgr, self.env)
                        if success:
                            self.state_mgr.set_state(BotState.WAITING)
                        else:
                            logger.error("Failed to close position. Manual intervention required.")
                            break
                    else:
                        # Still holding, update PnL
                        # Calculate elapsed and remaining time first
                        opened = from_iso_z(pos['opened_at'])
                        elapsed = (now - opened).total_seconds() / 3600
                        remaining = (target_close - now).total_seconds() / 3600
                        total_duration = config.hold_duration_hours
                        progress_percent = (elapsed / total_duration * 100) if total_duration > 0 else 0

                        # Get current PnL
                        pnl_data = await get_position_pnl(
                            self.env, pos['symbol'], pos['quote'],
                            pos['long_exchange'], pos['short_exchange'],
                            pos['entry']['edgex_contract_id'],
                            pos['entry']['lighter_market_id']
                        )

                        # Update position with timing info and PnL
                        pos['current_pnl'] = pnl_data
                        pos['time_elapsed_hours'] = elapsed
                        pos['time_remaining_hours'] = remaining
                        pos['progress_percent'] = progress_percent
                        pos['last_check'] = utc_now_iso()
                        self.state_mgr.save()

                        # Check stop-loss if enabled
                        if config.enable_stop_loss:
                            # Calculate notional from actual position size
                            size_base = pos['entry'].get('size_base', 0)
                            entry_price = pos['entry'].get('edgex_entry_price')

                            if size_base and entry_price:
                                notional = size_base * entry_price
                            else:
                                # Fallback to actual notional used (not configured max)
                                sizing = pos.get('position_sizing', {})
                                notional = sizing.get('actual_notional', config.notional_per_position)

                            stop_loss_triggered, stop_loss_reason, calculated_stop_loss_pct = check_stop_loss(
                                pnl_data, notional, config.leverage
                            )

                            if stop_loss_triggered:
                                logger.warning(f"\n{Colors.RED}{'!' * 70}")
                                logger.warning(f"STOP-LOSS TRIGGERED!")
                                logger.warning(f"{'!' * 70}{Colors.RESET}")
                                logger.warning(f"Reason: {stop_loss_reason}")
                                logger.warning(f"EdgeX PnL: ${pnl_data['edgex_unrealized_pnl']:+.4f}")
                                logger.warning(f"Lighter PnL: ${pnl_data['lighter_unrealized_pnl']:+.4f}")
                                logger.warning(f"Total PnL: ${pnl_data['total_unrealized_pnl']:+.4f}")
                                logger.warning(f"Closing position immediately...\n")

                                # Record stop-loss event in position data
                                pos['stop_loss_triggered'] = True
                                pos['stop_loss_reason'] = stop_loss_reason
                                pos['stop_loss_time'] = utc_now_iso()
                                self.state_mgr.save()

                                # Emergency close
                                success = await close_current_position(self.state_mgr, self.env)
                                if success:
                                    logger.info(f"{Colors.YELLOW}Position closed due to stop-loss. "
                                              f"Waiting before next cycle...{Colors.RESET}")
                                    self.state_mgr.set_state(BotState.WAITING)
                                else:
                                    logger.error("Failed to close position after stop-loss. Manual intervention required.")
                                    break
                                continue

                        # Update capital status periodically
                        await refresh_capital_status(self.state_mgr, self.env, config)
                        capital_info = self.state_mgr.state.get("capital_status", {})
                        self.state_mgr.save()

                        # Display PnL and capital status
                        pnl_color = Colors.GREEN if pnl_data['total_unrealized_pnl'] >= 0 else Colors.RED
                        edgex_color = Colors.GREEN if pnl_data['edgex_unrealized_pnl'] >= 0 else Colors.RED
                        lighter_color = Colors.GREEN if pnl_data['lighter_unrealized_pnl'] >= 0 else Colors.RED

                        # Get position sizing info
                        sizing = pos.get('position_sizing', {})

                        # Format timestamps for display
                        opened_dt = from_iso_z(pos['opened_at'])
                        target_dt = from_iso_z(pos['target_close_at'])
                        opened_str = opened_dt.strftime('%Y-%m-%d %H:%M:%S UTC')
                        target_str = target_dt.strftime('%Y-%m-%d %H:%M:%S UTC')

                        # Get cycle information from persistent state
                        current_cycle = self.state_mgr.state.get('current_cycle', 1)

                        logger.info(f"\n{Colors.CYAN}{'─' * 70}{Colors.RESET}")
                        logger.info(f"{Colors.BOLD}HOLDING {pos['symbol']} - Delta Neutral Position (Cycle #{current_cycle}){Colors.RESET}")
                        logger.info(f"{Colors.CYAN}{'─' * 70}{Colors.RESET}")
                        # Get current UTC time
                        current_utc = utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')

                        logger.info(f"🤖  Bot State:     {Colors.BOLD}{Colors.CYAN}{state}{Colors.RESET}")
                        logger.info(f"📅  Opened:        {Colors.GRAY}{opened_str}{Colors.RESET}")
                        logger.info(f"🎯  Target Close:  {Colors.GRAY}{target_str}{Colors.RESET}")
                        logger.info(f"🕐  Current Time:  {Colors.GRAY}{current_utc}{Colors.RESET}")
                        logger.info(f"⏱  Time Elapsed:  {Colors.CYAN}{elapsed:.2f}h{Colors.RESET} / {total_duration:.2f}h ({progress_percent:.1f}%)")
                        logger.info(f"⏰  Time Remaining: {Colors.YELLOW}{remaining:.2f}h{Colors.RESET} until close, wait, and re-open")

                        # Show position sizing info if available
                        if sizing:
                            configured = sizing.get('configured_notional', 0)
                            actual = sizing.get('actual_notional', 0)
                            was_limited = sizing.get('was_capital_limited', False)

                            if was_limited:
                                logger.info(f"💼  Position Size: {Colors.YELLOW}${actual:.2f}{Colors.RESET} (limited from ${configured:.2f})")
                                logger.info(f"    Limited by:   {Colors.YELLOW}{sizing.get('limiting_exchange', 'N/A')}{Colors.RESET}")
                            else:
                                logger.info(f"💼  Position Size: ${actual:.2f}")

                        # Calculate PnL percentages
                        position_notional = sizing.get('actual_notional', 0) if sizing else 0
                        edgex_pnl_pct = (pnl_data['edgex_unrealized_pnl'] / position_notional * 100) if position_notional > 0 else 0
                        lighter_pnl_pct = (pnl_data['lighter_unrealized_pnl'] / position_notional * 100) if position_notional > 0 else 0
                        total_pnl_pct = (pnl_data['total_unrealized_pnl'] / position_notional * 100) if position_notional > 0 else 0

                        logger.info(f"📈  EdgeX PnL:     {edgex_color}${pnl_data['edgex_unrealized_pnl']:+.4f} ({edgex_pnl_pct:+.2f}%){Colors.RESET}")
                        logger.info(f"📉  Lighter PnL:   {lighter_color}${pnl_data['lighter_unrealized_pnl']:+.4f} ({lighter_pnl_pct:+.2f}%){Colors.RESET}")
                        logger.info(f"💰  Total PnL:     {pnl_color}${pnl_data['total_unrealized_pnl']:+.4f} ({total_pnl_pct:+.2f}%){Colors.RESET}")

                        # Show long-term PnL if initial capital is available
                        initial_capital = capital_info.get('initial_total_capital')
                        current_capital = capital_info.get('total_capital', 0)
                        if initial_capital is not None and initial_capital > 0:
                            long_term_pnl_dollars = current_capital - initial_capital
                            long_term_pnl_percent = (long_term_pnl_dollars / initial_capital) * 100
                            lt_pnl_color = Colors.GREEN if long_term_pnl_dollars >= 0 else Colors.RED
                            logger.info(f"🎯  Long-term PnL: {lt_pnl_color}{long_term_pnl_percent:+.2f}%{Colors.RESET} ({lt_pnl_color}${long_term_pnl_dollars:+.2f}{Colors.RESET} from ${initial_capital:.2f})")

                        # Show stop-loss info (auto-calculated based on leverage)
                        stop_loss_enabled = config.enable_stop_loss
                        stop_loss_pct = calculate_stop_loss_percent(config.leverage)
                        if stop_loss_enabled:
                            logger.info(f"🛡  Stop Loss:     {Colors.YELLOW}{stop_loss_pct:.2f}%{Colors.RESET} (auto @ {config.leverage}x leverage)")
                        else:
                            logger.info(f"🛡  Stop Loss:     {Colors.GRAY}Disabled{Colors.RESET}")
                        edge_available = capital_info.get('edgex_available')
                        lighter_available = capital_info.get('lighter_available')
                        edge_total = capital_info.get('edgex_total')

                        edge_available_display = f"${edge_available:.2f}" if isinstance(edge_available, (int, float)) else "N/A"
                        lighter_available_display = f"${lighter_available:.2f}" if isinstance(lighter_available, (int, float)) else "N/A"
                        edge_total_display = f"${edge_total:.2f}" if isinstance(edge_total, (int, float)) else "N/A"
                        max_position_display = capital_info.get('max_position_notional')
                        max_position_display = f"${max_position_display:.2f}" if isinstance(max_position_display, (int, float)) else "N/A"

                        logger.info(f"💵  Available:     EdgeX {edge_available_display}, Lighter {lighter_available_display}")
                        logger.info(f"🏦  EdgeX Total:   {edge_total_display}")
                        logger.info(f"📊  Max new position:  {max_position_display} (limited by {capital_info.get('limiting_exchange')})")
                        logger.info(f"{Colors.CYAN}{'─' * 70}{Colors.RESET}")

                        # Display funding rates table
                        logger.info(f"\n{Colors.BOLD}📊 Funding Rates Overview{Colors.RESET}")
                        logger.info(f"{Colors.CYAN}{'─' * 90}{Colors.RESET}")

                        try:
                            # Fetch current funding rates for all monitored symbols
                            # Each symbol has its own 90-second timeout to prevent blocking
                            async def fetch_with_timeout(symbol: str, delay: float = 0.0, timeout: float = 90.0):
                                """Fetch funding for a symbol with individual timeout and staggered delay."""
                                # Add stagger delay to prevent rate limit hits
                                if delay > 0:
                                    await asyncio.sleep(delay)

                                try:
                                    return await asyncio.wait_for(
                                        # Enable volume checking for monitoring display
                                        fetch_symbol_funding(symbol, config.quote, self.env, check_volume=True, min_volume_usd=config.min_volume_usd, max_spread_pct=config.max_spread_pct),
                                        timeout=timeout
                                    )
                                except asyncio.TimeoutError:
                                    logger.warning(f"{symbol}: Funding rate fetch timed out after {timeout}s")
                                    return {"symbol": symbol, "available": False, "error": "timeout"}
                                except Exception as e:
                                    logger.warning(f"{symbol}: Error fetching funding - {str(e)[:50]}")
                                    return {"symbol": symbol, "available": False, "error": str(e)[:50]}

                            # Fetch all symbols with staggered delays to avoid rate limits
                            stagger_delay = 0.5
                            funding_results = await asyncio.gather(*[
                                fetch_with_timeout(symbol, delay=idx * stagger_delay)
                                for idx, symbol in enumerate(config.symbols_to_monitor)
                            ], return_exceptions=True)

                            # Filter successful results
                            funding_data = []
                            for result in funding_results:
                                if isinstance(result, dict) and result.get("available"):
                                    funding_data.append(result)

                            if funding_data:
                                # Sort by net APR descending
                                funding_data.sort(key=lambda x: x.get("net_apr", 0), reverse=True)

                                # Find current and best symbols
                                current_symbol = pos.get('symbol')
                                best_symbol = funding_data[0].get('symbol') if funding_data else None

                                # Display table header with volume column
                                logger.info(f"{'Symbol':<10} {'EdgeX APR':>10} {'Lighter APR':>12} {'Net APR':>10} {'24h Volume':>14} {'Status':<15}")
                                logger.info(f"{Colors.GRAY}{'-' * 90}{Colors.RESET}")

                                # Show top 5 opportunities
                                for idx, data in enumerate(funding_data[:5]):
                                    symbol = data.get('symbol', 'N/A')
                                    edgex_apr = data.get('edgex_apr', 0)
                                    lighter_apr = data.get('lighter_apr', 0)
                                    net_apr = data.get('net_apr', 0)
                                    total_volume = data.get('total_volume')

                                    # Format volume
                                    if total_volume is not None:
                                        if total_volume >= 1_000_000_000:
                                            volume_str = f"${total_volume/1e9:.2f}B"
                                        elif total_volume >= 1_000_000:
                                            volume_str = f"${total_volume/1e6:.0f}M"
                                        else:
                                            volume_str = f"${total_volume/1e3:.0f}K"
                                    else:
                                        volume_str = "N/A"

                                    # Color code based on status
                                    status = ""
                                    color = Colors.RESET
                                    if symbol == current_symbol:
                                        status = "◀ CURRENT"
                                        color = Colors.CYAN
                                    elif idx == 0:
                                        status = "★ BEST"
                                        color = Colors.GREEN

                                    logger.info(f"{color}{symbol:<10} {edgex_apr:>9.2f}% {lighter_apr:>11.2f}% {net_apr:>9.2f}% {volume_str:>14} {status:<15}{Colors.RESET}")

                                logger.info(f"{Colors.GRAY}{'-' * 90}{Colors.RESET}")

                                # Show summary if current position is not the best
                                if current_symbol and best_symbol and current_symbol != best_symbol:
                                    current_data = next((d for d in funding_data if d.get('symbol') == current_symbol), None)
                                    if current_data:
                                        current_apr = current_data.get('net_apr', 0)
                                        best_apr = funding_data[0].get('net_apr', 0)
                                        diff = best_apr - current_apr
                                        logger.info(f"{Colors.YELLOW}Note: {best_symbol} currently has +{diff:.2f}% better APR{Colors.RESET}")
                            else:
                                logger.info(f"{Colors.GRAY}No funding data available (some symbols may have timed out){Colors.RESET}")
                        except Exception as e:
                            logger.info(f"{Colors.YELLOW}Error fetching funding rates: {str(e)[:100]}{Colors.RESET}")
                            logger.info(f"{Colors.GRAY}Funding rates unavailable{Colors.RESET}")

                        logger.info(f"{Colors.CYAN}{'─' * 90}{Colors.RESET}\n")

                        # Sleep until next check
                        await self._interruptible_sleep(config.check_interval_seconds)

                # WAITING -> Cooldown between cycles
                elif state == BotState.WAITING:
                    # Use persistent cycle counter
                    completed_cycles = self.state_mgr.state.get('current_cycle', 0)
                    next_cycle = completed_cycles + 1

                    logger.info(f"\n{Colors.CYAN}{'─' * 70}{Colors.RESET}")
                    logger.info(f"{Colors.BOLD}WAITING - Cooldown Between Cycles{Colors.RESET}")
                    logger.info(f"{Colors.CYAN}{'─' * 70}{Colors.RESET}")
                    logger.info(f"✅  Completed Cycles: {completed_cycles}")
                    logger.info(f"🔄  Next Cycle:       #{next_cycle}")
                    logger.info(f"⏳  Waiting:          {config.wait_between_cycles_minutes} minutes before next analysis")
                    logger.info(f"{Colors.CYAN}{'─' * 70}{Colors.RESET}\n")

                    # Sleep with periodic updates
                    wait_seconds = config.wait_between_cycles_minutes * 60
                    update_interval = 30  # Update every 30 seconds
                    elapsed = 0

                    while elapsed < wait_seconds and not self.shutdown_requested:
                        sleep_time = min(update_interval, wait_seconds - elapsed)
                        await asyncio.sleep(sleep_time)
                        elapsed += sleep_time

                        remaining_minutes = (wait_seconds - elapsed) / 60
                        if remaining_minutes > 0 and not self.shutdown_requested:
                            logger.info(f"⏳  {remaining_minutes:.1f} minutes until next cycle...")

                    if not self.shutdown_requested:
                        logger.info(f"{Colors.GREEN}Cooldown complete. Starting next cycle...{Colors.RESET}\n")
                        self.state_mgr.set_state(BotState.IDLE)

                # ERROR -> Manual intervention required
                elif state == BotState.ERROR:
                    logger.error(f"{Colors.RED}Bot is in ERROR state. Manual intervention required.{Colors.RESET}")
                    logger.error(f"Last error: {self.state_mgr.state['cumulative_stats']['last_error']}")
                    logger.error(f"Error time: {self.state_mgr.state['cumulative_stats']['last_error_at']}")
                    break

                # Unknown state -> Reset to IDLE
                else:
                    logger.warning(f"{Colors.YELLOW}Unknown state '{state}'. Resetting to IDLE.{Colors.RESET}")
                    self.state_mgr.set_state(BotState.IDLE)
                    await self._interruptible_sleep(1)  # Brief sleep to prevent busy loop

            except Exception as e:
                logger.error(f"{Colors.RED}Unexpected error in main loop: {e}{Colors.RESET}", exc_info=True)
                self.state_mgr.set_state(BotState.ERROR)
                self.state_mgr.state["cumulative_stats"]["last_error"] = str(e)
                self.state_mgr.state["cumulative_stats"]["last_error_at"] = utc_now_iso()
                self.state_mgr.save()
                break

        # Shutdown
        logger.info(f"\n{Colors.CYAN}{'═' * 70}")
        logger.info(f"BOT SHUTTING DOWN")
        logger.info(f"{'═' * 70}{Colors.RESET}")
        display_status(self.state_mgr)
        logger.info("Goodbye!\n")


# ==================== Entry Point ====================

def main():
    parser = argparse.ArgumentParser(description="Automated delta-neutral rotation bot")
    default_state_file = os.getenv("BOT_STATE_FILE", "bot_state.json")
    parser.add_argument("--state-file", default=default_state_file, help=f"Path to state file (default: {default_state_file})")
    parser.add_argument("--config", default="bot_config.json", help="Path to config file (default: bot_config.json)")
    args = parser.parse_args()

    bot = RotationBot(state_file=args.state_file, config_file=args.config)

    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
