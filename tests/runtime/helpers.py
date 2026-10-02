from dataclasses import replace
from pathlib import Path
import pandas as pd
from xquant_assistant.settings import load_config
from xquant_assistant.domain import Security, AccountState, OrderDraft, Position
from xquant_assistant.runtime.calendar import TradingCalendar
from xquant_assistant.runtime.models import RuntimeState, SessionInput, OpenState
from tests.mvp.helpers import market_frame

PROJECT = Path(__file__).resolve().parents[2]


def fixture():
    config = load_config(PROJECT/'configs')
    days = tuple(x.date().isoformat() for x in pd.bdate_range('2026-01-01','2026-12-31'))
    calendar = TradingCalendar(days, 'ENGINEERING_FIXTURE_NOT_REAL_MARKET', days[0], days[-1])
    securities = (Security('CN_ETF:sh510300','sh510300','工程ETF','CN_ETF','SSE','ETF', tick_size=.001),
                  Security('CN_A:sh600000','sh600000','工程股票','CN_A_MAIN','SSE','MAIN'))
    frames = {'sh510300':market_frame(end='2026-10-30',periods=200,slope=.01),
              'sh600000':market_frame(end='2026-10-30',periods=200,slope=.02)}
    return config,calendar,securities,frames


def opening(security, day, *, suspended=False, upper=1000., lower=.001, known_at=None):
    return OpenState(security.symbol,day,suspended,upper,lower,'ENGINEERING_OPENING_FIXTURE',
        known_at or day+'T09:29:59+08:00')


def inputs(day,securities,frames,**kwargs):
    return SessionInput(day,securities,frames, {s.symbol:opening(s,day) for s in securities}, **kwargs)


def pending_state(day, securities, calendar, *, side='BUY', symbol='sh510300', shares=100):
    signal = calendar.shift(day,-1)
    asset = next(x.asset_type for x in securities if x.symbol == symbol)
    order = OrderDraft('engineering-order',signal,symbol,asset,side,shares,10,shares*10,None,'READY',
        valid_from=day,expires_at=calendar.shift(signal,3))
    state = RuntimeState(AccountState('CNY',500000,{}), pending=(order,), metadata={x.symbol:x for x in securities},
        anchor=signal,last_session=signal,previous_eligible=tuple(x.symbol for x in securities),
        previous_candidates=tuple({'symbol':x.symbol,'rank':1} for x in securities))
    return state
