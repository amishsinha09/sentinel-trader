from __future__ import annotations


class Broker:
    """Minimal broker interface used by the execution layer."""
    name = "base"
    live = False  # True only when real money is at stake

    def account(self) -> dict:  # {"equity", "cash"}
        raise NotImplementedError

    def positions(self) -> dict:  # {symbol: {"qty", "avg_price", "side"}}
        raise NotImplementedError

    def quote(self, symbol: str) -> dict | None:  # {"bid", "ask"}
        return None

    def market_open(self) -> bool:
        return True

    def buy_bracket(self, symbol: str, qty: int, ref_price: float, stop: float, target: float) -> dict:
        """Entry with broker-side stop-loss and take-profit. Returns {"ok", "id", "fill_price", "error"}."""
        raise NotImplementedError

    def close(self, symbol: str) -> dict:
        raise NotImplementedError

    def option_chain(self, symbol: str):
        return None

    def submit_option(self, intent) -> dict:
        return {"ok": False, "error": f"{self.name} does not support options"}
