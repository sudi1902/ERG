"""The tests that matter most: no lookahead, honest fills, correct sequencing."""

import datetime as dt
import unittest

from tests.helpers import frictionless_config, make_bars
from tradingbot.backtest import build_timeline, run_backtest
from tradingbot.bars import Bar
from tradingbot.datafeed import Feed
from tradingbot.engine import TradingCore
from tradingbot.portfolio import Portfolio, Side
from tradingbot.strategies import Signal, Strategy


class ListFeed(Feed):
    """Serves a fixed dict of bars, so tests never touch the network."""

    def __init__(self, history):
        self.data = history

    def history(self, symbols, *, start, end, timeframe="1d"):
        return {
            s: [b for b in self.data[s] if start <= b.ts.date() <= end]
            for s in symbols
            if s in self.data
        }


class EnterOnBar(Strategy):
    """Signals a long exactly once, on the Nth bar it sees."""

    name = "test_enter_on_bar"
    warmup = 1

    @classmethod
    def defaults(cls):
        return {"at_index": 20, "side": "long", "stop": None, "stop_frac": 0.03, "target": None}

    def configure(self):
        self.seen = 0
        self.fired = False

    def generate(self, symbol, series, ctx):
        self.seen += 1
        if self.fired or self.seen != self.p("at_index"):
            return []
        self.fired = True
        side = Side.LONG if self.p("side") == "long" else Side.SHORT
        # A stop is a price, not a distance, and the risk layer refuses any
        # sitting more than 25% away - so derive it from the latest close.
        stop = self.p("stop")
        if stop is None:
            move = 1 - self.p("stop_frac") if side is Side.LONG else 1 + self.p("stop_frac")
            stop = series.last.close * move
        return [Signal(symbol, "enter", side, "test", stop=stop, target=self.p("target"))]


class NeverTrades(Strategy):
    name = "test_never"
    warmup = 1

    def generate(self, symbol, series, ctx):
        return []


class Exploding(Strategy):
    name = "test_boom"
    warmup = 1

    def generate(self, symbol, series, ctx):
        raise RuntimeError("strategy bug")


class TestNoLookahead(unittest.TestCase):
    def test_signal_fills_at_the_next_bar_open_not_this_close(self):
        # Closes rise steadily; each bar opens at the previous close. A signal
        # raised on bar 20 must fill at bar 21's open, which equals close 20.
        closes = [100.0 + i for i in range(40)]
        bars = make_bars("TEST", closes)
        cfg = frictionless_config()
        result = run_backtest(
            cfg,
            feed=ListFeed({"TEST": bars}),
            start=bars[0].ts.date(),
            end=bars[-1].ts.date(),
            strategy=EnterOnBar(at_index=20),
        )
        entries = [e for e in result.events if e.kind == "entry"]
        self.assertEqual(len(entries), 1)
        signal_bar = bars[19]
        fill_bar = bars[20]
        self.assertEqual(entries[0].ts, fill_bar.ts, "fill must land on the bar after the signal")
        self.assertAlmostEqual(entries[0].detail["price"], fill_bar.open)
        self.assertAlmostEqual(fill_bar.open, signal_bar.close, msg="fixture assumption")

    def test_a_strategy_never_sees_a_bar_before_it_closes(self):
        seen: list[tuple[dt.datetime, int]] = []

        class Recorder(Strategy):
            name = "rec"
            warmup = 1

            def generate(self, symbol, series, ctx):
                seen.append((ctx.now, len(series)))
                self.testcase_last_close = series.last.close
                return []

        bars = make_bars("TEST", [100.0 + i for i in range(10)])
        core = TradingCore(frictionless_config(), Recorder())
        for bar in bars:
            core.process(bar.ts, [bar], session_end=True)
        # On the Nth call the strategy must see exactly N bars - never N+1.
        for index, (ts, count) in enumerate(seen, start=1):
            self.assertEqual(count, index)
            self.assertEqual(ts, bars[index - 1].ts)


