#!/usr/bin/env python3
"""
Test script to find the current/next funding rate endpoint
"""

import asyncio
import aiohttp
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


async def test_funding_endpoints():
    """Test various endpoints to find current funding rate"""

    base_url = "https://fapi.asterdex.com"
    symbol = "ASTERUSDT"

    # List of potential endpoints
    endpoints = [
        "/fapi/v1/premiumIndex",
        "/fapi/v1/fundingInfo",
        "/fapi/v1/ticker/24hr",
        "/fapi/v1/fundingRate",
    ]

    async with aiohttp.ClientSession() as session:
        for endpoint in endpoints:
            try:
                url = f"{base_url}{endpoint}"
                params = {'symbol': symbol}

                print(f"\nTesting: {url}")
                print(f"Params: {params}")

                async with session.get(url, params=params, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        print(f"[OK] Status: {response.status}")
                        print(f"Response keys: {list(data.keys()) if isinstance(data, dict) else 'List response'}")

                        # Look for funding rate fields
                        if isinstance(data, dict):
                            for key in ['fundingRate', 'lastFundingRate', 'nextFundingRate', 'rate']:
                                if key in data:
                                    print(f"  -> {key}: {data[key]}")
                        elif isinstance(data, list) and data:
                            print(f"  -> List with {len(data)} items")
                            if len(data) > 0:
                                print(f"  -> First item keys: {list(data[0].keys())}")
                                print(f"  -> Last item: {data[-1]}")
                    else:
                        print(f"[ERROR] Status: {response.status}")

            except Exception as e:
                print(f"[ERROR] Error: {e}")


if __name__ == "__main__":
    asyncio.run(test_funding_endpoints())
