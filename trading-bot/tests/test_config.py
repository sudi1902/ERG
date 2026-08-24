import json
import tempfile
import unittest
from pathlib import Path

from tradingbot.config import Config, ConfigError


class TestConfigValidation(unittest.TestCase):
    def test_defaults_are_valid_and_conservative(self):
        cfg = Config()
        cfg.validate()
        self.assertLessEqual(cfg.risk.risk_per_trade_pct, 1.0)
        self.assertEqual(cfg.broker.mode, "paper", "live money is never the default")
        self.assertEqual(cfg.broker.name, "paper")

    def test_symbols_are_normalised(self):
        self.assertEqual(Config.from_dict({"symbols": [" spy ", "aapl"]}).symbols, ["SPY", "AAPL"])

    def test_nested_sections_become_typed_objects(self):
        cfg = Config.from_dict({"risk": {"max_positions": 3}, "costs": {"slippage_bps": 5.0}})
        self.assertEqual(cfg.risk.max_positions, 3)
        self.assertEqual(cfg.costs.slippage_bps, 5.0)
        self.assertEqual(cfg.risk.risk_per_trade_pct, 0.5, "untouched fields keep defaults")

    def test_unsafe_and_malformed_settings_are_refused(self):
        cases = [
            ({"risk": {"risk_per_trade_pct": 25}}, "oversized per-trade risk"),
            ({"risk": {"risk_per_trade_pct": 0}}, "zero risk"),
            ({"risk": {"stop_atr_mult": 0}}, "no stop"),
            ({"risk": {"max_positions": 0}}, "no positions"),
            ({"risk": {"max_daily_loss_pct": 0}}, "no loss limit"),
            ({"risk": {"atr_period": 1}}, "degenerate ATR"),
            ({"symbols": []}, "nothing to trade"),
            ({"symbols": ["A", "A"]}, "duplicate symbols"),
            ({"starting_equity": -1}, "negative equity"),
            ({"broker": {"mode": "yolo"}}, "unknown mode"),
            ({"data": {"provider": "magic"}}, "unknown provider"),
            ({"data": {"lookback_days": 0}}, "no lookback"),
            ({"costs": {"slippage_bps": -1}}, "negative slippage"),
            ({"typo_key": 1}, "unknown option"),
            ({"risk": {"typo": 1}}, "unknown nested option"),
        ]
        for raw, label in cases:
            with self.subTest(label), self.assertRaises(ConfigError):
                Config.from_dict(raw)

    def test_round_trips_through_json(self):
        original = Config.from_dict({"symbols": ["SPY"], "risk": {"max_positions": 2}})
        restored = Config.from_dict(json.loads(json.dumps(original.to_dict())))
        self.assertEqual(restored.to_dict(), original.to_dict())

    def test_load_reports_a_missing_file_clearly(self):
        with self.assertRaises(ConfigError):
            Config.load("/definitely/not/here.json")

    def test_load_reports_bad_json_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text("{not json")
            with self.assertRaises(ConfigError) as ctx:
                Config.load(path)
            self.assertIn("not valid JSON", str(ctx.exception))

    def test_load_none_gives_defaults(self):
        self.assertEqual(Config.load(None).to_dict(), Config().to_dict())


class TestCostModel(unittest.TestCase):
    def test_commission_respects_the_minimum(self):
        from tradingbot.config import CostConfig

        costs = CostConfig(commission_per_share=0.005, commission_min=1.0)
        self.assertAlmostEqual(costs.commission(10, 100.0), 1.0, msg="minimum applies")
        self.assertAlmostEqual(costs.commission(1000, 100.0), 5.0)

    def test_percentage_commission(self):
        from tradingbot.config import CostConfig

        self.assertAlmostEqual(CostConfig(commission_pct=0.001).commission(100, 50.0), 5.0)

    def test_zero_shares_costs_nothing(self):
        from tradingbot.config import CostConfig

        self.assertEqual(CostConfig(commission_min=1.0).commission(0, 100.0), 0.0)

    def test_fill_slip_combines_slippage_and_half_the_spread(self):
        from tradingbot.config import CostConfig

        self.assertAlmostEqual(CostConfig(slippage_bps=2.0, spread_bps=4.0).fill_slip, 4.0 / 10_000)


if __name__ == "__main__":
    unittest.main()
