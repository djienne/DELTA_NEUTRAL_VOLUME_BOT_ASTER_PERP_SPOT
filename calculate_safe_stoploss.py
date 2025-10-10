#!/usr/bin/env python3
"""
Calculate maximum safe stop-loss for leveraged short perpetual positions.

This script computes the optimal emergency_stop_loss_pct for the delta-neutral bot
based on leverage settings, maintenance margin, and safety buffers.

For delta-neutral strategy:
- Spot position: LONG (no liquidation risk)
- Perpetual position: SHORT (has liquidation risk)

We calculate stop-loss for the SHORT position to avoid liquidation.
"""

import json
import math
from typing import Dict, Tuple


class LiquidationCalculator:
    """Calculate liquidation prices and safe stop-loss levels for leveraged positions."""

    def __init__(
        self,
        maintenance_margin: float = 0.005,  # 0.5% typical for major pairs
        safety_buffer: float = 0.007,        # 0.7% buffer for fees + slippage + volatility
    ):
        """
        Initialize calculator with exchange parameters.

        Args:
            maintenance_margin: Exchange maintenance margin rate (e.g., 0.005 = 0.5%)
            safety_buffer: Additional safety buffer in price fraction (e.g., 0.007 = 0.7%)
                          This should include: trading fees (~0.1%), slippage (~0.2%),
                          volatility buffer (~0.4%)
        """
        self.m = maintenance_margin
        self.b = safety_buffer

    def calculate_short_liquidation_price(
        self,
        entry_price: float,
        leverage: int
    ) -> float:
        """
        Calculate liquidation price for a SHORT position.

        Formula: P_liq = P_e * (1 + 1/L) / (1 + m)

        Args:
            entry_price: Entry price of the position
            leverage: Leverage multiplier (1-3)

        Returns:
            Liquidation price
        """
        L = leverage
        m = self.m

        P_liq = entry_price * (1 + 1/L) / (1 + m)
        return P_liq

    def calculate_max_stop_distance_short(self, leverage: int) -> float:
        """
        Calculate maximum stop distance for SHORT position (percentage).

        Formula: s_max = [(1 + 1/L)/(1 + m) - 1] - b

        This is the maximum price increase (as fraction) before hitting liquidation buffer.

        Args:
            leverage: Leverage multiplier

        Returns:
            Maximum stop distance as fraction (e.g., 0.0895 = 8.95%)
        """
        L = leverage
        m = self.m
        b = self.b

        s_max = ((1 + 1/L) / (1 + m) - 1) - b
        return s_max

    def calculate_pnl_percentage_short(
        self,
        price_change_pct: float,
        leverage: int
    ) -> float:
        """
        Calculate PnL percentage for a short position given price movement.

        IMPORTANT: For delta-neutral strategy, the perp position is only a fraction
        of total deployed capital. PnL is calculated relative to TOTAL capital.

        For delta-neutral SHORT with leverage L:
        - Perp notional = L/(L+1) of total capital
        - PnL% = -price_change% * [L/(L+1)]

        Args:
            price_change_pct: Price change as percentage (e.g., 0.0895 = 8.95% increase)
            leverage: Leverage multiplier

        Returns:
            PnL percentage relative to total deployed capital (negative for losses)
        """
        # Perp allocation as fraction of total capital in delta-neutral strategy
        perp_fraction = leverage / (leverage + 1)

        # PnL relative to total deployed capital
        return -price_change_pct * perp_fraction

    def calculate_safe_stoploss(self, leverage: int) -> Dict[str, float]:
        """
        Calculate comprehensive stop-loss data for given leverage.

        Args:
            leverage: Leverage multiplier (1-3)

        Returns:
            Dict containing:
                - liquidation_distance_pct: Price distance to liquidation (%)
                - max_stop_distance_pct: Max safe stop distance with buffer (%)
                - max_stop_pnl_pct: Max stop as PnL percentage (%)
                - recommended_stoploss: Suggested emergency_stop_loss_pct value
                - safety_margin_pct: Buffer between stop and liquidation (%)
        """
        # Calculate max stop distance in price terms
        s_max = self.calculate_max_stop_distance_short(leverage)

        # Calculate liquidation distance (without buffer)
        liq_distance = ((1 + 1/leverage) / (1 + self.m) - 1)

        # Convert to PnL percentage
        max_stop_pnl = self.calculate_pnl_percentage_short(s_max, leverage)

        # Calculate safety margin
        safety_margin = (liq_distance - s_max) * 100  # Convert to percentage

        return {
            'liquidation_distance_pct': liq_distance * 100,
            'max_stop_distance_pct': s_max * 100,
            'max_stop_pnl_pct': max_stop_pnl * 100,
            'recommended_stoploss': math.floor(max_stop_pnl * 100),  # Round down for safety
            'safety_margin_pct': safety_margin
        }

    def check_current_config(
        self,
        config_stoploss: float,
        leverage: int
    ) -> Dict[str, any]:
        """
        Validate current configuration against calculated safe values.

        Args:
            config_stoploss: Current emergency_stop_loss_pct from config
            leverage: Current leverage setting

        Returns:
            Dict with validation results
        """
        safe_data = self.calculate_safe_stoploss(leverage)
        max_safe_stoploss = safe_data['max_stop_pnl_pct']

        # Check if current setting is safe (must be LESS negative than max)
        # e.g., -15% is safer than -20% (closer to zero)
        is_safe = config_stoploss >= safe_data['recommended_stoploss']

        distance_to_liq = abs(max_safe_stoploss - config_stoploss)

        return {
            'is_safe': is_safe,
            'current_stoploss': config_stoploss,
            'max_safe_stoploss': safe_data['recommended_stoploss'],
            'distance_to_liquidation_pct': distance_to_liq,
            'liquidation_price_distance_pct': safe_data['liquidation_distance_pct'],
            'safety_margin_pct': safe_data['safety_margin_pct'],
            'recommendation': 'SAFE' if is_safe else 'UNSAFE - TOO CLOSE TO LIQUIDATION'
        }


