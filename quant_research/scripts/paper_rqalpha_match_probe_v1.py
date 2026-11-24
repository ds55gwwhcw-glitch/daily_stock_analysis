"""Isolated RQAlpha matching experiment; never touches the research ledger."""
from datetime import datetime
from types import SimpleNamespace as NS
from rqalpha.const import MATCHING_TYPE, MARKET, SIDE, POSITION_EFFECT
from rqalpha.core.events import EVENT
from rqalpha.environment import Environment
from rqalpha.interface import TransactionCost
from rqalpha.model.order import Order, MarketOrder
from rqalpha.mod.rqalpha_mod_sys_simulation.matcher import DefaultBarMatcher


def match_probe(raw_open, daily_volume):
    """Exercise installed RQAlpha on a hypothetical order, not a broker."""
    assert raw_open > 0 and daily_volume > 0
    code = "600000.XSHG"
    events = []
    instrument = NS(order_book_id=code, type="CS", board_type="MainBoard",
                    round_lot=100, min_order_quantity=100, order_step_size=100,
                    listed_date=datetime(1999,11,10), market=MARKET.CN,
                    contract_multiplier=1,
                    calc_cash_occupation=lambda price, quantity, direction, dt: price*quantity)
    bar = NS(open=raw_open, close=raw_open, volume=daily_volume)
    env = NS(calendar_dt=datetime(2026,11,23,10), trading_dt=datetime(2026,11,23,10),
             config=NS(base=NS(frequency="1d", round_price=False,
                              partial_fill_on_insufficient_cash=False)),
             event_bus=NS(publish_event=events.append),
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
        order = Order.__from_create__(code, 100, SIDE.BUY, MarketOrder(), POSITION_EFFECT.OPEN)
        order.active()
        matcher.match(NS(cash=1000000,
                         calc_close_today_amount=lambda *a: 0), order, False)
        trades = [e.trade for e in events if e.event_type == EVENT.TRADE]
        assert len(trades) == 1 and trades[0].last_quantity == 100
        assert abs(trades[0].last_price - raw_open) < 1e-8
        return {"shares": 100, "price": trades[0].last_price,
                "status": "ISOLATED_HISTORICAL_TEST_ONLY"}
    finally:
        Environment._env = prior
