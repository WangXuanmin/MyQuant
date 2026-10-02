from __future__ import annotations

from pathlib import Path
import unittest

from xquant_assistant.domain import (
    AccountState,
    CandidateScore,
    DataQualityReport,
    TargetWeight,
)
from xquant_assistant.portfolio import build_orders, build_target_weights, evaluate_risk
from xquant_assistant.settings import load_config

from .helpers import market_frame


def candidate(symbol: str, asset_type: str, rank: int = 1) -> CandidateScore:
    return CandidateScore(
        symbol=symbol,
        name=symbol,
        asset_type=asset_type,
        raw_factors={},
        normalized_factors={},
        component_scores={"price_volume": 0.8},
        effective_component_weights={"price_volume": 1.0},
        total_score=0.8,
        rank=rank,
        selected=True,
        factor_mode="DEGRADED_PRICE_VOLUME_ONLY",
        reasons=(),
    )


class PortfolioAndRiskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(Path(__file__).resolve().parents[2] / "configs")

    def test_sleeves_caps_cash_and_orders(self) -> None:
        account = AccountState("CNY", 500_000, {})
        data = {
            "sh510300": market_frame(slope=0.03),
            "sh600000": market_frame(slope=0.05),
        }
        targets = build_target_weights(
            (candidate("sh510300", "CN_ETF"), candidate("sh600000", "CN_A_MAIN")),
            account,
            data,
            self.config.risk,
        )
        weights = {item.symbol: item.target_weight for item in targets}
        self.assertEqual(weights["sh510300"], 0.30)
        self.assertEqual(weights["sh600000"], 0.08)
        self.assertGreaterEqual(1 - sum(weights.values()), 0.05)
        orders = build_orders(
            targets,
            account,
            "2026-09-28",
            lot_size=100,
            fee_config=self.config.account,
        )
        self.assertTrue(all(order.shares % 100 == 0 for order in orders))
        self.assertTrue(all(order.estimated_fee is not None for order in orders))
        self.assertTrue(all(order.estimated_fee > 0 for order in orders))
        report, resolved = evaluate_risk(
            targets,
            orders,
            account,
            data,
            DataQualityReport("2026-09-28", ()),
            self.config.risk,
            self.config.account,
            industry_available=False,
        )
        self.assertFalse(report.blocked)
        self.assertTrue(all(order.status == "READY_LOCAL_SIMULATION" for order in resolved))

    def test_industry_concentration_blocks_overweight_target(self) -> None:
        targets = (
            TargetWeight("sh600001", "CN_A_MAIN", 0.0, 0.15, 10.0, 75_000.0),
            TargetWeight("sh600002", "CN_A_MAIN", 0.0, 0.15, 10.0, 75_000.0),
        )
        report, _ = evaluate_risk(
            targets,
            (),
            AccountState("CNY", 500_000.0, {}),
            {"sh600001": market_frame(), "sh600002": market_frame()},
            DataQualityReport("2026-09-28", ()),
            self.config.risk,
            self.config.account,
            industry_available=True,
            portfolio_drawdown=0.0,
            industry_classifications={
                "sh600001": {"industry_l1": "银行"},
                "sh600002": {"industry_l1": "银行"},
            },
        )
        industry_checks = [item for item in report.checks if item.rule == "industry_concentration"]
        self.assertEqual(industry_checks[0].status, "BLOCKED")
        fee_check = next(check for check in report.checks if check.rule == "fee_model")
        self.assertEqual(fee_check.status, "PASS")


if __name__ == "__main__":
    unittest.main()