def format_results_table(results: Dict[int, Dict[str, float]]) -> str:
    """Format calculation results as a nice table."""
    lines = []
    lines.append("\n" + "="*100)
    lines.append("MAXIMUM SAFE STOP-LOSS CALCULATION FOR SHORT PERPETUAL POSITIONS")
    lines.append("="*100)
    lines.append(f"{'Leverage':<10} {'Liq Dist %':<15} {'Stop Dist %':<15} {'Stop PnL %':<15} {'Recommended':<20} {'Safety %':<12}")
    lines.append("-"*100)

    for lev in sorted(results.keys()):
        data = results[lev]
        lines.append(
            f"{lev}x{'':<7} "
            f"{data['liquidation_distance_pct']:>13.2f}% "
            f"{data['max_stop_distance_pct']:>13.2f}% "
            f"{data['max_stop_pnl_pct']:>13.2f}% "
            f"{data['recommended_stoploss']:>18.0f}% "
            f"{data['safety_margin_pct']:>10.2f}%"
        )

    lines.append("="*100)
    lines.append("\nColumn Descriptions:")
    lines.append("  Liq Dist %    : Price distance from entry to liquidation (without buffer)")
    lines.append("  Stop Dist %   : Max safe price move before stop (with 0.7% safety buffer)")
    lines.append("  Stop PnL %    : Stop distance converted to PnL percentage")
    lines.append("  Recommended   : Suggested emergency_stop_loss_pct for config")
    lines.append("  Safety %      : Buffer between your stop and liquidation")
    lines.append("\n")

    return "\n".join(lines)


def load_current_config(config_file: str = 'config_volume_farming_strategy.json') -> Tuple[float, int]:
    """
    Load current stop-loss and leverage from config file.

    Returns:
        Tuple of (emergency_stop_loss_pct, leverage)
    """
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)

        stoploss = config.get('risk_management', {}).get('emergency_stop_loss_pct', -20.0)
        leverage = config.get('risk_management', {}).get('leverage', 3)

        return stoploss, leverage
    except Exception as e:
        print(f"Warning: Could not load config: {e}")
        return -20.0, 3


