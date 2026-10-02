"""Configuration for backtest metrics."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .errors import MetricConfigError


@dataclass(frozen=True)
class MetricConfig:
    """Immutable calculation settings shared by every metric in a report."""

    periods_per_year: int = 252
    risk_free_rate: float = 0.0
    minimum_acceptable_return: float = 0.0
    drawdown_threshold: float = -0.001
    cvar_confidence: float = 0.95
    cash_annual_return: float = 0.0
    include_initial_turnover: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.periods_per_year, bool) or not isinstance(
            self.periods_per_year, int
        ):
            raise MetricConfigError("periods_per_year must be a positive integer")
        if self.periods_per_year <= 0:
            raise MetricConfigError("periods_per_year must be a positive integer")
        for field_name in (
            "risk_free_rate",
            "minimum_acceptable_return",
            "drawdown_threshold",
            "cvar_confidence",
            "cash_annual_return",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise MetricConfigError(f"{field_name} must be finite")
        if self.minimum_acceptable_return <= -1:
            raise MetricConfigError("minimum_acceptable_return must be greater than -1")
        if not -1 <= self.drawdown_threshold < 0:
            raise MetricConfigError("drawdown_threshold must be in [-1, 0)")
        if not 0 < self.cvar_confidence < 1:
            raise MetricConfigError("cvar_confidence must be in (0, 1)")
        if self.cash_annual_return <= -1:
            raise MetricConfigError("cash_annual_return must be greater than -1")
        if not isinstance(self.include_initial_turnover, bool):
            raise MetricConfigError("include_initial_turnover must be boolean")

