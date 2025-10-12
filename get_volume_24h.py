#!/usr/bin/env python3
"""
Get 24h trading volume for all pairs that have both spot and perpetual markets.
This shows volume from all traders on the exchange (not just your own trades).
"""

import asyncio
import aiohttp
from typing import List, Dict, Any
from colorama import init, Fore, Style

# Initialize colorama
init()

# Base URLs
FUTURES_BASE_URL = "https://fapi.asterdex.com"
SPOT_BASE_URL = "https://sapi.asterdex.com"


async def get_spot_24h_tickers(session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
    """Fetch 24h ticker data for all spot pairs."""
    try:
        url = f"{SPOT_BASE_URL}/api/v1/ticker/24hr"
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.json()
    except Exception as e:
        print(f"Error fetching spot 24h tickers: {e}")
        return []


async def get_perp_24h_tickers(session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
    """Fetch 24h ticker data for all perpetual pairs."""
    try:
        url = f"{FUTURES_BASE_URL}/fapi/v1/ticker/24hr"
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.json()
    except Exception as e:
        print(f"Error fetching perp 24h tickers: {e}")
        return []


async def get_spot_symbols(session: aiohttp.ClientSession) -> List[str]:
    """Get list of all spot trading symbols."""
    try:
        url = f"{SPOT_BASE_URL}/api/v1/exchangeInfo"
        async with session.get(url) as response:
            response.raise_for_status()
            data = await response.json()
            return [s['symbol'] for s in data.get('symbols', []) if s.get('status') == 'TRADING']
    except Exception as e:
        print(f"Error fetching spot symbols: {e}")
        return []


async def get_perp_symbols(session: aiohttp.ClientSession) -> List[str]:
    """Get list of all perpetual trading symbols."""
    try:
        url = f"{FUTURES_BASE_URL}/fapi/v1/exchangeInfo"
        async with session.get(url) as response:
            response.raise_for_status()
            data = await response.json()
            return [s['symbol'] for s in data.get('symbols', []) if s.get('status') == 'TRADING']
    except Exception as e:
        print(f"Error fetching perp symbols: {e}")
        return []


def find_delta_neutral_pairs(spot_symbols: List[str], perp_symbols: List[str]) -> List[str]:
    """Find symbols available in both spot and perpetual markets."""
    spot_set = set(spot_symbols)
    perp_set = set(perp_symbols)
    common_symbols = spot_set.intersection(perp_set)
    return sorted(list(common_symbols))


async def main():
    """Main function to fetch and display 24h volume data."""
    print(f"\n{Fore.CYAN}{'='*100}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}24h Trading Volume for Delta-Neutral Pairs (Spot + Perpetual){Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'='*100}{Style.RESET_ALL}\n")

    async with aiohttp.ClientSession() as session:
        # Fetch all required data concurrently
        print("Fetching data from Aster DEX...")
        spot_symbols, perp_symbols, spot_tickers, perp_tickers = await asyncio.gather(
            get_spot_symbols(session),
            get_perp_symbols(session),
            get_spot_24h_tickers(session),
            get_perp_24h_tickers(session)
        )

        # Find pairs available in both markets
        dn_pairs = find_delta_neutral_pairs(spot_symbols, perp_symbols)

        if not dn_pairs:
            print(f"{Fore.RED}No delta-neutral pairs found!{Style.RESET_ALL}")
            return

        print(f"Found {len(dn_pairs)} pairs with both spot and perpetual markets\n")

        # Create lookup dictionaries for volume data
        spot_volume_map = {
            ticker['symbol']: {
                'volume': float(ticker.get('volume', 0)),
                'quoteVolume': float(ticker.get('quoteVolume', 0)),
                'priceChange': float(ticker.get('priceChange', 0)),
                'priceChangePercent': float(ticker.get('priceChangePercent', 0))
            }
            for ticker in spot_tickers
        }

        perp_volume_map = {
            ticker['symbol']: {
                'volume': float(ticker.get('volume', 0)),
                'quoteVolume': float(ticker.get('quoteVolume', 0)),
                'priceChange': float(ticker.get('priceChange', 0)),
                'priceChangePercent': float(ticker.get('priceChangePercent', 0))
            }
            for ticker in perp_tickers
        }

        # Prepare data for display
        volume_data = []
        for symbol in dn_pairs:
            spot_data = spot_volume_map.get(symbol, {})
            perp_data = perp_volume_map.get(symbol, {})

            spot_vol_usd = spot_data.get('quoteVolume', 0)
            perp_vol_usd = perp_data.get('quoteVolume', 0)
            total_vol_usd = spot_vol_usd + perp_vol_usd

            spot_price_change_pct = spot_data.get('priceChangePercent', 0)
            perp_price_change_pct = perp_data.get('priceChangePercent', 0)

            volume_data.append({
                'symbol': symbol,
                'spot_volume_usd': spot_vol_usd,
                'perp_volume_usd': perp_vol_usd,
                'total_volume_usd': total_vol_usd,
                'spot_price_change_pct': spot_price_change_pct,
                'perp_price_change_pct': perp_price_change_pct
            })

        # Sort by total volume (descending)
        volume_data.sort(key=lambda x: x['total_volume_usd'], reverse=True)

        # Display table
        print(f"{Fore.CYAN}{'='*100}{Style.RESET_ALL}")
        header = f"{'Symbol':<12} {'Spot Vol (USDT)':<18} {'Perp Vol (USDT)':<18} {'Total Vol (USDT)':<18} {'24h Chg %':<12}"
        print(header)
        print(f"{Fore.CYAN}{'-'*100}{Style.RESET_ALL}")

        total_spot_volume = 0
        total_perp_volume = 0
        total_combined_volume = 0

        for data in volume_data:
            symbol = data['symbol']
            spot_vol = data['spot_volume_usd']
            perp_vol = data['perp_volume_usd']
            total_vol = data['total_volume_usd']
            price_change = data['spot_price_change_pct']  # Use spot price change

            # Accumulate totals
            total_spot_volume += spot_vol
            total_perp_volume += perp_vol
            total_combined_volume += total_vol

            # Color code based on price change
            if price_change > 0:
                price_color = Fore.GREEN
                price_sign = "+"
            elif price_change < 0:
                price_color = Fore.RED
                price_sign = ""
            else:
                price_color = Style.RESET_ALL
                price_sign = " "

            # Format volumes with proper grouping
            spot_vol_str = f"${spot_vol:,.2f}"
            perp_vol_str = f"${perp_vol:,.2f}"
            total_vol_str = f"${total_vol:,.2f}"
            price_change_str = f"{price_sign}{price_change:.2f}%"

            print(f"{symbol:<12} {spot_vol_str:<18} {perp_vol_str:<18} {total_vol_str:<18} {price_color}{price_change_str:<12}{Style.RESET_ALL}")

        # Print totals
        print(f"{Fore.CYAN}{'-'*100}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}{'TOTAL':<12} ${total_spot_volume:,.2f}{'':<7} ${total_perp_volume:,.2f}{'':<7} ${total_combined_volume:,.2f}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'='*100}{Style.RESET_ALL}\n")

        # Summary statistics
        print(f"{Fore.CYAN}Summary:{Style.RESET_ALL}")
        print(f"  Total pairs: {len(volume_data)}")
        print(f"  Total 24h spot volume: ${total_spot_volume:,.2f}")
        print(f"  Total 24h perp volume: ${total_perp_volume:,.2f}")
        print(f"  Combined 24h volume: ${total_combined_volume:,.2f}")

        if total_combined_volume > 0:
            spot_pct = (total_spot_volume / total_combined_volume) * 100
            perp_pct = (total_perp_volume / total_combined_volume) * 100
            print(f"  Spot/Perp split: {spot_pct:.1f}% / {perp_pct:.1f}%")

        # Find highest volume pair
        if volume_data:
            highest = volume_data[0]
            print(f"\n{Fore.GREEN}Highest volume pair: {highest['symbol']} (${highest['total_volume_usd']:,.2f}){Style.RESET_ALL}")

        print()


if __name__ == '__main__':
    asyncio.run(main())
