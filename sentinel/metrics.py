"""Performance metrics, including the Probabilistic and Deflated Sharpe Ratio
(Bailey & López de Prado, 2014) used to penalise repeated strategy testing."""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

N = NormalDist()
EULER = 0.5772156649


def perf_metrics(equity: pd.Series, trades: pd.DataFrame | None = None, periods: int = 252) -> dict:
    eq = equity.dropna()
    r = eq.pct_change().dropna()
    m = {"n_days": int(len(r))}
    std = r.std(ddof=1) if len(r) > 1 else 0.0
    m["sr_period"] = float(r.mean() / std) if std > 0 else 0.0
    m["sharpe"] = m["sr_period"] * math.sqrt(periods)
    down = math.sqrt(float((np.minimum(r, 0) ** 2).mean())) if len(r) else 0.0
    m["sortino"] = float(r.mean() / down * math.sqrt(periods)) if down > 0 else 0.0
    dd = eq / eq.cummax() - 1 if len(eq) else pd.Series(dtype=float)
    m["max_drawdown"] = float(-dd.min()) if len(dd) else 0.0
    total = float(eq.iloc[-1] / eq.iloc[0] - 1) if len(eq) > 1 else 0.0
    years = len(r) / periods
    m["total_return"] = total
    m["cagr"] = float((1 + total) ** (1 / years) - 1) if years > 0 and total > -1 else 0.0
    m["calmar"] = m["cagr"] / m["max_drawdown"] if m["max_drawdown"] > 0 else 0.0
    skew, kurt = (float(r.skew()) if len(r) > 2 else 0.0), (float(r.kurt() + 3) if len(r) > 3 else 3.0)
    m["skew"] = skew if math.isfinite(skew) else 0.0          # flat equity (no trades) gives NaN
    m["kurtosis"] = kurt if math.isfinite(kurt) else 3.0
    t = trades if trades is not None else pd.DataFrame()
    m["n_trades"] = int(len(t))
    if len(t):
        wins, losses = t.loc[t.pnl > 0, "pnl"].sum(), -t.loc[t.pnl <= 0, "pnl"].sum()
        m["win_rate"] = float((t.pnl > 0).mean())
        m["profit_factor"] = float(wins / losses) if losses > 0 else float("inf") if wins > 0 else 0.0
        m["avg_trade_return"] = float(t.ret.mean())
        m["avg_bars"] = float(t.bars.mean())
    else:
        m.update(win_rate=0.0, profit_factor=0.0, avg_trade_return=0.0, avg_bars=0.0)
    return m


def probabilistic_sharpe(sr: float, sr_star: float, T: int, skew: float, kurt: float) -> float:
    """P(true per-period Sharpe > sr_star) given an observed per-period Sharpe sr over T obs."""
    if T < 3 or sr == 0 or not all(math.isfinite(x) for x in (sr, sr_star, skew, kurt)):
        return 0.0
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if denom <= 0:
        return 0.0
    return N.cdf((sr - sr_star) * math.sqrt(T - 1) / math.sqrt(denom))


def expected_max_sharpe(n_trials: int, var_trials: float) -> float:
    """Expected best per-period Sharpe from n_trials skill-less strategies."""
    if n_trials < 2:
        return 0.0
    z1 = N.inv_cdf(1 - 1 / n_trials)
    z2 = N.inv_cdf(1 - 1 / (n_trials * math.e))
    return math.sqrt(max(var_trials, 0.0)) * ((1 - EULER) * z1 + EULER * z2)


def deflated_sharpe(sr: float, T: int, skew: float, kurt: float, n_trials: int, var_trials: float) -> float:
    var = max(var_trials, 1.0 / max(T, 1))  # never assume trials were less dispersed than noise
    return probabilistic_sharpe(sr, expected_max_sharpe(n_trials, var), T, skew, kurt)
