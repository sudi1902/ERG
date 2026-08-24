import datetime as dt
import unittest

from tradingbot import metrics as M
from tradingbot.portfolio import Portfolio, Side

T0 = dt.datetime(2026, 1, 5, 16, 0)


def portfolio_with_curve(equities, starting=100_000.0):
    p = Portfolio(starting)
    for index, equity in enumerate(equities):
        p.cash = equity
        p.record_equity(T0 + dt.timedelta(days=index))
    return p


class TestDrawdown(unittest.TestCase):
    def test_a_rising_curve_has_no_drawdown(self):
        value, length = M.max_drawdown([100, 110, 120, 130])
        self.assertEqual(value, 0.0)
        self.assertEqual(length, 0)

    def test_drawdown_measures_peak_to_trough(self):
        value, length = M.max_drawdown([100, 200, 150, 180])
        self.assertAlmostEqual(value, -25.0)
        self.assertEqual(length, 1)

    def test_deepest_of_several_drawdowns_wins(self):
        value, _ = M.max_drawdown([100, 90, 100, 50, 100])
        self.assertAlmostEqual(value, -50.0)

    def test_empty_curve_is_handled(self):
        self.assertEqual(M.max_drawdown([]), (0.0, 0))


class TestDailyReturns(unittest.TestCase):
    def test_returns_are_chained_off_the_previous_close(self):
        p = portfolio_with_curve([110_000.0, 121_000.0])
        self.assertEqual([round(r, 6) for r in M.daily_returns(p)], [10.0, 10.0])

    def test_one_equity_point_per_session_even_with_many_bars(self):
        p = Portfolio(100_000.0)
        for hour in (10, 12, 16):
            p.cash = 100_000.0 + hour
            p.record_equity(dt.datetime(2026, 1, 5, hour, 0))
        curve = M.daily_equity(p)
        self.assertEqual(len(curve), 1)
        self.assertAlmostEqual(curve[0][1], 100_016.0, msg="the day's last mark wins")


class TestPercentiles(unittest.TestCase):
    def test_known_percentiles(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertAlmostEqual(M.percentile(values, 50), 3.0)
        self.assertAlmostEqual(M.percentile(values, 0), 1.0)
        self.assertAlmostEqual(M.percentile(values, 100), 5.0)

    def test_interpolates_between_points(self):
        self.assertAlmostEqual(M.percentile([0.0, 10.0], 25), 2.5)

    def test_degenerate_inputs(self):
        self.assertEqual(M.percentile([], 50), 0.0)
        self.assertEqual(M.percentile([7.0], 90), 7.0)


class TestTargetAnalysis(unittest.TestCase):
    def test_counts_the_sessions_that_cleared_the_bar(self):
        analysis = M.analyse_target([0.1, 20.0, -3.0, 15.0], target_pct=15.0)
        self.assertEqual(analysis.days, 4)
        self.assertEqual(analysis.days_hit, 2)
        self.assertAlmostEqual(analysis.hit_rate, 0.5)

    def test_compounding_a_daily_target_is_reported_honestly(self):
        analysis = M.analyse_target([0.1] * 10, target_pct=15.0)
        self.assertEqual(analysis.days_hit, 0)
        self.assertGreater(analysis.implied_annual_multiple, 1e14)
        self.assertAlmostEqual(analysis.days_to_double_at_target, 4.959, places=2)

    def test_target_is_expressed_against_the_best_session_not_the_median(self):
        # A selective strategy is flat most days, so the median is 0 and would
        # make every comparison infinite. The best day is the real ceiling.
        analysis = M.analyse_target([0.0, 0.0, 0.0, 0.5], target_pct=15.0)
        self.assertEqual(analysis.median_day_pct, 0.0)
        self.assertAlmostEqual(analysis.multiple_of_best_day, 30.0)

    def test_no_positive_session_leaves_the_comparison_undefined(self):
        self.assertIsNone(M.analyse_target([-1.0, -2.0], target_pct=15.0).multiple_of_best_day)


class TestCompute(unittest.TestCase):
    def test_flat_curve_produces_zero_return_and_no_nonsense(self):
        p = portfolio_with_curve([100_000.0] * 10)
        m = M.compute(p, start=dt.date(2026, 1, 5), end=dt.date(2026, 1, 16))
        self.assertAlmostEqual(m.total_return_pct, 0.0)
        self.assertEqual(m.sharpe, 0.0)
        self.assertEqual(m.trades, 0)
        self.assertEqual(m.max_drawdown_pct, 0.0)

    def test_totals_and_trade_stats_line_up(self):
        p = Portfolio(100_000.0)
        p.open_position(
            symbol="X", side=Side.LONG, shares=100, price=100.0, ts=T0, stop=95.0,
            target=None, trail_mult=0.0, atr=1.0, commission=0.0, reason="t",
        )
        p.close_position("X", price=110.0, ts=T0 + dt.timedelta(days=1), commission=0.0, exit_reason="target")
        p.record_equity(T0 + dt.timedelta(days=1))
        m = M.compute(p, start=dt.date(2026, 1, 5), end=dt.date(2026, 1, 6), target_daily_pct=15.0)
        self.assertEqual(m.trades, 1)
        self.assertAlmostEqual(m.win_rate_pct, 100.0)
        self.assertAlmostEqual(m.total_return_pct, 1.0)
        self.assertAlmostEqual(m.expectancy, 1_000.0)
        self.assertEqual(m.profit_factor, float("inf"))
        self.assertIsNotNone(m.target)
        self.assertEqual(m.target.days_hit, 0)

    def test_monthly_returns_chain_correctly(self):
        p = Portfolio(100.0)
        p.cash = 110.0
        p.record_equity(dt.datetime(2026, 1, 30, 16, 0))
        p.cash = 121.0
        p.record_equity(dt.datetime(2026, 2, 27, 16, 0))
        months = M.monthly_returns(p)
        self.assertEqual([m for m, _ in months], ["2026-01", "2026-02"])
        self.assertAlmostEqual(months[0][1], 10.0)
        self.assertAlmostEqual(months[1][1], 10.0)


if __name__ == "__main__":
    unittest.main()
