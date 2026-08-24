"""Historical simulation driven by the same core the live runner uses."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

from .bars import Bar
from .config import Config
from .datafeed import DataError, Feed, build_feed
from .engine import Event, TradingCore
from .portfolio import Portfolio
from .risk import DayStats
from .strategies import Strategy, get_strategy


@dataclass
class BacktestResult:
    config: Config
    strategy: str
    portfolio: Portfolio
    events: list[Event]
    day_stats: list[DayStats]
    start: dt.date
    end: dt.date
    bars: int
    symbols: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_timeline(history: dict[str, list[Bar]]) -> list[tuple[dt.datetime, list[Bar], bool]]:
    """Merge per-symbol bars into (timestamp, bars, is_last_bar_of_day) steps."""
    grouped: dict[dt.datetime, list[Bar]] = defaultdict(list)
    for bars in history.values():
        for bar in bars:
            grouped[bar.ts].append(bar)
    stamps = sorted(grouped)
    last_of_day: set[dt.datetime] = set()
    for index, ts in enumerate(stamps):
        is_last = index == len(stamps) - 1 or stamps[index + 1].date() != ts.date()
        if is_last:
            last_of_day.add(ts)
    return [(ts, grouped[ts], ts in last_of_day) for ts in stamps]


def run_backtest(
    config: Config,
    *,
    feed: Feed | None = None,
    start: dt.date | None = None,
    end: dt.date | None = None,
    strategy: Strategy | None = None,
    verbose: bool = False,
) -> BacktestResult:
    config.validate()
    feed = feed or build_feed(config.data)
    end = end or dt.date.today()
    start = start or (end - dt.timedelta(days=config.data.lookback_days))
    if start >= end:
        raise DataError(f"start {start} must be before end {end}")

    strategy = strategy or get_strategy(config.strategy, config.strategy_params)
    history = feed.history(config.symbols, start=start, end=end, timeframe=config.data.timeframe)

    warnings: list[str] = []
    missing = [s for s in config.symbols if s not in history]
    if missing:
        warnings.append(f"no data for {', '.join(missing)} - skipped")
    thin = [s for s, bars in history.items() if len(bars) <= strategy.warmup]
    if thin:
        warnings.append(
            f"{', '.join(thin)} have fewer bars than the strategy's {strategy.warmup}-bar warmup - "
            "they can never signal. Widen the date range."
        )

    core = TradingCore(config, strategy, portfolio=Portfolio(config.starting_equity), verbose=verbose)
    timeline = build_timeline(history)
    if not timeline:
        raise DataError("no bars in the requested window")

    for ts, bars, session_end in timeline:
        core.process(ts, bars, session_end=session_end)
    core.finish(timeline[-1][0])

    return BacktestResult(
        config=config,
        strategy=strategy.describe(),
        portfolio=core.portfolio,
        events=core.events,
        day_stats=core.risk.history,
        start=timeline[0][0].date(),
        end=timeline[-1][0].date(),
        bars=sum(len(b) for b in history.values()),
        symbols=sorted(history),
        warnings=warnings,
    )
