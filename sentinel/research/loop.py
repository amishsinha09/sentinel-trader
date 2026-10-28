"""The controlled self-improvement loop:
observe -> diagnose -> hypothesise -> experiment -> validate -> keep or revert -> remember.
"""
from __future__ import annotations

import json

from ..alerts import Alerts
from ..config import resolve
from ..memory import Memory
from ..strategy import SpecError, describe, spec_hash, validate_spec
from ..validation import evaluate_candidate, split_dates
from .diagnose import diagnose


def ensure_baseline(cfg: dict, data: dict, mem: Memory, log=print) -> dict:
    champ = mem.champion("research")
    if champ:
        return champ
    spec = validate_spec(json.loads(resolve(cfg["research"]["baseline_spec"]).read_text(encoding="utf-8")))
    h = spec_hash(spec)
    if not mem.exists(h):
        rep = evaluate_candidate(spec, data, cfg, mem.trial_count() + 1, mem.trial_sr_var())
        failed = [c["name"] for c in rep["checks"] if not c["pass"]]
        mem.record(h, spec, rep, "baseline", "Starting strategy", "human", reasons=failed)
        log(f"baseline {h}: OOS sharpe {rep['validation']['sharpe']:.2f}, gate {'PASS' if rep['passed'] else 'FAIL'} {failed}")
        if rep["passed"] and cfg["research"]["auto_promote_paper"]:
            mem.set_champion("paper", h)
    mem.set_champion("research", h)
    return mem.get(h)


def research(cfg: dict, data: dict, mem: Memory, agent, iterations: int, log=print) -> list[dict]:
    alerts = Alerts(cfg)
    champ = ensure_baseline(cfg, data, mem, log)
    windows = {k: [str(a.date()), str(b.date())] for k, (a, b) in split_dates(data, cfg["splits"]).items()}
    if (champ["report"] or {}).get("windows") != windows or champ["report"].get("universe") != sorted(data):
        log("data changed since the champion was scored — re-evaluating it on the current data")
        rep = evaluate_candidate(champ["spec"], data, cfg, mem.trial_count(), mem.trial_sr_var())
        mem.record(champ["hash"], champ["spec"], rep, champ["status"], champ["hypothesis"], champ["agent"],
                   champ["parent"], [c["name"] for c in rep["checks"] if not c["pass"]])
        champ = mem.get(champ["hash"])
        for role in ("paper", "live"):
            c = mem.champion(role)
            if c and c["hash"] == champ["hash"] and not rep["passed"]:
                msg = (f"{role} strategy {champ['hash']} no longer passes the gate on current data "
                       f"(failed: {', '.join(x['name'] for x in rep['checks'] if not x['pass'])}) — removed from {role}.")
                mem.clear_champion(role, msg)
                alerts.send(msg, "warn")
                log(msg)
    bench = champ["report"].get("benchmark_validation", {}).get("sharpe")
    if bench is not None:
        log(f"champion {champ['hash']}: OOS sharpe {champ['report']['validation']['sharpe']:.2f} | "
            f"equal-weight buy & hold, same window: {bench:.2f}")

    def fresh(e):
        r = (e or {}).get("report") or {}
        return r.get("windows") == windows and r.get("universe") == sorted(data)

    results = []
    for it in range(1, iterations + 1):
        diag = diagnose(champ["spec"], data, cfg)
        history = [e for e in mem.recent(200) if fresh(e)][:20]
        try:
            ctx = {"report": champ["report"], "universe": sorted(data), "n_trials": mem.trial_count()}
            prop = agent.propose(champ["spec"], diag, history, context=ctx)
            if prop is None:
                log(f"[{it}] {agent.name} agent has no untested ideas left. Add ANTHROPIC_API_KEY for the Claude "
                    "researcher, or write a new baseline idea in strategies/.")
                break
            spec = validate_spec(prop["spec"])
        except SpecError as e:
            log(f"[{it}] invalid proposal: {e}")
            mem.event("invalid_proposal", str(e))
            continue
        except Exception as e:  # API key, credit, network or rate-limit problems: stop cleanly
            log(f"[{it}] research agent error, stopping: {type(e).__name__}: {e}")
            mem.event("agent_error", f"{type(e).__name__}: {e}")
            break
        h = spec_hash(spec)
        if mem.exists(h) and fresh(mem.get(h)):
            log(f"[{it}] duplicate of {h}, skipped (not counted)")
            continue
        retest = mem.exists(h)  # tested before on older data: re-score, but it is not a new trial
        n_trials = mem.trial_count() + (0 if retest else 1)
        spec["parent"] = champ["hash"]
        rep = evaluate_candidate(spec, data, cfg, n_trials, mem.trial_sr_var())
        cv, nv = champ["report"]["validation"], rep["validation"]
        min_imp = cfg["validation"]["min_improvement_sharpe"]
        improved = nv["sharpe"] >= cv["sharpe"] + min_imp and nv["max_drawdown"] <= cv["max_drawdown"] * 1.2 + 0.02
        failed = [c["name"] for c in rep["checks"] if not c["pass"]]
        if not improved:
            failed.append(f"not_better_than_champion(+{min_imp} sharpe)")
        status = "promoted" if rep["passed"] and improved else "rejected"
        mem.record(h, spec, rep, status, prop["hypothesis"], agent.name, champ["hash"], failed)
        log(f"[{it}] {h} {status.upper():8} OOS sharpe {nv['sharpe']:.2f} (champ {cv['sharpe']:.2f}) "
            f"DSR {rep['dsr']:.2f}" + (f" | failed: {failed}" if failed else ""))
        log(f"      idea: {prop['hypothesis']}")
        log(f"      rules: {describe(spec)}")
        results.append({"hash": h, "status": status, "hypothesis": prop["hypothesis"], "failed": failed})
        if status == "promoted":
            mem.set_champion("research", h, agent.name)
            if cfg["research"]["auto_promote_paper"]:
                mem.set_champion("paper", h, agent.name)
            alerts.send(f"New strategy promoted to paper: {h} (OOS Sharpe {nv['sharpe']:.2f}). {prop['hypothesis'][:200]}")
            champ = mem.get(h)
    u = getattr(agent, "usage", None)
    if u and u["calls"]:
        log(f"Claude API usage this run: {u['calls']} calls, {u['input_tokens']:,} input + "
            f"{u['output_tokens']:,} output tokens (cost: platform.claude.com > Usage)")
    return results