class TestExitSequencing(unittest.TestCase):
    def _core_with_position(self, *, stop, target, side=Side.LONG):
        cfg = frictionless_config()
        core = TradingCore(cfg, NeverTrades(), portfolio=Portfolio(100_000))
        core.portfolio.open_position(
            symbol="TEST",
            side=side,
            shares=100,
            price=100.0,
            ts=dt.datetime(2026, 1, 5, 16, 0),
            stop=stop,
            target=target,
            trail_mult=0.0,
            atr=1.0,
            commission=0.0,
            reason="fixture",
        )
        core.series["TEST"].append(
            Bar("TEST", dt.datetime(2026, 1, 5, 16, 0), 100, 100, 100, 100, 1000)
        )
        return core

    def test_stop_wins_when_one_bar_could_have_hit_both(self):
        core = self._core_with_position(stop=95.0, target=105.0)
        # This bar spans both levels. Without tick data the pessimistic read is
        # the only defensible one.
        bar = Bar("TEST", dt.datetime(2026, 1, 6, 16, 0), open=100, high=106, low=94, close=105, volume=1000)
        core.process(bar.ts, [bar], session_end=True)
        trade = core.portfolio.trades[0]
        self.assertEqual(trade.exit_reason, "stop")
        self.assertAlmostEqual(trade.exit_price, 95.0)

    def test_gap_through_the_stop_fills_at_the_open_and_loses_more(self):
        core = self._core_with_position(stop=95.0, target=None)
        bar = Bar("TEST", dt.datetime(2026, 1, 6, 16, 0), open=88, high=90, low=86, close=89, volume=1000)
        core.process(bar.ts, [bar], session_end=True)
        trade = core.portfolio.trades[0]
        self.assertAlmostEqual(trade.exit_price, 88.0, msg="a gap does not honour the stop price")
        self.assertLess(trade.pnl, -500.0)

    def test_target_fills_when_only_the_target_is_touched(self):
        core = self._core_with_position(stop=95.0, target=105.0)
        bar = Bar("TEST", dt.datetime(2026, 1, 6, 16, 0), open=100, high=106, low=99, close=105, volume=1000)
        core.process(bar.ts, [bar], session_end=True)
        self.assertEqual(core.portfolio.trades[0].exit_reason, "target")

    def test_position_opened_this_bar_is_still_exposed_to_the_rest_of_it(self):
        closes = [100.0 + i for i in range(25)]
        bars = make_bars("TEST", closes)
        # Give bar 21 a deep low so the stop placed at its open is hit at once.
        crash = bars[20]
        bars[20] = Bar("TEST", crash.ts, crash.open, crash.high, crash.open * 0.5, crash.close, crash.volume)
        result = run_backtest(
            frictionless_config(),
            feed=ListFeed({"TEST": bars}),
            start=bars[0].ts.date(),
            end=bars[-1].ts.date(),
            strategy=EnterOnBar(at_index=20, stop=bars[20].open * 0.9),
        )
        self.assertEqual(len(result.portfolio.trades), 1)
        trade = result.portfolio.trades[0]
        self.assertEqual(trade.entry_ts, trade.exit_ts, "entry and stop on the same bar")
        self.assertEqual(trade.exit_reason, "stop")


class TestCoreBehaviour(unittest.TestCase):
    def test_a_raising_strategy_is_contained(self):
        bars = make_bars("TEST", [100.0 + i for i in range(5)])
        core = TradingCore(frictionless_config(), Exploding())
        for bar in bars:
            core.process(bar.ts, [bar], session_end=True)
        self.assertTrue(any("strategy raised RuntimeError" in e.message for e in core.events))
        self.assertEqual(len(core.portfolio.positions), 0)

    def test_warmup_replay_books_no_trades(self):
        bars = make_bars("TEST", [100.0 + i for i in range(40)])
        core = TradingCore(frictionless_config(), EnterOnBar(at_index=20, stop=1.0))
        core.trading_enabled = False
        for bar in bars:
            core.process(bar.ts, [bar], session_end=True)
        self.assertEqual(len(core.portfolio.trades), 0)
        self.assertEqual(len(core.portfolio.positions), 0)
        self.assertEqual(len(core.series["TEST"]), len(bars), "history still warms")

    def test_run_ends_flat_so_pnl_is_fully_realised(self):
        result = run_backtest(
            frictionless_config(),
            feed=ListFeed({"TEST": make_bars("TEST", [100.0 + i for i in range(40)])}),
            start=dt.date(2026, 1, 1),
            end=dt.date(2026, 4, 1),
            strategy=EnterOnBar(at_index=20),
        )
        self.assertEqual(len(result.portfolio.positions), 0)
        self.assertAlmostEqual(
            result.portfolio.equity,
            result.portfolio.cash,
            msg="a flat book's equity is just cash",
        )

    def test_timeline_marks_the_last_bar_of_each_day(self):
        bars = (
            make_bars("A", [10, 11], start=dt.datetime(2026, 1, 5, 9, 35), daily=False)
            + make_bars("A", [12, 13], start=dt.datetime(2026, 1, 6, 9, 35), daily=False)
        )
        timeline = build_timeline({"A": bars})
        flags = [session_end for _, _, session_end in timeline]
        self.assertEqual(flags, [False, True, False, True])


if __name__ == "__main__":
    unittest.main()
