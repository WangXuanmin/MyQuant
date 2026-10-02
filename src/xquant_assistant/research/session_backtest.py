"""Research adapter using exactly the paper account's session engine."""
from decimal import Decimal
import pandas as pd
from oxq.core.types import BarSnapshot, Fill, Order, Portfolio, Position as OxqPosition, PositionSnapshot
from oxq.portfolio.analytics import RunResult
from ..domain import AccountState, Position
from ..runtime.engine import SessionEngine
from ..runtime.models import RuntimeState, SessionInput


class PriceVolumeResearchBacktester:
    def __init__(self, config):
        self.config = config
        self.last_results = []

    def run(self, securities, market_data, start_date, end_date, *, fundamental_records=None,
            industry_classifications=None, calendar=None, opening_states=None, initial_state=None):
        if calendar is None:
            raise ValueError('an explicit exchange calendar is required')
        calendar.check_range(start_date)
        calendar.check_range(end_date)
        dates = [day for day in calendar.sessions if start_date <= day <= end_date]
        if len(dates) < 2:
            raise ValueError('backtest requires at least two exchange sessions')
        positions = {symbol: Position(symbol=symbol, **payload) for symbol,payload in self.config.account.initial_positions.items()}
        state = initial_state or RuntimeState(AccountState(self.config.account.currency, self.config.account.initial_cash, positions))
        engine = SessionEngine(self.config, calendar)
        curve, snapshots, fills, self.last_results = [], [], [], []
        weights = {}
        for day in dates:
            inputs = SessionInput(day, securities, dict(market_data), (opening_states or {}).get(day, {}),
                fundamental_records, dict(industry_classifications or {}))
            result = engine.step(state, inputs)
            result.state.revision += 1
            state = result.state
            self.last_results.append(result)
            weights = dict(state.target_weights)
            for fill in result.fills:
                fills.append(Fill(order=Order(symbol=fill.symbol, side=fill.side, shares=fill.shares),
                    filled_price=Decimal(str(fill.filled_price)), filled_at=fill.filled_at, fee=Decimal(str(fill.fee))))
            if not result.valuation['valid']:
                continue
            value = result.valuation['total_value']
            date = pd.Timestamp(day)
            curve.append((date, value))
            snapshots.append(BarSnapshot(date=date, target_weights=dict(weights), adjusted_weights=dict(weights),
                positions={symbol:PositionSnapshot(x.shares, x.average_cost) for symbol,x in state.account.positions.items()},
                cash=state.account.cash, total_value=value))
        if len(curve) < 2:
            raise ValueError('insufficient valid valuations; execution events remain available in last_results')
        portfolio = Portfolio(cash=Decimal(str(state.account.cash)), positions={symbol:OxqPosition(symbol, x.shares,
            Decimal(str(x.average_cost))) for symbol,x in state.account.positions.items()})
        return RunResult(portfolio=portfolio, trades=fills, equity_curve=curve, mktdata=dict(market_data), snapshots=snapshots)
