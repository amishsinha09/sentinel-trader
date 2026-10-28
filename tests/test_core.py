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

BASE = json.loads((ROOT / "strategies/baseline_trend_pullback.json").read_text(encoding="utf-8"))


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

    def test_rank_by_prefers_strongest(self):
        """With one slot and two simultaneous signals, the higher-ranked symbol must be bought."""
        idx = pd.bdate_range("2020-01-01", periods=60)
        def frame(drift):
            c = 100 * (1 + drift) ** np.arange(60)
            return pd.DataFrame({"open": c, "high": c * 1.001, "low": c * 0.999, "close": c, "volume": 1e6}, index=idx)
        data = {"SLOW": frame(0.001), "FAST": frame(0.01)}
        spec = {"direction": "long", "params": {"max_hold_bars": 250, "target_atr": 20, "stop_atr": 10},
                "entry": {"all": [{"left": "roc(20)", "op": ">", "right": 0}]}, "rank_by": "roc(20)"}
        bt = dict(self.cfg["backtest"], max_positions=1)
        r = run_backtest(spec, data, bt)
        self.assertEqual(r.trades.iloc[0].symbol, "FAST")

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


    def test_no_trades_no_nan(self):
        from sentinel.metrics import perf_metrics
        eq = pd.Series(100000.0, index=pd.bdate_range("2020-01-01", periods=300))
        m = perf_metrics(eq)
        d = deflated_sharpe(m["sr_period"], m["n_days"], m["skew"], m["kurtosis"], 10, 0.001)
        self.assertEqual(d, 0.0)


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


class TestTicketIsLive(unittest.TestCase):
    def test_ticket_mode_needs_live_approval(self):
        import tempfile
        from sentinel.brokers.ticket import TicketBroker
        from sentinel.execution import run_once
        from sentinel.memory import Memory
        cfg = load_settings(); cfg["state_dir"] = tempfile.mkdtemp()
        mem, risk = Memory(cfg["state_dir"]), RiskEngine(cfg["state_dir"])
        spec = validate_spec(BASE); h = spec_hash(spec)
        mem.record(h, spec, {"passed": True}, "promoted"); mem.set_champion("paper", h)
        out = run_once(cfg, TicketBroker(cfg), mem, risk, synthetic(["AAA"], "2020-01-01"), log=lambda *a: None)
        self.assertIn("error", out)  # a paper-only strategy must not produce real-money tickets


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


class TestLivePlan(unittest.TestCase):
    def setUp(self):
        from sentinel.live_plan import signals_table
        self.cfg = load_settings("config/settings_etf.yaml")
        spec = json.loads((ROOT / "strategies/trend_momentum.json").read_text(encoding="utf-8"))
        self.sig = signals_table(spec, synthetic(self.cfg["universe"], "2018-01-01"))
        self.today = date.fromisoformat(self.sig["as_of"])

    def plan(self, **acct):
        from sentinel.live_plan import make_plan
        a = {"value": 1000.0, "cash": 1000.0, "positions": {}, "stops": {}, "open_buys": []}
        a.update(acct)
        return make_plan(self.sig, a, self.cfg, cap=1000, peak=1000, today=self.today)

    def test_sizing_respects_caps(self):
        p = self.plan()
        for b in p["buys"]:
            self.assertLessEqual(b["qty"] * b["ref_close"], 1000 * 0.20 + 1e-6)   # 20% per position
            self.assertLessEqual(b["risk_at_stop"], 1000 * 0.01 + 1e-6)          # 1% risk
        self.assertLessEqual(len(p["buys"]), 5)
        self.assertGreaterEqual(p["sizing"]["cash_after_buys"], 0)

    def test_cap_limits_big_accounts(self):
        a, b = self.plan(), self.plan(value=50000.0, cash=50000.0)
        self.assertEqual([x["qty"] for x in a["buys"]], [x["qty"] for x in b["buys"]])

    def test_drawdown_halts_buys_but_not_sells(self):
        held = next(s for s, r in self.sig["symbols"].items() if r["exit"]) if any(
            r["exit"] for r in self.sig["symbols"].values()) else None
        pos = {held: {"qty": 1, "avg_price": 10}} if held else {}
        p = self.plan(value=800.0, cash=800.0, positions=pos)
        self.assertTrue(p["halts"]); self.assertFalse(p["buys"])
        if held:
            self.assertEqual(p["sells"][0]["symbol"], held)

    def test_missing_stop_is_flagged(self):
        s = next(iter(self.sig["symbols"]))
        p = self.plan(positions={s: {"qty": 1, "avg_price": self.sig["symbols"][s]["close"]}})
        self.assertTrue(any(x["symbol"] == s for x in p["add_stops"]) or any(x["symbol"] == s for x in p["sells"]))

    def test_stale_signals_halt(self):
        from sentinel.live_plan import make_plan
        a = {"value": 1000.0, "cash": 1000.0, "positions": {}, "stops": {}}
        p = make_plan(self.sig, a, self.cfg, cap=1000, peak=1000, today=self.today + timedelta(days=30))
        self.assertTrue(p["halts"]); self.assertFalse(p["buys"])
