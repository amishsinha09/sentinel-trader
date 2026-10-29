"""Several strategies trading one account.

Each strategy runs in its own sleeve with a share of the capital (equal by default), using the same
engine, costs and risk sizing as single-strategy backtests. The account's equity is the sum of the
sleeves. Sleeves never borrow from each other, so one strategy cannot blow up the others' capital.

A combined portfolio is still just one more candidate: score it with the same validation gate before
trusting it, and count it as a trial.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

import pandas as pd

from .backtest import buy_and_hold, run_backtest
from .metrics import perf_metrics
from .strategy import spec_hash, validate_spec


@dataclass
class Sleeve:
    name: str
    spec: dict
    weight: float


@dataclass
class PortfolioResult:
    equity: pd.Series
    trades: pd.DataFrame          # every closed trade, with a "strategy" column
    sleeves: dict                  # name -> sleeve equity Series
    metrics: dict
    benchmark: pd.Series           # equal-weight buy & hold of the whole universe, same capital


def make_sleeves(specs: list[dict], weights: list[float] | None = None) -> list[Sleeve]:
    specs = [validate_spec(s) for s in specs]
    weights = weights or [1.0] * len(specs)
    total = float(sum(weights))
    seen, out = set(), []
    for s, w in zip(specs, weights):
        name = s.get("name") or spec_hash(s)
        if name in seen:
            name = f"{name}-{spec_hash(s)[:4]}"
        seen.add(name)
        out.append(Sleeve(name, s, w / total))
    return out


def run_portfolio(sleeves: list[Sleeve], data: dict, bt: dict, start=None, end=None) -> PortfolioResult:
    capital = float(bt.get("initial_capital", 100000.0))
    curves, trades = {}, []
    for sl in sleeves:
        b = copy.deepcopy(bt)
        b["initial_capital"] = capital * sl.weight
        res = run_backtest(sl.spec, data, b, start, end)
        curves[sl.name] = res.equity
        t = res.trades.copy()
        t.insert(0, "strategy", sl.name)
        trades.append(t)
    eq = pd.DataFrame(curves).sort_index().ffill().sum(axis=1).rename("equity")
    tr = pd.concat(trades, ignore_index=True) if trades else pd.DataFrame()
    if len(tr):
        tr = tr.sort_values("exit_date", kind="stable").reset_index(drop=True)
    bench = buy_and_hold(data, eq.index[0], eq.index[-1], capital).equity.reindex(eq.index).ffill()
    return PortfolioResult(eq, tr, curves, perf_metrics(eq, tr), bench)


def monthly_returns(equity: pd.Series) -> pd.Series:
    m = equity.resample("ME").last()
    first = equity.iloc[0]
    return m.pct_change().fillna(m.iloc[0] / first - 1)
