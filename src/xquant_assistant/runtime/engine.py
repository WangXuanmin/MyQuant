"""Causal open/close event processing, without persistence or networking."""
from copy import deepcopy
from dataclasses import asdict, replace
from decimal import Decimal, ROUND_HALF_UP
import math
import pandas as pd

from ..data.fundamentals import DisabledFundamentalProvider, score_fundamentals
from ..data.industry import DisabledIndustryProvider, build_industry_snapshot
from ..data.quality import validate_market_data
from ..domain import Position, SimulatedFill, RiskCheck, RiskReport, TargetWeight
from ..fees import calculate_fee
from ..execution.pricing import apply_slippage
from ..portfolio.constructor import build_target_weights, build_orders
from ..portfolio.risk import evaluate_risk
from ..strategies import VolumeFundamentalStrategy
from ..storage import hash_payload
from ..universe.providers import build_universe, refine_universe
from .models import SessionResult


def money(value):
    return float(Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def buffered_candidates(candidates, account, config):
    chosen = set()
    for asset_type in ('CN_ETF', 'CN_A_MAIN'):
        group = sorted((x for x in candidates if x.asset_type == asset_type and 'fundamental_filter_failed' not in x.reasons), key=lambda x: (x.rank, x.symbol))
        retained = [x for x in group if x.symbol in account.positions and x.rank <= config.hold_buffer_top_n][:config.new_buy_top_n]
        chosen.update(x.symbol for x in retained)
        additions = [x for x in group if x.symbol not in account.positions and x.rank <= config.new_buy_top_n]
        chosen.update(x.symbol for x in additions[:config.new_buy_top_n-len(retained)])
    return tuple(replace(x, selected=x.symbol in chosen,
        reasons=x.reasons + (('holding_buffer_retained' if x.symbol in account.positions else 'new_entry_top_n'),) if x.symbol in chosen else x.reasons + ('not_in_buffered_selection',)) for x in candidates)


def valuation(account, data, day, last_prices, calendar=None):
    rows, missing, stale = [], [], []
    prices = deepcopy(last_prices)
    for symbol, position in sorted(account.positions.items()):
        frame = data.get(symbol)
        visible = None if frame is None else frame.loc[:day]
        if visible is not None and not visible.empty:
            value = float(visible['close'].iloc[-1])
            if math.isfinite(value) and value > 0:
                prices[symbol] = {'price': value, 'date': visible.index[-1].date().isoformat()}
        mark = prices.get(symbol)
        if mark is None:
            missing.append(symbol)
            rows.append({'symbol': symbol, 'shares': position.shares, 'asset_type': position.asset_type,
                'sellable_shares': sum(x['shares'] for x in position.lots if x['sellable_date'] <= day) if position.lots else None,
                'price': None, 'market_value': None, 'price_date': None, 'stale_sessions': None})
            continue
        if mark['date'] != day:
            stale.append(symbol)
        sellable = sum(x['shares'] for x in position.lots if x['sellable_date'] <= day) if position.lots else None
        rows.append({'symbol': symbol, 'shares': position.shares, 'sellable_shares': sellable,
            'price': mark['price'], 'price_date': mark['date'],
            'stale_sessions': calendar.elapsed(mark['date'], day) if calendar else None,
            'market_value': money(position.shares * mark['price']), 'asset_type': position.asset_type})
    position_value = None if missing else money(sum(x['market_value'] for x in rows))
    total = None if position_value is None else money(account.cash + position_value)
    return {'as_of_date': day, 'cash': account.cash, 'positions_value': position_value, 'total_value': total,
        'valid': not missing and not stale, 'missing_prices': missing, 'stale_prices': stale, 'positions': rows,
        'fresh_position_coverage': 1 if not rows else (len(rows)-len(missing)-len(stale))/len(rows),
        'reconciliation_status': 'PASS' if not missing and not stale else 'VALUATION_INCOMPLETE'}, prices


class SessionEngine:
    def __init__(self, config, calendar):
        self.config, self.calendar = config, calendar
        self.strategy = VolumeFundamentalStrategy(config.strategy)

    def _attempt(self, state, order, inputs, data):
        day = inputs.session
        if state.needs_reconciliation:
            raise ValueError('NEEDS_RECONCILIATION: unresolved migration')
        security = state.metadata.get(order.symbol)
        opening = inputs.open_states.get(order.symbol)
        if security is None or security.asset_type != order.asset_type or order.asset_type not in ('CN_ETF', 'CN_A_MAIN'):
            return 'REJECTED', 'security_metadata_unknown', None
        if security.classification_method == 'explicit_symbol_inference' or security.name.lower() in (security.symbol.lower(), security.symbol[2:]):
            return 'PENDING', 'security_metadata_unverified', None
        if order.shares <= 0 or order.shares % security.lot_size or order.side not in ('BUY', 'SELL'):
            return 'REJECTED', 'invalid_order_quantity_or_side', None
        if opening is None or opening.symbol != order.symbol or opening.session != day or opening.is_suspended is None:
            return 'PENDING', 'opening_status_unknown', None
        known = pd.Timestamp(opening.known_at)
        cutoff = pd.Timestamp(day+'T09:30:00', tz='Asia/Shanghai')
        if known.tzinfo is None or known > cutoff or known.tz_convert('Asia/Shanghai').date().isoformat() != day or not opening.source:
            return 'PENDING', 'opening_status_not_known_at_open', None
        if opening.is_suspended:
            return 'PENDING', 'suspended', None
        if order.side == 'BUY' and opening.buy_allowed is None:
            return 'PENDING', 'opening_eligibility_unknown', None
        if order.side == 'BUY' and (not opening.buy_allowed or order.symbol not in state.previous_eligible):
            return 'CANCELLED', 'buy_eligibility_lost', None
        if order.side == 'SELL' and not opening.sell_allowed:
            return 'PENDING', 'sell_not_permitted', None
        if opening.limit_up is None or opening.limit_down is None or not all(math.isfinite(x) and x > 0 for x in (opening.limit_down, opening.limit_up)) or opening.limit_down >= opening.limit_up:
            return 'PENDING', 'price_limit_unknown', None
        frame = data.get(order.symbol)
        if frame is None or pd.Timestamp(day) not in frame.index:
            return 'PENDING', 'opening_price_missing', None
        raw_open = float(frame.loc[day, 'open'])
        if not math.isfinite(raw_open) or raw_open <= 0:
            return 'PENDING', 'opening_price_invalid', None
        if (order.side == 'BUY' and raw_open >= opening.limit_up) or (order.side == 'SELL' and raw_open <= opening.limit_down):
            return 'PENDING', 'opening_price_limit', None
        tick = Decimal(str(security.tick_size))
        price = float((Decimal(str(apply_slippage(raw_open, order.asset_type, order.side, self.config.account))) / tick).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * tick)
        if not opening.limit_down <= price <= opening.limit_up:
            return 'REJECTED', 'slippage_outside_price_limits', None
        previous = frame.loc[frame.index < pd.Timestamp(day)]
        if len(previous) < 20 or not all(math.isfinite(x) and x >= 0 for x in previous['amount'].tail(20)):
            return 'PENDING', 'liquidity_history_missing', None
        notional = money(order.shares * price)
        if notional > float(previous['amount'].tail(20).median()) * self.config.risk.max_order_pct_of_20d_turnover:
            return 'REJECTED', 'order_liquidity', None
        fee = money(calculate_fee(notional, order.asset_type, order.side, self.config.account).total)
        if self.config.account.fee_model == 'unconfigured':
            return 'REJECTED', 'fee_model_unconfigured', None
        account = state.account
        positions = dict(account.positions)
        old = positions.get(order.symbol)
        if order.side == 'BUY':
            ranks = {x['symbol']: x['rank'] for x in state.previous_candidates}
            rank_limit = self.config.strategy.hold_buffer_top_n if old else self.config.strategy.new_buy_top_n
            if ranks.get(order.symbol, rank_limit+1) > rank_limit:
                return 'CANCELLED', 'buy_rank_no_longer_valid', None
            marks = {}
            for symbol, position in positions.items():
                other = data.get(symbol)
                if other is None or pd.Timestamp(day) not in other.index:
                    return 'PENDING', 'portfolio_opening_valuation_incomplete', None
                mark = float(other.loc[day, 'open'])
                if not math.isfinite(mark) or mark <= 0:
                    return 'PENDING', 'portfolio_opening_valuation_incomplete', None
                marks[symbol] = position.shares * mark
            after_cash = money(account.cash - notional - fee)
            if after_cash < 0:
                return 'REJECTED', 'insufficient_cash', None
            equity = account.cash + sum(marks.values()) - fee + order.shares * (raw_open-price)
            if equity <= 0 or after_cash / equity + 1e-12 < self.config.risk.min_cash_weight:
                return 'REJECTED', 'minimum_cash', None
            marks[order.symbol] = marks.get(order.symbol, 0) + order.shares * raw_open
            cap = self.config.risk.max_single_etf_weight if order.asset_type == 'CN_ETF' else self.config.risk.max_single_stock_weight
            if marks[order.symbol]/equity > cap + 1e-12:
                return 'REJECTED', 'single_instrument_weight', None
            sleeve = sum(value for symbol,value in marks.items() if state.metadata[symbol].asset_type == order.asset_type)
            if sleeve/equity > .5 + 1e-12:
                return 'REJECTED', 'sleeve_weight', None
            if order.asset_type == 'CN_A_MAIN':
                name = state.previous_classifications.get(order.symbol, {}).get('industry_l1')
                if name:
                    sector = sum(value for symbol,value in marks.items() if state.metadata[symbol].asset_type == 'CN_A_MAIN' and state.previous_classifications.get(symbol, {}).get('industry_l1') == name)
                    if sector/equity > self.config.risk.max_industry_weight + 1e-12:
                        return 'REJECTED', 'industry_concentration', None
            old_shares = 0 if old is None else old.shares
            lots = list(old.lots) if old and old.lots else ([] if old is None else [{'shares': old.shares, 'acquired_date': old.acquired_date, 'sellable_date': old.acquired_date, 'source': 'legacy_conservative'}])
            days = opening.settlement_sessions if opening.settlement_verified else 1
            if days not in (0, 1):
                return 'REJECTED', 'unsupported_settlement_rule', None
            lots.append({'shares': order.shares, 'acquired_date': day, 'sellable_date': self.calendar.shift(day, days),
                'fee': fee, 'source': opening.source, 'settlement_assumption': 'verified' if opening.settlement_verified else 'conservative_T+1'})
            cost = ((old_shares * old.average_cost if old else 0) + notional + fee)/(old_shares+order.shares)
            positions[order.symbol] = Position(order.symbol, old_shares+order.shares, cost, day, order.asset_type, tuple(lots))
            account = replace(account, cash=after_cash, positions=positions)
        else:
            if old is None or old.shares < order.shares:
                return 'REJECTED', 'insufficient_position', None
            lots = [dict(x) for x in old.lots] or [{'shares': old.shares, 'acquired_date': old.acquired_date,
                'sellable_date': self.calendar.shift(old.acquired_date, 1)}]
            available = sum(x['shares'] for x in lots if x['sellable_date'] <= day)
            if available < order.shares:
                return 'PENDING', 't_plus_one', None
            quantity = order.shares
            for lot in lots:
                if lot['sellable_date'] <= day:
                    removed = min(lot['shares'], quantity)
                    lot['shares'] -= removed
                    quantity -= removed
            left = old.shares-order.shares
            if left:
                positions[order.symbol] = replace(old, shares=left, lots=tuple(x for x in lots if x['shares']))
            else:
                positions.pop(order.symbol)
            account = replace(account, cash=money(account.cash+notional-fee), positions=positions,
                realized_pnl=money(account.realized_pnl+(price-old.average_cost)*order.shares-fee))
        state.account = account
        return 'FILLED', 'local_open_model', SimulatedFill(order.order_id, order.symbol, order.side, order.shares, price, day, fee)

    def step(self, original_state, inputs):
        state = deepcopy(original_state)
        day = inputs.session
        self.calendar.index(day)
        if state.last_session is not None and self.calendar.shift(state.last_session, 1) != day:
            raise ValueError('NEEDS_RECONCILIATION: sessions must be processed consecutively')
        data = {symbol: frame.loc[:day].copy() for symbol,frame in inputs.market_data.items()}
        if state.account.cash < 0:
            raise ValueError('invalid account cash')
        if len({x.order_id for x in state.pending}) != len(state.pending):
            raise ValueError('NEEDS_RECONCILIATION: duplicate pending order IDs')
        events, fills, pending = [], [], []
        current_config_hash = hash_payload(self.config.raw)
        config_event = None
        if state.active_config_hash != current_config_hash:
            config_event = {'type': 'CONFIG_ACTIVATED', 'session': day, 'previous_config_hash': state.active_config_hash,
                'config_hash': current_config_hash, 'strategy_version': self.config.strategy.version, 'anchor': state.anchor}
            if state.active_config_hash is not None:
                for order in state.pending:
                    events.append({'type': 'ORDER', 'session': day, 'order': asdict(replace(order, status='CANCELLED')),
                        'reason': 'configuration_changed_old_intent_cancelled'})
                state.pending = ()
            state.active_config_hash = current_config_hash
        for original_order in sorted(state.pending, key=lambda x: (x.side != 'SELL', x.symbol, x.order_id)):
            order = original_order
            if order.valid_from is None or order.expires_at is None:
                raise ValueError('NEEDS_RECONCILIATION: order lifetime missing')
            if day > order.expires_at:
                events.append({'type': 'ORDER', 'session': day, 'order': asdict(replace(order, status='EXPIRED')), 'reason': 'validity_elapsed'})
                continue
            if day < order.valid_from:
                pending.append(order)
                continue
            if order.last_attempt_session == day:
                pending.append(order)
                continue
            status, reason, fill = self._attempt(state, order, inputs, data)
            if status == 'PENDING' and day == order.expires_at:
                status = 'EXPIRED'
            order = replace(order, status=status, attempts=order.attempts+1, last_attempt_session=day, block_reasons=(reason,))
            events.append({'type': 'ORDER', 'session': day, 'order': asdict(order), 'reason': reason})
            if fill:
                fills.append(fill)
                notional = money(fill.shares * fill.filled_price)
                events.append({'type': 'CASH', 'session': day, 'order_id': fill.order_id,
                    'delta': money(-notional-fill.fee if fill.side == 'BUY' else notional-fill.fee),
                    'cash_after': state.account.cash,
                    'fee_breakdown': asdict(calculate_fee(notional, order.asset_type, fill.side, self.config.account))})
            if status == 'PENDING':
                pending.append(order)
        if config_event:
            events.append(config_event)
        state.metadata.update({x.symbol: x for x in inputs.securities})
        quality = validate_market_data(data, day, min_history_sessions=self.config.universe.min_history_sessions,
            stale_after_calendar_days=self.config.data.stale_after_calendar_days, fetch_issues=inputs.quality_issues)
        allowed = set(inputs.buy_symbols) if inputs.buy_symbols is not None else {x.symbol for x in inputs.securities}
        initial = build_universe(tuple(x for x in inputs.securities if x.symbol in allowed), day, self.config.universe)
        universe = refine_universe(initial, data, self.config.universe)
        errors = {x.symbol for x in quality.issues if x.severity == 'ERROR'}
        eligible = tuple(x for x in universe.included if x.symbol not in errors and x.symbol in data and not data[x.symbol].empty and data[x.symbol].index[-1].date().isoformat() == day)
        classifications = {symbol: row for symbol,row in inputs.industry_classifications.items() if row.get('classification_as_of', '9999-12-31') <= day}
        industry = build_industry_snapshot(classifications, data, min_members=self.config.strategy.industry_min_members, min_coverage=self.config.strategy.industry_min_coverage) if classifications else DisabledIndustryProvider().load(tuple(x.symbol for x in eligible), day)
        # Date-only publication is conservatively usable on the next session.
        records = {symbol: tuple(row for row in history if row.get('announce_time', '9999') < day and row.get('usable_from', day) <= day and row.get('revision_published_at', row.get('announce_time', '9999')) < day)
            for symbol,history in (inputs.fundamental_records or {}).items()}
        fundamentals = score_fundamentals(records, day, classifications) if records else DisabledFundamentalProvider().load(tuple(x.symbol for x in eligible), day)
        candidates = buffered_candidates(self.strategy.rank_candidates(eligible, data, fundamentals, industry), state.account, self.config.strategy)
        mark, prices = valuation(state.account, data, day, state.last_prices, self.calendar)
        state.last_prices = prices
        drawdown = None
        if mark['valid']:
            state.peak = max(state.peak or mark['total_value'], mark['total_value'])
            drawdown = mark['total_value']/state.peak-1
        warning = drawdown is not None and drawdown <= -self.config.risk.max_portfolio_drawdown
        if drawdown is not None and (warning != state.warning_active or warning):
            events.append({'type': 'DRAWDOWN_WARNING' if warning else 'DRAWDOWN_RECOVERED', 'session': day,
                'drawdown': drawdown, 'continue_simulation': True, 'transition': warning != state.warning_active})
        if drawdown is not None:
            state.warning_active = warning
        due = self.calendar.due(state.anchor, day, self.config.strategy.rebalance_sessions)
        targets, orders = (), ()
        if due and candidates and mark['valid']:
            targets = build_target_weights(candidates, state.account, data, self.config.risk, securities=state.metadata, total_equity=mark['total_value'])
        # Daily exits for known loss of instrument eligibility; ranking exits only on due sessions.
        force_exit = {symbol for symbol in state.account.positions if symbol in state.metadata and state.metadata[symbol].status != 'NORMAL'}
        if force_exit:
            target_map = {x.symbol: x for x in targets}
            for symbol in force_exit:
                frame = data.get(symbol)
                if frame is not None and not frame.empty:
                    security = state.metadata[symbol]
                    price = float(frame['close'].iloc[-1])
                    target_map[symbol] = TargetWeight(symbol, security.asset_type, 0, 0, price, 0)
            targets = tuple(target_map.values())
        raw_orders = build_orders(targets, state.account, day, lot_size=self.config.risk.lot_size_cn, fee_config=self.config.account)
        risk_report, checked = evaluate_risk(targets, raw_orders, state.account, data, quality, self.config.risk, self.config.account,
            industry_available=bool(classifications), portfolio_drawdown=drawdown, industry_classifications=classifications)
        if not mark['valid']:
            risk_report = RiskReport(risk_report.checks + (RiskCheck('valuation', 'BLOCKED', 'incomplete or stale portfolio valuation'),))
        orders = tuple(replace(order, status='READY' if order.status == 'READY_LOCAL_SIMULATION' else 'REJECTED',
            valid_from=self.calendar.shift(day, 1), expires_at=self.calendar.shift(day, self.config.runtime.pending_order_valid_sessions)) for order in checked)
        if targets:
            if due:
                state.target_weights = {x.symbol: x.target_weight for x in targets}
            else:
                state.target_weights.update({x.symbol: x.target_weight for x in targets})
        new_symbols = {x.symbol for x in orders}
        next_pending = []
        current_buy_eligible = {x.symbol for x in candidates if 'fundamental_filter_failed' not in x.reasons}
        for order in pending:
            if order.symbol in new_symbols or (order.side == 'BUY' and order.symbol not in current_buy_eligible):
                events.append({'type': 'ORDER', 'session': day, 'order': asdict(replace(order, status='CANCELLED')), 'reason': 'superseded_or_eligibility_lost'})
            else:
                next_pending.append(order)
        for order in orders:
            events.append({'type': 'ORDER', 'session': day, 'order': asdict(order), 'reason': 'scheduled_signal' if due else 'instrument_exit'})
            if order.status == 'READY':
                next_pending.append(order)
        if due and candidates and mark['valid'] and state.anchor is None:
            state.anchor = day
        state.pending = tuple(next_pending)
        state.previous_eligible = tuple(sorted(current_buy_eligible))
        state.previous_candidates = tuple(asdict(x) for x in candidates)
        state.previous_classifications = classifications
        state.account = replace(state.account, last_updated=day)
        state.last_session = day
        next_due = None
        if state.anchor:
            offset = self.config.strategy.rebalance_sessions - self.calendar.elapsed(state.anchor, day) % self.config.strategy.rebalance_sessions
            try:
                next_due = self.calendar.shift(day, offset)
            except ValueError:
                pass
        mark.update({'drawdown': drawdown, 'running_peak': state.peak, 'drawdown_warning': warning,
            'initial_equity': self.config.account.initial_cash, 'total_return': None if not mark['valid'] else mark['total_value']/self.config.account.initial_cash-1,
            'after_cost': self.config.account.fee_model != 'unconfigured', 'execution_mode': 'LOCAL_SIMULATED',
            'fee_model': self.config.account.fee_model, 'realized_pnl': state.account.realized_pnl})
        return SessionResult(state, candidates, targets, orders, tuple(fills), tuple(events), mark, quality, risk_report, fundamentals, industry,
            {'anchor': state.anchor, 'due': due, 'next_due': next_due, 'calendar_hash': self.calendar.digest,
             'pending_remaining_sessions': {x.order_id: self.calendar.elapsed(day, x.expires_at) for x in state.pending}})
