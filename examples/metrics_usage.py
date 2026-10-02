"""Offline example covering the complete public metrics workflow."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
import json

import pandas as pd

from oxq.core.types import BarSnapshot, Fill, Order, Portfolio
from oxq.portfolio.analytics import RunResult
from xquant_assistant.metrics import BacktestMetrics, MetricConfig


DATES = pd.bdate_range("2024-01-02", periods=6)


def build_result(values: list[float], *, fee: str) -> RunResult:
    weights = [
        {"CASH": 1.0},
        {"510300.SH": 0.5, "CASH": 0.5},
        {"510300.SH": 0.5, "CASH": 0.5},
        {"510300.SH": 0.8, "CASH": 0.2},
        {"510300.SH": 0.8, "CASH": 0.2},
        {"510300.SH": 0.8, "CASH": 0.2},
    ]
    snapshots = [
        BarSnapshot(
            date=date,
            target_weights=weight,
            adjusted_weights=weight,
            positions={},
            cash=float(value * weight.get("CASH", 0.0)),
            total_value=float(value),
        )
        for date, value, weight in zip(DATES, values, weights, strict=True)
    ]
    trade = Fill(
        order=Order(symbol="510300.SH", side="BUY", shares=100),
        filled_price=Decimal("4.00"),
        filled_at=DATES[1].date().isoformat(),
        fee=Decimal(fee),
    )
    return RunResult(
        portfolio=Portfolio(cash=Decimal(str(values[-1]))),
        trades=[trade],
        equity_curve=list(zip(DATES, values, strict=True)),
        mktdata={},
        snapshots=snapshots,
    )


def main() -> None:
    net_result = build_result([500_000, 505_000, 490_000, 510_000, 508_000, 520_000], fee="8")
    gross_result = build_result([500_000, 505_100, 490_200, 510_300, 508_400, 520_500], fee="0")
    benchmark_returns = pd.Series(
        [0.008, -0.02, 0.025, -0.005, 0.018],
        index=DATES[1:],
        name="沪深300",
    )
    metrics = BacktestMetrics.from_run_result(
        net_result,
        config=MetricConfig(),
        benchmark_returns=benchmark_returns,
        gross_result=gross_result,
    )

    print("单策略指标总表")
    print(metrics.to_frame().to_string(index=False))
    print("\n回撤事件")
    print(pd.DataFrame(asdict(event) for event in metrics.drawdown_events()).to_string(index=False))
    print("\n双口径换手率")
    print(asdict(metrics.turnover()))
    print("\n毛净成本侵蚀")
    print(asdict(metrics.cost_drag()))
    print("\n资金利用率与现金拖累")
    print(asdict(metrics.exposure_summary()))
    print(asdict(metrics.cash_drag(benchmark_name="沪深300")))
    print("\n严格 JSON")
    print(json.dumps(metrics.to_json_dict(), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()

