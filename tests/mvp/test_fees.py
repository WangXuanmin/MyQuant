from __future__ import annotations

from pathlib import Path
import unittest

from xquant_assistant.fees import calculate_fee
from xquant_assistant.settings import load_config


class FeeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(Path(__file__).resolve().parents[2] / "configs").account

    def test_a_share_net_commission_buy_and_sell(self) -> None:
        buy = calculate_fee(100_000, "CN_A_MAIN", "BUY", self.config)
        sell = calculate_fee(100_000, "CN_A_MAIN", "SELL", self.config)
        self.assertAlmostEqual(buy.commission, 8.50)
        self.assertAlmostEqual(buy.regulatory_fee, 2.00)
        self.assertAlmostEqual(buy.exchange_handling_fee, 3.41)
        self.assertAlmostEqual(buy.transfer_fee, 1.00)
        self.assertEqual(buy.stamp_duty, 0.0)
        self.assertAlmostEqual(buy.total, 14.91)
        self.assertAlmostEqual(sell.stamp_duty, 50.00)
        self.assertAlmostEqual(sell.total, 64.91)

    def test_etf_uses_commission_and_fund_handling_fee(self) -> None:
        fee = calculate_fee(100_000, "CN_ETF", "BUY", self.config)
        self.assertAlmostEqual(fee.commission, 8.50)
        self.assertAlmostEqual(fee.exchange_handling_fee, 4.00)
        self.assertEqual(fee.regulatory_fee, 0.0)
        self.assertEqual(fee.transfer_fee, 0.0)
        self.assertEqual(fee.stamp_duty, 0.0)
        self.assertAlmostEqual(fee.total, 12.50)

    def test_commission_has_no_five_yuan_minimum(self) -> None:
        fee = calculate_fee(100, "CN_A_MAIN", "BUY", self.config)
        self.assertAlmostEqual(fee.commission, 0.0085)
        self.assertLess(fee.total, 5.0)


if __name__ == "__main__":
    unittest.main()
