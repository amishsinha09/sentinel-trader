"""HTML dashboard written for non-specialists: a verdict, a $10,000 comparison, the rules in plain
English, each safety check explained, and every idea tried. Technical detail is collapsed at the end."""
from __future__ import annotations

import html
import json
from pathlib import Path

from . import plain, trading_view
from .backtest import buy_and_hold, run_backtest
from .validation import split_dates

E = html.escape


def _money(x: float) -> str:
    return f"${x:,.0f}"


def _chart(strat, bench, w=760, h=260) -> str:
    """Growth of $10,000: strategy vs buy & hold, with axis labels and a legend."""
    s = strat / strat.iloc[0] * 10000
    b = bench.reindex(s.index).ffill() / bench.iloc[0] * 10000
    lo, hi = min(s.min(), b.min()), max(s.max(), b.max())
    pad_l, pad_r, pad_t, pad_b = 70, 16, 14, 28
    iw, ih = w - pad_l - pad_r, h - pad_t - pad_b
    n = len(s)

    def pts(series):
        return " ".join(f"{pad_l + i / (n - 1) * iw:.1f},{pad_t + ih - (v - lo) / (hi - lo or 1) * ih:.1f}"
                        for i, v in enumerate(series.values))

    grid, ticks = [], 4
    for k in range(ticks + 1):
        v = lo + (hi - lo) * k / ticks
        y = pad_t + ih - k / ticks * ih
        grid.append(f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                    f'<text x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end" class="axis">{_money(v)}</text>')
    years = sorted({d.year for d in s.index})
    step = max(1, len(years) // 6)
    for yr in years[::step]:
        i = s.index.searchsorted(f"{yr}-01-01")
        x = pad_l + i / (n - 1) * iw
        grid.append(f'<text x="{x:.1f}" y="{h - 8}" text-anchor="middle" class="axis">{yr}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Growth of $10,000">{"".join(grid)}'
            f'<polyline fill="none" class="l-bench" stroke-width="2" points="{pts(b)}"/>'
            f'<polyline fill="none" class="l-strat" stroke-width="2.2" points="{pts(s)}"/></svg>'
            f'<div class="legend"><span><i class="sw strat"></i>This strategy: {_money(s.iloc[-1])}</span>'
            f'<span><i class="sw bench"></i>Just holding the same investments: {_money(b.iloc[-1])}</span></div>')


def _setup_name(universe: list[str]) -> str:
    etfs = {"XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "IWM", "EFA", "EEM", "TLT", "IEF", "GLD"}
    kind = "ETFs (stock sectors, small caps, international, bonds, gold)" if set(universe) <= etfs | {"SPY", "QQQ"} \
        and len(set(universe) & etfs) > len(universe) / 2 else "US stocks"
    return f"{len(universe)} {kind}"


def _verdict(mem, roles, holdouts, current) -> tuple[str, str, str]:
    if roles["live"]:
        return "live", "LIVE — trading real money", f"Strategy {roles['live']['hash']} is approved for real money."
    if roles["paper"]:
        return "paper", "PRACTICE MODE — paper trading only", \
            f"Strategy {roles['paper']['hash']} passed every safety check and may trade with play money. Not approved for real money."
    msg = "No strategy has passed all the safety checks, so the bot will not place any trades."
    if any(not r["passed"] for r in holdouts):
        msg += " The best candidate also failed its final exam on years it had never seen."
    return "stop", "NOT READY TO TRADE", msg


def build_report(cfg, data, mem, risk, out_path, recent: dict | None = None) -> Path:
    w = split_dates(data, cfg["splits"])
    win = {k: [str(a.date()), str(b.date())] for k, (a, b) in w.items()}
    roles = {r: mem.champion(r) for r in ("research", "paper", "live")}
    current = roles["live"] or roles["paper"] or roles["research"]
    holdouts = [dict(r) for r in mem.db.execute("SELECT ts, hash, passed, report FROM holdout_log ORDER BY ts")]
    for r in holdouts:
        r["report"] = json.loads(r["report"])
    universe = sorted(data)
    last_day = max(df.index[-1] for df in data.values()).date()
    cls, title, msg = _verdict(mem, roles, holdouts, current)

    parts = [f"""<header><p class="eyebrow">Sentinel Trader · {E(_setup_name(universe))}</p>
<h1>Strategy report</h1><p class="sub">Market data through {last_day} · {mem.trial_count()} idea{'s' if mem.trial_count() != 1 else ''} tested so far</p></header>
<section class="verdict {cls}"><p class="vlabel">Verdict</p><h2>{E(title)}</h2><p>{E(msg)}</p></section>"""]
    if current:
        try:
            parts += trading_view.sections(cfg, mem, current["spec"], recent or data,
                                           approved=bool(roles["paper"] or roles["live"]),
                                           broker_name=cfg["execution"]["broker"])
        except Exception as e:  # the research report must still render if fresh prices are unavailable
            parts.append(f"<section><p class='k'>Trading view unavailable: {E(str(e))}</p></section>")
    parts.append("<h2 class='divider'>Research results</h2>")

    if current and current.get("report"):
        rep = current["report"]
        v, b = rep["validation"], rep.get("benchmark_validation", {})
        ts, te = rep["windows"]["validation"]
        sv, bv = 10000 * (1 + v["total_return"]), 10000 * (1 + b.get("total_return", 0))
        if v["cagr"] >= b.get("cagr", 0):
            line = "This strategy made at least as much as simply holding the same investments."
        else:
            line = (f"Simply holding the same investments made more money "
                    f"({b.get('cagr', 0):.1%} a year vs {v['cagr']:.1%}).")
        if v["max_drawdown"] < b.get("max_drawdown", 1) * 0.7:
            line += f" The strategy's worst drop was smaller ({v['max_drawdown']:.0%} vs {b.get('max_drawdown', 0):.0%}), because it moves to cash when trends break."
        parts.append(f"""<section><h2>The bottom line</h2>
<p class="k">If you had put <b>$10,000</b> into the current strategy during the test years ({ts[:4]}–{te[:4]}), which it was not built on:</p>
<div class="cards"><div class="card"><p class="clabel">This strategy</p><p class="big">{_money(sv)}</p>
<p>{v['cagr']:.1%} a year · worst drop {v['max_drawdown']:.0%}</p></div>
<div class="card"><p class="clabel">Just holding the same investments</p><p class="big">{_money(bv)}</p>
<p>{b.get('cagr', 0):.1%} a year · worst drop {b.get('max_drawdown', 0):.0%}</p></div></div>
<p>{E(line)}</p></section>""")

        res = run_backtest(current["spec"], data, cfg["backtest"], w["dev"][0], w["validation"][1])
        bh = buy_and_hold(data, w["dev"][0], w["validation"][1], cfg["backtest"]["initial_capital"])
        parts.append(f"""<section><h2>Growth of $10,000, {win['dev'][0][:4]}–{win['validation'][1][:4]}</h2>
<p class="k">The years it was built on plus the test years. The most recent years ({win['holdout'][0][:4]}–{win['holdout'][1][:4]}) are kept hidden for a one-time final exam.</p>
<div class="chart">{_chart(res.equity, bh.equity)}</div></section>""")

        r = plain.rules(current["spec"])
        rank = f"<p>{E(r['rank'])}</p>" if r["rank"] else ""
        parts.append(f"""<section><h2>What the current strategy does</h2>
<p class="k">{E(current['spec'].get('description', ''))}</p>
<div class="cards"><div class="card"><p class="clabel">Buys when ALL of these are true</p><ul>{''.join(f'<li>{E(x)}</li>' for x in r['buy'])}</ul></div>
<div class="card"><p class="clabel">Sells when ANY of these happens</p><ul>{''.join(f'<li>{E(x)}</li>' for x in r['sell'])}</ul></div></div>
{rank}<p class="k">It checks once a day using the closing price and trades at the next morning's open.</p></section>""")

        rows = []
        for c in rep["checks"]:
            name, why = plain.CHECKS.get(c["name"], (c["name"], ""))
            rows.append(f"<tr><td class='mark {'ok' if c['pass'] else 'bad'}'>{'✓' if c['pass'] else '✗'}</td>"
                        f"<td><b>{E(name)}</b><br><span class='k'>{E(why)}</span></td>"
                        f"<td>{E(plain.check_value(c['name'], c['value']))}</td></tr>")
        n_pass = sum(c["pass"] for c in rep["checks"])
        parts.append(f"""<section><h2>Safety checks: {n_pass} of {len(rep['checks'])} passed</h2>
<p class="k">A strategy must pass <b>every</b> check before it can trade, even with play money.</p>
<div class="scroll"><table><tbody>{''.join(rows)}</tbody></table></div></section>""")

    for h in holdouts:
        hr = h["report"]
        if hr.get("kind") != "confirmation":
            continue
        m, bb = hr["metrics"], hr["benchmark"]
        rows = "".join(f"<tr><td class='mark {'ok' if c['pass'] else 'bad'}'>{'✓' if c['pass'] else '✗'}</td>"
                       f"<td>{E(plain.CONFIRM_CHECKS.get(c['name'], c['name']))}</td></tr>" for c in hr["checks"])
        verdict = "PASSED" if h["passed"] else "FAILED"
        parts.append(f"""<section><h2>Final exam on never-seen years: {verdict}</h2>
<p class="k">Strategy <code>{E(h['hash'])}</code> was tested once on {hr['window'][0][:4]}–{hr['window'][1][:4]}, data no experiment had ever used. This test can only be run once.</p>
<div class="cards"><div class="card"><p class="clabel">This strategy</p><p class="big">{_money(10000 * (1 + m['total_return']))}</p>
<p>{m['cagr']:.1%} a year · worst drop {m['max_drawdown']:.0%}</p></div>
<div class="card"><p class="clabel">Just holding</p><p class="big">{_money(10000 * (1 + bb['total_return']))}</p>
<p>{bb['cagr']:.1%} a year · worst drop {bb['max_drawdown']:.0%}</p></div></div>
<div class="scroll"><table><tbody>{rows}</tbody></table></div></section>""")

    exps, older = [], 0
    for e in mem.recent(200):
        rp = e["report"] or {}
        if rp.get("windows") != win or rp.get("universe") != universe:
            older += 1
            continue
        v, b = rp.get("validation", {}), rp.get("benchmark_validation", {})
        status = {"baseline": "Starting idea", "promoted": "Approved", "rejected": "Rejected",
                  "manual_validate": "Checked"}.get(e["status"], e["status"])
        if mem.holdout_failed(e["hash"]):
            status = "Failed final exam"
        idea = (e["hypothesis"] or "").split(" | ")[0]
        idea = idea if len(idea) <= 160 else idea[:157].rsplit(" ", 1)[0] + "…"
        exps.append(f"<tr><td><b>{E(status)}</b><br><code class='k'>{E(e['hash'])}</code></td><td>{E(idea)}</td>"
                    f"<td>{v.get('sharpe', 0):.2f} <span class='k'>vs {b.get('sharpe', 0):.2f}</span></td>"
                    f"<td>{v.get('max_drawdown', 0):.0%}</td><td>{E(plain.reason(e['reasons']) if e['status'] != 'promoted' else '—')}</td></tr>")
    note = f" {older} earlier tests on a different data range are not shown." if older else ""
    parts.append(f"""<section><h2>Every idea tested ({len(exps)})</h2>
<p class="k">Newest first. "Return for risk" is the Sharpe ratio in the test years, next to just holding.{E(note)}</p>
<div class="scroll"><table><thead><tr><th>Result</th><th>Idea</th><th>Return for risk</th><th>Worst drop</th><th>Main reason</th></tr></thead>
<tbody>{''.join(exps)}</tbody></table></div></section>""")

    parts.append("""<section><details><summary>What the terms mean</summary><dl>
<dt>Backtest</dt><dd>Replaying the rules on past prices to see what would have happened. It is a simulation, not a promise.</dd>
<dt>Test years</dt><dd>Years the strategy was not designed on. Results there are a fairer guide than the years it was built on.</dd>
<dt>Final exam (holdout)</dt><dd>The most recent years, hidden from all research and used once at the end.</dd>
<dt>Return for risk (Sharpe ratio)</dt><dd>Return divided by how bumpy the ride was. Around 0.5 is ordinary; above 1.0 is strong.</dd>
<dt>Worst drop (max drawdown)</dt><dd>The biggest fall from a high point before recovering. 30% means $10,000 fell to $7,000 at its worst.</dd>
<dt>A year (CAGR)</dt><dd>The average yearly growth rate, compounded.</dd>
<dt>Paper trading</dt><dd>Trading with play money at a real broker to check the strategy behaves as the backtest expects.</dd>
<dt>Average daily range (ATR)</dt><dd>How much the price typically moves in a day. Stops and targets are set as multiples of it.</dd>
</dl></details></section>""")

    rs = risk.state
    tech = {k: (c["hash"] if c else None) for k, c in roles.items()}
    parts.append(f"""<section><details><summary>Technical details</summary>
<p class="k">Risk-limits file {risk.limits_sha} · kill switch {'ON' if risk.killed else 'off'} · halted {rs.get('halted', False)} {E(rs.get('halt_reason', ''))}
· trials {mem.trial_count()} · holdout uses {mem.holdout_uses()} · roles {E(json.dumps(tech))}</p>
<p class="k">Windows: {E(json.dumps(win))}</p>
<pre>{E(json.dumps(current['spec'], indent=1)) if current else ''}</pre>
<pre>{E(json.dumps(current['report']['checks'], indent=1)) if current and current.get('report') else ''}</pre>
</details></section>""")

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sentinel Report</title><style>
:root{{--bg:#f7f6f2;--card:#fff;--fg:#1d1d1b;--mut:#6b6a64;--line:#e4e2dc;--strat:#0f766e;--bench:#a8a29e;
--ok:#15803d;--bad:#b42318;--warn:#b45309;--okbg:#ecfdf3;--badbg:#fef3f2;--warnbg:#fffaeb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141413;--card:#1e1d1b;--fg:#ecebe6;--mut:#a3a29c;--line:#2f2e2b;--strat:#2dd4bf;--bench:#78716c;
--ok:#4ade80;--bad:#f97066;--warn:#fbbf24;--okbg:#0c2a1a;--badbg:#2d1311;--warnbg:#2b2107}}}}
*{{box-sizing:border-box}}body{{background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,Segoe UI,sans-serif;max-width:980px;margin:0 auto;padding:24px 16px 48px}}
h1{{font-size:28px;margin:2px 0}}h2{{font-size:20px;margin:0 0 8px}}section{{margin:28px 0}}.eyebrow,.clabel,.vlabel{{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--mut);margin:0}}
.sub,.k{{color:var(--mut)}}.k{{font-size:14px}}code{{font-size:13px}}
.verdict{{border-radius:12px;padding:18px 20px;border:1px solid var(--line)}}.verdict h2{{font-size:24px;margin:4px 0}}
.verdict.stop{{background:var(--badbg)}}.verdict.stop h2{{color:var(--bad)}}.verdict.paper{{background:var(--warnbg)}}.verdict.paper h2{{color:var(--warn)}}
.verdict.live{{background:var(--okbg)}}.verdict.live h2{{color:var(--ok)}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin:12px 0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}}.card ul{{margin:8px 0 0;padding-left:20px}}
.big{{font-size:30px;font-weight:650;margin:4px 0}}.chart{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}}
svg{{width:100%;height:auto}}.grid{{stroke:var(--line)}}.axis{{fill:var(--mut);font-size:12px}}.l-strat{{stroke:var(--strat)}}.l-bench{{stroke:var(--bench)}}
.legend{{display:flex;gap:18px;flex-wrap:wrap;font-size:14px;margin-top:6px}}.sw{{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:6px}}
.sw.strat{{background:var(--strat)}}.sw.bench{{background:var(--bench)}}
.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line);border-radius:12px;font-size:14px}}
td,th{{border-bottom:1px solid var(--line);padding:10px;text-align:left;vertical-align:top}}th{{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut)}}
.mark{{font-size:18px;font-weight:700;width:32px}}.ok{{color:var(--ok)}}.bad{{color:var(--bad)}}
details{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px}}summary{{cursor:pointer;font-weight:600}}
dt{{font-weight:600;margin-top:10px}}dd{{margin:2px 0 0;color:var(--mut)}}pre{{overflow:auto;font-size:12px;background:var(--bg);padding:10px;border-radius:8px}}
.divider{{margin-top:44px;padding-top:16px;border-top:2px solid var(--line);font-size:22px}}
{trading_view.CSS}</style></head><body>{''.join(parts)}
<p class="k">Simulated results, not a guarantee. Not financial advice.</p></body></html>"""
    p = Path(out_path)
    p.write_text(page, encoding="utf-8")
    return p
