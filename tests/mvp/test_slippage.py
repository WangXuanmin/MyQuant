from __future__ import annotations

from pathlib import Path
import unittest

from xquant_assistant.execution.pricing import apply_slippage, slippage_bps_for
from xquant_assistant.settings import load_config


class SlippageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(Path(__file__).resolve().parents[2] / "configs").account

    def test_asset_specific_one_way_slippage(self) -> None:
        self.assertEqual(slippage_bps_for("CN_ETF", self.config), 5.0)
        self.assertEqual(slippage_bps_for("CN_A_MAIN", self.config), 10.0)
        self.assertAlmostEqual(apply_slippage(10.0, "CN_ETF", "BUY", self.config), 10.005)
        self.assertAlmostEqual(apply_slippage(10.0, "CN_A_MAIN", "BUY", self.config), 10.01)
        self.assertAlmostEqual(apply_slippage(10.0, "CN_A_MAIN", "SELL", self.config), 9.99)


if __name__ == "__main__":
    unittest.main()
