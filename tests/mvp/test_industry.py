from __future__ import annotations

import unittest

from xquant_assistant.data.industry import score_industry_cycle

from .helpers import market_frame


class IndustryCycleTests(unittest.TestCase):
    def test_trend_and_breadth_favor_stronger_industry(self) -> None:
        classifications = {
            "sh600001": {"industry_l1": "Strong", "industry_code": "s"},
            "sh600002": {"industry_l1": "Strong", "industry_code": "s"},
            "sh600003": {"industry_l1": "Weak", "industry_code": "w"},
            "sh600004": {"industry_l1": "Weak", "industry_code": "w"},
        }
        data = {
            "sh600001": market_frame(slope=0.08),
            "sh600002": market_frame(slope=0.07),
            "sh600003": market_frame(slope=-0.01),
            "sh600004": market_frame(slope=-0.02),
        }
        scores, metrics = score_industry_cycle(
            classifications,
            data,
            min_members=2,
            min_coverage=1.0,
        )
        self.assertGreater(scores["sh600001"], scores["sh600003"])
        self.assertGreater(metrics["Strong"]["trend_score"], metrics["Weak"]["trend_score"])
        self.assertGreater(metrics["Strong"]["breadth_score"], metrics["Weak"]["breadth_score"])


if __name__ == "__main__":
    unittest.main()
