"""Configuration loading. settings.yaml is tunable; risk_limits.yaml is the hard-limit file."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "universe": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL"],
    "data": {"source": "synthetic", "start": "2015-01-01", "cache_dir": "state/data", "max_staleness_days": 4},
    "backtest": {
        "initial_capital": 100000.0,
        "commission_per_share": 0.0,
        "spread_bps": 2.0,
        "slippage_bps": 3.0,
        "max_positions": 5,
        "risk_per_trade": 0.01,
        "max_position_pct": 0.20,
        "max_gross_exposure": 1.0,
    },
    "splits": {"dev": 0.6, "validation": 0.2, "holdout": 0.2},
    "validation": {
        "min_trades_dev": 40,
        "min_trades_oos": 20,
        "min_oos_sharpe": 0.5,
        "max_oos_drawdown": 0.25,
        "min_oos_is_ratio": 0.3,
        "walk_forward_windows": 5,
        "min_wf_positive_frac": 0.6,
        "sensitivity_pct": 0.2,
        "min_sensitivity_frac": 0.7,
        "cost_stress_mult": 2.0,
        "min_dsr": 0.90,
        "min_improvement_sharpe": 0.10,
    },
    "research": {"agent": "auto", "model": "claude-sonnet-5-5", "iterations": 10,
                 "auto_promote_paper": True, "baseline_spec": "strategies/baseline_trend_pullback.json"},
    "execution": {"broker": "paper", "role": "paper", "options_enabled": False},
    "alerts": {"webhook_url_env": "ALERT_WEBHOOK_URL"},
    "state_dir": "state",
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader (KEY=value lines). Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_settings(path: str | os.PathLike | None = None) -> dict:
    load_dotenv()
    path =Path(path or os.environ.get("SENTINEL_SETTINGS", ROOT / "config" / "settings.yaml"))
    user = {}
    if path.exists():
        user = yaml.safe_load(path.read_text()) or {}
    cfg = _merge(DEFAULTS, user)
    state = Path(cfg["state_dir"])
    if not state.is_absolute():
        state = ROOT / state
    state.mkdir(parents=True, exist_ok=True)
    cfg["state_dir"] = str(state)
    cache = Path(cfg["data"]["cache_dir"])
    cfg["data"]["cache_dir"] = str(cache if cache.is_absolute() else ROOT / cache)
    return cfg


def resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p
