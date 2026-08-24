"""Opening-range breakout - the classic intraday day-trading pattern.

The first N minutes of a session set a high and a low. A clean break of that
range, on volume, with room left before the close, is the trade. The stop is
structural (the far side of the range), not an ATR guess, so the signal
carries its own stop and the risk layer sizes off that.

This one needs intraday bars; on daily data there is no opening range and the
strategy correctly does nothing.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..bars import Series
from ..portfolio import Side
from . import Context, Signal, Strategy, register


@register
class OpeningRangeBreakout(Strategy):
    """Trades breaks of the first N minutes' high or low, stop at the far side."""

    name = "orb"
    intraday_only = True

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {
            "range_minutes": 30,
            "min_range_pct": 0.15,
            "max_range_pct": 3.0,
            "volume_mult": 1.2,
            "no_entry_after_minutes": 240,
            "buffer_pct": 0.05,
        }

    def configure(self) -> None:
        # The stop is the far side of the opening range, so this strategy needs
        # no indicator history at all - just two bars to have a range.
        self.warmup = 2

    def generate(self, symbol: str, series: Series, ctx: Context) -> Iterable[Signal]:
        minutes = ctx.minutes_into_session
        if minutes is None:
            return []  # daily bars: no opening range exists
        session_bars = series.session_bars(ctx.session)
        if not session_bars:
            return []

        window = [b for b in session_bars if _minutes_between(session_bars[0], b) < self.p("range_minutes")]
        if len(window) < 2 or minutes < self.p("range_minutes"):
            return []  # range still forming

        or_high = max(b.high for b in window)
        or_low = min(b.low for b in window)
        or_mid = (or_high + or_low) / 2.0
        if or_mid <= 0:
            return []
        range_pct = (or_high - or_low) / or_mid * 100.0
        pos = ctx.position(symbol)
        bar = series.last

        if pos is not None:
            # Losing the midpoint means the breakout failed; leave early rather
            # than waiting for the full stop.
            if pos.side is Side.LONG and bar.close < or_mid:
                return [Signal(symbol, "exit", Side.LONG, "breakout failed back through range mid")]
            if pos.side is Side.SHORT and bar.close > or_mid:
                return [Signal(symbol, "exit", Side.SHORT, "breakdown failed back through range mid")]
            return []

        if minutes > self.p("no_entry_after_minutes"):
            return []
        if not self.p("min_range_pct") <= range_pct <= self.p("max_range_pct"):
            return []

        avg_volume = sum(b.volume for b in window) / len(window)
        if avg_volume > 0 and bar.volume < avg_volume * self.p("volume_mult"):
            return []

        buffer = or_mid * self.p("buffer_pct") / 100.0

        if bar.close > or_high + buffer:
            return [
                Signal(
                    symbol,
                    "enter",
                    Side.LONG,
                    f"broke {self.p('range_minutes')}m range high {or_high:.2f} ({range_pct:.2f}% range)",
                    stop=or_low,
                    target=bar.close + (or_high - or_low),
                    strength=min(range_pct / max(self.p("min_range_pct"), 0.01), 3.0),
                )
            ]
        if bar.close < or_low - buffer:
            return [
                Signal(
                    symbol,
                    "enter",
                    Side.SHORT,
                    f"broke {self.p('range_minutes')}m range low {or_low:.2f} ({range_pct:.2f}% range)",
                    stop=or_high,
                    target=bar.close - (or_high - or_low),
                    strength=min(range_pct / max(self.p("min_range_pct"), 0.01), 3.0),
                )
            ]
        return []


def _minutes_between(first, other) -> float:
    return (other.ts - first.ts).total_seconds() / 60.0
