"""Risk engine: the last gate between a signal and the broker. Independent of the AI loop."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from .config import resolve


@dataclass
class RiskDecision:
    approved: bool
    qty: int
    reasons: list = field(default_factory=list)


class RiskEngine:
    def __init__(self, state_dir: str, limits_path: str = "config/risk_limits.yaml", scope: str | None = None):
        p = resolve(limits_path)
        raw = p.read_bytes()
        self.limits = yaml.safe_load(raw)
        self.limits_sha = hashlib.sha256(raw).hexdigest()[:12]
        self.state_dir = Path(state_dir)
        self.kill_file = self.state_dir / "KILL"
        # each broker account (paper, live) tracks its own daily loss / drawdown; the kill switch is shared
        self.state_file = self.state_dir / (f"risk_state_{scope}.json" if scope else "risk_state.json")
        self.state = json.loads(self.state_file.read_text(encoding="utf-8")) if self.state_file.exists() else {}

    # state ---------------------------------------------------------------
    def _save(self):
        self.state_file.write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    @property
    def killed(self) -> bool:
        return self.kill_file.exists()

    def kill(self, reason: str):
        self.kill_file.write_text(reason, encoding="utf-8")

    def unkill(self):
        self.kill_file.unlink(missing_ok=True)
        self.state.update(halted=False, halt_reason="", consecutive_errors=0)
        self._save()

    def update_equity(self, equity: float, today: str | None = None) -> list[str]:
        """Call once per run with current account equity. Returns any newly tripped halts."""
        today = today or str(date.today())
        s, L, tripped = self.state, self.limits, []
        if s.get("day") != today:
            s.update(day=today, day_start_equity=equity, orders_today=0, day_halted=False)
        s["peak_equity"] = max(s.get("peak_equity", equity), equity)
        day_loss = 1 - equity / s["day_start_equity"]
        dd = 1 - equity / s["peak_equity"]
        if day_loss * 100 >= L["daily_loss_limit_pct"] and not s.get("day_halted"):
            s["day_halted"] = True
            tripped.append(f"daily loss {day_loss:.1%} >= {L['daily_loss_limit_pct']}% — entries halted for today")
        if dd * 100 >= L["max_drawdown_halt_pct"] and not s.get("halted"):
            s.update(halted=True, halt_reason=f"drawdown {dd:.1%}")
            tripped.append(f"drawdown {dd:.1%} >= {L['max_drawdown_halt_pct']}% — trading halted until `unkill`")
        s["last_equity"] = equity
        self._save()
        return tripped

    def record_result(self, ok: bool) -> str | None:
        s = self.state
        s["consecutive_errors"] = 0 if ok else s.get("consecutive_errors", 0) + 1
        if ok:
            s["orders_today"] = s.get("orders_today", 0) + 1
        msg = None
        if s["consecutive_errors"] >= self.limits["max_consecutive_errors"]:
            s.update(halted=True, halt_reason="circuit breaker: consecutive broker errors")
            msg = s["halt_reason"]
        self._save()
        return msg

    # checks --------------------------------------------------------------
    def check_entry(self, *, symbol: str, qty: int, price: float, stop: float, equity: float,
                    positions: dict, live: bool, market_open: bool = True, spread_bps: float | None = None,
                    gross_exposure: float = 0.0) -> RiskDecision:
        L, s, why = self.limits, self.state, []
        if self.killed:
            return RiskDecision(False, 0, ["kill switch engaged"])
        if s.get("halted"):
            return RiskDecision(False, 0, [f"halted: {s.get('halt_reason')}"])
        if s.get("day_halted"):
            return RiskDecision(False, 0, ["daily loss limit hit"])
        if live and not L["live_trading_enabled"]:
            return RiskDecision(False, 0, ["live_trading_enabled is false in risk_limits.yaml"])
        if live and L["require_market_hours"] and not market_open:
            return RiskDecision(False, 0, ["market closed"])
        if s.get("orders_today", 0) >= L["max_orders_per_day"]:
            return RiskDecision(False, 0, ["max orders per day reached"])
        if len(positions) >= L["max_open_positions"]:
            return RiskDecision(False, 0, ["max open positions"])
        if symbol in positions:
            return RiskDecision(False, 0, ["already holding this symbol"])
        if price < L["min_price"]:
            return RiskDecision(False, 0, [f"price {price:.2f} below min {L['min_price']}"])
        if spread_bps is not None and spread_bps > L["max_spread_bps"]:
            return RiskDecision(False, 0, [f"spread {spread_bps:.0f}bps > {L['max_spread_bps']}"])
        per_share_risk = abs(price - stop)
        if per_share_risk <= 0:
            return RiskDecision(False, 0, ["stop must differ from entry"])
        caps = {
            "risk_per_trade": math.floor(equity * L["max_risk_per_trade_pct"] / 100 / per_share_risk),
            "position_size": math.floor(equity * L["max_position_pct"] / 100 / price),
            "gross_exposure": math.floor(max(equity * L["max_gross_exposure_pct"] / 100 - gross_exposure, 0) / price),
        }
        final = min([qty] + list(caps.values()))
        for k, v in caps.items():
            if v < qty:
                why.append(f"qty clipped by {k} ({qty}->{v})")
        if final < 1:
            return RiskDecision(False, 0, why or ["size rounds to zero"])
        return RiskDecision(True, final, why)

    def check_option(self, *, max_loss_per_contract: float, contracts: int, defined_risk: bool,
                     equity: float, live: bool) -> RiskDecision:
        L, O = self.limits, self.limits["options"]
        if self.killed or self.state.get("halted") or self.state.get("day_halted"):
            return RiskDecision(False, 0, ["trading halted"])
        if live and not L["live_trading_enabled"]:
            return RiskDecision(False, 0, ["live_trading_enabled is false"])
        if O["defined_risk_only"] and not defined_risk:
            return RiskDecision(False, 0, ["undefined-risk option structures are blocked"])
        cap = math.floor(equity * O["max_loss_per_trade_pct"] / 100 / max_loss_per_contract) if max_loss_per_contract > 0 else 0
        n = min(contracts, cap)
        return RiskDecision(n >= 1, max(n, 0), [] if n >= 1 else ["max loss per contract exceeds budget"])
