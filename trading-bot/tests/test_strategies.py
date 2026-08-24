import datetime as dt
import unittest

from tests.helpers import make_bars
from tradingbot.bars import Bar, Series
from tradingbot.portfolio import Portfolio, Side
from tradingbot.strategies import Context, Signal, available, get_strategy


def context_for(series, *, minutes=None, portfolio=None, session=None):
    return Context(
        now=series.last.ts,
        session=session or series.last.ts.date(),
        series={series.symbol: series},
        portfolio=portfolio or Portfolio(100_000.0),
        updated=[series.symbol],
        minutes_into_session=minutes,
    )


def series_from(bars):
    series = Series(bars[0].symbol)
    for bar in bars:
        series.append(bar)
    return series


class TestRegistry(unittest.TestCase):
    def test_the_three_shipped_strategies_are_registered(self):
        self.assertEqual(available(), ["meanrev", "momentum", "orb"])

    def test_unknown_strategy_names_list_the_alternatives(self):
        with self.assertRaises(KeyError) as ctx:
            get_strategy("nope")
        self.assertIn("momentum", str(ctx.exception))

    def test_unknown_params_are_refused_with_the_valid_list(self):
        with self.assertRaises(ValueError) as ctx:
            get_strategy("momentum", {"fastt": 5})
        self.assertIn("fast", str(ctx.exception))

    def test_params_override_defaults(self):
        self.assertEqual(get_strategy("momentum", {"fast": 5}).p("fast"), 5)

    def test_describe_is_stable_and_readable(self):
        self.assertIn("momentum(", get_strategy("momentum").describe())

    def test_every_strategy_declares_a_warmup_and_stays_silent_below_it(self):
        for name in available():
            strategy = get_strategy(name)
            self.assertGreater(strategy.warmup, 0, name)
            short = series_from(make_bars("X", [100.0, 101.0, 102.0]))
            self.assertEqual(strategy.on_bar(context_for(short)), [], name)


class TestMomentum(unittest.TestCase):
    def setUp(self):
        self.strategy = get_strategy("momentum", {"min_roc_pct": 0.1, "min_atr_pct": 0.0})

    def test_goes_long_when_a_steady_uptrend_crosses_up(self):
        # Flat, then a sustained rise: the EMAs cross up inside an uptrend.
        closes = [100.0] * 120 + [100.0 + i * 1.5 for i in range(1, 40)]
        signals = self._signals_over(closes)
        longs = [s for s in signals if s.action == "enter" and s.side is Side.LONG]
        self.assertTrue(longs, "a clean uptrend should produce a long")

    def test_goes_short_when_a_steady_downtrend_crosses_down(self):
        closes = [200.0] * 120 + [200.0 - i * 1.5 for i in range(1, 40)]
        signals = self._signals_over(closes)
        shorts = [s for s in signals if s.action == "enter" and s.side is Side.SHORT]
        self.assertTrue(shorts, "a clean downtrend should produce a short")

    def test_stays_out_of_a_flat_market(self):
        signals = self._signals_over([100.0] * 200)
        self.assertEqual([s for s in signals if s.action == "enter"], [])

    def test_quiet_names_are_filtered_out_by_min_atr(self):
        picky = get_strategy("momentum", {"min_roc_pct": 0.1, "min_atr_pct": 99.0})
        closes = [100.0] * 120 + [100.0 + i * 1.5 for i in range(1, 40)]
        series = Series("X")
        found = []
        for bar in make_bars("X", closes):
            series.append(bar)
            found.extend(picky.on_bar(context_for(series)))
        self.assertEqual([s for s in found if s.action == "enter"], [])

    def _signals_over(self, closes):
        series = Series("X")
        out: list[Signal] = []
        for bar in make_bars("X", closes):
            series.append(bar)
            out.extend(self.strategy.on_bar(context_for(series)))
        return out


