"""Strategy spec: a small, typed JSON language the AI edits instead of writing code.

Example:
{
  "name": "trend_pullback", "version": "1.0", "direction": "long",
  "params": {"ema_trend": 200, "ema_entry": 50, "rsi_min": 50,
             "atr_period": 14, "stop_atr": 2.0, "target_atr": 4.0, "max_hold_bars": 20},
  "entry": {"all": [
      {"left": "close", "op": ">", "right": "ema(ema_trend)"},
      {"left": "low", "op": "<=", "right": "ema(ema_entry)"},
      {"left": "rsi(14)", "op": ">", "right": "param(rsi_min)"}]},
  "exit": {"any": [{"left": "close", "op": "<", "right": "ema(ema_trend)"}]}
}

Terms: a number | a column (open/high/low/close/volume) | param(name) | indicator(period)
where period is an int literal or a param name. Ops: > >= < <= crosses_above crosses_below.
Optional "scale" on a condition multiplies the right-hand side.
Signals are computed at the bar close and executed at the NEXT bar's open by the engine.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

import numpy as np
import pandas as pd

from .features import INDICATORS, FeatureCache, atr

COLUMNS = {"open", "high", "low", "close", "volume"}
OPS = {">", ">=", "<", "<=", "crosses_above", "crosses_below"}
TOP_KEYS = {"name", "version", "direction", "params", "entry", "exit", "description", "parent"}
TERM_RE = re.compile(r"^([a-z_]+)\(([A-Za-z0-9_.]+)\)$")
RESERVED = {  # name: (min, max, type, default)
    "atr_period": (2, 100, int, 14),
    "stop_atr": (0.5, 10.0, float, 2.0),
    "target_atr": (0.5, 20.0, float, 4.0),
    "max_hold_bars": (1, 250, int, 20),
}
PERIOD_MIN, PERIOD_MAX = 2, 400
MAX_ENTRY_CONDITIONS, MAX_EXIT_CONDITIONS, MAX_PARAMS = 8, 6, 16


class SpecError(ValueError):
    pass


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and np.isfinite(x)


def parse_term(term, params: dict):
    """Return ('num', v) | ('col', name) | ('ind', name, period). Raises SpecError."""
    if _num(term):
        return ("num", float(term))
    if not isinstance(term, str):
        raise SpecError(f"bad term {term!r}")
    t = term.strip()
    if t in COLUMNS:
        return ("col", t)
    m = TERM_RE.match(t)
    if not m:
        raise SpecError(f"unparseable term {term!r}")
    fn, arg = m.groups()
    if fn == "param":
        if arg not in params:
            raise SpecError(f"unknown param {arg!r}")
        return ("num", float(params[arg]))
    if fn not in INDICATORS:
        raise SpecError(f"unknown indicator {fn!r}; allowed: {sorted(INDICATORS)}")
    if arg.isdigit():
        p = int(arg)
    elif arg in params:
        v = params[arg]
        if not _num(v) or float(v) != int(v):
            raise SpecError(f"param {arg!r} used as a period must be an integer")
        p = int(v)
    else:
        raise SpecError(f"bad period {arg!r} in {term!r}")
    if not PERIOD_MIN <= p <= PERIOD_MAX:
        raise SpecError(f"period {p} out of range [{PERIOD_MIN},{PERIOD_MAX}]")
    return ("ind", fn, p)


def validate_spec(spec: dict) -> dict:
    """Validate and normalise a spec. Returns a deep copy with reserved params filled."""
    if not isinstance(spec, dict):
        raise SpecError("spec must be an object")
    s = copy.deepcopy(spec)
    extra = set(s) - TOP_KEYS
    if extra:
        raise SpecError(f"unknown top-level keys {sorted(extra)} (risk limits cannot be set from a spec)")
    if s.get("direction") not in ("long", "short"):
        raise SpecError("direction must be 'long' or 'short'")
    s.setdefault("name", "unnamed")
    s.setdefault("version", "1.0")
    params = s.setdefault("params", {})
    if not isinstance(params, dict) or len(params) > MAX_PARAMS:
        raise SpecError(f"params must be an object with at most {MAX_PARAMS} entries")
    for k, v in params.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", k) or not _num(v):
            raise SpecError(f"bad param {k}={v!r}")
    for k, (lo, hi, typ, default) in RESERVED.items():
        v = params.setdefault(k, default)
        if not lo <= v <= hi:
            raise SpecError(f"{k}={v} out of range [{lo},{hi}]")
        params[k] = typ(v)
    for block, key, limit, required in (("entry", "all", MAX_ENTRY_CONDITIONS, True),
                                        ("exit", "any", MAX_EXIT_CONDITIONS, False)):
        b = s.get(block)
        if b is None and not required:
            s[block] = {key: []}
            continue
        if not isinstance(b, dict) or set(b) != {key} or not isinstance(b[key], list):
            raise SpecError(f"{block} must be {{'{key}': [conditions]}}")
        conds = b[key]
        if required and not conds:
            raise SpecError(f"{block} needs at least one condition")
        if len(conds) > limit:
            raise SpecError(f"{block} has {len(conds)} conditions; max {limit} (complexity cap vs overfitting)")
        for c in conds:
            if not isinstance(c, dict) or not {"left", "op", "right"} <= set(c) or set(c) - {"left", "op", "right", "scale"}:
                raise SpecError(f"bad condition {c!r}")
            if c["op"] not in OPS:
                raise SpecError(f"bad op {c['op']!r}")
            parse_term(c["left"], params)
            parse_term(c["right"], params)
            if "scale" in c and not (_num(c["scale"]) and 0.1 <= c["scale"] <= 10):
                raise SpecError("scale must be a number in [0.1, 10]")
    return s


def spec_hash(spec: dict) -> str:
    core = {k: spec.get(k) for k in ("direction", "params", "entry", "exit")}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()[:12]


def _eval(fc: FeatureCache, parsed, index):
    if parsed[0] == "num":
        return pd.Series(parsed[1], index=index)
    if parsed[0] == "col":
        return fc.df[parsed[1]]
    return fc.get(parsed[1], parsed[2])


def _cond(fc: FeatureCache, c: dict, params: dict) -> pd.Series:
    idx = fc.df.index
    left = _eval(fc, parse_term(c["left"], params), idx)
    right = _eval(fc, parse_term(c["right"], params), idx) * c.get("scale", 1.0)
    op = c["op"]
    if op == ">":
        r = left > right
    elif op == ">=":
        r = left >= right
    elif op == "<":
        r = left < right
    elif op == "<=":
        r = left <= right
    elif op == "crosses_above":
        r = (left > right) & (left.shift() <= right.shift())
    else:
        r = (left < right) & (left.shift() >= right.shift())
    return r.fillna(False).astype(bool)


def compute_signals(spec: dict, df: pd.DataFrame):
    """Return (entry, exit, atr) Series aligned to df. Signals are known at that bar's close."""
    spec = validate_spec(spec)
    p = spec["params"]
    fc = FeatureCache(df)
    entry = pd.Series(True, index=df.index)
    for c in spec["entry"]["all"]:
        entry &= _cond(fc, c, p)
    exit_ = pd.Series(False, index=df.index)
    for c in spec["exit"]["any"]:
        exit_ |= _cond(fc, c, p)
    return entry, exit_, atr(df, p["atr_period"])


def describe(spec: dict) -> str:
    def side(conds, joiner):
        return f" {joiner} ".join(f"{c['left']} {c['op']} {c['right']}" + (f" x{c['scale']}" if "scale" in c else "")
                                  for c in conds) or "none"
    p = spec["params"]
    return (f"{spec['direction'].upper()} when {side(spec['entry']['all'], 'AND')}; "
            f"exit when {side(spec['exit']['any'], 'OR')}; stop {p['stop_atr']}xATR, "
            f"target {p['target_atr']}xATR, max hold {p['max_hold_bars']} bars")