def main():
    """Main execution function."""
    # Initialize calculator with 0.7% safety buffer
    calc = LiquidationCalculator(
        maintenance_margin=0.005,  # 0.5% (typical for BTC/ETH on most exchanges)
        safety_buffer=0.007        # 0.7% (fees 0.1% + slippage 0.2% + volatility 0.4%)
    )

    print("\n" + "="*100)
    print("LIQUIDATION & STOP-LOSS CALCULATOR FOR DELTA-NEUTRAL BOT")
    print("="*100)
    print(f"Maintenance Margin Rate: {calc.m * 100:.2f}%")
    print(f"Safety Buffer:           {calc.b * 100:.2f}% (includes fees, slippage, volatility)")
    print("="*100)

    # Calculate for all supported leverage levels
    results = {}
    for leverage in [1, 2, 3]:
        results[leverage] = calc.calculate_safe_stoploss(leverage)

    # Print results table
    print(format_results_table(results))

    # Check current configuration
    print("\n" + "="*100)
    print("CURRENT CONFIGURATION VALIDATION")
    print("="*100)

    current_stoploss, current_leverage = load_current_config()

    print(f"\nCurrent Settings:")
    print(f"  Leverage:              {current_leverage}x")
    print(f"  Emergency Stop-Loss:   {current_stoploss:.1f}%")
    print()

    validation = calc.check_current_config(current_stoploss, current_leverage)

    print(f"Validation Results:")
    print(f"  Status:                {validation['recommendation']}")
    print(f"  Max Safe Stop-Loss:    {validation['max_safe_stoploss']:.0f}%")
    print(f"  Your Current Setting:  {validation['current_stoploss']:.1f}%")
    print(f"  Distance to Max Safe:  {abs(validation['current_stoploss'] - validation['max_safe_stoploss']):.1f}%")
    print(f"  Liquidation Distance:  {validation['liquidation_price_distance_pct']:.2f}%")
    print(f"  Safety Margin:         {validation['safety_margin_pct']:.2f}%")
    print()

    if validation['is_safe']:
        print("[SAFE] Your stop-loss setting is SAFE - adequate distance from liquidation")
    else:
        print("[WARNING] Your stop-loss is TOO AGGRESSIVE!")
        print(f"          You risk liquidation before stop-loss triggers.")
        print(f"          Recommended: Change emergency_stop_loss_pct to {validation['max_safe_stoploss']:.0f}% or higher")

    print("="*100)

    # Example calculation with specific entry price
    print("\n" + "="*100)
    print("EXAMPLE: BTC SHORT POSITION")
    print("="*100)

    entry_price = 50000.0
    leverage = current_leverage

    liq_price = calc.calculate_short_liquidation_price(entry_price, leverage)
    safe_data = calc.calculate_safe_stoploss(leverage)
    safe_stop_price = entry_price * (1 + safe_data['max_stop_distance_pct'] / 100)

    print(f"\nEntry Price:           ${entry_price:,.2f}")
    print(f"Leverage:              {leverage}x")
    print(f"Liquidation Price:     ${liq_price:,.2f} (+{((liq_price/entry_price - 1) * 100):.2f}%)")
    print(f"Max Safe Stop Price:   ${safe_stop_price:,.2f} (+{safe_data['max_stop_distance_pct']:.2f}%)")
    print(f"Buffer to Liquidation: ${liq_price - safe_stop_price:,.2f} ({safe_data['safety_margin_pct']:.2f}%)")
    print(f"Stop-Loss PnL %:       {safe_data['max_stop_pnl_pct']:.2f}%")
    print()
    print("If BTC price rises to the safe stop price, position closes BEFORE liquidation.")
    print("="*100 + "\n")


if __name__ == '__main__':
    main()
