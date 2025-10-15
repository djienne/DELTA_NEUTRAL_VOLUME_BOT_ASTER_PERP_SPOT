#!/usr/bin/env python3
"""
Test script to verify funding rate history order (newest first vs oldest first)
"""

import asyncio
import os
from datetime import datetime
from dotenv import load_dotenv

from aster_api_manager import AsterApiManager

load_dotenv()


async def test_funding_order():
    """Check if funding rates are returned newest-first or oldest-first"""

    api_manager = AsterApiManager(
        api_user=os.getenv('API_USER'),
        api_signer=os.getenv('API_SIGNER'),
        api_private_key=os.getenv('API_PRIVATE_KEY'),
        apiv1_public=os.getenv('APIV1_PUBLIC_KEY'),
        apiv1_private=os.getenv('APIV1_PRIVATE_KEY')
    )

    try:
        # Fetch BTC funding rate history
        print("Fetching BTCUSDT funding rate history (last 5 entries)...\n")
        history = await api_manager.get_funding_rate_history('BTCUSDT', limit=5)

        if not history:
            print("No history returned")
            return

        print(f"{'Index':<8} {'Funding Time (UTC)':<25} {'Funding Rate':<15} {'Time Ago'}")
        print("-" * 75)

        now = datetime.utcnow()

        for i, entry in enumerate(history):
            funding_time_ms = int(entry['fundingTime'])
            funding_time = datetime.utcfromtimestamp(funding_time_ms / 1000)
            funding_rate = float(entry['fundingRate'])

            time_diff = now - funding_time
            hours_ago = time_diff.total_seconds() / 3600

            print(f"[{i}]      {funding_time.strftime('%Y-%m-%d %H:%M:%S')}     {funding_rate*100:>12.6f}%   {hours_ago:.1f}h ago")

        print("\n" + "="*75)
        if history:
            first_time = datetime.utcfromtimestamp(int(history[0]['fundingTime']) / 1000)
            last_time = datetime.utcfromtimestamp(int(history[-1]['fundingTime']) / 1000)

            if first_time > last_time:
                print("✓ Order: NEWEST FIRST (index [0] is most recent)")
                print("  → Using index [0] for current rate is CORRECT")
            else:
                print("✗ Order: OLDEST FIRST (index [0] is oldest)")
                print("  → Should use index [-1] for current rate instead!")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

    finally:
        await api_manager.close()


if __name__ == "__main__":
    asyncio.run(test_funding_order())
