from __future__ import annotations

import unittest

from xquant_assistant.metrics import BacktestMetrics, MetricInputError

from .helpers import make_fill, make_result


class CostTests(unittest.TestCase):
    def test_fees_and_gross_net_drag(self) -> None:
        net = make_result(
            [100, 105, 110], trades=[make_fill(shares=100, fee="2.50")]
        )
        gross = make_result(
            [100, 110, 120], trades=[make_fill(shares=110, fee="0")]
        )
        result = BacktestMetrics.from_run_result(net, gross_result=gross).cost_drag()
        self.assertEqual(result.total_fees, 2.5)
        self.assertAlmostEqual(result.fee_ratio_initial_equity, 0.025)
        self.assertAlmostEqual(result.gross_total_return, 0.20)
        self.assertAlmostEqual(result.net_total_return, 0.10)
        self.assertAlmostEqual(result.return_drag, 0.10)
        self.assertAlmostEqual(result.terminal_value_drag, 10.0)
        self.assertTrue(result.path_dependent)

    def test_missing_gross_keeps_fee_data_only(self) -> None:
        net = make_result([100, 101], trades=[make_fill(fee="1.25")])
        result = BacktestMetrics.from_run_result(net).cost_drag()
        self.assertEqual(result.total_fees, 1.25)
        self.assertIsNone(result.return_drag)
        self.assertIsNone(result.terminal_value_drag)

    def test_gross_result_must_align(self) -> None:
        net = make_result([100, 101], dates=["2024-01-02", "2024-01-03"])
        gross = make_result([100, 102], dates=["2024-01-02", "2024-01-04"])
        with self.assertRaises(MetricInputError):
            BacktestMetrics.from_run_result(net, gross_result=gross).cost_drag()


if __name__ == "__main__":
    unittest.main()

