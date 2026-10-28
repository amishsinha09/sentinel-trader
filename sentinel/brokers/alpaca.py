"""Alpaca adapter (alpaca-py). Paper and live use the same code; keys come from env vars:
ALPACA_API_KEY / ALPACA_SECRET_KEY. Entries are bracket orders, so the stop-loss and
take-profit rest at Alpaca and keep protecting the position if this machine is off.
"""
from __future__ import annotations

import os

from .base import Broker


class AlpacaBroker(Broker):
    def __init__(self, paper: bool = True):
        kv, sv = ("ALPACA_API_KEY", "ALPACA_SECRET_KEY") if paper else ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY")
        if not os.environ.get(kv) or not os.environ.get(sv):
            raise SystemExit(f"Alpaca {'paper' if paper else 'LIVE'} keys missing: put {kv} and {sv} in .env")
        from alpaca.trading.client import TradingClient

        self.paper = paper
        self.live = not paper
        self.name = "alpaca_paper" if paper else "alpaca_live"
        key, secret = os.environ[kv], os.environ[sv]
        self.client = TradingClient(key, secret, paper=paper)
        self._key, self._secret = key, secret

    def account(self):
        a = self.client.get_account()
        return {"equity": float(a.equity), "cash": float(a.cash)}

    def positions(self):
        return {p.symbol: {"qty": abs(int(float(p.qty))), "avg_price": float(p.avg_entry_price), "side": str(p.side.value)}
                for p in self.client.get_all_positions() if getattr(p, "asset_class", None) is None
                or str(p.asset_class.value) == "us_equity"}

    def market_open(self):
        return bool(self.client.get_clock().is_open)

    def quote(self, symbol):
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockLatestQuoteRequest

            q = StockHistoricalDataClient(self._key, self._secret).get_stock_latest_quote(
                StockLatestQuoteRequest(symbol_or_symbols=symbol))[symbol]
            return {"bid": float(q.bid_price), "ask": float(q.ask_price)}
        except Exception:
            return None

    def buy_bracket(self, symbol, qty, ref_price, stop, target):
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest, StopLossRequest, TakeProfitRequest

        try:
            o = self.client.submit_order(MarketOrderRequest(
                symbol=symbol, qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.GTC,
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=round(target, 2)),
                stop_loss=StopLossRequest(stop_price=round(stop, 2))))
            return {"ok": True, "id": str(o.id), "fill_price": ref_price}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def close(self, symbol):
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        try:
            # cancel ONLY this symbol's resting stop/target legs; other positions keep their protection
            for o in self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])):
                self.client.cancel_order_by_id(o.id)
            self.client.close_position(symbol)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def submit_option(self, intent):
        """Multi-leg limit order at the net mid. Verify against your alpaca-py version before live use."""
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest

        try:
            legs = [OptionLegRequest(symbol=l["symbol"], ratio_qty=1,
                                     side=OrderSide.BUY if l["side"] == "buy" else OrderSide.SELL) for l in intent.legs]
            o = self.client.submit_order(LimitOrderRequest(
                qty=intent.contracts, order_class=OrderClass.MLEG, time_in_force=TimeInForce.DAY,
                limit_price=round(intent.net_debit, 2), legs=legs))
            return {"ok": True, "id": str(o.id)}
        except Exception as e:
            return {"ok": False, "error": str(e)}
