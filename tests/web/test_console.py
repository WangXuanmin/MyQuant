"""Web acceptance uses isolated engineering accounts; never the user's account."""
from contextlib import closing
from dataclasses import asdict
from datetime import datetime
from http.client import HTTPConnection
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from xquant_assistant.execution.ledger import PaperLedger
from xquant_assistant.settings import load_config
from xquant_assistant.storage import hash_payload
from xquant_assistant.web.server import make_server
from xquant_assistant.web.service import BusyError, WebService, symbol_list
from tests.mvp.helpers import setup_offline_project, market_frame
from tests.runtime.helpers import PROJECT, opening


class ConsoleAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.securities = setup_offline_project(self.root, PROJECT)
        self.now = datetime.fromisoformat('2026-09-28T16:10:00+08:00')
        self.service = WebService(self.root, clock=lambda:self.now)
        self.server = make_server(self.root, 0, service=self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port
        _,_,data = self.request('GET','/api/state')
        self.token = json.loads(data)['csrf_token']

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self,method,path,body=None,headers=None):
        defaults = {'Content-Type':'application/json','X-Local-Token':self.token} if body is not None else {}
        defaults.update(headers or {})
        con = HTTPConnection('127.0.0.1',self.port,timeout=30)
        con.request(method,path,json.dumps(body).encode() if body is not None else None,defaults)
        response = con.getresponse()
        result = response.status,dict(response.getheaders()),response.read()
        con.close()
        return result

    def finished(self,body):
        status,_,payload=self.request('POST','/api/jobs',body)
        self.assertEqual(status,202,payload)
        key=json.loads(payload)['id']
        end=time.monotonic()+30
        while time.monotonic()<end:
            job=next(x for x in self.service.job_list() if x['id']==key)
            if job['status'] not in {'QUEUED','RUNNING'}:
                return job
            time.sleep(.03)
        self.fail('job timeout')

    def daily(self):
        job=self.finished({'action':'daily','date':'2026-09-28','symbols':'sh510300,sh600000','offline':True})
        self.assertEqual(job['status'],'SUCCEEDED',job)
        return job

    def test_get_state_market_and_assets_are_readonly(self):
        for path in ('/','/style.css','/app.js','/api/state','/api/jobs','/api/market?symbol=sh510300'):
            status,headers,data=self.request('GET',path)
            self.assertEqual(status,200,data)
            self.assertEqual(headers['Cache-Control'],'no-store')
            self.assertIn("script-src 'self'",headers['Content-Security-Policy'])
        state=self.service.state()
        self.assertEqual(state['account']['cash'],500000)
        self.assertEqual(state['curve'],[])
        self.assertFalse(self.service.ledger().path.exists())
        self.assertFalse((self.root/'data/web').exists())

    def test_local_origin_token_and_host_are_required(self):
        body={'action':'daily','symbols':'sh510300','offline':True}
        for headers in ({'X-Local-Token':''},{'Origin':'https://other.example'},{'Host':'attacker.example'}):
            self.assertEqual(self.request('POST','/api/jobs',body,headers)[0],403)
        self.assertEqual(self.request('GET','/api/state',headers={'Host':'attacker.example'})[0],403)
        self.assertEqual(self.service.job_list(),[])
        self.assertFalse(self.service.ledger().path.exists())

    def test_bad_actions_dates_and_symbols_never_queue(self):
        for body in ({'action':'reset'},{'action':'daily','symbols':'sh510300;rm'},
                     {'action':'backtest','symbols':'sh510300','start':'bad','end':'2026-09-28'},
                     {'action':'daily','symbols':'sh510300','offline':'false'},
                     {'action':'backtest','symbols':'sh510300','start':'2026-09-29','end':'2026-09-28'}):
            self.assertEqual(self.request('POST','/api/jobs',body)[0],400)
        self.assertEqual(symbol_list('sh510300， SH510300 sz000001'),('sh510300','sz000001'))
        self.assertFalse(self.service.ledger().path.exists())

    def test_preview_invalid_weights_and_stale_config_have_no_effect(self):
        original=load_config(self.root/'configs').raw
        digest=hash_payload(original)
        body={'expected_hash':digest,'changes':{'strategy':{'rebalance_sessions':5}},'preview':True}
        self.assertEqual(self.request('POST','/api/config',body)[0],200)
        weights={**original['strategy']['price_volume_weights'],'momentum_20':.99}
        self.assertEqual(self.request('POST','/api/config',{'expected_hash':digest,'changes':{'strategy':{'price_volume_weights':weights}}})[0],400)
        self.assertEqual(self.request('POST','/api/config',{'expected_hash':'old','changes':{'risk':{'min_cash_weight':.1}}})[0],409)
        for changes in ({'account':{'initial_cash':1}},{'runtime':{'drawdown_action':'block'}},{'data':{'market_source':'other'}}):
            self.assertEqual(self.request('POST','/api/config',{'expected_hash':digest,'changes':changes})[0],400)
        self.assertEqual(original,load_config(self.root/'configs').raw)
        self.assertFalse((self.root/'data/backups').exists())

    def test_config_save_versions_backs_up_and_preserves_account(self):
        self.daily()
        before=self.service.ledger().load()
        original=self.service.config().raw
        status,_,payload=self.request('POST','/api/config',{'expected_hash':hash_payload(original),
            'changes':{'universe':{'custom_include':['sh510300']},'strategy':{'rebalance_sessions':5}}})
        self.assertEqual(status,200,payload)
        saved=json.loads(payload)
        self.assertTrue((Path(saved['backup'])/'strategy.yaml').exists())
        self.assertNotEqual(original['strategy']['version'],self.service.config().strategy.version)
        self.assertEqual(before,self.service.ledger().load())
        self.assertEqual(self.service.config().account,load_config(Path(saved['backup'])).account)

    def test_serialized_jobs_reject_double_submit_and_config_mutation(self):
        gate=threading.Event()
        with patch.object(self.service,'_execute',side_effect=lambda job:gate.wait(5)):
            try:
                self.service.submit({'action':'refresh_calendar'})
                self.assertEqual(self.request('POST','/api/jobs',{'action':'refresh_calendar'})[0],409)
                self.assertEqual(self.request('POST','/api/config',{'expected_hash':self.service.state()['config_hash'],
                    'changes':{'risk':{'min_cash_weight':.1}}})[0],409)
            finally:
                gate.set()
                self.service.lock.release()

    def test_daily_repeat_analysis_and_rebuild_use_existing_ledger(self):
        first=self.daily()
        before=self.service.ledger().load()
        self.assertEqual(before.revision,1)
        self.assertGreater(len(before.pending),0)
        second=self.daily()
        self.assertEqual(first['result']['run_id'],second['result']['run_id'])
        for body in ({'action':'analysis','date':'2026-09-28','symbols':'sh510300,sh600000','offline':True},
                     {'action':'rebuild','date':'2026-09-28'}):
            self.assertEqual(self.finished(body)['status'],'SUCCEEDED')
        self.assertEqual(before,self.service.ledger().load())
        self.assertEqual(self.service.state()['candidate_source']['id'],first['result']['run_id'])

    def test_next_session_fill_matches_fixed_draft_quantity(self):
        self.daily()
        before=self.service.ledger().load()
        draft={x.order_id:x.shares for x in before.pending}
        self.now=datetime.fromisoformat('2026-09-29T16:10:00+08:00')
        for security,slope in zip(self.securities,(.03,.05)):
            market_frame(end='2026-09-29',periods=141,slope=slope).to_parquet(self.root/f'data/market/{security.symbol}.parquet')
        directory=self.root/'data/opening_status'
        directory.mkdir(parents=True,exist_ok=True)
        (directory/'2026-09-29.json').write_text(json.dumps({'observations':[asdict(opening(x,'2026-09-29')) for x in self.securities]}),encoding='utf-8')
        job=self.finished({'action':'daily','date':'2026-09-29','symbols':'sh510300,sh600000','offline':True})
        self.assertEqual(job['status'],'SUCCEEDED',job)
        self.assertEqual(self.service.ledger().load().revision,2)
        fills=self.service.ledger().history()[-1]['fills']
        self.assertGreater(len(fills),0)
        for fill in fills:
            self.assertEqual(fill['shares'],draft[fill['order_id']])
            self.assertGreater(fill['fee'],0)
        self.assertLess(self.service.state()['account']['cash'],500000)

    def test_weekend_and_backdated_jobs_do_not_create_an_account(self):
        for day in ('2026-09-27','2026-09-01'):
            job=self.finished({'action':'daily','date':day,'symbols':'sh510300','offline':True})
            self.assertEqual(job['status'],'FAILED')
        self.assertFalse(self.service.ledger().path.exists())

    def test_reports_export_bytes_and_reject_path_escape(self):
        job=self.daily()
        key=job['result']['run_id']
        status,headers,data=self.request('GET',f'/artifact/{key}/run_manifest.json')
        self.assertEqual(status,200,data)
        self.assertIn('attachment',headers['Content-Disposition'])
        self.assertEqual(json.loads(data)['as_of_date'],'2026-09-28')
        status,headers,_=self.request('GET',f'/artifact/{key}/daily_report.html')
        self.assertEqual(status,200)
        self.assertTrue(headers['Content-Security-Policy'].startswith('sandbox;'))
        for path in ('/api/report?id=..','/artifact/../configs/account.yaml',f'/artifact/{key}/%2e%2e%5caccount.yaml','/artifact/./latest.json'):
            self.assertEqual(self.request('GET',path)[0],400)

    def test_restart_recovers_records_without_rerunning_jobs(self):
        self.daily()
        before=self.service.ledger().load()
        self.service.save_job({'id':'interrupted','action':'daily','status':'RUNNING','submitted_at':self.now.isoformat()})
        restarted=WebService(self.root,clock=lambda:self.now)
        self.assertEqual(restarted.job_list()[0]['status'],'INTERRUPTED')
        self.assertFalse(restarted.lock.locked())
        self.assertEqual(before,restarted.ledger().load())
        self.assertEqual(len(restarted.job_list()),2)

    def test_research_and_offline_refresh_do_not_initialize_daily_account(self):
        for body in ({'action':'refresh_data','symbols':'sh510300,sh600000','offline':True},
                     {'action':'backtest','symbols':'sh510300,sh600000','start':'2026-09-21','end':'2026-09-28','benchmark':'sh510300','offline':True}):
            job=self.finished(body)
            self.assertEqual(job['status'],'SUCCEEDED',job)
        self.assertFalse(self.service.ledger().path.exists())
        research=[x for x in self.service.runs() if x['kind']=='research']
        self.assertEqual(len(research),1)
        report=self.service.report(research[0]['id'])
        self.assertTrue(report['metrics'])
        self.assertFalse(report['legacy'])

    def test_opening_probe_is_explicitly_blocked_and_never_trades(self):
        with patch('xquant_assistant.data.opening_status.capture_opening_status',return_value={'status':'PROBE_ONLY','observations':[]}):
            job=self.finished({'action':'capture','symbols':'sh510300'})
        self.assertEqual(job['status'],'BLOCKED')
        self.assertFalse(self.service.ledger().path.exists())

    def test_calendar_refresh_uses_existing_provider(self):
        from xquant_assistant.runtime.calendar import load_calendar
        calendar=load_calendar(self.root)
        with patch('xquant_assistant.web.service.refresh_calendar',return_value=calendar) as provider:
            job=self.finished({'action':'refresh_calendar'})
        provider.assert_called_once_with(self.root)
        self.assertEqual(job['status'],'SUCCEEDED')
        self.assertFalse(self.service.ledger().path.exists())

    def test_clean_checkout_without_runtime_data_is_readonly(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            import shutil
            shutil.copytree(PROJECT/'configs',root/'configs')
            service=WebService(root,clock=lambda:self.now)
            state=service.state()
            self.assertEqual(state['calendar']['status'],'BLOCKED')
            self.assertEqual(state['cached_symbols'],[])
            self.assertEqual(state['curve'],[])
            self.assertEqual(state['account']['cash'],500000)
            self.assertFalse((root/'data').exists())

    def test_same_port_cannot_start_an_ambiguous_second_server(self):
        with self.assertRaises(OSError):
            make_server(self.root,self.port)


if __name__=='__main__':
    unittest.main()
