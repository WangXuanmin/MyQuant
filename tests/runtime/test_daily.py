from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import replace
from contextlib import closing,redirect_stdout
from io import StringIO
import json
import unittest
from datetime import datetime
from xquant_assistant.runner import DailyRunner
from xquant_assistant.execution.ledger import PaperLedger
from xquant_assistant.settings import load_config
from xquant_assistant.cli import main
from tests.mvp.helpers import setup_offline_project
from .helpers import PROJECT


class DailyAcceptance(unittest.TestCase):
    def runner(self,root,config):
        return DailyRunner(root,config,clock=lambda:datetime.fromisoformat('2026-09-28T16:10:00+08:00'))
    def setup_project(self,directory):
        root=Path(directory)
        setup_offline_project(root,PROJECT)
        return root,load_config(root/'configs')

    def test_A01_A02_repeat_changed_config_analysis_revision(self):
        with TemporaryDirectory() as directory:
            root,config=self.setup_project(directory)
            runner=self.runner(root,config)
            manifest,path=runner.run('2026-09-28',symbols=('sh510300','sh600000'),allow_network=False)
            ledger=PaperLedger(root/'data/state')
            initial=ledger.load()
            second,path2=runner.run('2026-09-28',symbols=('sh510300','sh600000'),allow_network=False)
            self.assertEqual(path,path2)
            self.assertEqual(initial,ledger.load())
            changed=replace(config,raw={**config.raw,'analysis_note':'changed input'})
            third,path3=self.runner(root,changed).run('2026-09-28',allow_network=False)
            self.assertEqual(initial,ledger.load())
            revision,revision_path=self.runner(root,changed).run('2026-09-28',symbols=('sh510300','sh600000'),allow_network=False,analysis_revision=True)
            self.assertEqual(revision.status,'ANALYSIS_REVISION')
            self.assertNotEqual(path,revision_path)
            self.assertEqual(initial,ledger.load())
            saved=json.loads((path/'runtime_result.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['state']['revision'],1)
            self.assertEqual(manifest.account_revision_after,1)
            self.assertFalse(ledger.get_run('2026-09-28')['exports']['forward_observation']['eligible'])

    def test_A04_A19_post_commit_crash_then_rebuild(self):
        with TemporaryDirectory() as directory:
            root,config=self.setup_project(directory)
            runner=self.runner(root,config)
            def fault(stage):
                if stage=='after_commit':
                    raise RuntimeError('export_failure')
            with self.assertRaisesRegex(RuntimeError,'export_failure'):
                runner.run('2026-09-28',symbols=('sh510300','sh600000'),allow_network=False,fault=fault)
            ledger=PaperLedger(root/'data/state')
            self.assertEqual(ledger.load().revision,1)
            self.assertFalse(ledger.get_run('2026-09-28')['exported'])
            before=ledger.load()
            path=runner.rebuild_report('2026-09-28')
            self.assertTrue((path/'daily_report.html').exists())
            self.assertEqual(before,ledger.load())
            self.assertTrue(ledger.get_run('2026-09-28')['exported'])
            self.assertTrue((root/'data/monitoring/last_failure.json').exists())

    def test_nontrading_and_missing_calendar_do_not_initialize_account(self):
        with TemporaryDirectory() as directory:
            root,config=self.setup_project(directory)
            runner=self.runner(root,config)
            with self.assertRaisesRegex(ValueError,'NON_TRADING_DAY'):
                runner.run('2026-09-27',symbols=('sh510300',),allow_network=False)
            self.assertFalse((root/'data/state/paper_ledger.sqlite3').exists())
            (root/'data/calendar/sessions.json').unlink()
            with self.assertRaisesRegex(ValueError,'CALENDAR_UNAVAILABLE'):
                runner.run('2026-09-28',symbols=('sh510300',),allow_network=False)
            self.assertFalse((root/'data/state/paper_ledger.sqlite3').exists())

    def test_health_command_is_readonly(self):
        with TemporaryDirectory() as directory:
            root,config=self.setup_project(directory)
            output=StringIO()
            with redirect_stdout(output):
                result=main(['--project-root',str(root),'doctor'])
            self.assertEqual(result,0)
            payload=json.loads(output.getvalue())
            self.assertEqual(payload['calendar']['status'],'PASS')
            self.assertEqual(payload['ledger']['status'],'NOT_INITIALIZED')
            self.assertFalse((root/'data/state/paper_ledger.sqlite3').exists())

    def test_backdated_operational_run_is_rejected(self):
        with TemporaryDirectory() as directory:
            root,config=self.setup_project(directory)
            with self.assertRaisesRegex(ValueError,'HISTORICAL_SESSION_REQUIRES_ISOLATED_REPLAY'):
                self.runner(root,config).run('2026-09-01',symbols=('sh510300',),allow_network=False)
            self.assertFalse((root/'data/state/paper_ledger.sqlite3').exists())

    def test_analysis_without_original_commit_does_not_create_account(self):
        with TemporaryDirectory() as directory:
            root,config=self.setup_project(directory)
            with self.assertRaisesRegex(ValueError,'ANALYSIS_REVISION_REQUIRES_COMMITTED_SESSION'):
                self.runner(root,config).run('2026-09-28',allow_network=False,analysis_revision=True)
            self.assertFalse((root/'data/state/paper_ledger.sqlite3').exists())
