#!/usr/bin/env python3
"""
Test script to verify MA calculation fixes
"""

import asyncio
import os
from dotenv import load_dotenv
from colorama import Fore, Style, init

from aster_api_manager import AsterApiManager

init(autoreset=True)
load_dotenv()


async def test_ma_calculation():
    """Test MA calculation with correct frequencies"""

    api_manager = AsterApiManager(
        api_user=os.getenv('API_USER'),
        api_signer=os.getenv('API_SIGNER'),
        api_private_key=os.getenv('API_PRIVATE_KEY'),
        apiv1_public=os.getenv('APIV1_PUBLIC_KEY'),
        apiv1_private=os.getenv('APIV1_PRIVATE_KEY')
    )

    try:
        print(f"\n{Fore.CYAN}{'='*80}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}Testing MA Calculation with Correct Frequencies{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}\n")

        # Test symbols with different funding intervals
        test_symbols = ['BTCUSDT', 'ASTERUSDT']

        for symbol in test_symbols:
            print(f"\n{Fore.YELLOW}Testing {symbol}...{Style.RESET_ALL}")

            # Detect funding interval
            funding_freq = await api_manager.detect_funding_interval(symbol)
            interval_hours = 24 / funding_freq if funding_freq > 0 else 8

            print(f"  Funding Frequency: {funding_freq}x per day ({int(interval_hours)}h intervals)")

            # Get MA funding rate
            ma_result = await api_manager.get_funding_rate_ma(symbol, periods=10)

            if ma_result:
                print(f"\n  {Fore.GREEN}MA Calculation Results:{Style.RESET_ALL}")
                print(f"    Current Rate:  {ma_result['current_rate']*100:.4f}%")
                print(f"    MA Rate:       {ma_result['ma_rate']*100:.4f}%")
                print(f"    MA APR:        {ma_result['ma_apr']:.2f}%")
                print(f"    Effective APR: {ma_result['effective_ma_apr']:.2f}%")
                print(f"    Periods Used:  {ma_result['ma_periods']}")
                print(f"    Funding Freq:  {ma_result.get('funding_freq', 'N/A')}")
                print(f"    StDev:         {ma_result['stdev']*100:.4f}%")

                # Verify the calculation
                expected_ma_apr = ma_result['ma_rate'] * funding_freq * 365 * 100
                actual_ma_apr = ma_result['ma_apr']

                if abs(expected_ma_apr - actual_ma_apr) < 0.01:
                    print(f"\n  {Fore.GREEN}[OK] MA APR calculation is CORRECT{Style.RESET_ALL}")
                    print(f"    Expected: {expected_ma_apr:.2f}% (using freq={funding_freq})")
                    print(f"    Actual:   {actual_ma_apr:.2f}%")
                else:
                    print(f"\n  {Fore.RED}[ERROR] MA APR calculation MISMATCH{Style.RESET_ALL}")
                    print(f"    Expected: {expected_ma_apr:.2f}% (using freq={funding_freq})")
                    print(f"    Actual:   {actual_ma_apr:.2f}%")
            else:
                print(f"  {Fore.RED}[ERROR] Failed to get MA data{Style.RESET_ALL}")

        print(f"\n{Fore.CYAN}{'='*80}{Style.RESET_ALL}\n")

    except Exception as e:
        print(f"\n{Fore.RED}[ERROR] {e}{Style.RESET_ALL}")
        import traceback
        traceback.print_exc()

    finally:
        await api_manager.close()


if __name__ == "__main__":
    asyncio.run(test_ma_calculation())
