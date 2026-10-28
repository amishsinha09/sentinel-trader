"""Indicator library. Every indicator at bar t uses only data up to and including bar t."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, p: int) -> pd.Series:
    return s.ewm(span=p, adjust=False, min_periods=p).mean()


def wilder(s: pd.Series, p: int) -> pd.Series:
    return s.ewm(alpha=1 / p, adjust=False, min_periods=p).mean()


def rsi(close: pd.Series, p: int) -> pd.Series:
    d = close.diff()
    up, dn = wilder(d.clip(lower=0), p), wilder(-d.clip(upper=0), p)
    r = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    return r.mask((dn == 0) & up.notna(), 100.0)


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift()
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, p: int) -> pd.Series:
    return wilder(true_range(df), p)


def adx(df: pd.DataFrame, p: int) -> pd.Series:
    up, dn = df["high"].diff(), -df["low"].diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr = atr(df, p)
    pdi, mdi = 100 * wilder(plus, p) / tr, 100 * wilder(minus, p) / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return wilder(dx, p)


# name -> (function(df, period), description). Period is always an int lookback.
INDICATORS = {
    "ema": (lambda df, p: ema(df["close"], p), "Exponential moving average of close"),
    "sma": (lambda df, p: df["close"].rolling(p).mean(), "Simple moving average of close"),
    "rsi": (lambda df, p: rsi(df["close"], p), "Wilder RSI, 0-100"),
    "atr": (lambda df, p: atr(df, p), "Average true range (price units)"),
    "atr_pct": (lambda df, p: atr(df, p) / df["close"] * 100, "ATR as % of close"),
    "atr_pct_rank": (lambda df, p: (atr(df, 14) / df["close"]).rolling(p).rank(pct=True),
                     "Percentile (0-1) of today's ATR% within the last p bars — volatility regime"),
    "adx": (lambda df, p: adx(df, p), "Average directional index — trend strength, 0-100"),
    "roc": (lambda df, p: df["close"].pct_change(p) * 100, "Rate of change over p bars, %"),
    "highest": (lambda df, p: df["high"].rolling(p).max().shift(1), "Highest high of the PREVIOUS p bars"),
    "lowest": (lambda df, p: df["low"].rolling(p).min().shift(1), "Lowest low of the PREVIOUS p bars"),
    "dist_ema": (lambda df, p: (df["close"] / ema(df["close"], p) - 1) * 100, "Distance of close from EMA(p), %"),
    "vol_z": (lambda df, p: (df["volume"] - df["volume"].rolling(p).mean()) / df["volume"].rolling(p).std(),
              "Volume z-score vs last p bars"),
}


class FeatureCache:
    def __init__(self, df: pd.DataFrame):
        self.df = df
        self._c: dict[tuple, pd.Series] = {}

    def get(self, name: str, p: int) -> pd.Series:
        key = (name, p)
        if key not in self._c:
            self._c[key] = INDICATORS[name][0](self.df, p)
        return self._c[key]
