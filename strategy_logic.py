#!/usr/bin/env python3
"""
Pure logic for the delta-neutral funding strategy: no API calls, no state.
"""

import math
from typing import List, Dict, Optional, Any

DN_IMBALANCE_PCT = 2.0  # |spot - perp| above this % of the larger leg = not a hedge


class DeltaNeutralLogic:
    """Container for pure strategy functions (all static)."""

    @staticmethod
    def find_delta_neutral_pairs(spot_symbols: List[str], perp_symbols: List[str]) -> List[str]:
        """Symbols listed on both markets, sorted."""
        return sorted(set(spot_symbols) & set(perp_symbols))

    @staticmethod
    def analyze_position_data(
        perp_positions: List[Dict[str, Any]],
        spot_balances: Dict[str, float],
        perp_symbol_map: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """Per perp position: spot balance of the same base asset, imbalance, DN flag, value."""
        analysis = {}
        for position in perp_positions:
            symbol = position.get('symbol', '')
            perp_qty = float(position.get('positionAmt', '0'))
            if not symbol or abs(perp_qty) < 1e-9:
                continue

            base_asset = perp_symbol_map.get(symbol, {}).get('baseAsset', '')
            spot_qty = spot_balances.get(base_asset, 0.0)
            imbalance_pct = DeltaNeutralLogic.imbalance_pct(spot_qty, perp_qty)

            analysis[symbol] = {
                'symbol': symbol,
                'spot_balance': spot_qty,
                'perp_position': perp_qty,
                'is_delta_neutral': perp_qty < 0 and imbalance_pct <= DN_IMBALANCE_PCT,
                'imbalance_pct': imbalance_pct,
                'position_value_usd': abs(perp_qty) * float(position.get('markPrice', '0')),
                'leverage': int(float(position.get('leverage', '1'))),
            }
        return analysis

    @staticmethod
    def imbalance_pct(spot_qty: float, perp_qty: float) -> float:
        """Net delta as % of the larger leg (perp_qty is negative for a short)."""
        size = max(abs(spot_qty), abs(perp_qty))
        return abs(spot_qty + perp_qty) / size * 100 if size > 0 else 0.0

    @staticmethod
    def funding_apr(rate: float, interval_hours: float) -> float:
        """Per-interval funding rate -> APR in % (interval varies per symbol: 1h, 4h, 8h)."""
        return rate * (24 / interval_hours) * 365 * 100

    @staticmethod
    def safe_stop_loss_pct(leverage: int, maintenance_margin: float = 0.005, safety_buffer: float = 0.7) -> float:
        """Stop-loss on perp PnL as % of perp notional, 70% of the way to liquidation.

        Short with margin N/L liquidates when the price has risen by
        s = (1 + 1/L)/(1 + m) - 1, where perp PnL = -s * N. Rounded toward zero (ceil) so
        the stop sits inside the buffer: 1x -69%, 2x -34%, 3x -22%. Assumes isolated
        margin of N/L; cross margin only adds buffer.
        """
        s_max = (1 + 1 / leverage) / (1 + maintenance_margin) - 1
        return float(math.ceil(-s_max * safety_buffer * 100))

    @staticmethod
    def close_reason(held_symbol: str, held_apr: Optional[float], best_symbol: Optional[str],
                     best_apr: Optional[float], held_days: float, min_funding_apr: float,
                     min_hold_days: float, rotation_min_apr_gain: float) -> Optional[str]:
        """Economic exit rule for a healthy hedge. None = keep holding.

        Switching costs one round trip (~0.4% of notional with 4 taker legs), so a move
        must pay that back within the hold: over ~30 days with a 2x margin that is ~10
        APR points. Hence rotation needs an absolute APR gain, not a ratio (2x of 3% is
        only 3 points and would lose money), and a minimum hold stops churn on noise.
        """
        if held_apr is None:
            return None  # no funding data this cycle: never act on missing data
        if held_apr <= -min_funding_apr:
            return f"{held_symbol} 7d funding APR {held_apr:.2f}% <= -{min_funding_apr}% (paying funding)"
        if (best_symbol and best_symbol != held_symbol and best_apr is not None
                and held_days >= min_hold_days and best_apr - held_apr >= rotation_min_apr_gain):
            return (f"rotate {held_symbol} ({held_apr:.2f}%) -> {best_symbol} ({best_apr:.2f}%): "
                    f"+{best_apr - held_apr:.2f} APR points after {held_days:.1f} days")
        return None
