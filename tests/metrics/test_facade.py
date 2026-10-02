from __future__ import annotations

import json
from pathlib import Path
import unittest

import pandas as pd

from xquant_assistant.metrics import BacktestMetrics, MetricReport

from .helpers import make_result, make_snapshot


class FacadeTests(unittest.TestCase):
    def test_summary_contains_required_keys(self) -> None:
        snapshots = [
            make_snapshot("2024-01-02", cash=100, total_value=100),
            make_snapshot("2024-01-03", cash=50, total_value=100),
            make_snapshot("2024-01-04", cash=25, total_value=100),
        ]
        benchmark = pd.Series(
            [0.01, -0.01],
            index=pd.to_datetime(["2024-01-03", "2024-01-04"]),
        )
        metrics = BacktestMetrics.from_run_result(
            make_result([100, 95, 105], snapshots=snapshots),
            benchmark_returns=benchmark,
        )
        report = metrics.summary()
        self.assertIsInstance(report, MetricReport)
        required = {
            "total_return",
            "cagr",
            "annualized_volatility",
            "downside_volatility",
            "sharpe_ratio",
            "sortino_ratio",
            "calmar_ratio",
            "max_drawdown",
            "max_drawdown_duration",
            "longest_underwater_days",
            "worst_day",
            "worst_month",
            "worst_year",
            "historical_var_95",
            "historical_cvar_95",
            "weight_turnover_total",
            "weight_turnover_annualized",
            "traded_notional_ratio",
            "traded_notional_ratio_annualized",
            "total_fees",
            "fee_ratio_initial_equity",
            "return_drag",
            "average_exposure",
            "average_cash_ratio",
            "cash_drag",
        }
        self.assertTrue(required.issubset(report.metrics))

    def test_json_is_strict_and_undefined_values_become_null(self) -> None:
        metrics = BacktestMetrics.from_run_result(make_result([100, 101, 102]))
        payload = metrics.to_json_dict()
        encoded = json.dumps(payload, allow_nan=False)
        self.assertIn('"calmar_ratio": null', encoded)
        self.assertIn("missing_snapshots", payload["warnings"])
        self.assertIn("missing_gross_result", payload["warnings"])
        self.assertIn("missing_benchmark", payload["warnings"])

    def test_to_frame_is_one_row_and_supports_comparison(self) -> None:
        first = BacktestMetrics.from_run_result(make_result([100, 101, 103])).to_frame()
        second = BacktestMetrics.from_run_result(make_result([100, 99, 102])).to_frame()
        comparison = pd.concat([first, second], keys=["strategy_a", "strategy_b"])
        self.assertEqual(comparison.shape[0], 2)
        self.assertIn("cagr", comparison.columns)

    def test_json_golden_fixture(self) -> None:
        snapshots = [
            make_snapshot("2024-01-02", cash=100, total_value=100, weights={"CASH": 1}),
            make_snapshot("2024-01-03", cash=50, total_value=100, weights={"A": 0.5, "CASH": 0.5}),
            make_snapshot("2024-01-04", cash=20, total_value=100, weights={"A": 0.8, "CASH": 0.2}),
            make_snapshot("2024-01-05", cash=20, total_value=100, weights={"A": 0.8, "CASH": 0.2}),
        ]
        benchmark = pd.Series(
            [0.01, -0.02, 0.015],
            index=pd.to_datetime(["2024-01-03", "2024-01-04", "2024-01-05"]),
        )
        net = make_result([100, 105, 99, 110], snapshots=snapshots)
        gross = make_result([100, 106, 101, 112], snapshots=snapshots)
        actual = BacktestMetrics.from_run_result(
            net, benchmark_returns=benchmark, gross_result=gross
        ).to_json_dict()
        fixture_path = Path(__file__).parent / "fixtures" / "metric_report_golden.json"
        expected = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
