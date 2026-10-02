from pathlib import Path
from tempfile import TemporaryDirectory
from copy import deepcopy
import json
import unittest
from xquant_assistant.domain import AccountState
from xquant_assistant.runtime.models import RuntimeState
from xquant_assistant.runtime.engine import SessionEngine
from xquant_assistant.runtime.snapshots import freeze_input,load_input
from xquant_assistant.research.parity import validate_frozen_sequence,compare_results
from xquant_assistant.research.session_backtest import PriceVolumeResearchBacktester
from .helpers import fixture,inputs


class ParityAcceptance(unittest.TestCase):
    def setUp(self):
        self.config,self.calendar,self.securities,self.frames=fixture()
        self.start='2026-09-01'

    def test_22_sessions_three_signals_persisted_vs_backtest(self):
        entries=[inputs(self.calendar.shift(self.start,n),self.securities,self.frames) for n in range(22)]
        initial=RuntimeState(AccountState('CNY',500000,{}))
        with TemporaryDirectory() as directory:
            report,daily,research=validate_frozen_sequence(directory,self.config,self.calendar,entries,initial,
                output_path=Path(directory)/'parity.json')
            self.assertEqual(report['status'],'PASS')
            self.assertEqual(report['sessions'],22)
            due=[x['state']['last_session'] for x in daily if x['schedule']['due']]
            self.assertEqual(due,[self.calendar.shift(self.start,n) for n in (0,10,20)])
            self.assertGreater(sum(len(x['fills']) for x in daily),0)
            adapter=PriceVolumeResearchBacktester(self.config)
            adapter.run(self.securities,self.frames,self.start,entries[-1].session,calendar=self.calendar,
                opening_states={x.session:x.open_states for x in entries})
            self.assertEqual(compare_results(daily,adapter.last_results)['status'],'PASS')

    def test_A19_freeze_roundtrip_and_tamper_detection(self):
        state=RuntimeState(AccountState('CNY',500000,{}))
        entry=inputs(self.start,self.securities,self.frames)
        with TemporaryDirectory() as directory:
            digest,path=freeze_input(directory,entry,state,self.config,self.calendar)
            loaded,payload=load_input(path)
            self.assertEqual(payload['session'],self.start)
            self.assertEqual(SessionEngine(self.config,self.calendar).step(state,entry),SessionEngine(self.config,self.calendar).step(state,loaded))
            document=json.loads(path.read_text(encoding='utf-8'))
            document['payload']['market_data']['sh510300']['values'][0][0]=123
            path.write_text(json.dumps(document),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):
                load_input(path)

    def test_first_difference_identifies_date_and_field(self):
        entry=inputs(self.start,self.securities,self.frames)
        first=SessionEngine(self.config,self.calendar).step(RuntimeState(AccountState('CNY',500000,{})),entry)
        second=deepcopy(first)
        second.valuation['cash']-=.01
        report=compare_results([first],[second])
        self.assertEqual(report['status'],'FAIL')
        self.assertEqual(report['first_different_session'],self.start)
        self.assertIn('cash',report['difference']['field'])

    def test_A05_truncated_research_keeps_anchor_and_state(self):
        days=[self.calendar.shift(self.start,n) for n in range(22)]
        states={day:inputs(day,self.securities,self.frames).open_states for day in days}
        full=PriceVolumeResearchBacktester(self.config)
        full.run(self.securities,self.frames,days[0],days[-1],calendar=self.calendar,opening_states=states)
        prefix=PriceVolumeResearchBacktester(self.config)
        prefix.run(self.securities,self.frames,days[0],days[9],calendar=self.calendar,opening_states=states)
        suffix=PriceVolumeResearchBacktester(self.config)
        suffix.run(self.securities,self.frames,days[10],days[-1],calendar=self.calendar,opening_states=states,
            initial_state=deepcopy(prefix.last_results[-1].state))
        self.assertEqual(compare_results(full.last_results[10:],suffix.last_results)['status'],'PASS')
