"""Explicit fee and gross-to-net cost-drag calculations."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .errors import MetricInputError
from .models import CostDragResult
from .normalization import equity_series_from_result
from .returns import total_return


def total_fees(result: Any) -> float:
    """Sum explicit fill fees without adding slippage or opportunity cost."""

    total = 0.0
    for fill in getattr(result, "trades", None) or []:
        try:
            fee = float(fill.fee)
        except (AttributeError, TypeError, ValueError) as exc:
            raise MetricInputError("each trade must expose a numeric fee") from exc
        if not np.isfinite(fee):
            raise MetricInputError("trade fees must be finite")
        total += fee
    return float(total)


def _trade_path(result: Any) -> tuple[tuple[Any, ...], ...]:
    path: list[tuple[Any, ...]] = []
    for fill in getattr(result, "trades", None) or []:
        try:
            path.append(
                (
                    fill.order.symbol,
                    fill.order.side,
                    int(fill.order.shares),
                    str(fill.filled_at),
                )
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise MetricInputError("invalid trade path in RunResult") from exc
    return tuple(path)


def _final_positions(result: Any) -> tuple[tuple[str, float], ...] | None:
    snapshots = getattr(result, "snapshots", None) or []
    if not snapshots:
        return None
    positions = getattr(snapshots[-1], "positions", None)
    if positions is None:
        return None
    try:
        return tuple(
            sorted((str(symbol), float(position.shares)) for symbol, position in positions.items())
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise MetricInputError("invalid final positions in RunResult") from exc


def _path_dependent(net_result: Any, gross_result: Any) -> bool:
    if _trade_path(net_result) != _trade_path(gross_result):
        return True
    net_positions = _final_positions(net_result)
    gross_positions = _final_positions(gross_result)
    return (
        net_positions is not None
        and gross_positions is not None
        and net_positions != gross_positions
    )


def calculate_cost_drag(
    result: Any, equity: pd.Series, gross_result: Any | None
) -> CostDragResult:
    """Calculate explicit fees and, when supplied, gross-minus-net drag."""

    fees = total_fees(result)
    initial_equity = float(equity.iloc[0]) if not equity.empty else None
    fee_ratio = None if initial_equity is None else float(fees / initial_equity)
    net_return = total_return(equity)
    if gross_result is None:
        return CostDragResult(
            total_fees=fees,
            fee_ratio_initial_equity=fee_ratio,
            gross_total_return=None,
            net_total_return=net_return,
            return_drag=None,
            terminal_value_drag=None,
            path_dependent=False,
        )

    gross_equity = equity_series_from_result(gross_result)
    if equity.empty or gross_equity.empty:
        raise MetricInputError("gross and net results must both contain equity")
    if equity.index[0] != gross_equity.index[0] or equity.index[-1] != gross_equity.index[-1]:
        raise MetricInputError("gross and net results must share start and end dates")
    if not np.isclose(
        float(equity.iloc[0]), float(gross_equity.iloc[0]), rtol=1e-12, atol=1e-12
    ):
        raise MetricInputError("gross and net results must share initial equity")
    gross_return = total_return(gross_equity)
    return CostDragResult(
        total_fees=fees,
        fee_ratio_initial_equity=fee_ratio,
        gross_total_return=gross_return,
        net_total_return=net_return,
        return_drag=float(gross_return - net_return),
        terminal_value_drag=float(gross_equity.iloc[-1] - equity.iloc[-1]),
        path_dependent=_path_dependent(result, gross_result),
    )

