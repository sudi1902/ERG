"""Performance statistics, including an honest read on daily return targets.

Most backtest reports flatter the strategy by quoting a headline return. The
numbers that decide whether a strategy is worth running are the second-order
ones - drawdown, the shape of the daily distribution, and how often the thing
actually clears the bar you set for it. Those get equal billing here.
"""

from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from dataclasses import dataclass, field

from .portfolio import Portfolio, Trade

TRADING_DAYS = 252


@dataclass
class TargetAnalysis:
    """How a daily return target compares with what actually happened."""

    target_pct: float
    days: int
    days_hit: int
    best_day_pct: float
    median_day_pct: float
    mean_day_pct: float
    implied_annual_multiple: float

    @property
    def hit_rate(self) -> float:
        return self.days_hit / self.days if self.days else 0.0

    @property
    def multiple_of_best_day(self) -> float | None:
        """Target expressed as a multiple of the best day the run produced.

        The median is useless for this comparison - a selective strategy is
        flat most days, so the median is 0 and every target divides to
        infinity. The best day is the meaningful ceiling to measure against.
        """
        if self.best_day_pct <= 1e-9:
            return None
        return self.target_pct / self.best_day_pct

    @property
    def days_to_double_at_target(self) -> float:
        """Sessions to double the account if the target were met every day."""
        rate = self.target_pct / 100.0
        if rate <= 0:
            return float("inf")
        return math.log(2.0) / math.log(1.0 + rate)


@dataclass
class Metrics:
    start: dt.date
    end: dt.date
    days: int
    starting_equity: float
    ending_equity: float
    total_return_pct: float
    cagr_pct: float
    annual_vol_pct: float
    sharpe: float
    sortino: float
    max_drawdown_pct: float
    max_drawdown_days: int  # sessions from peak to trough, not to recovery
    calmar: float
    trades: int
    win_rate_pct: float
    profit_factor: float
    expectancy: float
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    avg_hold_hours: float
    trades_per_day: float
    exposure_pct: float
    commission_paid: float
    daily_returns_pct: list[float] = field(default_factory=list)
    best_day_pct: float = 0.0
    worst_day_pct: float = 0.0
    median_day_pct: float = 0.0
    positive_days_pct: float = 0.0
    target: TargetAnalysis | None = None


def daily_equity(portfolio: Portfolio) -> list[tuple[dt.date, float]]:
    """Collapse the equity curve to one closing value per session."""
    by_day: dict[dt.date, float] = {}
    for point in portfolio.equity_curve:
        by_day[point.ts.date()] = point.equity
    return [(day, by_day[day]) for day in sorted(by_day)]


def daily_returns(portfolio: Portfolio) -> list[float]:
    """Per-session percentage returns, seeded from starting equity."""
    curve = daily_equity(portfolio)
    if not curve:
        return []
    out: list[float] = []
    previous = portfolio.starting_equity
    for _, equity in curve:
        if previous > 0:
            out.append((equity / previous - 1.0) * 100.0)
        previous = equity
    return out


def max_drawdown(values: list[float]) -> tuple[float, int]:
    """Deepest peak-to-trough decline (%) and how many points it lasted."""
    if not values:
        return 0.0, 0
    peak = values[0]
    peak_index = 0
    worst = 0.0
    worst_length = 0
    for index, value in enumerate(values):
        if value > peak:
            peak = value
            peak_index = index
        elif peak > 0:
            decline = (value / peak - 1.0) * 100.0
            if decline < worst:
                worst = decline
                worst_length = index - peak_index
    return worst, worst_length


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct / 100.0
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[int(position)]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def analyse_target(returns: list[float], target_pct: float) -> TargetAnalysis:
    """Compare a daily return goal against the distribution actually produced."""
    days = len(returns)
    hits = sum(1 for r in returns if r >= target_pct)
    return TargetAnalysis(
        target_pct=target_pct,
        days=days,
        days_hit=hits,
        best_day_pct=max(returns) if returns else 0.0,
        median_day_pct=_median(returns),
        mean_day_pct=_mean(returns),
        implied_annual_multiple=(1.0 + target_pct / 100.0) ** TRADING_DAYS,
    )


