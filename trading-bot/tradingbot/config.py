"""Configuration objects, loaded from JSON and validated on construction.

Every knob that can lose money has a conservative default, and the validators
reject the settings that most often blow accounts up (no stop loss, risking a
huge slice of equity per trade, leverage nobody asked for).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_type_hints


class ConfigError(ValueError):
    """Raised when a config file asks for something unsafe or impossible."""


@dataclass
class CostConfig:
    """Trading frictions. Backtests that ignore these lie to you."""

    commission_per_share: float = 0.0
    commission_min: float = 0.0
    commission_pct: float = 0.0
    slippage_bps: float = 2.0
    spread_bps: float = 1.0

    def validate(self) -> None:
        for name in (
            "commission_per_share", "commission_min", "commission_pct",
            "slippage_bps", "spread_bps",
        ):
            if getattr(self, name) < 0:
                raise ConfigError(f"costs.{name} cannot be negative")

    def commission(self, shares: float, price: float) -> float:
        fee = shares * self.commission_per_share + shares * price * self.commission_pct
        return max(fee, self.commission_min) if shares > 0 else 0.0

    @property
    def fill_slip(self) -> float:
        """Fraction of price given up on every fill, both directions."""
        return (self.slippage_bps + self.spread_bps / 2.0) / 10_000.0


@dataclass
class RiskConfig:
    """The layer that decides how much, not what.

    `daily_profit_target_pct` and `max_daily_loss_pct` are both *stop trading
    for the day* thresholds. The target is not a forecast - it is the point at
    which the bot banks the day and stops handing profits back.
    """

    risk_per_trade_pct: float = 0.5
    max_position_pct: float = 20.0
    max_positions: int = 5
    max_gross_exposure_pct: float = 100.0
    max_daily_loss_pct: float = 2.0
    daily_profit_target_pct: float = 1.0
    max_trades_per_day: int = 20
    atr_period: int = 14
    stop_atr_mult: float = 2.0
    target_atr_mult: float = 3.0
    trail_atr_mult: float = 0.0
    allow_shorts: bool = True
    flat_at_close: bool = True

    def validate(self) -> None:
        if not 0 < self.risk_per_trade_pct <= 5:
            raise ConfigError(
                f"risk.risk_per_trade_pct={self.risk_per_trade_pct} must be in (0, 5]. "
                "Risking more than 5% of equity on one trade means a normal losing "
                "streak ends the account."
            )
        if not 0 < self.max_position_pct <= 100:
            raise ConfigError("risk.max_position_pct must be in (0, 100]")
        if self.max_positions < 1:
            raise ConfigError("risk.max_positions must be at least 1")
        if not 0 < self.max_gross_exposure_pct <= 400:
            raise ConfigError("risk.max_gross_exposure_pct must be in (0, 400]")
        if not 0 < self.max_daily_loss_pct <= 100:
            raise ConfigError("risk.max_daily_loss_pct must be in (0, 100]")
        if self.daily_profit_target_pct <= 0:
            raise ConfigError("risk.daily_profit_target_pct must be positive")
        if self.max_trades_per_day < 1:
            raise ConfigError("risk.max_trades_per_day must be at least 1")
        if self.atr_period < 2:
            raise ConfigError("risk.atr_period must be at least 2")
        if self.stop_atr_mult <= 0:
            raise ConfigError(
                "risk.stop_atr_mult must be positive - a strategy with no stop has "
                "unbounded loss per trade and cannot be position-sized."
            )
        if self.target_atr_mult < 0 or self.trail_atr_mult < 0:
            raise ConfigError("risk target/trail multiples cannot be negative")


@dataclass
class DataConfig:
    provider: str = "stooq"
    timeframe: str = "1d"
    lookback_days: int = 400
    cache_dir: str = ".cache/tradingbot"
    csv_dir: str = "data"
    feed: str = "iex"

    def validate(self) -> None:
        if self.provider not in {"stooq", "alpaca", "csv", "synthetic"}:
            raise ConfigError(f"data.provider '{self.provider}' is not one of stooq, alpaca, csv, synthetic")
        if self.lookback_days < 1:
            raise ConfigError("data.lookback_days must be at least 1")


@dataclass
class BrokerConfig:
    """`mode` is the live-money gate. Nothing else in the bot can flip it."""

    name: str = "paper"
    mode: str = "paper"
    base_url: str = "https://paper-api.alpaca.markets"
    key_id_env: str = "ALPACA_KEY_ID"
    secret_env: str = "ALPACA_SECRET_KEY"

    def validate(self) -> None:
        if self.name not in {"paper", "alpaca"}:
            raise ConfigError(f"broker.name '{self.name}' is not one of paper, alpaca")
        if self.mode not in {"paper", "live"}:
            raise ConfigError(f"broker.mode '{self.mode}' is not one of paper, live")


@dataclass
class Config:
    symbols: list[str] = field(default_factory=lambda: ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"])
    strategy: str = "momentum"
    strategy_params: dict[str, Any] = field(default_factory=dict)
    starting_equity: float = 25_000.0
    session_start: str = "09:30"
    session_end: str = "16:00"
    timezone: str = "America/New_York"
    risk: RiskConfig = field(default_factory=RiskConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    data: DataConfig = field(default_factory=DataConfig)
    broker: BrokerConfig = field(default_factory=BrokerConfig)

    def validate(self) -> None:
        if not self.symbols:
            raise ConfigError("config.symbols is empty - nothing to trade")
        if self.starting_equity <= 0:
            raise ConfigError("config.starting_equity must be positive")
        dupes = {s for s in self.symbols if self.symbols.count(s) > 1}
        if dupes:
            raise ConfigError(f"config.symbols has duplicates: {sorted(dupes)}")
        for sub in (self.risk, self.costs, self.data, self.broker):
            sub.validate()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Config:
        cfg = _build(cls, raw)
        cfg.symbols = [s.strip().upper() for s in cfg.symbols]
        cfg.validate()
        return cfg

    @classmethod
    def load(cls, path: str | Path | None) -> Config:
        if path is None:
            cfg = cls()
            cfg.validate()
            return cfg
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"config file not found: {p}")
        try:
            raw = json.loads(p.read_text())
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{p} is not valid JSON: {exc}") from exc
        return cls.from_dict(raw)

    def to_dict(self) -> dict[str, Any]:
        return _unbuild(self)


def _build(cls: type, raw: dict[str, Any]) -> Any:
    """Recursively construct a dataclass from a dict, rejecting unknown keys."""
    if not isinstance(raw, dict):
        raise ConfigError(f"expected an object for {cls.__name__}, got {type(raw).__name__}")
    # `from __future__ import annotations` leaves field types as strings, so
    # resolve them before deciding what is a nested config section.
    hints = get_type_hints(cls)
    unknown = set(raw) - set(hints)
    if unknown:
        raise ConfigError(f"unknown {cls.__name__} option(s): {', '.join(sorted(unknown))}")
    kwargs: dict[str, Any] = {}
    for name, value in raw.items():
        ftype = hints[name]
        if is_dataclass(ftype):
            kwargs[name] = _build(ftype, value)
        else:
            kwargs[name] = value
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise ConfigError(f"bad {cls.__name__} options: {exc}") from exc


def _unbuild(obj: Any) -> Any:
    if is_dataclass(obj):
        return {f.name: _unbuild(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, list):
        return [_unbuild(v) for v in obj]
    return obj
