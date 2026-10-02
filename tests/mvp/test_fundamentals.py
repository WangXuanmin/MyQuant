from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

import pandas as pd

from xquant_assistant.data.fundamentals import (
    TdxQuantFundamentalProvider,
    normalize_tdxquant_direct_response,
    normalize_tdxquant_response,
    score_fundamentals,
)


class FundamentalTests(unittest.TestCase):
    def test_tdxquant_column_payload_is_normalized(self) -> None:
        payload = {
            "result": {
                "ErrorId": "0",
                "Value": {
                    "600000.SH": {
                        "announce_time": ["20260430", "20260830"],
                        "tag_time": ["20260331", "20260630"],
                        "FN197": ["10.5", "11.2"],
                        "FN232": ["100", "120"],
                    }
                },
            }
        }
        records = normalize_tdxquant_response(payload)
        self.assertEqual(tuple(records), ("sh600000",))
        self.assertEqual(records["sh600000"][0]["announce_time"], "2026-04-30")
        self.assertEqual(records["sh600000"][1]["FN197"], 11.2)

    def test_tdxquant_direct_dataframe_is_normalized(self) -> None:
        payload = {
            "600000.SH": pd.DataFrame(
                {
                    "announce_time": [20260430],
                    "tag_time": [20260331],
                    "FN197": [10.5],
                }
            )
        }
        records = normalize_tdxquant_direct_response(payload)
        self.assertEqual(records["sh600000"][0]["announce_time"], "2026-04-30")
        self.assertEqual(records["sh600000"][0]["FN197"], 10.5)

    def test_provider_can_load_direct_tqcenter_module(self) -> None:
        module_source = """
import pandas as pd
class tq:
    @classmethod
    def initialize(cls, path):
        cls.path = path
    @classmethod
    def get_financial_data(cls, **kwargs):
        return {'600000.SH': pd.DataFrame({
            'announce_time': [20260430],
            'tag_time': [20260331],
            'FN197': [10.5],
            'FN232': [100.0],
        })}
    @classmethod
    def close(cls):
        pass
"""
        with tempfile.TemporaryDirectory() as directory:
            tqcenter_path = Path(directory) / "tqcenter.py"
            tqcenter_path.write_text(module_source, encoding="utf-8")
            provider = TdxQuantFundamentalProvider(
                Path(directory) / "cache",
                "",
                tqcenter_path=tqcenter_path,
            )
            records = provider._fetch(("sh600000",), "2026-09-29")
        self.assertEqual(records["sh600000"][0]["FN232"], 100.0)

    def test_scoring_uses_announcement_date_and_hard_filters(self) -> None:
        records = {
            "sh600001": (
                {
                    "announce_time": "2026-04-30",
                    "tag_time": "2026-03-31",
                    "FN183": 12.0,
                    "FN191": 10.0,
                    "FN197": 15.0,
                    "FN210": 40.0,
                    "FN228": 1.2,
                    "FN232": 100.0,
                    "FN234": 110.0,
                    "FN336": 1.0,
                },
                {
                    "announce_time": "2026-08-30",
                    "tag_time": "2026-06-30",
                    "FN183": -5.0,
                    "FN191": -20.0,
                    "FN197": 2.0,
                    "FN210": 80.0,
                    "FN228": -0.5,
                    "FN232": -10.0,
                    "FN234": -20.0,
                    "FN336": 3.0,
                },
            ),
            "sh600002": (
                {
                    "announce_time": "2026-04-29",
                    "tag_time": "2026-03-31",
                    "FN183": 2.0,
                    "FN191": 1.0,
                    "FN197": 6.0,
                    "FN210": 65.0,
                    "FN228": 0.8,
                    "FN232": 30.0,
                    "FN234": 20.0,
                    "FN336": 0.0,
                },
            ),
        }
        april = score_fundamentals(records, "2026-06-01")
        self.assertTrue(april.available)
        self.assertGreater(april.values["sh600001"]["score"], april.values["sh600002"]["score"])
        self.assertTrue(april.values["sh600001"]["passes_filter"])

        september = score_fundamentals(records, "2026-09-01")
        self.assertFalse(september.values["sh600001"]["passes_filter"])
        self.assertIn("nonpositive_parent_net_profit", september.values["sh600001"]["filter_reasons"])

    def test_financial_industry_does_not_use_cash_flow_hard_filter(self) -> None:
        records = {
            "sh600000": ({
                "announce_time": "2026-04-30",
                "tag_time": "2026-03-31",
                "FN197": 10.0,
                "FN232": 100.0,
                "FN234": -1.0,
                "FN336": 1.0,
            },)
        }
        snapshot = score_fundamentals(
            records,
            "2026-05-01",
            {"sh600000": {"industry_l1": "银行"}},
        )
        self.assertTrue(snapshot.values["sh600000"]["passes_filter"])
        self.assertTrue(snapshot.values["sh600000"]["is_financial_industry"])


if __name__ == "__main__":
    unittest.main()
