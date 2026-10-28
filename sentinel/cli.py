"""Command line: python -m sentinel <command>"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_settings, resolve


def _ctx(args):
    from .data import load_data
    from .memory import Memory
    from .risk import RiskEngine

    cfg = load_settings(args.settings)
    if getattr(args, "source", None):
        cfg["data"]["source"] = args.source
    return cfg, Memory(cfg["state_dir"]), RiskEngine(cfg["state_dir"]), (lambda refresh=False: load_data(cfg, refresh))


def _spec_from(args, mem, cfg):
    if getattr(args, "spec", None):
        return json.loads(resolve(args.spec).read_text(encoding="utf-8"))
    c = mem.champion(getattr(args, "role", "paper")) or mem.champion("research")
    if c:
        return c["spec"]
    return json.loads(resolve(cfg["research"]["baseline_spec"]).read_text(encoding="utf-8"))


def cmd_backtest(args):
    from .backtest import run_backtest
    from .strategy import describe
    from .validation import split_dates

    cfg, mem, _, load = _ctx(args)
    data, spec = load(), None
    spec = _spec_from(args, mem, cfg)
    w = split_dates(data, cfg["splits"])
    print(describe(spec))
    for name in ("dev", "validation"):
        m = run_backtest(spec, data, cfg["backtest"], *w[name]).metrics
        print(f"{name:11} sharpe {m['sharpe']:5.2f}  cagr {m['cagr']:6.1%}  maxDD {m['max_drawdown']:5.1%}  "
              f"trades {m['n_trades']:4d}  win {m['win_rate']:.0%}  PF {m['profit_factor']:.2f}")
    print("(holdout is reserved for live promotion)")


def cmd_validate(args):
    from .strategy import spec_hash, validate_spec
    from .validation import evaluate_candidate

    cfg, mem, _, load = _ctx(args)
    spec = validate_spec(_spec_from(args, mem, cfg))
    rep = evaluate_candidate(spec, load(), cfg, mem.trial_count() + 1, mem.trial_sr_var())
    if not mem.exists(spec_hash(spec)):
        failed = [c["name"] for c in rep["checks"] if not c["pass"]]
        mem.record(spec_hash(spec), spec, rep, "manual_validate", "manual validation", "human", reasons=failed)
    _print_gate(rep)


def _print_gate(rep):
    for c in rep["checks"]:
        print(f"  {'PASS' if c['pass'] else 'FAIL'}  {c['name']:26} {c['value']}  (need {c['threshold']})")
    v, b = rep["validation"], rep["benchmark_validation"]
    print(f"  info  strategy (OOS):   sharpe {v['sharpe']:.2f}, maxDD {v['max_drawdown']:.1%}, CAGR {v['cagr']:.1%}")
    print(f"  info  buy & hold (OOS): sharpe {b['sharpe']:.2f}, maxDD {b['max_drawdown']:.1%}, CAGR {b['cagr']:.1%}")
    print("GATE:", "PASSED" if rep["passed"] else "FAILED")


def cmd_baseline(args):
    """Start research from a new idea. It is scored like any candidate and counts as a trial."""
    from .strategy import describe, spec_hash, validate_spec
    from .validation import evaluate_candidate

    cfg, mem, _, load = _ctx(args)
    spec = validate_spec(json.loads(resolve(args.spec).read_text(encoding="utf-8")))
    h = spec_hash(spec)
    rep = evaluate_candidate(spec, load(), cfg, mem.trial_count() + (0 if mem.exists(h) else 1), mem.trial_sr_var())
    failed = [c["name"] for c in rep["checks"] if not c["pass"]]
    mem.record(h, spec, rep, "baseline", f"New starting idea: {spec['name']}", "human", reasons=failed)
    mem.set_champion("research", h, "human")
    print(f"research now starts from {h}: {describe(spec)}")
    _print_gate(rep)
    if rep["passed"] and cfg["research"]["auto_promote_paper"]:
        mem.set_champion("paper", h, "human")
        print(f"passed the gate -> paper strategy is now {h}")


def cmd_research(args):
    from .research.agents import make_agent
    from .research.loop import research

    cfg, mem, _, load = _ctx(args)
    agent = make_agent(cfg, seed=mem.trial_count())
    print(f"agent: {agent.name}; trials so far: {mem.trial_count()}")
    research(cfg, load(), mem, agent, args.iterations or cfg["research"]["iterations"])


def cmd_status(args):
    cfg, mem, risk, _ = _ctx(args)
    for role in ("research", "paper", "live"):
        c = mem.champion(role)
        print(f"{role:9} {c['hash'] if c else '-'}  {(c or {}).get('hypothesis', '')[:80]}")
    print(f"trials: {mem.trial_count()}  holdout uses: {mem.holdout_uses()}  risk file sha: {risk.limits_sha}")
    print(f"kill switch: {'ON' if risk.killed else 'off'}  risk state: {json.dumps(risk.state)}")
    print(f"live_trading_enabled: {risk.limits['live_trading_enabled']}")


def cmd_promote(args):
    from .validation import holdout_check

    cfg, mem, risk, load = _ctx(args)
    e = mem.get(args.hash)
    if not e:
        sys.exit(f"unknown strategy {args.hash}")
    if args.to == "paper":
        if not e["report"] or not e["report"]["passed"]:
            sys.exit("refused: this strategy did not pass the validation gate")
        mem.set_champion("paper", args.hash, "human")
        print(f"paper champion -> {args.hash}")
        return
    # live
    if not e["report"] or not e["report"]["passed"]:
        sys.exit("refused: this strategy did not pass the validation gate")
    if mem.champion("paper") is None or mem.champion("paper")["hash"] != args.hash:
        sys.exit("refused: promote to paper first and paper-trade it before going live")
    print("This runs the ONE-TIME holdout test on data the research loop has never seen.")
    print("Every holdout use is logged; repeated peeking defeats its purpose.")
    rep = holdout_check(e["spec"], load(), cfg, e["report"]["validation"]["sharpe"])
    mem.log_holdout(args.hash, rep)
    m = rep["metrics"]
    print(f"holdout {rep['window']}: sharpe {m['sharpe']:.2f}, maxDD {m['max_drawdown']:.1%}, trades {m['n_trades']}")
    for c in rep["checks"]:
        print(f"  {'PASS' if c['pass'] else 'FAIL'}  {c['name']}")
    if not rep["passed"]:
        sys.exit("refused: holdout test failed. Keep paper trading or research further.")
    if not risk.limits["live_trading_enabled"]:
        print("note: live_trading_enabled is false in config/risk_limits.yaml — live orders stay blocked until you change it.")
    typed = input(f"Type the strategy hash ({args.hash}) to approve it for REAL MONEY: ").strip()
    if typed != args.hash:
        sys.exit("not confirmed")
    mem.set_champion("live", args.hash, "human")
    print(f"live champion -> {args.hash}")


def cmd_run(args):
    from .brokers import make_broker
    from .execution import run_once

    cfg, mem, risk, load = _ctx(args)
    if args.broker:
        cfg["execution"]["broker"] = args.broker
    broker = make_broker(cfg)
    if broker.live:
        print("*** LIVE BROKER — real money ***")
    run_once(cfg, broker, mem, risk, load(refresh=True), dry_run=args.dry_run)


def cmd_kill(args):
    _, mem, risk, _ = _ctx(args)
    risk.kill(args.reason or "manual")
    mem.event("kill", args.reason or "manual")
    print("KILL SWITCH ON — no new entries. Exits still allowed. Close positions at your broker if needed.")


def cmd_unkill(args):
    _, mem, risk, _ = _ctx(args)
    risk.unkill()
    mem.event("unkill", "manual reset")
    print("kill switch off, halts cleared")


def cmd_pine(args):
    from .pine import to_pine
    from .strategy import spec_hash

    cfg, mem, _, _ = _ctx(args)
    spec = _spec_from(args, mem, cfg)
    out = Path(args.out or f"sentinel_{spec_hash(spec)}.pine")
    out.write_text(to_pine(spec, cfg["backtest"], spec_hash(spec)), encoding="utf-8")
    print(f"wrote {out}")


def cmd_report(args):
    from .report import build_report

    cfg, mem, risk, load = _ctx(args)
    p = build_report(cfg, load(), mem, risk, args.out or Path(cfg["state_dir"]) / "report.html")
    print(f"wrote {p}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sentinel", description="Controlled self-improving trading system")
    ap.add_argument("--settings", help="path to settings.yaml")
    ap.add_argument("--source", choices=["synthetic", "yfinance", "alpaca"], help="override data source")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(fn=fn)
        return p

    p = add("backtest", cmd_backtest, "backtest a spec (default: current champion)")
    p.add_argument("--spec"); p.add_argument("--role", default="paper")
    p = add("validate", cmd_validate, "run the full validation gate on a spec")
    p.add_argument("--spec"); p.add_argument("--role", default="paper")
    p = add("baseline", cmd_baseline, "start research from a new strategy idea (counts as a trial)")
    p.add_argument("--spec", required=True)
    p = add("research", cmd_research, "run the AI self-improvement loop")
    p.add_argument("--iterations", type=int)
    add("status", cmd_status, "champions, trials, risk state")
    p = add("promote", cmd_promote, "promote a strategy (live requires holdout test + typed approval)")
    p.add_argument("hash"); p.add_argument("--to", choices=["paper", "live"], required=True)
    p = add("run-once", cmd_run, "one trading cycle (schedule daily after the open)")
    p.add_argument("--broker", choices=["paper", "alpaca_paper", "alpaca_live", "ticket"])
    p.add_argument("--dry-run", action="store_true")
    p = add("kill", cmd_kill, "engage kill switch"); p.add_argument("--reason")
    add("unkill", cmd_unkill, "release kill switch and clear halts")
    p = add("pine", cmd_pine, "export a strategy to TradingView Pine Script v5")
    p.add_argument("--spec"); p.add_argument("--role", default="paper"); p.add_argument("--out")
    p = add("report", cmd_report, "write an HTML status report"); p.add_argument("--out")

    args = ap.parse_args(argv)
    args.fn(args)
