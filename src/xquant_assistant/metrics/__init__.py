"""Reusable, typed metrics for open-xquant backtest results."""

from .config import MetricConfig
from .errors import MetricConfigError, MetricInputError
from .facade import BacktestMetrics
from .models import (
    CashDragResult,
    CostDragResult,
    DrawdownEvent,
    DrawdownSummary,
    ExposureSummary,
    MetricReport,
    TurnoverResult,
    WorstPeriodReturn,
)

__all__ = [
    "BacktestMetrics",
    "MetricConfig",
    "MetricReport",
    "DrawdownEvent",
    "DrawdownSummary",
    "WorstPeriodReturn",
    "TurnoverResult",
    "CostDragResult",
    "ExposureSummary",
    "CashDragResult",
    "MetricInputError",
    "MetricConfigError",
]

