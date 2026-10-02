"""Portfolio turnover calculations."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .config import MetricConfig
from .errors import MetricInputError
from .models import TurnoverResult


def _adjusted_weights(result: Any) -> pd.DataFrame:
    method = getattr(result, "adj_weights_df", None)
    if not callable(method):
        return pd.DataFrame()
    weights = method()
    if weights is None or weights.empty:
        return pd.DataFrame()
    if not isinstance(weights, pd.DataFrame):
        raise MetricInputError("adj_weights_df() must return a DataFrame")
    try:
        index = pd.DatetimeIndex(weights.index)
    except Exception as exc:
        raise MetricInputError("weight dates must be valid datetimes") from exc
    if index.hasnans or not index.is_unique or not index.is_monotonic_increasing:
        raise MetricInputError("weight dates must be unique and strictly increasing")
    try:
        normalized = weights.astype("float64").copy()
    except (TypeError, ValueError) as exc:
        raise MetricInputError("adjusted weights must be numeric") from exc
    if normalized.isna().any().any() or not np.isfinite(normalized.to_numpy()).all():
        raise MetricInputError("adjusted weights must be finite and complete")
    normalized.index = index
    if "CASH" not in normalized.columns:
        normalized["CASH"] = 1.0 - normalized.sum(axis=1)
    return normalized


def _weight_turnover(weights: pd.DataFrame, include_initial: bool) -> float | None:
    if weights.empty:
        return None
    columns = list(weights.columns)
    if include_initial:
        initial = pd.Series(0.0, index=columns, dtype="float64")
        initial.loc["CASH"] = 1.0
        rows = pd.concat([initial.to_frame().T, weights], ignore_index=True)
        changes = rows.diff().iloc[1:]
    else:
        risk_columns = [column for column in columns if column != "CASH"]
        if risk_columns:
            risk_exposure = weights[risk_columns].abs().sum(axis=1)
            allocated = np.flatnonzero(risk_exposure.to_numpy() > 1e-12)
        else:
            allocated = np.array([], dtype=int)
        if allocated.size == 0:
            return 0.0
        rows = weights.iloc[int(allocated[0]) :]
        changes = rows.diff().iloc[1:]
    if changes.empty:
        return 0.0
    return float((0.5 * changes.abs().sum(axis=1)).sum())


def _traded_notional(result: Any) -> float:
    total = 0.0
    for fill in getattr(result, "trades", None) or []:
        try:
            value = abs(float(fill.order.shares) * float(fill.filled_price))
        except (AttributeError, TypeError, ValueError) as exc:
            raise MetricInputError("each trade must expose order.shares and filled_price") from exc
        if not np.isfinite(value):
            raise MetricInputError("traded notional must be finite")
        total += value
    return float(total)


def calculate_turnover(
    result: Any, equity: pd.Series, config: MetricConfig
) -> TurnoverResult:
    """Calculate target-weight and actual traded-notional turnover."""

    sessions = max(len(equity) - 1, 0)
    weight_total = _weight_turnover(
        _adjusted_weights(result), config.include_initial_turnover
    )
    weight_annualized = (
        None
        if weight_total is None or sessions == 0
        else float(weight_total * config.periods_per_year / sessions)
    )
    notional = _traded_notional(result)
    average_equity = float(equity.mean()) if not equity.empty else None
    notional_ratio = (
        None
        if average_equity is None
        else float(notional / average_equity)
    )
    notional_annualized = (
        None
        if notional_ratio is None or sessions == 0
        else float(notional_ratio * config.periods_per_year / sessions)
    )
    return TurnoverResult(
        weight_turnover_total=weight_total,
        weight_turnover_annualized=weight_annualized,
        traded_notional=notional,
        traded_notional_ratio=notional_ratio,
        traded_notional_ratio_annualized=notional_annualized,
        observation_sessions=sessions,
        include_initial_allocation=config.include_initial_turnover,
    )

