import unittest

from tradingbot import indicators as ind


class TestIndicators(unittest.TestCase):
    def test_returns_none_before_warmup(self):
        for fn, args in (
            (ind.sma, ([1, 2], 5)),
            (ind.ema, ([1, 2], 5)),
            (ind.rsi, ([1, 2], 14)),
            (ind.stdev, ([1, 2], 5)),
            (ind.roc, ([1, 2], 5)),
            (ind.highest, ([1, 2], 5)),
        ):
            self.assertIsNone(fn(*args), f"{fn.__name__} should withhold a partial reading")

    def test_sma_and_stdev(self):
        self.assertEqual(ind.sma([1, 2, 3, 4, 5], 5), 3.0)
        self.assertEqual(ind.sma([10, 20, 30], 2), 25.0)
        self.assertAlmostEqual(ind.stdev([2, 4, 4, 4, 5, 5, 7, 9], 8), 2.0)

    def test_ema_matches_manual_recursion(self):
        values = [22.0, 24.0, 23.0, 26.0, 28.0, 27.0]
        period = 3
        k = 2 / (period + 1)
        expected = sum(values[:period]) / period
        for value in values[period:]:
            expected = value * k + expected * (1 - k)
        self.assertAlmostEqual(ind.ema(values, period), expected)

    def test_ema_series_is_aligned_and_agrees_with_scalar(self):
        values = [float(v) for v in range(1, 30)]
        series = ind.ema_series(values, 10)
        self.assertEqual(len(series), len(values))
        self.assertIsNone(series[8])
        self.assertIsNotNone(series[9])
        self.assertAlmostEqual(series[-1], ind.ema(values, 10))

    def test_rsi_bounds_and_extremes(self):
        rising = [float(v) for v in range(1, 40)]
        self.assertEqual(ind.rsi(rising, 14), 100.0)
        falling = list(reversed(rising))
        self.assertEqual(ind.rsi(falling, 14), 0.0)
        mixed = [10, 11, 10.5, 12, 11.5, 13, 12.5, 14, 13.5, 15, 14.5, 16, 15.5, 17, 16.5, 18]
        value = ind.rsi(mixed, 14)
        self.assertTrue(0.0 <= value <= 100.0)

    def test_true_range_uses_previous_close(self):
        self.assertEqual(ind.true_range(10, 8, None), 2.0)
        self.assertEqual(ind.true_range(10, 8, 12), 4.0)  # gap down dominates
        self.assertEqual(ind.true_range(10, 8, 5), 5.0)  # gap up dominates

    def test_atr_of_constant_range_equals_that_range(self):
        n = 30
        highs = [101.0] * n
        lows = [99.0] * n
        closes = [100.0] * n
        self.assertAlmostEqual(ind.atr(highs, lows, closes, 14), 2.0)

    def test_bollinger_brackets_the_mean(self):
        values = [float(v) for v in range(1, 21)]
        lower, mid, upper = ind.bollinger(values, 20, 2.0)
        self.assertAlmostEqual(mid, 10.5)
        self.assertLess(lower, mid)
        self.assertGreater(upper, mid)
        self.assertAlmostEqual(upper - mid, mid - lower)

    def test_zscore_of_flat_series_is_none(self):
        self.assertIsNone(ind.zscore([5.0] * 25, 20))

    def test_roc(self):
        self.assertAlmostEqual(ind.roc([100, 101, 102, 103, 110], 4), 0.10)

    def test_vwap_weights_by_volume(self):
        import datetime as dt

        from tradingbot.bars import Bar

        bars = [
            Bar("X", dt.datetime(2026, 1, 5, 9, 30), 10, 10, 10, 10, 100),
            Bar("X", dt.datetime(2026, 1, 5, 9, 35), 20, 20, 20, 20, 300),
        ]
        self.assertAlmostEqual(ind.vwap(bars), (10 * 100 + 20 * 300) / 400)

    def test_vwap_without_volume_falls_back_to_typical_price(self):
        import datetime as dt

        from tradingbot.bars import Bar

        bars = [Bar("X", dt.datetime(2026, 1, 5, 9, 30), 10, 12, 8, 10, 0)]
        self.assertAlmostEqual(ind.vwap(bars), 10.0)

    def test_rejects_nonpositive_period(self):
        with self.assertRaises(ValueError):
            ind.sma([1, 2, 3], 0)
        with self.assertRaises(ValueError):
            ind.ema([1, 2, 3], -1)


if __name__ == "__main__":
    unittest.main()
