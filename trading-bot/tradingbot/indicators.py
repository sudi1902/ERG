"""Pure-Python technical indicators.

No numpy, no pandas: the whole bot runs on a stock CPython install. Every
function takes the oldest-first list of values a `Series` exposes and returns
either a scalar (the current reading) or `None` when there is not yet enough
history. Returning `None` instead of a partial value is deliberate - a
strategy that silently trades on a half-warmed indicator is a bug factory.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def sma(values: Sequence[float], period: int) -> float | None:
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        return None
    window = values[-period:]
    return sum(window) / period


def ema(values: Sequence[float], period: int) -> float | None:
    """Exponential moving average, seeded with the first `period` SMA."""
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    acc = sum(values[:period]) / period
    for value in values[period:]:
        acc = value * k + acc * (1.0 - k)
    return acc


def ema_series(values: Sequence[float], period: int) -> list[float | None]:
    """Full EMA history, aligned index-for-index with `values`."""
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1.0)
    acc = sum(values[:period]) / period
    out[period - 1] = acc
    for i in range(period, len(values)):
        acc = values[i] * k + acc * (1.0 - k)
        out[i] = acc
    return out


def stdev(values: Sequence[float], period: int) -> float | None:
    """Population standard deviation of the last `period` values."""
    if len(values) < period or period < 2:
        return None
    window = values[-period:]
    mean = sum(window) / period
    var = sum((v - mean) ** 2 for v in window) / period
    return math.sqrt(var)


def rsi(values: Sequence[float], period: int = 14) -> float | None:
    """Wilder's RSI. 0-100; <30 oversold, >70 overbought by convention."""
    if len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def true_range(high: float, low: float, prev_close: float | None) -> float:
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> float | None:
    """Wilder's Average True Range - the bot's unit of "normal" movement.

    Position size and stop distance are both derived from this, so a $400
    stock and a $12 stock get risked identically in dollars.
    """
    n = len(closes)
    if n < period + 1 or len(highs) != n or len(lows) != n:
        return None
    trs = [true_range(highs[i], lows[i], closes[i - 1] if i else None) for i in range(n)]
    acc = sum(trs[1 : period + 1]) / period
    for i in range(period + 1, n):
        acc = (acc * (period - 1) + trs[i]) / period
    return acc


def bollinger(
    values: Sequence[float], period: int = 20, mult: float = 2.0
) -> tuple[float, float, float] | None:
    """(lower, middle, upper) Bollinger bands."""
    mid = sma(values, period)
    sd = stdev(values, period)
    if mid is None or sd is None:
        return None
    return (mid - mult * sd, mid, mid + mult * sd)


def zscore(values: Sequence[float], period: int = 20) -> float | None:
    """How many standard deviations the last value sits from its mean."""
    mean = sma(values, period)
    sd = stdev(values, period)
    if mean is None or sd is None or sd == 0.0:
        return None
    return (values[-1] - mean) / sd


def roc(values: Sequence[float], period: int) -> float | None:
    """Rate of change over `period` bars, as a fraction."""
    if len(values) < period + 1:
        return None
    past = values[-period - 1]
    if past == 0.0:
        return None
    return values[-1] / past - 1.0


def vwap(bars) -> float | None:
    """Volume-weighted average price over the bars given.

    Feed it one session's bars for the intraday VWAP that most desks anchor
    to. Falls back to a simple typical-price mean if the feed has no volume.
    """
    if not bars:
        return None
    total_volume = sum(b.volume for b in bars)
    if total_volume <= 0.0:
        return sum(b.typical for b in bars) / len(bars)
    return sum(b.typical * b.volume for b in bars) / total_volume


def highest(values: Sequence[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return max(values[-period:])


def lowest(values: Sequence[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return min(values[-period:])
