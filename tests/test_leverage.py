"""
Test script to check and set leverage for BTC/USDT on Aster exchange.
This script will:
1. Check the current leverage for BTCUSDT
2. Set the leverage to 3x
3. Verify the leverage was set correctly
"""

import asyncio
import os
import json
from dotenv import load_dotenv
from aster_api_manager import AsterApiManager


async def test_leverage():
    """Test leverage checking and setting for BTCUSDT."""

    # Load environment variables from .env file
    load_dotenv()

    # Load API credentials from environment variables
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

    symbol = "BTCUSDT"
    target_leverage = 3

    try:
        # Step 1: Check current leverage
        print(f"\n1. Checking current leverage for {symbol}...")
        current_leverage = await api.get_perp_leverage(symbol)
        print(f"   Current leverage: {current_leverage}x")

        # Step 2: Set leverage to 3x
        print(f"\n2. Setting leverage to {target_leverage}x...")
        result = await api.set_perp_leverage(symbol, target_leverage)
        print(f"   API Response: {json.dumps(result, indent=2)}")

        # Step 3: Verify the leverage was set correctly
        print(f"\n3. Verifying leverage was set correctly...")
        await asyncio.sleep(1)  # Small delay to ensure API has updated
        new_leverage = await api.get_perp_leverage(symbol)
        print(f"   New leverage: {new_leverage}x")

        # Summary
        print("\n" + "="*60)
        print("SUMMARY")
        print("="*60)
        print(f"Symbol:           {symbol}")
        print(f"Initial leverage: {current_leverage}x")
        print(f"Target leverage:  {target_leverage}x")
        print(f"Final leverage:   {new_leverage}x")

        if new_leverage == target_leverage:
            print(f"\n[SUCCESS] Leverage successfully set to {target_leverage}x")
        else:
            print(f"\n[FAILED] Leverage is {new_leverage}x, expected {target_leverage}x")

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()

    finally:
        # Clean up
        await api.close()
        print("\nAPI session closed.")


if __name__ == "__main__":
    print("="*60)
    print("BTC/USDT Leverage Test Script")
    print("="*60)
    asyncio.run(test_leverage())
