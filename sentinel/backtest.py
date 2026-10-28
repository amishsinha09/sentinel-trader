"""Portfolio backtester.

Execution model (deliberately conservative):
  * Signals are read at bar t's close; orders fill at bar t+1's open (no look-ahead).
  * Every fill pays half the spread + slippage; commission per share.
  * Stops and targets are checked intrabar. A gap through the stop fills at the open.
    If stop and target are both inside one bar, the stop is assumed to hit first.
  * Position size = risk_per_trade * equity / (stop_atr * ATR), capped by max position %,
    gross exposure and (for longs) available cash.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .metrics import perf_metrics
from .strategy import compute_signals, validate_spec


@dataclass
class CostModel:
    commission_per_share: float = 0.0
    spread_bps: float = 2.0
    slippage_bps: float = 3.0
    mult: float = 1.0

    def fill(self, price: float, side: int) -> float:
        """side +1 = buying, -1 = selling."""
        return price * (1 + side * (self.spread_bps / 2 + self.slippage_bps) * self.mult / 1e4)

    def commission(self, qty: float) -> float:
        return qty * self.commission_per_share * self.mult


@dataclass
class Position:
    qty: int
    entry: float
    stop: float
    target: float
    entry_date: pd.Timestamp
    entry_comm: float
    last: float
    bars: int = 0


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: pd.DataFrame
    metrics: dict = field(default_factory=dict)


def precompute(spec: dict, data: dict[str, pd.DataFrame]) -> dict:
    return {s: compute_signals(spec, df) for s, df in data.items()}


def run_backtest(spec: dict, data: dict[str, pd.DataFrame], bt: dict, start=None, end=None,
                 cost_mult: float = 1.0, signals: dict | None = None) -> BacktestResult:
    spec = validate_spec(spec)
    p = spec["params"]
    d = 1 if spec["direction"] == "long" else -1
    sigs = signals or precompute(spec, data)
    cost = CostModel(bt.get("commission_per_share", 0.0), bt.get("spread_bps", 2.0), bt.get("slippage_bps", 3.0), cost_mult)

    idx = sorted(set().union(*[df.index for df in data.values()]))
    idx = pd.DatetimeIndex(idx)
    if start is not None:
        idx = idx[idx >= pd.Timestamp(start)]
    if end is not None:
        idx = idx[idx <= pd.Timestamp(end)]
    n = len(idx)
    syms = list(data)
    A = {}
    for s in syms:
        df = data[s].reindex(idx)
        ent, ex, at = (x.reindex(idx) for x in sigs[s])
        A[s] = (df.open.to_numpy(), df.high.to_numpy(), df.low.to_numpy(), df.close.to_numpy(),
                ent.fillna(False).to_numpy(bool), ex.fillna(False).to_numpy(bool), at.to_numpy())

    cash = float(bt.get("initial_capital", 100000.0))
    pos: dict[str, Position] = {}
    trades, equity = [], np.empty(n)

    def close(sym, price, reason, i):
        nonlocal cash
        ps = pos.pop(sym)
        fill = cost.fill(price, -d)
        comm = cost.commission(ps.qty)
        cash += d * ps.qty * fill - comm
        trades.append(dict(symbol=sym, side="long" if d == 1 else "short", entry_date=ps.entry_date,
                           exit_date=idx[i], entry_price=ps.entry, exit_price=fill, qty=ps.qty,
                           pnl=d * ps.qty * (fill - ps.entry) - ps.entry_comm - comm,
                           ret=d * (fill / ps.entry - 1), bars=ps.bars, exit_reason=reason))

    for i in range(n):
        # 1) exits at the open: signal from yesterday's close, max hold, gaps through stop/target
        for s in list(pos):
            o = A[s][0][i]
            if np.isnan(o):
                continue
            ps, reason = pos[s], None
            if i > 0 and A[s][5][i - 1]:
                reason = "signal"
            elif ps.bars >= p["max_hold_bars"]:
                reason = "max_hold"
            elif (o - ps.stop) * d <= 0:
                reason = "stop_gap"
            elif (o - ps.target) * d >= 0:
                reason = "target_gap"
            if reason:
                close(s, o, reason, i)

        # 2) entries at the open from yesterday's signal
        if i > 0:
            eq_prev = equity[i - 1]
            gross = sum(ps.qty * ps.last for ps in pos.values())
            for s in syms:
                if len(pos) >= bt.get("max_positions", 5):
                    break
                if s in pos or not A[s][4][i - 1]:
                    continue
                o, a = A[s][0][i], A[s][6][i - 1]
                if np.isnan(o) or np.isnan(a) or a <= 0:
                    continue
                fill = cost.fill(o, d)
                stop_dist = p["stop_atr"] * a
                qty = math.floor(eq_prev * bt.get("risk_per_trade", 0.01) / stop_dist)
                qty = min(qty, math.floor(eq_prev * bt.get("max_position_pct", 0.2) / fill))
                qty = min(qty, math.floor(max(eq_prev * bt.get("max_gross_exposure", 1.0) - gross, 0) / fill))
                if d == 1:
                    qty = min(qty, math.floor(cash / (fill + cost.commission_per_share * cost.mult)))
                if qty < 1:
                    continue
                comm = cost.commission(qty)
                cash -= d * qty * fill + comm
                gross += qty * fill
                pos[s] = Position(qty, fill, fill - d * stop_dist, fill + d * p["target_atr"] * a, idx[i], comm, fill)

        # 3) intrabar stops / targets (stop first if both touched)
        for s in list(pos):
            h, l = A[s][1][i], A[s][2][i]
            if np.isnan(h):
                continue
            ps = pos[s]
            if (d == 1 and l <= ps.stop) or (d == -1 and h >= ps.stop):
                close(s, ps.stop, "stop", i)
            elif (d == 1 and h >= ps.target) or (d == -1 and l <= ps.target):
                close(s, ps.target, "target", i)

        # 4) mark to market
        for s, ps in pos.items():
            c = A[s][3][i]
            if not np.isnan(c):
                ps.last = c
                ps.bars += 1
        equity[i] = cash + sum(d * ps.qty * ps.last for ps in pos.values())

    if n and pos:  # liquidate at the final close so trade stats are complete
        for s in list(pos):
            close(s, pos[s].last, "end", n - 1)
        equity[-1] = cash

    eq = pd.Series(equity, index=idx, name="equity")
    tr = pd.DataFrame(trades, columns=["symbol", "side", "entry_date", "exit_date", "entry_price", "exit_price",
                                       "qty", "pnl", "ret", "bars", "exit_reason"])
    return BacktestResult(eq, tr, perf_metrics(eq, tr))


def buy_and_hold(data: dict[str, pd.DataFrame], start=None, end=None, capital=100000.0) -> BacktestResult:
    closes = pd.DataFrame({s: df.close for s, df in data.items()}).sort_index()
    if start is not None:
        closes = closes[closes.index >= pd.Timestamp(start)]
    if end is not None:
        closes = closes[closes.index <= pd.Timestamp(end)]
    rets = closes.pct_change().mean(axis=1).fillna(0)
    eq = capital * (1 + rets).cumprod()
    return BacktestResult(eq, pd.DataFrame(), perf_metrics(eq))
