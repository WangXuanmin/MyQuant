"""Actual exposure and benchmark-relative cash drag."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .config import MetricConfig
from .errors import MetricInputError
from .models import CashDragResult, ExposureSummary
from .normalization import normalize_return_series
from .returns import simple_returns


def cash_ratio_series(result: Any) -> pd.Series:
    """Calculate actual cash divided by total value for every snapshot."""

    snapshots = getattr(result, "snapshots", None) or []
    if not snapshots:
        return pd.Series(dtype="float64", name="cash_ratio")
    dates: list[Any] = []
    ratios: list[float] = []
    for snapshot in snapshots:
        try:
            date = snapshot.date
            cash = float(snapshot.cash)
            total_value = float(snapshot.total_value)
        except (AttributeError, TypeError, ValueError) as exc:
            raise MetricInputError("invalid portfolio snapshot") from exc
        if not np.isfinite(cash) or not np.isfinite(total_value):
            raise MetricInputError("snapshot cash and total_value must be finite")
        if total_value <= 0:
            raise MetricInputError("snapshot total_value must be positive")
        dates.append(date)
        ratios.append(cash / total_value)
    try:
        index = pd.DatetimeIndex(dates)
    except Exception as exc:
        raise MetricInputError("snapshot dates must be valid datetimes") from exc
    if index.hasnans or not index.is_unique or not index.is_monotonic_increasing:
        raise MetricInputError("snapshot dates must be unique and strictly increasing")
    return pd.Series(ratios, index=index, dtype="float64", name="cash_ratio")


def exposure_series(result: Any) -> pd.Series:
    """Return one minus actual cash ratio without clipping anomalies."""

    cash = cash_ratio_series(result)
    exposure = 1.0 - cash
    exposure.name = "exposure"
    return exposure


def summarize_exposure(result: Any) -> ExposureSummary:
    """Summarize actual exposure and cash from snapshots."""

    cash = cash_ratio_series(result)
    if cash.empty:
        return ExposureSummary(None, None, None, None, None, 0)
    exposure = 1.0 - cash
    return ExposureSummary(
        average_exposure=float(exposure.mean()),
        minimum_exposure=float(exposure.min()),
        maximum_exposure=float(exposure.max()),
        average_cash_ratio=float(cash.mean()),
        maximum_cash_ratio=float(cash.max()),
        sessions=len(cash),
    )


def calculate_cash_drag(
    result: Any,
    equity: pd.Series,
    benchmark_returns: pd.Series | None,
    benchmark_name: str,
    config: MetricConfig,
) -> CashDragResult | None:
    """Estimate opportunity cost using lagged cash weight and benchmark returns."""

    if benchmark_returns is None:
        return None
    benchmark = normalize_return_series(benchmark_returns)
    cash = cash_ratio_series(result)
    if cash.empty:
        return None
    actual = simple_returns(equity).rename("actual")
    lagged_cash = cash.shift(1).rename("cash")
    aligned = pd.concat(
        [actual, benchmark.rename("benchmark"), lagged_cash], axis=1, join="inner"
    ).dropna()
    if len(aligned) < 2:
        return None
    cash_daily_return = math.expm1(
        math.log1p(config.cash_annual_return) / config.periods_per_year
    )
    incremental = aligned["cash"] * (
        aligned["benchmark"] - cash_daily_return
    )
    counterfactual = aligned["actual"] + incremental
    actual_total = float((1.0 + aligned["actual"]).prod() - 1.0)
    counterfactual_total = float((1.0 + counterfactual).prod() - 1.0)
    return CashDragResult(
        benchmark_name=str(benchmark_name),
        actual_total_return=actual_total,
        counterfactual_total_return=counterfactual_total,
        cash_drag=float(counterfactual_total - actual_total),
        average_cash_ratio=float(aligned["cash"].mean()),
        aligned_sessions=len(aligned),
    )

