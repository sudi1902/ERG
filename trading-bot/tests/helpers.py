"""Shared fixtures: deterministic bars built by hand, no network, no randomness."""

from __future__ import annotations

import datetime as dt

from tradingbot.bars import Bar
from tradingbot.config import Config


def make_bars(
    symbol: str, closes: list[float], *,
    start=dt.datetime(2026, 1, 5, 16, 0), spread=0.5, daily=True,
):
    """Turn a list of closes into well-formed daily bars.

    Each bar opens at the previous close so entry-price assertions are exact.
    """
    bars: list[Bar] = []
    step = dt.timedelta(days=1) if daily else dt.timedelta(minutes=5)
    ts = start
    for index, close in enumerate(closes):
        open_price = closes[index - 1] if index else close
        high = max(open_price, close) + spread
        low = max(0.01, min(open_price, close) - spread)
        bars.append(
            Bar(symbol=symbol, ts=ts, open=open_price, high=high, low=low, close=close, volume=1_000_000)
        )
        ts += step
        if daily:
            while ts.weekday() >= 5:
                ts += dt.timedelta(days=1)
    return bars


def frictionless_config(**overrides) -> Config:
    """A config with no costs, so tests assert on clean arithmetic."""
    base = {
        "symbols": ["TEST"],
        "strategy": "momentum",
        "starting_equity": 100_000.0,
        "costs": {"slippage_bps": 0.0, "spread_bps": 0.0},
        "data": {"provider": "synthetic"},
        "risk": {
            "risk_per_trade_pct": 1.0,
            "max_position_pct": 100.0,
            "max_daily_loss_pct": 100.0,
            "daily_profit_target_pct": 1000.0,
            "max_trades_per_day": 1000,
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return Config.from_dict(base)
