"""Local simulated broker. Fills at the reference price plus spread/slippage; checks stops on each run."""
from __future__ import annotations

import json
from pathlib import Path

from ..backtest import CostModel
from .base import Broker


class PaperBroker(Broker):
    name = "paper"

    def __init__(self, cfg: dict):
        bt = cfg["backtest"]
        self.cost = CostModel(bt["commission_per_share"], bt["spread_bps"], bt["slippage_bps"])
        self.file = Path(cfg["state_dir"]) / "paper_account.json"
        self.s = json.loads(self.file.read_text()) if self.file.exists() else {
            "cash": bt["initial_capital"], "positions": {}, "prices": {}, "fills": []}

    def _save(self):
        self.file.write_text(json.dumps(self.s, indent=1))

    def set_prices(self, bars: dict):
        """bars: {symbol: {"high","low","close"}} from the latest daily bar. Triggers stops/targets."""
        self.s["prices"].update({k: v["close"] for k, v in bars.items()})
        events = []
        for sym, p in list(self.s["positions"].items()):
            b = bars.get(sym)
            if not b:
                continue
            if b["low"] <= p["stop"]:
                events.append(self._exit(sym, p["stop"], "stop"))
            elif b["high"] >= p["target"]:
                events.append(self._exit(sym, p["target"], "target"))
        self._save()
        return events

    def account(self):
        mv = sum(p["qty"] * self.s["prices"].get(s, p["avg_price"]) for s, p in self.s["positions"].items())
        return {"equity": self.s["cash"] + mv, "cash": self.s["cash"]}

    def positions(self):
        return {s: {"qty": p["qty"], "avg_price": p["avg_price"], "side": "long"} for s, p in self.s["positions"].items()}

    def buy_bracket(self, symbol, qty, ref_price, stop, target):
        fill = self.cost.fill(ref_price, +1)
        cost = qty * fill + self.cost.commission(qty)
        if cost > self.s["cash"]:
            return {"ok": False, "error": "insufficient cash"}
        self.s["cash"] -= cost
        self.s["positions"][symbol] = {"qty": qty, "avg_price": fill, "stop": stop, "target": target}
        self.s["fills"].append({"symbol": symbol, "side": "buy", "qty": qty, "price": fill})
        self._save()
        return {"ok": True, "id": f"paper-{len(self.s['fills'])}", "fill_price": fill}

    def _exit(self, symbol, price, reason):
        p = self.s["positions"].pop(symbol)
        fill = self.cost.fill(price, -1)
        self.s["cash"] += p["qty"] * fill - self.cost.commission(p["qty"])
        self.s["fills"].append({"symbol": symbol, "side": "sell", "qty": p["qty"], "price": fill, "reason": reason})
        return {"symbol": symbol, "reason": reason, "pnl": p["qty"] * (fill - p["avg_price"])}

    def close(self, symbol):
        if symbol not in self.s["positions"]:
            return {"ok": False, "error": "no position"}
        ev = self._exit(symbol, self.s["prices"].get(symbol, self.s["positions"][symbol]["avg_price"]), "signal")
        self._save()
        return {"ok": True, **ev}

    def submit_option(self, intent):
        self.s["fills"].append({"option_intent": intent.__dict__})
        self._save()
        return {"ok": True, "id": "paper-option", "note": "logged only — paper options are not marked to market"}
