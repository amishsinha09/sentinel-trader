"""Plain-English wording for strategy rules, gate checks and metrics (used by the report)."""
from __future__ import annotations

import re

from .strategy import TERM_RE

OPS = {">": "is above", ">=": "is at or above", "<": "is below", "<=": "is at or below",
       "crosses_above": "crosses above", "crosses_below": "crosses below"}
COLS = {"close": "the closing price", "open": "the opening price", "high": "the day's high",
        "low": "the day's low", "volume": "trading volume"}


def _num(x) -> str:
    x = float(x)
    return f"{int(x)}" if x == int(x) else f"{x:g}"


def term(t, params: dict) -> str:
    if isinstance(t, (int, float)):
        return _num(t)
    t = str(t).strip()
    if t in COLS:
        return COLS[t]
    m = TERM_RE.match(t)
    if not m:
        return t
    fn, arg = m.groups()
    if fn == "param":
        return _num(params.get(arg, arg))
    n = _num(params[arg]) if arg in params else arg
    return {
        "ema": f"its {n}-day average (exponential)",
        "sma": f"its {n}-day average",
        "rsi": f"the {n}-day RSI (a 0-100 momentum gauge)",
        "atr": f"its {n}-day average daily range",
        "atr_pct": f"its {n}-day average daily range, as % of price",
        "atr_pct_rank": f"its volatility rank over the past {n} days (0 = calmest, 1 = wildest)",
        "adx": f"trend strength (ADX, {n}-day; under 20 = calm, over 40 = strong)",
        "roc": f"its % change over the past {n} trading days",
        "highest": f"the highest price of the previous {n} days",
        "lowest": f"the lowest price of the previous {n} days",
        "dist_ema": f"its % distance above its {n}-day average",
        "vol_z": f"how unusual volume is vs the past {n} days",
    }.get(fn, t)


def condition(c: dict, params: dict) -> str:
    left, right = term(c["left"], params), term(c["right"], params)
    if "scale" in c:
        right = f"{_num(c['scale'] * 100)}% of {right}"
    # "close > ema(200)" reads better as "the price is above its 200-day average"
    left = left.replace("the closing price", "the price")
    if c["left"] == "close" and str(c["right"]).startswith("roc"):
        left = "the price"
    if str(c["left"]).startswith("roc") and right == "0" and c["op"] in (">", ">="):
        return f"the price is higher than {term(c['left'], params).split('past ')[-1].replace(' trading days', '')} trading days ago"
    if str(c["left"]).startswith("roc") and right == "0" and c["op"] in ("<", "<="):
        return f"the price is lower than {term(c['left'], params).split('past ')[-1].replace(' trading days', '')} trading days ago"
    return f"{left} {OPS.get(c['op'], c['op'])} {right}"


def rules(spec: dict) -> dict:
    p = spec["params"]
    buy = [condition(c, p) for c in spec["entry"]["all"]]
    sell = [condition(c, p) for c in spec["exit"]["any"]]
    sell += [f"a stop-loss is hit ({_num(p['stop_atr'])}x the average daily range below the buy price)",
             f"a profit target is hit ({_num(p['target_atr'])}x the average daily range above the buy price)",
             f"it has been held for {p['max_hold_bars']} trading days"]
    rank = None
    if "rank_by" in spec:
        rank = f"If more ETFs or stocks qualify than there is room for, it buys the ones with the highest {term(spec['rank_by'], p)} first."
    return {"buy": buy, "sell": sell, "rank": rank}


CHECKS = {
    "min_trades_dev": ("Enough trades to judge it", "It needs plenty of trades in the years it was built on, or the results are just a few lucky bets."),
    "min_trades_oos": ("Enough trades in the test years", "Same idea, for the years it was tested on."),
    "oos_sharpe": ("Good return for the risk taken", "Return per unit of ups-and-downs (Sharpe ratio) in the test years. 0.5 is the minimum; 1.0+ is strong."),
    "oos_max_drawdown": ("Worst drop is acceptable", "The biggest fall from a peak in the test years must be no more than 25%."),
    "oos_vs_in_sample": ("Kept working on new years", "It must keep at least 30% of its performance on years it was NOT built on. Strategies that only work on the past fail here."),
    "walk_forward_consistency": ("Made money in most periods", "History is cut into 5 chunks; it must be profitable in at least 3."),
    "parameter_robustness": ("Not fragile", "Each setting is nudged up and down 20%. A real edge still works; a curve-fit one falls apart."),
    "survives_2x_costs": ("Survives higher trading costs", "Still profitable if every trade costs twice as much as assumed."),
    "deflated_sharpe": ("Not just luck", "The more ideas you try, the more likely one looks good by chance. This check corrects for every idea tried so far. It needs 90% confidence."),
}

CONFIRM_CHECKS = {
    "confidence_sharpe_above_zero": "Made money on never-seen years (90%+ confidence)",
    "sharpe_at_least_buy_and_hold": "At least as good as just holding",
    "max_drawdown": "Worst drop is acceptable",
    "min_trades": "Enough trades to judge it",
}


def check_value(name: str, value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if name in ("oos_max_drawdown", "max_drawdown"):
        return f"{v:.0%} worst drop"
    if name in ("walk_forward_consistency", "parameter_robustness"):
        return f"{v:.0%} of tests passed"
    if name in ("deflated_sharpe", "confidence_sharpe_above_zero"):
        return f"{v:.0%} confidence"
    if name == "oos_vs_in_sample":
        return f"kept {v:.0%}"
    if name == "survives_2x_costs":
        return f"{v:+.1%} total return at 2x costs"
    if name.startswith("min_trades"):
        return f"{int(v)} trades"
    return f"{v:.2f}"


def reason(failed: list[str]) -> str:
    """The single most important reason a candidate was rejected, in plain words."""
    order = ["min_trades_dev", "min_trades_oos", "survives_2x_costs", "oos_sharpe", "oos_vs_in_sample",
             "oos_max_drawdown", "walk_forward_consistency", "parameter_robustness", "deflated_sharpe"]
    names = [re.sub(r"\(.*", "", f) for f in failed or []]
    for k in order:
        if k in names:
            return CHECKS[k][0].replace("Enough", "Too few").replace("Good return for the risk taken", "Return too low for the risk") \
                .replace("Not just luck", "Could be luck").replace("Survives higher", "Fails with higher") \
                .replace("Kept working on new years", "Stopped working on new years").replace("Not fragile", "Fragile") \
                .replace("Made money in most periods", "Lost money in too many periods").replace("Worst drop is acceptable", "Drops too deep") \
                .replace("Too few trades to judge it", "Too few trades").replace("Too few trades in the test years", "Too few trades")
    if any(n.startswith("not_better_than_champion") for n in names):
        return "Not better than the current strategy"
    return "—"
