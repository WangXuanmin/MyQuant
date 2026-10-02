"""Typed configuration loading for the MVP."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml


class ConfigurationError(ValueError):
    """Raised for missing or inconsistent application configuration."""


@dataclass(frozen=True)
class DataConfig:
    market_source: str
    price_adjustment: str
    history_start: str
    stale_after_calendar_days: int
    cache_dir: str
    security_master_dir: str
    security_status_dir: str
    industry_cache_dir: str
    fundamental_cache_dir: str
    tdxquant_endpoint: str
    tdxquant_tqcenter_path: str
    fundamental_provider: str
    industry_provider: str
    allow_network: bool


@dataclass(frozen=True)
class UniverseConfig:
    asset_types: tuple[str, ...]
    exchanges: tuple[str, ...]
    exclude_boards: tuple[str, ...]
    exclude_status: tuple[str, ...]
    min_listing_days: int
    min_history_sessions: int
    min_price: float
    min_median_turnover_20d: float
    custom_include: tuple[str, ...]
    custom_exclude: tuple[str, ...]


@dataclass(frozen=True)
class StrategyConfig:
    strategy_id: str
    version: str
    rebalance_sessions: int
    new_buy_top_n: int
    hold_buffer_top_n: int
    minimum_factor_history: int
    price_volume_weights: dict[str, float]
    component_weights: dict[str, dict[str, float]]
    industry_min_members: int
    industry_min_coverage: float
    missing_optional_policy: str
    no_optional_data_fallback: str


@dataclass(frozen=True)
class RiskConfig:
    max_single_stock_weight: float
    max_single_etf_weight: float
    max_industry_weight: float
    min_cash_weight: float
    max_order_pct_of_20d_turnover: float
    max_portfolio_drawdown: float
    lot_size_cn: int


@dataclass(frozen=True)
class AccountConfig:
    currency: str
    initial_cash: float
    initial_positions: dict[str, dict[str, Any]]
    mode: str
    external_execution_enabled: bool
    fee_model: str
    fee_schedule_as_of: str
    commission_rate: float
    commission_minimum: float
    cn_a_regulatory_fee_rate: float
    cn_a_exchange_handling_fee_rate: float
    cn_a_transfer_fee_rate: float
    cn_a_sell_stamp_duty_rate: float
    etf_exchange_handling_fee_rate: float
    slippage_bps: dict[str, float]


@dataclass(frozen=True)
class RuntimeConfig:
    calendar_timezone: str = "Asia/Shanghai"
    pending_order_valid_sessions: int = 3
    drawdown_action: str = "warn_only"
    paper_execution_after_drawdown_warning: str = "continue"
    fill_quantity_policy: str = "fixed_at_signal"
    fill_policy: str = "all_or_none"
    ledger_backend: str = "sqlite"
    same_session_reprocess: str = "report_only"


@dataclass(frozen=True)
class AppConfig:
    data: DataConfig
    universe: UniverseConfig
    strategy: StrategyConfig
    risk: RiskConfig
    account: AccountConfig
    raw: dict[str, Any] = field(repr=False)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigurationError(f"missing configuration file: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ConfigurationError(f"configuration must be a mapping: {path}")
    return loaded


def load_config(config_dir: str | Path) -> AppConfig:
    """Load and validate the five MVP configuration files."""

    directory = Path(config_dir)
    raw = {
        name: _read_yaml(directory / f"{name}.yaml")
        for name in ("data", "universe", "strategy", "risk", "account")
    }
    data = DataConfig(**raw["data"])
    universe_raw = raw["universe"]
    universe = UniverseConfig(
        **{
            **universe_raw,
            "asset_types": tuple(universe_raw["asset_types"]),
            "exchanges": tuple(universe_raw["exchanges"]),
            "exclude_boards": tuple(universe_raw["exclude_boards"]),
            "exclude_status": tuple(universe_raw["exclude_status"]),
            "custom_include": tuple(universe_raw["custom_include"]),
            "custom_exclude": tuple(universe_raw["custom_exclude"]),
        }
    )
    strategy = StrategyConfig(**raw["strategy"])
    risk = RiskConfig(**raw["risk"])
    account = AccountConfig(**raw["account"])
    runtime_path = directory / "runtime.yaml"
    runtime = RuntimeConfig(**(_read_yaml(runtime_path) if runtime_path.exists() else {}))
    raw["runtime"] = {name: getattr(runtime, name) for name in runtime.__dataclass_fields__}
    if runtime != RuntimeConfig():
        raise ConfigurationError("runtime currently supports only the approved spec03 policies")
    if strategy.rebalance_sessions < 1 or not 1 <= strategy.new_buy_top_n <= strategy.hold_buffer_top_n:
        raise ConfigurationError("invalid rebalance frequency or ranking buffer")
    if data.market_source.lower() != "sina":
        raise ConfigurationError("MVP market_source must remain 'sina'")
    if data.price_adjustment != "raw":
        raise ConfigurationError("Sina MVP price_adjustment must be 'raw'")
    if account.external_execution_enabled:
        raise ConfigurationError("external execution is outside the MVP boundary")
    if account.mode != "dry_run":
        raise ConfigurationError("MVP account mode must be dry_run")
    if account.fee_model not in {"unconfigured", "cn_net_commission_v1"}:
        raise ConfigurationError(f"unsupported fee_model: {account.fee_model}")
    try:
        date.fromisoformat(account.fee_schedule_as_of)
    except ValueError as exc:
        raise ConfigurationError("fee_schedule_as_of must be an ISO date") from exc
    fee_rates = (
        account.commission_rate,
        account.commission_minimum,
        account.cn_a_regulatory_fee_rate,
        account.cn_a_exchange_handling_fee_rate,
        account.cn_a_transfer_fee_rate,
        account.cn_a_sell_stamp_duty_rate,
        account.etf_exchange_handling_fee_rate,
    )
    if any(value < 0 for value in fee_rates):
        raise ConfigurationError("fee rates and minimum commission must be non-negative")
    if not {"CN_ETF", "CN_A_MAIN"}.issubset(account.slippage_bps):
        raise ConfigurationError("slippage_bps must configure CN_ETF and CN_A_MAIN")
    if any(value < 0 for value in account.slippage_bps.values()):
        raise ConfigurationError("slippage_bps values must be non-negative")
    if risk.min_cash_weight < 0 or risk.min_cash_weight >= 1:
        raise ConfigurationError("min_cash_weight must be in [0, 1)")
    if sum(strategy.price_volume_weights.values()) <= 0:
        raise ConfigurationError("price-volume factor weights must sum above zero")
    if strategy.industry_min_members < 2:
        raise ConfigurationError("industry_min_members must be at least two")
    if not 0 < strategy.industry_min_coverage <= 1:
        raise ConfigurationError("industry_min_coverage must be in (0, 1]")
    return AppConfig(data, universe, strategy, risk, account, raw, runtime)


def default_project_root() -> Path:
    """Locate the q10 project root from the installed source tree."""

    return Path(__file__).resolve().parents[2]
