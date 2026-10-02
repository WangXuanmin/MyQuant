from __future__ import annotations

import unittest

from xquant_assistant.metrics import BacktestMetrics

from .helpers import make_result


class DrawdownTests(unittest.TestCase):
    def test_recovered_drawdown_event(self) -> None:
        result = make_result([100, 110, 88, 92, 110, 112])
        metrics = BacktestMetrics.from_run_result(result)
        event = metrics.drawdown_events()[0]
        self.assertAlmostEqual(event.depth, -0.20)
        self.assertEqual(event.peak_date, result.equity_curve[1][0])
        self.assertEqual(event.start_date, result.equity_curve[2][0])
        self.assertEqual(event.trough_date, result.equity_curve[2][0])
        self.assertEqual(event.recovery_date, result.equity_curve[4][0])
        self.assertEqual(event.decline_sessions, 0)
        self.assertEqual(event.recovery_sessions, 2)
        self.assertEqual(event.total_sessions, 2)
        self.assertTrue(event.recovered)
        self.assertEqual(metrics.max_drawdown_duration(), 2)

    def test_unrecovered_drawdown(self) -> None:
        result = make_result([100, 120, 90, 95])
        metrics = BacktestMetrics.from_run_result(result)
        event = metrics.drawdown_events()[0]
        self.assertIsNone(event.recovery_date)
        self.assertFalse(event.recovered)
        self.assertEqual(event.total_sessions, 1)
        self.assertTrue(metrics.drawdown_summary().unrecovered)

    def test_noise_inside_threshold_is_not_an_event(self) -> None:
        metrics = BacktestMetrics.from_run_result(make_result([100, 99.95, 100.1]))
        self.assertEqual(metrics.drawdown_events(), ())
        self.assertEqual(metrics.longest_underwater_days(), 0)

    def test_longest_event_can_differ_from_deepest(self) -> None:
        result = make_result([100, 90, 100, 99, 98, 99, 100, 101])
        metrics = BacktestMetrics.from_run_result(result)
        summary = metrics.drawdown_summary()
        self.assertAlmostEqual(summary.max_drawdown, -0.10)
        self.assertEqual(summary.max_drawdown_duration, 1)
        self.assertEqual(summary.longest_underwater_days, 3)


if __name__ == "__main__":
    unittest.main()
