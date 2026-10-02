"""Public facade for all backtest metrics."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from .config import MetricConfig
from .costs import calculate_cost_drag, total_fees
from .drawdown import (
    drawdown_events as calculate_drawdown_events,
    drawdown_series as calculate_drawdown_series,
    max_drawdown as calculate_max_drawdown,
    summarize_drawdowns,
)
from .errors import MetricConfigError
from .exposure import (
    calculate_cash_drag,
    cash_ratio_series as calculate_cash_ratio_series,
    exposure_series as calculate_exposure_series,
    summarize_exposure,
)
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
from .normalization import equity_series_from_result, normalize_return_series
from .returns import (
    annualized_volatility as calculate_annualized_volatility,
    cagr as calculate_cagr,
    calmar_ratio as calculate_calmar_ratio,
    downside_volatility as calculate_downside_volatility,
    sharpe_ratio as calculate_sharpe_ratio,
    simple_returns,
    sortino_ratio as calculate_sortino_ratio,
    total_return as calculate_total_return,
    worst_period_returns as calculate_worst_period_returns,
)
from .tail_risk import (
    historical_cvar as calculate_historical_cvar,
    historical_var as calculate_historical_var,
)
from .turnover import calculate_turnover


class BacktestMetrics:
    """Read-only metrics adapter around an open-xquant ``RunResult``."""

    def __init__(
        self,
        result: Any,
        config: MetricConfig,
        benchmark_returns: pd.Series | None,
        gross_result: Any | None,
    ) -> None:
        self._result = result
        self._config = config
        self._equity = equity_series_from_result(result)
        self._benchmark_returns = (
            None
            if benchmark_returns is None
            else normalize_return_series(benchmark_returns)
        )
        self._gross_result = gross_result

    @classmethod
    def from_run_result(
        cls,
        result: Any,
        *,
        config: MetricConfig | None = None,
        benchmark_returns: pd.Series | None = None,
        gross_result: Any | None = None,
    ) -> "BacktestMetrics":
        """Create a metrics facade without changing the supplied RunResult."""

        resolved_config = MetricConfig() if config is None else config
        if not isinstance(resolved_config, MetricConfig):
            raise MetricConfigError("config must be a MetricConfig")
        return cls(result, resolved_config, benchmark_returns, gross_result)

    @property
    def config(self) -> MetricConfig:
        """Return immutable metric configuration."""

        return self._config

    def total_return(self) -> float:
        """Return total simple growth, or NaN for insufficient data."""

        return calculate_total_return(self._equity)

    def cagr(self) -> float:
        """Return CAGR using configured periods per year."""

        return calculate_cagr(self._equity, self._config.periods_per_year)

    def annualized_volatility(self) -> float:
        """Return annualized sample volatility of log returns."""

        return calculate_annualized_volatility(
            self._equity, self._config.periods_per_year
        )

    def downside_volatility(self) -> float:
        """Return annualized downside RMS below the configured MAR."""

        return calculate_downside_volatility(
            self._equity,
            self._config.periods_per_year,
            self._config.minimum_acceptable_return,
        )

    def sharpe_ratio(self) -> float:
        """Return annualized simple-return Sharpe ratio."""

        return calculate_sharpe_ratio(
            self._equity,
            self._config.periods_per_year,
            self._config.risk_free_rate,
        )

    def sortino_ratio(self) -> float:
        """Return RunResult-compatible log-return Sortino ratio."""

        return calculate_sortino_ratio(
            self._equity,
            self._config.periods_per_year,
            self._config.risk_free_rate,
            self._config.minimum_acceptable_return,
        )

    def drawdown_series(self) -> pd.Series:
        """Return drawdown relative to the running equity peak."""

        return calculate_drawdown_series(self._equity)

    def max_drawdown(self) -> float:
        """Return the most negative drawdown observation."""

        return calculate_max_drawdown(self._equity)

    def calmar_ratio(self) -> float:
        """Return CAGR divided by absolute maximum drawdown."""

        return calculate_calmar_ratio(self.cagr(), self.max_drawdown())

    def drawdown_events(self) -> tuple[DrawdownEvent, ...]:
        """Return threshold-defined drawdown events in chronological order."""

        return calculate_drawdown_events(
            self._equity, self._config.drawdown_threshold
        )

    def drawdown_summary(self) -> DrawdownSummary:
        """Return depth and duration summary for drawdown events."""

        return summarize_drawdowns(self._equity, self._config.drawdown_threshold)

    def max_drawdown_duration(self) -> int | None:
        """Return duration of the deepest event in trading sessions."""

        return self.drawdown_summary().max_drawdown_duration

    def longest_underwater_days(self) -> int | None:
        """Return longest threshold event duration in trading sessions."""

        return self.drawdown_summary().longest_underwater_days

    def worst_period_returns(self) -> dict[str, WorstPeriodReturn | None]:
        """Return worst daily, monthly, and annual simple returns."""

        return calculate_worst_period_returns(self._equity)

    def historical_var(self, confidence: float | None = None) -> float:
        """Return lower-tail historical VaR as a signed daily return."""

        level = self._confidence(confidence)
        return calculate_historical_var(simple_returns(self._equity), level)

    def historical_cvar(self, confidence: float | None = None) -> float:
        """Return historical expected shortfall as a signed daily return."""

        level = self._confidence(confidence)
        return calculate_historical_cvar(simple_returns(self._equity), level)

    def _confidence(self, confidence: float | None) -> float:
        level = self._config.cvar_confidence if confidence is None else confidence
        if not isinstance(level, (int, float)) or not math.isfinite(float(level)):
            raise MetricConfigError("confidence must be finite")
        if not 0 < float(level) < 1:
            raise MetricConfigError("confidence must be in (0, 1)")
        return float(level)

    def turnover(self) -> TurnoverResult:
        """Return weight-change and traded-notional turnover together."""

        return calculate_turnover(self._result, self._equity, self._config)

    def total_fees(self) -> float:
        """Return explicit fees summed over fills."""

        return total_fees(self._result)

    def cost_drag(self) -> CostDragResult:
        """Return fees and gross-to-net drag when a gross result is supplied."""

        return calculate_cost_drag(self._result, self._equity, self._gross_result)

    def exposure_series(self) -> pd.Series:
        """Return actual invested fraction from portfolio snapshots."""

        return calculate_exposure_series(self._result)

    def cash_ratio_series(self) -> pd.Series:
        """Return actual cash fraction from portfolio snapshots."""

        return calculate_cash_ratio_series(self._result)

    def average_exposure(self) -> float | None:
        """Return mean actual invested fraction, or None without snapshots."""

        return self.exposure_summary().average_exposure

    def exposure_summary(self) -> ExposureSummary:
        """Return actual exposure and cash summary."""

        return summarize_exposure(self._result)

    def cash_drag(
        self,
        benchmark_returns: pd.Series | None = None,
        benchmark_name: str = "benchmark",
    ) -> CashDragResult | None:
        """Estimate cash opportunity cost relative to a named benchmark."""

        resolved = (
            self._benchmark_returns if benchmark_returns is None else benchmark_returns
        )
        return calculate_cash_drag(
            self._result,
            self._equity,
            resolved,
            benchmark_name,
            self._config,
        )

    def summary(self) -> MetricReport:
        """Calculate all core scalar metrics and data-quality warnings."""

        worst = self.worst_period_returns()
        drawdowns = self.drawdown_summary()
        turnover = self.turnover()
        costs = self.cost_drag()
        exposure = self.exposure_summary()
        cash_drag = self.cash_drag()
        confidence_label = f"{self._config.cvar_confidence * 100:g}".replace(".", "_")
        metrics: dict[str, float | int | str | bool | None] = {
            "total_return": self.total_return(),
            "cagr": self.cagr(),
            "annualized_volatility": self.annualized_volatility(),
            "downside_volatility": self.downside_volatility(),
            "sharpe_ratio": self.sharpe_ratio(),
            "sortino_ratio": self.sortino_ratio(),
            "calmar_ratio": self.calmar_ratio(),
            "max_drawdown": drawdowns.max_drawdown,
            "max_drawdown_duration": drawdowns.max_drawdown_duration,
            "longest_underwater_days": drawdowns.longest_underwater_days,
            "worst_day": None if worst["day"] is None else worst["day"].value,
            "worst_month": None if worst["month"] is None else worst["month"].value,
            "worst_year": None if worst["year"] is None else worst["year"].value,
            f"historical_var_{confidence_label}": self.historical_var(),
            f"historical_cvar_{confidence_label}": self.historical_cvar(),
            "weight_turnover_total": turnover.weight_turnover_total,
            "weight_turnover_annualized": turnover.weight_turnover_annualized,
            "traded_notional_ratio": turnover.traded_notional_ratio,
            "traded_notional_ratio_annualized": turnover.traded_notional_ratio_annualized,
            "total_fees": costs.total_fees,
            "fee_ratio_initial_equity": costs.fee_ratio_initial_equity,
            "return_drag": costs.return_drag,
            "average_exposure": exposure.average_exposure,
            "average_cash_ratio": exposure.average_cash_ratio,
            "cash_drag": None if cash_drag is None else cash_drag.cash_drag,
        }
        for frequency, item in worst.items():
            metrics[f"worst_{frequency}_label"] = None if item is None else item.label
            metrics[f"worst_{frequency}_partial_period"] = (
                None if item is None else item.partial_period
            )
        warnings: list[str] = []
        if len(self._equity) < 2:
            warnings.append("insufficient_data")
        if not (getattr(self._result, "snapshots", None) or []):
            warnings.append("missing_snapshots")
        if turnover.weight_turnover_total is None:
            warnings.append("missing_adjusted_weights")
        if self._gross_result is None:
            warnings.append("missing_gross_result")
        if self._benchmark_returns is None:
            warnings.append("missing_benchmark")
        elif cash_drag is None:
            warnings.append("insufficient_cash_drag_alignment")
        if costs.path_dependent:
            warnings.append("path_dependent_cost_drag")
        cash_series = self.cash_ratio_series()
        exposure_series = self.exposure_series()
        if (
            (not cash_series.empty and (cash_series < 0).any())
            or (not exposure_series.empty and (exposure_series > 1).any())
        ):
            warnings.append("leverage_or_negative_cash")
        return MetricReport(metrics, tuple(dict.fromkeys(warnings)), self._config)

    def to_frame(self) -> pd.DataFrame:
        """Return one report row suitable for multi-strategy concatenation."""

        return pd.DataFrame([self.summary().to_json_dict()["metrics"]])

    def to_json_dict(self) -> dict[str, Any]:
        """Return a strict-JSON-compatible report dictionary."""

        return self.summary().to_json_dict()

