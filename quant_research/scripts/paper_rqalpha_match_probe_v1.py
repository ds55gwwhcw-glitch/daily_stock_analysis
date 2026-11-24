"""Isolated RQAlpha matching experiment; never touches the research ledger."""
from datetime import datetime
from types import SimpleNamespace as NS
from rqalpha.const import MATCHING_TYPE, MARKET, SIDE, POSITION_EFFECT
from rqalpha.core.events import EVENT
from rqalpha.environment import Environment
from rqalpha.interface import TransactionCost
from rqalpha.model.order import Order, MarketOrder
from rqalpha.mod.rqalpha_mod_sys_simulation.matcher import DefaultBarMatcher


def match_probe(raw_open, daily_volume, side='buy', asof='2026-11-23'):
    """Exercise installed RQAlpha on a hypothetical order, not a broker."""
    assert raw_open > 0 and daily_volume > 0
    assert side in {'buy', 'sell'}
    day = datetime.fromisoformat(asof).replace(hour=10)
    code = "600000.XSHG"
    events = []
    instrument = NS(order_book_id=code, type="CS", board_type="MainBoard",
                    round_lot=100, min_order_quantity=100, order_step_size=100,
                    listed_date=datetime(1999,11,10), market=MARKET.CN,
                    contract_multiplier=1,
                    calc_cash_occupation=lambda price, quantity, direction, dt: price*quantity)
    bar = NS(open=raw_open, close=raw_open, volume=daily_volume)
    env = NS(calendar_dt=day, trading_dt=day,
             config=NS(base=NS(frequency="1d", round_price=False,
                              partial_fill_on_insufficient_cash=False)),
             event_bus=NS(publish_event=events.append),
             price_board=NS(get_last_price=lambda *a: raw_open,
                            get_limit_up=lambda *a: float('nan'),
                            get_limit_down=lambda *a: float('nan')),
             data_proxy=NS(get_active_instrument=lambda *a: instrument,
                           instrument_not_none=lambda *a: instrument,
                           instrument=lambda *a: instrument,
                           get_tick_size=lambda *a: 0.01),
             get_instrument=lambda *a: instrument, get_bar=lambda *a: bar,
             get_last_price=lambda *a: raw_open,
             calc_transaction_cost=lambda *a: TransactionCost.zero())
    config = NS(matching_type=MATCHING_TYPE.NEXT_BAR_OPEN,
                slippage_model="PriceRatioSlippage", slippage=0.0,
                price_limit=False, inactive_limit=True, volume_limit=True,
                volume_percent=0.01, liquidity_limit=False)
    prior = Environment._env
    try:
        Environment._env = env
        matcher = DefaultBarMatcher(env, config)
        order = Order.__from_create__(code, 100, SIDE.BUY if side == 'buy' else SIDE.SELL,
                                      MarketOrder(), POSITION_EFFECT.OPEN if side == 'buy' else POSITION_EFFECT.CLOSE)
        order.active()
        matcher.match(NS(cash=1000000,
                         calc_close_today_amount=lambda *a: 0), order, False)
        trades = [e.trade for e in events if e.event_type == EVENT.TRADE]
        assert len(trades) == 1 and trades[0].last_quantity == 100
        assert abs(trades[0].last_price - raw_open) < 1e-8
        return {"shares": 100, "price": trades[0].last_price,
                "side": side, "asof": asof, "status": "ISOLATED_HISTORICAL_TEST_ONLY"}
    finally:
        Environment._env = prior


def round_trip_probe(buy, sell, sessions):
    """Historical two-session sandbox; no persistent state or strategy signal."""
    from decimal import Decimal
    from paper_shadow_account_v1 import eligible_open, settled_for_sale
    from paper_exchange_calendar_v1 import validated_sessions
    dates = validated_sessions(sessions)
    a, b = buy["last_quote_day"], sell["last_quote_day"]
    assert a in sessions and b in sessions
    assert sessions.index(b) == sessions.index(a) + 1
    assert settled_for_sale({"entry_asof": a}, b)
    assert eligible_open("sh.600000", buy, a, "buy")
    assert eligible_open("sh.600000", sell, b, "sell")
    assert buy["adjustflag"] == sell["adjustflag"] == "3"
    # Price continuity screen only; not a full corporate-action clearance.
    assert abs(Decimal(str(buy["close"])) - Decimal(str(sell["preclose"]))) <= Decimal("0.01")
    first = match_probe(float(buy["open"]), float(buy["volume"]), "buy", a)
    second = match_probe(float(sell["open"]), float(sell["volume"]), "sell", b)
    shares = first["shares"]
    assert shares == second["shares"] == 100
    cost = Decimal(str(first["price"])) * shares
    proceeds = Decimal(str(second["price"])) * shares
    buy_fee = cost * Decimal("0.0015")
    sell_fee = proceeds * Decimal("0.002")
    initial = Decimal("1000000")
    cash = initial - cost - buy_fee
    assert cash >= 0
    interim_equity = cash + Decimal(str(buy["close"])) * shares
    final = cash + proceeds - sell_fee
    assert final - initial == proceeds - cost - buy_fee - sell_fee
    return {"test_only": True, "buy": first, "sell": second,
            "interim_equity": str(interim_equity), "final_cash": str(final),
            "final_positions": 0, "fees": str(buy_fee+sell_fee),
            "corporate_actions_fully_verified": False}
