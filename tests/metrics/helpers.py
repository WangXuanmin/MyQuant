"""Fixed, network-free RunResult fixtures."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd

from oxq.core.types import BarSnapshot, Fill, Order, Portfolio
from oxq.portfolio.analytics import RunResult


def make_result(
    values: list[float],
    *,
    dates: list[str] | None = None,
    snapshots: list[BarSnapshot] | None = None,
    trades: list[Fill] | None = None,
) -> RunResult:
    if dates is None:
        dates = list(pd.bdate_range("2024-01-02", periods=len(values)).astype(str))
    curve = list(zip(pd.to_datetime(dates), values, strict=True))
    return RunResult(
        portfolio=Portfolio(cash=Decimal(str(values[-1] if values else 0))),
        trades=[] if trades is None else trades,
        equity_curve=curve,
        mktdata={},
        snapshots=[] if snapshots is None else snapshots,
    )


def make_snapshot(
    date: str,
    *,
    cash: float,
    total_value: float,
    weights: dict[str, float] | None = None,
) -> BarSnapshot:
    adjusted = {} if weights is None else weights
    return BarSnapshot(
        date=pd.Timestamp(date),
        target_weights=adjusted,
        adjusted_weights=adjusted,
        positions={},
        cash=cash,
        total_value=total_value,
    )


def make_fill(
    *,
    symbol: str = "510300.SH",
    side: str = "BUY",
    shares: int = 100,
    price: str = "4.00",
    fee: str = "1.00",
    date: str = "2024-01-03",
) -> Fill:
    return Fill(
        order=Order(symbol=symbol, side=side, shares=shares),
        filled_price=Decimal(price),
        filled_at=date,
        fee=Decimal(fee),
    )

