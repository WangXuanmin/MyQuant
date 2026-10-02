"""Persistable engine state and dated opening observations."""
from dataclasses import dataclass, field
from typing import Any
from ..domain import AccountState, Position, OrderDraft, Security


def account_from_dict(data):
    positions = {symbol: Position(**{**item, 'lots': tuple(item.get('lots', ()))}) for symbol, item in data['positions'].items()}
    return AccountState(data['currency'], float(data['cash']), positions, data.get('last_updated'), float(data.get('realized_pnl', 0)))


@dataclass(frozen=True)
class OpenState:
    symbol: str
    session: str
    is_suspended: bool | None
    limit_up: float | None
    limit_down: float | None
    source: str
    known_at: str
    buy_allowed: bool | None = True
    sell_allowed: bool = True
    settlement_sessions: int = 1
    settlement_verified: bool = False


@dataclass
class RuntimeState:
    account: AccountState
    pending: tuple[OrderDraft, ...] = ()
    metadata: dict[str, Security] = field(default_factory=dict)
    anchor: str | None = None
    peak: float | None = None
    last_prices: dict[str, dict] = field(default_factory=dict)
    previous_eligible: tuple[str, ...] = ()
    previous_candidates: tuple[dict, ...] = ()
    previous_classifications: dict[str, dict] = field(default_factory=dict)
    revision: int = 0
    warning_active: bool = False
    last_session: str | None = None
    needs_reconciliation: bool = False
    target_weights: dict[str, float] = field(default_factory=dict)
    active_config_hash: str | None = None

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        data['account'] = account_from_dict(data['account'])
        data['pending'] = tuple(OrderDraft(**{**x, 'block_reasons': tuple(x.get('block_reasons', ()))}) for x in data.get('pending', ()))
        data['metadata'] = {symbol: Security(**item) for symbol, item in data.get('metadata', {}).items()}
        data['previous_eligible'] = tuple(data.get('previous_eligible', ()))
        data['previous_candidates'] = tuple(data.get('previous_candidates', ()))
        return cls(**data)


@dataclass
class SessionInput:
    session: str
    securities: tuple[Security, ...]
    market_data: dict
    open_states: dict[str, OpenState] = field(default_factory=dict)
    fundamental_records: dict | None = None
    industry_classifications: dict = field(default_factory=dict)
    buy_symbols: tuple[str, ...] | None = None
    coverage: dict = field(default_factory=lambda: {'universe': 'current_snapshot', 'opening_status': 'unknown', 'financial_revisions': 'unverified'})
    quality_issues: tuple = ()


@dataclass
class SessionResult:
    state: RuntimeState
    candidates: tuple
    targets: tuple
    orders: tuple
    fills: tuple
    events: tuple[dict[str, Any], ...]
    valuation: dict
    quality: Any
    risk: Any
    fundamentals: Any
    industry: Any
    schedule: dict
