from __future__ import annotations

import unittest

import pandas as pd

from xquant_assistant.metrics import BacktestMetrics, MetricConfigError

from .helpers import make_result


class TailRiskTests(unittest.TestCase):
    def test_cvar_is_signed_tail_return(self) -> None:
        metrics = BacktestMetrics.from_run_result(
            make_result([100, 90, 99, 79.2, 83.16, 74.844])
        )
        var = metrics.historical_var(0.8)
        cvar = metrics.historical_cvar(0.8)
        self.assertLessEqual(cvar, var)
        self.assertLess(cvar, 0)

    def test_short_sample_is_undefined(self) -> None:
        metrics = BacktestMetrics.from_run_result(make_result([100, 99]))
        self.assertTrue(pd.isna(metrics.historical_cvar()))

    def test_invalid_override_is_rejected(self) -> None:
        metrics = BacktestMetrics.from_run_result(make_result([100, 99, 101]))
        with self.assertRaises(MetricConfigError):
            metrics.historical_var(1.0)


if __name__ == "__main__":
    unittest.main()

