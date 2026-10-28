"""Observe + diagnose: where and why is the current strategy losing? Uses the dev window only."""
from __future__ import annotations

import pandas as pd

from ..backtest import run_backtest
from ..features import FeatureCache
from ..validation import split_dates


def diagnose(spec: dict, data: dict, cfg: dict) -> dict:
    w = split_dates(data, cfg["splits"])
    res = run_backtest(spec, data, cfg["backtest"], *w["dev"])
    t = res.trades.copy()
    out = {"metrics": {k: round(v, 4) if isinstance(v, float) else v for k, v in res.metrics.items()}}
    if t.empty:
        out["summary"] = "No trades in the development window — entry conditions are too strict."
        return out

    vol_rank, trend, adx_ = [], [], []
    for _, row in t.iterrows():
        fc = FeatureCache(data[row.symbol])
        loc = fc.df.index.get_indexer([row.entry_date])[0] - 1  # signal bar
        loc = max(loc, 0)
        vol_rank.append(fc.get("atr_pct_rank", 252).iloc[loc])
        trend.append(fc.df.close.iloc[loc] > fc.get("ema", 200).iloc[loc])
        adx_.append(fc.get("adx", 14).iloc[loc])
    t["vol_regime"] = pd.cut(pd.Series(vol_rank, index=t.index), [-0.01, 0.3, 0.7, 1.01], labels=["low", "mid", "high"])
    t["trend_regime"] = ["uptrend" if x else "downtrend" for x in trend]
    t["adx_regime"] = ["trending" if x >= 20 else "ranging" for x in pd.Series(adx_).fillna(0)]

    def bucket(col):
        g = t.groupby(col, observed=True)
        return {str(k): {"trades": int(len(v)), "pnl": round(float(v.pnl.sum()), 2),
                         "win_rate": round(float((v.pnl > 0).mean()), 3)} for k, v in g}

    t["year"] = pd.to_datetime(t.exit_date).dt.year
    by_year = {int(k): round(float(v), 2) for k, v in t.groupby("year").pnl.sum().items()}
    exits = t.exit_reason.value_counts(normalize=True).round(3).to_dict()
    out.update({
        "by_volatility": bucket("vol_regime"), "by_trend": bucket("trend_regime"), "by_adx": bucket("adx_regime"),
        "exit_reasons": exits, "pnl_by_year": by_year,
        "by_symbol": {k: round(float(v), 2) for k, v in t.groupby("symbol").pnl.sum().items()},
    })
    worst = min(out["by_volatility"].items(), key=lambda kv: kv[1]["pnl"])
    lines = [f"Dev Sharpe {res.metrics['sharpe']:.2f}, max DD {res.metrics['max_drawdown']:.1%}, "
             f"{res.metrics['n_trades']} trades, win rate {res.metrics['win_rate']:.0%}.",
             f"Worst volatility regime: {worst[0]} (P&L {worst[1]['pnl']:,.0f}).",
             f"Exit mix: {exits}."]
    out["summary"] = " ".join(lines)
    return out
