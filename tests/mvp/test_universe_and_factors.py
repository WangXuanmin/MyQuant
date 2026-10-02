from __future__ import annotations

import unittest

from xquant_assistant.data.fundamentals import DisabledFundamentalProvider
from xquant_assistant.data.industry import DisabledIndustryProvider
from xquant_assistant.data.security_master import _limit_price, classify_a_share
from xquant_assistant.settings import load_config
from xquant_assistant.strategies import VolumeFundamentalStrategy
from xquant_assistant.universe import build_universe, refine_universe

from .helpers import market_frame
from xquant_assistant.domain import Security


class UniverseAndFactorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from pathlib import Path

        cls.config = load_config(Path(__file__).resolve().parents[2] / "configs")

    def test_main_board_rules_exclude_growth_boards(self) -> None:
        self.assertEqual(classify_a_share("sh600000"), ("SSE", "MAIN"))
        self.assertEqual(classify_a_share("sz000001"), ("SZSE", "MAIN"))
        self.assertEqual(classify_a_share("sz300001"), ("SZSE", "CHINEXT"))
        self.assertEqual(classify_a_share("sh688001"), ("SSE", "STAR"))
        self.assertEqual(_limit_price(10.01, 0.10, 0.01, True), 11.01)
        self.assertEqual(_limit_price(10.01, 0.10, 0.01, False), 9.01)

    def test_universe_filters_and_market_refinement(self) -> None:
        main = Security("a", "sh600000", "A", "CN_A_MAIN", "SSE", "MAIN")
        growth = Security("b", "sz300001", "B", "CN_A_OTHER", "SZSE", "CHINEXT")
        snapshot = build_universe([main, growth], "2026-09-28", self.config.universe)
        self.assertEqual([item.symbol for item in snapshot.included], ["sh600000"])
        self.assertIn("board_excluded", snapshot.excluded[0].reasons)
        refined = refine_universe(
            snapshot, {"sh600000": market_frame()}, self.config.universe
        )
        self.assertEqual(len(refined.included), 1)

    def test_strategy_degrades_openly_to_price_volume(self) -> None:
        securities = (
            Security("a", "sh600000", "A", "CN_A_MAIN", "SSE", "MAIN"),
            Security("b", "sh601000", "B", "CN_A_MAIN", "SSE", "MAIN"),
        )
        data = {
            "sh600000": market_frame(slope=0.05),
            "sh601000": market_frame(slope=0.01),
        }
        fundamentals = DisabledFundamentalProvider().load(tuple(data), "2026-09-28")
        industry = DisabledIndustryProvider().load(tuple(data), "2026-09-28")
        candidates = VolumeFundamentalStrategy(self.config.strategy).rank_candidates(
            securities, data, fundamentals, industry
        )
        self.assertEqual(candidates[0].symbol, "sh600000")
        self.assertTrue(all(item.factor_mode == "DEGRADED_PRICE_VOLUME_ONLY" for item in candidates))
        self.assertTrue(all(item.effective_component_weights == {"price_volume": 1.0} for item in candidates))


if __name__ == "__main__":
    unittest.main()
