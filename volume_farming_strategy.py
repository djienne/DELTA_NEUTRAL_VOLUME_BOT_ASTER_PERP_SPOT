#!/usr/bin/env python3
"""
Delta-neutral funding carry on Aster: long spot + short perp on the pair with the best
7-day average funding, held for weeks and rotated only for a clearly better pair.
(The file name is historical: the bot used to churn positions to farm airdrop volume.)

Safety model:
- Legs open sequentially and are verified by reading positions back (two_leg via
  AsterApiManager.open_dn). A failed unwind writes halt.json; while it exists the bot
  keeps monitoring and closing but never opens.
- A close must complete before anything opens; a failed close is retried next cycle.
- The exchange is the source of truth for the legs. The state file only records which
  symbol and quantities belong to the bot, when it opened, and the cash it started with.
- Any perp position the bot does not track blocks opening: it is adopted when it is a
  clean hedge, otherwise the bot halts for a human.
"""

import asyncio
import json
import logging
import os
import statistics
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional, Tuple

from colorama import Fore, Style, init
from dotenv import load_dotenv

from aster_api_manager import AsterApiManager
from strategy_logic import DN_IMBALANCE_PCT, DeltaNeutralLogic as Logic
from two_leg import LegStatus, read_halt, write_halt

load_dotenv()
init()
logger = logging.getLogger(__name__)

# Liquidity filter: 24h spot + perp quote volume. ~99.9% of it is perp volume and spot
# depth is not checked; fine while each leg stays far below the spot book (top-20 depth
# ~$30-40k on BTC/ETH, measured 2026-09-30). Add a depth check before sizing up.
MIN_VOLUME_USD = 250_000_000
MAX_BASIS_PCT = 0.15     # |perp mid - spot mid| / spot mid, in %
MIN_BALANCE_USD = 30.0
DUST_USD = 5.0           # below the spot minimum notional a remainder cannot be sold

DEFAULT_CONFIG = {
    'capital_fraction': 0.96,
    'min_funding_apr': 10.0,
    'funding_avg_days': 7,
    'min_hold_days': 7,
    'rotation_min_apr_gain': 10.0,
    'loop_interval_seconds': 300,
    'leverage': 1,
}


