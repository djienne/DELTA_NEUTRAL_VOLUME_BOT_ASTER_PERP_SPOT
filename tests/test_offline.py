"""Offline checks of the trade path and decision rules. No network: a fake exchange
stands in for Aster and every sleep is instant. Run: python -m pytest tests -q"""

import asyncio
import os
import re
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aster_api_manager import AsterApiManager, fmt_qty  # noqa: E402
from strategy_logic import DeltaNeutralLogic as Logic  # noqa: E402
from volume_farming_strategy import DEFAULT_CONFIG, VolumeFarmingStrategy  # noqa: E402

CREDS = {'API_USER': '0x' + '1' * 40, 'API_SIGNER': '0x' + '2' * 40, 'API_PRIVATE_KEY': '0x' + 'a' * 64,
         'APIV1_PUBLIC_KEY': 'k', 'APIV1_PRIVATE_KEY': 's'}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """Instant sleeps; halt.json and the state file land in a temp dir."""
    async def no_sleep(*_args, **_kwargs):
        return None
    monkeypatch.setattr(asyncio, 'sleep', no_sleep)
    monkeypatch.chdir(tmp_path)
    for k, v in CREDS.items():
        monkeypatch.setenv(k, v)


class FakeAster:
    """Spot balance and perp position that market orders move. fee_in_base mimics a
    spot buy fee charged in the coin; the *_ok flags make a leg raise (a rejection)."""

    def __init__(self, spot=0.0, perp=0.0, fee_in_base=0.0, spot_ok=True, perp_ok=True, perp_close_fills=True):
        self.spot, self.perp, self.fee = spot, perp, fee_in_base
        self.spot_ok, self.perp_ok, self.perp_close_fills = spot_ok, perp_ok, perp_close_fills
        self.orders = []

    async def spot_market(self, symbol, side, quantity):
        self.orders.append(('spot', side, quantity))
        if not self.spot_ok:
            raise ValueError("spot rejected")
        q = float(quantity)
        self.spot += q * (1 - self.fee) if side == 'BUY' else -q
        return {'orderId': len(self.orders)}

    async def perp_market(self, symbol, side, quantity, reduce_only=False):
        self.orders.append(('perp', side, quantity, reduce_only))
        if not self.perp_ok:
            raise ValueError("perp rejected")
        if reduce_only and not self.perp_close_fills:
            return {'orderId': len(self.orders)}  # acknowledged, never filled
        self.perp += -float(quantity) if side == 'SELL' else float(quantity)
        return {'orderId': len(self.orders)}


def make_api(fake, price=1000.0, spot_step='0.00001', perp_step='0.001'):
    api = AsterApiManager(*CREDS.values())

    def info(step, notional_key):
        return {'baseAsset': 'X', 'filters': [{'filterType': 'LOT_SIZE', 'stepSize': step},
                                              {'filterType': 'MIN_NOTIONAL', notional_key: '5'}]}

    async def symbol_info(symbol, market):
        return info(spot_step, 'minNotional') if market == 'spot' else info(perp_step, 'notional')

    async def book(symbol, suppress_errors=False):
        return {'bidPrice': str(price), 'askPrice': str(price)}

    async def leverage_ok(symbol, leverage):
        return True

    async def spot_balance(asset, include_locked=True):
        return fake.spot

    async def perp_amt(symbol):
        return format(round(fake.perp, 12), 'f')

    api._symbol_info, api.get_spot_book_ticker, api.set_leverage = symbol_info, book, leverage_ok
    api._spot_market, api._perp_market = fake.spot_market, fake.perp_market
    api._spot_balance, api._perp_position_amt = spot_balance, perp_amt
    return api


# --- pure helpers ---

def test_fmt_qty_is_exact_on_the_grid():
    assert fmt_qty(0.29, '0.01') == '0.29'          # float truncation gave 0.28
    assert fmt_qty(0.004995, '0.001', rounding='ROUND_HALF_UP') == '0.005'
    assert fmt_qty(0.0000001, '0.0000001') == '0.0000001'  # no scientific notation
    assert fmt_qty(7.9, '1') == '7'


def test_stop_loss_values():
    assert [Logic.safe_stop_loss_pct(lev) for lev in (1, 2, 3)] == [-69.0, -34.0, -22.0]


def test_v3_signature_has_single_0x_prefix():
    sig = AsterApiManager(*CREDS.values())._sign_v3({'symbol': 'BTCUSDT'})['signature']
    assert re.fullmatch(r'0x[0-9a-f]{130}', sig)


