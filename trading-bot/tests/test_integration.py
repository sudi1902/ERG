"""End-to-end checks: the backtester, the CLI, and the live runner's plumbing."""

import contextlib
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import frictionless_config, make_bars
from tests.test_engine import ListFeed
from tradingbot.backtest import run_backtest
from tradingbot.broker import Account, Broker, BrokerPosition, PaperBroker
from tradingbot.cli import main
from tradingbot.config import Config
from tradingbot.datafeed import SyntheticFeed
from tradingbot.live import LiveRunner, scan
from tradingbot.portfolio import Portfolio, Side

SYNTH_START = dt.date(2022, 1, 1)
SYNTH_END = dt.date(2025, 1, 1)


def synthetic_config(**overrides) -> Config:
    base = {
        "symbols": ["AAA", "BBB", "CCC"],
        "strategy": "momentum",
        "starting_equity": 50_000.0,
        "data": {"provider": "synthetic", "timeframe": "1d"},
    }
    base.update(overrides)
    return Config.from_dict(base)


class TestBacktestBehaviour(unittest.TestCase):
    def test_is_deterministic(self):
        runs = [
            run_backtest(synthetic_config(), feed=SyntheticFeed(), start=SYNTH_START, end=SYNTH_END)
            for _ in range(2)
        ]
        self.assertEqual(
            [round(r.portfolio.equity, 6) for r in runs[:1]],
            [round(r.portfolio.equity, 6) for r in runs[1:]],
        )
        self.assertEqual(len(runs[0].portfolio.trades), len(runs[1].portfolio.trades))

    def test_costs_can_only_reduce_the_result(self):
        free = run_backtest(
            synthetic_config(costs={"slippage_bps": 0.0, "spread_bps": 0.0}),
            feed=SyntheticFeed(), start=SYNTH_START, end=SYNTH_END,
        )
        costly = run_backtest(
            synthetic_config(costs={"slippage_bps": 25.0, "spread_bps": 10.0, "commission_per_share": 0.02}),
            feed=SyntheticFeed(), start=SYNTH_START, end=SYNTH_END,
        )
        self.assertLess(costly.portfolio.equity, free.portfolio.equity)

    def test_ends_flat_with_a_full_equity_curve(self):
        result = run_backtest(synthetic_config(), feed=SyntheticFeed(), start=SYNTH_START, end=SYNTH_END)
        self.assertEqual(len(result.portfolio.positions), 0)
        self.assertGreater(len(result.portfolio.equity_curve), 200)

    def test_warns_when_history_is_shorter_than_the_warmup(self):
        result = run_backtest(
            synthetic_config(), feed=SyntheticFeed(),
            start=dt.date(2024, 11, 1), end=dt.date(2024, 12, 1),
        )
        self.assertTrue(any("warmup" in w for w in result.warnings))
        self.assertEqual(len(result.portfolio.trades), 0)

    def test_missing_symbols_are_reported_not_crashed_on(self):
        bars = make_bars("TEST", [100.0 + i for i in range(120)])
        cfg = frictionless_config(symbols=["TEST", "GHOST"])
        result = run_backtest(
            cfg, feed=ListFeed({"TEST": bars}), start=bars[0].ts.date(), end=bars[-1].ts.date()
        )
        self.assertTrue(any("GHOST" in w for w in result.warnings))

    def test_risk_per_trade_scales_position_size(self):
        small = run_backtest(
            synthetic_config(risk={"risk_per_trade_pct": 0.25}),
            feed=SyntheticFeed(), start=SYNTH_START, end=SYNTH_END,
        )
        large = run_backtest(
            synthetic_config(risk={"risk_per_trade_pct": 2.0}),
            feed=SyntheticFeed(), start=SYNTH_START, end=SYNTH_END,
        )
        small_notional = sum(t.shares * t.entry_price for t in small.portfolio.trades)
        large_notional = sum(t.shares * t.entry_price for t in large.portfolio.trades)
        self.assertGreater(large_notional, small_notional * 3)

    def test_a_loss_limit_halt_flattens_the_book(self):
        # A tiny loss limit trips almost immediately once anything is held.
        result = run_backtest(
            synthetic_config(risk={"max_daily_loss_pct": 0.01, "risk_per_trade_pct": 2.0}),
            feed=SyntheticFeed(), start=SYNTH_START, end=SYNTH_END,
        )
        halts = [e for e in result.events if e.kind == "halt"]
        self.assertTrue(halts, "a 0.01% daily loss limit must trip")
        for event in halts:
            self.assertIn("flattening", event.message)
        closes = [t for t in result.portfolio.trades if t.exit_reason == "daily_loss_limit"]
        self.assertTrue(closes)


