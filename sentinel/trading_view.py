"""Visual trading view for the dashboard: today's signal per symbol, price charts with the
strategy's lines and the bot's real trades, and the account (equity curve + open positions)."""
from __future__ import annotations

import html
import json
import math
import re

import numpy as np
import pandas as pd

from . import plain
from .features import FeatureCache
from .strategy import _cond, compute_rank, parse_term, validate_spec

E = html.escape
LINE_COLORS = ["var(--c1)", "var(--c2)", "var(--c3)"]


def _avg_lines(spec: dict) -> list[tuple[str, int]]:
    """Moving-average lines worth drawing on the price chart (ema/sma used by the rules)."""
    out, p = [], spec["params"]
    for c in spec["entry"]["all"] + spec["exit"]["any"]:
        for t in (c["left"], c["right"]):
            if isinstance(t, str):
                k = parse_term(t, p)
                if k[0] == "ind" and k[1] in ("ema", "sma") and (k[1], k[2]) not in out:
                    out.append((k[1], k[2]))
    return out[:3]


def _trades_from_runs(mem) -> tuple[list, list]:
    """(equity points, trade markers) from the logged daily runs."""
    eq, marks = [], []
    for ts, data in mem.db.execute("SELECT ts, data FROM events WHERE kind='run' ORDER BY ts"):
        d = json.loads(data or "{}")
        day = pd.Timestamp(ts[:10])
        if d.get("equity") is not None:
            eq.append((day, float(d["equity"])))
        for e in d.get("entries", []):
            if e.get("ok") and not e.get("dry_run"):
                marks.append({"symbol": e["symbol"], "day": day, "side": "buy", "price": e.get("ref")})
        for e in d.get("exits", []):
            if e.get("ok", True) and not e.get("dry_run") and e.get("symbol"):
                marks.append({"symbol": e["symbol"], "day": day, "side": "sell", "reason": e.get("reason")})
    return eq, marks


def _latest_positions(mem) -> dict:
    r = mem.db.execute("SELECT data FROM events WHERE kind='run' ORDER BY ts DESC LIMIT 1").fetchone()
    return json.loads(r[0]).get("positions", {}) if r else {}


