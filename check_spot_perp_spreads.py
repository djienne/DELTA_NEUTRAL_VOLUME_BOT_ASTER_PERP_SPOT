#!/usr/bin/env python3
"""
Spot-Perp Price Spread Checker

This script displays mid prices for all delta-neutral pairs across spot and perpetual markets,
showing the price spread between them.
"""

import asyncio
import aiohttp
import os
from datetime import datetime
from colorama import Fore, Style, init
from dotenv import load_dotenv

from aster_api_manager import AsterApiManager

# Initialize colorama
init(autoreset=True)

# Load environment variables
load_dotenv()


async def check_price_spreads():
    """
    Check and display spot vs perp price spreads for all delta-neutral pairs.
    """
    print(f"\n{Fore.CYAN}{'='*100}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Spot-Perp Price Spread Analysis - {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'='*100}{Style.RESET_ALL}\n")

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

        # Fetch spot and perp book tickers concurrently
        print(f"{Fore.YELLOW}Fetching spot and perpetual prices...{Style.RESET_ALL}")

        spot_tasks = [api_manager.get_spot_book_ticker(symbol, suppress_errors=True) for symbol in available_pairs]
        perp_tasks = [api_manager.get_perp_book_ticker(symbol) for symbol in available_pairs]

        spot_results = await asyncio.gather(*spot_tasks, return_exceptions=True)
        perp_results = await asyncio.gather(*perp_tasks, return_exceptions=True)

        # Process results
        spread_data = []
        for i, symbol in enumerate(available_pairs):
            spot_data = spot_results[i]
            perp_data = perp_results[i]

            # Skip if either fetch failed
            if isinstance(spot_data, Exception) or isinstance(perp_data, Exception):
                continue

            # Skip if missing price data
            if not spot_data or not perp_data:
                continue

            spot_bid = spot_data.get('bidPrice')
            spot_ask = spot_data.get('askPrice')
            perp_bid = perp_data.get('bidPrice')
            perp_ask = perp_data.get('askPrice')

            # Skip if any price is missing
            if not all([spot_bid, spot_ask, perp_bid, perp_ask]):
                continue

            # Convert to float
            spot_bid = float(spot_bid)
            spot_ask = float(spot_ask)
            perp_bid = float(perp_bid)
            perp_ask = float(perp_ask)

            # Calculate mid prices
            spot_mid = (spot_bid + spot_ask) / 2
            perp_mid = (perp_bid + perp_ask) / 2

            # Calculate spread (perp - spot)
            spread_absolute = perp_mid - spot_mid
            spread_percent = (spread_absolute / spot_mid) * 100

            spread_data.append({
                'symbol': symbol,
                'spot_mid': spot_mid,
                'perp_mid': perp_mid,
                'spread_absolute': spread_absolute,
                'spread_percent': spread_percent
            })

        if not spread_data:
            print(f"{Fore.RED}[ERROR] Failed to fetch price data{Style.RESET_ALL}")
            return

        print(f"{Fore.GREEN}[OK] Retrieved prices for {len(spread_data)} pairs{Style.RESET_ALL}\n")

        # Sort by absolute spread percentage (largest first)
        spread_data.sort(key=lambda x: abs(x.get('spread_percent', 0)), reverse=True)

        # Display results table
        print(f"{Fore.CYAN}{'='*100}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}SPOT vs PERP PRICE SPREADS{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'='*100}{Style.RESET_ALL}\n")

        # Table header
        print(f"{'Symbol':<15} {'Spot Mid':<18} {'Perp Mid':<18} {'Spread ($)':<18} {'Spread (%)':<15}")
        print(f"{'-'*100}")

        for item in spread_data:
            symbol = item['symbol']
            spot_mid = item['spot_mid']
            perp_mid = item['perp_mid']
            spread_abs = item['spread_absolute']
            spread_pct = item['spread_percent']

            # Color code based on spread direction and magnitude
            if abs(spread_pct) >= 0.1:  # >= 0.1% spread
                spread_color = Fore.RED
            elif abs(spread_pct) >= 0.05:  # >= 0.05% spread
                spread_color = Fore.YELLOW
            else:
                spread_color = Fore.GREEN

            # Format with appropriate precision based on price magnitude
            if spot_mid >= 1000:
                spot_str = f"${spot_mid:,.2f}"
                perp_str = f"${perp_mid:,.2f}"
            elif spot_mid >= 1:
                spot_str = f"${spot_mid:,.4f}"
                perp_str = f"${perp_mid:,.4f}"
            else:
                spot_str = f"${spot_mid:.8f}"
                perp_str = f"${perp_mid:.8f}"

            # Format spread with sign
            spread_sign = "+" if spread_abs >= 0 else ""
            spread_abs_str = f"{spread_sign}${spread_abs:.4f}"
            spread_pct_str = f"{spread_sign}{spread_pct:.4f}%"

            print(f"{symbol:<15} {spot_str:<18} {perp_str:<18} {spread_color}{spread_abs_str:<18}{Style.RESET_ALL} {spread_color}{spread_pct_str:<15}{Style.RESET_ALL}")

        # Summary statistics
        print(f"\n{Fore.CYAN}{'='*100}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}SUMMARY{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'='*100}{Style.RESET_ALL}\n")

        total_pairs = len(spread_data)
        avg_spread_pct = sum(abs(x['spread_percent']) for x in spread_data) / total_pairs if total_pairs > 0 else 0
        max_spread = max(spread_data, key=lambda x: abs(x['spread_percent'])) if spread_data else None
        min_spread = min(spread_data, key=lambda x: abs(x['spread_percent'])) if spread_data else None

        # Count premium vs discount
        perp_premium = sum(1 for x in spread_data if x['spread_percent'] > 0)
        perp_discount = sum(1 for x in spread_data if x['spread_percent'] < 0)
        exact_match = sum(1 for x in spread_data if x['spread_percent'] == 0)

        print(f"Total pairs analyzed:       {Fore.CYAN}{total_pairs}{Style.RESET_ALL}")
        print(f"Average absolute spread:    {Fore.YELLOW}{avg_spread_pct:.4f}%{Style.RESET_ALL}")

        if max_spread:
            max_color = Fore.RED if abs(max_spread['spread_percent']) >= 0.1 else Fore.YELLOW
            print(f"Largest spread:             {Fore.MAGENTA}{max_spread['symbol']}{Style.RESET_ALL} @ {max_color}{max_spread['spread_percent']:.4f}%{Style.RESET_ALL}")

        if min_spread:
            print(f"Smallest spread:            {Fore.MAGENTA}{min_spread['symbol']}{Style.RESET_ALL} @ {Fore.GREEN}{min_spread['spread_percent']:.4f}%{Style.RESET_ALL}")

        print(f"\nPerp trading at premium:    {Fore.GREEN}{perp_premium}{Style.RESET_ALL} pairs ({perp_premium/total_pairs*100:.1f}%)")
        print(f"Perp trading at discount:   {Fore.RED}{perp_discount}{Style.RESET_ALL} pairs ({perp_discount/total_pairs*100:.1f}%)")
        if exact_match > 0:
            print(f"Exact price match:          {Fore.CYAN}{exact_match}{Style.RESET_ALL} pairs")

        print(f"\n{Fore.CYAN}{'='*100}{Style.RESET_ALL}")
        print(f"\n{Fore.YELLOW}Note:{Style.RESET_ALL} Positive spread means perp is more expensive than spot (premium)")
        print(f"{Fore.YELLOW}      Negative spread means perp is cheaper than spot (discount){Style.RESET_ALL}\n")

    except Exception as e:
        print(f"\n{Fore.RED}[ERROR] {e}{Style.RESET_ALL}")
        import traceback
        traceback.print_exc()

    finally:
        # Close API connections
        await api_manager.close()


async def main():
    """Main entry point."""
    try:
        await check_price_spreads()
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Interrupted by user{Style.RESET_ALL}")
    except Exception as e:
        print(f"\n{Fore.RED}Fatal error: {e}{Style.RESET_ALL}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
