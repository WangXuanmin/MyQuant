from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from datetime import datetime

from xquant_assistant.runner import DailyRunner
from xquant_assistant.settings import load_config

from .helpers import setup_offline_project


class RunnerTests(unittest.TestCase):
    def test_offline_run_writes_complete_artifacts(self) -> None:
        source = Path(__file__).resolve().parents[2]
        with TemporaryDirectory() as directory:
            project = Path(directory)
            setup_offline_project(project, source)
            config = load_config(project / "configs")
            manifest, run_dir = DailyRunner(project, config, clock=lambda: datetime.fromisoformat('2026-09-28T16:10:00+08:00')).run(
                "2026-09-28",
                symbols=("sh510300", "sh600000"),
                allow_network=False,
            )
            self.assertEqual(manifest.status, "DEGRADED_PRICE_VOLUME_ONLY")
            required = {
                "run_manifest.json",
                "data_quality.json",
                "security_status.csv",
                "universe.csv",
                "excluded_instruments.csv",
                "factor_scores.parquet",
                "industry_classification.csv",
                "industry_metrics.csv",
                "fundamental_scores.csv",
                "candidate_ranking.csv",
                "evidence_cards.json",
                "target_weights.csv",
                "risk_report.json",
                "orders_draft.csv",
                "daily_report.html",
                "fills.csv",
                "positions_after.json",
                "cash_ledger.csv",
                "reconciliation.json",
                "performance_snapshot.json",
            }
            self.assertTrue(required.issubset({path.name for path in run_dir.iterdir()}))
            payload = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(payload["external_execution_enabled"])
            self.assertEqual(payload["execution_mode"], "LOCAL_SIMULATED")
            self.assertNotIn("fee_model_not_configured", " ".join(payload["warnings"]))
            orders = (run_dir / "orders_draft.csv").read_text(encoding="utf-8-sig")
            self.assertIn("estimated_fee", orders)


if __name__ == "__main__":
    unittest.main()
