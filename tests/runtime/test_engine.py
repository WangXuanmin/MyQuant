from dataclasses import replace
from copy import deepcopy
import unittest
import pandas as pd
from xquant_assistant.domain import Position, CandidateScore, Security
from xquant_assistant.runtime.engine import SessionEngine, buffered_candidates, valuation, money
from xquant_assistant.fees import calculate_fee
from xquant_assistant.runtime.models import RuntimeState
from xquant_assistant.domain import AccountState
from .helpers import fixture, inputs, pending_state, opening


class EngineAcceptance(unittest.TestCase):
    def setUp(self):
        self.config,self.calendar,self.securities,self.frames=fixture()
        self.day='2026-09-02'
        self.engine=SessionEngine(self.config,self.calendar)

    def test_A06_buffer_entry_exit_and_failed_fundamental(self):
        state=RuntimeState(AccountState('CNY',500000,{}))
        state.account=replace(state.account,positions={f'sh{rank:06d}':Position(f'sh{rank:06d}',100,10,'2026-01-01') for rank in (12,16)})
        candidates=tuple(CandidateScore(f'sh{rank:06d}','sample','CN_ETF',{}, {},{}, {},1/rank,rank,False,'TEST',
            ('fundamental_filter_failed',) if rank==2 else ()) for rank in range(1,17))
        selected={x.rank for x in buffered_candidates(candidates,state.account,self.config.strategy) if x.selected}
        self.assertIn(12,selected)
        self.assertNotIn(11,selected)
        self.assertNotIn(16,selected)
        self.assertNotIn(2,selected)
        self.assertLessEqual(len(selected),10)

    def test_fixed_quantity_price_fee_cash_hand_calculation(self):
        state=pending_state(self.day,self.securities,self.calendar)
        result=self.engine.step(state,inputs(self.day,self.securities,self.frames))
        self.assertEqual(len(result.fills),1)
        fill=result.fills[0]
        raw=float(self.frames['sh510300'].loc[self.day,'open'])
        expected=round(raw*1.0005,3)
        fee=calculate_fee(money(100*expected),'CN_ETF','BUY',self.config.account).total
        self.assertEqual(fill.shares,100)
        self.assertEqual(fill.filled_price,expected)
        self.assertEqual(result.state.account.cash,money(500000-100*expected-fee))
        self.assertEqual(fill.fee,fee)

    def test_A08_retry_first_second_third_session(self):
        state=pending_state(self.day,self.securities,self.calendar)
        for attempt in range(1,4):
            day=self.calendar.shift(self.day,attempt-1)
            entry=inputs(day,self.securities,self.frames)
            if attempt<3:
                entry.open_states['sh510300']=opening(self.securities[0],day,suspended=True)
            result=self.engine.step(state,entry)
            self.assertEqual(result.events[0]['order']['attempts'],attempt)
            self.assertEqual(result.events[0]['order']['last_attempt_session'],day)
            self.assertEqual(len(result.fills),int(attempt==3))
            state=result.state
        self.assertEqual(state.pending,())

    def test_A09_three_failures_expire_no_fourth_fill(self):
        state=pending_state(self.day,self.securities,self.calendar)
        for attempt in range(3):
            day=self.calendar.shift(self.day,attempt)
            entry=inputs(day,self.securities,self.frames)
            entry.open_states['sh510300']=opening(self.securities[0],day,suspended=True)
            result=self.engine.step(state,entry)
            state=result.state
        self.assertEqual(result.events[0]['order']['status'],'EXPIRED')
        self.assertEqual(state.pending,())
        after=self.engine.step(state,inputs(self.calendar.shift(self.day,3),self.securities,self.frames))
        self.assertEqual(after.fills,())

    def test_A10_latest_eligibility_blocks_pending_buy(self):
        state=pending_state(self.day,self.securities,self.calendar)
        state.previous_eligible=()
        result=self.engine.step(state,inputs(self.day,self.securities,self.frames))
        self.assertEqual(result.events[0]['order']['status'],'CANCELLED')
        self.assertEqual(result.fills,())

    def test_A11_drawdown_warning_continues_fill(self):
        state=pending_state(self.day,self.securities,self.calendar)
        state.peak=1000000
        result=self.engine.step(state,inputs(self.day,self.securities,self.frames))
        self.assertTrue(result.valuation['drawdown_warning'])
        self.assertEqual(len(result.fills),1)
        self.assertIn('WARNING',[x.status for x in result.risk.checks if x.rule=='portfolio_drawdown'])

    def test_A12_old_lots_sellable_new_lots_locked(self):
        state=pending_state(self.day,self.securities,self.calendar,side='SELL')
        old=self.calendar.shift(self.day,-2)
        state.account=replace(state.account,positions={'sh510300':Position('sh510300',200,10,self.day,'CN_ETF',(
            {'shares':100,'acquired_date':old,'sellable_date':self.calendar.shift(old,1)},
            {'shares':100,'acquired_date':self.day,'sellable_date':self.calendar.shift(self.day,1)}))})
        result=self.engine.step(state,inputs(self.day,self.securities,self.frames))
        self.assertEqual(len(result.fills),1)
        self.assertEqual(result.state.account.positions['sh510300'].shares,100)
        self.assertEqual(result.state.account.positions['sh510300'].lots[0]['acquired_date'],self.day)
        state.pending=(replace(state.pending[0],shares=200),)
        refused=self.engine.step(state,inputs(self.day,self.securities,self.frames))
        self.assertEqual(refused.fills,())
        self.assertEqual(refused.events[0]['reason'],'t_plus_one')

    def test_A13_missing_price_does_not_create_zero_equity(self):
        account=AccountState('CNY',100,{'sh510300':Position('sh510300',100,10,'2026-01-01')})
        mark,_=valuation(account,{},self.day,{})
        self.assertIsNone(mark['total_value'])
        self.assertEqual(len(mark['positions']),1)
        stale,_=valuation(account,{},self.day,{'sh510300':{'price':10,'date':'2026-09-01'}})
        self.assertEqual(stale['total_value'],1100)
        self.assertFalse(stale['valid'])

    def test_A14_limits_cash_unknown_and_close_state(self):
        for reason,transform in (
            ('opening_price_limit',lambda e:e.open_states.update({'sh510300':opening(self.securities[0],self.day,upper=1)})),
            ('price_limit_unknown',lambda e:e.open_states.update({'sh510300':opening(self.securities[0],self.day,upper=None)})),
            ('opening_status_not_known_at_open',lambda e:e.open_states.update({'sh510300':opening(self.securities[0],self.day,known_at=self.day+'T15:00:00+08:00')})),
            ('opening_status_unknown',lambda e:e.open_states.clear())):
            state=pending_state(self.day,self.securities,self.calendar)
            entry=inputs(self.day,self.securities,self.frames)
            transform(entry)
            result=self.engine.step(state,entry)
            self.assertEqual(result.events[0]['reason'],reason)
            self.assertEqual(result.fills,())
        state=pending_state(self.day,self.securities,self.calendar)
        state.account=replace(state.account,cash=1)
        result=self.engine.step(state,inputs(self.day,self.securities,self.frames))
        self.assertEqual(result.events[0]['reason'],'insufficient_cash')
        state=pending_state(self.day,self.securities,self.calendar)
        entry=inputs(self.day,self.securities,self.frames)
        raw=float(self.frames['sh510300'].loc[self.day,'open'])
        entry.open_states['sh510300']=opening(self.securities[0],self.day,upper=raw+.0001)
        result=self.engine.step(state,entry)
        self.assertEqual(result.events[0]['reason'],'slippage_outside_price_limits')
        self.assertEqual(result.fills,())

    def test_A07_exit_retains_etf_fee_metadata(self):
        state=pending_state(self.day,self.securities,self.calendar,side='SELL')
        old=self.calendar.shift(self.day,-3)
        state.account=replace(state.account,positions={'sh510300':Position('sh510300',100,10,old,'CN_ETF')})
        entry=inputs(self.day,(self.securities[1],),self.frames,buy_symbols=('sh600000',))
        entry.open_states['sh510300']=opening(self.securities[0],self.day)
        result=self.engine.step(state,entry)
        fill=result.fills[0]
        self.assertEqual(fill.fee,calculate_fee(money(fill.shares*fill.filled_price),'CN_ETF','SELL',self.config.account).total)
        self.assertNotIn('sh510300',result.state.account.positions)

    def test_A16_A17_missing_bar_and_future_prefix_invariance(self):
        state=RuntimeState(AccountState('CNY',500000,{}))
        reduced=deepcopy(self.frames)
        reduced['sh600000']=reduced['sh600000'].drop(pd.Timestamp(self.day))
        result=self.engine.step(state,inputs(self.day,self.securities,reduced))
        self.assertEqual(result.state.last_session,self.day)
        self.assertIn('sh510300',{x.symbol for x in result.candidates})
        truncated={symbol:frame.loc[:self.day] for symbol,frame in self.frames.items()}
        full=self.engine.step(state,inputs(self.day,self.securities,self.frames))
        prefix=self.engine.step(state,inputs(self.day,self.securities,truncated))
        self.assertEqual(full,prefix)

    def test_gap_requires_reconciliation(self):
        state=pending_state(self.day,self.securities,self.calendar)
        with self.assertRaisesRegex(ValueError,'NEEDS_RECONCILIATION'):
            self.engine.step(state,inputs(self.calendar.shift(self.day,1),self.securities,self.frames))

    def test_configuration_activation_cancels_old_intent_keeps_anchor(self):
        state=pending_state(self.day,self.securities,self.calendar)
        state.active_config_hash='old_configuration'
        result=self.engine.step(state,inputs(self.day,self.securities,self.frames))
        self.assertEqual(result.fills,())
        self.assertEqual(result.state.anchor,state.anchor)
        self.assertTrue(any(x['type']=='CONFIG_ACTIVATED' and x['session']==self.day for x in result.events))
        self.assertTrue(any(x.get('reason')=='configuration_changed_old_intent_cancelled' for x in result.events))

    def test_missing_valuation_does_not_report_drawdown_recovery(self):
        state=RuntimeState(AccountState('CNY',499000,{'sh510300':Position('sh510300',100,10,'2026-09-01','CN_ETF')}),
            warning_active=True,peak=600000,metadata={x.symbol:x for x in self.securities})
        result=self.engine.step(state,inputs(self.day,self.securities,{}))
        self.assertIsNone(result.valuation['drawdown'])
        self.assertTrue(result.state.warning_active)
        self.assertFalse(any(x['type']=='DRAWDOWN_RECOVERED' for x in result.events))

    def test_A15_new_signal_cancels_waiting_order(self):
        state=pending_state(self.day,self.securities,self.calendar)
        state.anchor=self.calendar.shift(self.day,-10)
        entry=inputs(self.day,self.securities,self.frames)
        entry.open_states['sh510300']=opening(self.securities[0],self.day,suspended=True)
        result=self.engine.step(state,entry)
        self.assertTrue(any(x['type']=='ORDER' and x['order']['order_id']=='engineering-order' and x['order']['status']=='CANCELLED' for x in result.events))
        self.assertLessEqual(sum(x.symbol=='sh510300' for x in result.state.pending),1)

    def test_A17_future_financial_publication_and_revision(self):
        state=RuntimeState(AccountState('CNY',500000,{}))
        row={'announce_time':'2026-08-01','tag_time':'2026-06-30','FN183':10,'FN191':10,'FN197':10,
            'FN210':30,'FN228':2,'FN232':100,'FN234':100,'FN336':1}
        baseline=inputs(self.day,self.securities,self.frames,fundamental_records={'sh600000':(row,)})
        future=deepcopy(baseline)
        future.fundamental_records['sh600000']+=(dict(row,announce_time='2026-12-01',FN232=-100),
            dict(row,revision_published_at='2026-10-01',FN232=-100))
        left=self.engine.step(state,baseline)
        right=self.engine.step(state,future)
        self.assertEqual(left,right)
        same_day=inputs(self.day,self.securities,self.frames,fundamental_records={'sh600000':(dict(row,announce_time=self.day),)})
        result=self.engine.step(state,same_day)
        self.assertFalse(result.fundamentals.available)
