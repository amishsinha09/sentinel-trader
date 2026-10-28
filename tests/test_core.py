"""Run: python -m unittest discover -s tests -v"""
import copy
import json
import tempfile
import unittest
from datetime import date, timedelta

import numpy as np
import pandas as pd

from sentinel.backtest import run_backtest
from sentinel.config import ROOT, load_settings
from sentinel.data import synthetic
from sentinel.metrics import deflated_sharpe
from sentinel.options import choose_structure, select_contracts
from sentinel.pine import to_pine
from sentinel.risk import RiskEngine
from sentinel.strategy import SpecError, compute_signals, spec_hash, validate_spec

BASE = json.loads((ROOT / "strategies/baseline_trend_pullback.json").read_text())


class TestStrategy(unittest.TestCase):
    def setUp(self):
        self.data = synthetic(["AAA", "BBB"], "2018-01-01", "2023-12-31")
        self.cfg = load_settings()

    def test_no_lookahead(self):
        """Changing future prices must not change past signals."""
        df = self.data["AAA"]
        e1, x1, _ = compute_signals(BASE, df)
        df2 = df.copy()
        df2.iloc[800:] *= 3.0
        e2, x2, _ = compute_signals(BASE, df2)
        pd.testing.assert_series_equal(e1.iloc[:800], e2.iloc[:800])
        pd.testing.assert_series_equal(x1.iloc[:800], x2.iloc[:800])

    def test_rejects_bad_specs(self):
        for mutate in (
            lambda s: s.update(risk={"max_loss": 99}),                      # cannot set risk from a spec
            lambda s: s["entry"]["all"].append({"left": "os.system(1)", "op": ">", "right": 1}),
            lambda s: s["entry"]["all"].append({"left": "magic(14)", "op": ">", "right": 1}),
            lambda s: s["params"].update(stop_atr=50),
            lambda s: s["entry"]["all"].extend([s["entry"]["all"][0]] * 8),  # complexity cap
        ):
            s = copy.deepcopy(BASE)
            mutate(s)
            with self.assertRaises(SpecError):
                validate_spec(s)

    def test_hash_ignores_name(self):
        s = copy.deepcopy(BASE)
        s["name"] = "renamed"
        self.assertEqual(spec_hash(validate_spec(s)), spec_hash(validate_spec(BASE)))

    def test_costs_reduce_returns(self):
        cheap = run_backtest(BASE, self.data, self.cfg["backtest"]).metrics["total_return"]
        dear = run_backtest(BASE, self.data, self.cfg["backtest"], cost_mult=5).metrics["total_return"]
        self.assertLess(dear, cheap)

    def test_position_risk_respected(self):
        r = run_backtest(BASE, self.data, self.cfg["backtest"])
        stops = r.trades[r.trades.exit_reason == "stop"]
        self.assertTrue(len(stops) > 0)
        # a plain stop-out should lose roughly <= 1% of starting equity (plus costs)
        self.assertLess(-stops.pnl.min(), self.cfg["backtest"]["initial_capital"] * 0.02)


class TestMetrics(unittest.TestCase):
    def test_dsr_falls_with_more_trials(self):
        a = deflated_sharpe(0.08, 500, 0, 3, 1, 0.001)
        b = deflated_sharpe(0.08, 500, 0, 3, 200, 0.001)
        self.assertGreater(a, b)


class TestRisk(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.r = RiskEngine(self.tmp)

    def entry(self, **kw):
        args = dict(symbol="AAPL", qty=1000, price=100, stop=98, equity=100000, positions={}, live=False)
        args.update(kw)
        return self.r.check_entry(**args)

    def test_clips_to_risk_budget(self):
        d = self.entry()
        self.assertTrue(d.approved)
        self.assertLessEqual(d.qty * 2, 100000 * 0.01)

    def test_kill_switch(self):
        self.r.kill("test")
        self.assertFalse(self.entry().approved)
        self.r.unkill()
        self.assertTrue(self.entry().approved)

    def test_live_blocked_by_default(self):
        self.assertFalse(self.entry(live=True).approved)

    def test_daily_loss_halt(self):
        self.r.update_equity(100000, "2026-01-02")
        msgs = self.r.update_equity(96000, "2026-01-02")
        self.assertTrue(msgs)
        self.assertFalse(self.entry().approved)

    def test_circuit_breaker(self):
        for _ in range(3):
            self.r.record_result(False)
        self.assertFalse(self.entry().approved)

    def test_naked_options_blocked(self):
        d = self.r.check_option(max_loss_per_contract=200, contracts=5, defined_risk=False, equity=100000, live=False)
        self.assertFalse(d.approved)


class TestOptions(unittest.TestCase):
    def test_structure_map(self):
        self.assertEqual(choose_structure(1, 20), "call_debit_spread")
        self.assertEqual(choose_structure(1, 80), "bull_put_credit_spread")
        self.assertEqual(choose_structure(-1, 40), "put_debit_spread")
        self.assertEqual(choose_structure(0, 80), "iron_condor")

    def test_select_debit_spread(self):
        exp = date.today() + timedelta(days=35)
        rows = []
        for k in range(80, 121, 5):
            d = float(np.clip(0.5 - (k - 100) / 40, 0.05, 0.95))
            mid = max(100 - k, 0) + 3 * d
            rows.append(dict(symbol=f"X{k}C", type="call", strike=k, expiry=exp, bid=mid - 0.05, ask=mid + 0.05,
                             delta=d, open_interest=1000))
        limits = {"min_dte": 14, "max_dte": 60, "min_open_interest": 500, "max_bid_ask_pct": 10}
        it = select_contracts("X", "call_debit_spread", pd.DataFrame(rows), limits)
        self.assertIsNotNone(it)
        self.assertEqual([l["side"] for l in it.legs], ["buy", "sell"])
        self.assertGreater(it.max_loss, 0)


class TestPine(unittest.TestCase):
    def test_exports(self):
        src = to_pine(BASE, {"initial_capital": 100000})
        self.assertIn("//@version=5", src)
        self.assertIn("strategy.entry", src)
        self.assertIn("ta.ema(close, i_ema_trend)", src)


if __name__ == "__main__":
    unittest.main()
