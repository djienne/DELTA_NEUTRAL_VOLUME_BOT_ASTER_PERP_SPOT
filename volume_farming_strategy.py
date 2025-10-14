#!/usr/bin/env python3
"""
Automated Volume Farming Strategy for Delta-Neutral Funding Rate Arbitrage.

This strategy implements an automated loop that:
1. Scans for the best funding rates across all delta-neutral pairs
2. Opens a position on the pair with the highest funding rate
3. Monitors the position until entry + exit fees are covered by funding payments
4. Closes the position and reopens on the current best funding rate pair
5. Repeats indefinitely with safety checks and risk management

Safety Features:
- Health checks before each trade
- Position imbalance monitoring
- Automatic leverage validation (1x only)
- Funding rate stability analysis
- Minimum profitability thresholds
- Emergency stop conditions
- Comprehensive logging and monitoring
"""

import asyncio
import os
import sys
import json
import math
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
from colorama import init, Fore, Style
import logging

from aster_api_manager import AsterApiManager
from strategy_logic import DeltaNeutralLogic

# Load environment variables
load_dotenv()
init()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('volume_farming.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class VolumeFarmingStrategy:
    """
    Automated volume farming strategy that continuously farms funding rates
    by rotating positions to maximize volume and funding rate capture.
    """

    def __init__(
        self,
        capital_fraction: float = 0.95,
        min_funding_apr: float = 15.0,
        fee_coverage_multiplier: float = 1.5,
        loop_interval_seconds: int = 300,  # 5 minutes
        max_position_age_hours: int = 24,
        use_funding_ma: bool = True,
        funding_ma_periods: int = 10,
        leverage: int = 1
    ):
        """
        Initialize the volume farming strategy.

        Args:
            capital_fraction: Fraction of total available USDT to deploy (0.95 = 95%)
            min_funding_apr: Minimum annualized funding APR to consider (%)
            fee_coverage_multiplier: Multiplier for fee coverage (1.5 = 150% of fees)
            loop_interval_seconds: Seconds between strategy loop cycles
            max_position_age_hours: Maximum hours to hold a position
            use_funding_ma: Use moving average of funding rates instead of instantaneous
            funding_ma_periods: Number of periods for funding rate moving average
            leverage: Leverage multiplier (1-3). 1=50/50, 2=33% perp/67% spot, 3=25% perp/75% spot
        """
        # Validate leverage
        if leverage < 1 or leverage > 3:
            raise ValueError(f"Leverage must be between 1 and 3, got {leverage}")
        self.api_manager = AsterApiManager(
            api_user=os.getenv('API_USER'),
            api_signer=os.getenv('API_SIGNER'),
            api_private_key=os.getenv('API_PRIVATE_KEY'),
            apiv1_public=os.getenv('APIV1_PUBLIC_KEY'),
            apiv1_private=os.getenv('APIV1_PRIVATE_KEY')
        )
        self.logic = DeltaNeutralLogic()

        # Strategy parameters
        self.capital_fraction = capital_fraction
        self.min_funding_apr = min_funding_apr
        self.fee_coverage_multiplier = fee_coverage_multiplier
        self.loop_interval_seconds = loop_interval_seconds
        self.max_position_age = timedelta(hours=max_position_age_hours)
        self.use_funding_ma = use_funding_ma
        self.funding_ma_periods = funding_ma_periods
        self.leverage = leverage

        # Calculate emergency stop-loss automatically based on leverage
        # This ensures we stay safely away from liquidation
        self.emergency_stop_loss_pct = self._calculate_safe_stoploss(leverage)

        # State tracking
        self.state_file = 'volume_farming_state.json'
        self.current_position: Optional[Dict[str, Any]] = None
        self.position_opened_at: Optional[datetime] = None
        self.position_leverage: Optional[int] = None  # Track leverage used for current position
        self.total_funding_received: float = 0.0
        self.entry_fees_paid: float = 0.0
        self.running = True
        self.cycle_count = 0  # Count of completed trading cycles (open → hold → close)
        self.total_profit_loss: float = 0.0
        self.total_positions_opened: int = 0
        self.total_positions_closed: int = 0

        # Portfolio PnL tracking (long-term performance)
        self.initial_portfolio_value_usdt: Optional[float] = None  # Baseline portfolio value
        self.initial_portfolio_timestamp: Optional[datetime] = None  # When baseline was captured

        # Load persisted state if available
        self._load_state()

        logger.info(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}")
        logger.info(f"{Fore.CYAN}Volume Farming Strategy initialized{Style.RESET_ALL}")
        logger.info(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}")
        logger.info(f"Capital Fraction: {Fore.MAGENTA}{capital_fraction*100:.0f}%{Style.RESET_ALL} of available USDT")
        logger.info(f"Emergency Stop-Loss: {Fore.RED}{self.emergency_stop_loss_pct:.1f}%{Style.RESET_ALL} (auto-calculated for {Fore.MAGENTA}{leverage}x{Style.RESET_ALL} leverage with 0.7% safety buffer)")

        # Check if we have a position with different leverage before logging config leverage
        has_leverage_mismatch = (self.current_position and
                                self.position_leverage and
                                self.position_leverage != self.leverage)

        if has_leverage_mismatch:
            leverage_msg = f"Leverage: {Fore.MAGENTA}{leverage}x{Style.RESET_ALL} (Perp: {Fore.CYAN}{100/(leverage+1):.1f}%{Style.RESET_ALL}, Spot: {Fore.CYAN}{100*leverage/(leverage+1):.1f}%{Style.RESET_ALL}) - {Fore.YELLOW}Current position using {self.position_leverage}x, will switch at next rebalancing{Style.RESET_ALL}"
            logger.info(leverage_msg)
            # Also print to terminal to ensure visibility
            print(f"\n{Fore.YELLOW}{'='*80}{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}⚠️  LEVERAGE MISMATCH DETECTED{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}{'='*80}{Style.RESET_ALL}")
            print(f"Config leverage:   {Fore.MAGENTA}{leverage}x{Style.RESET_ALL} (will apply to new positions)")
            print(f"Position leverage: {Fore.MAGENTA}{self.position_leverage}x{Style.RESET_ALL} (current ASTERUSDT position)")
            print(f"Action required:   Position will switch to {Fore.MAGENTA}{leverage}x{Style.RESET_ALL} at next rebalancing")
            print(f"{Fore.YELLOW}{'='*80}{Style.RESET_ALL}\n")
        else:
            logger.info(f"Leverage: {Fore.MAGENTA}{leverage}x{Style.RESET_ALL} (Perp: {Fore.CYAN}{100/(leverage+1):.1f}%{Style.RESET_ALL}, Spot: {Fore.CYAN}{100*leverage/(leverage+1):.1f}%{Style.RESET_ALL})")

        logger.info(f"Min Funding APR: {Fore.GREEN}{min_funding_apr}%{Style.RESET_ALL}")
        logger.info(f"Fee Coverage Multiplier: {Fore.CYAN}{fee_coverage_multiplier}x{Style.RESET_ALL}")
        logger.info(f"Funding Rate Mode: {Fore.YELLOW}{'Moving Average (' + str(funding_ma_periods) + ' periods)' if use_funding_ma else 'Instantaneous'}{Style.RESET_ALL}")

        if self.current_position:
            logger.info(f"{Fore.YELLOW}Recovered open position: {self.current_position['symbol']}{Style.RESET_ALL}")
            logger.info(f"  Opened at: {self.position_opened_at.strftime('%Y-%m-%d %H:%M:%S')} UTC")
            logger.info(f"  Entry fees: ${self.entry_fees_paid:.4f}")
            if self.position_leverage:
                logger.info(f"  Position leverage: {self.position_leverage}x")
                logger.debug(f"[LEVERAGE] Recovered position {self.current_position['symbol']} at {self.position_leverage}x leverage")
                if self.position_leverage != self.leverage:
                    logger.warning(f"  Config leverage is {self.leverage}x but position opened at {self.position_leverage}x")
                    logger.warning(f"  Position will maintain {self.position_leverage}x until closed. New positions will use {self.leverage}x.")
                    logger.debug(f"[LEVERAGE] Leverage mismatch: position={self.position_leverage}x, config={self.leverage}x - preserving position leverage")

    @staticmethod
    def _calculate_safe_stoploss(leverage: int, maintenance_margin: float = 0.005, safety_buffer: float = 0.007) -> float:
        """
        Calculate maximum safe stop-loss for SHORT perpetual position in delta-neutral strategy.

        This calculation ensures the stop-loss triggers BEFORE reaching liquidation,
        with a safety buffer to account for fees, slippage, and volatility.

        Args:
            leverage: Leverage multiplier (1-3)
            maintenance_margin: Exchange maintenance margin rate (default: 0.5%)
            safety_buffer: Safety buffer in price fraction (default: 0.7%)
                          Includes: fees (~0.1%), slippage (~0.2%), volatility (~0.4%)

        Returns:
            Maximum safe stop-loss as negative percentage (e.g., -24.0 for -24%)

        Formula:
            1. Calculate max price move before liquidation: s_max = [(1 + 1/L)/(1 + m) - 1] - b
            2. Adjust for delta-neutral capital allocation: PnL% = -s_max * [L/(L+1)]
            3. Round down for extra safety

        Example for 3x leverage:
            - Liquidation at +32.67% price move
            - Max safe stop at +31.97% (with 0.7% buffer)
            - Perp allocation: 75% of total capital
            - Max safe stop-loss: -31.97% × 0.75 = -23.98% ≈ -24%
        """
        L = leverage
        m = maintenance_margin
        b = safety_buffer

        # Calculate max price distance before hitting liquidation buffer (for SHORT)
        s_max = ((1 + 1/L) / (1 + m) - 1) - b

        # In delta-neutral strategy, perp is only L/(L+1) of total capital
        # So PnL relative to total deployed capital is:
        perp_fraction = L / (L + 1)
        max_stop_pnl = -s_max * perp_fraction

        # Convert to percentage and round down for safety
        max_stop_pct = math.floor(max_stop_pnl * 100)

        return float(max_stop_pct)

    def _load_state(self):
        """Load persisted state from JSON file with validation."""
        if not os.path.exists(self.state_file):
            logger.info("No state file found, starting fresh")
            return

        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)

            # Validate and load with type checking
            self.current_position = state.get('current_position')

            # Validate current_position structure if exists
            if self.current_position:
                if not isinstance(self.current_position, dict):
                    logger.error("Invalid position data in state file")
                    self.current_position = None
                elif 'symbol' not in self.current_position:
                    logger.error("Position missing symbol in state file")
                    self.current_position = None

            # Load numeric values with validation
            try:
                self.total_funding_received = float(state.get('total_funding_received', 0.0))
                self.entry_fees_paid = float(state.get('entry_fees_paid', 0.0))
                self.cycle_count = int(state.get('cycle_count', 0))
                self.total_profit_loss = float(state.get('total_profit_loss', 0.0))
                self.total_positions_opened = int(state.get('total_positions_opened', 0))
                self.total_positions_closed = int(state.get('total_positions_closed', 0))
            except (ValueError, TypeError) as e:
                logger.error(f"Invalid numeric data in state file: {e}")
                # Keep defaults

            # Load position leverage if it exists (separate try block since it can be None)
            try:
                if self.current_position and 'position_leverage' in state:
                    leverage_value = state.get('position_leverage')
                    if leverage_value is not None:
                        self.position_leverage = int(leverage_value)
                    else:
                        self.position_leverage = None
                else:
                    self.position_leverage = None
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid position_leverage in state file: {e}, setting to None")
                self.position_leverage = None

            # Load portfolio baseline values if they exist
            try:
                if 'initial_portfolio_value_usdt' in state:
                    self.initial_portfolio_value_usdt = float(state.get('initial_portfolio_value_usdt'))
                if 'initial_portfolio_timestamp' in state:
                    timestamp_str = state.get('initial_portfolio_timestamp')
                    if timestamp_str:
                        self.initial_portfolio_timestamp = datetime.fromisoformat(timestamp_str)
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid portfolio baseline data in state file: {e}")
                self.initial_portfolio_value_usdt = None
                self.initial_portfolio_timestamp = None

            # Parse datetime if position exists
            if self.current_position:
                opened_at_str = state.get('position_opened_at')
                if opened_at_str:
                    try:
                        self.position_opened_at = datetime.fromisoformat(opened_at_str)
                    except (ValueError, TypeError) as e:
                        logger.error(f"Invalid datetime in state file: {e}")
                        self.position_opened_at = None
                        # If we can't parse datetime, clear position for safety
                        self.current_position = None

            logger.info(f"{Fore.GREEN}State loaded successfully from {self.state_file}{Style.RESET_ALL}")
            logger.info(f"  Trading cycles completed: {Fore.CYAN}{self.cycle_count}{Style.RESET_ALL}")
            logger.info(f"  Total positions opened: {Fore.CYAN}{self.total_positions_opened}{Style.RESET_ALL}")
            logger.info(f"  Total positions closed: {Fore.CYAN}{self.total_positions_closed}{Style.RESET_ALL}")
            pnl_color = Fore.GREEN if self.total_profit_loss >= 0 else Fore.RED
            logger.info(f"  Cumulative P/L: {pnl_color}${self.total_profit_loss:.4f}{Style.RESET_ALL}")

        except json.JSONDecodeError as e:
            logger.error(f"Corrupted state file (invalid JSON): {e}")
            logger.info("Starting with fresh state")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            logger.info("Starting with fresh state")

    def _save_state(self):
        """Save current state to JSON file."""
        try:
            state = {
                'current_position': self.current_position,
                'position_opened_at': self.position_opened_at.isoformat() if self.position_opened_at else None,
                'position_leverage': self.position_leverage,
                'total_funding_received': self.total_funding_received,
                'entry_fees_paid': self.entry_fees_paid,
                'cycle_count': self.cycle_count,
                'total_profit_loss': self.total_profit_loss,
                'total_positions_opened': self.total_positions_opened,
                'total_positions_closed': self.total_positions_closed,
                'initial_portfolio_value_usdt': self.initial_portfolio_value_usdt,
                'initial_portfolio_timestamp': self.initial_portfolio_timestamp.isoformat() if self.initial_portfolio_timestamp else None,
                'last_updated': datetime.utcnow().isoformat()
            }

            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)

            logger.debug(f"State saved to {self.state_file}")

        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    async def _capture_initial_portfolio(self):
        """
        Capture the initial portfolio value as baseline for long-term PnL tracking.
        This should only be called once when the bot starts fresh (no baseline in state).

        Initial Portfolio Value = Total Spot Value + Perp Wallet Balance + Perp Unrealized PnL
        """
        try:
            logger.info(f"{Fore.CYAN}Capturing initial portfolio baseline...{Style.RESET_ALL}")

            # Use the same calculation method as _get_current_portfolio_value()
            initial_value = await self._get_current_portfolio_value()

            if initial_value is None:
                logger.error("Failed to calculate initial portfolio value")
                return

            # Store baseline
            self.initial_portfolio_value_usdt = initial_value
            self.initial_portfolio_timestamp = datetime.utcnow()

            logger.info(f"{Fore.GREEN}Initial portfolio baseline captured:{Style.RESET_ALL}")
            logger.info(f"  {Fore.MAGENTA}Total Baseline: ${initial_value:.2f}{Style.RESET_ALL}")
            logger.info(f"  Timestamp: {Fore.YELLOW}{self.initial_portfolio_timestamp.strftime('%Y-%m-%d %H:%M:%S')} UTC{Style.RESET_ALL}")

            # Save to state immediately
            self._save_state()

        except Exception as e:
            logger.error(f"Error capturing initial portfolio baseline: {e}", exc_info=True)

    async def _get_current_portfolio_value(self) -> Optional[float]:
        """
        Calculate current total portfolio value including all assets.

        Returns:
            Total portfolio value in USDT, or None if unable to fetch
        """
        try:
            # Get comprehensive portfolio data
            portfolio_data = await self.api_manager.get_comprehensive_portfolio_data()
            if not portfolio_data:
                return None

            # SPOT SIDE: Calculate total value of all spot holdings
            spot_balances = portfolio_data.get('spot_balances', [])
            spot_total_value = 0.0

            for balance in spot_balances:
                asset = balance.get('asset')
                free_amount = float(balance.get('free', 0))

                if free_amount <= 0:
                    continue

                if asset == 'USDT':
                    # USDT is already in USDT
                    spot_total_value += free_amount
                else:
                    # For other assets, get current price and convert to USDT value
                    try:
                        symbol = f"{asset}USDT"
                        # Get current price from perp market (same price as spot)
                        import aiohttp
                        if not self.api_manager.session:
                            self.api_manager.session = aiohttp.ClientSession()

                        perp_ticker_url = f"https://fapi.asterdex.com/fapi/v1/ticker/price?symbol={symbol}"
                        async with self.api_manager.session.get(perp_ticker_url) as resp:
                            if resp.status == 200:
                                ticker_data = await resp.json()
                                current_price = float(ticker_data.get('price', 0))
                                asset_value_usdt = free_amount * current_price
                                spot_total_value += asset_value_usdt
                                logger.debug(f"Spot {asset}: {free_amount:.8f} @ ${current_price:.2f} = ${asset_value_usdt:.2f}")
                    except Exception as price_error:
                        logger.warning(f"Could not get price for {asset}: {price_error}")
                        # Skip this asset if we can't get price
                        continue

            # PERP SIDE: Get wallet balance (includes all realized PnL)
            perp_account_info = portfolio_data.get('perp_account_info', {})
            assets = perp_account_info.get('assets', [])
            perp_wallet_balance = next((float(a.get('walletBalance', 0)) for a in assets if a.get('asset') == 'USDT'), 0.0)

            # Get perp unrealized PnL (if any open positions)
            raw_perp_positions = portfolio_data.get('raw_perp_positions', [])
            perp_unrealized_pnl = sum(float(p.get('unrealizedProfit', 0)) for p in raw_perp_positions)

            # TOTAL: Spot holdings value + Perp wallet + Perp unrealized PnL
            current_value = spot_total_value + perp_wallet_balance + perp_unrealized_pnl

            logger.debug(f"Portfolio breakdown: Spot=${spot_total_value:.2f}, Perp Wallet=${perp_wallet_balance:.2f}, Perp uPnL=${perp_unrealized_pnl:.2f}, Total=${current_value:.2f}")

            return current_value

        except Exception as e:
            logger.error(f"Error calculating current portfolio value: {e}", exc_info=True)
            return None

    def _calculate_total_portfolio_pnl(self, current_portfolio_value: float) -> Dict[str, Any]:
        """
        Calculate total portfolio PnL vs initial baseline.

        Args:
            current_portfolio_value: Current total portfolio value in USDT

        Returns:
            Dict with PnL_usd, PnL_pct, and formatted strings
        """
        if self.initial_portfolio_value_usdt is None or self.initial_portfolio_value_usdt == 0:
            return {
                'pnl_usd': 0.0,
                'pnl_pct': 0.0,
                'has_baseline': False
            }

        pnl_usd = current_portfolio_value - self.initial_portfolio_value_usdt
        pnl_pct = (pnl_usd / self.initial_portfolio_value_usdt) * 100

        return {
            'pnl_usd': pnl_usd,
            'pnl_pct': pnl_pct,
            'has_baseline': True,
            'initial_value': self.initial_portfolio_value_usdt,
            'current_value': current_portfolio_value,
            'baseline_timestamp': self.initial_portfolio_timestamp
        }

    async def _discover_existing_position(self):
        """
        Detect if there's an existing delta-neutral position that the bot doesn't know about.
        This happens when a position was opened manually or state file was deleted.
        """
        try:
            logger.info("Checking for existing delta-neutral positions...")

            portfolio_data = await self.api_manager.get_comprehensive_portfolio_data()
            if not portfolio_data:
                return

            analyzed_positions = portfolio_data.get('analyzed_positions', [])
            dn_positions = [p for p in analyzed_positions if p.get('is_delta_neutral')]

            if not dn_positions:
                logger.info("No existing delta-neutral positions found")
                return

            if len(dn_positions) > 1:
                logger.warning(f"Found {len(dn_positions)} delta-neutral positions. Bot manages one at a time.")
                logger.warning(f"Will track the first one: {dn_positions[0]['symbol']}")

            # Take the first DN position
            existing_pos = dn_positions[0]
            symbol = existing_pos['symbol']

            # Get entry price from raw perp position
            raw_perp_positions = portfolio_data.get('raw_perp_positions', [])
            perp_pos = next((p for p in raw_perp_positions if p.get('symbol') == symbol), None)
            entry_price = float(perp_pos.get('entryPrice', 0)) if perp_pos else 0

            logger.info(f"{Fore.YELLOW}Discovered existing position: {symbol}{Style.RESET_ALL}")
            logger.info(f"  Spot balance: {existing_pos.get('spot_balance', 0):.6f}")
            logger.info(f"  Perp position: {existing_pos.get('perp_position', 0):.6f}")
            logger.info(f"  Position value: ${existing_pos.get('position_value_usd', 0):.2f}")
            logger.info(f"  Entry price: ${entry_price:.4f}")

            # Try to fetch actual funding data from API
            funding_analysis = await self.api_manager.perform_funding_analysis(symbol)

            if funding_analysis:
                # We have complete funding history
                total_funding = float(funding_analysis.get('total_funding', 0))
                position_value = float(funding_analysis.get('effective_position_value', existing_pos.get('position_value_usd', 0)))

                # Estimate entry fees (0.1% total)
                entry_fees = position_value * 0.001

                # Parse position start time
                start_time_str = funding_analysis.get('position_start_time')
                if start_time_str:
                    position_opened_at = datetime.strptime(start_time_str, '%Y-%m-%d %H:%M:%S')
                else:
                    position_opened_at = datetime.utcnow()

                logger.info(f"  Position opened: {start_time_str or 'Unknown (using now)'}")
                logger.info(f"  Funding received: ${total_funding:.4f}")
                logger.info(f"  Funding payments: {funding_analysis.get('funding_payments_count', 0)}")

                # Get current funding rate for the position
                funding_rate = 0.0
                effective_apr = 0.0

                try:
                    if self.use_funding_ma:
                        rate_data = await self.api_manager.get_funding_rate_ma(symbol, self.funding_ma_periods)
                        if rate_data:
                            funding_rate = rate_data['ma_rate']
                            effective_apr = rate_data['effective_ma_apr']
                    else:
                        funding_rates = await self.api_manager.get_all_funding_rates()
                        rate_info = next((r for r in funding_rates if r['symbol'] == symbol), None)
                        if rate_info:
                            funding_rate = rate_info['rate']
                            effective_apr = rate_info['apr'] / 2  # Effective APR for 1x leverage
                except Exception as rate_error:
                    logger.warning(f"Could not fetch current funding rate: {rate_error}")
                    # Continue with zero rates - position will still be tracked

                # Try to detect leverage from current position
                # Get current leverage from exchange
                try:
                    current_leverage = await self.api_manager.get_perp_leverage(symbol)
                    self.position_leverage = current_leverage
                    logger.info(f"  Detected leverage from exchange: {current_leverage}x")
                    logger.debug(f"[LEVERAGE] Position {symbol}: Detected {current_leverage}x from exchange, config is {self.leverage}x")
                except Exception as lev_error:
                    logger.warning(f"Could not detect leverage from exchange: {lev_error}")
                    logger.debug(f"[LEVERAGE] Position {symbol}: Failed to detect, assuming config leverage {self.leverage}x")
                    # Assume config leverage for discovered positions
                    self.position_leverage = self.leverage
                    logger.info(f"  Assuming config leverage: {self.leverage}x")

                # Adopt this position
                self.current_position = {
                    'symbol': symbol,
                    'capital': position_value,
                    'funding_rate': funding_rate,
                    'effective_apr': effective_apr,
                    'spot_qty': existing_pos.get('spot_balance', 0),
                    'perp_qty': abs(existing_pos.get('perp_position', 0)),
                    'entry_price': entry_price
                }
                self.position_opened_at = position_opened_at
                self.total_funding_received = total_funding
                self.entry_fees_paid = entry_fees

                logger.info(f"{Fore.GREEN}Successfully adopted existing position{Style.RESET_ALL}")
                logger.info(f"  Current funding rate: {funding_rate*100:.4f}%")
                logger.info(f"  Effective APR: {effective_apr:.2f}%")

                # Save the discovered position
                self._save_state()
            else:
                logger.warning("Could not fetch funding history for existing position")
                logger.warning("Position will not be tracked until manually added to state file")

        except Exception as e:
            logger.error(f"Error discovering existing position: {e}", exc_info=True)

    async def _reconcile_position_state(self):
        """
        Reconcile position state between state file and exchange.
        Exchange is always the source of truth.

        This handles:
        1. Position closed on exchange but still in state -> Clear state
        2. Position opened on exchange but not in state -> Adopt it
        3. Position in both -> Verify they match, update from exchange
        """
        try:
            logger.info("Reconciling position state with exchange...")

            # Get actual positions from exchange
            portfolio_data = await self.api_manager.get_comprehensive_portfolio_data()
            if not portfolio_data:
                logger.warning("Could not fetch portfolio data for reconciliation")
                return

            analyzed_positions = portfolio_data.get('analyzed_positions', [])
            dn_positions = [p for p in analyzed_positions if p.get('is_delta_neutral')]

            # Case 1: We think we have a position but exchange says no
            if self.current_position and not dn_positions:
                logger.warning(f"{Fore.YELLOW}Position {self.current_position['symbol']} tracked in state but not found on exchange{Style.RESET_ALL}")
                logger.warning("Position was likely closed externally. Clearing state.")

                self.current_position = None
                self.position_opened_at = None
                self.position_leverage = None
                self.total_funding_received = 0.0
                self.entry_fees_paid = 0.0
                self._save_state()
                return

            # Case 2: Exchange has position but we don't track it
            if not self.current_position and dn_positions:
                logger.info(f"{Fore.CYAN}Exchange has delta-neutral position but not tracked in state{Style.RESET_ALL}")
                await self._discover_existing_position()
                return

            # Case 3: Both have a position - verify they match
            if self.current_position and dn_positions:
                tracked_symbol = self.current_position['symbol']
                exchange_symbols = [p['symbol'] for p in dn_positions]

                # Check if tracked position exists on exchange
                if tracked_symbol not in exchange_symbols:
                    logger.warning(f"{Fore.YELLOW}Tracked position {tracked_symbol} not found on exchange{Style.RESET_ALL}")
                    logger.warning(f"Exchange has: {', '.join(exchange_symbols)}")
                    logger.warning("Adopting the exchange position...")

                    # Clear old position and discover new one
                    self.current_position = None
                    self.position_opened_at = None
                    self.position_leverage = None
                    self.total_funding_received = 0.0
                    self.entry_fees_paid = 0.0
                    await self._discover_existing_position()
                    return

                # Position matches - update funding data from exchange
                logger.info(f"{Fore.GREEN}Position {tracked_symbol} confirmed on exchange{Style.RESET_ALL}")

                # Detect and update leverage if not already set
                if not self.position_leverage:
                    try:
                        current_leverage = await self.api_manager.get_perp_leverage(tracked_symbol)
                        self.position_leverage = current_leverage
                        logger.info(f"  Detected leverage from exchange: {current_leverage}x")
                        logger.debug(f"[LEVERAGE] Position {tracked_symbol}: Detected {current_leverage}x from exchange during reconciliation")

                        # Print mismatch warning to terminal
                        if current_leverage != self.leverage:
                            logger.warning(f"  Config leverage is {Fore.MAGENTA}{self.leverage}x{Style.RESET_ALL} but position is at {Fore.MAGENTA}{current_leverage}x{Style.RESET_ALL}")
                            logger.warning(f"  Position will maintain {Fore.MAGENTA}{current_leverage}x{Style.RESET_ALL} until closed. New positions will use {Fore.MAGENTA}{self.leverage}x{Style.RESET_ALL}.")
                            print(f"\n{Fore.YELLOW}{'='*80}{Style.RESET_ALL}")
                            print(f"{Fore.YELLOW}⚠️  LEVERAGE MISMATCH DETECTED{Style.RESET_ALL}")
                            print(f"{Fore.YELLOW}{'='*80}{Style.RESET_ALL}")
                            print(f"Config leverage:   {Fore.MAGENTA}{self.leverage}x{Style.RESET_ALL} (will apply to new positions)")
                            print(f"Position leverage: {Fore.MAGENTA}{current_leverage}x{Style.RESET_ALL} (current {Fore.CYAN}{tracked_symbol}{Style.RESET_ALL} position)")
                            print(f"Action:            Position will switch to {Fore.MAGENTA}{self.leverage}x{Style.RESET_ALL} at next rebalancing")
                            print(f"{Fore.YELLOW}{'='*80}{Style.RESET_ALL}\n")

                        # Save the updated state with detected leverage
                        self._save_state()
                    except Exception as lev_error:
                        logger.warning(f"Could not detect leverage from exchange: {lev_error}")
                        self.position_leverage = self.leverage
                        logger.info(f"  Assuming config leverage: {self.leverage}x")

                # Fetch latest funding data from exchange (source of truth)
                funding_analysis = await self.api_manager.perform_funding_analysis(tracked_symbol)
                if funding_analysis:
                    # Update funding received from exchange
                    exchange_funding = float(funding_analysis.get('total_funding', 0))

                    if abs(exchange_funding - self.total_funding_received) > 0.0001:
                        logger.info(f"  Updating funding from exchange: ${self.total_funding_received:.4f} -> ${exchange_funding:.4f}")
                        self.total_funding_received = exchange_funding
                        self._save_state()
                    else:
                        logger.info(f"  Funding data synchronized: ${self.total_funding_received:.4f}")

                # Update position value from exchange
                exchange_pos = next((p for p in dn_positions if p['symbol'] == tracked_symbol), None)
                if exchange_pos:
                    exchange_value = exchange_pos.get('position_value_usd', 0)
                    state_value = self.current_position.get('capital', 0)

                    if abs(exchange_value - state_value) > 1.0:  # More than $1 difference
                        logger.info(f"  Updating position value: ${state_value:.2f} -> ${exchange_value:.2f}")
                        self.current_position['capital'] = exchange_value
                        self.current_position['spot_qty'] = exchange_pos.get('spot_balance', 0)
                        self.current_position['perp_qty'] = abs(exchange_pos.get('perp_position', 0))
                        self._save_state()

            # Case 4: No position anywhere
            if not self.current_position and not dn_positions:
                logger.info("No positions tracked or on exchange - ready to open new position")

        except Exception as e:
            logger.error(f"Error reconciling position state: {e}", exc_info=True)

    async def run(self):
        """Main strategy loop."""
        logger.info("Starting Volume Farming Strategy...")

        # Always reconcile state with exchange on startup
        await self._reconcile_position_state()

        # Capture initial portfolio baseline if not already set
        if self.initial_portfolio_value_usdt is None:
            await self._capture_initial_portfolio()

        try:
            check_iteration = 0  # Track loop iterations separately from trading cycles
            while self.running:
                check_iteration += 1
                logger.info(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}")
                logger.info(f"{Fore.CYAN}CHECK #{Fore.MAGENTA}{check_iteration}{Fore.CYAN} - {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC | Trading Cycles Completed: {Fore.MAGENTA}{self.cycle_count}{Style.RESET_ALL}")

                # Get and display portfolio PnL
                current_portfolio_value = await self._get_current_portfolio_value()
                if current_portfolio_value is not None:
                    pnl_data = self._calculate_total_portfolio_pnl(current_portfolio_value)
                    if pnl_data['has_baseline']:
                        pnl_usd = pnl_data['pnl_usd']
                        pnl_pct = pnl_data['pnl_pct']
                        pnl_color = Fore.GREEN if pnl_usd >= 0 else Fore.RED
                        pnl_sign = '+' if pnl_usd >= 0 else ''
                        baseline_date = pnl_data['baseline_timestamp'].strftime('%Y-%m-%d %H:%M UTC') if pnl_data['baseline_timestamp'] else 'Unknown'

                        logger.info(f"{Fore.CYAN}📊 Portfolio: {Fore.MAGENTA}${current_portfolio_value:.2f}{Fore.CYAN} | PnL: {pnl_color}{pnl_sign}${pnl_usd:.2f} ({pnl_sign}{pnl_pct:.2f}%){Fore.CYAN} | Since: {Fore.YELLOW}{baseline_date}{Style.RESET_ALL}")

                logger.info(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}")

                # Step 1: Perform health check
                if not await self._perform_health_check():
                    logger.warning("Health check failed. Waiting before retry...")
                    await asyncio.sleep(self.loop_interval_seconds)
                    continue

                # Step 2: Check if we have an open position
                if self.current_position:
                    # Check if leverage setting has changed
                    if self.position_leverage and self.position_leverage != self.leverage:
                        logger.info(f"{Fore.YELLOW}Note: Position opened at {self.position_leverage}x leverage, config is {self.leverage}x{Style.RESET_ALL}")
                        logger.info(f"  New leverage will apply when position is closed and reopened")
                        logger.debug(f"[LEVERAGE] Check #{check_iteration}: Position at {self.position_leverage}x, config at {self.leverage}x - preserving position leverage")

                    # Monitor existing position
                    should_close = await self._should_close_position()
                    if should_close:
                        await self._close_current_position()
                    else:
                        logger.info(f"{Fore.CYAN}Holding position on {Fore.MAGENTA}{self.current_position['symbol']}{Style.RESET_ALL}")
                        await asyncio.sleep(self.loop_interval_seconds)
                        continue

                # Step 3: Scan for best funding rate opportunity
                best_opportunity = await self._find_best_funding_opportunity()
                if not best_opportunity:
                    logger.warning("No viable opportunities found. Waiting...")
                    await asyncio.sleep(self.loop_interval_seconds)
                    continue

                # Step 4: Open position on best opportunity
                await self._open_position(best_opportunity)

                # Step 5: Save state after each cycle
                self._save_state()

                # Step 6: Wait before next check
                await asyncio.sleep(self.loop_interval_seconds)

        except KeyboardInterrupt:
            logger.info("Shutdown signal received...")
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
        finally:
            await self._shutdown()

    async def _perform_health_check(self) -> bool:
        """
        Perform comprehensive health check before trading.

        Returns:
            True if healthy, False otherwise
        """
        try:
            logger.info(f"{Fore.CYAN}Performing health check...{Style.RESET_ALL}")

            # Check account balances
            portfolio_data = await self.api_manager.get_comprehensive_portfolio_data()
            if not portfolio_data:
                logger.error(f"{Fore.RED}Failed to fetch portfolio data{Style.RESET_ALL}")
                return False

            spot_balances = portfolio_data.get('spot_balances', [])
            perp_account_info = portfolio_data.get('perp_account_info', {})
            assets = perp_account_info.get('assets', [])

            # Get USDT balances
            spot_usdt = next((float(b.get('free', 0)) for b in spot_balances if b.get('asset') == 'USDT'), 0.0)
            perp_usdt = next((float(a.get('availableBalance', 0)) for a in assets if a.get('asset') == 'USDT'), 0.0)

            logger.info(f"Spot USDT: {Fore.GREEN}${spot_usdt:.2f}{Style.RESET_ALL}")
            logger.info(f"Perp USDT: {Fore.GREEN}${perp_usdt:.2f}{Style.RESET_ALL}")

            # Basic balance check - just ensure we have some USDT available
            if not self.current_position:
                if spot_usdt + perp_usdt < 30.0:
                    logger.error(f"Insufficient balance. Spot: ${spot_usdt:.2f}, Perp: ${perp_usdt:.2f} (needs at least 30$ total)")
                    return False

            # Check existing positions health
            health_issues, critical_issues, dn_count, _ = await self.api_manager.perform_health_check_analysis()
            if critical_issues:
                logger.error(f"Critical health issues detected: {critical_issues}")
                return False

            if health_issues:
                logger.warning(f"Health warnings: {health_issues}")

            logger.info(f"{Fore.GREEN}✓ Health check passed{Style.RESET_ALL} (Existing DN positions: {Fore.CYAN}{dn_count}{Style.RESET_ALL})")
            return True

        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    async def _get_24h_volumes(self) -> Dict[str, float]:
        """
        Fetch 24h volumes for all delta-neutral pairs.

        Returns:
            Dict mapping symbol -> total 24h volume (spot + perp) in USDT
        """
        try:
            import aiohttp

            if not self.api_manager.session:
                self.api_manager.session = aiohttp.ClientSession()

            session = self.api_manager.session

            # Fetch spot and perp 24h ticker data concurrently
            spot_url = "https://sapi.asterdex.com/api/v1/ticker/24hr"
            perp_url = "https://fapi.asterdex.com/fapi/v1/ticker/24hr"

            async with session.get(spot_url) as spot_resp, session.get(perp_url) as perp_resp:
                spot_resp.raise_for_status()
                perp_resp.raise_for_status()
                spot_tickers = await spot_resp.json()
                perp_tickers = await perp_resp.json()

            # Build volume map
            volumes = {}

            # Add spot volumes
            for ticker in spot_tickers:
                symbol = ticker.get('symbol')
                quote_volume = float(ticker.get('quoteVolume', 0))
                if symbol:
                    volumes[symbol] = quote_volume

            # Add perp volumes
            for ticker in perp_tickers:
                symbol = ticker.get('symbol')
                quote_volume = float(ticker.get('quoteVolume', 0))
                if symbol:
                    volumes[symbol] = volumes.get(symbol, 0) + quote_volume

            return volumes

        except Exception as e:
            logger.warning(f"Could not fetch 24h volumes: {e}")
            return {}

    async def _find_best_funding_opportunity(self) -> Optional[Dict[str, Any]]:
        """
        Scan all available pairs and find the best funding rate opportunity.
        Uses moving average if enabled, otherwise uses instantaneous rates.

        Applies multiple filters:
        1. Negative current funding rates (excluded)
        2. Minimum 24h volume >= $250M
        3. Spot-perp price spread <= 0.15%
        4. Minimum APR threshold

        Returns:
            Dict with symbol and funding rate info, or None if no opportunity
        """
        try:
            # Track filtered pairs for logging
            negative_rate_pairs = []

            # Choose mode based on configuration
            if self.use_funding_ma:
                # MA MODE: Use moving average of funding rates for stability
                logger.info(f"Scanning for best funding rate opportunity (MA mode: {self.funding_ma_periods} periods)...")

                # Get all available symbols first
                available_symbols = await self.api_manager.discover_delta_neutral_pairs()
                if not available_symbols:
                    logger.warning("No delta-neutral pairs available")
                    return None

                # Fetch MA funding rates for all symbols
                ma_tasks = [self.api_manager.get_funding_rate_ma(symbol, self.funding_ma_periods) for symbol in available_symbols]
                ma_results = await asyncio.gather(*ma_tasks, return_exceptions=True)

                # Also fetch current rates for comparison and negative rate filtering
                current_rates_data = await self.api_manager.get_all_funding_rates()
                current_rates_map = {r['symbol']: r for r in current_rates_data} if current_rates_data else {}

                funding_rates = []
                for i, symbol in enumerate(available_symbols):
                    ma_data = ma_results[i]

                    if isinstance(ma_data, Exception) or not ma_data:
                        logger.debug(f"Could not fetch MA for {symbol}: {ma_data if isinstance(ma_data, Exception) else 'No data'}")
                        continue

                    # Get current rate for this symbol (for filtering and display)
                    current_rate_info = current_rates_map.get(symbol)
                    current_rate = current_rate_info['rate'] if current_rate_info else 0

                    # CRITICAL: Filter based on CURRENT rate, not MA
                    # Even if MA is positive, if current rate is negative, exclude the pair
                    if current_rate < 0:
                        negative_rate_pairs.append(f"{symbol} ({current_rate*100:.4f}%)")
                        logger.debug(f"Filtered {symbol}: current funding rate {current_rate*100:.4f}% is negative (MA was {ma_data.get('ma_rate', 0)*100:.4f}%)")
                        continue

                    funding_rates.append({
                        'symbol': symbol,
                        'funding_rate': ma_data['ma_rate'],  # Use MA rate for decision
                        'current_rate': current_rate,  # Store current rate for display
                        'effective_apr': ma_data['effective_ma_apr'],
                        'funding_freq': current_rate_info.get('funding_freq', 3) if current_rate_info else 3,
                        'next_funding_time': None,
                        'using_ma': True
                    })

                # Log negative rate filtering summary
                if negative_rate_pairs:
                    logger.info(f"{Fore.RED}Negative rate filter: {Fore.MAGENTA}{len(negative_rate_pairs)}{Fore.RED} pair(s) excluded: {Fore.YELLOW}{', '.join(negative_rate_pairs)}{Style.RESET_ALL}")

            else:
                # INSTANTANEOUS MODE: Use current/next funding rates from premiumIndex
                logger.info("Scanning for best funding rate opportunity (current/next rates from premiumIndex)...")

                # Fetch current/next funding rates from premiumIndex endpoint
                current_funding_rates_data = await self.api_manager.get_all_funding_rates()
                if not current_funding_rates_data:
                    logger.warning("No current funding rates available")
                    return None

                # Use instantaneous (current/next) funding rates, filtering negative rates
                funding_rates = []
                for rate_data in current_funding_rates_data:
                    symbol = rate_data['symbol']
                    current_rate = rate_data['rate']

                    # Skip if current rate is negative
                    if current_rate < 0:
                        negative_rate_pairs.append(f"{symbol} ({current_rate*100:.4f}%)")
                        logger.debug(f"Filtered {symbol}: current funding rate {current_rate*100:.4f}% is negative")
                        continue

                    funding_rates.append({
                        'symbol': symbol,
                        'funding_rate': current_rate,
                        'current_rate': current_rate,
                        'effective_apr': rate_data['apr'],  # Already calculated correctly with frequency
                        'funding_freq': rate_data.get('funding_freq', 3),
                        'next_funding_time': None,
                        'using_ma': False
                    })

                # Log negative rate filtering summary
                if negative_rate_pairs:
                    logger.info(f"{Fore.RED}Negative rate filter: {Fore.MAGENTA}{len(negative_rate_pairs)}{Fore.RED} pair(s) excluded: {Fore.YELLOW}{', '.join(negative_rate_pairs)}{Style.RESET_ALL}")

            # Get available delta-neutral pairs
            available_pairs = await self.api_manager.discover_delta_neutral_pairs()
            if not available_pairs:
                logger.warning("No delta-neutral pairs available")
                return None

            # Fetch 24h volumes for filtering
            volumes = await self._get_24h_volumes()
            min_volume_threshold = 250_000_000  # $250 million

            # Filter pairs by volume (keep only pairs with >= $250M 24h volume)
            high_volume_pairs = []
            for symbol in available_pairs:
                volume = volumes.get(symbol, 0)
                if volume >= min_volume_threshold:
                    high_volume_pairs.append(symbol)
                else:
                    logger.debug(f"Filtered out {symbol}: 24h volume ${volume:,.0f} < ${min_volume_threshold:,.0f}")

            if not high_volume_pairs:
                logger.warning(f"No pairs meet minimum volume threshold of ${min_volume_threshold:,.0f}")
                return None

            logger.info(f"{Fore.CYAN}Volume filter: {Fore.MAGENTA}{len(high_volume_pairs)}/{len(available_pairs)}{Fore.CYAN} pairs with >= {Fore.GREEN}${min_volume_threshold/1e6:.0f}M{Fore.CYAN} volume{Style.RESET_ALL}")
            logger.info(f"High-volume pairs: {Fore.YELLOW}{', '.join(high_volume_pairs)}{Style.RESET_ALL}")

            # Apply spread filtering (filter out pairs with spread > 0.15%)
            logger.info(f"{Fore.CYAN}Checking spot-perp price spreads...{Style.RESET_ALL}")
            max_spread_threshold = 0.15  # 0.15% maximum spread

            # Fetch spot and perp prices concurrently
            spot_tasks = [self.api_manager.get_spot_book_ticker(symbol, suppress_errors=True) for symbol in high_volume_pairs]
            perp_tasks = [self.api_manager.get_perp_book_ticker(symbol) for symbol in high_volume_pairs]

            spot_results = await asyncio.gather(*spot_tasks, return_exceptions=True)
            perp_results = await asyncio.gather(*perp_tasks, return_exceptions=True)

            # Calculate spreads and filter
            spread_filtered_pairs = []
            high_spread_pairs = []

            for i, symbol in enumerate(high_volume_pairs):
                spot_data = spot_results[i]
                perp_data = perp_results[i]

                # Skip if either fetch failed
                if isinstance(spot_data, Exception) or isinstance(perp_data, Exception):
                    logger.debug(f"Failed to fetch prices for {symbol}, skipping spread check")
                    spread_filtered_pairs.append(symbol)  # Include if we can't check
                    continue

                # Skip if missing price data
                if not spot_data or not perp_data:
                    logger.debug(f"Missing price data for {symbol}, skipping spread check")
                    spread_filtered_pairs.append(symbol)  # Include if we can't check
                    continue

                spot_bid = spot_data.get('bidPrice')
                spot_ask = spot_data.get('askPrice')
                perp_bid = perp_data.get('bidPrice')
                perp_ask = perp_data.get('askPrice')

                # Skip if any price is missing
                if not all([spot_bid, spot_ask, perp_bid, perp_ask]):
                    logger.debug(f"Incomplete price data for {symbol}, skipping spread check")
                    spread_filtered_pairs.append(symbol)  # Include if we can't check
                    continue

                try:
                    # Convert to float
                    spot_bid = float(spot_bid)
                    spot_ask = float(spot_ask)
                    perp_bid = float(perp_bid)
                    perp_ask = float(perp_ask)

                    # Calculate mid prices
                    spot_mid = (spot_bid + spot_ask) / 2
                    perp_mid = (perp_bid + perp_ask) / 2

                    # Calculate spread percentage (perp - spot) / spot * 100
                    spread_pct = abs((perp_mid - spot_mid) / spot_mid * 100)

                    if spread_pct <= max_spread_threshold:
                        spread_filtered_pairs.append(symbol)
                        logger.debug(f"{symbol}: spread {spread_pct:.4f}% <= {max_spread_threshold}% (OK)")
                    else:
                        high_spread_pairs.append(f"{symbol} ({spread_pct:.4f}%)")
                        logger.debug(f"{symbol}: spread {spread_pct:.4f}% > {max_spread_threshold}% (FILTERED)")
                except Exception as calc_error:
                    logger.debug(f"Error calculating spread for {symbol}: {calc_error}")
                    spread_filtered_pairs.append(symbol)  # Include if calculation fails

            # Log spread filtering results
            if high_spread_pairs:
                logger.info(f"{Fore.RED}Spread filter: {Fore.MAGENTA}{len(high_spread_pairs)}{Fore.RED} pair(s) excluded (spread > {max_spread_threshold}%): {Fore.YELLOW}{', '.join(high_spread_pairs)}{Style.RESET_ALL}")

            if not spread_filtered_pairs:
                logger.warning(f"No pairs meet spread threshold of {max_spread_threshold}%")
                return None

            logger.info(f"{Fore.GREEN}Spread filter: {Fore.MAGENTA}{len(spread_filtered_pairs)}/{len(high_volume_pairs)}{Fore.GREEN} pairs with spread <= {max_spread_threshold}%{Style.RESET_ALL}")

            # Show ALL available pairs (including currently held position) that passed all filters
            all_candidates = [
                rate for rate in funding_rates
                if rate['symbol'] in spread_filtered_pairs
            ]

            if not all_candidates:
                logger.warning("No delta-neutral pairs available")
                return None

            # Get current position symbol if any
            current_symbol = self.current_position['symbol'] if self.current_position else None

            # Sort all by effective APR (descending) for display
            all_candidates.sort(key=lambda x: x['effective_apr'], reverse=True)

            # Display table with format adapted to mode
            if self.use_funding_ma:
                # MA MODE: Show both MA APR and Current APR for comparison
                logger.info(f"{Fore.CYAN}Funding Rate Scan Results (MA Mode - {self.funding_ma_periods} periods):{Style.RESET_ALL}")
                logger.info("=" * 120)

                header = f"{'Symbol':<12} {'Interval':<10} {'MA Rate %':<12} {'MA APR %':<12} {'Curr APR %':<12} {'Status':<15}"
                logger.info(header)
                logger.info("-" * 120)

                for c in all_candidates:
                    # Mark current position
                    is_current = (c['symbol'] == current_symbol)

                    # Highlight based on whether it meets threshold and if it's current position
                    if is_current:
                        color = Fore.CYAN
                        status = "[CURRENT]"
                    elif c['effective_apr'] >= self.min_funding_apr:
                        color = Fore.GREEN
                        status = ""
                    else:
                        color = Fore.YELLOW
                        status = f"<{self.min_funding_apr}%"

                    symbol_display = f"{c['symbol']:<12}"

                    # Display interval (e.g., "4h/6x")
                    funding_freq = c.get('funding_freq', 3)
                    interval_hours = 24 / funding_freq if funding_freq > 0 else 8
                    interval_str = f"{int(interval_hours)}h/{funding_freq}x"
                    interval_display = f"{interval_str:<10}"

                    ma_rate = f"{c['funding_rate']*100:>11.4f}"
                    ma_apr = f"{c['effective_apr']:>11.2f}"

                    # Calculate current APR for comparison
                    current_rate = c.get('current_rate', 0)
                    current_apr = current_rate * funding_freq * 365 * 100
                    curr_apr = f"{current_apr:>11.2f}"

                    logger.info(f"{color}{symbol_display} {interval_display} {ma_rate} {ma_apr} {curr_apr} {status:<15}{Style.RESET_ALL}")

                logger.info("=" * 120)

            else:
                # INSTANTANEOUS MODE: Show current/next rates with interval
                logger.info(f"{Fore.CYAN}Funding Rate Scan Results (Current/Next Rates):{Style.RESET_ALL}")
                logger.info("=" * 110)

                header = f"{'Symbol':<12} {'Interval':<10} {'Rate %':<12} {'APR %':<12} {'Status':<15}"
                logger.info(header)
                logger.info("-" * 110)

                for c in all_candidates:
                    # Mark current position
                    is_current = (c['symbol'] == current_symbol)

                    # Highlight based on whether it meets threshold and if it's current position
                    if is_current:
                        color = Fore.CYAN
                        status = "[CURRENT]"
                    elif c['effective_apr'] >= self.min_funding_apr:
                        color = Fore.GREEN
                        status = ""
                    else:
                        color = Fore.YELLOW
                        status = f"<{self.min_funding_apr}%"

                    symbol_display = f"{c['symbol']:<12}"

                    # Display interval (e.g., "4h/6x")
                    funding_freq = c.get('funding_freq', 3)
                    interval_hours = 24 / funding_freq if funding_freq > 0 else 8
                    interval_str = f"{int(interval_hours)}h/{funding_freq}x"
                    interval_display = f"{interval_str:<10}"

                    rate = f"{c['funding_rate']*100:>11.4f}"
                    eff_apr = f"{c['effective_apr']:>11.2f}"

                    logger.info(f"{color}{symbol_display} {interval_display} {rate} {eff_apr} {status:<15}{Style.RESET_ALL}")

                logger.info("=" * 110)

            # Filter by minimum APR threshold (effective APR for 1x leverage)
            candidates = [
                c for c in all_candidates
                if c['effective_apr'] >= self.min_funding_apr
            ]

            if not candidates:
                logger.warning(f"{Fore.RED}No pairs meet minimum APR threshold of {self.min_funding_apr}%{Style.RESET_ALL}")
                return None

            # Announce selection
            best = candidates[0]
            logger.info(f"{Fore.GREEN}>>> Selected: {best['symbol']} with {best['effective_apr']:.2f}% effective APR{Style.RESET_ALL}")

            return best

        except Exception as e:
            logger.error(f"Error finding opportunities: {e}")
            return None

    async def _open_position(self, opportunity: Dict[str, Any]):
        """
        Open a delta-neutral position on the given opportunity.

        Args:
            opportunity: Dict with symbol and funding rate info
        """
        try:
            symbol = opportunity['symbol']
            logger.info(f"{Fore.YELLOW}{'='*80}{Style.RESET_ALL}")
            logger.info(f"{Fore.YELLOW}Opening position on {Fore.MAGENTA}{symbol}{Fore.YELLOW}...{Style.RESET_ALL}")
            logger.info(f"{Fore.YELLOW}{'='*80}{Style.RESET_ALL}")

            # Set leverage on the exchange for this symbol
            logger.info(f"Setting leverage to {Fore.MAGENTA}{self.leverage}x{Style.RESET_ALL} on {Fore.CYAN}{symbol}{Style.RESET_ALL}...")
            logger.debug(f"[LEVERAGE] Opening new position {symbol}: Setting leverage to {self.leverage}x on exchange")
            try:
                leverage_set = await self.api_manager.set_leverage(symbol, self.leverage)
                if leverage_set:
                    logger.info(f"{Fore.GREEN}Leverage set to {self.leverage}x successfully{Style.RESET_ALL}")
                    logger.debug(f"[LEVERAGE] Successfully set {symbol} leverage to {self.leverage}x")
                else:
                    logger.warning(f"Failed to set leverage to {self.leverage}x, continuing anyway...")
                    logger.debug(f"[LEVERAGE] Failed to set {symbol} leverage to {self.leverage}x (API returned False)")
            except Exception as leverage_error:
                logger.warning(f"Failed to set leverage: {leverage_error}")
                logger.warning("Continuing with current leverage setting...")
                logger.debug(f"[LEVERAGE] Exception setting {symbol} leverage: {leverage_error}", exc_info=True)

            # Rebalance USDT before opening to maximize available capital
            logger.info(f"Rebalancing USDT for {self.leverage}x leverage before opening position...")
            try:
                rebalance_result = await self.api_manager.rebalance_usdt_by_leverage(self.leverage)
                if rebalance_result.get('transfer_needed'):
                    direction = rebalance_result.get('transfer_direction')
                    amount = rebalance_result.get('transfer_amount', 0)
                    logger.info(f"{Fore.GREEN}Rebalanced ${amount:.2f} USDT ({direction}){Style.RESET_ALL}")
                    logger.info(f"  Spot USDT: ${rebalance_result.get('current_spot_usdt', 0):.2f} -> ${rebalance_result.get('target_spot_usdt', 0):.2f} ({rebalance_result.get('spot_target_pct', 0):.1f}%)")
                    logger.info(f"  Perp USDT: ${rebalance_result.get('current_perp_usdt', 0):.2f} -> ${rebalance_result.get('target_perp_usdt', 0):.2f} ({rebalance_result.get('perp_target_pct', 0):.1f}%)")
                else:
                    logger.info(f"USDT wallets already balanced for {self.leverage}x leverage (difference < $1)")
            except Exception as rebalance_error:
                logger.warning(f"Failed to rebalance USDT: {rebalance_error}")
                logger.warning("Continuing with current balances...")

            # Determine capital to deploy
            portfolio_data = await self.api_manager.get_comprehensive_portfolio_data()
            if not portfolio_data:
                logger.error("Failed to fetch portfolio data")
                return

            spot_balances = portfolio_data.get('spot_balances', [])
            perp_account_info = portfolio_data.get('perp_account_info', {})
            assets = perp_account_info.get('assets', [])

            spot_usdt = next((float(b.get('free', 0)) for b in spot_balances if b.get('asset') == 'USDT'), 0.0)
            perp_usdt = next((float(a.get('availableBalance', 0)) for a in assets if a.get('asset') == 'USDT'), 0.0)

            # Calculate maximum position size based on leverage and available balances
            # With leverage, we need to find the limiting factor between spot and perp wallets
            #
            # For a position of notional value N:
            # - Spot side needs: N worth of USDT to buy base asset
            # - Perp side needs: N / leverage worth of USDT as margin
            #
            # Therefore:
            # - Max position from spot wallet: spot_usdt
            # - Max position from perp wallet: perp_usdt * leverage
            #
            # The actual max position is the minimum of these two

            max_position_from_spot = spot_usdt
            max_position_from_perp = perp_usdt * self.leverage

            # The limiting factor determines max position size
            max_position_size = min(max_position_from_spot, max_position_from_perp)

            # Apply capital fraction
            capital_to_deploy = max_position_size * self.capital_fraction

            logger.info(f"{Fore.CYAN}Wallet balances:{Style.RESET_ALL}")
            logger.info(f"  Spot USDT: {Fore.GREEN}${spot_usdt:.2f}{Style.RESET_ALL} (max position: {Fore.MAGENTA}${max_position_from_spot:.2f}{Style.RESET_ALL})")
            logger.info(f"  Perp USDT: {Fore.GREEN}${perp_usdt:.2f}{Style.RESET_ALL} (max position: {Fore.MAGENTA}${max_position_from_perp:.2f}{Style.RESET_ALL} at {Fore.CYAN}{self.leverage}x{Style.RESET_ALL})")
            limiting_factor = 'SPOT' if max_position_from_spot < max_position_from_perp else 'PERP'
            logger.info(f"  Limiting factor: {Fore.YELLOW}{limiting_factor}{Style.RESET_ALL}")

            # Basic validation - ensure we have some capital
            if capital_to_deploy < 1.0:
                logger.error(f"Insufficient capital to deploy. Max position: ${max_position_size:.2f}, Deploying: ${capital_to_deploy:.2f}")
                logger.error(f"  Spot USDT: ${spot_usdt:.2f}")
                logger.error(f"  Perp USDT: ${perp_usdt:.2f}")
                return

            logger.info(f"Capital to deploy: {Fore.MAGENTA}${capital_to_deploy:.2f}{Style.RESET_ALL} ({Fore.CYAN}{self.capital_fraction*100:.0f}%{Style.RESET_ALL} of max {Fore.MAGENTA}${max_position_size:.2f}{Style.RESET_ALL} at {Fore.CYAN}{self.leverage}x{Style.RESET_ALL} leverage)")

            # Execute the trade
            result = await self.api_manager.prepare_and_execute_dn_position(
                symbol=symbol,
                capital_to_deploy=capital_to_deploy,
                leverage=self.leverage
            )

            if result.get('success'):
                # Calculate actual entry fees from trade details
                details = result.get('details', {})
                spot_qty = details.get('spot_qty_to_buy', 0)
                perp_qty = details.get('final_perp_qty', 0)
                spot_price = details.get('spot_price', 0)

                # Estimate fees: 0.05% maker + 0.05% taker per side (conservative)
                self.entry_fees_paid = (spot_qty * spot_price * 0.001) + (perp_qty * spot_price * 0.001)

                self.current_position = {
                    'symbol': symbol,
                    'capital': capital_to_deploy,
                    'funding_rate': opportunity['funding_rate'],
                    'effective_apr': opportunity['effective_apr'],
                    'spot_qty': spot_qty,
                    'perp_qty': perp_qty,
                    'entry_price': spot_price  # Save entry price for PnL calculations
                }
                self.position_opened_at = datetime.utcnow()
                self.position_leverage = self.leverage  # Track leverage used for this position
                self.total_funding_received = 0.0
                self.total_positions_opened += 1

                logger.info(f"{Fore.GREEN}{'='*80}{Style.RESET_ALL}")
                logger.info(f"{Fore.GREEN}✓ Position opened successfully!{Style.RESET_ALL}")
                logger.info(f"{Fore.GREEN}{'='*80}{Style.RESET_ALL}")
                logger.info(f"  Leverage: {Fore.MAGENTA}{self.position_leverage}x{Style.RESET_ALL}")
                logger.info(f"  Entry fees: {Fore.YELLOW}${self.entry_fees_paid:.2f}{Style.RESET_ALL}")
                logger.info(f"  Spot qty: {Fore.CYAN}{spot_qty:.8f}{Style.RESET_ALL}")
                logger.info(f"  Perp qty: {Fore.CYAN}{perp_qty:.8f}{Style.RESET_ALL}")
                logger.info(f"  Total positions opened: {Fore.MAGENTA}{self.total_positions_opened}{Style.RESET_ALL}")
                logger.debug(f"[LEVERAGE] Position {symbol} opened at {self.position_leverage}x leverage (saved to state)")

                # Save state immediately after opening
                self._save_state()
            else:
                logger.error(f"Failed to open position: {result.get('message')}")

        except Exception as e:
            logger.error(f"Error opening position: {e}", exc_info=True)

    async def _should_close_position(self) -> bool:
        """
        Determine if the current position should be closed.

        Reasons to close:
        1. Fees covered by funding payments (primary goal)
        2. Better opportunity available (significant APR difference)
        3. Position age exceeded
        4. Health issues detected
        5. Emergency stop loss triggered

        Returns:
            True if position should be closed
        """
        if not self.current_position:
            return False

        try:
            symbol = self.current_position['symbol']
            logger.info(f"{Fore.CYAN}Evaluating position on {Fore.MAGENTA}{symbol}{Fore.CYAN}...{Style.RESET_ALL}")

            # Get current position data
            portfolio_data = await self.api_manager.get_comprehensive_portfolio_data()
            analyzed_positions = portfolio_data.get('analyzed_positions', [])
            raw_perp_positions = portfolio_data.get('raw_perp_positions', [])

            # Find our position
            position_data = next((p for p in analyzed_positions if p.get('symbol') == symbol and p.get('is_delta_neutral')), None)
            if not position_data:
                logger.warning(f"Position {symbol} not found in analyzed positions")
                return True  # Close if we can't find it

            # Check 1: Emergency stop loss
            perp_pos = next((p for p in raw_perp_positions if p.get('symbol') == symbol), None)
            if perp_pos:
                perp_unrealized_pnl = float(perp_pos.get('unrealizedProfit', 0))
                entry_value = self.current_position.get('capital', 1)
                perp_pnl_pct = (perp_unrealized_pnl / entry_value) * 100 if entry_value > 0 else 0

                # Calculate spot unrealized PnL
                spot_unrealized_pnl = 0.0
                combined_unrealized_pnl = perp_unrealized_pnl
                combined_pnl_pct = perp_pnl_pct

                if perp_pos.get('markPrice'):
                    mark_price = float(perp_pos['markPrice'])
                    spot_balance = position_data.get('spot_balance', 0)

                    # Get entry price from position state, or fallback to perp entry price
                    entry_price = self.current_position.get('entry_price', 0)
                    if entry_price == 0:
                        # Fallback: use perp entry price (same for both spot and perp in DN strategy)
                        entry_price = float(perp_pos.get('entryPrice', 0))
                        # Update state with entry price for future use
                        if entry_price > 0:
                            self.current_position['entry_price'] = entry_price
                            self._save_state()

                    if entry_price > 0 and spot_balance > 0:
                        # Spot PnL = current_qty * (current_price - entry_price)
                        spot_unrealized_pnl = spot_balance * (mark_price - entry_price)

                    # Combined DN PnL (includes funding and fees)
                    # = Spot PnL + Perp PnL + Funding Received - Entry Fees - Exit Fees
                    position_value = self.current_position.get('capital', 0)
                    exit_fees_estimate = position_value * 0.001  # 0.1% total exit fees

                    combined_unrealized_pnl = (
                        spot_unrealized_pnl +
                        perp_unrealized_pnl +
                        self.total_funding_received -
                        self.entry_fees_paid -
                        exit_fees_estimate
                    )
                    combined_pnl_pct = (combined_unrealized_pnl / entry_value) * 100 if entry_value > 0 else 0

                # Emergency stop loss check (use perp PnL for trigger as it's more volatile)
                if perp_pnl_pct <= self.emergency_stop_loss_pct:
                    logger.error(f"{Fore.RED}{'='*80}{Style.RESET_ALL}")
                    logger.error(f"{Fore.RED}⚠️  EMERGENCY STOP LOSS TRIGGERED!{Style.RESET_ALL}")
                    logger.error(f"{Fore.RED}Perp PnL: {perp_pnl_pct:.2f}% (threshold: {self.emergency_stop_loss_pct}%){Style.RESET_ALL}")
                    logger.error(f"{Fore.RED}{'='*80}{Style.RESET_ALL}")
                    return True

                # Log all PnL components with color coding
                perp_pnl_color = Fore.GREEN if perp_unrealized_pnl >= 0 else Fore.RED
                spot_pnl_color = Fore.GREEN if spot_unrealized_pnl >= 0 else Fore.RED
                combined_pnl_color = Fore.GREEN if combined_unrealized_pnl >= 0 else Fore.RED

                logger.info(f"  Perp Unrealized PnL: {perp_pnl_color}${perp_unrealized_pnl:.2f} ({perp_pnl_pct:.2f}%){Style.RESET_ALL} -> used for stoploss trigger at {Fore.RED}{self.emergency_stop_loss_pct}%{Style.RESET_ALL}")
                logger.info(f"  Spot Unrealized PnL: {spot_pnl_color}${spot_unrealized_pnl:.2f}{Style.RESET_ALL}")
                logger.info(f"  Combined DN PnL (net): {combined_pnl_color}${combined_unrealized_pnl:.2f} ({combined_pnl_pct:.2f}%){Style.RESET_ALL} {Fore.YELLOW}[includes funding & fees]{Style.RESET_ALL}")

                # Calculate and log delta-neutral position size
                if perp_pos.get('markPrice'):
                    mark_price = float(perp_pos['markPrice'])
                    spot_balance = position_data.get('spot_balance', 0)
                    spot_notional = spot_balance * mark_price
                    perp_notional = abs(float(perp_pos.get('notional', 0)))

                    # Per user request: size = spot_notional + abs(perp_notional) + unrealized_pnl
                    total_dn_size = spot_notional + perp_notional + perp_unrealized_pnl
                    logger.info(f"  Delta-neutral position size: {Fore.MAGENTA}${total_dn_size:.2f}{Style.RESET_ALL} (Spot: {Fore.CYAN}${spot_notional:.2f}{Style.RESET_ALL}, Perp: {Fore.CYAN}${perp_notional:.2f}{Style.RESET_ALL})")

            # Check 2: Calculate funding received
            # Try to fetch actual funding from API first, fallback to estimate
            api_funding_available = False
            try:
                funding_analysis = await self.api_manager.perform_funding_analysis(symbol)
                if funding_analysis:
                    # Use actual funding from API (source of truth)
                    actual_funding = float(funding_analysis.get('total_funding', 0))
                    self.total_funding_received = actual_funding
                    api_funding_available = True
                    logger.info(f"  Actual funding from API: ${actual_funding:.4f}")
            except Exception as e:
                logger.debug(f"Could not fetch funding from API: {e}")

            # Calculate position age
            if not self.position_opened_at:
                logger.error("Position opened time is None, cannot calculate age")
                return True  # Close position if we can't track it properly

            time_elapsed = datetime.utcnow() - self.position_opened_at
            hours_elapsed = time_elapsed.total_seconds() / 3600
            funding_periods_elapsed = hours_elapsed / 8  # Funding every 8 hours

            # Get position value for fee calculations
            position_value = self.current_position.get('capital', 0)

            # If we don't have API data, estimate funding
            if not api_funding_available and hours_elapsed > 0:
                funding_rate = self.current_position.get('funding_rate', 0)
                estimated_funding = funding_rate * position_value * funding_periods_elapsed
                self.total_funding_received = estimated_funding
                logger.info(f"  Estimated funding (no API data): ${estimated_funding:.4f}")

            exit_fees_estimate = position_value * 0.001  # 0.1% total exit fees
            total_fees = self.entry_fees_paid + exit_fees_estimate

            fees_coverage_ratio = self.total_funding_received / total_fees if total_fees > 0 else 0

            logger.info(f"  Position age: {Fore.CYAN}{hours_elapsed:.2f} hours{Style.RESET_ALL} (since {Fore.YELLOW}{self.position_opened_at.strftime('%Y-%m-%d %H:%M:%S')} UTC{Style.RESET_ALL})")
            logger.info(f"  Funding periods: {Fore.CYAN}{funding_periods_elapsed:.2f}{Style.RESET_ALL}")
            logger.info(f"  Estimated funding received: {Fore.GREEN}${self.total_funding_received:.4f}{Style.RESET_ALL}")
            logger.info(f"  Total fees (entry + exit): {Fore.YELLOW}${total_fees:.4f}{Style.RESET_ALL}")

            # Progress bar for fees coverage
            progress = min(fees_coverage_ratio / self.fee_coverage_multiplier, 1.0)
            bar_length = 25
            filled_length = int(bar_length * progress)
            bar = '█' * filled_length + '-' * (bar_length - filled_length)
            progress_percentage = progress * 100

            # Color the bar based on progress
            if progress >= 1.0:
                bar_color = Fore.GREEN
            elif progress >= 0.75:
                bar_color = Fore.YELLOW
            else:
                bar_color = Fore.CYAN

            progress_bar_message = (
                f"  Fees coverage: [{bar_color}{bar}{Style.RESET_ALL}] {Fore.MAGENTA}{progress_percentage:.1f}%{Style.RESET_ALL} "
                f"({Fore.CYAN}{fees_coverage_ratio:.2f}x{Style.RESET_ALL} / {Fore.GREEN}{self.fee_coverage_multiplier}x{Style.RESET_ALL})"
            )
            logger.info(progress_bar_message)

            if fees_coverage_ratio >= self.fee_coverage_multiplier:
                logger.info(f"{Fore.GREEN}Fees covered! Ready to close and rotate.{Style.RESET_ALL}")
                return True

            # Check 3: Better opportunity available (significant difference)
            current_best = await self._find_best_funding_opportunity()
            if current_best:
                current_apr = self.current_position.get('effective_apr', 0)
                new_apr = current_best.get('effective_apr', 0)
                apr_improvement = new_apr - current_apr

                # Only rotate if improvement is > 10% APR points AND we've held for at least 4 hours
                if apr_improvement > 10.0 and hours_elapsed >= 4.0:
                    logger.info(f"{Fore.YELLOW}Better opportunity found: {Fore.MAGENTA}{current_best['symbol']}{Style.RESET_ALL} ({Fore.GREEN}{new_apr:.2f}%{Style.RESET_ALL} vs {Fore.CYAN}{current_apr:.2f}%{Style.RESET_ALL}) - improvement: {Fore.GREEN}+{apr_improvement:.2f}%{Style.RESET_ALL}")
                    return True

            # Check 4: Position age exceeded
            if time_elapsed > self.max_position_age:
                logger.warning(f"{Fore.YELLOW}Position age exceeded {Fore.MAGENTA}{self.max_position_age.total_seconds()/3600:.1f}{Fore.YELLOW} hours - rotating...{Style.RESET_ALL}")
                return True

            # Check 5: Health issues
            health_issues, critical_issues, _, position_pnl_data = await self.api_manager.perform_health_check_analysis()
            if critical_issues:
                logger.error(f"Critical health issues: {critical_issues}")
                return True

            # Find our position in health data
            our_health = next((p for p in position_pnl_data if p['symbol'] == symbol), None)
            if our_health:
                imbalance_pct = our_health.get('imbalance_pct', 0)
                if abs(imbalance_pct) > 10.0:  # More than 10% imbalance
                    logger.warning(f"Position imbalance too high: {imbalance_pct:.2f}%")
                    return True

            logger.info(f"{Fore.CYAN}Position healthy, continuing to hold...{Style.RESET_ALL}")
            return False

        except Exception as e:
            logger.error(f"Error evaluating position: {e}")
            return True  # Close on error to be safe

    async def _close_current_position(self):
        """Close the current position."""
        if not self.current_position:
            return

        try:
            symbol = self.current_position['symbol']
            logger.info(f"{Fore.YELLOW}{'='*80}{Style.RESET_ALL}")
            logger.info(f"{Fore.YELLOW}Closing position on {Fore.MAGENTA}{symbol}{Fore.YELLOW}...{Style.RESET_ALL}")
            logger.info(f"{Fore.YELLOW}{'='*80}{Style.RESET_ALL}")

            result = await self.api_manager.execute_dn_position_close(symbol)

            if result.get('success'):
                # Calculate net profit/loss
                net_profit = self.total_funding_received - self.entry_fees_paid
                self.total_profit_loss += net_profit
                self.total_positions_closed += 1
                self.cycle_count += 1  # Increment trading cycle on successful close

                logger.info(f"{Fore.GREEN}{'='*80}{Style.RESET_ALL}")
                logger.info(f"{Fore.GREEN}✓ Position closed successfully! (Trading Cycle #{self.cycle_count} Completed){Style.RESET_ALL}")
                logger.info(f"{Fore.GREEN}{'='*80}{Style.RESET_ALL}")
                logger.info(f"  Total funding received: {Fore.GREEN}${self.total_funding_received:.4f}{Style.RESET_ALL}")
                logger.info(f"  Total fees paid: {Fore.YELLOW}${self.entry_fees_paid:.4f}{Style.RESET_ALL}")

                net_profit_color = Fore.GREEN if net_profit >= 0 else Fore.RED
                logger.info(f"  Net profit (this position): {net_profit_color}${net_profit:.4f}{Style.RESET_ALL}")

                cumulative_pnl_color = Fore.GREEN if self.total_profit_loss >= 0 else Fore.RED
                logger.info(f"  Cumulative P/L: {cumulative_pnl_color}${self.total_profit_loss:.4f}{Style.RESET_ALL}")
                logger.info(f"  Total positions closed: {Fore.MAGENTA}{self.total_positions_closed}{Style.RESET_ALL}")

                # Reset position tracking
                closed_symbol = symbol
                closed_leverage = self.position_leverage
                self.current_position = None
                self.position_opened_at = None
                self.position_leverage = None
                self.total_funding_received = 0.0
                self.entry_fees_paid = 0.0

                # Save state immediately after closing
                self._save_state()
                logger.debug(f"[LEVERAGE] Position {closed_symbol} closed (was at {closed_leverage}x leverage)")

                # Automatically rebalance USDT based on NEW config leverage (for next position)
                # This allows leverage changes to take effect after closing current position
                if closed_leverage and closed_leverage != self.leverage:
                    logger.info(f"Leverage changed from {closed_leverage}x to {self.leverage}x - rebalancing for new positions")
                    logger.debug(f"[LEVERAGE] Transitioning from {closed_leverage}x to {self.leverage}x leverage")

                logger.info(f"Rebalancing USDT for {self.leverage}x leverage (next position) between spot and perp wallets...")
                logger.debug(f"[LEVERAGE] Rebalancing USDT for {self.leverage}x leverage (next position will use this)")
                try:
                    rebalance_result = await self.api_manager.rebalance_usdt_by_leverage(self.leverage)
                    if rebalance_result.get('transfer_needed'):
                        direction = rebalance_result.get('transfer_direction')
                        amount = rebalance_result.get('transfer_amount', 0)
                        logger.info(f"{Fore.GREEN}Rebalanced ${amount:.2f} USDT ({direction}){Style.RESET_ALL}")
                        logger.info(f"  Spot USDT: ${rebalance_result.get('current_spot_usdt', 0):.2f} -> ${rebalance_result.get('target_spot_usdt', 0):.2f} ({rebalance_result.get('spot_target_pct', 0):.1f}%)")
                        logger.info(f"  Perp USDT: ${rebalance_result.get('current_perp_usdt', 0):.2f} -> ${rebalance_result.get('target_perp_usdt', 0):.2f} ({rebalance_result.get('perp_target_pct', 0):.1f}%)")
                    else:
                        logger.info(f"USDT wallets already balanced for {self.leverage}x leverage (difference < $1)")
                except Exception as rebalance_error:
                    logger.warning(f"Failed to rebalance USDT: {rebalance_error}")
                    logger.warning("Continuing anyway - you may want to manually rebalance")
            else:
                logger.error(f"Failed to close position: {result.get('message')}")

        except Exception as e:
            logger.error(f"Error closing position: {e}", exc_info=True)

    async def _shutdown(self):
        """Graceful shutdown with position cleanup."""
        logger.info(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}")
        logger.info(f"{Fore.CYAN}Shutting down strategy...{Style.RESET_ALL}")
        logger.info(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}")

        # Save final state before shutdown
        self._save_state()
        logger.info(f"{Fore.GREEN}Final state saved to {self.state_file}{Style.RESET_ALL}")

        # Close any open positions (optional - user can choose to keep them open)
        if self.current_position:
            logger.warning(f"{Fore.YELLOW}Open position on {self.current_position['symbol']} will remain open{Style.RESET_ALL}")
            logger.warning(f"{Fore.YELLOW}State has been saved. Restart the strategy to continue monitoring.{Style.RESET_ALL}")
            logger.info("To force-close on shutdown, modify the code in _shutdown()")

        # Close API connections
        await self.api_manager.close()

        logger.info(f"{Fore.CYAN}Trading cycles completed: {Fore.MAGENTA}{self.cycle_count}{Fore.CYAN} (open → hold → close){Style.RESET_ALL}")
        logger.info(f"Total positions opened: {Fore.MAGENTA}{self.total_positions_opened}{Style.RESET_ALL}")
        logger.info(f"Total positions closed: {Fore.MAGENTA}{self.total_positions_closed}{Style.RESET_ALL}")

        cumulative_pnl_color = Fore.GREEN if self.total_profit_loss >= 0 else Fore.RED
        logger.info(f"Cumulative P/L: {cumulative_pnl_color}${self.total_profit_loss:.4f}{Style.RESET_ALL}")

        logger.info(f"{Fore.GREEN}Shutdown complete{Style.RESET_ALL}")


