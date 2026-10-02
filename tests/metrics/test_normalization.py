from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from xquant_assistant.metrics import MetricInputError
from xquant_assistant.metrics.normalization import (
    equity_series_from_result,
    normalize_return_series,
)

from .helpers import make_result


class NormalizationTests(unittest.TestCase):
    def test_equity_is_float_datetime_series(self) -> None:
        result = make_result([100, 101], dates=["2024-01-02", "2024-01-03"])
        equity = equity_series_from_result(result)
        self.assertEqual(str(equity.dtype), "float64")
        self.assertIsInstance(equity.index, pd.DatetimeIndex)
        self.assertEqual(equity.name, "equity")

    def test_equity_rejects_duplicate_unsorted_nonfinite_and_nonpositive(self) -> None:
        cases = [
            make_result([100, 101], dates=["2024-01-02", "2024-01-02"]),
            make_result([100, 101], dates=["2024-01-03", "2024-01-02"]),
            make_result([100, np.inf]),
            make_result([100, 0]),
        ]
        for result in cases:
            with self.subTest(curve=result.equity_curve):
                with self.assertRaises(MetricInputError):
                    equity_series_from_result(result)

    def test_return_normalization_preserves_name_and_timezone(self) -> None:
        index = pd.date_range("2024-01-02", periods=2, tz="Asia/Shanghai")
        returns = pd.Series([0.01, -0.02], index=index, name="benchmark")
        normalized = normalize_return_series(returns)
        self.assertEqual(normalized.name, "benchmark")
        self.assertEqual(str(normalized.index.tz), "Asia/Shanghai")

    def test_return_normalization_rejects_nan(self) -> None:
        returns = pd.Series([0.01, np.nan], index=pd.date_range("2024-01-02", periods=2))
        with self.assertRaises(MetricInputError):
            normalize_return_series(returns)


if __name__ == "__main__":
    unittest.main()

