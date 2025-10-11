#!/usr/bin/env python3
"""
Emergency Exit Script - Closes Delta-Neutral Position Immediately

This script will:
1. Read current position from state file
2. Display position details
3. Ask for confirmation
4. Close both spot and perpetual legs simultaneously
"""

import asyncio
import os
import json
import subprocess
from datetime import datetime
from dotenv import load_dotenv
from colorama import init, Fore, Style

from aster_api_manager import AsterApiManager

# Initialize
load_dotenv()
init()


async def main():
    print(f"{Fore.RED}{'='*80}{Style.RESET_ALL}")
    print(f"{Fore.RED}⚠️  EMERGENCY EXIT - DELTA NEUTRAL POSITION CLOSER{Style.RESET_ALL}")
    print(f"{Fore.RED}{'='*80}{Style.RESET_ALL}\n")

    # Load state file
    state_file = 'volume_farming_state.json'
    if not os.path.exists(state_file):
        print(f"{Fore.RED}ERROR: State file '{state_file}' not found!{Style.RESET_ALL}")
        print(f"No position to close.")
        return

    try:
        with open(state_file, 'r') as f:
            state = json.load(f)
    except json.JSONDecodeError as e:
        print(f"{Fore.RED}ERROR: Failed to read state file: {e}{Style.RESET_ALL}")
        return

    # Check if there's a position
    current_position = state.get('current_position')
    if not current_position:
        print(f"{Fore.YELLOW}No open position found in state file.{Style.RESET_ALL}")
        return

    # Display position details
    symbol = current_position.get('symbol', 'UNKNOWN')
    spot_qty = current_position.get('spot_qty', 0)
    perp_qty = current_position.get('perp_qty', 0)
    capital = current_position.get('capital', 0)
    entry_price = current_position.get('entry_price', 0)
    position_leverage = state.get('position_leverage', 1)

    position_opened_at = state.get('position_opened_at', 'Unknown')
    if position_opened_at != 'Unknown':
        try:
            opened_dt = datetime.fromisoformat(position_opened_at)
            position_opened_at = opened_dt.strftime('%Y-%m-%d %H:%M:%S UTC')
        except:
            pass

    print(f"{Fore.CYAN}Current Position Details:{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}")
    print(f"  Symbol:        {Fore.MAGENTA}{symbol}{Style.RESET_ALL}")
    print(f"  Leverage:      {Fore.MAGENTA}{position_leverage}x{Style.RESET_ALL}")
    print(f"  Capital:       {Fore.GREEN}${capital:.2f}{Style.RESET_ALL}")
    print(f"  Entry Price:   {Fore.YELLOW}${entry_price:.4f}{Style.RESET_ALL}")
    print(f"  Opened At:     {Fore.YELLOW}{position_opened_at}{Style.RESET_ALL}")
    print()
    print(f"  {Fore.CYAN}Position to Close:{Style.RESET_ALL}")
    print(f"    Spot:  {Fore.GREEN}SELL {spot_qty:.8f} {symbol.replace('USDT', '')}{Style.RESET_ALL}")
    print(f"    Perp:  {Fore.GREEN}BUY {perp_qty:.8f} {symbol}{Style.RESET_ALL} (close short)")
    print(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}\n")

    # Initialize API manager early to fetch PnL
    try:
        api_manager = AsterApiManager(
            api_user=os.getenv('API_USER'),
            api_signer=os.getenv('API_SIGNER'),
            api_private_key=os.getenv('API_PRIVATE_KEY'),
            apiv1_public=os.getenv('APIV1_PUBLIC_KEY'),
            apiv1_private=os.getenv('APIV1_PRIVATE_KEY')
        )
    except Exception as e:
        print(f"{Fore.RED}ERROR: Failed to initialize API manager: {e}{Style.RESET_ALL}")
        return

    # Fetch current position PnL
    print(f"{Fore.CYAN}Fetching current PnL...{Style.RESET_ALL}\n")
    try:
        # Get current price and position data
        perp_account = await api_manager.get_perp_account_info()
        perp_ticker = await api_manager.get_perp_book_ticker(symbol)

        # Get current price (mid-price from book ticker)
        current_price = (float(perp_ticker['bidPrice']) + float(perp_ticker['askPrice'])) / 2

        # Find perp position
        perp_positions = perp_account.get('positions', [])
        perp_pos = next((p for p in perp_positions if p.get('symbol') == symbol and float(p.get('positionAmt', 0)) != 0), None)

        if perp_pos:
            # Perp unrealized PnL
            perp_unrealized_pnl = float(perp_pos.get('unrealizedProfit', 0))

            # Spot unrealized PnL
            spot_unrealized_pnl = 0.0
            if entry_price > 0:
                spot_unrealized_pnl = spot_qty * (current_price - entry_price)

            # Combined PnL (excluding fees and funding for simplicity - just show position PnL)
            combined_pnl = perp_unrealized_pnl + spot_unrealized_pnl
            combined_pnl_pct = (combined_pnl / capital * 100) if capital > 0 else 0

            # Display PnL
            print(f"{Fore.CYAN}Current Position PnL:{Style.RESET_ALL}")
            print(f"  Current Price:     {Fore.YELLOW}${current_price:.4f}{Style.RESET_ALL}")

            perp_pnl_color = Fore.GREEN if perp_unrealized_pnl >= 0 else Fore.RED
            spot_pnl_color = Fore.GREEN if spot_unrealized_pnl >= 0 else Fore.RED
            combined_pnl_color = Fore.GREEN if combined_pnl >= 0 else Fore.RED

            print(f"  Perp PnL:          {perp_pnl_color}${perp_unrealized_pnl:.2f}{Style.RESET_ALL}")
            print(f"  Spot PnL:          {spot_pnl_color}${spot_unrealized_pnl:.2f}{Style.RESET_ALL}")
            print(f"  Combined PnL:      {combined_pnl_color}${combined_pnl:.2f} ({combined_pnl_pct:+.2f}%){Style.RESET_ALL}")

            # Show funding and fees info if available
            total_funding = state.get('total_funding_received', 0)
            entry_fees = state.get('entry_fees_paid', 0)
            if total_funding > 0 or entry_fees > 0:
                exit_fees_estimate = capital * 0.001
                net_pnl = combined_pnl + total_funding - entry_fees - exit_fees_estimate
                net_pnl_color = Fore.GREEN if net_pnl >= 0 else Fore.RED
                print(f"  Funding Received:  {Fore.GREEN}${total_funding:.2f}{Style.RESET_ALL}")
                print(f"  Entry Fees:        {Fore.YELLOW}${entry_fees:.2f}{Style.RESET_ALL}")
                print(f"  Est. Exit Fees:    {Fore.YELLOW}${exit_fees_estimate:.2f}{Style.RESET_ALL}")
                print(f"  {Fore.CYAN}Net PnL (est):     {net_pnl_color}${net_pnl:.2f}{Style.RESET_ALL}")

            print()
        else:
            print(f"{Fore.YELLOW}Could not find perpetual position data (PnL unavailable){Style.RESET_ALL}\n")
    except Exception as e:
        print(f"{Fore.YELLOW}Warning: Could not fetch current PnL: {e}{Style.RESET_ALL}\n")

    # Confirmation
    print(f"{Fore.YELLOW}⚠️  WARNING: This will immediately close your position!{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}           Market orders will be used (may have slippage).{Style.RESET_ALL}\n")

    confirmation = input(f"{Fore.CYAN}Press ENTER to confirm and execute, or Ctrl+C to cancel: {Style.RESET_ALL}")

    print(f"\n{Fore.YELLOW}Executing emergency exit...{Style.RESET_ALL}\n")

    # Execute close orders concurrently
    try:
        print(f"{Fore.CYAN}Closing position on {Fore.MAGENTA}{symbol}{Fore.CYAN}...{Style.RESET_ALL}")

        # Execute both orders at the same time
        results = await asyncio.gather(
            api_manager.close_perp_position(symbol, str(perp_qty), 'BUY'),  # BUY to close SHORT
            api_manager.place_spot_sell_market_order(symbol, str(spot_qty)),
            return_exceptions=True
        )

        perp_result = results[0]
        spot_result = results[1]

        # Check results
        print(f"\n{Fore.CYAN}{'='*80}{Style.RESET_ALL}")

        perp_success = not isinstance(perp_result, Exception)
        spot_success = not isinstance(spot_result, Exception)

        if perp_success:
            print(f"{Fore.GREEN}✓ Perpetual position closed successfully{Style.RESET_ALL}")
            print(f"  Order ID: {perp_result.get('orderId', 'N/A')}")
        else:
            print(f"{Fore.RED}✗ Perpetual close FAILED: {perp_result}{Style.RESET_ALL}")

        if spot_success:
            print(f"{Fore.GREEN}✓ Spot position sold successfully{Style.RESET_ALL}")
            print(f"  Order ID: {spot_result.get('orderId', 'N/A')}")
        else:
            print(f"{Fore.RED}✗ Spot sell FAILED: {spot_result}{Style.RESET_ALL}")

        print(f"{Fore.CYAN}{'='*80}{Style.RESET_ALL}\n")

        if perp_success and spot_success:
            print(f"{Fore.GREEN}✓ Emergency exit completed successfully!{Style.RESET_ALL}\n")

            # Clear position from state file
            print(f"{Fore.YELLOW}Clearing position from state file...{Style.RESET_ALL}")
            state['current_position'] = None
            state['position_opened_at'] = None
            state['position_leverage'] = None
            state['total_funding_received'] = 0.0
            state['entry_fees_paid'] = 0.0
            state['last_updated'] = datetime.utcnow().isoformat()

            with open(state_file, 'w') as f:
                json.dump(state, f, indent=2)

            print(f"{Fore.GREEN}✓ State file updated{Style.RESET_ALL}\n")

            # Stop docker compose
            print(f"{Fore.YELLOW}Stopping Docker Compose...{Style.RESET_ALL}")
            try:
                result = subprocess.run(
                    ['docker', 'compose', 'down'],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if result.returncode == 0:
                    print(f"{Fore.GREEN}✓ Docker Compose stopped successfully{Style.RESET_ALL}\n")
                else:
                    print(f"{Fore.YELLOW}Warning: Docker Compose command exited with code {result.returncode}{Style.RESET_ALL}")
                    if result.stderr:
                        print(f"{Fore.YELLOW}  {result.stderr.strip()}{Style.RESET_ALL}\n")
            except subprocess.TimeoutExpired:
                print(f"{Fore.YELLOW}Warning: Docker Compose down command timed out{Style.RESET_ALL}\n")
            except FileNotFoundError:
                print(f"{Fore.YELLOW}Warning: Docker command not found (are you running in Docker?){Style.RESET_ALL}\n")
            except Exception as e:
                print(f"{Fore.YELLOW}Warning: Failed to stop Docker Compose: {e}{Style.RESET_ALL}\n")
        else:
            print(f"{Fore.RED}⚠️  Emergency exit PARTIALLY FAILED!{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}Please check your exchange positions manually.{Style.RESET_ALL}\n")

    except Exception as e:
        print(f"{Fore.RED}ERROR during emergency exit: {e}{Style.RESET_ALL}")
        import traceback
        traceback.print_exc()
    finally:
        # Close API session
        await api_manager.close()

    print(f"{Fore.CYAN}Emergency exit script completed.{Style.RESET_ALL}")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Emergency exit cancelled by user.{Style.RESET_ALL}")
    except Exception as e:
        print(f"\n{Fore.RED}Fatal error: {e}{Style.RESET_ALL}")
