from __future__ import annotations

import unittest

from xquant_assistant.domain import Security
from xquant_assistant.research.benchmark import build_composite_benchmark

from .helpers import market_frame


class CompositeBenchmarkTests(unittest.TestCase):
    def test_composite_is_half_of_each_equal_weight_sleeve(self) -> None:
        securities = (
            Security("e", "sh510300", "ETF", "CN_ETF", "SSE", "ETF"),
            Security("a", "sh600000", "A", "CN_A_MAIN", "SSE", "MAIN"),
        )
        data = {
            "sh510300": market_frame(periods=8, slope=0.02),
            "sh600000": market_frame(periods=8, slope=0.05),
        }
        result = build_composite_benchmark(
            securities,
            data,
            data["sh510300"].index[0].date().isoformat(),
            data["sh510300"].index[-1].date().isoformat(),
            rebalance_sessions=3,
        )
        expected = 0.5 * result["etf_sleeve_equity"] + 0.5 * result["a_share_sleeve_equity"]
        self.assertTrue(result["composite_equity"].equals(expected))
        self.assertGreater(result["composite_equity"].iloc[-1], 1.0)


if __name__ == "__main__":
    unittest.main()
