"""One trading cycle. Schedule it once per trading day shortly after the open (e.g. 9:35 ET):
signals come from the last completed daily bar, orders go out at today's open — the same
timing the backtester assumes.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .alerts import Alerts
from .data import last_bar_age_days
from .options import choose_structure, iv_rank_proxy, select_contracts
from .strategy import compute_rank, compute_signals, describe


def run_once(cfg: dict, broker, mem, risk, data: dict, dry_run: bool = False, log=print) -> dict:
    alerts = Alerts(cfg)
    role = "live" if broker.live else cfg["execution"].get("role", "paper")
    champ = mem.champion(role)
    if not champ:
        msg = f"No '{role}' strategy promoted yet — nothing to trade."
        log(msg)
        return {"error": msg}
    spec = champ["spec"]
    if spec["direction"] != "long":
        return {"error": "execution layer currently trades long strategies only"}
    log(f"[{broker.name}] strategy {champ['hash']} ({role}): {describe(spec)}")
    p, bt = spec["params"], cfg["backtest"]
    if bt["max_positions"] > risk.limits["max_open_positions"]:
        log(f"warning: backtest assumes {bt['max_positions']} positions but risk_limits.yaml caps live/paper at "
            f"{risk.limits['max_open_positions']} — results will differ from the backtest")
    out = {"strategy": champ["hash"], "broker": broker.name, "exits": [], "entries": [], "skipped": [], "options": []}

    # stale data guard: never trade on old bars
    age = last_bar_age_days(data)
    stale = age > cfg["data"]["max_staleness_days"]
    if stale:
        alerts.send(f"Data feed stale: last bar is {age} business days old — no new entries.", "warn")

    if hasattr(broker, "set_prices"):  # paper broker: apply the latest bar's stops/targets
        bars = {s: {k: float(df[k].iloc[-1]) for k in ("high", "low", "close")} for s, df in data.items()}
        for ev in broker.set_prices(bars):
            out["exits"].append(ev)

    acct = broker.account()
    for msg in risk.update_equity(acct["equity"]):
        alerts.send(msg, "critical")
    held = broker.positions()

    state_f = Path(cfg["state_dir"]) / f"bot_positions_{broker.name}.json"
    owned = json.loads(state_f.read_text(encoding="utf-8")) if state_f.exists() else {}
    owned = {s: v for s, v in owned.items() if s in held}  # drop positions closed at the broker (stop/target hit)

    sigs = {s: compute_signals(spec, df) for s, df in data.items()}

    # exits: signal or max hold (bracket legs handle stop/target at the broker)
    for s, info in list(owned.items()):
        ent, ex, _ = sigs[s]
        bars_held = int((data[s].index > np.datetime64(info["entry_date"])).sum())
        reason = "signal" if ex.iloc[-1] else "max_hold" if bars_held >= p["max_hold_bars"] else None
        if reason:
            res = {"ok": True, "dry_run": True} if dry_run else broker.close(s)
            risk.record_result(res.get("ok", False))
            out["exits"].append({"symbol": s, "reason": reason, **res})
            if res.get("ok"):
                owned.pop(s)
                held.pop(s, None)

    # entries
    gross = sum(v["qty"] * v["avg_price"] for v in held.values())
    def _rank(s):
        r = float(compute_rank(spec, data[s]).iloc[-1])
        return -r if math.isfinite(r) else math.inf

    for s in sorted(sigs, key=_rank):  # strongest first, same order as the backtest
        ent, ex, at = sigs[s]
        if stale or not ent.iloc[-1] or s in held:
            continue
        df, a = data[s], float(at.iloc[-1])
        q = broker.quote(s)
        ref = q["ask"] if q and q.get("ask") else float(df.close.iloc[-1])
        spread = (q["ask"] - q["bid"]) / ((q["ask"] + q["bid"]) / 2) * 1e4 if q and q.get("bid") else None
        if not math.isfinite(a) or a <= 0:
            continue
        stop, target = ref - p["stop_atr"] * a, ref + p["target_atr"] * a
        want = math.floor(acct["equity"] * bt["risk_per_trade"] / (p["stop_atr"] * a))
        dec = risk.check_entry(symbol=s, qty=want, price=ref, stop=stop, equity=acct["equity"], positions=held,
                               live=broker.live, market_open=broker.market_open(), spread_bps=spread,
                               gross_exposure=gross)
        if not dec.approved:
            out["skipped"].append({"symbol": s, "reasons": dec.reasons})
            continue
        if cfg["execution"]["options_enabled"]:
            out["options"].append(_options_leg(s, df, broker, risk, acct, cfg, dry_run))
            continue
        res = {"ok": True, "dry_run": True, "fill_price": ref} if dry_run else broker.buy_bracket(s, dec.qty, ref, stop, target)
        cb = risk.record_result(res.get("ok", False))
        if cb:
            alerts.send(cb, "critical")
        out["entries"].append({"symbol": s, "qty": dec.qty, "ref": round(ref, 2), "stop": round(stop, 2),
                               "target": round(target, 2), "notes": dec.reasons, **res})
        if res.get("ok"):
            owned[s] = {"entry_date": str(df.index[-1].date()), "spec": champ["hash"]}
            held[s] = {"qty": dec.qty, "avg_price": ref}
            gross += dec.qty * ref
        if not res.get("ok"):
            alerts.send(f"Order rejected for {s}: {res.get('error')}", "warn")

    if not dry_run:
        state_f.write_text(json.dumps(owned, indent=1), encoding="utf-8")
    acct = broker.account()
    summary = (f"{broker.name}: equity ${acct['equity']:,.0f} | entries {[e['symbol'] for e in out['entries']]} "
               f"| exits {[e['symbol'] for e in out['exits']]} | skipped {len(out['skipped'])}")
    log(summary)
    if out["entries"] or out["exits"]:
        alerts.send(summary)
    mem.event("run", summary, out)
    out["equity"] = acct["equity"]
    return out


def _options_leg(symbol, df, broker, risk, acct, cfg, dry_run):
    ivr = iv_rank_proxy(df)
    structure = choose_structure(+1, ivr)
    chain = broker.option_chain(symbol)
    if chain is None or structure is None:
        return {"symbol": symbol, "structure": structure, "iv_rank_proxy": round(ivr), "status": "no chain — intent only"}
    intent = select_contracts(symbol, structure, chain, risk.limits["options"])
    if intent is None:
        return {"symbol": symbol, "structure": structure, "status": "no liquid contracts"}
    dec = risk.check_option(max_loss_per_contract=intent.max_loss, contracts=99, defined_risk=intent.defined_risk,
                            equity=acct["equity"], live=broker.live)
    if not dec.approved:
        return {"symbol": symbol, "structure": structure, "status": "blocked", "reasons": dec.reasons}
    intent.contracts = dec.qty
    res = {"ok": True, "dry_run": True} if dry_run else broker.submit_option(intent)
    return {"symbol": symbol, "intent": intent.__dict__, **res}