@pytest.mark.parametrize('held_apr,best,best_apr,days,expected', [
    (12.0, 'Y', 30.0, 6.9, None),            # too young to rotate
    (12.0, 'Y', 21.9, 8.0, None),            # +9.9 points is not enough
    (12.0, 'Y', 22.0, 8.0, 'rotate'),        # +10 points after the hold
    (12.0, 'X', 90.0, 30.0, None),           # best is the held symbol
    (-10.0, None, None, 0.5, 'paying'),      # paying funding: close at any age
    (None, 'Y', 90.0, 30.0, None),           # no data: never act
])
def test_close_reason(held_apr, best, best_apr, days, expected):
    reason = Logic.close_reason('X', held_apr, best, best_apr, days, 10.0, 7, 10.0)
    assert (reason is None) if expected is None else (expected in reason)


def test_credentials_come_from_aster_env(monkeypatch, tmp_path):
    import aster_api_manager
    env_file = tmp_path / 'aster.env'
    monkeypatch.setattr(aster_api_manager, 'ENV_FILE', str(env_file))
    for k in CREDS:
        monkeypatch.delenv(k)
    with pytest.raises(ValueError, match='aster.env'):
        AsterApiManager.from_env()
    env_file.write_text(''.join(f"{k}={v}\n" for k, v in CREDS.items()))
    assert AsterApiManager.from_env().api_user == CREDS['API_USER']


# --- open_dn: sequential legs through two_leg ---

def test_open_hedges_the_spot_actually_received():
    fake = FakeAster(fee_in_base=0.001)          # 0.5 bought, 0.4995 received
    outcome = asyncio.run(make_api(fake).open_dn('XUSDT', 500.0, 1))
    assert outcome.ok and not outcome.halted
    assert [(o[0], o[1], float(o[2])) for o in fake.orders] == [('spot', 'BUY', 0.5), ('perp', 'SELL', 0.5)]  # no trim


def test_spot_rejected_means_perp_never_sent():
    fake = FakeAster(spot_ok=False)
    outcome = asyncio.run(make_api(fake).open_dn('XUSDT', 500.0, 1))
    assert not outcome.ok and all(o[0] == 'spot' for o in fake.orders)
    assert fake.perp == 0


def test_perp_rejected_after_spot_fill_unwinds_spot():
    fake = FakeAster(perp_ok=False)
    outcome = asyncio.run(make_api(fake).open_dn('XUSDT', 500.0, 1))
    assert not outcome.ok and not outcome.halted
    assert ('spot', 'SELL', '0.5') in fake.orders and abs(fake.spot) < 1e-9 and fake.perp == 0


def test_size_guard_sends_nothing_below_three_perp_steps():
    fake = FakeAster()
    with pytest.raises(ValueError):
        asyncio.run(make_api(fake).open_dn('XUSDT', 2.5, 1))   # 0.0025 < 3 x 0.001
    assert fake.orders == []


# --- close_dn: perp first, then only the bot's own spot ---

def test_close_sells_only_the_bots_spot():
    fake = FakeAster(spot=0.8, perp=-0.5)        # 0.3 of the coin is yours
    result = asyncio.run(make_api(fake).close_dn('XUSDT', 0.5))
    assert result['success'] and fake.perp == 0 and abs(fake.spot - 0.3) < 1e-9
    assert fake.orders[0][:2] == ('perp', 'BUY') and fake.orders[0][3] is True


def test_empty_spot_book_is_no_book():
    api = AsterApiManager(*CREDS.values())

    async def tickers(method, url, **kwargs):
        return [{'symbol': 'AUSDT', 'bidPrice': '1.0', 'askPrice': '1.1'},
                {'symbol': 'BUSDT', 'bidPrice': '0.00000', 'askPrice': '0.00000'}]
    api._request = tickers
    assert asyncio.run(api.get_book_tickers('spot')) == {'AUSDT': (1.0, 1.1)}


def test_close_keeps_spot_when_spot_book_is_empty():
    fake = FakeAster(spot=0.5, perp=-0.5)
    result = asyncio.run(make_api(fake, price=0.0).close_dn('XUSDT', 0.5))
    assert not result['success'] and result['spot_left'] == 0.5 and fake.spot == 0.5


def test_unfilled_perp_close_leaves_spot_untouched():
    fake = FakeAster(spot=0.5, perp=-0.5, perp_close_fills=False)
    result = asyncio.run(make_api(fake).close_dn('XUSDT', 0.5))
    assert not result['success'] and fake.spot == 0.5
    assert all(o[0] == 'perp' for o in fake.orders)


# --- strategy decisions ---

