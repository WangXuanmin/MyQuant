"""Reproducible engineering acceptance; all account writes stay in --output."""
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT/'src'), str(PROJECT)]

import pandas as pd
from xquant_assistant.domain import AccountState, json_value
from xquant_assistant.execution.ledger import PaperLedger
from xquant_assistant.research.parity import compare_results, validate_frozen_sequence
from xquant_assistant.research.session_backtest import PriceVolumeResearchBacktester
from xquant_assistant.runner import DailyRunner
from xquant_assistant.runtime.calendar import load_calendar
from xquant_assistant.runtime.models import RuntimeState, SessionInput
from xquant_assistant.settings import load_config
from xquant_assistant.storage import atomic_write_json, hash_source_tree
from tests.mvp.helpers import setup_offline_project
from tests.runtime.helpers import fixture, inputs


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = []
    def addSuccess(self, test):
        super().addSuccess(test)
        self.rows.append({'test': test.id(), 'status': 'PASS'})
    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.rows.append({'test': test.id(), 'status': 'FAIL', 'detail': self._exc_info_to_string(err, test)})
    def addError(self, test, err):
        super().addError(test, err)
        self.rows.append({'test': test.id(), 'status': 'ERROR', 'detail': self._exc_info_to_string(err, test)})


def durable_adapter_comparison(output):
    config, calendar, securities, frames = fixture()
    root = output/'engineering_daily'
    setup_offline_project(root, PROJECT)
    calendar.save(root/'data/calendar/sessions.json')
    pd.DataFrame([asdict(x) for x in securities]).to_parquet(root/'data/security_master/master-2026-01-01.parquet', index=False)
    (root/'data/security_master/master-2026-09-28.parquet').unlink()
    for symbol, frame in frames.items():
        frame.to_parquet(root/f'data/market/{symbol}.parquet')
    (root/'data/opening_status').mkdir(parents=True)
    days = [calendar.shift('2026-09-01', n) for n in range(22)]
    daily, states, input_manifest = [], {}, []
    for day in days:
        states[day] = inputs(day, securities, frames).open_states
        atomic_write_json(root/f'data/opening_status/{day}.json', {'observations': [asdict(x) for x in states[day].values()]})
        runner = DailyRunner(root, config, clock=lambda day=day: datetime.fromisoformat(day+'T16:10:00+08:00'))
        manifest, path = runner.run(day, symbols=tuple(x.symbol for x in securities), allow_network=False)
        daily.append(json.loads((path/'runtime_result.json').read_text(encoding='utf-8')))
        input_manifest.append({'session': day, 'input_hash': manifest.input_hash, **json.loads((path/'input_manifest.json').read_text(encoding='utf-8'))})
    research = PriceVolumeResearchBacktester(config)
    research.run(securities, frames, days[0], days[-1], calendar=calendar, opening_states=states)
    report = compare_results(daily, research.last_results)
    report.update({'sample_kind': 'ENGINEERING_FIXTURE_NOT_REAL_QUOTES', 'signal_dates': [x['state']['last_session'] for x in daily if x['schedule']['due']],
        'fills': sum(len(x['fills']) for x in daily), 'forward_validation': False})
    assert report['status'] == 'PASS' and report['sessions'] == 22 and report['fills'] > 0
    assert report['signal_dates'] == [days[n] for n in (0, 10, 20)]
    atomic_write_json(output/'engineering_parity.json', report)
    atomic_write_json(output/'engineering_daily_results.json', daily)
    atomic_write_json(output/'engineering_research_results.json', research.last_results)
    atomic_write_json(output/'input_manifest.json', input_manifest)
    return report


