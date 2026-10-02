"""Return, volatility, ratio, and worst-period calculations."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .models import WorstPeriodReturn


def simple_returns(equity: pd.Series) -> pd.Series:
    """Calculate simple period returns from a normalized equity series."""

    if len(equity) < 2:
        return pd.Series(index=equity.index[:0], dtype="float64", name="return")
    values = equity.to_numpy(dtype=np.float64)
    result = values[1:] / values[:-1] - 1.0
    return pd.Series(result, index=equity.index[1:], name="return", dtype="float64")


def log_returns(equity: pd.Series) -> pd.Series:
    """Calculate logarithmic period returns from positive equity values."""

    if len(equity) < 2:
        return pd.Series(index=equity.index[:0], dtype="float64", name="log_return")
    values = np.log(equity.to_numpy(dtype=np.float64))
    return pd.Series(
        np.diff(values), index=equity.index[1:], name="log_return", dtype="float64"
    )


def total_return(equity: pd.Series) -> float:
    """Return the simple return between first and last equity observations."""

    if len(equity) < 2:
        return float("nan")
    return float(equity.iloc[-1] / equity.iloc[0] - 1.0)


def cagr(equity: pd.Series, periods_per_year: int) -> float:
    """Annualize total growth using the number of observed transitions."""

    if len(equity) < 2:
        return float("nan")
    periods = len(equity) - 1
    return float((equity.iloc[-1] / equity.iloc[0]) ** (periods_per_year / periods) - 1)


def annualized_volatility(equity: pd.Series, periods_per_year: int) -> float:
    """Annualize sample standard deviation of logarithmic returns."""

    returns = log_returns(equity)
    if len(returns) < 2:
        return float("nan")
    return float(returns.std(ddof=1) * math.sqrt(periods_per_year))


def downside_volatility(
    equity: pd.Series,
    periods_per_year: int,
    minimum_acceptable_return: float,
) -> float:
    """Annualize the root mean square of log returns below daily MAR."""

    returns = log_returns(equity)
    if returns.empty:
        return float("nan")
    mar_daily = math.log1p(minimum_acceptable_return) / periods_per_year
    downside = returns.to_numpy(dtype=np.float64) - mar_daily
    downside = downside[downside < 0]
    if downside.size == 0:
        return float("nan")
    return float(np.sqrt(np.mean(np.square(downside))) * math.sqrt(periods_per_year))


def sharpe_ratio(
    equity: pd.Series, periods_per_year: int, risk_free_rate: float
) -> float:
    """Calculate a RunResult-compatible Sharpe ratio with annual risk-free rate."""

    returns = simple_returns(equity)
    if returns.empty:
        return float("nan")
    values = returns.to_numpy(dtype=np.float64)
    deviation = float(np.std(values, ddof=0))
    if deviation == 0 or not math.isfinite(deviation):
        return float("nan")
    annualized_excess = float(np.mean(values) * periods_per_year - risk_free_rate)
    return float(annualized_excess / (deviation * math.sqrt(periods_per_year)))


def sortino_ratio(
    equity: pd.Series,
    periods_per_year: int,
    risk_free_rate: float,
    minimum_acceptable_return: float,
) -> float:
    """Calculate Sortino using annualized mean log return and downside RMS."""

    returns = log_returns(equity)
    downside = downside_volatility(
        equity, periods_per_year, minimum_acceptable_return
    )
    if returns.empty or not math.isfinite(downside) or downside == 0:
        return float("nan")
    annualized_log_return = float(returns.mean() * periods_per_year)
    return float((annualized_log_return - risk_free_rate) / downside)


def calmar_ratio(cagr_value: float, max_drawdown: float) -> float:
    """Divide CAGR by absolute maximum drawdown when drawdown is nonzero."""

    if not math.isfinite(cagr_value) or not math.isfinite(max_drawdown):
        return float("nan")
    if max_drawdown == 0:
        return float("nan")
    return float(cagr_value / abs(max_drawdown))


def _partial_period(
    period: pd.Period,
    first_period: pd.Period,
    last_period: pd.Period,
    first_date: pd.Timestamp,
    last_date: pd.Timestamp,
) -> bool:
    begins_late = period == first_period and first_date.date() > period.start_time.date()
    ends_early = period == last_period and last_date.date() < period.end_time.date()
    return bool(begins_late or ends_early)


def _worst_grouped_return(
    equity: pd.Series, frequency: str, period_code: str
) -> WorstPeriodReturn | None:
    periods = equity.index.to_period(period_code)
    unique_periods = list(dict.fromkeys(periods))
    candidates: list[WorstPeriodReturn] = []
    previous_value = float(equity.iloc[0])
    previous_date = equity.index[0]
    for position, period in enumerate(unique_periods):
        mask = periods == period
        period_equity = equity.loc[mask]
        end_date = period_equity.index[-1]
        end_value = float(period_equity.iloc[-1])
        start_date = equity.index[0] if position == 0 else previous_date
        value = end_value / previous_value - 1.0
        candidates.append(
            WorstPeriodReturn(
                frequency=frequency,
                label=str(period),
                start=pd.Timestamp(start_date),
                end=pd.Timestamp(end_date),
                value=float(value),
                partial_period=_partial_period(
                    period,
                    unique_periods[0],
                    unique_periods[-1],
                    equity.index[0],
                    equity.index[-1],
                ),
            )
        )
        previous_value = end_value
        previous_date = end_date
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item.value, item.end))


def worst_period_returns(
    equity: pd.Series,
) -> dict[str, WorstPeriodReturn | None]:
    """Return the worst day, month, and year using simple returns."""

    empty = {"day": None, "month": None, "year": None}
    if len(equity) < 2:
        return empty
    daily = simple_returns(equity)
    worst_date = daily.idxmin()
    end_position = equity.index.get_loc(worst_date)
    day = WorstPeriodReturn(
        frequency="day",
        label=pd.Timestamp(worst_date).date().isoformat(),
        start=pd.Timestamp(equity.index[end_position - 1]),
        end=pd.Timestamp(worst_date),
        value=float(daily.loc[worst_date]),
        partial_period=False,
    )
    return {
        "day": day,
        "month": _worst_grouped_return(equity, "month", "M"),
        "year": _worst_grouped_return(equity, "year", "Y"),
    }

