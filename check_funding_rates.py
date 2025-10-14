#!/usr/bin/env python3
"""
Funding Rate and Volume Checker

This script displays current funding rates (APR) for all delta-neutral pairs
and shows which pairs are filtered out due to insufficient volume.
"""

import asyncio
import aiohttp
import os
from datetime import datetime
from colorama import Fore, Style, init
from dotenv import load_dotenv
from collections import Counter

from aster_api_manager import AsterApiManager
from strategy_logic import DeltaNeutralLogic

# Initialize colorama
init(autoreset=True)

# Load environment variables
load_dotenv()


async def detect_funding_interval(api_manager: AsterApiManager, symbol: str) -> int:
    """
    Detect the funding interval for a symbol by analyzing historical funding times.

    Args:
        api_manager: API manager instance
        symbol: Trading symbol to analyze

    Returns:
        Number of times funding is paid per day (3, 6, 24, etc.)
    """
    try:
        # Fetch more history to accurately detect interval
        history = await api_manager.get_funding_rate_history(symbol, limit=10)

        if not history or len(history) < 2:
            return 3  # Default to 3x per day (8-hour interval)

        # Calculate time differences between consecutive funding times
        time_diffs = []
        for i in range(len(history) - 1):
            t1 = int(history[i]['fundingTime'])
            t2 = int(history[i + 1]['fundingTime'])
            diff_hours = abs(t1 - t2) / (1000 * 3600)  # Convert ms to hours
            time_diffs.append(diff_hours)

        # Determine most common interval (round to nearest hour)
        rounded_diffs = [round(d) for d in time_diffs]
        most_common = Counter(rounded_diffs).most_common(1)

        if most_common:
            interval_hours = most_common[0][0]
            if interval_hours > 0:
                return int(24 / interval_hours)  # Convert to times per day

        return 3  # Default to 3x per day
    except Exception:
        return 3  # Default to 3x per day on error


