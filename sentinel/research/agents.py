"""Research agents propose ONE candidate strategy spec per iteration.

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
You write a JSON trading-strategy spec. You never write code.
Top-level keys allowed: name, version, direction, params, entry, exit, description, rank_by.
direction: must be "long" (the execution layer trades long only; flat = in cash).
params: flat object of numbers (max 16). Reserved params and ranges: {json.dumps({k: v[:2] for k, v in RESERVED.items()})}.
entry: {{"all": [conditions]}} (max 8, all must be true). exit: {{"any": [conditions]}} (max 6, any one exits).
condition: {{"left": TERM, "op": OP, "right": TERM, "scale": optional number 0.1-10 multiplying right}}
OP: > >= < <= crosses_above crosses_below
TERM: a number | open|high|low|close|volume | param(NAME) | INDICATOR(PERIOD) where PERIOD is an int 2-400 or an integer param name.
INDICATORs: {json.dumps({k: v[1] for k, v in INDICATORS.items()})}
rank_by: optional TERM; when more symbols signal than free slots, the highest value enters first.
Engine: daily bars; signals at the close, fills at the next open; spread+slippage on every fill; stop and target are
ATR multiples set at entry; positions also close on any exit condition or after max_hold_bars. Sizing risks a fixed
% of equity to the stop, capped per position. Cash earns nothing in the backtest.
"""

SYSTEM = """You are a disciplined quantitative researcher looking for a robust, tradable edge in daily-bar data.
Each turn you propose ONE candidate. It may be a focused change to the current champion, or a clearly different,
economically motivated idea (trend, momentum, mean reversion, volatility regime, breakout, ...) if the evidence says
the champion's family is exhausted.
Rules:
- Tie the proposal to the evidence: the diagnosis, the gate results, the benchmark, and what has already failed.
- Prefer simple rules with an economic reason to work. Fewer conditions generalise better.
- Never repeat or trivially re-parameterise something in the experiment history.
- Use round, conventional parameter values (10, 20, 50, 100, 200, 0.5, 2.0), never fitted decimals.
- Every candidate is counted by a Deflated Sharpe gate that gets stricter with each trial, and results are judged
  out of sample. Do not spend trials on tiny tweaks. A candidate must plausibly beat both the champion and
  equal-weight buy & hold on a risk-adjusted basis.
Reply with ONLY a JSON object:
{"hypothesis": "why this should work, citing the evidence", "change": "what is different", "spec": {...full spec...}}"""


def _history_lines(history: list[dict]) -> str:
    from ..strategy import describe

    lines = []
    for h in history or []:
        rep = h.get("report") or {}
        v, b = rep.get("validation", {}), rep.get("benchmark_validation", {})
        try:
            rules = describe(h["spec"])
        except Exception:
            rules = "?"
        lines.append(f"- [{h['status']}] {rules}\n    OOS sharpe {v.get('sharpe', 0):.2f}, CAGR {v.get('cagr', 0):.1%}, "
                     f"maxDD {v.get('max_drawdown', 0):.1%} (buy&hold sharpe {b.get('sharpe', 0):.2f}) | "
                     f"failed: {', '.join(h.get('reasons') or []) or '-'}")
    return "\n".join(lines) or "(none yet)"


def _context_text(ctx: dict) -> str:
    if not ctx:
        return ""
    rep = ctx.get("report") or {}
    v, b = rep.get("validation", {}), rep.get("benchmark_validation", {})
    checks = "\n".join(f"  {'PASS' if c['pass'] else 'FAIL'} {c['name']}: {c['value']} (need {c['threshold']})"
                       for c in rep.get("checks", []))
    return (f"UNIVERSE: {', '.join(ctx.get('universe', []))}\n"
            f"TRIALS SO FAR (each one raises the Deflated Sharpe bar): {ctx.get('n_trials')}\n"
            f"CHAMPION OUT-OF-SAMPLE: sharpe {v.get('sharpe', 0):.2f}, CAGR {v.get('cagr', 0):.1%}, "
            f"maxDD {v.get('max_drawdown', 0):.1%}, trades {v.get('n_trades', 0)}\n"
            f"EQUAL-WEIGHT BUY & HOLD, SAME WINDOW: sharpe {b.get('sharpe', 0):.2f}, CAGR {b.get('cagr', 0):.1%}, "
            f"maxDD {b.get('max_drawdown', 0):.1%}\n"
            f"CHAMPION GATE RESULTS:\n{checks}\n")


class ClaudeAgent:
    name = "claude"

    def __init__(self, model: str):
        import anthropic  # optional dependency

        self.client = anthropic.Anthropic()
        self.model = model
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}

    def propose(self, champion: dict, diagnosis: dict, history: list[dict], context: dict | None = None) -> dict:
        prompt = (f"{GRAMMAR}\n{_context_text(context)}\nCURRENT CHAMPION SPEC:\n{json.dumps(champion, indent=1)}\n\n"
                  f"DIAGNOSIS OF THE CHAMPION (development window only):\n"
                  f"{json.dumps(diagnosis, indent=1, default=str)}\n\n"
                  f"EXPERIMENT HISTORY (newest first, all scored on the current data):\n{_history_lines(history)}\n\n"
                  f"Propose one candidate.")
        messages = [{"role": "user", "content": prompt}]
        last_err = None
        for _ in range(3):
            resp = self.client.messages.create(model=self.model, max_tokens=4000, system=SYSTEM, messages=messages)
            self.usage["calls"] += 1
            u = getattr(resp, "usage", None)
            self.usage["input_tokens"] += getattr(u, "input_tokens", 0) or 0
            self.usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            try:
                m = re.search(r"\{.*\}", text, re.S)
                out = json.loads(m.group(0))
                spec = validate_spec(out["spec"])
                if spec["direction"] != "long":
                    raise SpecError("direction must be long")
                return {"spec": spec, "hypothesis": f"{out.get('hypothesis', '')} | {out.get('change', '')}"}
            except (AttributeError, KeyError, TypeError, ValueError, SpecError) as e:
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

    def propose(self, champion: dict, diagnosis: dict, history: list[dict], context: dict | None = None) -> dict:
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
