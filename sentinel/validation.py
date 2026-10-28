"""The validation gate. A candidate must pass every blocking check to be promoted.

The holdout slice is never touched here — only `holdout_check` reads it, and only at
live promotion.
"""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd

from .backtest import buy_and_hold, precompute, run_backtest
from .metrics import deflated_sharpe
from .strategy import RESERVED, SpecError, validate_spec


def split_dates(data: dict[str, pd.DataFrame], splits: dict) -> dict[str, tuple]:
    idx = pd.DatetimeIndex(sorted(set().union(*[df.index for df in data.values()])))
    n = len(idx)
    a = int(n * splits["dev"])
    b = int(n * (splits["dev"] + splits["validation"]))
    return {"dev": (idx[0], idx[a - 1]), "validation": (idx[a], idx[b - 1]),
            "research": (idx[0], idx[b - 1]), "holdout": (idx[b], idx[-1])}


def perturbations(spec: dict, pct: float) -> list[dict]:
    out = []
    for k, v in spec["params"].items():
        for f in (1 - pct, 1 + pct):
            s = copy.deepcopy(spec)
            nv = v * f
            if isinstance(v, int) and not isinstance(v, bool):
                nv = int(round(nv))
                if nv == v:
                    nv = v + (1 if f > 1 else -1)
            if k in RESERVED:
                lo, hi = RESERVED[k][:2]
                nv = min(max(nv, lo), hi)
            s["params"][k] = nv
            try:
                out.append(validate_spec(s))
            except SpecError:
                pass  # e.g. a period pushed out of range
    return out


def evaluate_candidate(spec: dict, data: dict, cfg: dict, n_trials: int, var_trials: float) -> dict:
    v, bt = cfg["validation"], cfg["backtest"]
    spec = validate_spec(spec)
    w = split_dates(data, cfg["splits"])
    sigs = precompute(spec, data)
    dev = run_backtest(spec, data, bt, *w["dev"], signals=sigs)
    val = run_backtest(spec, data, bt, *w["validation"], signals=sigs)
    stress = run_backtest(spec, data, bt, *w["validation"], cost_mult=v["cost_stress_mult"], signals=sigs)
    bench = buy_and_hold(data, *w["validation"])

    # Rolling out-of-sample consistency across the research period
    r_idx = pd.DatetimeIndex(sorted(set().union(*[df.index for df in data.values()])))
    r_idx = r_idx[(r_idx >= w["research"][0]) & (r_idx <= w["research"][1])]
    wf = []
    for chunk in np.array_split(np.arange(len(r_idx)), v["walk_forward_windows"]):
        res = run_backtest(spec, data, bt, r_idx[chunk[0]], r_idx[chunk[-1]], signals=sigs)
        wf.append({"start": str(r_idx[chunk[0]].date()), "end": str(r_idx[chunk[-1]].date()),
                   "return": res.metrics["total_return"], "sharpe": res.metrics["sharpe"],
                   "max_drawdown": res.metrics["max_drawdown"]})
    wf_pos = float(np.mean([x["return"] > 0 for x in wf]))

    # Parameter sensitivity: neighbours of a real edge should also work
    sens = [run_backtest(s, data, bt, *w["research"]).metrics["sharpe"] for s in perturbations(spec, v["sensitivity_pct"])]
    sens_frac = float(np.mean([x > 0 for x in sens])) if sens else 0.0

    m = val.metrics
    dsr = deflated_sharpe(m["sr_period"], m["n_days"], m["skew"], m["kurtosis"], n_trials, var_trials)
    ratio = m["sharpe"] / dev.metrics["sharpe"] if dev.metrics["sharpe"] > 0 else (1.0 if m["sharpe"] > 0 else 0.0)

    checks = [
        ("min_trades_dev", dev.metrics["n_trades"] >= v["min_trades_dev"], dev.metrics["n_trades"], v["min_trades_dev"]),
        ("min_trades_oos", m["n_trades"] >= v["min_trades_oos"], m["n_trades"], v["min_trades_oos"]),
        ("oos_sharpe", m["sharpe"] >= v["min_oos_sharpe"], round(m["sharpe"], 3), v["min_oos_sharpe"]),
        ("oos_max_drawdown", m["max_drawdown"] <= v["max_oos_drawdown"], round(m["max_drawdown"], 3), v["max_oos_drawdown"]),
        ("oos_vs_in_sample", ratio >= v["min_oos_is_ratio"], round(ratio, 3), v["min_oos_is_ratio"]),
        ("walk_forward_consistency", wf_pos >= v["min_wf_positive_frac"], round(wf_pos, 3), v["min_wf_positive_frac"]),
        ("parameter_robustness", sens_frac >= v["min_sensitivity_frac"], round(sens_frac, 3), v["min_sensitivity_frac"]),
        ("survives_2x_costs", stress.metrics["total_return"] > 0, round(stress.metrics["total_return"], 4), "> 0"),
        ("deflated_sharpe", dsr >= v["min_dsr"], round(dsr, 3), v["min_dsr"]),
    ]
    return {
        "passed": all(c[1] for c in checks),
        "checks": [{"name": n, "pass": bool(ok), "value": val_, "threshold": thr} for n, ok, val_, thr in checks],
        "dev": dev.metrics, "validation": m, "cost_stress": stress.metrics,
        "benchmark_validation": bench.metrics, "walk_forward": wf, "sensitivity_sharpes": sens,
        "n_trials": n_trials, "dsr": dsr, "windows": {k: [str(a.date()), str(b.date())] for k, (a, b) in w.items()},
    }


def holdout_check(spec: dict, data: dict, cfg: dict, val_sharpe: float) -> dict:
    """One-shot test on never-seen data. Called only by live promotion, and logged."""
    w = split_dates(data, cfg["splits"])
    res = run_backtest(spec, data, cfg["backtest"], *w["holdout"])
    m = res.metrics
    checks = [
        ("holdout_positive_sharpe", m["sharpe"] > 0),
        ("holdout_drawdown", m["max_drawdown"] <= cfg["validation"]["max_oos_drawdown"]),
        ("holdout_not_collapsed", m["sharpe"] >= 0.5 * val_sharpe),
    ]
    return {"passed": all(ok for _, ok in checks), "checks": [{"name": n, "pass": bool(ok)} for n, ok in checks],
            "metrics": m, "window": [str(w["holdout"][0].date()), str(w["holdout"][1].date())]}
