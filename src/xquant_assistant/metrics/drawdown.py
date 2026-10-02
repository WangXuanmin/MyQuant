"""Threshold-aware drawdown event analysis."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .models import DrawdownEvent, DrawdownSummary


def drawdown_series(equity: pd.Series) -> pd.Series:
    """Calculate equity relative to its running high-water mark."""

    if equity.empty:
        return pd.Series(index=equity.index, dtype="float64", name="drawdown")
    values = equity.to_numpy(dtype=np.float64)
    peaks = np.maximum.accumulate(values)
    return pd.Series(
        values / peaks - 1.0,
        index=equity.index,
        dtype="float64",
        name="drawdown",
    )


def drawdown_events(
    equity: pd.Series, threshold: float
) -> tuple[DrawdownEvent, ...]:
    """Identify episodes below ``threshold`` without using calendar-day gaps."""

    series = drawdown_series(equity)
    if series.empty:
        return ()
    events: list[DrawdownEvent] = []
    in_event = False
    last_peak_position = 0
    event_peak_position = 0
    start_position = 0
    trough_position = 0
    trough_value = 0.0

    for position, value in enumerate(series.to_numpy(dtype=np.float64)):
        if value == 0.0:
            last_peak_position = position
        if not in_event and value < threshold:
            in_event = True
            event_peak_position = last_peak_position
            start_position = position
            trough_position = position
            trough_value = float(value)
            continue
        if not in_event:
            continue
        if value < trough_value:
            trough_position = position
            trough_value = float(value)
        if value >= threshold:
            events.append(
                DrawdownEvent(
                    peak_date=pd.Timestamp(series.index[event_peak_position]),
                    start_date=pd.Timestamp(series.index[start_position]),
                    trough_date=pd.Timestamp(series.index[trough_position]),
                    recovery_date=pd.Timestamp(series.index[position]),
                    depth=trough_value,
                    decline_sessions=trough_position - start_position,
                    recovery_sessions=position - trough_position,
                    total_sessions=position - start_position,
                    recovered=True,
                )
            )
            in_event = False

    if in_event:
        last_position = len(series) - 1
        events.append(
            DrawdownEvent(
                peak_date=pd.Timestamp(series.index[event_peak_position]),
                start_date=pd.Timestamp(series.index[start_position]),
                trough_date=pd.Timestamp(series.index[trough_position]),
                recovery_date=None,
                depth=trough_value,
                decline_sessions=trough_position - start_position,
                recovery_sessions=None,
                total_sessions=last_position - start_position,
                recovered=False,
            )
        )
    return tuple(events)


def max_drawdown(equity: pd.Series) -> float:
    """Return the most negative point of the drawdown series."""

    if equity.empty:
        return float("nan")
    return float(drawdown_series(equity).min())


def summarize_drawdowns(
    equity: pd.Series, threshold: float
) -> DrawdownSummary:
    """Summarize deepest-event duration and longest threshold event separately."""

    maximum = max_drawdown(equity)
    events = drawdown_events(equity, threshold)
    if len(equity) < 2:
        return DrawdownSummary(maximum, None, None, None, False, 0)
    if not events:
        return DrawdownSummary(maximum, 0, 0, None, False, 0)
    deepest = min(events, key=lambda event: (event.depth, event.start_date))
    recoveries = [
        event.recovery_sessions
        for event in events
        if event.recovery_sessions is not None
    ]
    return DrawdownSummary(
        max_drawdown=maximum,
        max_drawdown_duration=deepest.total_sessions,
        longest_underwater_days=max(event.total_sessions for event in events),
        longest_recovery_sessions=max(recoveries) if recoveries else None,
        unrecovered=any(not event.recovered for event in events),
        event_count=len(events),
    )