def load_config(config_file: str = 'config_volume_farming_strategy.json') -> Dict[str, Any]:
    """
    Load configuration from JSON file.

    Args:
        config_file: Path to config file

    Returns:
        Dict with configuration values
    """
    default_config = {
        'capital_fraction': 0.95,
        'min_funding_apr': 15.0,
        'fee_coverage_multiplier': 1.5,
        'loop_interval_seconds': 300,
        'max_position_age_hours': 24,
        'use_funding_ma': True,
        'funding_ma_periods': 10,
        'leverage': 1
    }

    if not os.path.exists(config_file):
        logger.info(f"Config file {config_file} not found, using defaults")
        return default_config

    try:
        with open(config_file, 'r') as f:
            config_data = json.load(f)

        # Extract values from nested structure
        config = default_config.copy()

        # Capital management
        if 'capital_management' in config_data:
            cm = config_data['capital_management']
            config['capital_fraction'] = cm.get('capital_fraction', config['capital_fraction'])

        # Funding rate strategy
        if 'funding_rate_strategy' in config_data:
            frs = config_data['funding_rate_strategy']
            config['min_funding_apr'] = frs.get('min_funding_apr', config['min_funding_apr'])
            config['use_funding_ma'] = frs.get('use_funding_ma', config['use_funding_ma'])
            config['funding_ma_periods'] = frs.get('funding_ma_periods', config['funding_ma_periods'])

        # Position management
        if 'position_management' in config_data:
            pm = config_data['position_management']
            config['fee_coverage_multiplier'] = pm.get('fee_coverage_multiplier', config['fee_coverage_multiplier'])
            config['max_position_age_hours'] = pm.get('max_position_age_hours', config['max_position_age_hours'])
            config['loop_interval_seconds'] = pm.get('loop_interval_seconds', config['loop_interval_seconds'])

        # Leverage settings (support both old 'risk_management' and new 'leverage_settings' for backward compatibility)
        if 'leverage_settings' in config_data:
            ls = config_data['leverage_settings']
            config['leverage'] = ls.get('leverage', config['leverage'])
        elif 'risk_management' in config_data:
            # Backward compatibility with old config format
            rm = config_data['risk_management']
            config['leverage'] = rm.get('leverage', config['leverage'])
            logger.info("Note: 'risk_management' section is deprecated, use 'leverage_settings' instead")

        # Validate leverage
        if config['leverage'] < 1 or config['leverage'] > 3:
            logger.warning(f"Invalid leverage {config['leverage']} in config, must be 1-3. Using default: 1")
            config['leverage'] = 1

        # Note: emergency_stop_loss_pct is now calculated automatically based on leverage

        logger.info(f"Configuration loaded from {config_file}")
        return config

    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in config file: {e}")
        logger.info("Using default configuration")
        return default_config
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        logger.info("Using default configuration")
        return default_config