def real_cache_comparison(output, source):
    from xquant_assistant.data.fundamentals import TdxQuantFundamentalProvider
    from xquant_assistant.data.security_master import SinaSecurityMasterProvider
    config = load_config(source/'configs')
    calendar = load_calendar(source)
    end = '2026-09-28'
    symbols = ('sh510300','sh510500','sh600000','sh600519','sz000001','sz000002')
    securities, warnings = SinaSecurityMasterProvider(source/config.data.security_master_dir).fetch(end, symbols=symbols, allow_network=False)
    frames = {symbol: pd.read_parquet(source/config.data.cache_dir/f'{symbol}.parquet').loc[:end] for symbol in symbols}
    fundamentals = TdxQuantFundamentalProvider(source/config.data.fundamental_cache_dir, config.data.tdxquant_endpoint,
        tqcenter_path=config.data.tdxquant_tqcenter_path).load(tuple(x.symbol for x in securities if x.asset_type=='CN_A_MAIN'), end, allow_network=False)
    days = [calendar.shift(end, n-21) for n in range(22)]
    coverage = {'universe': 'FIXED_2026_09_28_SNAPSHOT_NOT_HISTORICAL_MEMBERSHIP', 'opening_status': 'MISSING_NO_FABRICATION',
        'financial_revisions': 'UNVERIFIED', 'industry_history': 'UNAVAILABLE_BEFORE_FIRST_COLLECTION'}
    entries = [SessionInput(day,securities,frames,fundamental_records=fundamentals.records,coverage=coverage) for day in days]
    root = output/'real_cache_replay'
    report, daily, research = validate_frozen_sequence(root, config, calendar, entries, RuntimeState(AccountState('CNY',500000,{})))
    adapter = PriceVolumeResearchBacktester(config)
    adapter.run(securities,frames,days[0],days[-1],calendar=calendar,fundamental_records=fundamentals.records)
    adapter_report = compare_results(daily,adapter.last_results)
    assert adapter_report['status']=='PASS'
    report.update({'sample_kind': 'REAL_SINA_CACHE_EXECUTION_BLOCKED_WITHOUT_HISTORICAL_OPENING_STATUS', 'coverage':coverage,
        'start':days[0], 'end':days[-1], 'fills':sum(len(x['fills']) for x in daily), 'warnings':warnings,
        'profitability_validation':False, 'research_adapter_comparison':adapter_report})
    assert report['status']=='PASS' and report['sessions']==22
    atomic_write_json(output/'real_cache_parity.json',report)
    atomic_write_json(output/'real_cache_daily_results.json',daily)
    atomic_write_json(output/'real_cache_research_results.json',research)
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--real-project',type=Path)
    args=parser.parse_args()
    output=args.output.resolve()
    if output == PROJECT.resolve() or output.is_relative_to((PROJECT/'data/state').resolve()):
        raise ValueError('acceptance output must not be an operational account directory')
    output.mkdir(parents=True,exist_ok=False)
    stream=io.StringIO()
    suite=unittest.defaultTestLoader.discover(str(PROJECT/'tests'),top_level_dir=str(PROJECT))
    result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=RecordedResult).run(suite)
    (output/'test_log.txt').write_text(stream.getvalue(),encoding='utf-8')
    summary={'status':'PASS' if result.wasSuccessful() else 'FAIL', 'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
        'modules':dict(Counter(x['test'].split('.')[2] for x in result.rows)), 'results':result.rows,
        'source_hash':hash_source_tree(PROJECT/'src'), 'observed_at':datetime.now(timezone.utc).isoformat()}
    atomic_write_json(output/'tests.json',summary)
    if not result.wasSuccessful():
        print(stream.getvalue())
        return 1
    engineering=durable_adapter_comparison(output)
    real=real_cache_comparison(output,args.real_project.resolve()) if args.real_project else None
    atomic_write_json(output/'acceptance_summary.json',{'status':'ENGINEERING_READY','tests':summary['tests'],'engineering_parity':engineering,
        'real_cache_parity':real,'forward_validation':'NOT_YET_VALIDATED','real_forward_sessions':0})
    print(json.dumps({'status':'ENGINEERING_READY','tests':summary['tests'],'output':str(output),'engineering_fills':engineering['fills'],
        'real_cache_parity':None if real is None else real['status'],'forward_validation':'NOT_YET_VALIDATED'},ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
