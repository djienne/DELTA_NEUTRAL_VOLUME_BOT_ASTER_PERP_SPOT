#!/usr/bin/env python3
"""
Emergency exit: halt the bot, then close the tracked position (perp first, then spot).

halt.json is written first so a running bot cannot open a new position once this one
is closed. It keeps monitoring but will not trade until you check both markets and
delete halt.json.
"""

import asyncio
import json
import os
from datetime import datetime

from dotenv import load_dotenv

from aster_api_manager import AsterApiManager
from two_leg import write_halt

STATE_FILE = 'volume_farming_state.json'


async def main():
    load_dotenv()
    if not os.path.exists(STATE_FILE):
        print(f"No {STATE_FILE}: nothing tracked to close.")
        return
    with open(STATE_FILE) as f:
        state = json.load(f)
    position = state.get('current_position')
    if not position:
        print("No position tracked in the state file.")
        return

    symbol, spot_qty = position['symbol'], float(position['spot_qty'])
    print(f"Will close {symbol}: perp short at market (reduce-only), then sell up to {spot_qty} spot.")
    input("Press ENTER to halt the bot and close, or Ctrl+C to cancel: ")
    write_halt("emergency_exit.py", symbol=symbol, venue="Aster", residual_qty=float(position.get('perp_qty') or 0))

    api = AsterApiManager(
        api_user=os.getenv('API_USER'),
        api_signer=os.getenv('API_SIGNER'),
        api_private_key=os.getenv('API_PRIVATE_KEY'),
        apiv1_public=os.getenv('APIV1_PUBLIC_KEY'),
        apiv1_private=os.getenv('APIV1_PRIVATE_KEY'))
    try:
        result = await api.close_dn(symbol, spot_qty)
    finally:
        await api.close()
    print(result['message'])

    if result['success']:
        state.update(current_position=None, position_opened_at=None, position_leverage=None,
                     entry_cash=None, last_updated=datetime.utcnow().isoformat())
        with open(STATE_FILE + '.tmp', 'w') as f:
            json.dump(state, f, indent=2)
        os.replace(STATE_FILE + '.tmp', STATE_FILE)
        print("Closed and state cleared. The bot stays halted until you delete halt.json.")
    else:
        print("NOT fully closed - check both markets manually. The bot stays halted.")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nCancelled; nothing was sent.")