class TestIntradaySessions(unittest.TestCase):
    def test_flat_at_close_leaves_nothing_open_overnight(self):
        cfg = Config.from_dict({
            "symbols": ["AAA", "BBB"],
            "strategy": "orb",
            "starting_equity": 50_000.0,
            "data": {"provider": "synthetic", "timeframe": "5m"},
            "risk": {"flat_at_close": True, "max_daily_loss_pct": 100.0, "daily_profit_target_pct": 100.0},
        })
        result = run_backtest(
            cfg, feed=SyntheticFeed(), start=dt.date(2025, 9, 1), end=dt.date(2025, 10, 15)
        )
        overnight = [
            t for t in result.portfolio.trades if t.entry_ts.date() != t.exit_ts.date()
        ]
        self.assertEqual(overnight, [], "flat_at_close must not leave positions overnight")
        self.assertEqual(len(result.portfolio.positions), 0)

    def test_orb_actually_trades_on_intraday_bars(self):
        cfg = Config.from_dict({
            "symbols": ["AAA", "BBB", "CCC"],
            "strategy": "orb",
            "starting_equity": 50_000.0,
            "data": {"provider": "synthetic", "timeframe": "5m"},
            "risk": {"max_daily_loss_pct": 100.0, "daily_profit_target_pct": 100.0, "max_trades_per_day": 50},
        })
        result = run_backtest(
            cfg, feed=SyntheticFeed(), start=dt.date(2025, 9, 1), end=dt.date(2025, 10, 15)
        )
        self.assertGreater(len(result.portfolio.trades), 0)


class TestScan(unittest.TestCase):
    def test_scan_trades_nothing_and_reports_intents(self):
        cfg = synthetic_config()
        core, pending = scan(cfg, feed=SyntheticFeed(), end=SYNTH_END)
        self.assertIsInstance(pending, list)
        for order in pending:
            self.assertIn(order.action, {"enter", "exit"})
            self.assertIn(order.symbol, cfg.symbols)


class StubBroker(Broker):
    name = "stub"

    def __init__(self, positions=None):
        self._positions = positions or []
        self.submitted = []
        self.closed = []

    def account(self):
        return Account(equity=50_000.0, cash=50_000.0, buying_power=50_000.0)

    def positions(self):
        return self._positions

    def market_open(self):
        return True

    def submit(self, *, symbol, side, shares, stop=None, target=None):
        from tradingbot.broker import OrderResult

        self.submitted.append((symbol, side, shares, stop, target))
        return OrderResult(True, "stub", "ok")

    def close(self, symbol):
        from tradingbot.broker import OrderResult

        self.closed.append(symbol)
        return OrderResult(True, "stub", "ok")


class TestLiveRunner(unittest.TestCase):
    def _runner(self, bars, broker=None, **cfg_overrides):
        cfg = frictionless_config(**cfg_overrides)
        return LiveRunner(
            cfg,
            feed=ListFeed({"TEST": bars}),
            broker=broker or PaperBroker(Portfolio(cfg.starting_equity)),
            verbose=False,
        )

    def test_warmup_leaves_the_account_untouched_but_history_warm(self):
        bars = make_bars("TEST", [100.0 + i for i in range(150)], start=dt.datetime(2026, 1, 5, 16, 0))
        runner = self._runner(bars)
        runner.warmup(end=bars[-1].ts.date())
        self.assertEqual(len(runner.portfolio.trades), 0)
        self.assertAlmostEqual(runner.portfolio.equity, runner.cfg.starting_equity)
        self.assertEqual(runner.core.events, [])
        self.assertGreater(len(runner.core.series["TEST"]), 100)

    def test_a_second_poll_with_no_new_bars_does_nothing(self):
        bars = make_bars("TEST", [100.0 + i for i in range(150)], start=dt.datetime(2026, 1, 5, 16, 0))
        runner = self._runner(bars)
        runner.warmup(end=bars[-1].ts.date())
        self.assertEqual(runner.poll_once(now=dt.datetime.combine(bars[-1].ts.date(), dt.time(16, 30))), [])

    def test_reconcile_refuses_to_touch_positions_it_did_not_open(self):
        bars = make_bars("TEST", [100.0 + i for i in range(30)])
        broker = StubBroker([
            BrokerPosition("TEST", Side.LONG, 100, 90.0, 10_000.0, 500.0)
        ])
        runner = self._runner(bars, broker=broker)
        notes = runner.reconcile()
        self.assertIn("TEST", runner.state.blocked)
        self.assertTrue(any("did not open" in note for note in notes))

    def test_paper_broker_records_orders_without_side_effects(self):
        broker = PaperBroker(Portfolio(10_000.0))
        broker.submit(symbol="X", side=Side.LONG, shares=10, stop=90.0, target=110.0)
        self.assertEqual(len(broker.orders), 1)
        self.assertAlmostEqual(broker.account().equity, 10_000.0)


