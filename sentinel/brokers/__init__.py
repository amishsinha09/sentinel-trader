from .base import Broker  # noqa: F401


def make_broker(cfg: dict):
    kind = cfg["execution"]["broker"]
    if kind == "paper":
        from .paper import PaperBroker
        return PaperBroker(cfg)
    if kind in ("alpaca", "alpaca_paper", "alpaca_live"):
        from .alpaca import AlpacaBroker
        return AlpacaBroker(paper=(kind != "alpaca_live"))
    if kind == "ticket":
        from .ticket import TicketBroker
        return TicketBroker(cfg)
    raise ValueError(f"unknown broker {kind}")
