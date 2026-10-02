from __future__ import annotations

import unittest

from xquant_assistant.metrics import BacktestMetrics, MetricConfig

from .helpers import make_fill, make_result, make_snapshot


class TurnoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshots = [
            make_snapshot(
                "2024-01-02", cash=100, total_value=100, weights={"CASH": 1.0}
            ),
            make_snapshot(
                "2024-01-03",
                cash=0,
                total_value=100,
                weights={"A": 0.5, "B": 0.5, "CASH": 0.0},
            ),
            make_snapshot(
                "2024-01-04",
                cash=0,
                total_value=100,
                weights={"A": 0.2, "B": 0.8, "CASH": 0.0},
            ),
        ]

    def test_initial_allocation_switch(self) -> None:
        result = make_result([100, 100, 100], snapshots=self.snapshots)
        excluded = BacktestMetrics.from_run_result(result).turnover()
        included = BacktestMetrics.from_run_result(
            result, config=MetricConfig(include_initial_turnover=True)
        ).turnover()
        self.assertAlmostEqual(excluded.weight_turnover_total, 0.3)
        self.assertAlmostEqual(included.weight_turnover_total, 1.3)

    def test_traded_notional_is_two_sided_and_not_halved(self) -> None:
        fills = [
            make_fill(side="BUY", shares=100, price="4"),
            make_fill(side="SELL", shares=50, price="5"),
        ]
        result = make_result(
            [1000, 1000, 1000], snapshots=self.snapshots, trades=fills
        )
        turnover = BacktestMetrics.from_run_result(result).turnover()
        self.assertEqual(turnover.traded_notional, 650)
        self.assertAlmostEqual(turnover.traded_notional_ratio, 0.65)

    def test_missing_snapshots_does_not_invent_weight_turnover(self) -> None:
        turnover = BacktestMetrics.from_run_result(make_result([100, 101])).turnover()
        self.assertIsNone(turnover.weight_turnover_total)
        self.assertEqual(turnover.traded_notional, 0)


if __name__ == "__main__":
    unittest.main()