def portfolio(perp_amt=None, spot=0.0, price=100.0, upnl=0.0, leverage=1):
    raw = [] if perp_amt is None else [{'symbol': 'XUSDT', 'positionAmt': str(perp_amt), 'entryPrice': str(price),
                                        'unrealizedProfit': str(upnl), 'leverage': str(leverage), 'markPrice': price}]
    return {
        'perp_account_info': {'assets': [{'asset': 'USDT', 'walletBalance': '1000', 'availableBalance': '1000'}]},
        'raw_perp_positions': raw,
        'spot_balances': [{'asset': 'X', 'free': str(spot), 'locked': '0', 'value_usd': spot * price},
                          {'asset': 'USDT', 'free': '1000', 'locked': '0', 'value_usd': 1000.0}],
        'analyzed_positions': list(Logic.analyze_position_data(raw, {'X': spot}, {'XUSDT': {'baseAsset': 'X'}}).values()),
        'spot_books': {'XUSDT': (price, price)},
    }


def strategy_holding(position_leverage=1):
    s = VolumeFarmingStrategy(**DEFAULT_CONFIG)
    s.current_position = {'symbol': 'XUSDT', 'spot_qty': 0.5, 'perp_qty': 0.5, 'entry_apr': 12.0}
    s.position_opened_at = datetime.utcnow() - timedelta(days=1)
    s.position_leverage, s.entry_cash = position_leverage, 2000.0
    return s


def test_stop_uses_position_leverage_not_config():
    pf = portfolio(perp_amt=-0.5, spot=0.5, upnl=-12.5)   # -25% of the 50 USD perp notional
    assert strategy_holding(position_leverage=3)._check_position(pf, {}, None)[0] == 'close'
    assert strategy_holding(position_leverage=1)._check_position(pf, {}, None)[0] is None


def test_spot_only_remnant_is_closed_not_forgotten():
    action, reason = strategy_holding()._check_position(portfolio(perp_amt=None, spot=0.5), {}, None)
    assert action == 'close' and 'hedge broken' in reason
    assert strategy_holding()._check_position(portfolio(perp_amt=None, spot=0.01), {}, None)[0] == 'flat'


def run_cycle(s, pf, close_result):
    calls = []

    async def get_pf():
        if isinstance(pf, Exception):
            raise pf
        return pf

    async def scan():
        return {}, {'symbol': 'YUSDT', 'avg_apr': 50.0}

    async def close_dn(symbol, spot_qty):
        calls.append('close')
        return close_result

    async def open_position(best, pf):
        calls.append('open')

    s.api_manager.get_comprehensive_portfolio_data, s._scan = get_pf, scan
    s.api_manager.close_dn, s._open_position = close_dn, open_position
    asyncio.run(s._cycle())
    return calls


def test_failed_close_never_opens_in_the_same_cycle():
    # Perp already flat, spot sale failed: nothing else would stop an open here.
    s = strategy_holding()
    calls = run_cycle(s, portfolio(perp_amt=None, spot=0.5), {'success': False, 'spot_left': 0.5, 'message': 'x'})
    assert calls == ['close'] and s.current_position['spot_qty'] == 0.5


def test_account_fetch_failure_holds():
    s = strategy_holding()
    assert run_cycle(s, ConnectionError("down"), None) == [] and s.current_position is not None


def test_untracked_naked_short_halts_instead_of_opening():
    s = VolumeFarmingStrategy(**DEFAULT_CONFIG)
    calls = run_cycle(s, portfolio(perp_amt=-0.5, spot=0.0), None)
    assert calls == [] and os.path.exists('halt.json')
    assert run_cycle(s, portfolio(perp_amt=None), None) == []   # halted: still no open


def test_untracked_clean_hedge_is_adopted():
    s = VolumeFarmingStrategy(**DEFAULT_CONFIG)
    assert run_cycle(s, portfolio(perp_amt=-0.5, spot=0.5, leverage=2), None) == []
    assert s.current_position['symbol'] == 'XUSDT' and s.position_leverage == 2 and s.entry_cash is None


@pytest.mark.parametrize('perp_ok', [True, False])
def test_open_position_tracks_fills_or_halts(perp_ok):
    s = VolumeFarmingStrategy(**DEFAULT_CONFIG)
    s.api_manager = make_api(FakeAster(perp_ok=perp_ok))

    async def rebalance(leverage):
        return {'transfer_needed': False, 'current_spot_usdt': 520.0, 'current_perp_usdt': 520.0}
    s.api_manager.rebalance_usdt_by_leverage = rebalance

    asyncio.run(s._open_position({'symbol': 'XUSDT', 'avg_apr': 20.0}, portfolio()))
    if perp_ok:   # 520 x 0.96 = 499.2 USD -> 0.499 at 1000
        assert s.current_position['spot_qty'] == pytest.approx(0.499) and s.entry_cash == 2000.0
        assert not os.path.exists('halt.json')
    else:         # spot filled, hedge rejected, spot unwound: flat, but stop repeating it
        assert s.current_position is None and os.path.exists('halt.json')