class VolumeFarmingStrategy:
    """Holds one delta-neutral position at a time; see the module docstring."""

    def __init__(self, capital_fraction: float, min_funding_apr: float, funding_avg_days: float,
                 min_hold_days: float, rotation_min_apr_gain: float, loop_interval_seconds: int, leverage: int):
        if not 1 <= leverage <= 3:
            raise ValueError(f"Leverage must be between 1 and 3, got {leverage}")
        self.api_manager = AsterApiManager(
            api_user=os.getenv('API_USER'),
            api_signer=os.getenv('API_SIGNER'),
            api_private_key=os.getenv('API_PRIVATE_KEY'),
            apiv1_public=os.getenv('APIV1_PUBLIC_KEY'),
            apiv1_private=os.getenv('APIV1_PRIVATE_KEY')
        )
        self.capital_fraction = capital_fraction
        self.min_funding_apr = min_funding_apr
        self.funding_avg_days = funding_avg_days
        self.min_hold_days = min_hold_days
        self.rotation_min_apr_gain = rotation_min_apr_gain
        self.loop_interval_seconds = loop_interval_seconds
        self.leverage = leverage

        self.state_file = 'volume_farming_state.json'
        self.current_position: Optional[Dict[str, Any]] = None  # symbol, spot_qty, perp_qty, entry_apr
        self.position_opened_at: Optional[datetime] = None      # naive UTC
        self.position_leverage: Optional[int] = None
        self.entry_cash: Optional[float] = None                 # USDT in both wallets just before opening
        self.cycle_count = 0                                    # completed open -> close cycles
        self.total_profit_loss = 0.0                            # sum of measured realized PnL
        self.total_positions_opened = 0
        self.total_positions_closed = 0
        self.initial_portfolio_value_usdt: Optional[float] = None
        self.initial_portfolio_timestamp: Optional[datetime] = None
        self._load_state()

        logger.info(f"{Fore.CYAN}Funding carry: {leverage}x, entry >= {min_funding_apr}% "
                    f"{funding_avg_days}d-avg APR, hold >= {min_hold_days}d, rotate for "
                    f"+{rotation_min_apr_gain} APR points, stop-loss {Logic.safe_stop_loss_pct(leverage):.0f}% "
                    f"of perp notional{Style.RESET_ALL}")
        if self.current_position:
            logger.info(f"{Fore.YELLOW}Tracked position: {self.current_position['symbol']} since "
                        f"{self.position_opened_at:%Y-%m-%d %H:%M} UTC at {self.position_leverage}x{Style.RESET_ALL}")

    # --- State ---

    def _load_state(self):
        """Load persisted state. Anything unreadable falls back to fresh state: open
        positions are then re-adopted from the exchange, so nothing live is forgotten."""
        if not os.path.exists(self.state_file):
            logger.info("No state file found, starting fresh")
            return
        try:
            with open(self.state_file) as f:
                state = json.load(f)
            position = state.get('current_position')
            if position and position.get('symbol') and state.get('position_opened_at'):
                self.current_position = position
                self.position_opened_at = datetime.fromisoformat(state['position_opened_at'])
                self.position_leverage = int(state.get('position_leverage') or self.leverage)
                self.entry_cash = state.get('entry_cash')  # absent for positions opened by older versions
            self.cycle_count = int(state.get('cycle_count', 0))
            self.total_profit_loss = float(state.get('total_profit_loss', 0.0))
            self.total_positions_opened = int(state.get('total_positions_opened', 0))
            self.total_positions_closed = int(state.get('total_positions_closed', 0))
            if state.get('initial_portfolio_value_usdt') is not None:
                self.initial_portfolio_value_usdt = float(state['initial_portfolio_value_usdt'])
            if state.get('initial_portfolio_timestamp'):
                self.initial_portfolio_timestamp = datetime.fromisoformat(state['initial_portfolio_timestamp'])
            logger.info(f"State loaded: {self.cycle_count} cycles, realized P/L ${self.total_profit_loss:.4f}")
        except (OSError, ValueError, TypeError, KeyError) as e:
            logger.error(f"Could not load state ({e}); starting fresh. Live positions are re-adopted from the exchange.")

    def _save_state(self):
        """Write state atomically: a crash mid-write must not leave a corrupt file."""
        state = {
            'current_position': self.current_position,
            'position_opened_at': self.position_opened_at.isoformat() if self.position_opened_at else None,
            'position_leverage': self.position_leverage,
            'entry_cash': self.entry_cash,
            'cycle_count': self.cycle_count,
            'total_profit_loss': self.total_profit_loss,
            'total_positions_opened': self.total_positions_opened,
            'total_positions_closed': self.total_positions_closed,
            'initial_portfolio_value_usdt': self.initial_portfolio_value_usdt,
            'initial_portfolio_timestamp': self.initial_portfolio_timestamp.isoformat() if self.initial_portfolio_timestamp else None,
            'last_updated': datetime.utcnow().isoformat(),
        }
        try:
            tmp = self.state_file + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, self.state_file)
        except OSError as e:
            logger.error(f"Failed to save state: {e}")

    def _clear_position(self):
        self.current_position = None
        self.position_opened_at = None
        self.position_leverage = None
        self.entry_cash = None
        self._save_state()

    # --- Account measurements ---

    @staticmethod
    def _cash(pf: Dict[str, Any]) -> float:
        """USDT in both wallets (spot free+locked, perp walletBalance). Measured only when flat,
        so wallet transfers cancel and funding, fees, spread and basis all land in the difference."""
        spot = sum(float(b.get('free', 0)) + float(b.get('locked', 0)) for b in pf['spot_balances'] if b['asset'] == 'USDT')
        perp = sum(float(a.get('walletBalance', 0)) for a in pf['perp_account_info'].get('assets', []) if a.get('asset') == 'USDT')
        return spot + perp

    @staticmethod
    def _portfolio_value(pf: Dict[str, Any]) -> float:
        """All spot holdings at the spot bid + perp wallet + perp unrealized PnL."""
        perp = sum(float(a.get('walletBalance', 0)) for a in pf['perp_account_info'].get('assets', []) if a.get('asset') == 'USDT')
        upnl = sum(float(p.get('unrealizedProfit', 0)) for p in pf['raw_perp_positions'])
        return sum(b['value_usd'] for b in pf['spot_balances']) + perp + upnl

    def _log_portfolio(self, pf: Dict[str, Any]):
        value = self._portfolio_value(pf)
        if self.initial_portfolio_value_usdt is None:
            self.initial_portfolio_value_usdt, self.initial_portfolio_timestamp = value, datetime.utcnow()
            self._save_state()
            logger.info(f"Portfolio baseline captured: ${value:.2f} (assumes no deposits/withdrawals)")
        pnl = value - self.initial_portfolio_value_usdt
        pct = pnl / self.initial_portfolio_value_usdt * 100 if self.initial_portfolio_value_usdt else 0.0
        color = Fore.GREEN if pnl >= 0 else Fore.RED
        logger.info(f"{Fore.CYAN}📊 Portfolio: {Fore.MAGENTA}${value:.2f}{Fore.CYAN} | PnL: {color}{pnl:+.2f} "
                    f"({pct:+.2f}%){Fore.CYAN} | Since: "
                    f"{self.initial_portfolio_timestamp:%Y-%m-%d %H:%M} UTC{Style.RESET_ALL}")

    async def _funding_since_open(self, symbol: str) -> Optional[float]:
        """Funding received on `symbol` since the position opened (income history)."""
        try:
            start = int(self.position_opened_at.replace(tzinfo=timezone.utc).timestamp() * 1000)
            income = await self.api_manager.get_income_history(symbol=symbol, income_type='FUNDING_FEE', start_time=start)
            return sum(float(i['income']) for i in income)
        except Exception as e:
            logger.warning(f"Could not fetch funding history: {e}")
            return None

    # --- Funding scan ---

    async def _avg_apr(self, row: Dict[str, Any]) -> Optional[float]:
        """APR of the mean of the settled rates over funding_avg_days plus the predicted next one.
        None when the symbol has less history than the window (too new to judge)."""
        n = max(1, int(self.funding_avg_days * 24 / row['interval_hours']) - 1)
        history = await self.api_manager.get_funding_rate_history(row['symbol'], limit=n)
        if len(history) < n:
            return None
        rates = [float(h['fundingRate']) for h in history[-n:]] + [row['rate']]
        return Logic.funding_apr(statistics.mean(rates), row['interval_hours'])

    async def _scan(self) -> Tuple[Dict[str, Dict[str, Any]], Optional[Dict[str, Any]]]:
        """One funding snapshot per cycle -> (rows by symbol, best candidate or None).

        A candidate is on both markets, has a published interval, a predicted rate >= 0,
        24h volume >= MIN_VOLUME_USD and |basis| <= MAX_BASIS_PCT. Missing data excludes
        a pair (fail closed). 7-day averages are fetched for candidates and the held
        symbol only; `best` must also reach min_funding_apr.
        """
        pairs, rates, volumes, spot_books, perp_books = await asyncio.gather(
            self.api_manager.discover_delta_neutral_pairs(), self.api_manager.get_all_funding_rates(),
            self.api_manager.get_24h_quote_volumes(), self.api_manager.get_book_tickers('spot'),
            self.api_manager.get_book_tickers('perp'))
        held = self.current_position['symbol'] if self.current_position else None

        rows: Dict[str, Dict[str, Any]] = {}
        for symbol in pairs:
            rate = rates.get(symbol)
            if not rate:
                continue
            status = ''
            if rate['rate'] < 0:
                status = 'negative rate'
            elif volumes.get(symbol, 0.0) < MIN_VOLUME_USD:
                status = 'low volume'
            elif symbol not in spot_books or symbol not in perp_books:
                status = 'no book'
            else:
                spot_mid, perp_mid = sum(spot_books[symbol]) / 2, sum(perp_books[symbol]) / 2
                basis = (perp_mid - spot_mid) / spot_mid * 100
                if abs(basis) > MAX_BASIS_PCT:
                    status = f'basis {basis:+.3f}%'
            rows[symbol] = {'symbol': symbol, **rate, 'status': status, 'avg_apr': None,
                            'apr': Logic.funding_apr(rate['rate'], rate['interval_hours'])}

        to_average = [s for s, row in rows.items() if not row['status'] or s == held]
        averages = await asyncio.gather(*(self._avg_apr(rows[s]) for s in to_average), return_exceptions=True)
        for symbol, avg in zip(to_average, averages):
            if isinstance(avg, Exception) or avg is None:
                rows[symbol]['status'] = rows[symbol]['status'] or 'no history'
            else:
                rows[symbol]['avg_apr'] = avg

        candidates = [r for r in rows.values() if not r['status'] and r['avg_apr'] >= self.min_funding_apr]
        best = max(candidates, key=lambda r: r['avg_apr'], default=None)

        excluded: Dict[str, int] = {}
        for row in rows.values():
            if row['status'] and row['symbol'] != held:
                reason = 'basis' if row['status'].startswith('basis') else row['status']
                excluded[reason] = excluded.get(reason, 0) + 1
        logger.info(f"Scan: {len(to_average)} shown, excluded " + ", ".join(f"{k} {v}" for k, v in excluded.items()))
        logger.info(f"{'Symbol':<14}{'Interval':<10}{'Now APR %':>11}{f'{self.funding_avg_days}d APR %':>12}  Status")
        for symbol in sorted(to_average, key=lambda s: -(rows[s]['avg_apr'] or -1e9)):
            r = rows[symbol]
            avg = f"{r['avg_apr']:>12.2f}" if r['avg_apr'] is not None else f"{'-':>12}"
            tag = r['status'] or ('best' if r is best else f"< {self.min_funding_apr}%" if r['avg_apr'] < self.min_funding_apr else '')
            tag += ' [HELD]' if symbol == held else ''
            color = Fore.CYAN if symbol == held else Fore.GREEN if r is best else Fore.YELLOW
            logger.info(f"{color}{symbol:<14}{int(r['interval_hours'])}h{'':<8}{r['apr']:>11.2f}{avg}  {tag}{Style.RESET_ALL}")
        return rows, best

    # --- Decisions ---

    def _check_position(self, pf: Dict[str, Any], rows: Dict[str, Dict[str, Any]],
                        best: Optional[Dict[str, Any]]) -> Tuple[Optional[str], str]:
        """('flat' | 'close' | None, reason) for the tracked position, from exchange legs.

        The bot's spot leg is min(spot balance, tracked spot_qty): coins you hold yourself
        do not count. Safety rules (stop-loss, broken hedge) come before economics and do
        not depend on the funding scan.
        """
        symbol = self.current_position['symbol']
        perp = next((p for p in pf['raw_perp_positions'] if p['symbol'] == symbol), None)
        perp_amt = float(perp['positionAmt']) if perp else 0.0
        base = symbol[:-len('USDT')]
        spot_balance = sum(float(b.get('free', 0)) + float(b.get('locked', 0))
                           for b in pf['spot_balances'] if b['asset'] == base)
        spot_left = min(spot_balance, float(self.current_position['spot_qty']))
        bid = pf['spot_books'].get(symbol, (0.0, 0.0))[0]

        if perp_amt == 0 and spot_left * bid < DUST_USD:
            return 'flat', f"{symbol}: both legs flat on the exchange (closed outside the bot)"

        if perp:
            notional = abs(perp_amt) * float(perp['entryPrice'])
            pnl_pct = float(perp['unrealizedProfit']) / notional * 100 if notional else 0.0
            stop = Logic.safe_stop_loss_pct(self.position_leverage or self.leverage)
            logger.info(f"  Perp PnL {pnl_pct:+.2f}% of notional (stop {stop:.0f}% at {self.position_leverage}x)")
            if pnl_pct <= stop:
                return 'close', f"STOP-LOSS: perp PnL {pnl_pct:.2f}% <= {stop:.0f}%"

        imbalance = Logic.imbalance_pct(spot_left, perp_amt)
        if perp_amt >= 0 or imbalance > DN_IMBALANCE_PCT:
            return 'close', f"hedge broken: spot {spot_left} vs perp {perp_amt} ({imbalance:.1f}% imbalance)"

        if self.position_leverage != self.leverage:
            logger.info(f"  Position at {self.position_leverage}x; config {self.leverage}x applies to the next position")
        held_apr = rows.get(symbol, {}).get('avg_apr')
        days = (datetime.utcnow() - self.position_opened_at).total_seconds() / 86400
        reason = Logic.close_reason(symbol, held_apr, best['symbol'] if best else None,
                                    best['avg_apr'] if best else None, days, self.min_funding_apr,
                                    self.min_hold_days, self.rotation_min_apr_gain)
        if reason:
            return 'close', reason
        held = f"{held_apr:.2f}%" if held_apr is not None else "n/a"
        logger.info(f"{Fore.CYAN}Holding {symbol}: {days:.1f} d, {self.funding_avg_days}d APR {held}, "
                    f"imbalance {imbalance:.2f}%{Style.RESET_ALL}")
        return None, ''

    def _adopt_or_halt(self, pf: Dict[str, Any]) -> bool:
        """Before opening with nothing tracked: any live perp position blocks the open.

        A single clean hedge is adopted (lost state file, manual open). Anything else is
        exposure nobody is watching, so the bot halts for a human. Returns True if it acted.
        """
        live = pf['raw_perp_positions']
        if not live:
            return False
        hedges = [p for p in pf['analyzed_positions'] if p['is_delta_neutral']]
        if len(live) == 1 and len(hedges) == 1:
            h, perp = hedges[0], live[0]
            self.current_position = {'symbol': h['symbol'], 'spot_qty': h['spot_balance'],
                                     'perp_qty': abs(h['perp_position']), 'entry_apr': None}
            self.position_opened_at = datetime.utcnow()  # true open time unknown
            self.position_leverage = int(float(perp.get('leverage') or self.leverage))
            self.entry_cash = None  # realized PnL of an adopted position cannot be measured
            self._save_state()
            logger.warning(f"{Fore.YELLOW}Adopted untracked hedge {h['symbol']}: spot {h['spot_balance']}, "
                           f"perp {h['perp_position']} at {self.position_leverage}x{Style.RESET_ALL}")
            return True
        summary = ', '.join(f"{p['symbol']} {p['positionAmt']}" for p in live)
        write_halt(f"untracked perp exposure the bot will not manage: {summary}", symbol=live[0]['symbol'],
                   venue='Aster-perp', residual_qty=float(live[0]['positionAmt']))
        return True

    # --- Actions ---

    async def _close_current_position(self):
        """Close via close_dn. State is cleared only when both legs read closed."""
        symbol = self.current_position['symbol']
        try:
            result = await self.api_manager.close_dn(symbol, float(self.current_position['spot_qty']))
        except Exception as e:
            logger.error(f"Close of {symbol} failed ({e}); position kept, retrying next cycle")
            return
        if not result['success']:
            self.current_position['spot_qty'] = result['spot_left']
            self._save_state()
            logger.error(f"Close of {symbol} incomplete: {result['message']}; retrying next cycle")
            return

        funding = await self._funding_since_open(symbol)
        realized = None
        if self.entry_cash is not None:
            try:
                realized = self._cash(await self.api_manager.get_comprehensive_portfolio_data()) - self.entry_cash
            except Exception as e:
                logger.warning(f"Could not measure realized PnL: {e}")
        if realized is not None:
            self.total_profit_loss += realized
            rest = f" = funding {funding:+.4f} + fees/basis/slippage {realized - funding:+.4f}" if funding is not None else ""
            logger.info(f"{Fore.GREEN}✓ {symbol} closed. Realized PnL ${realized:+.4f}{rest}{Style.RESET_ALL}")
        else:
            logger.info(f"{Fore.GREEN}✓ {symbol} closed. Realized PnL unknown (no entry snapshot); "
                        f"funding received {funding}{Style.RESET_ALL}")
        self.total_positions_closed += 1
        self.cycle_count += 1
        self._clear_position()

    async def _open_position(self, best: Optional[Dict[str, Any]], pf: Dict[str, Any]):
        """Rebalance wallets for the leverage, then open the best pair via open_dn."""
        if not best:
            logger.info(f"No pair reaches {self.min_funding_apr}% {self.funding_avg_days}d APR; waiting")
            return
        spot_usdt = sum(float(b.get('free', 0)) for b in pf['spot_balances'] if b['asset'] == 'USDT')
        perp_usdt = sum(float(a.get('availableBalance', 0)) for a in pf['perp_account_info'].get('assets', []) if a.get('asset') == 'USDT')
        if spot_usdt + perp_usdt < MIN_BALANCE_USD:
            logger.error(f"Insufficient balance: ${spot_usdt + perp_usdt:.2f} < ${MIN_BALANCE_USD}")
            return

        symbol = best['symbol']
        entry_cash = self._cash(pf)  # before the rebalance; transfers stay inside this sum
        try:
            rebalance = await self.api_manager.rebalance_usdt_by_leverage(self.leverage)
            moved = rebalance['transfer_needed']
            spot_usdt = rebalance['target_spot_usdt'] if moved else rebalance['current_spot_usdt']
            perp_usdt = rebalance['target_perp_usdt'] if moved else rebalance['current_perp_usdt']
            if moved:
                logger.info(f"Rebalanced ${rebalance['transfer_amount']:.2f} USDT ({rebalance['transfer_direction']})")
            # Spot pays the full notional, the perp only notional / leverage as margin.
            notional = min(spot_usdt, perp_usdt * self.leverage) * self.capital_fraction
            logger.info(f"{Fore.YELLOW}Opening {symbol} ({best['avg_apr']:.2f}% {self.funding_avg_days}d APR): "
                        f"${notional:.2f} per leg at {self.leverage}x{Style.RESET_ALL}")
            outcome = await self.api_manager.open_dn(symbol, notional, self.leverage)
        except Exception as e:
            logger.error(f"Open of {symbol} failed: {e}")
            return

        if outcome.ok or outcome.halted:
            # Halted: an unwind failed, so track the symbol anyway and let the monitor
            # stop-loss and close whatever legs are left (halt blocks opening, not closing).
            self.current_position = {'symbol': symbol, 'spot_qty': outcome.pilot.filled_qty,
                                     'perp_qty': outcome.hedge.filled_qty if outcome.hedge else 0.0,
                                     'entry_apr': best['avg_apr']}
            self.position_opened_at = datetime.utcnow()
            self.position_leverage = self.leverage
            self.entry_cash = entry_cash
            self.total_positions_opened += 1
            self._save_state()
        if outcome.ok:
            logger.info(f"{Fore.GREEN}✓ Opened {symbol}: spot {outcome.pilot.filled_qty}, perp "
                        f"-{outcome.hedge.filled_qty} ({'; '.join(outcome.notes) or 'clean'}){Style.RESET_ALL}")
        elif outcome.halted:
            logger.critical(f"Open of {symbol} left legs that could not be unwound: {outcome.reason}. HALTED.")
        elif outcome.pilot and outcome.pilot.status in (LegStatus.FILLED, LegStatus.PARTIAL):
            # The spot leg filled, the hedge failed and both were unwound: flat but fees paid.
            # Stop here so a persistent failure cannot repeat that every cycle.
            write_halt(f"open failed after the spot leg filled ({outcome.reason}); unwound",
                       symbol=symbol, venue='Aster', residual_qty=0.0)
        else:
            logger.warning(f"Open of {symbol} failed, nothing live: {outcome.reason}")

    # --- Loop ---

    async def _cycle(self):
        """One monitoring/decision step. Never opens in a cycle that closed or tried to."""
        try:
            pf = await self.api_manager.get_comprehensive_portfolio_data()
        except Exception as e:
            logger.error(f"Account data unavailable ({e}); holding, retrying next cycle")
            return
        self._log_portfolio(pf)
        try:
            rows, best = await self._scan()
        except Exception as e:
            logger.error(f"Funding scan failed ({e}); only safety checks run this cycle")
            rows, best = {}, None

        if self.current_position:
            action, reason = self._check_position(pf, rows, best)
            if action == 'flat':
                logger.warning(reason)
                self._clear_position()
            elif action == 'close':
                logger.warning(f"{Fore.YELLOW}Closing {self.current_position['symbol']}: {reason}{Style.RESET_ALL}")
                await self._close_current_position()
            return

        halt = read_halt()
        if halt:
            logger.critical(f"halt.json present ({halt.get('reason')}); not opening. Check both markets, then delete it.")
            return
        if not self._adopt_or_halt(pf):
            await self._open_position(best, pf)

    async def run(self):
        """Main loop: one cycle every loop_interval_seconds; errors never end the loop."""
        logger.info("Starting funding carry strategy...")
        check = 0
        try:
            while True:
                check += 1
                logger.info(f"{Fore.CYAN}{'=' * 80}\nCHECK #{check} - {datetime.utcnow():%Y-%m-%d %H:%M:%S} UTC | "
                            f"Cycles completed: {self.cycle_count}{Style.RESET_ALL}")
                try:
                    await self._cycle()
                except Exception as e:
                    logger.error(f"Unexpected error in cycle: {e}", exc_info=True)
                await asyncio.sleep(self.loop_interval_seconds)
        finally:
            self._save_state()
            await self.api_manager.close()
            if self.current_position:
                logger.warning(f"Stopped with {self.current_position['symbol']} open; restart to keep monitoring it.")


def load_config(config_file: str = 'config_volume_farming_strategy.json') -> Dict[str, Any]:
    """Defaults overridden by any known key in any section of the config file.
    An unreadable config raises: trading on silent defaults is worse than not starting."""
    config = dict(DEFAULT_CONFIG)
    if not os.path.exists(config_file):
        logger.info(f"Config file {config_file} not found, using defaults")
        return config
    with open(config_file) as f:
        for section in json.load(f).values():
            if isinstance(section, dict):
                config.update({k: v for k, v in section.items() if k in config})
    return config


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[RotatingFileHandler('volume_farming.log', maxBytes=10_000_000, backupCount=3),
                  logging.StreamHandler(sys.stdout)])


async def main():
    """Entry point."""
    setup_logging()
    required = ['API_USER', 'API_SIGNER', 'API_PRIVATE_KEY', 'APIV1_PUBLIC_KEY', 'APIV1_PRIVATE_KEY']
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        logger.error(f"Missing environment variables: {', '.join(missing)} (see .env.example)")
        sys.exit(1)
    await VolumeFarmingStrategy(**load_config()).run()


if __name__ == '__main__':
    asyncio.run(main())