def compute(
    portfolio: Portfolio,
    *,
    start: dt.date,
    end: dt.date,
    target_daily_pct: float | None = None,
    risk_free_pct: float = 0.0,
) -> Metrics:
    curve = daily_equity(portfolio)
    equities = [e for _, e in curve] or [portfolio.starting_equity]
    returns = daily_returns(portfolio)
    trades: list[Trade] = portfolio.trades

    starting = portfolio.starting_equity
    ending = equities[-1]
    total_return = (ending / starting - 1.0) * 100.0

    days = max(len(curve), 1)
    years = max(days / TRADING_DAYS, 1e-9)
    cagr = ((ending / starting) ** (1.0 / years) - 1.0) * 100.0 if ending > 0 else -100.0

    vol = _stdev(returns) * math.sqrt(TRADING_DAYS)
    excess = _mean(returns) - risk_free_pct / TRADING_DAYS
    sharpe = (excess / _stdev(returns) * math.sqrt(TRADING_DAYS)) if _stdev(returns) > 0 else 0.0
    downside = [r for r in returns if r < 0]
    downside_dev = _stdev(downside) if len(downside) > 1 else 0.0
    sortino = (excess / downside_dev * math.sqrt(TRADING_DAYS)) if downside_dev > 0 else 0.0

    drawdown, drawdown_days = max_drawdown(equities)
    calmar = cagr / abs(drawdown) if drawdown < 0 else 0.0

    wins = [t for t in trades if t.won]
    losses = [t for t in trades if not t.won]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)

    hold_hours = [t.bars_held.total_seconds() / 3600.0 for t in trades]
    exposure_points = [p for p in portfolio.equity_curve if p.equity > 0]
    exposure = _mean([p.exposure / p.equity * 100.0 for p in exposure_points]) if exposure_points else 0.0

    return Metrics(
        start=start,
        end=end,
        days=days,
        starting_equity=starting,
        ending_equity=ending,
        total_return_pct=total_return,
        cagr_pct=cagr,
        annual_vol_pct=vol,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown_pct=drawdown,
        max_drawdown_days=drawdown_days,
        calmar=calmar,
        trades=len(trades),
        win_rate_pct=len(wins) / len(trades) * 100.0 if trades else 0.0,
        profit_factor=profit_factor,
        expectancy=_mean([t.pnl for t in trades]),
        avg_win=_mean([t.pnl for t in wins]),
        avg_loss=_mean([t.pnl for t in losses]),
        largest_win=max((t.pnl for t in trades), default=0.0),
        largest_loss=min((t.pnl for t in trades), default=0.0),
        avg_hold_hours=_mean(hold_hours),
        trades_per_day=len(trades) / days,
        exposure_pct=exposure,
        commission_paid=sum(t.commission for t in trades),
        daily_returns_pct=returns,
        best_day_pct=max(returns) if returns else 0.0,
        worst_day_pct=min(returns) if returns else 0.0,
        median_day_pct=_median(returns),
        positive_days_pct=(sum(1 for r in returns if r > 0) / len(returns) * 100.0) if returns else 0.0,
        target=analyse_target(returns, target_daily_pct) if target_daily_pct is not None else None,
    )


def monthly_returns(portfolio: Portfolio) -> list[tuple[str, float]]:
    """Month-by-month percentage returns, for spotting regime dependence."""
    curve = daily_equity(portfolio)
    if not curve:
        return []
    by_month: dict[str, list[float]] = defaultdict(list)
    for day, equity in curve:
        by_month[f"{day:%Y-%m}"].append(equity)
    out: list[tuple[str, float]] = []
    previous = portfolio.starting_equity
    for month in sorted(by_month):
        closing = by_month[month][-1]
        out.append((month, (closing / previous - 1.0) * 100.0 if previous > 0 else 0.0))
        previous = closing
    return out
