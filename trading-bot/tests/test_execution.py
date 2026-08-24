import datetime as dt
import unittest

from tradingbot.bars import Bar
from tradingbot.config import CostConfig
from tradingbot.execution import entry_fill, exit_fill, stop_trigger_price, target_trigger_price
from tradingbot.portfolio import Side

T0 = dt.datetime(2026, 1, 5, 16, 0)
COSTS = CostConfig(slippage_bps=10.0, spread_bps=0.0, commission_per_share=0.01, commission_min=0.0)


def bar(open_, high, low, close):
    return Bar("X", T0, open_, high, low, close, 1_000)


class TestFillPricing(unittest.TestCase):
    def test_you_always_cross_the_spread(self):
        self.assertGreater(entry_fill(Side.LONG, 100.0, 10, COSTS).price, 100.0)
        self.assertLess(exit_fill(Side.LONG, 100.0, 10, COSTS).price, 100.0)
        self.assertLess(entry_fill(Side.SHORT, 100.0, 10, COSTS).price, 100.0)
        self.assertGreater(exit_fill(Side.SHORT, 100.0, 10, COSTS).price, 100.0)

    def test_slippage_is_proportional(self):
        self.assertAlmostEqual(entry_fill(Side.LONG, 100.0, 10, COSTS).price, 100.1)

    def test_zero_cost_config_fills_at_the_reference(self):
        free = CostConfig(slippage_bps=0.0, spread_bps=0.0)
        self.assertAlmostEqual(entry_fill(Side.LONG, 100.0, 10, free).price, 100.0)
        self.assertAlmostEqual(exit_fill(Side.SHORT, 100.0, 10, free).price, 100.0)

    def test_commission_is_charged_on_the_fill_price(self):
        self.assertAlmostEqual(entry_fill(Side.LONG, 100.0, 10, COSTS).commission, 0.1)


class TestTriggers(unittest.TestCase):
    def test_long_stop_untouched_returns_none(self):
        self.assertIsNone(stop_trigger_price(Side.LONG, 95.0, bar(100, 101, 96, 100)))

    def test_long_stop_touched_fills_at_the_stop(self):
        self.assertEqual(stop_trigger_price(Side.LONG, 95.0, bar(100, 101, 94, 99)), 95.0)

    def test_long_stop_gapped_fills_at_the_open_which_is_worse(self):
        self.assertEqual(stop_trigger_price(Side.LONG, 95.0, bar(90, 92, 88, 91)), 90.0)

    def test_short_stop_gapped_fills_at_the_open(self):
        self.assertEqual(stop_trigger_price(Side.SHORT, 105.0, bar(110, 112, 109, 111)), 110.0)

    def test_short_stop_touched_fills_at_the_stop(self):
        self.assertEqual(stop_trigger_price(Side.SHORT, 105.0, bar(100, 106, 99, 104)), 105.0)

    def test_long_target_gapped_fills_at_the_open_which_is_better(self):
        self.assertEqual(target_trigger_price(Side.LONG, 105.0, bar(110, 112, 109, 111)), 110.0)

    def test_long_target_touched_fills_at_the_target(self):
        self.assertEqual(target_trigger_price(Side.LONG, 105.0, bar(100, 106, 99, 104)), 105.0)

    def test_short_target_untouched_returns_none(self):
        self.assertIsNone(target_trigger_price(Side.SHORT, 95.0, bar(100, 101, 96, 100)))

    def test_exact_touch_counts_as_a_fill(self):
        self.assertEqual(stop_trigger_price(Side.LONG, 95.0, bar(100, 101, 95.0, 99)), 95.0)


if __name__ == "__main__":
    unittest.main()
