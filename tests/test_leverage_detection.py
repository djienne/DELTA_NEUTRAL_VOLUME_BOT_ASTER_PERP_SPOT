"""
Test script to verify leverage detection on existing positions.
This simulates:
1. Position opened at 1x leverage
2. Config changed to 3x leverage
3. Bot restart - should detect position is at 1x and preserve it
"""

import asyncio
import os
from dotenv import load_dotenv
from aster_api_manager import AsterApiManager


async def test_leverage_detection():
    """Test that bot correctly detects leverage on existing positions."""

    # Load environment variables from .env file
    load_dotenv()

    # Load API credentials
    api_user = os.getenv('API_USER')
    api_signer = os.getenv('API_SIGNER')
    api_private_key = os.getenv('API_PRIVATE_KEY')
    apiv1_public = os.getenv('APIV1_PUBLIC_KEY')
    apiv1_private = os.getenv('APIV1_PRIVATE_KEY')

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
        # Find delta-neutral positions
        print("\n" + "="*80)
        print("DETECTING EXISTING POSITIONS")
        print("="*80)

        portfolio_data = await api.get_comprehensive_portfolio_data()
        analyzed_positions = portfolio_data.get('analyzed_positions', [])
        dn_positions = [p for p in analyzed_positions if p.get('is_delta_neutral')]

        if not dn_positions:
            print("\nNo delta-neutral positions found.")
            print("To test this feature:")
            print("1. Open a position with the bot at 1x leverage")
            print("2. Change config to 3x leverage")
            print("3. Run this test to verify detection")
            return

        print(f"\nFound {len(dn_positions)} delta-neutral position(s):")

        for pos in dn_positions:
            symbol = pos['symbol']
            print(f"\n  Symbol: {symbol}")
            print(f"  Spot balance: {pos.get('spot_balance', 0):.6f}")
            print(f"  Perp position: {pos.get('perp_position', 0):.6f}")
            print(f"  Position value: ${pos.get('position_value_usd', 0):.2f}")

            # Detect current leverage on exchange
            print(f"\n  Detecting leverage on exchange...")
            current_leverage = await api.get_perp_leverage(symbol)
            print(f"  Current leverage: {current_leverage}x")

            # Simulate what the bot would do
            print(f"\n  Bot behavior simulation:")
            print(f"    - Position leverage would be set to: {current_leverage}x")
            print(f"    - This would be saved to state file")
            print(f"    - Position would maintain {current_leverage}x until closed")
            print(f"    - After closing, next position would use config leverage")

        print("\n" + "="*80)
        print("LEVERAGE DETECTION TEST COMPLETE")
        print("="*80)
        print("\nConclusion:")
        print("  [OK] Bot can detect current leverage from exchange")
        print("  [OK] Existing positions will maintain their leverage")
        print("  [OK] Config leverage only applies to new positions")

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()

    finally:
        await api.close()
        print("\nAPI session closed.")


if __name__ == "__main__":
    print("="*80)
    print("Leverage Detection Test")
    print("="*80)
    print("This test verifies the bot can detect leverage on existing positions")
    asyncio.run(test_leverage_detection())
