from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from xquant_assistant.backtest_runner import ResearchBacktestRunner
from xquant_assistant.settings import load_config

from .helpers import setup_offline_project


class BacktestRunnerTests(unittest.TestCase):
    def test_research_backtest_exports_metrics_and_limitations(self) -> None:
        source = Path(__file__).resolve().parents[2]
        with TemporaryDirectory() as directory:
            project = Path(directory)
            setup_offline_project(project, source)
            config = load_config(project / "configs")
            run_dir, manifest = ResearchBacktestRunner(project, config).run(
                "2026-07-01",
                "2026-09-28",
                ("sh510300", "sh600000"),
                benchmark_symbol="sh510300",
                allow_network=False,
            )
            self.assertEqual(manifest["status"], "RESEARCH_ONLY_CURRENT_UNIVERSE")
            self.assertTrue((run_dir / "metrics.json").exists())
            self.assertTrue((run_dir / "equity_curve.csv").exists())
            self.assertTrue((run_dir / "trades.csv").exists())
            self.assertTrue((run_dir / "composite_benchmark.csv").exists())
            self.assertTrue((run_dir / "benchmark_summary.json").exists())
            self.assertTrue((run_dir / "fundamental_scores.csv").exists())
            self.assertTrue((run_dir / "industry_classification.csv").exists())
            payload = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
            self.assertIn("cagr", payload["metrics"])
            self.assertIn("cash_drag", payload["metrics"])
            self.assertIn(
                "sina_current_lists_cannot_reconstruct_historical_membership",
                manifest["limitations"],
            )


if __name__ == "__main__":
    unittest.main()
