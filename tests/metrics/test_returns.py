from __future__ import annotations

import math
import unittest

import numpy as np

from xquant_assistant.metrics import BacktestMetrics, MetricConfig, MetricConfigError

from .helpers import make_result


class ReturnMetricTests(unittest.TestCase):
    def test_stable_rise_has_undefined_downside_ratios(self) -> None:
        result = make_result([100, 101, 102, 103])
        metrics = BacktestMetrics.from_run_result(result)
        self.assertGreater(metrics.cagr(), 0)
        self.assertEqual(metrics.max_drawdown(), 0)
        self.assertTrue(math.isnan(metrics.downside_volatility()))
        self.assertTrue(math.isnan(metrics.sortino_ratio()))
        self.assertTrue(math.isnan(metrics.calmar_ratio()))
        self.assertGreater(metrics.worst_period_returns()["day"].value, 0)

    def test_existing_run_result_regression(self) -> None:
        result = make_result([100, 105, 99, 110, 103])
        metrics = BacktestMetrics.from_run_result(result)
        self.assertAlmostEqual(metrics.cagr(), result.annualized_return(), places=12)
        self.assertAlmostEqual(metrics.sortino_ratio(), result.sortino_ratio(), places=12)
        self.assertAlmostEqual(metrics.calmar_ratio(), result.calmar_ratio(), places=12)
        self.assertAlmostEqual(metrics.max_drawdown(), result.max_drawdown(), places=12)
        self.assertAlmostEqual(
            metrics.annualized_volatility(), result.annualized_volatility(), places=12
        )

    def test_worst_month_and_partial_period(self) -> None:
        result = make_result(
            [100, 90, 99, 80],
            dates=["2024-01-02", "2024-01-31", "2024-02-29", "2024-03-01"],
        )
        worst = BacktestMetrics.from_run_result(result).worst_period_returns()
        self.assertEqual(worst["month"].label, "2024-03")
        self.assertAlmostEqual(worst["month"].value, 80 / 99 - 1)
        self.assertTrue(worst["month"].partial_period)

    def test_single_point_returns_nan_and_none(self) -> None:
        metrics = BacktestMetrics.from_run_result(make_result([100]))
        self.assertTrue(np.isnan(metrics.cagr()))
        self.assertIsNone(metrics.worst_period_returns()["day"])
        self.assertIn("insufficient_data", metrics.summary().warnings)

    def test_configuration_validation(self) -> None:
        for kwargs in (
            {"periods_per_year": 0},
            {"drawdown_threshold": 0},
            {"cvar_confidence": 1},
            {"minimum_acceptable_return": -1},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(MetricConfigError):
                    MetricConfig(**kwargs)


if __name__ == "__main__":
    unittest.main()