async def check_funding_rates():
    """
    Check and display funding rates for all pairs.
    Shows which pairs pass/fail volume filtering.
    """
    print(f"\n{Fore.CYAN}{'='*80}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Funding Rate & Volume Analysis - {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}\n")

    # Initialize API manager
    api_user = os.getenv('API_USER')
    api_signer = os.getenv('API_SIGNER')
    api_private_key = os.getenv('API_PRIVATE_KEY')
    apiv1_public = os.getenv('APIV1_PUBLIC_KEY')
    apiv1_private = os.getenv('APIV1_PRIVATE_KEY')

    api_manager = AsterApiManager(
        api_user=api_user,
        api_signer=api_signer,
        api_private_key=api_private_key,
        apiv1_public=apiv1_public,
        apiv1_private=apiv1_private
    )

    try:
        # Get all available delta-neutral pairs
        print(f"{Fore.YELLOW}Fetching available delta-neutral pairs...{Style.RESET_ALL}")
        available_pairs = await api_manager.discover_delta_neutral_pairs()

        if not available_pairs:
            print(f"{Fore.RED}[ERROR] No delta-neutral pairs found{Style.RESET_ALL}")
            return

        print(f"{Fore.GREEN}[OK] Found {len(available_pairs)} delta-neutral pairs{Style.RESET_ALL}\n")

        # Get current funding rates, intervals, and 24h ticker data for all pairs
        print(f"{Fore.YELLOW}Fetching current funding rates, intervals, and volume data...{Style.RESET_ALL}")

        # Fetch CURRENT funding rates (not historical), intervals, and 24h ticker data concurrently
        if not api_manager.session:
            api_manager.session = aiohttp.ClientSession()

        # Use get_current_funding_rate() to get the current/next rate from premiumIndex
        funding_tasks = [api_manager.get_current_funding_rate(symbol) for symbol in available_pairs]
        ticker_tasks = []
        for symbol in available_pairs:
            url = f"https://fapi.asterdex.com/fapi/v1/ticker/24hr"
            ticker_tasks.append(api_manager.session.get(url, params={'symbol': symbol}))

        funding_results = await asyncio.gather(*funding_tasks, return_exceptions=True)
        ticker_responses = await asyncio.gather(*ticker_tasks, return_exceptions=True)

        # Detect funding intervals for all symbols
        print(f"{Fore.YELLOW}Detecting funding intervals...{Style.RESET_ALL}")
        interval_tasks = [api_manager.detect_funding_interval(symbol) for symbol in available_pairs]
        funding_intervals = await asyncio.gather(*interval_tasks, return_exceptions=True)

        # Process ticker responses
        ticker_results = []
        for resp in ticker_responses:
            if isinstance(resp, Exception):
                ticker_results.append(None)
            else:
                try:
                    data = await resp.json()
                    ticker_results.append(data)
                except:
                    ticker_results.append(None)

        # Combine funding rates with volume data and intervals
        funding_rates = []
        for i, symbol in enumerate(available_pairs):
            funding_data = funding_results[i]
            ticker_data = ticker_results[i]
            funding_freq = funding_intervals[i] if not isinstance(funding_intervals[i], Exception) else 3

            if isinstance(funding_data, Exception) or not funding_data:
                continue

            # Get current/next funding rate (already from premiumIndex endpoint)
            funding_rate = float(funding_data.get('fundingRate', 0))
            # Calculate APR using the correct frequency for this symbol
            effective_apr = funding_rate * funding_freq * 365 * 100  # funding_freq times per day, 365 days, convert to %

            # Calculate interval hours for display
            interval_hours = 24 / funding_freq if funding_freq > 0 else 8

            volume_24h = 0
            if ticker_data and not isinstance(ticker_data, Exception):
                volume_24h = float(ticker_data.get('quoteVolume', 0))  # Volume in USDT

            funding_rates.append({
                'symbol': symbol,
                'funding_rate': funding_rate * 100,  # Convert to percentage
                'effective_apr': effective_apr,
                'volume_24h': volume_24h,
                'funding_freq': funding_freq,
                'interval_hours': interval_hours
            })

        if not funding_rates:
            print(f"{Fore.RED}[ERROR] Failed to fetch funding rates{Style.RESET_ALL}")
            return

        print(f"{Fore.GREEN}[OK] Retrieved funding rates for {len(funding_rates)} pairs{Style.RESET_ALL}\n")

        # Volume filtering threshold (from strategy logic)
        min_volume_threshold = 250_000_000  # $250M minimum 24h volume

        # Separate pairs by volume threshold
        high_volume_pairs = []
        low_volume_pairs = []

        for rate_info in funding_rates:
            symbol = rate_info.get('symbol')
            volume_24h = rate_info.get('volume_24h', 0)

            if volume_24h >= min_volume_threshold:
                high_volume_pairs.append(rate_info)
            else:
                low_volume_pairs.append(rate_info)

        # Sort by effective APR (descending)
        high_volume_pairs.sort(key=lambda x: x.get('effective_apr', 0), reverse=True)
        low_volume_pairs.sort(key=lambda x: x.get('effective_apr', 0), reverse=True)

        # Display results
        print(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}")
        print(f"{Fore.GREEN}[OK] PAIRS MEETING VOLUME REQUIREMENTS (>= ${min_volume_threshold/1e6:.0f}M){Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}\n")

        if high_volume_pairs:
            print(f"{'Symbol':<13} {'Interval':<10} {'Funding Rate':<15} {'Effective APR':<15} {'24h Volume':<20}")
            print(f"{'-'*90}")

            for rate_info in high_volume_pairs:
                symbol = rate_info.get('symbol', 'N/A')
                funding_rate = rate_info.get('funding_rate', 0)
                effective_apr = rate_info.get('effective_apr', 0)
                volume_24h = rate_info.get('volume_24h', 0)
                interval_hours = rate_info.get('interval_hours', 8)
                funding_freq = rate_info.get('funding_freq', 3)

                # Format interval display
                interval_str = f"{int(interval_hours)}h/{funding_freq}x"

                # Color code by APR
                if effective_apr >= 20:
                    apr_color = Fore.GREEN
                elif effective_apr >= 10:
                    apr_color = Fore.YELLOW
                else:
                    apr_color = Fore.WHITE

                print(f"{symbol:<13} {interval_str:<10} {funding_rate:>13.4f}%  {apr_color}{effective_apr:>13.2f}%{Style.RESET_ALL}  ${volume_24h:>18,.0f}")

            print(f"\n{Fore.GREEN}Total: {len(high_volume_pairs)} pairs eligible for trading{Style.RESET_ALL}")
        else:
            print(f"{Fore.RED}No pairs meet the volume requirement{Style.RESET_ALL}")

        print(f"\n{Fore.CYAN}{'='*80}{Style.RESET_ALL}")
        print(f"{Fore.RED}[ERROR] PAIRS FILTERED OUT BY VOLUME (< ${min_volume_threshold/1e6:.0f}M){Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}\n")

        if low_volume_pairs:
            print(f"{'Symbol':<13} {'Interval':<10} {'Funding Rate':<15} {'Effective APR':<15} {'24h Volume':<20}")
            print(f"{'-'*90}")

            for rate_info in low_volume_pairs:
                symbol = rate_info.get('symbol', 'N/A')
                funding_rate = rate_info.get('funding_rate', 0)
                effective_apr = rate_info.get('effective_apr', 0)
                volume_24h = rate_info.get('volume_24h', 0)
                interval_hours = rate_info.get('interval_hours', 8)
                funding_freq = rate_info.get('funding_freq', 3)

                # Format interval display
                interval_str = f"{int(interval_hours)}h/{funding_freq}x"

                print(f"{Fore.RED}{symbol:<13}{Style.RESET_ALL} {interval_str:<10} {funding_rate:>13.4f}%  {effective_apr:>13.2f}%  ${volume_24h:>18,.0f}")

            print(f"\n{Fore.RED}Total: {len(low_volume_pairs)} pairs filtered out (insufficient volume){Style.RESET_ALL}")
        else:
            print(f"{Fore.GREEN}All pairs meet the volume requirement{Style.RESET_ALL}")

        # Summary statistics
        print(f"\n{Fore.CYAN}{'='*80}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}SUMMARY{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}\n")

        total_pairs = len(funding_rates)
        eligible_pairs = len(high_volume_pairs)
        filtered_pairs = len(low_volume_pairs)
        filter_percentage = (filtered_pairs / total_pairs * 100) if total_pairs > 0 else 0

        print(f"Total pairs discovered:     {Fore.CYAN}{total_pairs}{Style.RESET_ALL}")
        print(f"Pairs meeting volume req:   {Fore.GREEN}{eligible_pairs}{Style.RESET_ALL}")
        print(f"Pairs filtered out:         {Fore.RED}{filtered_pairs}{Style.RESET_ALL} ({filter_percentage:.1f}%)")
        print(f"Volume threshold:           {Fore.YELLOW}${min_volume_threshold:,.0f}{Style.RESET_ALL} (${min_volume_threshold/1e6:.0f}M)")

        if high_volume_pairs:
            best_pair = high_volume_pairs[0]
            print(f"\nBest opportunity:           {Fore.MAGENTA}{best_pair['symbol']}{Style.RESET_ALL} @ {Fore.GREEN}{best_pair['effective_apr']:.2f}%{Style.RESET_ALL} APR")

        print(f"\n{Fore.CYAN}{'='*80}{Style.RESET_ALL}\n")

    except Exception as e:
        print(f"\n{Fore.RED}[ERROR] Error: {e}{Style.RESET_ALL}")
        import traceback
        traceback.print_exc()

    finally:
        # Close API connections
        await api_manager.close()


async def main():
    """Main entry point."""
    try:
        await check_funding_rates()
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Interrupted by user{Style.RESET_ALL}")
    except Exception as e:
        print(f"\n{Fore.RED}Fatal error: {e}{Style.RESET_ALL}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
