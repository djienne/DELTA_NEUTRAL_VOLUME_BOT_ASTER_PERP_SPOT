#!/usr/bin/env python3
"""
Detect Funding Intervals for Aster DEX

This script analyzes funding rate history to determine the actual
funding interval for each symbol (4h, 8h, etc.)
"""

import asyncio
import os
from datetime import datetime
from colorama import Fore, Style, init
from dotenv import load_dotenv
from collections import Counter

from aster_api_manager import AsterApiManager

# Initialize colorama
init(autoreset=True)

# Load environment variables
load_dotenv()


async def detect_funding_intervals():
    """
    Analyze funding rate history to detect actual funding intervals.
    """
    print(f"\n{Fore.CYAN}{'='*80}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Funding Interval Detection - {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC{Style.RESET_ALL}")
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

        # Analyze funding intervals for all pairs
        print(f"{Fore.YELLOW}Analyzing funding intervals...{Style.RESET_ALL}\n")

        interval_data = []

        for symbol in available_pairs:
            try:
                # Fetch more history to accurately detect interval
                history = await api_manager.get_funding_rate_history(symbol, limit=20)

                if not history or len(history) < 2:
                    interval_data.append({
                        'symbol': symbol,
                        'interval_hours': None,
                        'intervals_per_day': None,
                        'status': 'INSUFFICIENT DATA'
                    })
                    continue

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
                    intervals_per_day = 24 / interval_hours if interval_hours > 0 else 0

                    interval_data.append({
                        'symbol': symbol,
                        'interval_hours': interval_hours,
                        'intervals_per_day': intervals_per_day,
                        'status': 'DETECTED'
                    })
                else:
                    interval_data.append({
                        'symbol': symbol,
                        'interval_hours': None,
                        'intervals_per_day': None,
                        'status': 'COULD NOT DETECT'
                    })

            except Exception as e:
                interval_data.append({
                    'symbol': symbol,
                    'interval_hours': None,
                    'intervals_per_day': None,
                    'status': f'ERROR: {str(e)}'
                })

        # Group by interval
        intervals_4h = []
        intervals_8h = []
        intervals_other = []
        intervals_unknown = []

        for data in interval_data:
            if data['interval_hours'] == 4:
                intervals_4h.append(data)
            elif data['interval_hours'] == 8:
                intervals_8h.append(data)
            elif data['interval_hours'] is not None:
                intervals_other.append(data)
            else:
                intervals_unknown.append(data)

        # Display results
        print(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}")
        print(f"{Fore.GREEN}[OK] SYMBOLS WITH 4-HOUR FUNDING (6x per day){Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}\n")

        if intervals_4h:
            print(f"{'Symbol':<15} {'Interval':<15} {'Freq/Day':<15}")
            print(f"{'-'*45}")
            for data in intervals_4h:
                print(f"{Fore.GREEN}{data['symbol']:<15}{Style.RESET_ALL} {data['interval_hours']:<15} {data['intervals_per_day']:<15.1f}")
            print(f"\n{Fore.GREEN}Total: {len(intervals_4h)} symbols{Style.RESET_ALL}")
        else:
            print(f"{Fore.YELLOW}No symbols with 4-hour funding found{Style.RESET_ALL}")

        print(f"\n{Fore.CYAN}{'='*80}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}[OK] SYMBOLS WITH 8-HOUR FUNDING (3x per day){Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}\n")

        if intervals_8h:
            print(f"{'Symbol':<15} {'Interval':<15} {'Freq/Day':<15}")
            print(f"{'-'*45}")
            for data in intervals_8h:
                print(f"{Fore.YELLOW}{data['symbol']:<15}{Style.RESET_ALL} {data['interval_hours']:<15} {data['intervals_per_day']:<15.1f}")
            print(f"\n{Fore.YELLOW}Total: {len(intervals_8h)} symbols{Style.RESET_ALL}")
        else:
            print(f"{Fore.RED}No symbols with 8-hour funding found{Style.RESET_ALL}")

        if intervals_other:
            print(f"\n{Fore.CYAN}{'='*80}{Style.RESET_ALL}")
            print(f"{Fore.MAGENTA}[OK] SYMBOLS WITH OTHER INTERVALS{Style.RESET_ALL}")
            print(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}\n")

            print(f"{'Symbol':<15} {'Interval':<15} {'Freq/Day':<15}")
            print(f"{'-'*45}")
            for data in intervals_other:
                print(f"{Fore.MAGENTA}{data['symbol']:<15}{Style.RESET_ALL} {data['interval_hours']:<15} {data['intervals_per_day']:<15.1f}")
            print(f"\n{Fore.MAGENTA}Total: {len(intervals_other)} symbols{Style.RESET_ALL}")

        if intervals_unknown:
            print(f"\n{Fore.CYAN}{'='*80}{Style.RESET_ALL}")
            print(f"{Fore.RED}[ERROR] SYMBOLS WITH UNKNOWN INTERVALS{Style.RESET_ALL}")
            print(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}\n")

            print(f"{'Symbol':<15} {'Status':<50}")
            print(f"{'-'*65}")
            for data in intervals_unknown:
                print(f"{Fore.RED}{data['symbol']:<15}{Style.RESET_ALL} {data['status']:<50}")
            print(f"\n{Fore.RED}Total: {len(intervals_unknown)} symbols{Style.RESET_ALL}")

        # Summary
        print(f"\n{Fore.CYAN}{'='*80}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}SUMMARY{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}\n")

        print(f"Total symbols:              {Fore.CYAN}{len(interval_data)}{Style.RESET_ALL}")
        print(f"4-hour funding (6x/day):    {Fore.GREEN}{len(intervals_4h)}{Style.RESET_ALL}")
        print(f"8-hour funding (3x/day):    {Fore.YELLOW}{len(intervals_8h)}{Style.RESET_ALL}")
        print(f"Other intervals:            {Fore.MAGENTA}{len(intervals_other)}{Style.RESET_ALL}")
        print(f"Unknown:                    {Fore.RED}{len(intervals_unknown)}{Style.RESET_ALL}")

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
        await detect_funding_intervals()
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Interrupted by user{Style.RESET_ALL}")
    except Exception as e:
        print(f"\n{Fore.RED}Fatal error: {e}{Style.RESET_ALL}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
