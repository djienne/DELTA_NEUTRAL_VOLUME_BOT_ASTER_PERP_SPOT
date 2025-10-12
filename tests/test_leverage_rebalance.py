"""
Test script to verify leverage-aware rebalancing functionality.
This script will test the rebalance_usdt_by_leverage method with different leverage values.
"""

import asyncio
import os
from dotenv import load_dotenv
from aster_api_manager import AsterApiManager


async def test_leverage_rebalancing():
    """Test leverage-aware USDT rebalancing for different leverage values."""

    # Load environment variables from .env file
    load_dotenv()

    # Load API credentials
    api_user = os.getenv('API_USER')
    api_signer = os.getenv('API_SIGNER')
    api_private_key = os.getenv('API_PRIVATE_KEY')
    apiv1_public = os.getenv('APIV1_PUBLIC_KEY')
    apiv1_private = os.getenv('APIV1_PRIVATE_KEY')

    # Validate that all credentials are present
    missing_creds = []
    if not api_user:
        missing_creds.append('API_USER')
    if not api_signer:
        missing_creds.append('API_SIGNER')
    if not api_private_key:
        missing_creds.append('API_PRIVATE_KEY')
    if not apiv1_public:
        missing_creds.append('APIV1_PUBLIC_KEY')
    if not apiv1_private:
        missing_creds.append('APIV1_PRIVATE_KEY')

    if missing_creds:
        print(f"ERROR: Missing environment variables: {', '.join(missing_creds)}")
        print("\nPlease set the following environment variables:")
        for cred in missing_creds:
            print(f"  - {cred}")
        return

    # Initialize API manager
    print("Initializing API manager...")
    api = AsterApiManager(
        api_user=api_user,
        api_signer=api_signer,
        api_private_key=api_private_key,
        apiv1_public=apiv1_public,
        apiv1_private=apiv1_private
    )

    try:
        # Get current balances
        print("\n" + "="*80)
        print("CURRENT BALANCES")
        print("="*80)

        spot_balances = await api.get_spot_account_balances()
        perp_account = await api.get_perp_account_info()

        spot_usdt = next((float(b.get('free', 0)) for b in spot_balances if b.get('asset') == 'USDT'), 0.0)
        perp_assets = perp_account.get('assets', [])
        perp_usdt = next((float(a.get('availableBalance', 0)) for a in perp_assets if a.get('asset') == 'USDT'), 0.0)
        total_usdt = spot_usdt + perp_usdt

        print(f"Spot USDT:  ${spot_usdt:.2f}")
        print(f"Perp USDT:  ${perp_usdt:.2f}")
        print(f"Total USDT: ${total_usdt:.2f}")

        # Test different leverage values
        for leverage in [1, 2, 3]:
            print("\n" + "="*80)
            print(f"TESTING LEVERAGE {leverage}x")
            print("="*80)

            # Calculate expected distribution
            perp_pct = 100.0 / (leverage + 1)
            spot_pct = 100.0 * leverage / (leverage + 1)
            expected_perp = total_usdt * (1.0 / (leverage + 1))
            expected_spot = total_usdt * (leverage / (leverage + 1))

            print(f"\nExpected distribution for {leverage}x:")
            print(f"  Spot: {spot_pct:.1f}% = ${expected_spot:.2f}")
            print(f"  Perp: {perp_pct:.1f}% = ${expected_perp:.2f}")

            # Perform rebalancing
            print(f"\nRebalancing for {leverage}x leverage...")
            result = await api.rebalance_usdt_by_leverage(leverage)

            print(f"\nRebalance result:")
            print(f"  Transfer needed: {result.get('transfer_needed')}")
            if result.get('transfer_needed'):
                print(f"  Transfer direction: {result.get('transfer_direction')}")
                print(f"  Transfer amount: ${result.get('transfer_amount'):.2f}")
                print(f"  Before -> After:")
                print(f"    Spot: ${result.get('current_spot_usdt'):.2f} -> ${result.get('target_spot_usdt'):.2f} ({result.get('spot_target_pct'):.1f}%)")
                print(f"    Perp: ${result.get('current_perp_usdt'):.2f} -> ${result.get('target_perp_usdt'):.2f} ({result.get('perp_target_pct'):.1f}%)")
            else:
                print(f"  Wallets already balanced (difference < $1)")

            # Wait a bit between tests
            if leverage < 3:
                print(f"\nWaiting 2 seconds before next test...")
                await asyncio.sleep(2)

        # Final balance check
        print("\n" + "="*80)
        print("FINAL BALANCES")
        print("="*80)

        spot_balances = await api.get_spot_account_balances()
        perp_account = await api.get_perp_account_info()

        spot_usdt = next((float(b.get('free', 0)) for b in spot_balances if b.get('asset') == 'USDT'), 0.0)
        perp_assets = perp_account.get('assets', [])
        perp_usdt = next((float(a.get('availableBalance', 0)) for a in perp_assets if a.get('asset') == 'USDT'), 0.0)
        total_usdt = spot_usdt + perp_usdt

        print(f"Spot USDT:  ${spot_usdt:.2f} ({100*spot_usdt/total_usdt:.1f}%)")
        print(f"Perp USDT:  ${perp_usdt:.2f} ({100*perp_usdt/total_usdt:.1f}%)")
        print(f"Total USDT: ${total_usdt:.2f}")

        print("\n" + "="*80)
        print("TEST COMPLETE")
        print("="*80)

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()

    finally:
        # Clean up
        await api.close()
        print("\nAPI session closed.")


if __name__ == "__main__":
    print("="*80)
    print("Leverage-Aware Rebalancing Test Script")
    print("="*80)
    asyncio.run(test_leverage_rebalancing())
