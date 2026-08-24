"""Strategy framework: signals in, orders never.

A strategy answers one question - "given the bars that have closed, do I want
to be long, short, or flat in this symbol?" It never sizes a position, never
touches cash, and never places an order. That separation is what lets the risk
layer stay authoritative.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..bars import Series
from ..portfolio import Portfolio, Position, Side


@dataclass
class Signal:
    """An intent. The engine and risk manager decide whether it becomes an order."""

    symbol: str
    action: str  # "enter" | "exit"
    side: Side
    reason: str
    strength: float = 1.0
    stop: float | None = None
    target: float | None = None

    def __post_init__(self) -> None:
        if self.action not in {"enter", "exit"}:
            raise ValueError(f"unknown signal action '{self.action}'")


@dataclass
class Context:
    """Everything a strategy is allowed to see at decision time."""

    now: dt.datetime
    session: dt.date
    series: dict[str, Series]
    portfolio: Portfolio
    updated: list[str] = field(default_factory=list)
    minutes_into_session: float | None = None

    def history(self, symbol: str) -> Series | None:
        return self.series.get(symbol)

    def position(self, symbol: str) -> Position | None:
        return self.portfolio.positions.get(symbol)

    def is_flat(self, symbol: str) -> bool:
        return symbol not in self.portfolio.positions


class Strategy:
    """Base class. Subclasses implement `generate` for a single symbol."""

    name = "base"
    #: Bars of history required before this strategy may trade a symbol.
    warmup = 50
    #: Set to True for strategies that only make sense on intraday bars.
    intraday_only = False

    def __init__(self, **params: Any) -> None:
        unknown = set(params) - set(self.defaults())
        if unknown:
            raise ValueError(
                f"strategy '{self.name}' got unknown param(s): {', '.join(sorted(unknown))}. "
                f"Known: {', '.join(sorted(self.defaults()))}"
            )
        self.params: dict[str, Any] = {**self.defaults(), **params}
        self.configure()

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {}

    def configure(self) -> None:
        """Hook for subclasses to derive state from params."""

    def p(self, key: str) -> Any:
        return self.params[key]

    def on_bar(self, ctx: Context) -> list[Signal]:
        signals: list[Signal] = []
        for symbol in ctx.updated:
            series = ctx.series.get(symbol)
            if series is None or len(series) < self.warmup:
                continue
            signals.extend(self.generate(symbol, series, ctx))
        return signals

    def generate(self, symbol: str, series: Series, ctx: Context) -> Iterable[Signal]:
        raise NotImplementedError

    def describe(self) -> str:
        if not self.params:
            return self.name
        rendered = ", ".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.name}({rendered})"


_REGISTRY: dict[str, type[Strategy]] = {}


def register(cls: type[Strategy]) -> type[Strategy]:
    key = cls.name.lower()
    if key in _REGISTRY and _REGISTRY[key] is not cls:
        raise ValueError(f"strategy name '{key}' is already registered")
    _REGISTRY[key] = cls
    return cls


def get_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    key = name.lower()
    if key not in _REGISTRY:
        raise KeyError(f"unknown strategy '{name}'. Available: {', '.join(available())}")
    return _REGISTRY[key](**(params or {}))


def available() -> list[str]:
    return sorted(_REGISTRY)


def registry() -> dict[str, type[Strategy]]:
    return dict(_REGISTRY)


from . import meanrev, momentum, orb  # noqa: E402,F401  (import for side-effect registration)

__all__ = [
    "Signal",
    "Context",
    "Strategy",
    "register",
    "get_strategy",
    "available",
    "registry",
]
