import datetime as dt
import tempfile
import unittest
from pathlib import Path

from tradingbot.config import DataConfig
from tradingbot.datafeed import (
    CsvFeed,
    DataError,
    StooqFeed,
    SyntheticFeed,
    build_feed,
    is_intraday,
    timeframe_minutes,
)


class TestTimeframes(unittest.TestCase):
    def test_known_timeframes(self):
        self.assertEqual(timeframe_minutes("5m"), 5)
        self.assertEqual(timeframe_minutes("1h"), 60)
        self.assertIsNone(timeframe_minutes("1d"))
        self.assertTrue(is_intraday("15m"))
        self.assertFalse(is_intraday("1d"))

    def test_unknown_timeframe_is_rejected(self):
        with self.assertRaises(DataError):
            timeframe_minutes("3w")


class TestSyntheticFeed(unittest.TestCase):
    def setUp(self):
        self.feed = SyntheticFeed()
        self.start = dt.date(2024, 1, 1)
        self.end = dt.date(2025, 12, 31)

    def test_is_deterministic_across_instances(self):
        a = SyntheticFeed(seed=3).history(["X"], start=self.start, end=self.end)["X"]
        b = SyntheticFeed(seed=3).history(["X"], start=self.start, end=self.end)["X"]
        self.assertEqual([bar.close for bar in a], [bar.close for bar in b])

    def test_different_seeds_diverge(self):
        a = SyntheticFeed(seed=1).history(["X"], start=self.start, end=self.end)["X"]
        b = SyntheticFeed(seed=2).history(["X"], start=self.start, end=self.end)["X"]
        self.assertNotEqual([bar.close for bar in a], [bar.close for bar in b])

    def test_bars_are_well_formed_and_ordered(self):
        bars = self.feed.history(["X"], start=self.start, end=self.end)["X"]
        self.assertGreater(len(bars), 400)
        for previous, current in zip(bars, bars[1:], strict=False):
            self.assertLess(previous.ts, current.ts)
        for bar in bars:
            self.assertGreaterEqual(bar.high, max(bar.open, bar.close))
            self.assertLessEqual(bar.low, min(bar.open, bar.close))
            self.assertGreater(bar.low, 0)

    def test_skips_weekends(self):
        for bar in self.feed.history(["X"], start=self.start, end=self.end)["X"]:
            self.assertLess(bar.ts.weekday(), 5)

    def test_prices_stay_in_a_plausible_range(self):
        # A generator that compounds away to 1e6 makes every backtest on it
        # meaningless, so this guards the drift parameters.
        for symbol in ("A", "B", "C", "D", "E", "F"):
            bars = self.feed.history([symbol], start=self.start, end=self.end)[symbol]
            ratio = bars[-1].close / bars[0].close
            self.assertTrue(0.1 < ratio < 10.0, f"{symbol} moved {ratio:.1f}x over two years")

    def test_intraday_bars_land_inside_the_session(self):
        bars = self.feed.history(
            ["X"], start=dt.date(2026, 1, 5), end=dt.date(2026, 1, 9), timeframe="5m"
        )["X"]
        self.assertTrue(bars)
        for bar in bars:
            self.assertGreaterEqual(bar.ts.time(), dt.time(9, 30))
            self.assertLessEqual(bar.ts.time(), dt.time(16, 0))


class TestCsvFeed(unittest.TestCase):
    def test_reads_a_well_formed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "ABC.csv").write_text(
                "Date,Open,High,Low,Close,Volume\n"
                "2026-01-05,100,102,99,101,1000\n"
                "2026-01-06,101,104,100,103,1200\n"
            )
            bars = CsvFeed(tmp).history(["ABC"], start=dt.date(2026, 1, 1), end=dt.date(2026, 1, 31))["ABC"]
            self.assertEqual(len(bars), 2)
            self.assertAlmostEqual(bars[1].close, 103.0)

    def test_skips_malformed_rows_rather_than_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "ABC.csv").write_text(
                "Date,Open,High,Low,Close,Volume\n"
                "2026-01-05,100,102,99,101,1000\n"
                "not-a-date,x,y,z,w,v\n"
                "2026-01-06,101,104,100,103,1200\n"
            )
            bars = CsvFeed(tmp).history(["ABC"], start=dt.date(2026, 1, 1), end=dt.date(2026, 1, 31))["ABC"]
            self.assertEqual(len(bars), 2)

    def test_missing_files_raise_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(DataError):
            CsvFeed(tmp).history(["NOPE"], start=dt.date(2026, 1, 1), end=dt.date(2026, 1, 31))


class TestStooqParsing(unittest.TestCase):
    def test_parses_the_csv_shape_stooq_returns(self):
        text = (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-01-05,100.0,102.0,99.0,101.0,1000000\n"
            "2026-01-06,101.0,104.0,100.5,103.5,1100000\n"
        )
        bars = StooqFeed._parse("SPY", text)
        self.assertEqual(len(bars), 2)
        self.assertAlmostEqual(bars[0].close, 101.0)
        self.assertEqual(bars[0].symbol, "SPY")

    def test_ticker_suffix(self):
        self.assertEqual(StooqFeed._ticker("SPY"), "spy.us")
        self.assertEqual(StooqFeed._ticker("^SPX.x"), "^spx.x")

    def test_refuses_intraday_requests(self):
        with self.assertRaises(DataError):
            StooqFeed().history(["SPY"], start=dt.date(2026, 1, 1), end=dt.date(2026, 2, 1), timeframe="5m")


class TestBuildFeed(unittest.TestCase):
    def test_builds_each_provider(self):
        for provider, expected in (
            ("stooq", "stooq"), ("alpaca", "alpaca"), ("csv", "csv"), ("synthetic", "synthetic"),
        ):
            self.assertEqual(build_feed(DataConfig(provider=provider)).name, expected)


if __name__ == "__main__":
    unittest.main()
