"""Static HTML status report: champions, experiment leaderboard, equity curve, risk state."""
from __future__ import annotations

import html
import json
from pathlib import Path

from .backtest import buy_and_hold, run_backtest
from .validation import split_dates


def _svg(series_list, w=720, h=220):
    allv = [v for s, _, _ in series_list for v in s.values]
    lo, hi = min(allv), max(allv)
    n = max(len(s) for s, _, _ in series_list)
    lines = []
    for s, color, label in series_list:
        pts = " ".join(f"{i / (n - 1) * w:.1f},{h - (v - lo) / (hi - lo or 1) * (h - 10) - 5:.1f}" for i, v in enumerate(s.values))
        lines.append(f'<polyline fill="none" stroke="{color}" stroke-width="1.6" points="{pts}"><title>{label}</title></polyline>')
    return f'<svg viewBox="0 0 {w} {h}" width="100%" preserveAspectRatio="none">{"".join(lines)}</svg>'


def build_report(cfg, data, mem, risk, out_path) -> Path:
    w = split_dates(data, cfg["splits"])
    rows, champ_html = [], ""
    for e in mem.recent(60):
        v = (e["report"] or {}).get("validation", {})
        rows.append(f"<tr class='{e['status']}'><td><code>{e['hash']}</code></td><td>{e['status']}</td>"
                    f"<td>{v.get('sharpe', 0):.2f}</td><td>{v.get('max_drawdown', 0):.1%}</td>"
                    f"<td>{(e['report'] or {}).get('dsr', 0):.2f}</td><td>{html.escape(e['hypothesis'] or '')[:160]}</td>"
                    f"<td>{html.escape(', '.join(e['reasons'] or []))}</td></tr>")
    roles = {r: mem.champion(r) for r in ("research", "paper", "live")}
    champ = roles["paper"] or roles["research"]
    if champ:
        res = run_backtest(champ["spec"], data, cfg["backtest"], w["dev"][0], w["validation"][1])
        bh = buy_and_hold(data, w["dev"][0], w["validation"][1], cfg["backtest"]["initial_capital"])
        checks = "".join(f"<li class='{'ok' if c['pass'] else 'bad'}'>{c['name']}: {c['value']} (need {c['threshold']})</li>"
                         for c in champ["report"]["checks"])
        champ_html = (f"<h2>Current strategy <code>{champ['hash']}</code></h2>"
                      f"<pre>{html.escape(json.dumps(champ['spec'], indent=1))}</pre>"
                      f"<p>Research-period equity: <span style='color:var(--a)'>strategy</span> vs "
                      f"<span style='color:var(--m)'>equal-weight buy &amp; hold</span>. Holdout not shown.</p>"
                      f"{_svg([(bh.equity, 'var(--m)', 'buy & hold'), (res.equity, 'var(--a)', 'strategy')])}"
                      f"<h3>Validation gate</h3><ul>{checks}</ul>")
    roles_html = "".join(f"<li><b>{r}</b>: {('<code>' + c['hash'] + '</code>') if c else '—'}</li>" for r, c in roles.items())
    rs = risk.state
    page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sentinel Report</title><style>
:root{{--bg:#fbfaf7;--fg:#1d1d1b;--mut:#6b6b66;--a:#0f766e;--m:#a8a29e;--bad:#b42318;--line:#e7e5e0}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--fg:#ecebe6;--mut:#a3a29c;--a:#2dd4bf;--m:#57534e;--bad:#f97066;--line:#2c2b29}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;max-width:1000px;margin:0 auto;padding:16px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}}
tr.promoted td:nth-child(2),li.ok{{color:var(--a)}}li.bad{{color:var(--bad)}}pre{{overflow:auto;font-size:12px;border:1px solid var(--line);padding:8px}}
.k{{color:var(--mut)}}div.scroll{{overflow-x:auto}}</style></head><body>
<h1>Sentinel Trader</h1><p class="k">Risk limits file sha {risk.limits_sha} · kill switch {'ON' if risk.killed else 'off'} ·
halted {rs.get('halted', False)} {html.escape(rs.get('halt_reason', ''))} · trials so far {mem.trial_count()} · holdout uses {mem.holdout_uses()}</p>
<ul>{roles_html}</ul>{champ_html}
<h2>Experiments</h2><div class="scroll"><table><tr><th>hash</th><th>status</th><th>OOS Sharpe</th><th>OOS DD</th><th>DSR</th><th>hypothesis</th><th>failed checks</th></tr>
{''.join(rows)}</table></div></body></html>"""
    p = Path(out_path)
    p.write_text(page)
    return p
