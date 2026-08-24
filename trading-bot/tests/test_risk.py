import datetime as dt
import unittest

from tradingbot.config import RiskConfig
from tradingbot.portfolio import Portfolio, Side
from tradingbot.risk import Halt, RiskManager

DAY = dt.date(2026, 1, 5)
T0 = dt.datetime(2026, 1, 5, 16, 0)


def manager(**overrides) -> RiskManager:
    base = dict(
        risk_per_trade_pct=1.0, max_position_pct=100.0, max_positions=5,
        max_gross_exposure_pct=100.0, max_daily_loss_pct=2.0,
        daily_profit_target_pct=1.0, max_trades_per_day=20,
    )
    base.update(overrides)
    return RiskManager(RiskConfig(**base))


class TestSizing(unittest.TestCase):
    def test_size_equals_risk_budget_divided_by_stop_distance(self):
        rm = manager()
        p = Portfolio(100_000.0)
        rm.start_session(DAY, p.equity)
        # 1% of 100k = $1,000 risk; a $4 stop distance buys 250 shares.
        decision = rm.size_position(portfolio=p, symbol="X", side=Side.LONG, price=100.0, stop=96.0)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.shares, 250)

    def test_a_tighter_stop_buys_more_shares_for_the_same_dollar_risk(self):
        rm = manager()
        p = Portfolio(100_000.0)
        rm.start_session(DAY, p.equity)
        wide = rm.size_position(portfolio=p, symbol="X", side=Side.LONG, price=100.0, stop=90.0)
        tight = rm.size_position(portfolio=p, symbol="X", side=Side.LONG, price=100.0, stop=98.0)
        self.assertGreater(tight.shares, wide.shares)
        self.assertAlmostEqual(wide.shares * 10.0, tight.shares * 2.0, delta=1.0)

    def test_position_cap_binds_before_risk_budget(self):
        rm = manager(max_position_pct=10.0)
        p = Portfolio(100_000.0)
        rm.start_session(DAY, p.equity)
        decision = rm.size_position(portfolio=p, symbol="X", side=Side.LONG, price=100.0, stop=99.0)
        self.assertEqual(decision.shares, 100, "10% of 100k at $100 is 100 shares")

    def test_the_bot_never_borrows(self):
        rm = manager(max_position_pct=100.0)
        p = Portfolio(1_000.0)
        rm.start_session(DAY, p.equity)
        decision = rm.size_position(portfolio=p, symbol="X", side=Side.LONG, price=100.0, stop=99.9)
        self.assertLessEqual(decision.shares * 100.0, p.cash)

    def test_rejections_each_explain_themselves(self):
        rm = manager()
        p = Portfolio(100_000.0)
        rm.start_session(DAY, p.equity)
        cases = {
            "stop equals entry": dict(price=100.0, stop=100.0, side=Side.LONG),
            "wrong side": dict(price=100.0, stop=105.0, side=Side.LONG),
            "too wide": dict(price=100.0, stop=50.0, side=Side.LONG),
        }
        for label, kwargs in cases.items():
            decision = rm.size_position(portfolio=p, symbol="X", **kwargs)
            self.assertTrue(decision.rejected, label)
            self.assertTrue(decision.reason, f"{label} must carry a reason")

    def test_max_positions_blocks_the_next_entry(self):
        rm = manager(max_positions=2)
        p = Portfolio(100_000.0)
        rm.start_session(DAY, p.equity)
        for symbol in ("A", "B"):
            p.open_position(
                symbol=symbol, side=Side.LONG, shares=1, price=100.0, ts=T0, stop=95.0,
                target=None, trail_mult=0.0, atr=1.0, commission=0.0, reason="t",
            )
        decision = rm.size_position(portfolio=p, symbol="C", side=Side.LONG, price=100.0, stop=96.0)
        self.assertTrue(decision.rejected)
        self.assertIn("max_positions", decision.reason)

    def test_shorts_can_be_switched_off(self):
        rm = manager(allow_shorts=False)
        p = Portfolio(100_000.0)
        rm.start_session(DAY, p.equity)
        decision = rm.size_position(portfolio=p, symbol="X", side=Side.SHORT, price=100.0, stop=104.0)
        self.assertTrue(decision.rejected)


class TestHalts(unittest.TestCase):
    def test_loss_limit_trips_and_stays_tripped(self):
        rm = manager(max_daily_loss_pct=2.0)
        rm.start_session(DAY, 100_000.0)
        self.assertIs(rm.check_halt(99_000.0), Halt.NONE)
        self.assertIs(rm.check_halt(97_900.0), Halt.DAILY_LOSS)
        self.assertIs(rm.check_halt(101_000.0), Halt.DAILY_LOSS, "a halt does not un-trip intraday")

    def test_profit_target_halts_the_day(self):
        rm = manager(daily_profit_target_pct=1.0)
        rm.start_session(DAY, 100_000.0)
        self.assertIs(rm.check_halt(101_000.0), Halt.DAILY_TARGET)
        self.assertTrue(rm.halted)

    def test_trade_cap_halts_the_day(self):
        rm = manager(max_trades_per_day=2)
        rm.start_session(DAY, 100_000.0)
        rm.note_entry()
        rm.note_entry()
        self.assertIs(rm.check_halt(100_000.0), Halt.TRADE_CAP)

    def test_a_new_session_clears_the_halt_and_rebases_equity(self):
        rm = manager()
        rm.start_session(DAY, 100_000.0)
        rm.check_halt(101_000.0)
        self.assertTrue(rm.halted)
        rm.start_session(DAY + dt.timedelta(days=1), 101_000.0)
        self.assertFalse(rm.halted)
        self.assertAlmostEqual(rm.day.starting_equity, 101_000.0)
        self.assertEqual(len(rm.history), 1)

    def test_bracket_places_stop_and_target_the_right_way_round(self):
        rm = manager()
        stop, target = rm.bracket(side=Side.LONG, price=100.0, atr=2.0)
        self.assertLess(stop, 100.0)
        self.assertGreater(target, 100.0)
        stop, target = rm.bracket(side=Side.SHORT, price=100.0, atr=2.0)
        self.assertGreater(stop, 100.0)
        self.assertLess(target, 100.0)

    def test_bracket_needs_a_positive_atr(self):
        with self.assertRaises(ValueError):
            manager().bracket(side=Side.LONG, price=100.0, atr=0.0)


if __name__ == "__main__":
    unittest.main()
