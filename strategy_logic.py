#!/usr/bin/env python3
"""
Pure computational logic for delta-neutral funding rate farming strategy.
This module contains stateless functions for opportunity analysis, position sizing,
and risk management. All functions are pure and highly testable.
"""

import typing
import statistics
from typing import List, Dict, Optional, Tuple, Any

# Strategy constants for easy tuning
ANNUALIZED_APR_THRESHOLD = 15.0  # Minimum annual percentage rate to consider
MIN_FUNDING_RATE_COUNT = 10      # Minimum historical data points required
MAX_VOLATILITY_THRESHOLD = 0.05  # Maximum coefficient of variation for stability
LIQUIDATION_BUFFER_PCT = 0.05    # 5% buffer from liquidation price
IMBALANCE_THRESHOLD_PCT = 5.0    # Maximum allowed position imbalance percentage
HIGH_RISK_LIQUIDATION_PCT = 2.0  # Liquidation risk percentage considered HIGH


class DeltaNeutralLogic:
    """
    Container for all delta-neutral strategy logic.
    All methods are static for maximum testability and statelessness.
    """

    @staticmethod
    def find_delta_neutral_pairs(
        spot_symbols: List[str],
        perp_symbols: List[str]
    ) -> List[str]:
        """
        Find trading pairs that have both spot and perpetual markets available.

        Args:
            spot_symbols: List of available spot trading symbols (e.g., ['BTCUSDT', 'ETHUSDT', 'ASTERUSDT'])
            perp_symbols: List of available perpetual trading symbols (e.g., ['BTCUSDT', 'ETHUSDT', 'ASTERUSDT'])

        Returns:
            List of symbols that have both spot and perpetual markets available
        """
        # Convert to sets for efficient intersection
        spot_set = set(spot_symbols)
        perp_set = set(perp_symbols)

        # Find intersection - symbols available in both markets
        common_symbols = spot_set.intersection(perp_set)

        # Return as sorted list for consistent ordering
        return sorted(list(common_symbols))

    @staticmethod
    def filter_viable_pairs(
        common_pairs: List[str],
        min_liquidity_usd: float = 10000.0,
        spot_volumes_24h: Optional[Dict[str, float]] = None,
        perp_volumes_24h: Optional[Dict[str, float]] = None
    ) -> List[str]:
        """
        Filter trading pairs based on liquidity and volume requirements.

        Args:
            common_pairs: List of pairs available in both spot and perp markets
            min_liquidity_usd: Minimum 24h volume required in USD
            spot_volumes_24h: Dict mapping symbol -> 24h spot volume in USD
            perp_volumes_24h: Dict mapping symbol -> 24h perp volume in USD

        Returns:
            List of viable pairs that meet liquidity requirements
        """
        if not spot_volumes_24h or not perp_volumes_24h:
            # If no volume data provided, return all common pairs
            return common_pairs

        viable_pairs = []

        for symbol in common_pairs:
            spot_volume = spot_volumes_24h.get(symbol, 0.0)
            perp_volume = perp_volumes_24h.get(symbol, 0.0)

            # Both markets must meet minimum liquidity
            if spot_volume >= min_liquidity_usd and perp_volume >= min_liquidity_usd:
                viable_pairs.append(symbol)

        return sorted(viable_pairs)

    @staticmethod
    def get_aster_known_pairs() -> Dict[str, Dict[str, bool]]:
        """
        Get currently known trading pairs on Aster DEX.
        This serves as a fallback when API discovery is not available.

        Returns:
            Dict mapping symbol -> {spot: bool, perp: bool}
        """
        # Known pairs as of implementation (this should be updated as Aster adds more)
        known_pairs = {
            'BTCUSDT': {'spot': True, 'perp': True},
            'ETHUSDT': {'spot': True, 'perp': True},
            'ASTERUSDT': {'spot': True, 'perp': True},
            'USD1USDT': {'spot': True, 'perp': True},  # Stablecoin pair
            'XRPUSDT': {'spot': False, 'perp': True},  # Perp only currently
            # Add more pairs as they become available
        }

        return known_pairs

    @staticmethod
    def extract_delta_neutral_candidates(known_pairs: Dict[str, Dict[str, bool]]) -> List[str]:
        """
        Extract symbols that have both spot and perpetual markets from known pairs data.

        Args:
            known_pairs: Dict from get_aster_known_pairs() or similar structure

        Returns:
            List of symbols suitable for delta-neutral strategies
        """
        candidates = []

        for symbol, markets in known_pairs.items():
            if markets.get('spot', False) and markets.get('perp', False):
                candidates.append(symbol)

        return sorted(candidates)

    @staticmethod
    def analyze_funding_opportunities(
        funding_histories: Dict[str, List[float]],
        spot_prices: Dict[str, float]
    ) -> List[Dict[str, Any]]:
        """
        Analyze funding rate histories to identify profitable opportunities.

        Args:
            funding_histories: Dict mapping symbol -> list of funding rates
            spot_prices: Dict mapping symbol -> current spot price

        Returns:
            List of opportunity dictionaries sorted by annualized APR descending.
            Each dict contains: symbol, mean_funding, stdev_funding, annualized_apr,
            coefficient_of_variation, data_points_count, spot_price
        """
        opportunities = []

        for symbol, funding_rates in funding_histories.items():
            if len(funding_rates) < MIN_FUNDING_RATE_COUNT:
                continue

            if symbol not in spot_prices:
                continue

            # Calculate statistics
            mean_funding = statistics.mean(funding_rates)
            stdev_funding = statistics.stdev(funding_rates) if len(funding_rates) > 1 else 0.0

            # Skip negative funding rates (would cost us money)
            if mean_funding <= 0:
                continue

            # Calculate coefficient of variation for stability assessment
            coefficient_of_variation = stdev_funding / mean_funding if mean_funding > 0 else float('inf')

            # Skip highly volatile funding rates
            if coefficient_of_variation > MAX_VOLATILITY_THRESHOLD:
                continue

            # Annualize the funding rate (assuming 8-hour intervals, 3 times per day)
            annualized_apr = mean_funding * 3 * 365 * 100  # Convert to percentage

            # Only include opportunities above threshold
            if annualized_apr >= ANNUALIZED_APR_THRESHOLD:
                opportunities.append({
                    'symbol': symbol,
                    'mean_funding': mean_funding,
                    'stdev_funding': stdev_funding,
                    'annualized_apr': annualized_apr,
                    'coefficient_of_variation': coefficient_of_variation,
                    'data_points_count': len(funding_rates),
                    'spot_price': spot_prices[symbol]
                })

        # Sort by annualized APR descending
        opportunities.sort(key=lambda x: x['annualized_apr'], reverse=True)
        return opportunities

    @staticmethod
    def calculate_position_size(
        total_usd_capital: float,
        spot_price: float,
        leverage: int = 1,
        existing_spot_usd: float = 0.0
    ) -> Dict[str, float]:
        """
        Calculate position sizes for delta-neutral strategy, accounting for existing spot holdings.

        Args:
            total_usd_capital: Total desired USD value of the position.
            spot_price: Current spot price of the asset.
            leverage: Leverage setting (should be 1 for delta-neutral).
            existing_spot_usd: The USD value of the asset already held in the spot account.

        Returns:
            Dict containing spot_quantity_to_buy, total_perp_quantity_to_short, and capital details.
        """
        if spot_price <= 0:
            return {} # Avoid division by zero

        # The total size of the perpetual short should match the total desired capital
        total_perp_quantity_to_short = total_usd_capital / spot_price

        # The amount of new spot to buy is the total desired capital minus what's already owned
        new_spot_capital_required = max(0, total_usd_capital - existing_spot_usd)
        spot_quantity_to_buy = new_spot_capital_required / spot_price

        # For delta-neutral, perp capital should match spot capital value
        perp_capital_required = total_usd_capital / leverage

        result = {
            'spot_quantity_to_buy': spot_quantity_to_buy,
            'total_perp_quantity_to_short': total_perp_quantity_to_short,
            'new_spot_capital_required': new_spot_capital_required,
            'perp_capital_required': perp_capital_required,
            'total_capital_deployed': total_usd_capital,
            'existing_spot_usd_utilized': existing_spot_usd,
            'leverage_used': leverage,
            'is_proper_delta_neutral': leverage == 1
        }

        # Add legacy aliases for backward compatibility with tests
        result['spot_quantity'] = spot_quantity_to_buy
        result['perp_quantity'] = total_perp_quantity_to_short

        return result

    @staticmethod
    def check_position_health(
        perp_position: Dict[str, Any],
        spot_balance_qty: float,
        leverage: int = 1
    ) -> Dict[str, Any]:
        """
        Analyze the health of an existing delta-neutral position.

        Args:
            perp_position: Perpetual position info from API
            spot_balance_qty: Current spot balance quantity
            leverage: Current leverage setting (affects liquidation risk calculations)

        Returns:
            Dict containing health metrics: net_delta, imbalance_percentage,
            liquidation_risk_pct, liquidation_risk_level, position_value_usd, leverage_risk_factor
        """
        # Extract position data
        perp_quantity = float(perp_position.get('positionAmt', 0))
        liquidation_price = float(perp_position.get('liquidationPrice', 0))
        mark_price = float(perp_position.get('markPrice', 0))
        unrealized_pnl = float(perp_position.get('unrealizedProfit', 0))

        # Calculate net delta (should be close to 0 for delta-neutral)
        net_delta = spot_balance_qty + perp_quantity  # Note: perp_quantity is negative for short

        # Calculate imbalance percentage
        total_position_size = abs(perp_quantity)
        if total_position_size > 0:
            imbalance_percentage = abs(net_delta) / total_position_size * 100
        else:
            imbalance_percentage = 0.0

        # Calculate liquidation risk (higher leverage = higher risk)
        liquidation_risk_pct = 0.0
        liquidation_risk_level = 'NONE'
        leverage_risk_factor = leverage  # Higher leverage multiplies risk

        # Safety check: leverage should always be >= 1
        if leverage < 1:
            leverage = 1  # Default to 1x for safety

        if liquidation_price > 0 and mark_price > 0:
            if perp_quantity < 0:  # Short position
                liquidation_risk_pct = (liquidation_price - mark_price) / mark_price * 100
            else:  # Long position
                liquidation_risk_pct = (mark_price - liquidation_price) / mark_price * 100

            # Adjust risk thresholds based on leverage
            high_risk_threshold = HIGH_RISK_LIQUIDATION_PCT / leverage
            medium_risk_threshold = (LIQUIDATION_BUFFER_PCT * 100) / leverage

            if abs(liquidation_risk_pct) <= high_risk_threshold:
                liquidation_risk_level = 'HIGH'
            elif abs(liquidation_risk_pct) <= medium_risk_threshold:
                liquidation_risk_level = 'MEDIUM'
            else:
                liquidation_risk_level = 'LOW'

        # Warn if leverage is not 1x for delta-neutral strategy
        if leverage != 1:
            liquidation_risk_level = 'CRITICAL'  # Force attention to leverage issue

        # Calculate position value
        position_value_usd = abs(perp_quantity) * mark_price

        return {
            'net_delta': net_delta,
            'imbalance_percentage': imbalance_percentage,
            'liquidation_risk_pct': liquidation_risk_pct,
            'liquidation_risk_level': liquidation_risk_level,
            'position_value_usd': position_value_usd,
            'unrealized_pnl': unrealized_pnl,
            'total_position_size': total_position_size,
            'leverage_risk_factor': leverage_risk_factor,
            'leverage_warning': leverage != 1
        }

    @staticmethod
    def determine_rebalance_action(
        health_report: Dict[str, Any]
    ) -> str:
        """
        Determine what action to take based on position health.

        Args:
            health_report: Output from check_position_health()

        Returns:
            Action string: 'ACTION_CLOSE_POSITION', 'ACTION_REBALANCE', or 'ACTION_HOLD'
        """
        liquidation_risk_level = health_report.get('liquidation_risk_level', 'NONE')
        imbalance_percentage = health_report.get('imbalance_percentage', 0.0)

        # Priority 1: Close position if liquidation risk is high
        if liquidation_risk_level == 'HIGH':
            return 'ACTION_CLOSE_POSITION'

        # Priority 2: Rebalance if position is significantly imbalanced
        if imbalance_percentage >= IMBALANCE_THRESHOLD_PCT:
            return 'ACTION_REBALANCE'

        # Default: Hold position
        return 'ACTION_HOLD'

    @staticmethod
    def calculate_rebalance_quantities(
        health_report: Dict[str, Any],
        current_spot_balance: float,
        current_perp_quantity: float,
        spot_price: float
    ) -> Dict[str, Any]:
        """
        Calculate quantities needed to rebalance position back to delta-neutral.

        Args:
            health_report: Output from check_position_health()
            current_spot_balance: Current spot asset balance
            current_perp_quantity: Current perpetual position quantity (negative for short)
            spot_price: Current spot price

        Returns:
            Dict with rebalance instructions: action_type, spot_action, perp_action,
            spot_quantity, perp_quantity, estimated_cost_usd
        """
        net_delta = health_report.get('net_delta', 0.0)

        if abs(net_delta) < 0.001:  # Already balanced
            return {
                'action_type': 'NO_ACTION',
                'spot_action': None,
                'perp_action': None,
                'spot_quantity': 0.0,
                'perp_quantity': 0.0,
                'estimated_cost_usd': 0.0
            }

        # Calculate rebalance amounts
        rebalance_amount = abs(net_delta) / 2  # Split the imbalance equally

        if net_delta > 0:  # Too much spot, need to sell spot and increase short perp
            return {
                'action_type': 'REDUCE_SPOT_INCREASE_SHORT',
                'spot_action': 'SELL',
                'perp_action': 'INCREASE_SHORT',
                'spot_quantity': rebalance_amount,
                'perp_quantity': rebalance_amount,
                'estimated_cost_usd': rebalance_amount * spot_price
            }
        else:  # Too much short perp, need to buy spot and reduce short perp
            return {
                'action_type': 'INCREASE_SPOT_REDUCE_SHORT',
                'spot_action': 'BUY',
                'perp_action': 'REDUCE_SHORT',
                'spot_quantity': rebalance_amount,
                'perp_quantity': rebalance_amount,
                'estimated_cost_usd': rebalance_amount * spot_price
            }

    @staticmethod
    def validate_strategy_preconditions(
        spot_balance_usdt: float,
        perp_balance_usdt: float,
        current_leverage: int = 1,
        min_capital_usd: float = 50.0
    ) -> Tuple[bool, List[str]]:
        """
        Validate that preconditions are met before opening a position.

        Args:
            spot_balance_usdt: Available USDT in spot account
            perp_balance_usdt: Available balance in perpetual account
            current_leverage: Current leverage setting for perpetuals (must be 1 for delta-neutral)
            min_capital_usd: Minimum capital required

        Returns:
            Tuple of (is_valid, list_of_error_messages)
        """
        errors = []

        # Validate leverage is set to 1x for delta-neutral strategy
        if current_leverage != 1:
            errors.append(f"Invalid leverage setting: {current_leverage}x. Delta-neutral strategy requires 1x leverage.")

        if spot_balance_usdt < min_capital_usd / 2:
            errors.append(f"Insufficient spot balance: ${spot_balance_usdt:.2f} < ${min_capital_usd/2:.2f}")

        if perp_balance_usdt < min_capital_usd / 2:
            errors.append(f"Insufficient perp balance: ${perp_balance_usdt:.2f} < ${min_capital_usd/2:.2f}")

        return len(errors) == 0, errors

    @staticmethod
    def analyze_position_data(
        perp_positions: List[Dict[str, Any]],
        spot_balances: Dict[str, float],
        perp_symbol_map: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Analyze position data to identify delta-neutral positions and calculate metrics.

        Args:
            perp_positions: List of perpetual position dictionaries from API
            spot_balances: Dict mapping asset -> spot balance quantity
            perp_symbol_map: Dict mapping symbol -> exchange info for perpetuals

        Returns:
            Dict mapping symbol -> position analysis with delta-neutral metrics
        """
        analysis = {}

        for position in perp_positions:
            symbol = position.get('symbol', '')
            perp_qty = float(position.get('positionAmt', '0'))
            if not symbol or abs(perp_qty) < 1e-9:
                continue

            base_asset = perp_symbol_map.get(symbol, {}).get('baseAsset', '')
            spot_qty = spot_balances.get(base_asset, 0.0)

            net_delta = spot_qty + perp_qty  # perp_qty is negative for short
            total_size = max(abs(spot_qty), abs(perp_qty))
            imbalance_pct = abs(net_delta) / total_size * 100 if total_size > 0 else 0.0
            is_delta_neutral = imbalance_pct <= 2.0  # 2% threshold for delta-neutral classification

            mark_price = float(position.get('markPrice', '0'))
            position_value_usd = abs(perp_qty) * mark_price

            analysis[symbol] = {
                'symbol': symbol,
                'spot_balance': spot_qty,
                'perp_position': perp_qty,
                'is_delta_neutral': is_delta_neutral,
                'imbalance_pct': imbalance_pct,
                'net_delta': net_delta,
                'position_value_usd': position_value_usd,
                'leverage': int(float(position.get('leverage', '1'))),
            }

        return analysis

    @staticmethod
    def perform_portfolio_health_analysis(
        positions_data: List[Dict[str, Any]]
    ) -> Tuple[List[str], List[str], int]:
        """
        Analyze portfolio health and identify issues with delta-neutral positions.

        Args:
            positions_data: List of position dictionaries from analyze_position_data

        Returns:
            Tuple of (health_issues, critical_issues, dn_positions_count)
        """
        # Filter for delta-neutral positions
        dn_positions = [p for p in positions_data if p.get('is_delta_neutral')]
        dn_positions_count = len(dn_positions)

        if dn_positions_count == 0:
            return [], [], 0

        health_issues = []
        critical_issues = []

        # Check each delta-neutral position
        for pos in dn_positions:
            symbol = pos.get('symbol', 'N/A')
            imbalance_pct = pos.get('imbalance_pct', 0.0)
            leverage = pos.get('leverage', 1)
            position_value_usd = pos.get('position_value_usd', 0.0)

            # Check for leverage issues (must be within valid range)
            if leverage < 1 or leverage > 3:
                critical_issues.append(f"{symbol}: Leverage is {leverage}x (must be 1x-3x for delta-neutral)")

            # Check for significant imbalance
            if imbalance_pct > IMBALANCE_THRESHOLD_PCT:
                if imbalance_pct > 10.0:  # Critical threshold
                    critical_issues.append(f"{symbol}: Critical imbalance {imbalance_pct:.1f}% (>10%)")
                else:
                    health_issues.append(f"{symbol}: Position imbalance {imbalance_pct:.1f}% (target: <{IMBALANCE_THRESHOLD_PCT}%)")

            # Check for very small positions (might indicate incomplete trades)
            if position_value_usd < 5.0:
                health_issues.append(f"{symbol}: Very small position value ${position_value_usd:.2f}")

        return health_issues, critical_issues, dn_positions_count

    @staticmethod
    def calculate_funding_rate_ma(
        funding_rates: List[float],
        periods: int = 10
    ) -> Optional[Dict[str, Any]]:
        """
        Calculate moving average and statistics for funding rates.

        Args:
            funding_rates: List of funding rates (most recent first)
            periods: Number of periods to include in moving average

        Returns:
            Dict with MA rate, APR, statistics, or None if insufficient data
        """
        if not funding_rates or len(funding_rates) < periods:
            return None

        # Take only the requested number of periods
        rates = funding_rates[:periods]

        # Calculate moving average
        ma_rate = statistics.mean(rates)

        # Calculate standard deviation for volatility measure
        stdev = statistics.stdev(rates) if len(rates) > 1 else 0.0

        # Current (latest) rate
        current_rate = rates[0]

        # Calculate APR based on MA rate (3 payments per day, 365 days)
        # For delta-neutral 1x leverage, divide by 2
        ma_apr = ma_rate * 3 * 365 * 100
        effective_ma_apr = ma_apr / 2  # For 1x leverage

        return {
            'current_rate': current_rate,
            'ma_rate': ma_rate,
            'ma_periods': periods,
            'rates_used': rates,
            'stdev': stdev,
            'ma_apr': ma_apr,
            'effective_ma_apr': effective_ma_apr,
            'data_points': len(rates)
        }