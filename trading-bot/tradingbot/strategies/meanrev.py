"""Mean reversion: fade stretched moves, but only with the larger trend.

Buying every dip is how mean reversion dies - the cheapest stock in a
downtrend keeps getting cheaper. So entries require price to be extended
*against* a short horizon while still on the right side of a long one, and
every trade carries the same ATR stop as any other, because "it has to come
back" is not a risk plan.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..bars import Series
from ..indicators import atr, rsi, sma, zscore
from ..portfolio import Side
from . import Context, Signal, Strategy, register


@register
class MeanReversion(Strategy):
    """Fades short-term extremes that still agree with the long-term trend."""

    name = "meanrev"

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {
            "lookback": 20,
            "regime": 200,
            "entry_z": 2.0,
            "exit_z": 0.3,
            "rsi_period": 14,
            "rsi_low": 30.0,
            "rsi_high": 70.0,
            "atr_period": 14,
            "max_hold_bars": 10,
        }

    def configure(self) -> None:
        self.warmup = max(self.p("regime"), self.p("lookback"), self.p("atr_period")) + 5

    def generate(self, symbol: str, series: Series, ctx: Context) -> Iterable[Signal]:
        closes = series.closes
        z = zscore(closes, self.p("lookback"))
        regime = sma(closes, self.p("regime"))
        strength_rsi = rsi(closes, self.p("rsi_period"))
        a = atr(series.highs, series.lows, closes, self.p("atr_period"))
        if None in (z, regime, strength_rsi, a) or a <= 0:
            return []
        price = closes[-1]
        pos = ctx.position(symbol)

        if pos is not None:
            bars_held = sum(1 for b in series.bars if b.ts > pos.entry_ts)
            if bars_held >= self.p("max_hold_bars"):
                return [Signal(symbol, "exit", pos.side, f"held {bars_held} bars without reverting")]
            if pos.side is Side.LONG and z >= -self.p("exit_z"):
                return [Signal(symbol, "exit", Side.LONG, f"reverted to mean (z={z:+.2f})")]
            if pos.side is Side.SHORT and z <= self.p("exit_z"):
                return [Signal(symbol, "exit", Side.SHORT, f"reverted to mean (z={z:+.2f})")]
            return []

        entry_z = self.p("entry_z")
        # Long: stretched below its own short-term mean, still in an uptrend.
        if z <= -entry_z and strength_rsi <= self.p("rsi_low") and price > regime:
            return [
                Signal(
                    symbol,
                    "enter",
                    Side.LONG,
                    f"z={z:+.2f}, RSI={strength_rsi:.0f}, above SMA{self.p('regime')}",
                    strength=min(abs(z) / entry_z, 3.0),
                )
            ]
        # Short: stretched above its mean while the larger trend is down.
        if z >= entry_z and strength_rsi >= self.p("rsi_high") and price < regime:
            return [
                Signal(
                    symbol,
                    "enter",
                    Side.SHORT,
                    f"z={z:+.2f}, RSI={strength_rsi:.0f}, below SMA{self.p('regime')}",
                    strength=min(abs(z) / entry_z, 3.0),
                )
            ]
        return []
