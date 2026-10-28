"""Split live trading for brokers the bot cannot reach directly (Robinhood via Claude's connector).

1. `signals` (runs where market data is reachable, e.g. GitHub Actions): computes, for every symbol,
   the strategy's entry/exit state, ranking score, ATR and the stop a new buy would use. Output is a
   small JSON file committed to the repo.
2. `plan` (runs anywhere, no network): takes that file plus the account's real cash, positions and
   resting stop orders, and returns the exact orders, sized with the same rules and hard risk limits
   as the bot's own execution layer.

The person or agent placing orders only executes the plan; it never decides trades itself.
"""
from __future__ import annotations

import json
import math
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .config import resolve
from .risk import RiskEngine
from .strategy import compute_rank, compute_signals, describe, spec_hash, validate_spec

PAUSE_FILE = "config/PAUSE"   # create this file in the repo (e.g. on github.com) to stop all new buys


def signals_table(spec: dict, data: dict) -> dict:
    spec = validate_spec(spec)
    p = spec["params"]
    rows, last_dates = {}, []
    for sym, df in data.items():
        if len(df) < 60:
            continue
        ent, ex, at = compute_signals(spec, df)
        rk = compute_rank(spec, df)
        close, a = float(df.close.iloc[-1]), float(at.iloc[-1])
        last_dates.append(df.index[-1])
        rows[sym] = {
            "date": str(df.index[-1].date()), "close": round(close, 4),
            "entry": bool(ent.iloc[-1]), "exit": bool(ex.iloc[-1]),
            "rank": round(float(rk.iloc[-1]), 4) if math.isfinite(rk.iloc[-1]) else None,
            "atr": round(a, 4) if math.isfinite(a) else None,
            "stop_if_bought": round(close - p["stop_atr"] * a, 2) if math.isfinite(a) else None,
        }
    return {"strategy": spec_hash(spec), "rules": describe(spec), "stop_atr": p["stop_atr"],
            "as_of": str(max(last_dates).date()),
            "generated": pd.Timestamp.utcnow().isoformat(timespec="seconds"), "symbols": rows}


def make_plan(sig: dict, account: dict, cfg: dict, cap: float, peak: float, today: date | None = None) -> dict:
    """account = {"value", "cash", "positions": {sym: {"qty","avg_price"}}, "stops": {sym: price},
                  "open_buys": [sym, ...]}"""
    today = today or date.today()
    bt = cfg["backtest"]
    risk = RiskEngine(tempfile.mkdtemp())  # hard limits file; no persisted halts in this stateless mode
    L = risk.limits
    rows = sig["symbols"]
    held = {s: v for s, v in account.get("positions", {}).items() if float(v["qty"]) > 0}
    stops = account.get("stops", {})
    open_buys = set(account.get("open_buys", []))
    plan = {"strategy": sig["strategy"], "signals_as_of": sig["as_of"], "sells": [], "buys": [],
            "add_stops": [], "skipped": [], "halts": []}

    age = int(np.busday_count(date.fromisoformat(sig["as_of"]), today))
    if age > cfg["data"]["max_staleness_days"]:
        plan["halts"].append(f"signals are {age} business days old; no new buys")
    if resolve(PAUSE_FILE).exists():
        plan["halts"].append(f"{PAUSE_FILE} exists; new buys paused")
    if account["value"] <= peak * (1 - L["max_drawdown_halt_pct"] / 100):
        plan["halts"].append(f"account ${account['value']:,.2f} is {L['max_drawdown_halt_pct']}%+ below "
                             f"${peak:,.2f}; new buys halted until reviewed")

    # exits first (always allowed, even when halted)
    for s, v in held.items():
        r = rows.get(s)
        if r and r["exit"]:
            plan["sells"].append({"symbol": s, "qty": v["qty"], "reason": "trend broke (exit rule)",
                                  "cancel_stop_first": s in stops})
    selling = {x["symbol"] for x in plan["sells"]}

    # every remaining holding must have a resting stop
    for s, v in held.items():
        if s in selling or s in stops:
            continue
        r = rows.get(s) or {}
        stop = round(float(v["avg_price"]) - sig["stop_atr"] * r["atr"], 2) if r.get("atr") else None
        plan["add_stops"].append({"symbol": s, "qty": v["qty"], "stop": stop})

    # entries, strongest rank first, into free slots
    E = min(float(account["value"]), float(cap))
    remaining = {s: v for s, v in held.items() if s not in selling}
    gross = sum(float(v["qty"]) * float(rows.get(s, {}).get("close") or v["avg_price"]) for s, v in remaining.items())
    cash = float(account["cash"])
    cands = sorted((s for s, r in rows.items() if r["entry"] and s not in remaining and s not in open_buys),
                   key=lambda s: -(rows[s]["rank"] if rows[s]["rank"] is not None else -1e9))
    for s in cands:
        r = rows[s]
        if plan["halts"]:
            plan["skipped"].append({"symbol": s, "reason": "halted"})
            continue
        if len(remaining) >= min(bt["max_positions"], L["max_open_positions"]):
            plan["skipped"].append({"symbol": s, "reason": "no free slot"})
            continue
        if not r["atr"] or r["atr"] <= 0:
            continue
        price, stop = r["close"], r["stop_if_bought"]
        want = math.floor(E * bt["risk_per_trade"] / (price - stop))
        dec = risk.check_entry(symbol=s, qty=want, price=price, stop=stop, equity=E, positions=remaining,
                               live=False, gross_exposure=gross)
        qty = min(dec.qty, math.floor(cash / (price * 1.01)))  # 1% headroom for the limit price
        if not dec.approved or qty < 1:
            plan["skipped"].append({"symbol": s, "reason": "; ".join(dec.reasons) or "not enough cash"})
            continue
        plan["buys"].append({"symbol": s, "qty": qty, "ref_close": price, "max_limit": round(price * 1.01, 2),
                             "stop": stop, "rank": r["rank"], "risk_at_stop": round(qty * (price - stop), 2)})
        remaining[s] = {"qty": qty, "avg_price": price}
        gross += qty * price
        cash -= qty * price
    plan["sizing"] = {"equity_used": E, "cap": cap, "cash_after_buys": round(cash, 2)}
    return plan


def summary(plan: dict) -> str:
    out = [f"Strategy {plan['strategy']}, signals as of {plan['signals_as_of']}"]
    out += [f"HALT: {h}" for h in plan["halts"]]
    out += [f"SELL {x['qty']} {x['symbol']} at market ({x['reason']})" +
            (" — cancel its stop first" if x["cancel_stop_first"] else "") for x in plan["sells"]]
    out += [f"ADD STOP: sell {x['qty']} {x['symbol']} if price falls to {x['stop']}" for x in plan["add_stops"]]
    out += [f"BUY {x['qty']} {x['symbol']} limit at the ask, max ${x['max_limit']} "
            f"(close {x['ref_close']}), then stop at {x['stop']} (risk ${x['risk_at_stop']})" for x in plan["buys"]]
    if not (plan["sells"] or plan["buys"] or plan["add_stops"]):
        out.append("No orders today.")
    out += [f"skip {x['symbol']}: {x['reason']}" for x in plan["skipped"]]
    return "\n".join(out)
