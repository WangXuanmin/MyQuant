"""Historical tail-return statistics."""

from __future__ import annotations

import numpy as np
import pandas as pd


def historical_var(returns: pd.Series, confidence: float) -> float:
    """Return the lower-tail historical return quantile."""

    if len(returns) < 2:
        return float("nan")
    return float(np.quantile(returns.to_numpy(dtype=np.float64), 1 - confidence))


def historical_cvar(returns: pd.Series, confidence: float) -> float:
    """Return the mean of returns at or below historical VaR."""

    value_at_risk = historical_var(returns, confidence)
    if not np.isfinite(value_at_risk):
        return float("nan")
    tail = returns[returns <= value_at_risk]
    if tail.empty:
        return float("nan")
    return float(tail.mean())

