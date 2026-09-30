#!/usr/bin/env python3
"""Print the bot's own funding scan (same filters and averages) without trading."""

import asyncio
import logging

from volume_farming_strategy import VolumeFarmingStrategy, load_config


async def main():
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    strategy = VolumeFarmingStrategy(**load_config())
    try:
        _, best = await strategy._scan()
        print(f"\nBest: {best['symbol']} at {best['avg_apr']:.2f}% APR" if best
              else f"\nNo pair reaches the {strategy.min_funding_apr}% entry threshold")
    finally:
        await strategy.api_manager.close()


if __name__ == '__main__':
    asyncio.run(main())
