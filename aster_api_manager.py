import asyncio
import aiohttp
import time
import hmac
import hashlib
import json
import logging
import math
import urllib.parse
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Dict, List, Optional, Any, Tuple
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_abi import encode
from strategy_logic import DeltaNeutralLogic
from two_leg import LegSpec, TwoLegOutcome, execute_two_leg

logger = logging.getLogger(__name__)

# Base URLs for the APIs
FUTURES_BASE_URL = "https://fapi.asterdex.com"
SPOT_BASE_URL = "https://sapi.asterdex.com"


def fmt_qty(qty: float, step, rounding=ROUND_DOWN) -> str:
    """Quantity on the lot-step grid as a plain decimal string.

    Decimal, not float: float truncation turned 0.29 into 0.28 on ~3% of values.
    """
    step = Decimal(str(step))
    return format((Decimal(str(qty)) / step).to_integral_value(rounding) * step, 'f')


def _lot_step(symbol_info: dict) -> Decimal:
    return Decimal(next(f['stepSize'] for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'))


def _min_notional(symbol_info: dict) -> float:
    f = next((f for f in symbol_info['filters'] if f['filterType'] == 'MIN_NOTIONAL'), {})
    return float(f.get('minNotional') or f.get('notional') or 0)  # spot uses minNotional, perp notional


async def _no_resting_orders() -> int:
    return 0  # market orders only: nothing can rest on the book


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
        self._exchange_info_cache: Dict[str, dict] = {}

    def _session(self) -> aiohttp.ClientSession:
        # A hung request must not stall the stop-loss check for aiohttp's default 5 minutes.
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self.session

    async def _request(self, method: str, url: str, **kwargs) -> Any:
        """One HTTP call. Errors after which an order MAY have executed become ConnectionError.

        two_leg.classify_submission treats ConnectionError as UNKNOWN (verify by reading the
        position back) but any other exception as a definite rejection. A 5xx, a dropped
        connection or a truncated body can all hide an order that did land.
        """
        try:
            async with self._session().request(method, url, **kwargs) as response:
                if response.status >= 500:
                    raise ConnectionError(f"{method} {url}: HTTP {response.status} {await response.text()}")
                if not response.ok:
                    logger.warning(f"API error {method} {url}: {response.status} {await response.text()}")
                response.raise_for_status()
                return await response.json()
        except (aiohttp.ServerDisconnectedError, aiohttp.ClientPayloadError) as e:
            raise ConnectionError(f"{method} {url}: {e}") from e

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
        # bytes().hex() never has a 0x prefix; HexBytes.hex() has one before hexbytes 1.0
        my_dict['signature'] = '0x' + bytes(signed_message.signature).hex()

        return my_dict

    async def _signed_request_v3(self, method: str, endpoint: str, params: dict = None) -> Any:
        """Signed request to the v3 futures API (Ethereum signature)."""
        signed = self._sign_v3(params or {})
        where = 'params' if method.upper() == 'GET' else 'data'
        return await self._request(method, f"{FUTURES_BASE_URL}{endpoint}", headers={
            'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': 'PythonApp/1.0'}, **{where: signed})

    async def _make_spot_request(self, method: str, path: str, params: dict = None, signed: bool = False,
                                 base_url: str = SPOT_BASE_URL) -> Any:
        """Request to a v1 endpoint, HMAC-SHA256 signed when `signed`."""
        params = dict(params or {})
        if signed:
            params['timestamp'] = int(time.time() * 1000)
            params['recvWindow'] = 5000
            query = urllib.parse.urlencode(params)
            params['signature'] = hmac.new(self.apiv1_private.encode(), query.encode(), hashlib.sha256).hexdigest()
        return await self._request(method, f"{base_url}{path}", params=params,
                                   headers={'X-MBX-APIKEY': self.apiv1_public})

    # --- Exchange info ---

    async def _exchange_info(self, market: str) -> dict:
        """Cached exchangeInfo for 'spot' or 'perp'."""
        if market not in self._exchange_info_cache:
            url = f"{SPOT_BASE_URL}/api/v1/exchangeInfo" if market == 'spot' else f"{FUTURES_BASE_URL}/fapi/v1/exchangeInfo"
            self._exchange_info_cache[market] = await self._request('GET', url)
        return self._exchange_info_cache[market]

    async def _symbol_info(self, symbol: str, market: str) -> dict:
        info = await self._exchange_info(market)
        found = next((s for s in info.get('symbols', []) if s['symbol'] == symbol), None)
        if not found:
            raise ValueError(f"Symbol {symbol} not found in {market} exchange info.")
        return found

    async def discover_delta_neutral_pairs(self) -> List[str]:
        """Symbols trading on both the spot and the perp market."""
        spot, perp = await asyncio.gather(self._exchange_info('spot'), self._exchange_info('perp'))
        trading = lambda info: [s['symbol'] for s in info.get('symbols', []) if s.get('status') == 'TRADING']
        return DeltaNeutralLogic.find_delta_neutral_pairs(trading(spot), trading(perp))

    # --- Market data (public) ---

    async def get_all_funding_rates(self) -> Dict[str, Dict[str, float]]:
        """{symbol: {'rate': predicted next funding rate, 'interval_hours': h}} for every perp.

        Two bulk calls, no cache: Aster runs 1h, 4h and 8h intervals side by side and
        changes them over time (ATUSDT went 1h -> 4h). A symbol without a published
        interval is left out rather than guessed.
        """
        premium, info = await asyncio.gather(
            self._request('GET', f"{FUTURES_BASE_URL}/fapi/v1/premiumIndex"),
            self._request('GET', f"{FUTURES_BASE_URL}/fapi/v1/fundingInfo"))
        hours = {i['symbol']: float(i['fundingIntervalHours']) for i in info if i.get('fundingIntervalHours')}
        return {p['symbol']: {'rate': float(p['lastFundingRate']), 'interval_hours': hours[p['symbol']]}
                for p in premium if p['symbol'] in hours and p.get('lastFundingRate') not in (None, '')}

    async def get_funding_rate_history(self, symbol: str, limit: int = 50) -> list:
        """Settled funding rates, oldest first."""
        return await self._request('GET', f"{FUTURES_BASE_URL}/fapi/v1/fundingRate",
                                   params={'symbol': symbol, 'limit': limit})

    async def get_24h_quote_volumes(self) -> Dict[str, float]:
        """{symbol: spot + perp 24h quote volume in USDT}."""
        spot, perp = await asyncio.gather(
            self._request('GET', f"{SPOT_BASE_URL}/api/v1/ticker/24hr"),
            self._request('GET', f"{FUTURES_BASE_URL}/fapi/v1/ticker/24hr"))
        volumes: Dict[str, float] = {}
        for t in spot + perp:
            volumes[t['symbol']] = volumes.get(t['symbol'], 0.0) + float(t.get('quoteVolume') or 0)
        return volumes

    async def get_book_tickers(self, market: str) -> Dict[str, Tuple[float, float]]:
        """{symbol: (bid, ask)} for every symbol of 'spot' or 'perp' with a two-sided book, one call.

        Empty books report "0.00000" (a truthy string); several paired coins have one on spot.
        """
        url = f"{SPOT_BASE_URL}/api/v1/ticker/bookTicker" if market == 'spot' else f"{FUTURES_BASE_URL}/fapi/v1/ticker/bookTicker"
        books = {t['symbol']: (float(t.get('bidPrice') or 0), float(t.get('askPrice') or 0))
                 for t in await self._request('GET', url)}
        return {s: b for s, b in books.items() if b[0] > 0 and b[1] > 0}

    async def get_perp_book_ticker(self, symbol: str) -> dict:
        """Get perpetuals book ticker for a symbol."""
        return await self._request('GET', f"{FUTURES_BASE_URL}/fapi/v1/ticker/bookTicker", params={'symbol': symbol})

    async def get_spot_book_ticker(self, symbol: str, suppress_errors: bool = False) -> dict:
        """Get spot book ticker for a symbol. `suppress_errors` is kept for callers; errors still raise."""
        return await self._make_spot_request('GET', '/api/v1/ticker/bookTicker', params={'symbol': symbol})

    # --- Account (signed) ---

    async def get_perp_account_info(self) -> dict:
        """Get perpetuals account information."""
        return await self._signed_request_v3('GET', '/fapi/v3/account')

    async def get_spot_account_balances(self) -> list:
        """Get spot account balances."""
        response = await self._make_spot_request('GET', '/api/v1/account', signed=True)
        return response.get('balances', [])

    async def _perp_position_amt(self, symbol: str) -> str:
        """Signed perp position size as the exchange's own string ('0' when flat)."""
        account = await self.get_perp_account_info()
        return next((p['positionAmt'] for p in account.get('positions', [])
                     if p.get('symbol') == symbol and Decimal(p.get('positionAmt', '0')) != 0), '0')

    async def _spot_balance(self, asset: str, include_locked: bool = True) -> float:
        balances = await self.get_spot_account_balances()
        return sum(float(b.get('free', 0)) + (float(b.get('locked', 0)) if include_locked else 0.0)
                   for b in balances if b.get('asset') == asset)

    async def get_income_history(self, symbol: Optional[str] = None, income_type: Optional[str] = None,
                                 start_time: Optional[int] = None, limit: int = 1000) -> List[Dict[str, Any]]:
        """Perp income history (e.g. FUNDING_FEE). v1 endpoint, HMAC signed."""
        params = {'limit': limit, 'symbol': symbol, 'incomeType': income_type, 'startTime': start_time}
        params = {k: v for k, v in params.items() if v is not None}
        return await self._make_spot_request('GET', '/fapi/v1/income', params=params, signed=True,
                                             base_url=FUTURES_BASE_URL)

    async def get_comprehensive_portfolio_data(self) -> Dict[str, Any]:
        """Account snapshot. Raises on any failed call: a partial snapshot is not evidence of anything.

        spot_balances carry value_usd at the spot bid (USDT at 1). raw_perp_positions carry
        markPrice = perp book mid. analyzed_positions covers every symbol with a perp position.
        """
        perp_account, spot_balances, perp_info, spot_books, perp_books = await asyncio.gather(
            self.get_perp_account_info(), self.get_spot_account_balances(), self._exchange_info('perp'),
            self.get_book_tickers('spot'), self.get_book_tickers('perp'))

        raw_perp_positions = [p for p in perp_account.get('positions', []) if float(p.get('positionAmt', 0)) != 0]
        for pos in raw_perp_positions:
            if pos['symbol'] in perp_books:
                pos['markPrice'] = sum(perp_books[pos['symbol']]) / 2

        held = [b for b in spot_balances if float(b.get('free', 0)) > 0 or float(b.get('locked', 0)) > 0]
        for b in held:
            qty = float(b.get('free', 0)) + float(b.get('locked', 0))
            b['value_usd'] = qty if b['asset'] == 'USDT' else qty * spot_books.get(f"{b['asset']}USDT", (0.0, 0.0))[0]

        analyzed = DeltaNeutralLogic.analyze_position_data(
            perp_positions=raw_perp_positions,
            spot_balances={b['asset']: float(b.get('free', 0)) + float(b.get('locked', 0)) for b in held},
            perp_symbol_map={s['symbol']: s for s in perp_info.get('symbols', [])})

        return {
            'perp_account_info': perp_account,
            'raw_perp_positions': raw_perp_positions,
            'spot_balances': held,
            'analyzed_positions': list(analyzed.values()),
            'spot_books': spot_books,
        }

    # --- Orders ---

    async def _perp_market(self, symbol: str, side: str, quantity: str, reduce_only: bool = False) -> dict:
        params = {'symbol': symbol, 'side': side, 'type': 'MARKET', 'quantity': quantity, 'positionSide': 'BOTH'}
        if reduce_only:
            params['reduceOnly'] = 'true'
        return await self._signed_request_v3('POST', '/fapi/v3/order', params)

    async def _spot_market(self, symbol: str, side: str, quantity: str) -> dict:
        return await self._make_spot_request('POST', '/api/v1/order', signed=True, params={
            'symbol': symbol, 'side': side, 'type': 'MARKET', 'quantity': quantity})

    async def get_perp_leverage(self, symbol: str) -> int:
        """Get current leverage for a perpetual trading symbol (1 if the symbol is not listed)."""
        account_info = await self.get_perp_account_info()
        return next((int(float(p.get('leverage', '1'))) for p in account_info.get('positions', [])
                     if p.get('symbol') == symbol), 1)

    async def set_leverage(self, symbol: str, leverage: int = 1) -> bool:
        """Set perp leverage (v1, HMAC signed). True only when the exchange confirms the value."""
        try:
            response = await self._make_spot_request('POST', '/fapi/v1/leverage', signed=True,
                                                     params={'symbol': symbol, 'leverage': leverage},
                                                     base_url=FUTURES_BASE_URL)
            return bool(response) and int(response.get('leverage')) == leverage
        except Exception as e:
            logger.warning(f"set_leverage {symbol} {leverage}x failed: {e}")
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

    # --- Delta-neutral open / close ---

    async def open_dn(self, symbol: str, notional_usd: float, leverage: int) -> TwoLegOutcome:
        """Open long spot + short perp through two_leg.execute_two_leg. Nothing is sent if a guard fails.

        Spot is the pilot: a spot rejection (the likelier one) leaves nothing to unwind, and
        the perp hedge is sized from the spot quantity actually received, so a fee taken
        in the base coin cannot unbalance the pair. The hedge rounds to the NEAREST perp
        step: flooring 0.004995 BTC to 0.004 would leave a whole step unhedged.

        Each leg uses its own lot step as tolerance. two_leg counts a fill of <= 2 ticks as
        "nothing moved", so the pilot must be >= 3 perp steps or a filled hedge would read
        as rejected and the spot leg would be unwound around a live short.
        """
        spot_info, perp_info, book = await asyncio.gather(
            self._symbol_info(symbol, 'spot'), self._symbol_info(symbol, 'perp'), self.get_spot_book_ticker(symbol))
        spot_step, perp_step = _lot_step(spot_info), _lot_step(perp_info)
        ask = float(book['askPrice'])
        qty = float(fmt_qty(notional_usd / ask, max(spot_step, perp_step)))
        min_usd = max(_min_notional(spot_info), _min_notional(perp_info))
        if qty < 3 * float(perp_step) or qty * ask < min_usd:
            raise ValueError(f"{symbol}: {qty} (${qty * ask:.2f}) is below 3 perp steps ({perp_step}) "
                             f"or the ${min_usd} minimum notional; not opening")
        # The stop-loss is derived from this leverage, so a failed set must not open.
        if not await self.set_leverage(symbol, leverage):
            raise RuntimeError(f"could not set {symbol} leverage to {leverage}x; not opening")

        base = spot_info['baseAsset']

        async def perp_position() -> float:
            return float(await self._perp_position_amt(symbol))

        spot = LegSpec(
            name="Aster-spot", symbol=symbol, side="buy", intent_qty=qty,
            submit=lambda q: self._spot_market(symbol, 'BUY', fmt_qty(q, spot_step)),
            read_position=lambda: self._spot_balance(base),
            close_market=lambda q, side: self._spot_market(symbol, side.upper(), fmt_qty(q, spot_step)),
            cancel_open=_no_resting_orders, amount_tick=float(spot_step))
        perp = LegSpec(
            name="Aster-perp", symbol=symbol, side="sell", intent_qty=qty,
            submit=lambda q: self._perp_market(symbol, 'SELL', fmt_qty(q, perp_step, ROUND_HALF_UP)),
            read_position=perp_position,
            close_market=lambda q, side: self._perp_market(symbol, side.upper(), fmt_qty(q, perp_step, ROUND_HALF_UP)),
            cancel_open=_no_resting_orders, amount_tick=float(perp_step))

        outcome = await execute_two_leg(
            spot, perp, min_notional_qty=max(float(perp_step), _min_notional(perp_info) / ask))
        if outcome.pilot is not None:
            # Tells us which asset spot fees are charged in (base coin vs quote).
            logger.info(f"Spot order response: {outcome.pilot.raw}")
        return outcome

    async def close_dn(self, symbol: str, spot_qty: float) -> Dict[str, Any]:
        """Close the perp leg first, then the bot's own spot, reading each back.

        Spot is sold only once the perp reads flat, so a failed perp close can never turn
        into a naked short. Only min(free, spot_qty) is sold: coins you hold yourself stay.
        Works on one-legged remnants. Returns success, message and spot_left (the bot's
        spot quantity still held); success means perp flat and spot_left below the spot
        minimum notional (unsellable dust).
        """
        spot_info = await self._symbol_info(symbol, 'spot')
        base, spot_step, spot_min = spot_info['baseAsset'], _lot_step(spot_info), _min_notional(spot_info)

        amt = Decimal(await self._perp_position_amt(symbol))
        if amt != 0:
            await self._perp_market(symbol, 'BUY' if amt < 0 else 'SELL', format(abs(amt), 'f'), reduce_only=True)
            await asyncio.sleep(2)
            amt = Decimal(await self._perp_position_amt(symbol))
            if amt != 0:
                return {'success': False, 'spot_left': spot_qty,
                        'message': f"perp still {amt} after the close order; spot left untouched"}

        bid = float((await self.get_spot_book_ticker(symbol))['bidPrice'] or 0)
        if bid <= 0:  # empty spot book: nothing can be sold or valued; keep tracking it
            return {'success': False, 'spot_left': spot_qty, 'message': f"no {symbol} spot bid; spot kept"}
        before = await self._spot_balance(base)
        sell = fmt_qty(min(await self._spot_balance(base, include_locked=False), spot_qty), spot_step)
        if float(sell) * bid >= spot_min:
            await self._spot_market(symbol, 'SELL', sell)
            await asyncio.sleep(2)
        spot_left = max(0.0, spot_qty - (before - await self._spot_balance(base)))
        if spot_left * bid >= spot_min:
            return {'success': False, 'spot_left': spot_left,
                    'message': f"perp flat but {spot_left} {base} (${spot_left * bid:.2f}) still held"}
        return {'success': True, 'spot_left': spot_left, 'message': f"{symbol} closed; perp flat, spot dust {spot_left}"}

    async def close(self):
        """Close the HTTP session."""
        if self.session and not self.session.closed:
            await self.session.close()
