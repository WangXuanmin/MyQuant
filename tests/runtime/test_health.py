from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from xquant_assistant.runtime.health import data_health
from .helpers import fixture


class HealthAcceptance(unittest.TestCase):
    def test_readonly_health_reports_stale_and_missing_opening_data(self):
        config, calendar, _, frames = fixture()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/config.data.cache_dir
            path.mkdir(parents=True)
            frames['sh510300'].loc[:'2026-09-01'].to_parquet(path/'sh510300.parquet')
            report = data_health(root, config, calendar, datetime.fromisoformat('2026-09-02T16:10:00+08:00'))
            self.assertEqual(report['status'], 'DATA_REFRESH_REQUIRED')
            self.assertEqual(report['market_caches'][0]['stale_sessions'], 1)
            self.assertEqual(report['opening_archives'], 0)
            self.assertFalse((root/'data/state').exists())
