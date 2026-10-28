"""Research agents propose ONE modified strategy spec per iteration.

ClaudeAgent  — uses the Anthropic API (set ANTHROPIC_API_KEY).
MutationAgent — offline, diagnosis-guided heuristics. Used when no API key is set, and for tests.

Neither agent can touch risk limits, the holdout, or executable code.
"""
from __future__ import annotations

import copy
import json
import os
import random
import re

from ..features import INDICATORS
from ..strategy import RESERVED, SpecError, spec_hash, validate_spec

GRAMMAR = f"""
You edit a JSON trading-strategy spec. You never write code.
Top-level keys allowed: name, version, direction ("long"|"short"), params, entry, exit, description.
params: flat object of numbers (max 16). Reserved params and ranges: {json.dumps({k: v[:2] for k, v in RESERVED.items()})}.
entry: {{"all": [conditions]}} (max 8). exit: {{"any": [conditions]}} (max 6).
condition: {{"left": TERM, "op": OP, "right": TERM, "scale": optional number 0.1-10 multiplying right}}
OP: > >= < <= crosses_above crosses_below
TERM: a number | open|high|low|close|volume | param(NAME) | INDICATOR(PERIOD) where PERIOD is an int 2-400 or an integer param name.
INDICATORs: {json.dumps({k: v[1] for k, v in INDICATORS.items()})}
Signals are evaluated at the bar close and filled at the next open; stops/targets are ATR multiples.
"""

SYSTEM = """You are a disciplined quantitative researcher improving a daily-bar US equity strategy.
Rules:
- Make ONE focused change per proposal, tied to a specific weakness in the diagnosis.
- Prefer removing complexity over adding it. Fewer conditions generalise better.
- Do not repeat a change that the history shows was already rejected.
- Use round, economically sensible parameter values (e.g. 20, 50, 200), not curve-fit decimals.
- Every test you request is counted and penalised by a Deflated Sharpe gate; propose only changes you
  expect to hold up out of sample.
Reply with ONLY a JSON object: {"hypothesis": "...", "change": "...", "spec": {...full spec...}}"""


def _history_lines(history: list[dict]) -> str:
    lines = []
    for h in history or []:
        rep = h.get("report") or {}
        v = rep.get("validation", {})
        lines.append(f"- [{h['status']}] {h.get('hypothesis', '')[:140]} | OOS sharpe {v.get('sharpe', 0):.2f}"
                     f" | failed: {', '.join(h.get('reasons') or []) or '-'}")
    return "\n".join(lines) or "(none yet)"


class ClaudeAgent:
    name = "claude"

    def __init__(self, model: str):
        import anthropic  # optional dependency

        self.client = anthropic.Anthropic()
        self.model = model

    def propose(self, champion: dict, diagnosis: dict, history: list[dict]) -> dict:
        prompt = (f"{GRAMMAR}\nCURRENT CHAMPION SPEC:\n{json.dumps(champion, indent=1)}\n\n"
                  f"DIAGNOSIS (development window only):\n{json.dumps(diagnosis, indent=1, default=str)}\n\n"
                  f"RECENT EXPERIMENTS:\n{_history_lines(history)}\n\nPropose one improvement.")
        messages = [{"role": "user", "content": prompt}]
        last_err = None
        for _ in range(3):
            resp = self.client.messages.create(model=self.model, max_tokens=3000, system=SYSTEM, messages=messages)
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            try:
                m = re.search(r"\{.*\}", text, re.S)
                out = json.loads(m.group(0))
                out["spec"] = validate_spec(out["spec"])
                return {"spec": out["spec"], "hypothesis": f"{out.get('hypothesis', '')} | {out.get('change', '')}"}
            except (AttributeError, KeyError, ValueError, SpecError) as e:
                last_err = e
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": f"That spec was invalid: {e}. Reply with corrected JSON only."}]
        raise SpecError(f"agent failed to produce a valid spec: {last_err}")


class MutationAgent:
    """Heuristic researcher: reads the diagnosis and applies a single targeted change."""
    name = "mutation"

    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)

    def _has(self, spec, ind):
        return any(ind in str(c["left"]) + str(c["right"]) for c in spec["entry"]["all"])

    def propose(self, champion: dict, diagnosis: dict, history: list[dict]) -> dict:
        tried = {h["hash"] for h in history or []}
        ideas = []
        s0 = copy.deepcopy(champion)
        vol = diagnosis.get("by_volatility", {})
        if vol.get("high", {}).get("pnl", 0) < 0 and not self._has(s0, "atr_pct_rank"):
            s = copy.deepcopy(s0)
            s["params"]["vol_rank_max"] = 0.8
            s["entry"]["all"].append({"left": "atr_pct_rank(252)", "op": "<", "right": "param(vol_rank_max)"})
            ideas.append((s, "Losses concentrate in high volatility; skip entries when ATR% is in its top 20% of the year."))
        adx = diagnosis.get("by_adx", {})
        if adx.get("ranging", {}).get("pnl", 0) < 0 and not self._has(s0, "adx("):
            s = copy.deepcopy(s0)
            s["params"]["adx_min"] = 20
            s["entry"]["all"].append({"left": "adx(14)", "op": ">", "right": "param(adx_min)"})
            ideas.append((s, "Ranging-market trades lose; require ADX(14) > 20 for trend strength."))
        exits = diagnosis.get("exit_reasons", {})
        if exits.get("stop", 0) > 0.35:
            s = copy.deepcopy(s0)
            s["params"]["stop_atr"] = round(min(s["params"]["stop_atr"] * 1.25, 10), 2)
            ideas.append((s, f"{exits['stop']:.0%} of exits are stop-outs; widen stop to {s['params']['stop_atr']}xATR."))
        if exits.get("max_hold", 0) > 0.3:
            s = copy.deepcopy(s0)
            s["params"]["target_atr"] = round(max(s["params"]["target_atr"] * 0.8, 0.5), 2)
            ideas.append((s, f"Many trades time out; bring target in to {s['params']['target_atr']}xATR."))
        if len(s0["entry"]["all"]) > 1:
            for i in range(len(s0["entry"]["all"])):
                s = copy.deepcopy(s0)
                dropped = s["entry"]["all"].pop(i)
                ideas.append((s, f"Simplify: drop entry condition {dropped['left']} {dropped['op']} {dropped['right']}."))
        # No random parameter nudges: those are curve-fitting, burn the trial budget and are
        # already covered by the gate's parameter-robustness check.
        self.rng.shuffle(ideas)
        for s, why in ideas:
            try:
                s = validate_spec(s)
            except SpecError:
                continue
            if spec_hash(s) not in tried:
                return {"spec": s, "hypothesis": why}
        return None  # out of ideas


def make_agent(cfg: dict, seed: int = 0):
    kind = cfg["research"]["agent"]
    if kind == "claude" or (kind == "auto" and os.environ.get("ANTHROPIC_API_KEY")):
        return ClaudeAgent(cfg["research"]["model"])
    return MutationAgent(seed)