class TestCli(unittest.TestCase):
    def _run(self, argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def test_reality_check_states_the_compounding_plainly(self):
        code, out = self._run(["reality-check", "--target", "15"])
        self.assertEqual(code, 0)
        self.assertIn("REALITY CHECK", out)
        self.assertIn("e+15", out, "the annual multiple must be shown, not softened")
        self.assertIn("risk setting, not a", out)

    def test_reality_check_is_encouraging_for_a_sane_target(self):
        _, out = self._run(["reality-check", "--target", "0.05"])
        self.assertIn("ambitious but not absurd", out)

    def test_strategies_lists_all_three(self):
        code, out = self._run(["strategies"])
        self.assertEqual(code, 0)
        for name in ("momentum", "meanrev", "orb"):
            self.assertIn(name, out)

    def test_backtest_runs_and_reports(self):
        code, out = self._run([
            "backtest", "--provider", "synthetic", "--symbols", "AAA,BBB",
            "--start", "2023-01-01", "--end", "2025-01-01",
        ])
        self.assertEqual(code, 0)
        self.assertIn("BACKTEST RESULT", out)
        self.assertIn("Max drawdown", out)
        self.assertIn("DAILY TARGET CHECK", out)
        self.assertIn("not a prediction", out)

    def test_backtest_writes_machine_readable_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            code, _ = self._run([
                "backtest", "--provider", "synthetic", "--symbols", "AAA",
                "--start", "2023-01-01", "--end", "2025-01-01", "--json", str(path),
            ])
            self.assertEqual(code, 0)
            payload = json.loads(path.read_text())
            self.assertIn("metrics", payload)
            self.assertIn("trades", payload)
            self.assertIn("total_return_pct", payload["metrics"])

    def test_live_is_refused_without_the_acknowledgement(self):
        code, out = self._run(["live", "--provider", "synthetic"])
        self.assertEqual(code, 2)
        self.assertIn("--i-understand-the-risk", out)

    def test_live_is_still_refused_when_the_broker_is_paper(self):
        code, out = self._run(["live", "--provider", "synthetic", "--i-understand-the-risk"])
        self.assertEqual(code, 2)
        self.assertIn("broker.name", out)

    def test_init_writes_a_valid_config_and_refuses_to_clobber(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            self.assertEqual(self._run(["init", str(path)])[0], 0)
            Config.from_dict(json.loads(path.read_text()))  # must round-trip
            self.assertEqual(self._run(["init", str(path)])[0], 1, "no silent overwrite")
            self.assertEqual(self._run(["init", str(path), "--force"])[0], 0)

    def test_bad_dates_are_reported_not_traced(self):
        code, out = self._run(["backtest", "--provider", "synthetic", "--start", "yesterday"])
        self.assertEqual(code, 1)
        self.assertIn("error:", out)

    def test_scan_reports_intents(self):
        code, out = self._run([
            "scan", "--provider", "synthetic", "--symbols", "AAA,BBB", "--end", "2025-01-01",
        ])
        self.assertEqual(code, 0)
        self.assertIn("SCAN", out)
        self.assertIn("next open", out)

    def test_cli_overrides_reach_the_config(self):
        code, out = self._run([
            "backtest", "--provider", "synthetic", "--symbols", "AAA",
            "--start", "2023-01-01", "--end", "2025-01-01",
            "--equity", "12345", "--daily-target", "3",
        ])
        self.assertEqual(code, 0)
        self.assertIn("$12,345.00", out)
        self.assertIn("3.00% PER DAY", out)


if __name__ == "__main__":
    unittest.main()
