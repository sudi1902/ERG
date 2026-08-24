import datetime as dt
import unittest

from tradingbot.portfolio import Portfolio, Position, Side

T0 = dt.datetime(2026, 1, 5, 16, 0)


class TestPositionMath(unittest.TestCase):
    def test_long_and_short_pnl_have_opposite_signs(self):
        long = Position("X", Side.LONG, 10, 100.0, T0, stop=95.0)
        short = Position("X", Side.SHORT, 10, 100.0, T0, stop=105.0)
        self.assertAlmostEqual(long.unrealized(110.0), 100.0)
        self.assertAlmostEqual(short.unrealized(110.0), -100.0)
        self.assertAlmostEqual(long.unrealized(90.0), -100.0)
        self.assertAlmostEqual(short.unrealized(90.0), 100.0)

    def test_risk_at_entry_is_stop_distance_times_size(self):
        self.assertAlmostEqual(Position("X", Side.LONG, 50, 100.0, T0, stop=98.0).risk_at_entry(), 100.0)

    def test_trailing_stop_ratchets_up_but_never_down(self):
        pos = Position("X", Side.LONG, 10, 100.0, T0, stop=90.0, trail_mult=2.0, atr_at_entry=1.0)
        self.assertAlmostEqual(pos.effective_stop(), 98.0)
        pos.mark(110.0, 99.0)
        self.assertAlmostEqual(pos.effective_stop(), 108.0)
        pos.mark(105.0, 100.0)  # a lower high must not loosen it
        self.assertAlmostEqual(pos.effective_stop(), 108.0)

    def test_short_trailing_stop_ratchets_down(self):
        pos = Position("X", Side.SHORT, 10, 100.0, T0, stop=110.0, trail_mult=2.0, atr_at_entry=1.0)
        pos.mark(101.0, 90.0)
        self.assertAlmostEqual(pos.effective_stop(), 92.0)

    def test_no_trail_configured_keeps_the_fixed_stop(self):
        pos = Position("X", Side.LONG, 10, 100.0, T0, stop=95.0, trail_mult=0.0, atr_at_entry=1.0)
        pos.mark(200.0, 99.0)
        self.assertAlmostEqual(pos.effective_stop(), 95.0)

    def test_zero_size_is_rejected(self):
        with self.assertRaises(ValueError):
            Position("X", Side.LONG, 0, 100.0, T0, stop=95.0)


class TestPortfolioAccounting(unittest.TestCase):
    def setUp(self):
        self.p = Portfolio(10_000.0)

    def test_opening_a_long_moves_cash_but_not_equity(self):
        self.p.open_position(
            symbol="X", side=Side.LONG, shares=10, price=100.0, ts=T0,
            stop=95.0, target=None, trail_mult=0.0, atr=1.0, commission=0.0, reason="t",
        )
        self.assertAlmostEqual(self.p.cash, 9_000.0)
        self.assertAlmostEqual(self.p.equity, 10_000.0)

    def test_commission_comes_straight_off_equity(self):
        self.p.open_position(
            symbol="X", side=Side.LONG, shares=10, price=100.0, ts=T0,
            stop=95.0, target=None, trail_mult=0.0, atr=1.0, commission=5.0, reason="t",
        )
        self.assertAlmostEqual(self.p.equity, 9_995.0)

    def test_round_trip_pnl_is_net_of_both_commissions(self):
        self.p.open_position(
            symbol="X", side=Side.LONG, shares=10, price=100.0, ts=T0,
            stop=95.0, target=None, trail_mult=0.0, atr=1.0, commission=1.0, reason="t",
        )
        trade = self.p.close_position("X", price=110.0, ts=T0, commission=1.0, exit_reason="target")
        self.assertAlmostEqual(trade.gross_pnl, 100.0)
        self.assertAlmostEqual(trade.commission, 2.0)
        self.assertAlmostEqual(trade.pnl, 98.0)
        self.assertAlmostEqual(self.p.equity, 10_098.0)
        self.assertAlmostEqual(self.p.realized_pnl, 98.0)
        self.assertTrue(trade.won)

    def test_short_round_trip(self):
        self.p.open_position(
            symbol="X", side=Side.SHORT, shares=10, price=100.0, ts=T0,
            stop=105.0, target=None, trail_mult=0.0, atr=1.0, commission=0.0, reason="t",
        )
        self.assertAlmostEqual(self.p.cash, 11_000.0, msg="a short credits cash")
        self.assertAlmostEqual(self.p.equity, 10_000.0)
        trade = self.p.close_position("X", price=90.0, ts=T0, commission=0.0, exit_reason="target")
        self.assertAlmostEqual(trade.pnl, 100.0)
        self.assertAlmostEqual(self.p.equity, 10_100.0)

    def test_gross_exposure_counts_both_directions(self):
        for symbol, side in (("A", Side.LONG), ("B", Side.SHORT)):
            self.p.open_position(
                symbol=symbol, side=side, shares=10, price=100.0, ts=T0,
                stop=95.0 if side is Side.LONG else 105.0, target=None,
                trail_mult=0.0, atr=1.0, commission=0.0, reason="t",
            )
        self.assertAlmostEqual(self.p.gross_exposure, 2_000.0)

    def test_double_entry_is_refused(self):
        kwargs = dict(
            side=Side.LONG, shares=10, price=100.0, ts=T0, stop=95.0,
            target=None, trail_mult=0.0, atr=1.0, commission=0.0, reason="t",
        )
        self.p.open_position(symbol="X", **kwargs)
        with self.assertRaises(ValueError):
            self.p.open_position(symbol="X", **kwargs)

    def test_equity_curve_records_a_point_per_call(self):
        self.p.record_equity(T0)
        self.p.record_equity(T0 + dt.timedelta(days=1))
        self.assertEqual(len(self.p.equity_curve), 2)
        self.assertAlmostEqual(self.p.equity_curve[0].equity, 10_000.0)

    def test_negative_starting_equity_is_rejected(self):
        with self.assertRaises(ValueError):
            Portfolio(0)


if __name__ == "__main__":
    unittest.main()
