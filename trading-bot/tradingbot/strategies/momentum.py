"""Trend-following: ride established moves, cut them when the trend breaks.

Entry needs three things to agree - a fast/slow EMA cross, price on the right
side of a longer regime filter, and enough recent movement to be worth the
spread. That agreement requirement is what keeps it out of chop; the cost is
that it sits out a lot of days, which is the correct behaviour.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..bars import Series
from ..indicators import atr, ema, roc
from ..portfolio import Side
from . import Context, Signal, Strategy, register


@register
class Momentum(Strategy):
    """Buys strength and sells weakness once a trend confirms."""

    name = "momentum"

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {
            "fast": 12,
            "slow": 26,
            "regime": 100,
            "atr_period": 14,
            "min_roc_pct": 0.5,
            "roc_period": 10,
            "min_atr_pct": 0.4,
        }

    def configure(self) -> None:
        self.warmup = max(self.p("slow"), self.p("regime"), self.p("atr_period")) + 5

    def generate(self, symbol: str, series: Series, ctx: Context) -> Iterable[Signal]:
        closes = series.closes
        fast = ema(closes, self.p("fast"))
        slow = ema(closes, self.p("slow"))
        regime = ema(closes, self.p("regime"))
        a = atr(series.highs, series.lows, closes, self.p("atr_period"))
        if None in (fast, slow, regime, a) or a <= 0:
            return []
        price = closes[-1]

        # Previous bar's cross state, so we act on the turn rather than on
        # every bar of an already-extended move.
        prev_fast = ema(closes[:-1], self.p("fast"))
        prev_slow = ema(closes[:-1], self.p("slow"))
        if prev_fast is None or prev_slow is None:
            return []

        pos = ctx.position(symbol)
        crossed_up = prev_fast <= prev_slow and fast > slow
        crossed_down = prev_fast >= prev_slow and fast < slow

        if pos is not None:
            if pos.side is Side.LONG and crossed_down:
                return [Signal(symbol, "exit", Side.LONG, "EMA cross down")]
            if pos.side is Side.SHORT and crossed_up:
                return [Signal(symbol, "exit", Side.SHORT, "EMA cross up")]
            return []

        # Skip names that are too quiet to pay for the round trip.
        if a / price * 100.0 < self.p("min_atr_pct"):
            return []
        momentum_pct = (roc(closes, self.p("roc_period")) or 0.0) * 100.0
        threshold = self.p("min_roc_pct")

        if crossed_up and price > regime and momentum_pct >= threshold:
            return [
                Signal(
                    symbol,
                    "enter",
                    Side.LONG,
                    f"EMA{self.p('fast')}>EMA{self.p('slow')}, above regime, "
                    f"{momentum_pct:+.1f}% over {self.p('roc_period')} bars",
                    strength=min(momentum_pct / max(threshold, 0.01), 3.0),
                )
            ]
        if crossed_down and price < regime and momentum_pct <= -threshold:
            return [
                Signal(
                    symbol,
                    "enter",
                    Side.SHORT,
                    f"EMA{self.p('fast')}<EMA{self.p('slow')}, below regime, "
                    f"{momentum_pct:+.1f}% over {self.p('roc_period')} bars",
                    strength=min(abs(momentum_pct) / max(threshold, 0.01), 3.0),
                )
            ]
        return []
