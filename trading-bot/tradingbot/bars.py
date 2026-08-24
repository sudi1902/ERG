"""Core price-bar types shared by every layer of the bot."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Bar:
    """One OHLCV candle for a single symbol."""

    symbol: str
    ts: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def session(self) -> dt.date:
        return self.ts.date()

    @property
    def typical(self) -> float:
        return (self.high + self.low + self.close) / 3.0

    @property
    def range(self) -> float:
        return self.high - self.low

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"{self.symbol} {self.ts}: high {self.high} < low {self.low}")
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"{self.symbol} {self.ts}: open {self.open} outside [{self.low}, {self.high}]")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"{self.symbol} {self.ts}: close {self.close} outside [{self.low}, {self.high}]")


class Series:
    """Append-only per-symbol bar history with cheap column views.

    Strategies see this, never the raw feed, so they cannot look ahead: the
    engine appends a bar only once it has closed.
    """

    __slots__ = ("symbol", "bars", "_closes", "_highs", "_lows", "_volumes")

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.bars: list[Bar] = []
        self._closes: list[float] = []
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._volumes: list[float] = []

    def append(self, bar: Bar) -> None:
        if self.bars and bar.ts <= self.bars[-1].ts:
            raise ValueError(f"{self.symbol}: bar {bar.ts} is not after {self.bars[-1].ts}")
        self.bars.append(bar)
        self._closes.append(bar.close)
        self._highs.append(bar.high)
        self._lows.append(bar.low)
        self._volumes.append(bar.volume)

    @property
    def closes(self) -> list[float]:
        return self._closes

    @property
    def highs(self) -> list[float]:
        return self._highs

    @property
    def lows(self) -> list[float]:
        return self._lows

    @property
    def volumes(self) -> list[float]:
        return self._volumes

    @property
    def last(self) -> Bar:
        return self.bars[-1]

    def session_bars(self, session: dt.date) -> list[Bar]:
        """Bars belonging to one trading day, newest last."""
        out: list[Bar] = []
        for bar in reversed(self.bars):
            if bar.session != session:
                break
            out.append(bar)
        out.reverse()
        return out

    def __len__(self) -> int:
        return len(self.bars)
