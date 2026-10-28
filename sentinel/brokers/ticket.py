"""Ticket broker: writes approved, risk-checked order tickets to state/tickets/ instead of trading.

Use it for brokers without a bot API (e.g. Robinhood). You — or Claude through your Robinhood
connector, with trade approval on — place the ticket by hand. Keep state/tickets/positions.json
in sync ({"SYMBOL": {"qty": 10, "avg_price": 100.0}}) and set equity in state/tickets/account.json.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .base import Broker


class TicketBroker(Broker):
    name = "ticket"
    live = True  # tickets are placed with REAL money, so they need a live-approved strategy and the live lock

    def __init__(self, cfg: dict):
        self.dir = Path(cfg["state_dir"]) / "tickets"
        self.dir.mkdir(exist_ok=True)
        self.default_equity = cfg["backtest"]["initial_capital"]

    def _read(self, name, default):
        f = self.dir / name
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else default

    def account(self):
        a = self._read("account.json", {"equity": self.default_equity, "cash": self.default_equity})
        return {"equity": float(a["equity"]), "cash": float(a.get("cash", a["equity"]))}

    def positions(self):
        return {s: {"qty": p["qty"], "avg_price": p["avg_price"], "side": "long"}
                for s, p in self._read("positions.json", {}).items()}

    def _ticket(self, body: dict) -> dict:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        f = self.dir / f"{ts}-{body['action']}-{body.get('symbol', body.get('underlying', 'x'))}.json"
        f.write_text(json.dumps(body, indent=1, default=str), encoding="utf-8")
        return {"ok": True, "id": f.name, "fill_price": body.get("ref_price")}

    def buy_bracket(self, symbol, qty, ref_price, stop, target):
        return self._ticket({"action": "BUY", "symbol": symbol, "qty": qty, "ref_price": round(ref_price, 2),
                             "stop_loss": round(stop, 2), "take_profit": round(target, 2),
                             "instructions": "Buy at market; then place a GTC stop and a GTC limit (OCO if available)."})

    def close(self, symbol):
        return self._ticket({"action": "SELL", "symbol": symbol, "instructions": "Cancel resting orders, sell entire position."})

    def submit_option(self, intent):
        return self._ticket({"action": "OPTION", **intent.__dict__})
