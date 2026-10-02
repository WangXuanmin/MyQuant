"""Market-data validation used before strategy calculation."""

from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

from ..domain import DataQualityIssue, DataQualityReport
from .market import REQUIRED_COLUMNS


def validate_market_data(
    market_data: Mapping[str, pd.DataFrame],
    as_of_date: str,
    *,
    min_history_sessions: int,
    stale_after_calendar_days: int,
    fetch_issues: tuple[DataQualityIssue, ...] = (),
) -> DataQualityReport:
    """Validate schema, prices, chronology, history, and freshness."""

    issues = list(fetch_issues)
    as_of = pd.Timestamp(as_of_date)
    if not market_data and not fetch_issues:
        issues.append(DataQualityIssue("no_market_data", "no market data loaded", "ERROR"))
    for symbol, frame in market_data.items():
        if frame.empty:
            issues.append(DataQualityIssue("empty_market_data", "no rows", "ERROR", symbol))
            continue
        if not isinstance(frame.index, pd.DatetimeIndex):
            issues.append(DataQualityIssue("invalid_index", "index is not DatetimeIndex", "ERROR", symbol))
            continue
        if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
            issues.append(DataQualityIssue("invalid_dates", "dates are duplicated or unsorted", "ERROR", symbol))
        missing = set(REQUIRED_COLUMNS) - set(frame.columns)
        if missing:
            issues.append(DataQualityIssue("missing_columns", str(sorted(missing)), "ERROR", symbol))
            continue
        values = frame[list(REQUIRED_COLUMNS)].to_numpy(dtype="float64")
        if not np.isfinite(values).all():
            issues.append(DataQualityIssue("nonfinite_market_data", "required values are non-finite", "ERROR", symbol))
        price_columns = frame[["open", "high", "low", "close"]]
        if (price_columns <= 0).any().any():
            issues.append(DataQualityIssue("nonpositive_price", "OHLC contains non-positive values", "ERROR", symbol))
        if ((frame["high"] < frame[["open", "close", "low"]].max(axis=1)) | (frame["low"] > frame[["open", "close", "high"]].min(axis=1))).any():
            issues.append(DataQualityIssue("invalid_ohlc", "OHLC bounds are inconsistent", "ERROR", symbol))
        if len(frame) < min_history_sessions:
            issues.append(DataQualityIssue("insufficient_history", f"{len(frame)} < {min_history_sessions} sessions", "WARN", symbol))
        latest = frame.index[-1]
        if latest > as_of:
            issues.append(DataQualityIssue("future_market_data", f"latest {latest.date()} is after as-of", "ERROR", symbol))
        elif (as_of - latest).days > stale_after_calendar_days:
            issues.append(DataQualityIssue("stale_market_data", f"latest {latest.date()} is stale", "ERROR", symbol))
    return DataQualityReport(as_of_date, tuple(issues))

