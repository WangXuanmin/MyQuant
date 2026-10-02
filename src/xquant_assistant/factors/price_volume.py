"""Point-in-time price-volume factors and cross-sectional scoring."""

from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd


FACTOR_NAMES = (
    "momentum_20",
    "momentum_60",
    "ma_structure",
    "volume_ratio_5_20",
    "price_volume_corr_20",
    "breakout_60",
    "inverse_realized_vol_20",
)


def calculate_price_volume_factors(frame: pd.DataFrame) -> dict[str, float | None]:
    """Calculate the configured factors using only rows already visible."""

    if len(frame) < 61:
        return {name: None for name in FACTOR_NAMES}
    close = frame["close"].astype(float)
    volume = frame["volume"].astype(float)
    simple = close.pct_change(fill_method=None)
    log_return = np.log(close).diff()
    ma20 = float(close.tail(20).mean())
    ma60 = float(close.tail(60).mean())
    latest = float(close.iloc[-1])
    realized = float(log_return.tail(20).std(ddof=1) * np.sqrt(252))
    volume_change = volume.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    correlation = simple.tail(20).corr(volume_change.tail(20))
    raw = {
        "momentum_20": latest / float(close.iloc[-21]) - 1.0,
        "momentum_60": latest / float(close.iloc[-61]) - 1.0,
        "ma_structure": 0.5 * float(latest > ma20) + 0.5 * float(ma20 > ma60),
        "volume_ratio_5_20": float(volume.tail(5).mean() / volume.tail(20).mean()),
        "price_volume_corr_20": float(correlation),
        "breakout_60": latest / float(close.tail(60).max()) - 1.0,
        "inverse_realized_vol_20": None if realized <= 0 else 1.0 / realized,
    }
    return {
        name: (
            float(value)
            if value is not None and np.isfinite(float(value))
            else None
        )
        for name, value in raw.items()
    }


def _percentile(values: pd.Series) -> pd.Series:
    valid = values.dropna()
    result = pd.Series(np.nan, index=values.index, dtype="float64")
    if valid.empty:
        return result
    if valid.nunique() <= 1:
        result.loc[valid.index] = 0.5
    else:
        result.loc[valid.index] = valid.rank(method="average", pct=True)
    return result


def score_price_volume(
    raw_by_symbol: Mapping[str, dict[str, float | None]],
    weights: Mapping[str, float],
) -> tuple[dict[str, dict[str, float | None]], dict[str, float]]:
    """Percentile-normalize a cross section and combine available factors."""

    if not raw_by_symbol:
        return {}, {}
    raw_frame = pd.DataFrame.from_dict(raw_by_symbol, orient="index")
    normalized = pd.DataFrame(index=raw_frame.index)
    for name in weights:
        normalized[name] = _percentile(raw_frame[name].astype("float64"))
    scores: dict[str, float] = {}
    for symbol, row in normalized.iterrows():
        available = {
            name: float(weight)
            for name, weight in weights.items()
            if pd.notna(row[name]) and weight > 0
        }
        denominator = sum(available.values())
        if denominator == 0:
            scores[symbol] = float("nan")
        else:
            scores[symbol] = float(
                sum(row[name] * weight for name, weight in available.items())
                / denominator
            )
    normalized_records = {
        symbol: {
            name: None if pd.isna(value) else float(value)
            for name, value in row.items()
        }
        for symbol, row in normalized.iterrows()
    }
    return normalized_records, scores

