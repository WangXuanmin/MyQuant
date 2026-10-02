from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import unittest
import subprocess
import sys
from xquant_assistant.execution.ledger import PaperLedger,LedgerConflict
from xquant_assistant.runtime.engine import SessionEngine
from xquant_assistant.runtime.models import RuntimeState
from xquant_assistant.domain import AccountState
from .helpers import fixture,inputs,pending_state


class LedgerAcceptance(unittest.TestCase):
    def setUp(self):
        self.config,self.calendar,self.securities,self.frames=fixture()
        self.day='2026-09-02'
        self.engine=SessionEngine(self.config,self.calendar)

    def make_result(self, ledger):
        return self.engine.step(ledger.load(),inputs(self.day,self.securities,self.frames))

    def test_A01_same_session_and_revision_are_unique(self):
        with TemporaryDirectory() as root:
            ledger=PaperLedger(root)
            ledger.initialize(self.config.account)
            result=self.make_result(ledger)
            duplicate=deepcopy(result)
            ledger.commit(result,'config','input',{})
            first=ledger.load()
            with self.assertRaisesRegex(LedgerConflict,'SESSION_ALREADY_COMMITTED'):
                ledger.commit(duplicate,'config','input',{})
            self.assertEqual(first,ledger.load())
            self.assertEqual(len(ledger.history()),1)

    def test_A03_two_writers_one_success(self):
        with TemporaryDirectory() as root:
            ledger=PaperLedger(root)
            ledger.initialize(self.config.account)
            first=self.make_result(ledger)
            second=deepcopy(first)
            def submit(result):
                try:
                    PaperLedger(root).commit(result,'config','input',{})
                    return 'COMMITTED'
                except LedgerConflict:
                    return 'CONFLICT'
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes=list(pool.map(submit,[first,second]))
            self.assertCountEqual(outcomes,['COMMITTED','CONFLICT'])
            self.assertEqual(ledger.load().revision,1)

    def test_A03_two_processes_one_transaction(self):
        with TemporaryDirectory() as directory:
            ledger = PaperLedger(directory)
            ledger.initialize(self.config.account)
            program = '''
import sys
from tests.runtime.helpers import fixture, inputs
from xquant_assistant.execution.ledger import PaperLedger, LedgerConflict
from xquant_assistant.runtime.engine import SessionEngine
config,calendar,securities,frames=fixture()
ledger=PaperLedger(sys.argv[1])
result=SessionEngine(config,calendar).step(ledger.load(),inputs('2026-09-02',securities,frames))
print('READY',flush=True)
sys.stdin.readline()
try:
    ledger.commit(result,'config','input',{})
    print('COMMITTED',flush=True)
except LedgerConflict:
    print('CONFLICT',flush=True)
'''
            workers = [subprocess.Popen([sys.executable, '-c', program, directory], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
            try:
                for worker in workers:
                    self.assertEqual(worker.stdout.readline().strip(), 'READY')
                for worker in workers:
                    worker.stdin.write('commit\n')
                    worker.stdin.flush()
                outputs = [worker.communicate(timeout=15) for worker in workers]
                self.assertCountEqual([x[0].strip() for x in outputs], ['COMMITTED', 'CONFLICT'])
                self.assertTrue(all(worker.returncode == 0 for worker in workers), outputs)
                self.assertEqual(ledger.load().revision, 1)
            finally:
                for worker in workers:
                    if worker.poll() is None:
                        worker.kill()
                    worker.communicate()

    def test_A04_faults_roll_back_or_retain_whole_commit(self):
        for stage in ('after_state_write','before_commit','after_commit'):
            with self.subTest(stage=stage),TemporaryDirectory() as root:
                ledger=PaperLedger(root)
                ledger.initialize(self.config.account)
                result=self.make_result(ledger)
                before=ledger.load()
                def fault(value):
                    if value==stage:
                        raise RuntimeError('injected_fault')
                with self.assertRaisesRegex(RuntimeError,'injected_fault'):
                    ledger.commit(result,'config','input',{},fault=fault)
                if stage=='after_commit':
                    self.assertEqual(ledger.load().revision,1)
                    self.assertIsNotNone(ledger.get_run(self.day))
                else:
                    self.assertEqual(before,ledger.load())
                    self.assertIsNone(ledger.get_run(self.day))

    def test_fill_and_account_roll_back_together(self):
        with TemporaryDirectory() as root:
            ledger=PaperLedger(root)
            ledger.initialize(self.config.account)
            state=pending_state(self.day,self.securities,self.calendar)
            from xquant_assistant.execution.ledger import encode
            with closing(ledger.connect()) as con,con:
                con.execute('UPDATE accounts SET state_json=?',(encode(state),))
            result=self.make_result(ledger)
            self.assertEqual(len(result.fills),1)
            def fault(stage):
                if stage=='before_commit':
                    raise RuntimeError('crash')
            with self.assertRaises(RuntimeError):
                ledger.commit(result,'config','input',{},fault=fault)
            self.assertEqual(ledger.load().account.cash,500000)
            self.assertEqual(ledger.health()['fills'],0)
            ledger.commit(self.make_result(ledger),'config','input',{})
            self.assertEqual(ledger.health()['fills'],1)
            self.assertLess(ledger.load().account.cash,500000)

    def test_A18_migration_preserves_money_and_archives_orders(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)/'state'
            root.mkdir()
            original={'currency':'CNY','cash':500000,'positions':{},'realized_pnl':0,'last_updated':'2026-09-28'}
            account_path=root/'paper_account.json'
            account_path.write_text(json.dumps(original),encoding='utf-8')
            order={'order_id':'legacy','signal_date':'2026-09-28'}
            (root/'pending_orders.json').write_text(json.dumps({'orders':[order]}),encoding='utf-8')
            ledger=PaperLedger(root)
            state=ledger.migrate(self.config.account,cancel_pending=True)
            self.assertEqual(state.account.cash,500000)
            self.assertEqual(state.pending,())
            self.assertEqual(json.loads(account_path.read_text()),original)
            self.assertEqual(len(list((root.parent/'backups').glob('*/manifest.json'))),1)
            with closing(ledger.connect()) as con:
                event=json.loads(con.execute('SELECT payload FROM events').fetchone()[0])
                self.assertEqual(event['status'],'CANCELLED')
            with self.assertRaises(LedgerConflict):
                ledger.migrate(self.config.account,cancel_pending=True)

    def test_A18_nonempty_or_mismatched_legacy_requires_review(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            payload={'currency':'CNY','cash':490000,'positions':{},'realized_pnl':0}
            (root/'paper_account.json').write_text(json.dumps(payload))
            ledger=PaperLedger(root)
            with self.assertRaisesRegex(LedgerConflict,'REVIEW_REQUIRED'):
                ledger.migrate(self.config.account,cancel_pending=True)
            self.assertFalse(ledger.path.exists())
