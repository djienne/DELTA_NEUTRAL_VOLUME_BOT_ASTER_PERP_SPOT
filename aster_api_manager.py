import asyncio
import aiohttp
import os
import time
import hmac
import hashlib
import json
import urllib.parse
import math
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Any, Tuple
from collections import Counter
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_abi import encode
from strategy_logic import DeltaNeutralLogic
from utils import truncate

# Base URLs for the APIs
FUTURES_BASE_URL = "https://fapi.asterdex.com"
SPOT_BASE_URL = "https://sapi.asterdex.com"


class AsterApiManager:
    """
    Unified API manager for both Aster Perpetual and Spot markets.
    Handles all API communications with proper authentication and precision formatting.
    """

    def __init__(self, api_user: str, api_signer: str, api_private_key: str,
                 apiv1_public: str, apiv1_private: str):
        """
        Initialize the API manager with all required credentials.
        """
        # Validate Ethereum addresses and keys
        if not api_user or not Web3.is_address(api_user):
            raise ValueError("API_USER is missing or not a valid Ethereum address.")
        if not api_signer or not Web3.is_address(api_signer):
            raise ValueError("API_SIGNER is missing or not a valid Ethereum address.")
        if not api_private_key:
            raise ValueError("API_PRIVATE_KEY is missing.")

        self.api_user = api_user
        self.api_signer = api_signer
        self.api_private_key = api_private_key
        self.apiv1_public = apiv1_public
        self.apiv1_private = apiv1_private

        self.session = None
        self.spot_exchange_info = None
        self.perp_exchange_info = None
        self._funding_interval_cache = {}  # Cache for funding intervals per symbol

    # --- Ethereum Signature Authentication (v3 API) ---

    def _trim_dict(self, my_dict: dict) -> dict:
        """Recursively converts all values in a dictionary to strings, matching the API doc example."""
        for key, value in my_dict.items():
            if isinstance(value, list):
                new_value = []
                for item in value:
                    if isinstance(item, dict):
                        new_value.append(json.dumps(self._trim_dict(item)))
                    else:
                        new_value.append(str(item))
                my_dict[key] = json.dumps(new_value)
            elif isinstance(value, dict):
                my_dict[key] = json.dumps(self._trim_dict(value))
            else:
                my_dict[key] = str(value)
        return my_dict

    def _sign_v3(self, params: dict) -> dict:
        """Signs the request parameters using Ethereum signature for v3 API."""
        nonce = math.trunc(time.time() * 1000000)
        my_dict = {k: v for k, v in params.items() if v is not None}
        my_dict["recvWindow"] = 50000
        my_dict["timestamp"] = int(round(time.time() * 1000))

        # Use the recursive trim function
        self._trim_dict(my_dict)

        # Create the JSON string exactly as in the documentation
        json_str = json.dumps(my_dict, sort_keys=True).replace(' ', '')

        # Encode and hash
        encoded = encode(['string', 'address', 'address', 'uint256'],
                         [json_str, self.api_user, self.api_signer, nonce])
        keccak_hex = Web3.keccak(encoded).hex()

        # Sign the message
        signable_msg = encode_defunct(hexstr=keccak_hex)
        signed_message = Account.sign_message(signable_message=signable_msg, private_key=self.api_private_key)

        # Append auth data to the dictionary
        my_dict['nonce'] = nonce
        my_dict['user'] = self.api_user
        my_dict['signer'] = self.api_signer
        my_dict['signature'] = '0x' + signed_message.signature.hex()

        return my_dict

    async def _signed_request_v3(self, method: str, endpoint: str, params: dict = None) -> dict:
        """Generic method for making signed requests to the v3 API (Ethereum signature)."""
        if params is None:
            params = {}

        if not self.session:
            self.session = aiohttp.ClientSession()

        url = f"{FUTURES_BASE_URL}{endpoint}"
        signed_params = self._sign_v3(params)
        headers = {'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': 'PythonApp/1.0'}

        if method.upper() == 'GET':
            # For GET requests, parameters must be in the query string
            query_string = urllib.parse.urlencode(signed_params)
            full_url = f"{url}?{query_string}"
            async with self.session.get(full_url, headers=headers) as response:
                if not response.ok:
                    error_body = await response.text()
                    print(f"API Error on {method} {endpoint}: Status={response.status}, Body={error_body}")
                response.raise_for_status()
                return await response.json()

        elif method.upper() == 'POST':
            # For POST, parameters are in the body
            async with self.session.post(url, data=signed_params, headers=headers) as response:
                if not response.ok:
                    error_body = await response.text()
                    print(f"API Error on {method} {endpoint}: Status={response.status}, Body={error_body}")
                response.raise_for_status()
                return await response.json()

        elif method.upper() == 'DELETE':
            # For DELETE, parameters are in the body
            async with self.session.delete(url, data=signed_params, headers=headers) as response:
                if not response.ok:
                    error_body = await response.text()
                    print(f"API Error on {method} {endpoint}: Status={response.status}, Body={error_body}")
                response.raise_for_status()
                return await response.json()
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

    # --- Exchange Info and Formatting Helpers ---

    async def _get_spot_exchange_info(self, force_refresh: bool = False) -> dict:
        """Fetches and caches spot exchange information."""
        if not self.spot_exchange_info or force_refresh:
            self.spot_exchange_info = await self._make_spot_request('GET', '/api/v1/exchangeInfo')
        return self.spot_exchange_info

    async def _get_perp_exchange_info(self, force_refresh: bool = False) -> dict:
        """Fetches and caches perpetual exchange information."""
        if not self.perp_exchange_info or force_refresh:
            if not self.session:
                self.session = aiohttp.ClientSession()
            # Public endpoint - no authentication needed
            url = f"{FUTURES_BASE_URL}/fapi/v1/exchangeInfo"
            async with self.session.get(url) as response:
                response.raise_for_status()
                self.perp_exchange_info = await response.json()
        return self.perp_exchange_info

    def _truncate(self, value: float, precision: int) -> float:
        """Truncates a float to a given precision without rounding."""
        return truncate(value, precision)

    async def _get_formatted_order_params(self, symbol: str, market_type: str, price: Optional[float] = None, quantity: Optional[float] = None, quote_quantity: Optional[float] = None) -> dict:
        """Fetches symbol filters and formats order parameters to the correct precision."""
        if market_type == 'spot':
            exchange_info = await self._get_spot_exchange_info()
        elif market_type == 'perp':
            exchange_info = await self._get_perp_exchange_info()
        else:
            return {}

        symbol_info = next((s for s in exchange_info.get('symbols', []) if s['symbol'] == symbol), None)
        if not symbol_info:
            raise ValueError(f"Symbol {symbol} not found in {market_type} exchange info.")

        params = {}

        # Format price based on PRICE_FILTER (tickSize)
        if price is not None:
            price_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'PRICE_FILTER'), None)
            if price_filter:
                tick_size_str = price_filter['tickSize']
                precision = abs(Decimal(tick_size_str).as_tuple().exponent)
                price = self._truncate(price, precision)
                params['price'] = f"{price:.{precision}f}"
            else:
                params['price'] = str(price)

        # Format quantity based on LOT_SIZE (stepSize)
        if quantity is not None:
            lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
            if lot_size_filter:
                step_size_str = lot_size_filter['stepSize']
                precision = abs(Decimal(step_size_str).as_tuple().exponent)
                quantity = self._truncate(quantity, precision)
                params['quantity'] = f"{quantity:.{precision}f}"
            else:
                params['quantity'] = str(quantity)

        # Format quote quantity for spot market buys based on quoteAssetPrecision
        if quote_quantity is not None and market_type == 'spot':
            precision = symbol_info.get('quoteAssetPrecision', 2) # Default to 2 for safety if not found
            quote_quantity = self._truncate(quote_quantity, precision)
            params['quoteOrderQty'] = f"{quote_quantity:.{precision}f}"

        return params

    # --- Core Request Methods ---

    def _create_spot_signature(self, params: dict) -> str:
        """Create HMAC-SHA256 signature for spot API requests."""
        query_string = urllib.parse.urlencode(params)
        return hmac.new(self.apiv1_private.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()

    async def _make_spot_request(self, method: str, path: str, params: dict = None, signed: bool = False, suppress_errors: bool = False, base_url: str = SPOT_BASE_URL) -> dict:
        """Generic method for making requests to the Spot API."""
        if params is None:
            params = {}
        if not self.session:
            self.session = aiohttp.ClientSession()

        url = f"{base_url}{path}"
        headers = {'X-MBX-APIKEY': self.apiv1_public}

        if signed:
            params['timestamp'] = int(time.time() * 1000)
            params['recvWindow'] = 5000
            params['signature'] = self._create_spot_signature(params)

        async with self.session.request(method, url, params=params, headers=headers) as response:
            if not response.ok:
                error_body = await response.text()
                if not suppress_errors:
                    print(f"API Error: {response.status}, Body: {error_body}")
            response.raise_for_status()
            return await response.json()

    # --- Funding Interval Detection ---

    async def detect_funding_interval(self, symbol: str) -> int:
        """
        Detect the funding interval for a symbol using the fundingInfo endpoint.
        Results are cached to avoid repeated API calls.

        Args:
            symbol: Trading symbol to analyze

        Returns:
            Number of times funding is paid per day (3, 6, 24, etc.)
        """
        # Return cached value if available
        if symbol in self._funding_interval_cache:
            return self._funding_interval_cache[symbol]

        try:
            # Try to get funding info from the API (most reliable method)
            funding_info = await self.get_funding_info(symbol)
            if funding_info and 'fundingIntervalHours' in funding_info:
                interval_hours = int(funding_info['fundingIntervalHours'])
                if interval_hours > 0:
                    funding_freq = int(24 / interval_hours)
                    self._funding_interval_cache[symbol] = funding_freq
                    return funding_freq

            # Fallback: Analyze historical funding times
            history = await self.get_funding_rate_history(symbol, limit=10)

            if not history or len(history) < 2:
                self._funding_interval_cache[symbol] = 3  # Default to 3x per day
                return 3

            # Calculate time differences between consecutive funding times
            time_diffs = []
            for i in range(len(history) - 1):
                t1 = int(history[i]['fundingTime'])
                t2 = int(history[i + 1]['fundingTime'])
                diff_hours = abs(t1 - t2) / (1000 * 3600)  # Convert ms to hours
                time_diffs.append(diff_hours)

            # Determine most common interval (round to nearest hour)
            rounded_diffs = [round(d) for d in time_diffs]
            most_common = Counter(rounded_diffs).most_common(1)

            if most_common:
                interval_hours = most_common[0][0]
                if interval_hours > 0:
                    funding_freq = int(24 / interval_hours)
                    self._funding_interval_cache[symbol] = funding_freq
                    return funding_freq

            self._funding_interval_cache[symbol] = 3  # Default to 3x per day
            return 3
        except Exception:
            self._funding_interval_cache[symbol] = 3  # Default to 3x per day on error
            return 3

    def calculate_funding_apr(self, funding_rate: float, funding_freq: int) -> float:
        """
        Calculate annualized APR from funding rate and frequency.

        Args:
            funding_rate: Single funding rate (as decimal, e.g., 0.0001)
            funding_freq: Number of times funding is paid per day (3, 6, 24, etc.)

        Returns:
            Annualized APR as percentage
        """
        return funding_rate * funding_freq * 365 * 100

    # --- Public Data Fetching Methods ---

    async def get_perp_account_info(self) -> dict:
        """Get perpetuals account information."""
        return await self._signed_request_v3('GET', '/fapi/v3/account')

    async def get_spot_account_balances(self) -> list:
        """Get spot account balances."""
        response = await self._make_spot_request('GET', '/api/v1/account', signed=True)
        return response.get('balances', [])

    async def get_funding_rate_history(self, symbol: str, limit: int = 50) -> list:
        """Get funding rate history for a symbol."""
        if not self.session:
            self.session = aiohttp.ClientSession()
        url = f"{FUTURES_BASE_URL}/fapi/v1/fundingRate"
        params = {'symbol': symbol, 'limit': limit}
        async with self.session.get(url, params=params) as response:
            response.raise_for_status()
            return await response.json()

    async def get_current_funding_rate(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get the current/next funding rate for a symbol (not historical).
        This is the rate that will be used for the next funding payment.

        Returns:
            Dict with 'fundingRate', 'nextFundingTime', 'markPrice', etc.
        """
        if not self.session:
            self.session = aiohttp.ClientSession()
        url = f"{FUTURES_BASE_URL}/fapi/v1/premiumIndex"
        params = {'symbol': symbol}
        try:
            async with self.session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                # Convert lastFundingRate to fundingRate for consistency
                if 'lastFundingRate' in data:
                    data['fundingRate'] = data['lastFundingRate']
                return data
        except Exception:
            return None

    async def get_funding_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get funding configuration info for a symbol.
        Returns fundingIntervalHours, fundingFeeCap, fundingFeeFloor, etc.
        """
        if not self.session:
            self.session = aiohttp.ClientSession()
        url = f"{FUTURES_BASE_URL}/fapi/v1/fundingInfo"
        params = {'symbol': symbol}
        try:
            async with self.session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                # Returns a list, get the first item
                if isinstance(data, list) and len(data) > 0:
                    return data[0]
                return None
        except Exception:
            return None

    async def get_perp_book_ticker(self, symbol: str) -> dict:
        """Get perpetuals book ticker for a symbol."""
        if not self.session:
            self.session = aiohttp.ClientSession()
        url = f"{FUTURES_BASE_URL}/fapi/v1/ticker/bookTicker"
        params = {'symbol': symbol}
        async with self.session.get(url, params=params) as response:
            response.raise_for_status()
            return await response.json()

    async def get_spot_book_ticker(self, symbol: str, suppress_errors: bool = False) -> dict:
        """Get spot book ticker for a symbol."""
        return await self._make_spot_request('GET', '/api/v1/ticker/bookTicker', params={'symbol': symbol}, suppress_errors=suppress_errors)

    # --- Public Execution Methods (Write Actions) ---

    async def place_perp_order(self, symbol: str, price: str, quantity: str, side: str, reduce_only: bool = False) -> dict:
        """Place a perpetuals limit order with correct precision."""
        formatted_params = await self._get_formatted_order_params(
            symbol=symbol, market_type='perp', price=float(price), quantity=float(quantity)
        )

        params = {
            "symbol": symbol, "side": side, "type": "LIMIT",
            "timeInForce": "GTX", "price": formatted_params['price'],
            "quantity": formatted_params['quantity'], "positionSide": "BOTH"
        }
        if reduce_only:
            params['reduceOnly'] = 'true'
        return await self._signed_request_v3('POST', '/fapi/v3/order', params)

    async def place_perp_market_order(self, symbol: str, quantity: str, side: str) -> dict:
        """Place a perpetuals market order with correct precision."""
        formatted_params = await self._get_formatted_order_params(
            symbol=symbol, market_type='perp', quantity=float(quantity)
        )

        params = {
            'symbol': symbol,
            'side': side,
            'type': 'MARKET',
            'quantity': formatted_params['quantity']
        }
        return await self._signed_request_v3('POST', '/fapi/v3/order', params)

    async def place_spot_buy_market_order(self, symbol: str, quote_quantity: str) -> dict:
        """Place a spot market buy order using USDT amount with correct precision."""
        formatted_params = await self._get_formatted_order_params(
            symbol=symbol, market_type='spot', quote_quantity=float(quote_quantity)
        )
        params = {'symbol': symbol, 'side': 'BUY', 'type': 'MARKET', 'quoteOrderQty': formatted_params['quoteOrderQty']}
        return await self._make_spot_request('POST', '/api/v1/order', params=params, signed=True)

    async def place_spot_buy_market_order_by_quantity(self, symbol: str, base_quantity: str) -> dict:
        """Place a spot market buy order using exact base asset quantity with correct precision."""
        formatted_params = await self._get_formatted_order_params(
            symbol=symbol, market_type='spot', quantity=float(base_quantity)
        )
        params = {'symbol': symbol, 'side': 'BUY', 'type': 'MARKET', 'quantity': formatted_params['quantity']}
        return await self._make_spot_request('POST', '/api/v1/order', params=params, signed=True)

    async def place_spot_sell_market_order(self, symbol: str, base_quantity: str) -> dict:
        """Place a spot market sell order with correct precision."""
        formatted_params = await self._get_formatted_order_params(
            symbol=symbol, market_type='spot', quantity=float(base_quantity)
        )
        params = {'symbol': symbol, 'side': 'SELL', 'type': 'MARKET', 'quantity': formatted_params['quantity']}
        return await self._make_spot_request('POST', '/api/v1/order', params=params, signed=True)

    async def close_perp_position(self, symbol: str, quantity: str, side_to_close: str) -> dict:
        """Close a perpetuals position using a market order with correct precision."""
        formatted_params = await self._get_formatted_order_params(
            symbol=symbol, market_type='perp', quantity=float(quantity)
        )
        params = {
            'symbol': symbol, 'side': side_to_close, 'type': 'MARKET',
            'quantity': formatted_params['quantity'], 'reduceOnly': 'true', 'positionSide': 'BOTH'
        }
        return await self._signed_request_v3('POST', '/fapi/v3/order', params)

    async def get_perp_leverage(self, symbol: str) -> int:
        """Get current leverage for a perpetual trading symbol."""
        account_info = await self.get_perp_account_info()
        positions = account_info.get('positions', [])

        for position in positions:
            if position.get('symbol') == symbol:
                leverage_val = position.get('leverage', '1')
                return int(float(leverage_val))

        # Default to 1x if symbol not found
        return 1

    async def set_perp_leverage(self, symbol: str, leverage: int = 1) -> dict:
        """Set leverage for a perpetual trading symbol."""
        params = {'symbol': symbol, 'leverage': leverage}
        # This endpoint uses HMAC-SHA256, not the custom eth signature, so we use the spot request method
        return await self._make_spot_request(
            method='POST',
            path='/fapi/v1/leverage',
            params=params,
            signed=True,
            base_url=FUTURES_BASE_URL
        )

    async def set_leverage(self, symbol: str, leverage: int = 1) -> bool:
        """
        Alias for set_perp_leverage for backward compatibility.
        Returns True on success, False on failure.
        """
        try:
            response = await self.set_perp_leverage(symbol, leverage)
            # The API returns a dict with the set leverage on success
            return response and int(response.get('leverage')) == leverage
        except Exception:
            return False

    # --- Transfer Methods ---

    async def transfer_between_spot_and_perp(self, asset: str, amount: float, direction: str) -> dict:
        """
        Transfer assets between spot and perpetual accounts.

        Args:
            asset: Asset to transfer (e.g., 'USDT')
            amount: Amount to transfer
            direction: 'SPOT_TO_PERP' or 'PERP_TO_SPOT'

        Returns:
            Transfer response with transaction ID and status
        """
        # Generate unique transaction ID
        client_tran_id = f"transfer_{int(time.time() * 1000000)}"

        # Map direction to API parameter
        direction_map = {
            'SPOT_TO_PERP': 'SPOT_FUTURE',
            'PERP_TO_SPOT': 'FUTURE_SPOT'
        }

        if direction not in direction_map:
            raise ValueError(f"Invalid direction: {direction}. Must be 'SPOT_TO_PERP' or 'PERP_TO_SPOT'")

        params = {
            'asset': asset,
            'amount': str(amount),
            'clientTranId': client_tran_id,
            'kindType': direction_map[direction]
        }

        return await self._signed_request_v3('POST', '/fapi/v3/asset/wallet/transfer', params)

    async def rebalance_usdt_by_leverage(self, leverage: int = 1) -> dict:
        """
        Rebalance USDT between spot and perpetual accounts based on leverage.

        Leverage determines the split:
        - leverage=1: 50% spot, 50% perp (1x leverage)
        - leverage=2: 67% spot, 33% perp (2x leverage)
        - leverage=3: 75% spot, 25% perp (3x leverage)

        Args:
            leverage: Leverage multiplier (1-3)

        Returns:
            Dictionary with rebalance details and transfer result (if transfer was needed)
        """
        # Validate leverage
        if leverage < 1 or leverage > 3:
            raise ValueError(f"Leverage must be between 1 and 3, got {leverage}")

        # Calculate target percentages based on leverage
        # Formula: perp_pct = 1 / (leverage + 1), spot_pct = leverage / (leverage + 1)
        perp_target_pct = 1.0 / (leverage + 1)
        spot_target_pct = leverage / (leverage + 1)

        # Get current balances
        spot_balances = await self.get_spot_account_balances()
        perp_account = await self.get_perp_account_info()

        # Extract USDT balances
        spot_usdt = next((float(b.get('free', 0)) for b in spot_balances if b.get('asset') == 'USDT'), 0.0)

        # Get USDT from perpetual account assets
        perp_assets = perp_account.get('assets', [])
        perp_usdt = next((float(a.get('availableBalance', 0)) for a in perp_assets if a.get('asset') == 'USDT'), 0.0)

        total_usdt = spot_usdt + perp_usdt
        target_spot = total_usdt * spot_target_pct
        target_perp = total_usdt * perp_target_pct

        # Calculate transfer needed
        spot_difference = target_spot - spot_usdt

        result = {
            'leverage': leverage,
            'current_spot_usdt': spot_usdt,
            'current_perp_usdt': perp_usdt,
            'total_usdt': total_usdt,
            'target_spot_usdt': target_spot,
            'target_perp_usdt': target_perp,
            'spot_target_pct': spot_target_pct * 100,
            'perp_target_pct': perp_target_pct * 100,
            'transfer_needed': abs(spot_difference) > 1.0,  # Only transfer if difference > $1
            'transfer_amount': abs(spot_difference),
            'transfer_direction': None,
            'transfer_result': None
        }

        # Perform transfer if needed (minimum $1 difference to avoid micro-transfers)
        if abs(spot_difference) > 1.0:
            transfer_amount = round(abs(spot_difference), 6) # Round to 6 decimal places for safety
            if spot_difference > 0:
                # Need to transfer from perp to spot
                result['transfer_direction'] = 'PERP_TO_SPOT'
                result['transfer_result'] = await self.transfer_between_spot_and_perp(
                    'USDT', transfer_amount, 'PERP_TO_SPOT'
                )
            else:
                # Need to transfer from spot to perp
                result['transfer_direction'] = 'SPOT_TO_PERP'
                result['transfer_result'] = await self.transfer_between_spot_and_perp(
                    'USDT', transfer_amount, 'SPOT_TO_PERP'
                )

        return result

    async def rebalance_usdt_50_50(self) -> dict:
        """
        Automatically rebalance USDT to be 50/50 between spot and perpetual accounts.

        Returns:
            Dictionary with rebalance details and transfer result (if transfer was needed)
        """
        # Get current balances
        spot_balances = await self.get_spot_account_balances()
        perp_account = await self.get_perp_account_info()

        # Extract USDT balances
        spot_usdt = next((float(b.get('free', 0)) for b in spot_balances if b.get('asset') == 'USDT'), 0.0)

        # Get USDT from perpetual account assets
        perp_assets = perp_account.get('assets', [])
        perp_usdt = next((float(a.get('availableBalance', 0)) for a in perp_assets if a.get('asset') == 'USDT'), 0.0)

        total_usdt = spot_usdt + perp_usdt
        target_each = total_usdt / 2

        # Calculate transfer needed
        spot_difference = target_each - spot_usdt

        result = {
            'current_spot_usdt': spot_usdt,
            'current_perp_usdt': perp_usdt,
            'total_usdt': total_usdt,
            'target_each': target_each,
            'transfer_needed': abs(spot_difference) > 1.0,  # Only transfer if difference > $1
            'transfer_amount': abs(spot_difference),
            'transfer_direction': None,
            'transfer_result': None
        }

        # Perform transfer if needed (minimum $1 difference to avoid micro-transfers)
        if abs(spot_difference) > 1.0:
            transfer_amount = round(abs(spot_difference), 6) # Round to 6 decimal places for safety
            if spot_difference > 0:
                # Need to transfer from perp to spot
                result['transfer_direction'] = 'PERP_TO_SPOT'
                result['transfer_result'] = await self.transfer_between_spot_and_perp(
                    'USDT', transfer_amount, 'PERP_TO_SPOT'
                )
            else:
                # Need to transfer from spot to perp
                result['transfer_direction'] = 'SPOT_TO_PERP'
                result['transfer_result'] = await self.transfer_between_spot_and_perp(
                    'USDT', transfer_amount, 'SPOT_TO_PERP'
                )

        return result

    # --- Symbol Discovery and Analysis ---

    async def get_available_spot_symbols(self) -> List[str]:
        """Get list of all available spot trading symbols."""
        try:
            exchange_info = await self._get_spot_exchange_info()
            if exchange_info and 'symbols' in exchange_info:
                return sorted([s['symbol'] for s in exchange_info['symbols'] if s.get('status') == 'TRADING'])
            return []
        except Exception as e:
            print(f"Error fetching spot symbols: {e}")
            return []

    async def get_available_perp_symbols(self) -> List[str]:
        """Get list of all available perpetual trading symbols."""
        try:
            exchange_info = await self._get_perp_exchange_info()
            if exchange_info and 'symbols' in exchange_info:
                return sorted([s['symbol'] for s in exchange_info['symbols'] if s.get('status') == 'TRADING'])
            return []
        except Exception as e:
            print(f"Error fetching perpetual symbols: {e}")
            return []

    async def get_perp_symbol_filter(self, symbol: str, filter_type: str) -> Optional[Dict]:
        """Retrieves a specific filter for a perpetual symbol from exchange info."""
        try:
            exchange_info = await self._get_perp_exchange_info()
            symbol_info = next((s for s in exchange_info.get('symbols', []) if s['symbol'] == symbol), None)
            if symbol_info:
                return next((f for f in symbol_info['filters'] if f['filterType'] == filter_type), None)
        except Exception as e:
            print(f"Error getting perp filter for {symbol}: {e}")
        return None

    async def discover_delta_neutral_pairs(self) -> List[str]:
        """Dynamically discover which pairs are available for delta-neutral strategies."""
        try:
            spot_symbols, perp_symbols = await asyncio.gather(
                self.get_available_spot_symbols(),
                self.get_available_perp_symbols(),
                return_exceptions=True
            )
            if isinstance(spot_symbols, Exception) or isinstance(perp_symbols, Exception):
                spot_symbols, perp_symbols = [], []

            from strategy_logic import DeltaNeutralLogic
            return DeltaNeutralLogic.find_delta_neutral_pairs(spot_symbols, perp_symbols)
        except Exception as e:
            print(f"Error discovering delta-neutral pairs: {e}")
            return []

    async def analyze_current_positions(self) -> Dict[str, Dict[str, Any]]:
        """Analyze current open positions across spot and perpetual markets."""
        try:
            # Fetch all required data concurrently
            perp_info, spot_info, perp_account, spot_balances = await asyncio.gather(
                self._get_perp_exchange_info(),
                self._get_spot_exchange_info(),
                self.get_perp_account_info(),
                self.get_spot_account_balances(),
                return_exceptions=True
            )
            if isinstance(perp_info, Exception) or isinstance(spot_info, Exception) or isinstance(perp_account, Exception) or isinstance(spot_balances, Exception):
                return {}

            # Prepare data for strategy logic
            spot_lookup = {b.get('asset', ''): float(b.get('free', '0')) + float(b.get('locked', '0')) for b in spot_balances}
            perp_symbol_map = {s['symbol']: s for s in perp_info.get('symbols', [])}
            perp_positions = perp_account.get('positions', [])

            # Filter for positions with non-zero amounts and fetch current prices
            active_positions = [p for p in perp_positions if float(p.get('positionAmt', 0)) != 0]
            if active_positions:
                # Fetch current mark prices for all active positions
                price_tasks = [self.get_perp_book_ticker(p['symbol']) for p in active_positions]
                price_results = await asyncio.gather(*price_tasks, return_exceptions=True)

                # Update positions with current mark prices
                for i, pos in enumerate(active_positions):
                    price_data = price_results[i]
                    if not isinstance(price_data, Exception) and price_data.get('bidPrice') and price_data.get('askPrice'):
                        # Use mid-price as mark price
                        bid_price = float(price_data['bidPrice'])
                        ask_price = float(price_data['askPrice'])
                        pos['markPrice'] = (bid_price + ask_price) / 2
                    # If price fetch fails, keep existing markPrice or set to 0

            # Use strategy logic for computational analysis
            analysis = DeltaNeutralLogic.analyze_position_data(
                perp_positions=perp_positions,
                spot_balances=spot_lookup,
                perp_symbol_map=perp_symbol_map
            )

            return analysis
        except Exception as e:
            print(f"Error analyzing positions: {e}")
            return {}

    async def get_all_funding_rates(self) -> List[Dict[str, Any]]:
        """Fetches and returns CURRENT/NEXT funding rates for all available delta-neutral pairs with correct funding intervals."""
        symbols_to_scan = await self.discover_delta_neutral_pairs()
        if not symbols_to_scan:
            return []

        # Use current funding rate (not historical) - this is the rate that will be paid at next funding
        rate_tasks = [self.get_current_funding_rate(s) for s in symbols_to_scan]
        interval_tasks = [self.detect_funding_interval(s) for s in symbols_to_scan]

        rate_results, interval_results = await asyncio.gather(
            asyncio.gather(*rate_tasks, return_exceptions=True),
            asyncio.gather(*interval_tasks, return_exceptions=True)
        )

        funding_data = []
        for i, symbol in enumerate(symbols_to_scan):
            rate_data = rate_results[i]
            funding_freq = interval_results[i] if not isinstance(interval_results[i], Exception) else 3

            if not isinstance(rate_data, Exception) and rate_data:
                # Get current/next funding rate (from premiumIndex endpoint)
                rate = float(rate_data.get('fundingRate', 0))
                apr = self.calculate_funding_apr(rate, funding_freq)
                funding_data.append({'symbol': symbol, 'rate': rate, 'apr': apr, 'funding_freq': funding_freq})

        # Sort by highest APR
        return sorted(funding_data, key=lambda x: x['apr'], reverse=True)

    async def get_comprehensive_portfolio_data(self) -> Dict[str, Any]:
        """Fetches and processes all portfolio data in a structured way."""
        # 1. Fetch all required raw data concurrently
        results = await asyncio.gather(
            self.get_perp_account_info(),
            self.get_spot_account_balances(),
            self._get_perp_exchange_info(),
            self._get_spot_exchange_info(),
            return_exceptions=True
        )
        perp_account, spot_balances, perp_info, spot_info = results

        if isinstance(perp_account, Exception) or isinstance(spot_balances, Exception) or \
           isinstance(perp_info, Exception) or isinstance(spot_info, Exception):
            # Handle potential fetching errors gracefully
            # Consider logging the specific errors here
            return {}

        # 2. Process raw perpetual positions
        raw_perp_positions = [p for p in perp_account.get('positions', []) if float(p.get('positionAmt', 0)) != 0]
        if raw_perp_positions:
            price_tasks = [self.get_perp_book_ticker(p['symbol']) for p in raw_perp_positions]
            price_results = await asyncio.gather(*price_tasks, return_exceptions=True)
            for i, pos in enumerate(raw_perp_positions):
                price_data = price_results[i]
                if not isinstance(price_data, Exception) and price_data.get('bidPrice'):
                    pos['markPrice'] = (float(price_data['bidPrice']) + float(price_data['askPrice'])) / 2

        # 3. Process spot balances
        processed_spot_balances = [b for b in spot_balances if float(b.get('free', 0)) > 0 or float(b.get('locked', 0)) > 0]
        stablecoins = {'USDT', 'USDC', 'USDF'}
        non_stable_balances = [b for b in processed_spot_balances if b.get('asset') not in stablecoins]
        if non_stable_balances:
            price_tasks = [self.get_spot_book_ticker(f"{b['asset']}USDT", suppress_errors=True) for b in non_stable_balances]
            price_results = await asyncio.gather(*price_tasks, return_exceptions=True)
            for i, balance in enumerate(non_stable_balances):
                price_data = price_results[i]
                if not isinstance(price_data, Exception) and price_data.get('bidPrice'):
                    balance['value_usd'] = (float(balance.get('free', 0)) + float(balance.get('locked', 0))) * float(price_data['bidPrice'])
                else:
                    balance['value_usd'] = 0.0

        # 4. Perform delta-neutral analysis
        spot_lookup = {b.get('asset', ''): float(b.get('free', '0')) + float(b.get('locked', '0')) for b in processed_spot_balances}
        perp_symbol_map = {s['symbol']: s for s in perp_info.get('symbols', [])}
        analyzed_positions = list(DeltaNeutralLogic.analyze_position_data(
            perp_positions=raw_perp_positions,
            spot_balances=spot_lookup,
            perp_symbol_map=perp_symbol_map
        ).values())

        # 5. Enrich analyzed positions with CURRENT funding APR (with correct funding intervals)
        dn_positions = [p for p in analyzed_positions if p.get('is_delta_neutral')]
        if dn_positions:
            # Use current funding rate (not historical) - this is the rate that will be paid at next funding
            rate_tasks = [self.get_current_funding_rate(p['symbol']) for p in dn_positions]
            interval_tasks = [self.detect_funding_interval(p['symbol']) for p in dn_positions]

            rate_results, interval_results = await asyncio.gather(
                asyncio.gather(*rate_tasks, return_exceptions=True),
                asyncio.gather(*interval_tasks, return_exceptions=True)
            )

            for i, pos in enumerate(dn_positions):
                rate_data = rate_results[i]
                funding_freq = interval_results[i] if not isinstance(interval_results[i], Exception) else 3

                if not isinstance(rate_data, Exception) and rate_data:
                    # Get current/next funding rate (from premiumIndex endpoint)
                    pos['current_apr'] = self.calculate_funding_apr(float(rate_data.get('fundingRate', 0)), funding_freq)

        # 6. Return all processed data in a structured dictionary
        return {
            'perp_account_info': perp_account,
            'raw_perp_positions': raw_perp_positions,
            'spot_balances': processed_spot_balances,
            'analyzed_positions': analyzed_positions,
        }

    async def get_spot_symbol_filter(self, symbol: str, filter_type: str) -> Optional[Dict]:
        """Retrieves a specific filter for a spot symbol from exchange info."""
        try:
            exchange_info = await self._get_spot_exchange_info()
            symbol_info = next((s for s in exchange_info.get('symbols', []) if s['symbol'] == symbol), None)
            if symbol_info:
                return next((f for f in symbol_info['filters'] if f['filterType'] == filter_type), None)
        except Exception as e:
            print(f"Error getting spot filter for {symbol}: {e}")
        return None

    async def prepare_and_execute_dn_position(self, symbol: str, capital_to_deploy: float, leverage: int = 1, dry_run: bool = False) -> Dict[str, Any]:
        """Prepares and (optionally) executes a delta-neutral position opening."""
        trade_details = {'success': False, 'message': '', 'details': None}
        try:
            # 1. Fetch required data including BOTH spot and perp LOT_SIZE filters
            spot_price_data, perp_lot_size_filter, spot_lot_size_filter, spot_balances, perp_account = await asyncio.gather(
                self.get_spot_book_ticker(symbol),
                self.get_perp_symbol_filter(symbol, 'LOT_SIZE'),
                self.get_spot_symbol_filter(symbol, 'LOT_SIZE'),
                self.get_spot_account_balances(),
                self.get_perp_account_info()
            )
            spot_price = Decimal(str(spot_price_data['bidPrice']))

            # 2. Determine coarser LOT_SIZE step size (use the larger one for both markets)
            perp_step_size = Decimal(perp_lot_size_filter['stepSize']) if perp_lot_size_filter and perp_lot_size_filter.get('stepSize') else Decimal('0.00001')
            spot_step_size = Decimal(spot_lot_size_filter['stepSize']) if spot_lot_size_filter and spot_lot_size_filter.get('stepSize') else Decimal('0.00001')

            # Use the coarser (larger) step size for calculations to ensure both orders are valid
            coarser_step_size = max(perp_step_size, spot_step_size)
            precision = abs(coarser_step_size.as_tuple().exponent)

            # Check for existing short position
            raw_perp_positions = [p for p in perp_account.get('positions', []) if float(p.get('positionAmt', 0)) != 0]
            existing_short = next((p for p in raw_perp_positions if p.get('symbol') == symbol and float(p.get('positionAmt', 0)) < 0), None)
            if existing_short:
                trade_details['message'] = f"Cannot open position. Already have a short position: {existing_short.get('positionAmt')}"
                return trade_details

            # 3. Set leverage (note: leverage is already set in volume_farming_strategy before calling this)
            # We set it again here as a safety check
            leverage_set = await self.set_leverage(symbol, leverage)
            if not leverage_set:
                trade_details['message'] = f"Failed to set leverage to {leverage}x."
                return trade_details

            # 4. Calculate position sizes using Decimal for precision
            base_asset = symbol.replace('USDT', '')
            existing_spot_quantity = Decimal(str(sum(float(b.get('free', '0')) for b in spot_balances if b.get('asset') == base_asset)))
            capital_to_deploy_decimal = Decimal(str(capital_to_deploy))

            sizing = DeltaNeutralLogic.calculate_position_size(
                total_usd_capital=float(capital_to_deploy_decimal),
                spot_price=float(spot_price),
                leverage=leverage,
                existing_spot_usd=float(existing_spot_quantity * spot_price)
            )

            # 5. Adjust quantities based on the coarser step size
            ideal_perp_qty = Decimal(str(sizing['total_perp_quantity_to_short']))
            final_perp_qty = Decimal(str(self._truncate(float(ideal_perp_qty), precision)))

            if final_perp_qty <= 0:
                trade_details['message'] = "Final perpetual quantity is zero or less after rounding."
                return trade_details

            # 6. Calculate spot side - must match the perp quantity exactly (delta-neutral)
            # Spot side buys exactly final_perp_qty minus what we already have
            spot_qty_needed = max(Decimal('0'), final_perp_qty - existing_spot_quantity)
            # Truncate to coarser step size to ensure both orders use same precision
            spot_qty_to_buy = Decimal(str(self._truncate(float(spot_qty_needed), precision)))

            # CRITICAL: Recalculate final_perp_qty based on actual achievable spot total
            # This ensures perfect delta-neutral matching even with misaligned existing balances
            actual_total_spot = existing_spot_quantity + spot_qty_to_buy
            final_perp_qty = Decimal(str(self._truncate(float(actual_total_spot), precision)))

            spot_capital_to_buy = spot_qty_to_buy * spot_price

            # 7. Prepare details dictionary
            details = {
                'symbol': symbol,
                'capital_to_deploy': float(capital_to_deploy_decimal),
                'spot_price': float(spot_price),
                'perp_lot_size_filter': perp_lot_size_filter,
                'spot_lot_size_filter': spot_lot_size_filter,
                'perp_step_size': str(perp_step_size),
                'spot_step_size': str(spot_step_size),
                'coarser_step_size': str(coarser_step_size),
                'precision': precision,
                'ideal_perp_qty': float(ideal_perp_qty),
                'final_perp_qty': float(final_perp_qty),
                'existing_spot_quantity': float(existing_spot_quantity),
                'spot_qty_to_buy': float(spot_qty_to_buy),
                'spot_capital_to_buy': float(spot_capital_to_buy)
            }
            trade_details['details'] = details

            if dry_run:
                trade_details['success'] = True
                trade_details['message'] = "Dry run successful. Trade details calculated."
                return trade_details

            # 8. Execute trades - use exact quantities for both spot and perp (delta-neutral)
            # IMPORTANT: We use quantity for both, not USDT amount, to ensure exact matching
            exec_results = await asyncio.gather(
                self.place_perp_market_order(symbol, str(float(final_perp_qty)), 'SELL'),
                self.place_spot_buy_market_order_by_quantity(symbol, str(float(spot_qty_to_buy))) if spot_qty_to_buy > Decimal('0.0001') else asyncio.sleep(0),
                return_exceptions=True
            )

            perp_result, spot_result = exec_results
            trade_details['success'] = True
            trade_details['message'] = f"Successfully opened position for {symbol}."
            trade_details['perp_order'] = perp_result
            trade_details['spot_order'] = spot_result
            return trade_details

        except Exception as e:
            trade_details['message'] = f"Failed to open position: {e}"
            return trade_details

    async def execute_dn_position_close(self, symbol: str) -> Dict[str, Any]:
        """Fetches position state and executes closing orders for a delta-neutral position."""
        close_details = {'success': False, 'message': ''}
        try:
            # 1. Get current position state
            portfolio_data = await self.get_comprehensive_portfolio_data()
            if not portfolio_data:
                close_details['message'] = "Could not retrieve portfolio data."
                return close_details

            position_to_close = next((p for p in portfolio_data.get('analyzed_positions', []) if p.get('symbol') == symbol), None)

            if not position_to_close:
                close_details['message'] = f"No position found for symbol {symbol}."
                return close_details

            # 2. Get quantities to close
            perp_quantity = abs(position_to_close.get('perp_position', 0))
            spot_quantity = position_to_close.get('spot_balance', 0)
            side_to_close = 'BUY' if position_to_close.get('perp_position', 0) < 0 else 'SELL'

            if perp_quantity == 0 or spot_quantity == 0:
                close_details['message'] = f"Position for {symbol} is not a valid delta-neutral pair to close (perp or spot leg is zero)."
                return close_details

            # 3. Execute closing trades
            exec_results = await asyncio.gather(
                self.close_perp_position(symbol, str(perp_quantity), side_to_close),
                self.place_spot_sell_market_order(symbol, str(spot_quantity)),
                return_exceptions=True
            )

            perp_result, spot_result = exec_results
            close_details['success'] = True
            close_details['message'] = f"Successfully closed position for {symbol}."
            close_details['perp_order'] = perp_result
            close_details['spot_order'] = spot_result
            return close_details

        except Exception as e:
            close_details['message'] = f"Failed to close position: {e}"
            return close_details


    async def get_income_history(self, symbol: Optional[str] = None, income_type: Optional[str] = None, start_time: Optional[int] = None, end_time: Optional[int] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get income history for the perpetuals account.
        NOTE: This v1 endpoint uses HMAC-SHA256 authentication, not the v3 eth signature.
        """
        params = {'limit': limit}
        if symbol:
            params['symbol'] = symbol
        if income_type:
            params['incomeType'] = income_type
        if start_time:
            params['startTime'] = start_time
        if end_time:
            params['endTime'] = end_time

        return await self._make_spot_request(
            method='GET',
            path='/fapi/v1/income',
            params=params,
            signed=True,
            base_url=FUTURES_BASE_URL
        )

    async def get_user_trades(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get user's trade history for a specific symbol.
        NOTE: This v1 endpoint uses HMAC-SHA256 authentication.
        """
        params = {
            'symbol': symbol,
            'limit': limit
        }
        return await self._make_spot_request(
            method='GET',
            path='/fapi/v1/userTrades',
            params=params,
            signed=True,
            base_url=FUTURES_BASE_URL
        )

    async def perform_funding_analysis(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Performs a standalone funding analysis for a given symbol.
        This function is self-contained and fetches all necessary data.

        Args:
            symbol: Trading symbol to analyze (e.g., 'BTCUSDT')

        Returns:
            Dict with funding analysis data or None if analysis fails
        """
        try:
            # 1. Fetch all necessary data concurrently
            all_positions_task = self.get_perp_account_info()
            spot_balances_task = self.get_spot_account_balances()
            ticker_task = self.get_perp_book_ticker(symbol)

            all_positions, spot_balances, ticker = await asyncio.gather(
                all_positions_task, spot_balances_task, ticker_task
            )

            position = next((p for p in all_positions.get('positions', []) if p.get('symbol') == symbol and Decimal(p.get('positionAmt', '0')) != 0), None)

            if not position:
                return None

            # Extract data from fetched results
            current_pos_amount = Decimal(position.get('positionAmt', '0'))
            position_notional = Decimal(position.get('notional', '0'))
            unrealized_pnl = Decimal(position.get('unrealizedProfit', '0'))
            mark_price = Decimal(ticker.get('bidPrice'))
            base_asset = symbol.replace('USDT', '')
            spot_balance = next((Decimal(b.get('free', '0')) for b in spot_balances if b.get('asset') == base_asset), Decimal('0'))
            spot_value_usd = spot_balance * mark_price
            effective_position_value = spot_value_usd + abs(position_notional) + unrealized_pnl

        except Exception as e:
            return None

        # 2. Fetch recent trades to find the position's opening time
        try:
            trades = await self.get_user_trades(symbol=symbol, limit=1000)
            if not trades:
                return None

            trades.sort(key=lambda x: int(x['time']))
            position_start_time = None
            running_total = Decimal('0')

            for trade in reversed(trades):
                trade_qty = Decimal(trade['qty'])
                if trade['side'].upper() == 'SELL':
                    trade_qty *= -1

                running_total += trade_qty
                if abs(running_total - current_pos_amount) < Decimal('0.000001'):
                    position_start_time = int(trade['time'])
                    break

            if not position_start_time:
                return None

            start_datetime = datetime.fromtimestamp(position_start_time / 1000)

        except Exception as e:
            return None

        # 3. Fetch funding payments since the position was opened
        try:
            funding_payments = await self.get_income_history(
                symbol=symbol,
                income_type='FUNDING_FEE',
                start_time=position_start_time,
                limit=1000
            )

            total_funding = sum(Decimal(p['income']) for p in funding_payments)
            funding_percentage = (total_funding / effective_position_value) * 100 if effective_position_value != 0 else Decimal('0')

            FEE_THRESHOLD_PERCENT = Decimal('0.135')
            fee_coverage_progress = (funding_percentage / FEE_THRESHOLD_PERCENT) * 100 if funding_percentage > 0 else Decimal('0')

            return {
                "symbol": symbol,
                "position_amount": current_pos_amount,
                "position_notional": position_notional,
                "spot_balance": spot_balance,
                "effective_position_value": effective_position_value,
                "position_start_time": start_datetime.strftime('%Y-%m-%d %H:%M:%S'),
                "funding_payments_count": len(funding_payments),
                "total_funding": total_funding,
                "funding_as_percentage_of_effective_value": funding_percentage,
                "fee_coverage_progress": fee_coverage_progress,
                "asset": funding_payments[0]['asset'] if funding_payments else 'USDT'
            }

        except Exception as e:
            return None

    async def get_funding_rate_ma(self, symbol: str, periods: int = 10) -> Optional[Dict[str, Any]]:
        """
        Get moving average of funding rates for a symbol with correct funding frequency.

        MA calculation uses:
        - 1 current/next rate (from premiumIndex - the rate that will be paid next)
        - N-1 most recent historical rates (from fundingRate - rates already paid)

        This provides a more up-to-date MA that includes the current market rate.

        Args:
            symbol: Trading symbol (e.g., 'BTCUSDT')
            periods: Number of periods to include in moving average (default: 10)

        Returns:
            Dict with current rate, MA rate, and metadata, or None if insufficient data
        """
        try:
            # Detect funding frequency for this symbol
            funding_freq = await self.detect_funding_interval(symbol)

            # Fetch BOTH current/next rate AND historical rates concurrently
            current_rate_task = self.get_current_funding_rate(symbol)
            history_task = self.get_funding_rate_history(symbol=symbol, limit=periods - 1)

            current_rate_data, history = await asyncio.gather(current_rate_task, history_task)

            # Validate we have enough data
            if not current_rate_data or not history or len(history) < (periods - 1):
                return None

            # Extract current/next rate (will be paid at next funding)
            current_rate = float(current_rate_data.get('fundingRate', 0))

            # Extract historical rates (oldest first, as returned by API)
            # Take only N-1 historical rates
            historical_rates = [float(entry['fundingRate']) for entry in history[:(periods - 1)]]

            # Combine: historical rates (oldest to newest) + current rate (newest)
            # This gives us a list of N rates ordered from oldest to newest
            rates = historical_rates + [current_rate]

            # Use strategy logic for calculation with correct frequency
            result = DeltaNeutralLogic.calculate_funding_rate_ma(rates, periods, funding_freq)

            if result:
                # Add symbol and current rate for reference
                result['symbol'] = symbol
                result['current_rate'] = current_rate  # Store current rate separately

                # Calculate next funding time (funding happens every 8 hours at 00:00, 08:00, 16:00 UTC)
                from datetime import datetime, timedelta
                now = datetime.utcnow()
                current_hour = now.hour

                # Find next funding hour
                funding_hours = [0, 8, 16]
                next_funding_hour = None
                for fh in funding_hours:
                    if fh > current_hour:
                        next_funding_hour = fh
                        break

                if next_funding_hour is None:
                    # Next funding is tomorrow at 00:00
                    next_funding = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                else:
                    # Next funding is today
                    next_funding = now.replace(hour=next_funding_hour, minute=0, second=0, microsecond=0)

                # Convert to millisecond timestamp
                result['next_funding_time'] = int(next_funding.timestamp() * 1000)

            return result

        except Exception as e:
            return None

    async def get_all_funding_rates_ma(self, periods: int = 10) -> List[Dict[str, Any]]:
        """
        Get moving average funding rates for all available delta-neutral pairs.

        Args:
            periods: Number of periods for moving average calculation

        Returns:
            List of dicts with symbol, rates, and APR info sorted by effective MA APR
        """
        try:
            # Get all available delta-neutral pairs
            available_pairs = await self.discover_delta_neutral_pairs()
            if not available_pairs:
                return []

            # Fetch MA funding rates for all pairs concurrently
            tasks = [self.get_funding_rate_ma(symbol, periods) for symbol in available_pairs]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Filter out None and exceptions
            valid_results = [
                r for r in results
                if r is not None and not isinstance(r, Exception)
            ]

            # Sort by effective MA APR (highest first)
            valid_results.sort(key=lambda x: x['effective_ma_apr'], reverse=True)

            return valid_results

        except Exception as e:
            return []

    async def perform_health_check_analysis(self) -> Tuple[List[str], List[str], int, List[Dict[str, Any]]]:
        """
        Shared health check logic that analyzes positions and returns health issues.

        Returns:
            Tuple of (health_issues, critical_issues, dn_positions_count, position_pnl_data)
        """
        # Fetch position analysis data
        results = await asyncio.gather(
            self.analyze_current_positions(),
            self.get_perp_account_info(),
            return_exceptions=True
        )

        analysis_results = results[0] if isinstance(results[0], dict) else {}
        perp_account_info = results[1] if isinstance(results[1], dict) else {}

        if not analysis_results:
            return [], [], 0, []

        # Process positions data into list format
        all_positions = list(analysis_results.values())

        # Use strategy logic for core health analysis
        health_issues, critical_issues, dn_positions_count = DeltaNeutralLogic.perform_portfolio_health_analysis(all_positions)

        # Add additional PnL and price-specific checks for delta-neutral positions
        dn_positions = [p for p in all_positions if p.get('is_delta_neutral')]
        raw_perp_positions = [p for p in perp_account_info.get('positions', []) if float(p.get('positionAmt', 0)) != 0]

        # Fetch current prices for perpetual positions
        if raw_perp_positions:
            price_tasks = [self.get_perp_book_ticker(p['symbol']) for p in raw_perp_positions]
            price_results = await asyncio.gather(*price_tasks, return_exceptions=True)
            for i, pos in enumerate(raw_perp_positions):
                price_data = price_results[i]
                if not isinstance(price_data, Exception) and price_data.get('bidPrice'):
                    pos['markPrice'] = (float(price_data['bidPrice']) + float(price_data['askPrice'])) / 2

        # Add PnL and liquidity specific checks and collect position data
        position_pnl_data = []

        for pos in dn_positions:
            symbol = pos.get('symbol', 'N/A')
            spot_balance = pos.get('spot_balance', 0.0)

            # Find corresponding raw perp position to get PnL data and price
            perp_pos = next((p for p in raw_perp_positions if p.get('symbol') == symbol), None)
            current_price = 0.0
            pnl_pct = None
            position_value_usd = pos.get('position_value_usd', 0.0)

            if perp_pos:
                entry_price = float(perp_pos.get('entryPrice', 0))
                mark_price = perp_pos.get('markPrice', entry_price)
                current_price = mark_price
                position_amt = float(perp_pos.get('positionAmt', 0))

                # Calculate PnL percentage for short position
                if entry_price > 0 and position_amt < 0:  # Short position
                    pnl_pct = ((entry_price - mark_price) / entry_price) * 100

                    # Check for PnL warnings
                    if pnl_pct <= -50:
                        critical_issues.append(f"CRITICAL: {symbol} short position PnL: {pnl_pct:.2f}% (below -50%)")
                    elif pnl_pct <= -25:
                        health_issues.append(f"WARNING: {symbol} short position PnL: {pnl_pct:.2f}% (below -25%)")

            # Calculate spot position value using current price
            spot_value_usd = spot_balance * current_price

            # Check spot position value for liquidity concerns
            if spot_value_usd < 10:
                if spot_value_usd < 5:
                    critical_issues.append(f"CRITICAL: {symbol} spot position value: ${spot_value_usd:.2f} (below $5 - impossible to close)")
                else:
                    health_issues.append(f"WARNING: {symbol} spot position value: ${spot_value_usd:.2f} (below $10 - rebalancing advised)")

            # Update position with current price for rendering
            pos['current_price'] = current_price

            # Store position data for display
            position_pnl_data.append({
                'symbol': symbol,
                'position_value_usd': position_value_usd,
                'pnl_pct': pnl_pct,
                'imbalance_pct': pos.get('imbalance_pct', 0.0),
                'spot_value_usd': spot_value_usd
            })

        return health_issues, critical_issues, dn_positions_count, position_pnl_data

    async def close(self):
        """Close the HTTP session."""
        if self.session and not self.session.closed:
            await self.session.close()
