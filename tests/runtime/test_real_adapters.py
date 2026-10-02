from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import asdict
import pandas as pd
import json
import unittest
from datetime import datetime
from xquant_assistant.runner import DailyRunner
from xquant_assistant.execution.ledger import PaperLedger
from xquant_assistant.runtime.calendar import TradingCalendar
from xquant_assistant.research.session_backtest import PriceVolumeResearchBacktester
from xquant_assistant.research.parity import compare_results
from xquant_assistant.runtime.snapshots import load_input
from tests.mvp.helpers import setup_offline_project
from .helpers import PROJECT,fixture,inputs


class AdapterAcceptance(unittest.TestCase):
    def test_22_daily_io_sessions_vs_research(self):
        config,calendar,securities,frames=fixture()
        days=[calendar.shift('2026-09-01',n) for n in range(22)]
        with TemporaryDirectory() as directory:
            root=Path(directory)
            setup_offline_project(root,PROJECT)
            calendar.save(root/'data/calendar/sessions.json')
            pd.DataFrame([asdict(x) for x in securities]).to_parquet(root/'data/security_master/master-2026-01-01.parquet',index=False)
            # The fixture must not introduce a new master version in mid-replay.
            (root/'data/security_master/master-2026-09-28.parquet').unlink()
            for symbol,frame in frames.items():
                frame.to_parquet(root/f'data/market/{symbol}.parquet')
            (root/'data/opening_status').mkdir(parents=True)
            daily_results,opening_states=[],{}
            for day in days:
                runner=DailyRunner(root,config,clock=lambda day=day:datetime.fromisoformat(day+'T16:10:00+08:00'))
                states=inputs(day,securities,frames).open_states
                opening_states[day]=states
                (root/f'data/opening_status/{day}.json').write_text(json.dumps({'observations':[asdict(x) for x in states.values()]}),encoding='utf-8')
                manifest,path=runner.run(day,symbols=tuple(x.symbol for x in securities),allow_network=False)
                daily_results.append(json.loads((path/'runtime_result.json').read_text(encoding='utf-8')))
                self.assertEqual(manifest.account_revision_after,len(daily_results))
            research=PriceVolumeResearchBacktester(config)
            research.run(securities,frames,days[0],days[-1],calendar=calendar,opening_states=opening_states)
            comparison=compare_results(daily_results,research.last_results)
            self.assertEqual(comparison['status'],'PASS',comparison)
            self.assertGreater(sum(len(x['fills']) for x in daily_results),0)
            self.assertEqual(PaperLedger(root/'data/state').load().revision,22)
