"""Paper / live runner: the same core, fed by fresh bars instead of history.

The loop is deliberately boring. Every poll it asks the feed for recent bars,
hands any that are new to the trading core, and mirrors whatever the core
decided to the broker. State lives in the core, so a paper session and a
backtest of the same window produce the same decisions.

Safety properties worth knowing about:

* Orders mirror to a real broker only when the broker is a real broker. The
  default `PaperBroker` records them and touches nothing.
* On startup the runner reconciles against the broker's actual positions and
  refuses to trade symbols it did not open itself.
* Entries go out as bracket orders, so the stop sits at the exchange and
  survives this process crashing.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field

from .bars import Bar
from .broker import Broker, BrokerError, PaperBroker
from .config import Config
from .datafeed import DataError, Feed, timeframe_minutes
from .engine import Event, TradingCore
from .portfolio import Portfolio
from .strategies import Strategy, get_strategy


@dataclass
class RunnerState:
    polls: int = 0
    bars_seen: int = 0
    last_ts: dict[str, dt.datetime] = field(default_factory=dict)
    blocked: set[str] = field(default_factory=set)


class LiveRunner:
    def __init__(
        self,
        config: Config,
        *,
        feed: Feed,
        broker: Broker,
        strategy: Strategy | None = None,
        verbose: bool = True,
        dry_run: bool = False,
    ) -> None:
        config.validate()
        self.cfg = config
        self.feed = feed
        self.broker = broker
        self.dry_run = dry_run
        self.strategy = strategy or get_strategy(config.strategy, config.strategy_params)
        self.portfolio = Portfolio(config.starting_equity)
        self.core = TradingCore(config, self.strategy, portfolio=self.portfolio, verbose=verbose)
        self.state = RunnerState()
        self.verbose = verbose

    # ---- startup ---------------------------------------------------------

    def reconcile(self) -> list[str]:
        """Compare the broker's book with ours; refuse to trade what we do not own."""
        notes: list[str] = []
        try:
            broker_positions = self.broker.positions()
            account = self.broker.account()
        except BrokerError as exc:
            raise BrokerError(f"cannot reconcile with broker: {exc}") from exc

        if not isinstance(self.broker, PaperBroker):
            self.portfolio.cash = account.cash
            notes.append(f"broker equity {account.equity:,.2f}, cash {account.cash:,.2f}")

        for pos in broker_positions:
            if pos.symbol not in self.portfolio.positions:
                self.state.blocked.add(pos.symbol)
                notes.append(
                    f"{pos.symbol}: broker holds {pos.shares:,.0f} shares this bot did not open - "
                    "leaving it alone and skipping the symbol"
                )
        return notes

    def warmup(self, *, end: dt.date | None = None) -> int:
        """Replay recent history so indicators are warm before the first decision."""
        end = end or dt.date.today()
        start = end - dt.timedelta(days=self.cfg.data.lookback_days)
        history = self.feed.history(
            self.cfg.symbols, start=start, end=end, timeframe=self.cfg.data.timeframe
        )
        from .backtest import build_timeline

        timeline = build_timeline(history)
        # Replay with trading switched off. The point is to fill the indicator
        # windows, not to book imaginary P&L from bars that already happened -
        # the session starts flat, at full equity, with warm history.
        self.core.trading_enabled = False
        try:
            for ts, bars, session_end in timeline:
                self.core.process(ts, bars, session_end=session_end)
                for symbol in (b.symbol for b in bars):
                    self.state.last_ts[symbol] = ts
        finally:
            self.core.trading_enabled = True
        # Discard the replay's bookkeeping so the run's own record starts here.
        self.core.events.clear()
        self.core.portfolio.equity_curve.clear()
        self.core.pending.clear()
        self.core.risk.day = None
        self.state.bars_seen += sum(len(b) for b in history.values())
        return len(timeline)

    # ---- polling ---------------------------------------------------------

    def poll_once(self, *, now: dt.datetime | None = None) -> list[Event]:
        """Fetch, process any genuinely new bars, and mirror resulting orders."""
        now = now or dt.datetime.now()
        self.state.polls += 1
        before = len(self.core.events)

        end = now.date()
        start = end - dt.timedelta(days=max(5, self.cfg.data.lookback_days // 10))
        try:
            history = self.feed.history(
                self.cfg.symbols, start=start, end=end, timeframe=self.cfg.data.timeframe
            )
        except DataError as exc:
            self.core.log(now, "info", "", f"data fetch failed: {exc}")
            return []

        fresh: list[Bar] = []
        for symbol, bars in history.items():
            if symbol in self.state.blocked:
                continue
            last = self.state.last_ts.get(symbol)
            for bar in bars:
                if last is None or bar.ts > last:
                    fresh.append(bar)
        if not fresh:
            return []

        for ts in sorted({b.ts for b in fresh}):
            batch = [b for b in fresh if b.ts == ts]
            self.core.process(ts, batch, session_end=self._is_session_end(ts))
            for bar in batch:
                self.state.last_ts[bar.symbol] = bar.ts
            self.state.bars_seen += len(batch)

        new_events = self.core.events[before:]
        self._mirror(new_events)
        return new_events

    def run(
        self,
        *,
        poll_seconds: int = 60,
        max_polls: int | None = None,
        sleeper=time.sleep,
        clock=dt.datetime.now,
    ) -> None:
        """Poll until interrupted, or until `max_polls` passes have run."""
        polls = 0
        while max_polls is None or polls < max_polls:
            polls += 1
            now = clock()
            try:
                if not self.broker.market_open():
                    if self.verbose:
                        print(f"{now:%Y-%m-%d %H:%M}  market closed - waiting")
                    sleeper(poll_seconds)
                    continue
                self.poll_once(now=now)
            except BrokerError as exc:
                print(f"{now:%Y-%m-%d %H:%M}  broker error: {exc}")
            except KeyboardInterrupt:
                print("\nstopping - open positions are left as they are")
                return
            if max_polls is None or polls < max_polls:
                sleeper(poll_seconds)

    # ---- order mirroring -------------------------------------------------

    def _mirror(self, events: list[Event]) -> None:
        """Send the core's realised entries and exits to the broker."""
        if isinstance(self.broker, PaperBroker) and not self.dry_run:
            return  # the core already *is* the paper account
        for event in events:
            if self.dry_run:
                if event.kind in {"entry", "exit"}:
                    print(f"  [dry-run] would {event.kind} {event.symbol}: {event.message}")
                continue
            try:
                if event.kind == "entry":
                    pos = self.portfolio.positions.get(event.symbol)
                    if pos is None:
                        continue
                    self.broker.submit(
                        symbol=event.symbol,
                        side=pos.side,
                        shares=pos.shares,
                        stop=pos.stop,
                        target=pos.target,
                    )
                elif event.kind == "exit":
                    self.broker.close(event.symbol)
            except BrokerError as exc:
                self.core.log(event.ts, "info", event.symbol, f"broker rejected order: {exc}")

    def _is_session_end(self, ts: dt.datetime) -> bool:
        minutes = timeframe_minutes(self.cfg.data.timeframe)
        if minutes is None:
            return True  # a daily bar is by definition the session's last
        close_time = _parse_time(self.cfg.session_end)
        end = dt.datetime.combine(ts.date(), close_time)
        return ts >= end - dt.timedelta(minutes=minutes)


def scan(
    config: Config,
    *,
    feed: Feed,
    end: dt.date | None = None,
    strategy: Strategy | None = None,
) -> tuple[TradingCore, list]:
    """Replay history and report the intents queued for the next open.

    This is the "what would you do tomorrow?" command - it trades nothing.
    """
    from .backtest import build_timeline

    strategy = strategy or get_strategy(config.strategy, config.strategy_params)
    end = end or dt.date.today()
    start = end - dt.timedelta(days=config.data.lookback_days)
    history = feed.history(config.symbols, start=start, end=end, timeframe=config.data.timeframe)
    core = TradingCore(config, strategy, portfolio=Portfolio(config.starting_equity))
    timeline = build_timeline(history)
    for ts, bars, session_end in timeline:
        # The final bar must not trigger flat-at-close, or every scan reports
        # an empty book regardless of what the strategy wants.
        is_last = ts == timeline[-1][0]
        core.process(ts, bars, session_end=session_end and not is_last)
    return core, list(core.pending)


def _parse_time(text: str) -> dt.time:
    hour, _, minute = text.partition(":")
    return dt.time(int(hour), int(minute or 0))
