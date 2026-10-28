"""Market data: synthetic (offline testing), yfinance (free daily bars) or Alpaca (account data feed).

All loaders return {symbol: DataFrame[open, high, low, close, volume]} indexed by a
tz-naive daily DatetimeIndex, split/dividend adjusted where the source supports it.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

COLS = ["open", "high", "low", "close", "volume"]


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=str.lower)[COLS].astype(float)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.dropna(subset=["open", "high", "low", "close"])


def synthetic(symbols, start="2015-01-01", end=None, seed=7) -> dict[str, pd.DataFrame]:
    """Regime-switching price paths (bull / bear-high-vol / chop). For tests and dry runs only."""
    idx = pd.bdate_range(start, end or date.today())
    regimes = {0: (0.0006, 0.009), 1: (-0.0008, 0.022), 2: (0.0000, 0.012)}
    trans = np.array([[0.985, 0.005, 0.010], [0.020, 0.960, 0.020], [0.015, 0.010, 0.975]])
    out = {}
    for k, sym in enumerate(symbols):
        rng = np.random.default_rng(seed + k)
        n = len(idx)
        state, states = 0, np.empty(n, dtype=int)
        for i in range(n):
            states[i] = state
            state = rng.choice(3, p=trans[state])
        mu = np.array([regimes[s][0] for s in states]) * (1 + 0.3 * rng.standard_normal())
        sig = np.array([regimes[s][1] for s in states]) * (0.8 + 0.4 * rng.random())
        ret = mu + sig * rng.standard_normal(n)
        close = 50 * (1 + k * 0.4) * np.exp(np.cumsum(ret))
        prev = np.concatenate([[close[0]], close[:-1]])
        open_ = prev * np.exp(sig * 0.3 * rng.standard_normal(n))
        hi = np.maximum(open_, close) * np.exp(np.abs(rng.standard_normal(n)) * sig * 0.5)
        lo = np.minimum(open_, close) * np.exp(-np.abs(rng.standard_normal(n)) * sig * 0.5)
        vol = 1e6 * np.exp(0.3 * rng.standard_normal(n) + 15 * np.abs(ret))
        out[sym] = pd.DataFrame({"open": open_, "high": hi, "low": lo, "close": close, "volume": vol}, index=idx)
    return out


def load_yfinance(symbols, start) -> dict[str, pd.DataFrame]:
    import yfinance as yf  # optional dependency

    out = {}
    for sym in symbols:
        df = yf.download(sym, start=start, auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if len(df):
            out[sym] = _clean(df)
    return out


def load_alpaca(symbols, start) -> dict[str, pd.DataFrame]:
    from alpaca.data.historical import StockHistoricalDataClient  # optional dependency
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
    req = StockBarsRequest(symbol_or_symbols=list(symbols), timeframe=TimeFrame.Day,
                           start=pd.Timestamp(start).to_pydatetime(), adjustment="all")
    bars = client.get_stock_bars(req).df
    out = {}
    for sym in symbols:
        if sym in bars.index.get_level_values(0):
            out[sym] = _clean(bars.xs(sym, level=0))
    return out


def load_data(cfg: dict, refresh: bool = False) -> dict[str, pd.DataFrame]:
    src, start = cfg["data"]["source"], cfg["data"]["start"]
    symbols = cfg["universe"]
    if src == "synthetic":
        return synthetic(symbols, start)
    cache = Path(cfg["data"]["cache_dir"])
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / f"{src}_{start}.pkl"
    if f.exists() and not refresh:
        data = pd.read_pickle(f)
        if set(symbols) <= set(data):
            return {s: data[s] for s in symbols}
    loader = {"yfinance": load_yfinance, "alpaca": load_alpaca}[src]
    data = loader(symbols, start)
    if not data:
        raise RuntimeError(f"No data returned from {src}")
    pd.to_pickle(data, f)
    return data


def last_bar_age_days(data: dict[str, pd.DataFrame], today=None) -> int:
    today = pd.Timestamp(today or date.today()).normalize()
    last = max(df.index[-1] for df in data.values())
    return int(np.busday_count(last.date(), today.date()))
