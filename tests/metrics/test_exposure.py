from __future__ import annotations

import unittest

import pandas as pd

from xquant_assistant.metrics import BacktestMetrics, MetricInputError

from .helpers import make_result, make_snapshot


class ExposureTests(unittest.TestCase):
    def test_average_exposure_from_actual_snapshots(self) -> None:
        snapshots = [
            make_snapshot("2024-01-02", cash=100, total_value=100),
            make_snapshot("2024-01-03", cash=40, total_value=100),
            make_snapshot("2024-01-04", cash=10, total_value=100),
        ]
        metrics = BacktestMetrics.from_run_result(
            make_result([100, 100, 100], snapshots=snapshots)
        )
        self.assertEqual(list(metrics.exposure_series()), [0.0, 0.6, 0.9])
        summary = metrics.exposure_summary()
        self.assertAlmostEqual(summary.average_exposure, 0.5)
        self.assertAlmostEqual(summary.average_cash_ratio, 0.5)

    def test_missing_snapshots_returns_empty_and_none(self) -> None:
        metrics = BacktestMetrics.from_run_result(make_result([100, 101]))
        self.assertTrue(metrics.exposure_series().empty)
        self.assertIsNone(metrics.average_exposure())

    def test_negative_cash_is_reported_without_clipping(self) -> None:
        snapshots = [make_snapshot("2024-01-02", cash=-10, total_value=100)]
        metrics = BacktestMetrics.from_run_result(
            make_result([100], snapshots=snapshots)
        )
        self.assertEqual(metrics.exposure_series().iloc[0], 1.1)
        self.assertIn("leverage_or_negative_cash", metrics.summary().warnings)

    def test_nonpositive_total_value_is_invalid(self) -> None:
        snapshots = [make_snapshot("2024-01-02", cash=0, total_value=0)]
        metrics = BacktestMetrics.from_run_result(
            make_result([100], snapshots=snapshots)
        )
        with self.assertRaises(MetricInputError):
            metrics.exposure_series()

    def test_cash_drag_uses_previous_snapshot_cash(self) -> None:
        snapshots = [
            make_snapshot("2024-01-02", cash=100, total_value=100),
            make_snapshot("2024-01-03", cash=0, total_value=100),
            make_snapshot("2024-01-04", cash=0, total_value=100),
        ]
        result = make_result([100, 101, 102], snapshots=snapshots)
        index = pd.to_datetime(["2024-01-03", "2024-01-04"])
        benchmark = pd.Series([0.02, 0.02], index=index)
        drag = BacktestMetrics.from_run_result(
            result, benchmark_returns=benchmark
        ).cash_drag(benchmark_name="沪深300")
        actual = (1.01 * (102 / 101)) - 1
        counterfactual = (1.03 * (102 / 101)) - 1
        self.assertAlmostEqual(drag.actual_total_return, actual)
        self.assertAlmostEqual(drag.counterfactual_total_return, counterfactual)
        self.assertAlmostEqual(drag.average_cash_ratio, 0.5)
        self.assertGreater(drag.cash_drag, 0)

    def test_falling_benchmark_can_make_cash_drag_negative(self) -> None:
        snapshots = [
            make_snapshot("2024-01-02", cash=50, total_value=100),
            make_snapshot("2024-01-03", cash=50, total_value=100),
            make_snapshot("2024-01-04", cash=50, total_value=100),
        ]
        result = make_result([100, 100, 100], snapshots=snapshots)
        benchmark = pd.Series(
            [-0.02, -0.01],
            index=pd.to_datetime(["2024-01-03", "2024-01-04"]),
        )
        drag = BacktestMetrics.from_run_result(
            result, benchmark_returns=benchmark
        ).cash_drag()
        self.assertLess(drag.cash_drag, 0)


if __name__ == "__main__":
    unittest.main()

