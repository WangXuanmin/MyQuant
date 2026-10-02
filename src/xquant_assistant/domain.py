"""Core, serializable data contracts for the trading-assistant MVP."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
import math
from typing import Any

import numpy as np
import pandas as pd


def json_value(value: Any) -> Any:
    """Convert domain objects to strict JSON values."""

    if is_dataclass(value):
        return json_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_value(item) for item in value]
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if value is pd.NaT or value is None:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return value


@dataclass(frozen=True)
class Security:
    """Point-in-time security master record."""

    instrument_id: str
    symbol: str
    name: str
    asset_type: str
    exchange: str
    board: str
    status: str = "NORMAL"
    list_date: str | None = None
    delist_date: str | None = None
    currency: str = "CNY"
    lot_size: int = 100
    tick_size: float = 0.01
    tradable: bool = True
    classification_method: str = "source_field"
    industry_l1: str | None = None
    industry_l2: str | None = None


@dataclass(frozen=True)
class SecurityStatus:
    """Observed tradability state saved as an append-only daily snapshot."""

    as_of_date: str
    symbol: str
    asset_type: str
    name: str
    status: str
    is_st: bool
    is_delisting: bool
    is_suspended: bool | None
    list_date: str | None
    delist_date: str | None
    previous_close: float | None
    last_price: float | None
    volume: float | None
    amount: float | None
    limit_up_price: float | None
    limit_down_price: float | None
    is_limit_up_locked: bool | None
    is_limit_down_locked: bool | None
    tradable: bool
    observed_at: str
    source: str


@dataclass(frozen=True)
class ExcludedSecurity:
    """Security rejected by the universe and its explicit reasons."""

    security: Security
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class UniverseSnapshot:
    """Immutable universe decision for one as-of date."""

    as_of_date: str
    included: tuple[Security, ...]
    excluded: tuple[ExcludedSecurity, ...]
    source: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DataQualityIssue:
    """One data validation result with blocking severity."""

    code: str
    message: str
    severity: str
    symbol: str | None = None


@dataclass(frozen=True)
class DataQualityReport:
    """Quality report for a collection of market-data frames."""

    as_of_date: str
    issues: tuple[DataQualityIssue, ...]

    @property
    def blocked(self) -> bool:
        return any(issue.severity == "ERROR" for issue in self.issues)


@dataclass(frozen=True)
class CandidateScore:
    """Auditable factor score and selection decision for one security."""

    symbol: str
    name: str
    asset_type: str
    raw_factors: dict[str, float | None]
    normalized_factors: dict[str, float | None]
    component_scores: dict[str, float | None]
    effective_component_weights: dict[str, float]
    total_score: float
    rank: int
    selected: bool
    factor_mode: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Position:
    """One holding in the local simulated account."""

    symbol: str
    shares: int
    average_cost: float
    acquired_date: str
    asset_type: str = "UNKNOWN"
    lots: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class AccountState:
    """Persistent local dry-run account state."""

    currency: str
    cash: float
    positions: dict[str, Position]
    last_updated: str | None = None
    realized_pnl: float = 0.0


@dataclass(frozen=True)
class TargetWeight:
    """Current-to-target portfolio allocation for one symbol."""

    symbol: str
    asset_type: str
    current_weight: float
    target_weight: float
    reference_price: float
    target_value: float


@dataclass(frozen=True)
class OrderDraft:
    """Local, non-executable order proposal."""

    order_id: str
    signal_date: str
    symbol: str
    asset_type: str
    side: str
    shares: int
    reference_price: float
    estimated_notional: float
    estimated_fee: float | None
    status: str
    block_reasons: tuple[str, ...] = ()
    valid_from: str | None = None
    expires_at: str | None = None
    last_attempt_session: str | None = None
    attempts: int = 0


@dataclass(frozen=True)
class SimulatedFill:
    """Hypothetical fill produced only by the local simulator."""

    order_id: str
    symbol: str
    side: str
    shares: int
    filled_price: float
    filled_at: str
    fee: float
    execution_mode: str = "LOCAL_SIMULATED"
    fill_is_hypothetical: bool = True


@dataclass(frozen=True)
class RiskCheck:
    """One pre-trade risk rule outcome."""

    rule: str
    status: str
    message: str
    symbol: str | None = None


@dataclass(frozen=True)
class RiskReport:
    """Pre-trade checks for target weights and order drafts."""

    checks: tuple[RiskCheck, ...]

    @property
    def blocked(self) -> bool:
        return any(check.status == "BLOCKED" for check in self.checks)


@dataclass(frozen=True)
class RunManifest:
    """Reproducibility record written for every daily run."""

    run_id: str
    as_of_date: str
    created_at: str
    strategy_id: str
    strategy_version: str
    run_mode: str
    config_hash: str
    source_hash: str
    market_source: str
    price_adjustment: str
    fee_model: str
    fee_schedule_as_of: str
    symbols: tuple[str, ...]
    status: str
    warnings: tuple[str, ...] = ()
    execution_mode: str = "LOCAL_SIMULATED"
    external_execution_enabled: bool = False
    account_revision_before: int | None = None
    account_revision_after: int | None = None
    input_hash: str | None = None
    commit_status: str = "UNCOMMITTED"