class TestMeanReversion(unittest.TestCase):
    def _pullback(self, depth):
        """A long uptrend followed by a `depth` pullback over three bars."""
        closes = [100.0 + i * 0.5 for i in range(220)]
        top = closes[-1]
        return closes + [top * (1 - depth * f) for f in (0.4, 0.7, 1.0)]

    def _longs_for(self, closes, **params):
        strategy = get_strategy("meanrev", {"regime": 50, "lookback": 20, "entry_z": 1.5, **params})
        series = Series("X")
        signals = []
        for bar in make_bars("X", closes):
            series.append(bar)
            signals.extend(strategy.on_bar(context_for(series)))
        return [s for s in signals if s.action == "enter" and s.side is Side.LONG]

    def test_buys_a_moderate_dip_inside_an_uptrend(self):
        self.assertTrue(
            self._longs_for(self._pullback(0.05), rsi_low=45.0),
            "a stretched but survivable dip in an uptrend is the setup this strategy exists for",
        )

    def test_ignores_a_shallow_wobble(self):
        self.assertEqual(
            self._longs_for(self._pullback(0.02), rsi_low=45.0), [],
            "a 2% dip is not stretched enough to be worth the risk",
        )

    def test_a_collapse_deep_enough_to_break_the_trend_is_refused(self):
        # The same setup, only deeper: price falls through the regime filter and
        # the strategy stands down. This is the line between a dip and a knife.
        self.assertEqual(
            self._longs_for(self._pullback(0.10), rsi_low=45.0), [],
            "once price loses the regime filter it is no longer a dip in an uptrend",
        )

    def test_does_not_buy_a_dip_below_the_regime_filter(self):
        strategy = get_strategy("meanrev", {"regime": 50, "lookback": 20, "entry_z": 1.5})
        closes = [300.0 - i * 0.8 for i in range(220)]  # sustained downtrend
        closes += [closes[-1] * 0.9, closes[-1] * 0.85]
        series = Series("X")
        signals = []
        for bar in make_bars("X", closes):
            series.append(bar)
            signals.extend(strategy.on_bar(context_for(series)))
        self.assertEqual(
            [s for s in signals if s.action == "enter" and s.side is Side.LONG],
            [],
            "catching a falling knife is exactly what the regime filter blocks",
        )


class TestOpeningRangeBreakout(unittest.TestCase):
    def setUp(self):
        self.strategy = get_strategy("orb", {"range_minutes": 30, "volume_mult": 0.0, "min_range_pct": 0.05})

    def _session(self, closes, *, day=dt.date(2026, 1, 5)):
        series = Series("X")
        ts = dt.datetime.combine(day, dt.time(9, 35))
        bars = []
        for index, close in enumerate(closes):
            open_price = closes[index - 1] if index else close
            bars.append(
                Bar("X", ts, open_price, max(open_price, close) + 0.05,
                    min(open_price, close) - 0.05, close, 100_000)
            )
            ts += dt.timedelta(minutes=5)
        for bar in bars:
            series.append(bar)
        return series, bars

    def test_silent_on_daily_bars_because_there_is_no_opening_range(self):
        series = series_from(make_bars("X", [100.0 + i for i in range(60)]))
        self.assertEqual(self.strategy.on_bar(context_for(series, minutes=None)), [])

    def test_silent_while_the_range_is_still_forming(self):
        series, _ = self._session([100.0, 100.5, 101.0])
        self.assertEqual(self.strategy.on_bar(context_for(series, minutes=15)), [])

    def test_breaks_out_above_the_opening_range_with_a_structural_stop(self):
        # Six bars build a 100-101 range, then price breaks out above it.
        closes = [100.0, 100.5, 101.0, 100.2, 100.8, 100.4, 102.5]
        series, _ = self._session(closes)
        signals = self.strategy.on_bar(context_for(series, minutes=35))
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.side, Side.LONG)
        self.assertIsNotNone(signal.stop)
        self.assertLess(signal.stop, 100.5, "the stop is the far side of the range, not an ATR guess")

    def test_breaks_down_below_the_opening_range(self):
        closes = [100.0, 100.5, 101.0, 100.2, 100.8, 100.4, 98.0]
        series, _ = self._session(closes)
        signals = self.strategy.on_bar(context_for(series, minutes=35))
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].side, Side.SHORT)
        self.assertGreater(signals[0].stop, 100.5)

    def test_no_entry_late_in_the_session(self):
        closes = [100.0, 100.5, 101.0, 100.2, 100.8, 100.4, 102.5]
        series, _ = self._session(closes)
        self.assertEqual(self.strategy.on_bar(context_for(series, minutes=380)), [])

    def test_thin_ranges_are_skipped(self):
        picky = get_strategy("orb", {"range_minutes": 30, "volume_mult": 0.0, "min_range_pct": 50.0})
        closes = [100.0, 100.5, 101.0, 100.2, 100.8, 100.4, 102.5]
        series, _ = self._session(closes)
        self.assertEqual(picky.on_bar(context_for(series, minutes=35)), [])


class TestSignalValidation(unittest.TestCase):
    def test_unknown_actions_are_rejected(self):
        with self.assertRaises(ValueError):
            Signal("X", "sell_everything", Side.LONG, "nope")


if __name__ == "__main__":
    unittest.main()