async def main():
    """Entry point for the volume farming strategy."""
    # Load configuration from config file
    config = load_config('config_volume_farming_strategy.json')

    # Validate environment variables
    required_vars = ['API_USER', 'API_SIGNER', 'API_PRIVATE_KEY', 'APIV1_PUBLIC_KEY', 'APIV1_PRIVATE_KEY']
    if not all(os.getenv(var) for var in required_vars):
        logger.error("ERROR: Not all required environment variables are set in your .env file.")
        logger.error("Please ensure API_USER, API_SIGNER, API_PRIVATE_KEY, APIV1_PUBLIC_KEY, and APIV1_PRIVATE_KEY are configured.")
        sys.exit(1)

    # Create and run strategy with config values
    strategy = VolumeFarmingStrategy(
        capital_fraction=config['capital_fraction'],
        min_funding_apr=config['min_funding_apr'],
        fee_coverage_multiplier=config['fee_coverage_multiplier'],
        loop_interval_seconds=config['loop_interval_seconds'],
        max_position_age_hours=config['max_position_age_hours'],
        use_funding_ma=config['use_funding_ma'],
        funding_ma_periods=config['funding_ma_periods'],
        leverage=config['leverage']
    )

    try:
        await strategy.run()
    except KeyboardInterrupt:
        logger.info("\nReceived shutdown signal...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())