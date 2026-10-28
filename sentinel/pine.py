"""Export a strategy spec as a TradingView Pine Script v5 strategy().

Paste the output into the Pine Editor, add it to a chart, and use Strategy Tester to see it.
Entry/exit orders carry JSON alert messages, so a TradingView alert on the strategy can drive
a webhook. Small differences from the Python engine are expected: Pine sets stops from the
signal bar's close (Python uses the next-bar fill), and Pine's percentrank/stdev definitions
differ slightly.
"""
from __future__ import annotations

from .strategy import parse_term, validate_spec

PINE_IND = {
    "ema": "ta.ema(close, {p})",
    "sma": "ta.sma(close, {p})",
    "rsi": "ta.rsi(close, {p})",
    "atr": "ta.atr({p})",
    "atr_pct": "(ta.atr({p}) / close * 100)",
    "atr_pct_rank": "(ta.percentrank(ta.atr(14) / close, {p}) / 100)",
    "adx": "f_adx({p})",
    "roc": "ta.roc(close, {p})",
    "highest": "ta.highest(high, {p})[1]",
    "lowest": "ta.lowest(low, {p})[1]",
    "dist_ema": "((close / ta.ema(close, {p}) - 1) * 100)",
    "vol_z": "((volume - ta.sma(volume, {p})) / ta.stdev(volume, {p}))",
}


def _period_expr(raw: str, params: dict) -> str:
    """Keep a param reference as its input variable so it stays tunable in TradingView."""
    arg = raw[raw.index("(") + 1:-1]
    return f"i_{arg}" if arg in params else str(int(arg))


def _term(t, params) -> str:
    kind = parse_term(t, params)
    if kind[0] == "col":
        return kind[1]
    if isinstance(t, str) and t.startswith("param("):
        return "i_" + t[6:-1]
    if kind[0] == "num":
        return repr(kind[1])
    return PINE_IND[kind[1]].format(p=_period_expr(t, params))


def _cond(c, params) -> str:
    l, r = _term(c["left"], params), _term(c["right"], params)
    if "scale" in c:
        r = f"({r} * {c['scale']})"
    if c["op"] == "crosses_above":
        return f"ta.crossover({l}, {r})"
    if c["op"] == "crosses_below":
        return f"ta.crossunder({l}, {r})"
    return f"({l} {c['op']} {r})"


def to_pine(spec: dict, bt: dict, strategy_id: str = "") -> str:
    s = validate_spec(spec)
    P = s["params"]
    long = s["direction"] == "long"
    comm_pct = (bt.get("spread_bps", 2) / 2 + bt.get("slippage_bps", 3)) / 100
    inputs = []
    for k, v in P.items():
        if isinstance(v, int) and not isinstance(v, bool):
            inputs.append(f'i_{k} = input.int({v}, "{k}")')
        else:
            inputs.append(f'i_{k} = input.float({v}, "{k}", step=0.05)')
    # Evaluate every condition on every bar (Pine's and/or short-circuit would skip ta.* calls)
    ent_vars = [f"e{i} = {_cond(c, P)}" for i, c in enumerate(s["entry"]["all"])]
    ex_vars = [f"x{i} = {_cond(c, P)}" for i, c in enumerate(s["exit"]["any"])]
    entry = "\n".join(ent_vars) + "\nentryCond = " + " and ".join(f"e{i}" for i in range(len(ent_vars)))
    exit_ = "\n".join(ex_vars) + "\nexitCond = " + (" or ".join(f"x{i}" for i in range(len(ex_vars))) or "false")
    side, name = ("strategy.long", "L") if long else ("strategy.short", "S")
    sgn = "-" if long else "+"
    tsgn = "+" if long else "-"
    plots = sorted({_term(t, P) for c in s["entry"]["all"] + s["exit"]["any"] for t in (c["left"], c["right"])
                    if isinstance(t, str) and t.split("(")[0] in ("ema", "sma")})
    plot_lines = "\n".join(f'plot({e}, "{e}", linewidth=1)' for e in plots)
    return f"""//@version=5
// Sentinel Trader export — strategy {strategy_id or s['name']} v{s['version']}
// {s.get('description', '')}{chr(10) + "// rank_by (" + s["rank_by"] + ") applies to multi-symbol portfolios only; ignored on a single chart" if "rank_by" in s else ""}
strategy("Sentinel: {s['name']} {strategy_id}", overlay=true, initial_capital={int(bt.get('initial_capital', 100000))},
     commission_type=strategy.commission.percent, commission_value={comm_pct:.4f}, slippage=0,
     pyramiding=0, process_orders_on_close=false)

// ── Parameters (tunable, but re-validate in Python before trusting changes) ──
{chr(10).join(inputs)}
risk_pct = input.float({bt.get('risk_per_trade', 0.01) * 100}, "Risk per trade %", step=0.25)

f_adx(len) =>
    [_dip, _dim, _adx] = ta.dmi(len, len)
    _adx

atrv = ta.atr(i_atr_period)
{entry}
{exit_}

// ── Orders: signal on bar close, fill next open; stop/target as ATR multiples ──
var float stopLvl = na
var float tgtLvl = na
qty = math.max(math.floor(strategy.equity * risk_pct / 100 / (i_stop_atr * atrv)), 0)
if entryCond and strategy.position_size == 0 and qty > 0
    stopLvl := close {sgn} i_stop_atr * atrv
    tgtLvl := close {tsgn} i_target_atr * atrv
    strategy.entry("{name}", {side}, qty=qty,
         alert_message='{{"action":"entry","side":"{s['direction']}","ticker":"' + syminfo.ticker + '","qty":' + str.tostring(qty) + ',"stop":' + str.tostring(stopLvl) + ',"target":' + str.tostring(tgtLvl) + '}}')
if strategy.position_size != 0
    strategy.exit("{name}X", "{name}", stop=stopLvl, limit=tgtLvl,
         alert_message='{{"action":"exit","ticker":"' + syminfo.ticker + '"}}')
    held = bar_index - strategy.opentrades.entry_bar_index(0)
    if exitCond or held >= i_max_hold_bars
        strategy.close("{name}", alert_message='{{"action":"close","ticker":"' + syminfo.ticker + '"}}')

// ── Visuals ──
{plot_lines}
plotshape(entryCond and strategy.position_size == 0, "Signal", shape.triangleup, location.belowbar, color.new(color.teal, 0), size=size.tiny)
plot(strategy.position_size != 0 ? stopLvl : na, "Stop", color.new(color.red, 0), style=plot.style_linebr)
plot(strategy.position_size != 0 ? tgtLvl : na, "Target", color.new(color.green, 0), style=plot.style_linebr)
"""
