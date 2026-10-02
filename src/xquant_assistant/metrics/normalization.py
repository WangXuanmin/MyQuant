"""Strict normalization at the metrics boundary."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .errors import MetricInputError


def _validated_datetime_index(values: Any, *, label: str) -> pd.DatetimeIndex:
    try:
        index = pd.DatetimeIndex(values)
    except Exception as exc:  # pandas exposes several conversion exceptions
        raise MetricInputError(f"{label} must be valid datetimes") from exc
    if index.hasnans:
        raise MetricInputError(f"{label} contains missing datetimes")
    if not index.is_unique:
        raise MetricInputError(f"{label} contains duplicate datetimes")
    if not index.is_monotonic_increasing:
        raise MetricInputError(f"{label} must be strictly increasing")
    return index


def equity_series_from_result(result: Any) -> pd.Series:
    """Build a validated float64 equity Series from ``result.equity_curve``."""

    if not hasattr(result, "equity_curve"):
        raise MetricInputError("result must expose equity_curve")
    curve = result.equity_curve
    if curve is None:
        raise MetricInputError("equity_curve cannot be None")
    dates: list[Any] = []
    values: list[Any] = []
    try:
        for item in curve:
            if len(item) != 2:
                raise MetricInputError("each equity point must contain date and value")
            date, value = item
            dates.append(date)
            values.append(value)
    except TypeError as exc:
        raise MetricInputError("equity_curve must be iterable") from exc
    index = _validated_datetime_index(dates, label="equity dates")
    try:
        array = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise MetricInputError("equity values must be numeric") from exc
    if not np.isfinite(array).all():
        raise MetricInputError("equity values must be finite")
    if (array <= 0).any():
        raise MetricInputError("equity values must be strictly positive")
    return pd.Series(array, index=index, name="equity", dtype="float64")


def normalize_return_series(returns: pd.Series) -> pd.Series:
    """Validate a return Series without sorting, filling, or changing its index."""

    if not isinstance(returns, pd.Series):
        raise MetricInputError("returns must be a pandas Series")
    index = _validated_datetime_index(returns.index, label="return dates")
    try:
        values = pd.to_numeric(returns, errors="raise").to_numpy(dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise MetricInputError("returns must be numeric") from exc
    if not np.isfinite(values).all():
        raise MetricInputError("returns must not contain NaN or infinity")
    return pd.Series(values, index=index, name=returns.name, dtype="float64")

