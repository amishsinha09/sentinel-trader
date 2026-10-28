"""Map an equity signal to a defined-risk options structure and pick contracts from a chain.

Structure choice follows the same logic as a direction + volatility-regime options engine:
  bullish, IV low/normal  -> call debit spread (long call if conviction is high and IV is low)
  bullish, IV high        -> bull put credit spread
  bearish, IV low/normal  -> put debit spread
  bearish, IV high        -> bear call credit spread
  neutral, IV high        -> iron condor
Chain columns expected: symbol, type ('call'|'put'), strike, expiry (date), bid, ask, delta, open_interest
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd


@dataclass
class OptionIntent:
    underlying: str
    structure: str
    legs: list = field(default_factory=list)  # {symbol, side, type, strike, expiry, mid}
    net_debit: float = 0.0                    # per share; negative = credit
    max_loss: float = 0.0                     # per contract, dollars
    max_gain: float = 0.0
    contracts: int = 0
    defined_risk: bool = True


def iv_regime(iv_rank: float) -> str:
    return "low" if iv_rank < 30 else "high" if iv_rank > 60 else "normal"


def choose_structure(direction: int, iv_rank: float, conviction: float = 0.5) -> str | None:
    reg = iv_regime(iv_rank)
    if direction > 0:
        if reg == "high":
            return "bull_put_credit_spread"
        return "long_call" if reg == "low" and conviction >= 0.8 else "call_debit_spread"
    if direction < 0:
        return "bear_call_credit_spread" if reg == "high" else "put_debit_spread"
    return "iron_condor" if reg == "high" else None


def _liquid(chain: pd.DataFrame, limits: dict, today: date) -> pd.DataFrame:
    c = chain.copy()
    c["mid"] = (c.bid + c.ask) / 2
    c["dte"] = (pd.to_datetime(c.expiry) - pd.Timestamp(today)).dt.days
    c = c[(c.dte >= limits["min_dte"]) & (c.dte <= limits["max_dte"]) & (c.open_interest >= limits["min_open_interest"])
          & (c.mid > 0) & ((c.ask - c.bid) / c.mid * 100 <= limits["max_bid_ask_pct"])]
    return c


def _nearest(df: pd.DataFrame, delta: float) -> pd.Series | None:
    if df.empty:
        return None
    return df.iloc[(df.delta.abs() - delta).abs().argsort().iloc[0]]


def _leg(row, side):
    return {"symbol": row.symbol, "side": side, "type": row.type, "strike": float(row.strike),
            "expiry": str(pd.Timestamp(row.expiry).date()), "mid": round(float(row.mid), 2)}


def _vertical(c, typ, long_delta, short_delta, debit: bool):
    side = c[c.type == typ]
    lng, sht = _nearest(side, long_delta), _nearest(side, short_delta)
    if lng is None or sht is None or lng.strike == sht.strike:
        return None
    width = abs(lng.strike - sht.strike)
    net = lng.mid - sht.mid
    if debit and not 0 < net < width:
        return None
    if not debit and not 0 < -net < width:
        return None
    max_loss = (net if debit else width + net) * 100
    max_gain = (width - net if debit else -net) * 100
    return [_leg(lng, "buy"), _leg(sht, "sell")], net, max_loss, max_gain


def select_contracts(underlying: str, structure: str, chain: pd.DataFrame, limits: dict,
                     today: date | None = None, target_dte: int = 35) -> OptionIntent | None:
    c = _liquid(chain, limits, today or date.today())
    if c.empty:
        return None
    exp = c.loc[(c.dte - target_dte).abs().idxmin(), "expiry"]
    c = c[c.expiry == exp]
    built = None
    if structure == "long_call":
        r = _nearest(c[c.type == "call"], 0.60)
        if r is not None:
            built = [_leg(r, "buy")], r.mid, r.mid * 100, float("inf")
    elif structure == "call_debit_spread":
        built = _vertical(c, "call", 0.55, 0.30, True)
    elif structure == "put_debit_spread":
        built = _vertical(c, "put", 0.55, 0.30, True)
    elif structure == "bull_put_credit_spread":
        built = _vertical(c, "put", 0.15, 0.30, False)
    elif structure == "bear_call_credit_spread":
        built = _vertical(c, "call", 0.15, 0.30, False)
    elif structure == "iron_condor":
        p = _vertical(c, "put", 0.10, 0.20, False)
        k = _vertical(c, "call", 0.10, 0.20, False)
        if p and k:
            net = p[1] + k[1]
            width = max(p[2] / 100 - p[1], k[2] / 100 - k[1])  # widest wing (credit nets are negative)
            built = p[0] + k[0], net, (width + net) * 100, -net * 100
    if not built:
        return None
    legs, net, ml, mg = built
    return OptionIntent(underlying, structure, legs, round(float(net), 2), round(float(ml), 2), mg)


def iv_rank_proxy(df: pd.DataFrame) -> float:
    """Without IV history, use the 1-year percentile of realised volatility (0-100)."""
    from .features import FeatureCache

    v = FeatureCache(df).get("atr_pct_rank", 252).iloc[-1]
    return float(v * 100) if pd.notna(v) else 50.0
