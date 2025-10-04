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
        emergency_stop_loss_pct: float = -10.0,
        use_funding_ma: bool = True,
        funding_ma_periods: int = 10
    ):
        """
        Initialize the volume farming strategy.

        Args:
            capital_fraction: Fraction of total available USDT to deploy (0.95 = 95%)
            min_funding_apr: Minimum annualized funding APR to consider (%)
            fee_coverage_multiplier: Multiplier for fee coverage (1.5 = 150% of fees)
            loop_interval_seconds: Seconds between strategy loop cycles
            max_position_age_hours: Maximum hours to hold a position
            emergency_stop_loss_pct: Emergency stop loss percentage
            use_funding_ma: Use moving average of funding rates instead of instantaneous
            funding_ma_periods: Number of periods for funding rate moving average
        """
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
        self.emergency_stop_loss_pct = emergency_stop_loss_pct
        self.use_funding_ma = use_funding_ma
        self.funding_ma_periods = funding_ma_periods

        # State tracking
        self.state_file = 'volume_farming_state.json'
        self.current_position: Optional[Dict[str, Any]] = None
        self.position_opened_at: Optional[datetime] = None
        self.total_funding_received: float = 0.0
        self.entry_fees_paid: float = 0.0
        self.running = True
        self.cycle_count = 0
        self.total_profit_loss: float = 0.0
        self.total_positions_opened: int = 0
        self.total_positions_closed: int = 0

        # Load persisted state if available
        self._load_state()

        logger.info(f"Volume Farming Strategy initialized")
        logger.info(f"Capital Fraction: {capital_fraction*100:.0f}% of available USDT")
        logger.info(f"Min Funding APR: {min_funding_apr}%")
        logger.info(f"Fee Coverage Multiplier: {fee_coverage_multiplier}x")
        logger.info(f"Funding Rate Mode: {'Moving Average (' + str(funding_ma_periods) + ' periods)' if use_funding_ma else 'Instantaneous'}")

        if self.current_position:
            logger.info(f"{Fore.YELLOW}Recovered open position: {self.current_position['symbol']}{Style.RESET_ALL}")
            logger.info(f"  Opened at: {self.position_opened_at}")
            logger.info(f"  Entry fees: ${self.entry_fees_paid:.4f}")

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
            logger.info(f"  Total cycles: {self.cycle_count}")
            logger.info(f"  Total positions opened: {self.total_positions_opened}")
            logger.info(f"  Total positions closed: {self.total_positions_closed}")
            logger.info(f"  Cumulative P/L: ${self.total_profit_loss:.4f}")

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
                'total_funding_received': self.total_funding_received,
                'entry_fees_paid': self.entry_fees_paid,
                'cycle_count': self.cycle_count,
                'total_profit_loss': self.total_profit_loss,
                'total_positions_opened': self.total_positions_opened,
                'total_positions_closed': self.total_positions_closed,
                'last_updated': datetime.now().isoformat()
            }

            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)

            logger.debug(f"State saved to {self.state_file}")

        except Exception as e:
            logger.error(f"Failed to save state: {e}")

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

            logger.info(f"{Fore.YELLOW}Discovered existing position: {symbol}{Style.RESET_ALL}")
            logger.info(f"  Spot balance: {existing_pos.get('spot_balance', 0):.6f}")
            logger.info(f"  Perp position: {existing_pos.get('perp_position', 0):.6f}")
            logger.info(f"  Position value: ${existing_pos.get('position_value_usd', 0):.2f}")

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
                    position_opened_at = datetime.now()

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

                # Adopt this position
                self.current_position = {
                    'symbol': symbol,
                    'capital': position_value,
                    'funding_rate': funding_rate,
                    'effective_apr': effective_apr,
                    'spot_qty': existing_pos.get('spot_balance', 0),
                    'perp_qty': abs(existing_pos.get('perp_position', 0))
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
                    self.total_funding_received = 0.0
                    self.entry_fees_paid = 0.0
                    await self._discover_existing_position()
                    return

                # Position matches - update funding data from exchange
                logger.info(f"{Fore.GREEN}Position {tracked_symbol} confirmed on exchange{Style.RESET_ALL}")

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

        try:
            while self.running:
                self.cycle_count += 1
                logger.info(f"\n{'='*80}")
                logger.info(f"CYCLE #{self.cycle_count} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                logger.info(f"{'='*80}")

                # Step 1: Perform health check
                if not await self._perform_health_check():
                    logger.warning("Health check failed. Waiting before retry...")
                    await asyncio.sleep(self.loop_interval_seconds)
                    continue

                # Step 2: Check if we have an open position
                if self.current_position:
                    # Monitor existing position
                    should_close = await self._should_close_position()
                    if should_close:
                        await self._close_current_position()
                    else:
                        logger.info(f"Holding position on {self.current_position['symbol']}")
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
            logger.info("Performing health check...")

            # Check account balances
            portfolio_data = await self.api_manager.get_comprehensive_portfolio_data()
            if not portfolio_data:
                logger.error("Failed to fetch portfolio data")
                return False

            spot_balances = portfolio_data.get('spot_balances', [])
            perp_account_info = portfolio_data.get('perp_account_info', {})
            assets = perp_account_info.get('assets', [])

            # Get USDT balances
            spot_usdt = next((float(b.get('free', 0)) for b in spot_balances if b.get('asset') == 'USDT'), 0.0)
            perp_usdt = next((float(a.get('availableBalance', 0)) for a in assets if a.get('asset') == 'USDT'), 0.0)

            logger.info(f"Spot USDT: ${spot_usdt:.2f}")
            logger.info(f"Perp USDT: ${perp_usdt:.2f}")

            # Basic balance check - just ensure we have some USDT available
            if not self.current_position:
                if spot_usdt < 1.0 or perp_usdt < 1.0:
                    logger.error(f"Insufficient balance. Spot: ${spot_usdt:.2f}, Perp: ${perp_usdt:.2f}")
                    return False

            # Check existing positions health
            health_issues, critical_issues, dn_count, _ = await self.api_manager.perform_health_check_analysis()
            if critical_issues:
                logger.error(f"Critical health issues detected: {critical_issues}")
                return False

            if health_issues:
                logger.warning(f"Health warnings: {health_issues}")

            logger.info(f"Health check passed (Existing DN positions: {dn_count})")
            return True

        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    async def _find_best_funding_opportunity(self) -> Optional[Dict[str, Any]]:
        """
        Scan all available pairs and find the best funding rate opportunity.
        Uses moving average if enabled, otherwise uses instantaneous rates.

        Returns:
            Dict with symbol and funding rate info, or None if no opportunity
        """
        try:
            if self.use_funding_ma:
                logger.info(f"Scanning for best funding rate opportunity (MA {self.funding_ma_periods} periods)...")
            else:
                logger.info("Scanning for best funding rate opportunity (instantaneous)...")

            # Get funding rates based on mode
            if self.use_funding_ma:
                # Use moving average funding rates
                funding_rates_ma = await self.api_manager.get_all_funding_rates_ma(self.funding_ma_periods)
                if not funding_rates_ma:
                    logger.warning("No MA funding rates available")
                    return None

                # Convert MA format to standard format
                funding_rates = []
                for ma_data in funding_rates_ma:
                    funding_rates.append({
                        'symbol': ma_data['symbol'],
                        'funding_rate': ma_data['ma_rate'],  # Use MA rate instead of current
                        'effective_apr': ma_data['effective_ma_apr'],
                        'next_funding_time': ma_data['next_funding_time'],
                        'ma_periods': ma_data['ma_periods'],
                        'ma_stdev': ma_data['stdev'],
                        'using_ma': True
                    })
            else:
                # Use instantaneous funding rates
                funding_rates_data = await self.api_manager.get_all_funding_rates()
                if not funding_rates_data:
                    logger.warning("No funding rates available")
                    return None

                # Convert to expected format
                funding_rates = []
                for rate_data in funding_rates_data:
                    funding_rates.append({
                        'symbol': rate_data['symbol'],
                        'funding_rate': rate_data['rate'],
                        'effective_apr': rate_data['apr'] / 2,  # Effective APR for 1x leverage
                        'next_funding_time': None,
                        'using_ma': False
                    })

            # Get available delta-neutral pairs
            available_pairs = await self.api_manager.discover_delta_neutral_pairs()
            if not available_pairs:
                logger.warning("No delta-neutral pairs available")
                return None

            # Show ALL available pairs (including currently held position)
            all_candidates = [
                rate for rate in funding_rates
                if rate['symbol'] in available_pairs
            ]

            if not all_candidates:
                logger.warning("No delta-neutral pairs available")
                return None

            # Get current position symbol if any
            current_symbol = self.current_position['symbol'] if self.current_position else None

            # Sort all by effective APR (descending) for display
            all_candidates.sort(key=lambda x: x['effective_apr'], reverse=True)

            # Display table of ALL available rates
            logger.info(f"\n{Fore.CYAN}Funding Rate Scan Results:{Style.RESET_ALL}")
            logger.info("=" * 100)

            if all_candidates[0].get('using_ma'):
                # MA mode - show MA rate and stdev
                header = f"{'Symbol':<12} {'MA Rate %':<12} {'Eff APR %':<12} {'StDev %':<12} {'Next Funding':<20} {'Status':<15}"
                logger.info(header)
                logger.info("-" * 100)

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
                    ma_rate = f"{c['funding_rate']*100:>11.4f}"
                    eff_apr = f"{c['effective_apr']:>11.2f}"
                    stdev = f"{c.get('ma_stdev', 0)*100:>11.4f}"
                    next_funding = str(c.get('next_funding_time', 'N/A'))[:19]

                    logger.info(f"{color}{symbol_display} {ma_rate} {eff_apr} {stdev} {next_funding:<20} {status:<15}{Style.RESET_ALL}")
            else:
                # Instantaneous mode - simpler table
                header = f"{'Symbol':<12} {'Rate %':<12} {'Eff APR %':<12} {'Next Funding':<20} {'Status':<15}"
                logger.info(header)
                logger.info("-" * 100)

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
                    rate = f"{c['funding_rate']*100:>11.4f}"
                    eff_apr = f"{c['effective_apr']:>11.2f}"
                    next_funding = str(c.get('next_funding_time', 'N/A'))[:19]

                    logger.info(f"{color}{symbol_display} {rate} {eff_apr} {next_funding:<20} {status:<15}{Style.RESET_ALL}")

            logger.info("=" * 100)

            # Filter by minimum APR threshold (effective APR for 1x leverage)
            candidates = [
                c for c in all_candidates
                if c['effective_apr'] >= self.min_funding_apr
            ]

            if not candidates:
                logger.warning(f"\n{Fore.RED}No pairs meet minimum APR threshold of {self.min_funding_apr}%{Style.RESET_ALL}")
                return None

            # Announce selection
            best = candidates[0]
            logger.info(f"\n{Fore.GREEN}>>> Selected: {best['symbol']} with {best['effective_apr']:.2f}% effective APR{Style.RESET_ALL}")

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
            logger.info(f"Opening position on {symbol}...")

            # Rebalance USDT before opening to maximize available capital
            logger.info("Rebalancing USDT before opening position...")
            try:
                rebalance_result = await self.api_manager.rebalance_usdt_50_50()
                if rebalance_result.get('transfer_needed'):
                    direction = rebalance_result.get('transfer_direction')
                    amount = rebalance_result.get('transfer_amount', 0)
                    logger.info(f"{Fore.GREEN}Rebalanced ${amount:.2f} USDT ({direction}){Style.RESET_ALL}")
                    logger.info(f"  Spot USDT: ${rebalance_result.get('current_spot_usdt', 0):.2f} -> ${rebalance_result.get('target_each', 0):.2f}")
                    logger.info(f"  Perp USDT: ${rebalance_result.get('current_perp_usdt', 0):.2f} -> ${rebalance_result.get('target_each', 0):.2f}")
                else:
                    logger.info("USDT wallets already balanced (difference < $1)")
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

            # After rebalancing, both should be equal (or close), use minimum to be safe
            available_capital = min(spot_usdt, perp_usdt)

            # Apply capital fraction
            capital_to_deploy = available_capital * self.capital_fraction

            # Basic validation - ensure we have some capital
            if capital_to_deploy < 1.0:
                logger.error(f"Insufficient capital to deploy. Available: ${available_capital:.2f}, Deploying: ${capital_to_deploy:.2f}")
                logger.error(f"  Spot USDT: ${spot_usdt:.2f}")
                logger.error(f"  Perp USDT: ${perp_usdt:.2f}")
                return

            logger.info(f"Capital to deploy: ${capital_to_deploy:.2f} ({self.capital_fraction*100:.0f}% of ${available_capital:.2f})")

            # Execute the trade
            result = await self.api_manager.prepare_and_execute_dn_position(
                symbol=symbol,
                capital_to_deploy=capital_to_deploy
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
                    'perp_qty': perp_qty
                }
                self.position_opened_at = datetime.now()
                self.total_funding_received = 0.0
                self.total_positions_opened += 1

                logger.info(f"{Fore.GREEN}Position opened successfully!{Style.RESET_ALL}")
                logger.info(f"  Entry fees: ${self.entry_fees_paid:.2f}")
                logger.info(f"  Spot qty: {spot_qty:.8f}")
                logger.info(f"  Perp qty: {perp_qty:.8f}")
                logger.info(f"  Total positions opened: {self.total_positions_opened}")

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
            logger.info(f"Evaluating position on {symbol}...")

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
                unrealized_pnl = float(perp_pos.get('unrealizedProfit', 0))
                entry_value = self.current_position.get('capital', 1)
                pnl_pct = (unrealized_pnl / entry_value) * 100 if entry_value > 0 else 0

                if pnl_pct <= self.emergency_stop_loss_pct:
                    logger.error(f"Emergency stop loss triggered! PnL: {pnl_pct:.2f}%")
                    return True

                logger.info(f"  Unrealized PnL: ${unrealized_pnl:.2f} ({pnl_pct:.2f}%)")

                # Calculate and log delta-neutral position size
                if perp_pos.get('markPrice'):
                    mark_price = float(perp_pos['markPrice'])
                    spot_balance = position_data.get('spot_balance', 0)
                    spot_notional = spot_balance * mark_price
                    perp_notional = abs(float(perp_pos.get('notional', 0)))

                    # Per user request: size = spot_notional + abs(perp_notional) + unrealized_pnl
                    total_dn_size = spot_notional + perp_notional + unrealized_pnl
                    logger.info(f"  Delta-neutral position size: ${total_dn_size:.2f} (Spot: ${spot_notional:.2f}, Perp: ${perp_notional:.2f}, PnL: ${unrealized_pnl:.2f})")

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

            time_elapsed = datetime.now() - self.position_opened_at
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

            logger.info(f"  Position age: {hours_elapsed:.2f} hours")
            logger.info(f"  Funding periods: {funding_periods_elapsed:.2f}")
            logger.info(f"  Estimated funding received: ${self.total_funding_received:.4f}")
            logger.info(f"  Total fees (entry + exit): ${total_fees:.4f}")

            # Progress bar for fees coverage
            progress = min(fees_coverage_ratio / self.fee_coverage_multiplier, 1.0)
            bar_length = 25
            filled_length = int(bar_length * progress)
            bar = '█' * filled_length + '-' * (bar_length - filled_length)
            progress_percentage = progress * 100

            progress_bar_message = (
                f"  Fees coverage: [{Fore.GREEN}{bar}{Style.RESET_ALL}] {progress_percentage:.1f}% "
                f"({fees_coverage_ratio:.2f}x / {self.fee_coverage_multiplier}x)"
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
                    logger.info(f"{Fore.YELLOW}Better opportunity found: {current_best['symbol']} ({new_apr:.2f}% vs {current_apr:.2f}%){Style.RESET_ALL}")
                    return True

            # Check 4: Position age exceeded
            if time_elapsed > self.max_position_age:
                logger.warning(f"Position age exceeded {self.max_position_age.total_seconds()/3600:.1f} hours")
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
            logger.info(f"Closing position on {symbol}...")

            result = await self.api_manager.execute_dn_position_close(symbol)

            if result.get('success'):
                # Calculate net profit/loss
                net_profit = self.total_funding_received - self.entry_fees_paid
                self.total_profit_loss += net_profit
                self.total_positions_closed += 1

                logger.info(f"{Fore.GREEN}Position closed successfully!{Style.RESET_ALL}")
                logger.info(f"  Total funding received: ${self.total_funding_received:.4f}")
                logger.info(f"  Total fees paid: ${self.entry_fees_paid:.4f}")
                logger.info(f"  Net profit (this position): ${net_profit:.4f}")
                logger.info(f"  Cumulative P/L: ${self.total_profit_loss:.4f}")
                logger.info(f"  Total positions closed: {self.total_positions_closed}")

                # Reset position tracking
                self.current_position = None
                self.position_opened_at = None
                self.total_funding_received = 0.0
                self.entry_fees_paid = 0.0

                # Save state immediately after closing
                self._save_state()

                # Automatically rebalance USDT 50/50 between spot and perp
                logger.info("Rebalancing USDT between spot and perp wallets...")
                try:
                    rebalance_result = await self.api_manager.rebalance_usdt_50_50()
                    if rebalance_result.get('transfer_needed'):
                        direction = rebalance_result.get('transfer_direction')
                        amount = rebalance_result.get('transfer_amount', 0)
                        logger.info(f"{Fore.GREEN}Rebalanced ${amount:.2f} USDT ({direction}){Style.RESET_ALL}")
                        logger.info(f"  Spot USDT: ${rebalance_result.get('current_spot_usdt', 0):.2f} -> ${rebalance_result.get('target_each', 0):.2f}")
                        logger.info(f"  Perp USDT: ${rebalance_result.get('current_perp_usdt', 0):.2f} -> ${rebalance_result.get('target_each', 0):.2f}")
                    else:
                        logger.info("USDT wallets already balanced (difference < $1)")
                except Exception as rebalance_error:
                    logger.warning(f"Failed to rebalance USDT: {rebalance_error}")
                    logger.warning("Continuing anyway - you may want to manually rebalance")
            else:
                logger.error(f"Failed to close position: {result.get('message')}")

        except Exception as e:
            logger.error(f"Error closing position: {e}", exc_info=True)

    async def _shutdown(self):
        """Graceful shutdown with position cleanup."""
        logger.info("Shutting down strategy...")

        # Save final state before shutdown
        self._save_state()
        logger.info(f"Final state saved to {self.state_file}")

        # Close any open positions (optional - user can choose to keep them open)
        if self.current_position:
            logger.warning(f"Open position on {self.current_position['symbol']} will remain open")
            logger.warning("State has been saved. Restart the strategy to continue monitoring.")
            logger.info("To force-close on shutdown, modify the code in _shutdown()")

        # Close API connections
        await self.api_manager.close()

        logger.info(f"Strategy completed {self.cycle_count} cycles")
        logger.info(f"Total positions opened: {self.total_positions_opened}")
        logger.info(f"Total positions closed: {self.total_positions_closed}")
        logger.info(f"Cumulative P/L: ${self.total_profit_loss:.4f}")
        logger.info("Shutdown complete")


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
        'emergency_stop_loss_pct': -10.0,
        'use_funding_ma': True,
        'funding_ma_periods': 10
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

        # Risk management
        if 'risk_management' in config_data:
            rm = config_data['risk_management']
            config['emergency_stop_loss_pct'] = rm.get('emergency_stop_loss_pct', config['emergency_stop_loss_pct'])

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
        emergency_stop_loss_pct=config['emergency_stop_loss_pct'],
        use_funding_ma=config['use_funding_ma'],
        funding_ma_periods=config['funding_ma_periods']
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