def _price_svg(df: pd.DataFrame, lines: list, marks: list, w=460, h=200) -> str:
    view = df.iloc[-252:]
    fc = FeatureCache(df)
    series = [("Price", view.close, "var(--fg)", 1.6)]
    for i, (kind, n) in enumerate(lines):
        series.append((f"{n}-day avg", fc.get(kind, n).reindex(view.index), LINE_COLORS[i % 3], 1.3))
    vals = np.concatenate([s.dropna().values for _, s, _, _ in series])
    lo, hi = float(vals.min()), float(vals.max())
    pl, pr, pt, pb = 74, 8, 10, 30
    iw, ih, n = w - pl - pr, h - pt - pb, len(view)

    def xy(i, v):
        return pl + i / max(n - 1, 1) * iw, pt + ih - (v - lo) / (hi - lo or 1) * ih

    out = []
    for k in range(3):
        v = lo + (hi - lo) * k / 2
        y = xy(0, v)[1]
        out.append(f'<line x1="{pl}" x2="{w - pr}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{pl - 6}" y="{y + 6:.1f}" text-anchor="end" class="axis">{v:,.0f}</text>')
    for m in range(0, n, max(n // 4, 1)):
        x = xy(m, lo)[0]
        out.append(f'<text x="{x:.1f}" y="{h - 4}" text-anchor="middle" class="axis">{view.index[m]:%b %y}</text>')
    for label, s, color, sw in series:
        pts = " ".join(f"{xy(i, v)[0]:.1f},{xy(i, v)[1]:.1f}" for i, v in enumerate(s.values) if math.isfinite(v))
        dash = ' stroke-dasharray="4 3"' if label != "Price" else ""
        out.append(f'<polyline fill="none" stroke="{color}" stroke-width="{sw}"{dash} points="{pts}"><title>{E(label)}</title></polyline>')
    for mk in marks:
        i = view.index.searchsorted(mk["day"])
        if i >= n:
            i = n - 1
        if mk["day"] < view.index[0]:
            continue
        x, y = xy(i, float(view.close.iloc[i]))
        if mk["side"] == "buy":
            out.append(f'<path d="M{x:.1f},{y + 4:.1f} l-6,10 h12 z" class="m-buy"><title>Bought {mk["day"]:%b %d}</title></path>')
        else:
            out.append(f'<path d="M{x:.1f},{y - 4:.1f} l-6,-10 h12 z" class="m-sell"><title>Sold {mk["day"]:%b %d}</title></path>')
    legend = "".join(f'<span><i class="sw" style="background:{c}"></i>{E(l)}</span>' for l, _, c, _ in series)
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Price chart">{"".join(out)}</svg>'
            f'<div class="legend sm">{legend}<span><i class="tri buy"></i>bot bought</span><span><i class="tri sell"></i>bot sold</span></div>')


def _equity_svg(points: list, w=760, h=200) -> str:
    s = pd.Series([v for _, v in points], index=[d for d, _ in points])
    s = s[~s.index.duplicated(keep="last")]
    lo, hi = float(s.min()), float(s.max())
    pl, pr, pt, pb = 70, 10, 10, 22
    iw, ih, n = w - pl - pr, h - pt - pb, len(s)
    pts = " ".join(f"{pl + i / max(n - 1, 1) * iw:.1f},{pt + ih - (v - lo) / (hi - lo or 1) * ih:.1f}" for i, v in enumerate(s.values))
    labels = "".join(f'<text x="{pl - 6}" y="{pt + ih - k / 2 * ih + 4:.1f}" text-anchor="end" class="axis">${lo + (hi - lo) * k / 2:,.0f}</text>'
                     f'<line x1="{pl}" x2="{w - pr}" y1="{pt + ih - k / 2 * ih:.1f}" y2="{pt + ih - k / 2 * ih:.1f}" class="grid"/>' for k in range(3))
    dates = (f'<text x="{pl}" y="{h - 5}" class="axis">{s.index[0]:%b %d, %Y}</text>'
             f'<text x="{w - pr}" y="{h - 5}" text-anchor="end" class="axis">{s.index[-1]:%b %d, %Y}</text>')
    return f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Account value">{labels}{dates}<polyline fill="none" class="l-strat" stroke-width="2.2" points="{pts}"/></svg>'


def sections(cfg: dict, mem, spec: dict, recent: dict, approved: bool, broker_name: str) -> list[str]:
    spec = validate_spec(spec)
    p = spec["params"]
    positions = _latest_positions(mem)
    eq, marks = _trades_from_runs(mem)
    parts = []

    # ── Account ─────────────────────────────────────────────────────────
    if eq:
        start, now = eq[0][1], eq[-1][1]
        rows = "".join(
            f"<tr><td><b>{E(s)}</b></td><td>{v['qty']}</td><td>${v['avg_price']:,.2f}</td><td>${v['last']:,.2f}</td>"
            f"<td class='{'ok' if v['pnl'] >= 0 else 'bad'}'>{'+' if v['pnl'] >= 0 else '−'}${abs(v['pnl']):,.0f} "
            f"({(v['last'] / v['avg_price'] - 1):+.1%})</td>"
            f"<td>{'$' + format(v['stop'], ',.2f') if v.get('stop') else '—'}</td>"
            f"<td>{'$' + format(v['target'], ',.2f') if v.get('target') else '—'}</td><td>{E(v.get('since') or '—')}</td></tr>"
            for s, v in positions.items())
        table = (f"<div class='scroll'><table><thead><tr><th>Holding</th><th>Shares</th><th>Bought at</th><th>Now</th>"
                 f"<th>Profit / loss</th><th>Stop-loss</th><th>Target</th><th>Since</th></tr></thead><tbody>{rows}</tbody></table></div>"
                 if positions else "<p class='k'>No open positions right now. The bot is in cash.</p>")
        chart = _equity_svg(eq) if len(eq) >= 2 else "<p class='k'>The account chart appears after the second daily run.</p>"
        parts.append(f"""<section><h2>Your account ({E(broker_name)})</h2>
<div class="cards"><div class="card"><p class="clabel">Account value</p><p class="big">${now:,.0f}</p>
<p class="{'ok' if now >= start else 'bad'}">{now / start - 1:+.2%} since the first run</p></div>
<div class="card"><p class="clabel">Open positions</p><p class="big">{len(positions)}</p><p>{len(marks)} trades so far</p></div></div>
<div class="chart">{chart}</div>{table}</section>""")
    else:
        parts.append("""<section><h2>Your account</h2><div class="card"><p><b>No trading yet.</b> Once paper trading starts
(<code>run-once</code> each market day), this panel shows your account value over time, open positions with profit/loss,
stop-loss and target levels, and every buy and sell on the charts below.</p></div></section>""")

    # ── Today's signals ─────────────────────────────────────────────────
    cards = []
    slots = cfg["backtest"]["max_positions"]
    full = len(positions) >= slots
    for sym, df in recent.items():
        if len(df) < 60:
            continue
        fc = FeatureCache(df)
        ent = [(plain.condition(c, p), bool(_cond(fc, c, p).iloc[-1])) for c in spec["entry"]["all"]]
        ex = [(plain.condition(c, p), bool(_cond(fc, c, p).iloc[-1])) for c in spec["exit"]["any"]]
        holding = sym in positions
        if holding and any(ok for _, ok in ex):
            state, cls = "SELL signal", "sell"
        elif holding:
            state, cls = "HOLD", "hold"
        elif all(ok for _, ok in ent) and full:
            state, cls = "QUALIFIES — no free slot", "full"
        elif all(ok for _, ok in ent):
            state, cls = "BUY signal", "buy"
        else:
            state, cls = "WAIT", "wait"
        rank = float(compute_rank(spec, df).iloc[-1]) if "rank_by" in spec else None
        last, prev = float(df.close.iloc[-1]), float(df.close.iloc[-2])
        checks = "".join(f"<li class='{'ok' if ok else 'bad'}'><span>{'✓' if ok else '✗'}</span> {E(t)}</li>" for t, ok in ent)
        if holding:
            checks += "".join(f"<li class='bad'><span>!</span> <b>Sell rule triggered:</b> {E(t)}</li>" if ok else
                              f"<li class='k'><span>·</span> Sells if {E(t)}: not yet</li>" for t, ok in ex)
        if cls == "full":
            checks += f"<li class='k'>All {slots} position slots are in use. It buys only when a holding is sold.</li>"
        if holding:
            pz = positions[sym]
            checks = (f"<li><b class='{'ok' if pz['pnl'] >= 0 else 'bad'}'>Holding {pz['qty']} shares, "
                      f"{'+' if pz['pnl'] >= 0 else '−'}${abs(pz['pnl']):,.0f} ({pz['last'] / pz['avg_price'] - 1:+.1%})</b></li>") + checks
        order = {"sell": 0, "buy": 1, "hold": 2, "full": 3, "wait": 4}[cls]
        cards.append((order, -(rank if rank is not None and math.isfinite(rank) else -1e9), sym, f"""
<div class="sig {cls}"><div class="sighead"><b>{E(sym)}</b><span class="pill {cls}">{state}</span></div>
<p class="px">${last:,.2f} <span class="{'ok' if last >= prev else 'bad'}">{last / prev - 1:+.2%}</span>
<span class="k">· {df.index[-1]:%b %d}</span></p>
<ul class="checks">{checks}</ul>
{f'<p class="k">Ranking score: {rank:+.1f}</p>' if rank is not None and math.isfinite(rank) else ''}
<div class="mini">{_price_svg(df, _avg_lines(spec), [m for m in marks if m['symbol'] == sym])}</div></div>"""))
    cards.sort()
    n_buy = sum(1 for c in cards if c[0] == 1)
    n_hold = sum(1 for c in cards if c[0] in (0, 2))
    note = ("" if approved else
            "<p class='warnline'>No strategy is approved to trade, so these signals are <b>for information only</b>. "
            "The bot will not act on them.</p>")
    parts.append(f"""<section><h2>Today's signals</h2>{note}
<p class="k">What the strategy says for each one based on the latest close: {n_hold} held, {n_buy} new buy signal{'s' if n_buy != 1 else ''}.
Green ✓ = rule met, red ✗ = not met. Charts show the past 12 months with the strategy's average lines.</p>
<div class="sigs">{''.join(c[3] for c in cards)}</div></section>""")
    return parts


CSS = """
:root{--c1:#2563eb;--c2:#d97706;--c3:#9333ea}
@media (prefers-color-scheme:dark){:root{--c1:#60a5fa;--c2:#fbbf24;--c3:#c084fc}}
.sigs{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.sig{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--line);border-radius:12px;padding:12px 14px}
.sig.buy{border-left-color:var(--ok)}.sig.full{border-left-color:var(--warn)}.pill.full{background:var(--warnbg);color:var(--warn)}
.mini .axis{font-size:19px}.sig.sell{border-left-color:var(--bad)}.sig.hold{border-left-color:var(--c1)}
.sighead{display:flex;justify-content:space-between;align-items:center;font-size:18px}
.pill{font-size:12px;font-weight:700;padding:3px 10px;border-radius:999px;background:var(--bg);color:var(--mut)}
.pill.buy{background:var(--okbg);color:var(--ok)}.pill.sell{background:var(--badbg);color:var(--bad)}.pill.hold{color:var(--c1)}
.px{margin:4px 0;font-size:15px}.checks{list-style:none;padding:0;margin:6px 0;font-size:13px}.checks li{margin:3px 0}
.checks span{display:inline-block;width:16px;font-weight:700}.mini{margin-top:6px}
.legend.sm{font-size:12px;gap:10px}.tri{display:inline-block;width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;margin-right:5px}
.tri.buy{border-bottom:9px solid var(--ok)}.tri.sell{border-top:9px solid var(--bad)}
.m-buy{fill:var(--ok)}.m-sell{fill:var(--bad)}
.warnline{background:var(--warnbg);border-radius:8px;padding:8px 12px}
"""
