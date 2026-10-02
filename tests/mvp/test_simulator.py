"""Regression: the public simulator uses the same causal engine."""
import unittest
from xquant_assistant.execution import LocalSimulator
from tests.runtime.helpers import fixture, inputs, pending_state

class SimulatorTests(unittest.TestCase):
    def test_ready_order_fills_at_current_valid_open(self):
        config,calendar,securities,frames=fixture()
        day="2026-09-02"
        result=LocalSimulator(config,calendar).process_session(pending_state(day,securities,calendar),inputs(day,securities,frames))
        self.assertEqual(len(result.fills),1)
        self.assertTrue(result.fills[0].fill_is_hypothetical)
        self.assertEqual(result.fills[0].filled_at,day)
