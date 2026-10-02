"""Typed return models for backtest metrics."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import math
from typing import Any

import numpy as np
import pandas as pd

from .config import MetricConfig


@dataclass(frozen=True)
class DrawdownEvent:
    """One threshold-defined drawdown episode measured in sessions."""

    peak_date: pd.Timestamp
    start_date: pd.Timestamp
    trough_date: pd.Timestamp
    recovery_date: pd.Timestamp | None
    depth: float
    decline_sessions: int
    recovery_sessions: int | None
    total_sessions: int
    recovered: bool


@dataclass(frozen=True)
class DrawdownSummary:
    """Depth and duration statistics for all drawdown episodes."""

    max_drawdown: float
    max_drawdown_duration: int | None
    longest_underwater_days: int | None
    longest_recovery_sessions: int | None
    unrecovered: bool
    event_count: int


@dataclass(frozen=True)
class WorstPeriodReturn:
    """Worst simple return at one reporting frequency."""

    frequency: str
    label: str
    start: pd.Timestamp
    end: pd.Timestamp
    value: float
    partial_period: bool


@dataclass(frozen=True)
class TurnoverResult:
    """Weight-based and traded-notional turnover using distinct conventions."""

    weight_turnover_total: float | None
    weight_turnover_annualized: float | None
    traded_notional: float
    traded_notional_ratio: float | None
    traded_notional_ratio_annualized: float | None
    observation_sessions: int
    include_initial_allocation: bool


@dataclass(frozen=True)
class CostDragResult:
    """Explicit fees and the gross-to-net return difference."""

    total_fees: float
    fee_ratio_initial_equity: float | None
    gross_total_return: float | None
    net_total_return: float
    return_drag: float | None
    terminal_value_drag: float | None
    path_dependent: bool


@dataclass(frozen=True)
class ExposureSummary:
    """Actual invested and cash fractions derived from snapshots."""

    average_exposure: float | None
    minimum_exposure: float | None
    maximum_exposure: float | None
    average_cash_ratio: float | None
    maximum_cash_ratio: float | None
    sessions: int


@dataclass(frozen=True)
class CashDragResult:
    """Counterfactual opportunity cost of cash relative to a benchmark."""

    benchmark_name: str
    actual_total_return: float
    counterfactual_total_return: float
    cash_drag: float
    average_cash_ratio: float
    aligned_sessions: int


def json_safe(value: Any) -> Any:
    """Convert metric values to objects accepted by strict JSON encoders."""

    if is_dataclass(value):
        return {key: json_safe(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.isoformat()
    if value is pd.NaT or value is None:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return value


@dataclass(frozen=True)
class MetricReport:
    """Serializable collection of scalar metrics, warnings, and configuration."""

    metrics: dict[str, float | int | str | bool | None]
    warnings: tuple[str, ...]
    config: MetricConfig

    def to_json_dict(self) -> dict[str, Any]:
        """Return a strict-JSON-compatible dictionary."""

        return {
            "metrics": json_safe(self.metrics),
            "warnings": list(self.warnings),
            "config": json_safe(self.config),
        }

