"""Broker adapters.

`PaperBroker` keeps the whole account in memory - it is the default and the
only one that runs without credentials. `AlpacaBroker` speaks Alpaca's REST
API and can point at either the paper or the live endpoint; the live endpoint
is gated by config *and* an explicit confirmation flag on the command line,
because a typo should never be able to spend real money.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .config import BrokerConfig
from .portfolio import Portfolio, Side


class BrokerError(RuntimeError):
    pass


@dataclass
class Account:
    equity: float
    cash: float
    buying_power: float
    currency: str = "USD"


@dataclass
class BrokerPosition:
    symbol: str
    side: Side
    shares: float
    avg_price: float
    market_value: float
    unrealized: float


@dataclass
class OrderResult:
    accepted: bool
    order_id: str
    message: str


class Broker:
    name = "base"
    is_live = False

    def account(self) -> Account:
        raise NotImplementedError

    def positions(self) -> list[BrokerPosition]:
        raise NotImplementedError

    def market_open(self) -> bool:
        raise NotImplementedError

    def submit(
        self,
        *,
        symbol: str,
        side: Side,
        shares: float,
        stop: float | None = None,
        target: float | None = None,
    ) -> OrderResult:
        raise NotImplementedError

    def close(self, symbol: str) -> OrderResult:
        raise NotImplementedError


class PaperBroker(Broker):
    """An in-memory account. Fills are simulated by the trading core."""

    name = "paper"
    is_live = False

    def __init__(self, portfolio: Portfolio) -> None:
        self.portfolio = portfolio
        self.orders: list[dict] = []

    def account(self) -> Account:
        equity = self.portfolio.equity
        return Account(equity=equity, cash=self.portfolio.cash, buying_power=max(self.portfolio.cash, 0.0))

    def positions(self) -> list[BrokerPosition]:
        out: list[BrokerPosition] = []
        for pos in self.portfolio.positions.values():
            last = self.portfolio.price(pos.symbol) or pos.entry_price
            out.append(
                BrokerPosition(
                    symbol=pos.symbol,
                    side=pos.side,
                    shares=pos.shares,
                    avg_price=pos.entry_price,
                    market_value=pos.market_value(last),
                    unrealized=pos.unrealized(last),
                )
            )
        return out

    def market_open(self) -> bool:
        return True  # the paper account trades whenever bars arrive

    def submit(self, *, symbol, side, shares, stop=None, target=None) -> OrderResult:
        self.orders.append(
            {"symbol": symbol, "side": side.value, "shares": shares, "stop": stop, "target": target}
        )
        return OrderResult(True, f"paper-{len(self.orders)}", "simulated locally")

    def close(self, symbol: str) -> OrderResult:
        self.orders.append({"symbol": symbol, "action": "close"})
        return OrderResult(True, f"paper-{len(self.orders)}", "simulated locally")


class AlpacaBroker(Broker):
    """REST client for Alpaca. Defaults to the paper endpoint."""

    name = "alpaca"

    def __init__(self, cfg: BrokerConfig) -> None:
        cfg.validate()
        self.cfg = cfg
        self.base_url = cfg.base_url.rstrip("/")
        self.key_id = os.environ.get(cfg.key_id_env, "")
        self.secret = os.environ.get(cfg.secret_env, "")
        self.is_live = cfg.mode == "live"
        if not (self.key_id and self.secret):
            raise BrokerError(
                f"missing credentials: set {cfg.key_id_env} and {cfg.secret_env} in the environment. "
                "Free paper-trading keys: https://alpaca.markets"
            )
        if self.is_live and "paper-api" in self.base_url:
            raise BrokerError(
                "broker.mode is 'live' but broker.base_url still points at the paper endpoint. "
                "Set base_url to https://api.alpaca.markets to trade live, or set mode back to 'paper'."
            )
        if not self.is_live and "paper-api" not in self.base_url:
            raise BrokerError(
                "broker.mode is 'paper' but broker.base_url is not the paper endpoint. "
                "Refusing to send paper-mode orders to a live account."
            )

    # ---- transport -------------------------------------------------------

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "APCA-API-KEY-ID": self.key_id,
                "APCA-API-SECRET-KEY": self.secret,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise BrokerError(f"alpaca {method} {path} -> HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BrokerError(f"alpaca unreachable: {exc}") from exc

    # ---- API -------------------------------------------------------------

    def account(self) -> Account:
        raw = self._request("GET", "/v2/account")
        return Account(
            equity=float(raw.get("equity", 0.0)),
            cash=float(raw.get("cash", 0.0)),
            buying_power=float(raw.get("buying_power", 0.0)),
            currency=raw.get("currency", "USD"),
        )

    def positions(self) -> list[BrokerPosition]:
        raw = self._request("GET", "/v2/positions")
        out: list[BrokerPosition] = []
        for row in raw if isinstance(raw, list) else []:
            shares = float(row["qty"])
            out.append(
                BrokerPosition(
                    symbol=row["symbol"],
                    side=Side.LONG if shares >= 0 else Side.SHORT,
                    shares=abs(shares),
                    avg_price=float(row["avg_entry_price"]),
                    market_value=float(row["market_value"]),
                    unrealized=float(row.get("unrealized_pl", 0.0)),
                )
            )
        return out

    def market_open(self) -> bool:
        return bool(self._request("GET", "/v2/clock").get("is_open"))

    def next_open(self) -> dt.datetime | None:
        raw = self._request("GET", "/v2/clock").get("next_open")
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None

    def submit(self, *, symbol, side, shares, stop=None, target=None) -> OrderResult:
        order: dict = {
            "symbol": symbol,
            "qty": str(int(shares)),
            "side": "buy" if side is Side.LONG else "sell",
            "type": "market",
            "time_in_force": "day",
        }
        # A bracket order puts the stop on the exchange, so the position stays
        # protected even if this process dies mid-session.
        if stop is not None and target is not None:
            order["order_class"] = "bracket"
            order["stop_loss"] = {"stop_price": round(stop, 2)}
            order["take_profit"] = {"limit_price": round(target, 2)}
        elif stop is not None:
            order["order_class"] = "oto"
            order["stop_loss"] = {"stop_price": round(stop, 2)}
        raw = self._request("POST", "/v2/orders", order)
        return OrderResult(True, str(raw.get("id", "")), raw.get("status", "submitted"))

    def close(self, symbol: str) -> OrderResult:
        raw = self._request("DELETE", f"/v2/positions/{urllib.parse.quote(symbol)}")
        return OrderResult(True, str(raw.get("id", "")), raw.get("status", "closing"))


def build_broker(cfg: BrokerConfig, portfolio: Portfolio) -> Broker:
    if cfg.name == "paper":
        return PaperBroker(portfolio)
    if cfg.name == "alpaca":
        return AlpacaBroker(cfg)
    raise BrokerError(f"unknown broker '{cfg.name}'")
