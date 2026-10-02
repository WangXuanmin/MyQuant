"""Pre-trade hard checks and explicit not-evaluated rules."""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping

import pandas as pd

from ..domain import (
    AccountState,
    DataQualityReport,
    OrderDraft,
    RiskCheck,
    RiskReport,
    TargetWeight,
)
from ..settings import AccountConfig, RiskConfig


def evaluate_risk(
    targets: tuple[TargetWeight, ...],
    orders: tuple[OrderDraft, ...],
    account: AccountState,
    market_data: Mapping[str, pd.DataFrame],
    data_quality: DataQualityReport,
    risk: RiskConfig,
    account_config: AccountConfig,
    *,
    industry_available: bool,
    portfolio_drawdown: float | None = None,
    industry_classifications: Mapping[str, Mapping[str, str]] | None = None,
) -> tuple[RiskReport, tuple[OrderDraft, ...]]:
    """Evaluate configured limits and mark every order READY or BLOCKED."""

    checks: list[RiskCheck] = []
    if account_config.external_execution_enabled:
        checks.append(RiskCheck("external_execution", "BLOCKED", "external execution must remain disabled"))
    else:
        checks.append(RiskCheck("external_execution", "PASS", "local dry-run only"))
    if account.cash < 0:
        checks.append(RiskCheck("account_cash", "BLOCKED", "account cash is negative"))
    else:
        checks.append(RiskCheck("account_cash", "PASS", "account cash is non-negative"))
    missing_position_prices = [
        symbol
        for symbol in account.positions
        if symbol not in market_data or market_data[symbol].empty
    ]
    checks.append(
        RiskCheck(
            "account_position_prices",
            "BLOCKED" if missing_position_prices else "PASS",
            (
                "missing prices: " + ",".join(missing_position_prices)
                if missing_position_prices
                else "all positions have current market data"
            ),
        )
    )
    sleeve_weights: dict[str, float] = {}
    for target in targets:
        sleeve_weights[target.asset_type] = (
            sleeve_weights.get(target.asset_type, 0.0) + target.target_weight
        )
    for asset_type in ("CN_ETF", "CN_A_MAIN"):
        value = sleeve_weights.get(asset_type, 0.0)
        checks.append(
            RiskCheck(
                "sleeve_weight",
                "PASS" if value <= 0.5 + 1e-12 else "BLOCKED",
                f"{asset_type} target={value:.6f}, cap=0.500000",
            )
        )
    if portfolio_drawdown is None:
        checks.append(
            RiskCheck(
                "portfolio_drawdown",
                "NOT_EVALUATED",
                "performance history unavailable",
            )
        )
    else:
        checks.append(
            RiskCheck(
                "portfolio_drawdown",
                (
                    "WARNING"
                    if portfolio_drawdown <= -risk.max_portfolio_drawdown
                    else "PASS"
                ),
                f"current={portfolio_drawdown:.6f}, limit={-risk.max_portfolio_drawdown:.6f}",
            )
        )
    if any(issue.severity == "ERROR" and issue.symbol is None for issue in data_quality.issues):
        checks.append(RiskCheck("data_quality", "BLOCKED", "critical market-data validation failed"))
    else:
        checks.append(RiskCheck("data_quality", "PASS", "critical market-data checks passed"))
    checks.extend(RiskCheck("symbol_data_quality", "BLOCKED", issue.message, issue.symbol)
        for issue in data_quality.issues if issue.severity == "ERROR" and issue.symbol is not None)

    target_sum = sum(item.target_weight for item in targets)
    cash_weight = 1.0 - target_sum
    checks.append(
        RiskCheck(
            "minimum_cash",
            "PASS" if cash_weight + 1e-12 >= risk.min_cash_weight else "BLOCKED",
            f"target cash weight={cash_weight:.6f}",
        )
    )
    for target in targets:
        cap = risk.max_single_etf_weight if target.asset_type == "CN_ETF" else risk.max_single_stock_weight
        checks.append(
            RiskCheck(
                "single_instrument_weight",
                "PASS" if target.target_weight <= cap + 1e-12 else "BLOCKED",
                f"target={target.target_weight:.6f}, cap={cap:.6f}",
                target.symbol,
            )
        )
    for order in orders:
        if order.shares % risk.lot_size_cn:
            checks.append(RiskCheck("lot_size", "BLOCKED", f"shares={order.shares}", order.symbol))
        else:
            checks.append(RiskCheck("lot_size", "PASS", f"shares={order.shares}", order.symbol))
        frame = market_data.get(order.symbol)
        if frame is None or frame.empty:
            checks.append(RiskCheck("order_liquidity", "BLOCKED", "market data unavailable", order.symbol))
        else:
            capacity = float(frame["amount"].tail(20).median()) * risk.max_order_pct_of_20d_turnover
            checks.append(
                RiskCheck(
                    "order_liquidity",
                    "PASS" if order.estimated_notional <= capacity else "BLOCKED",
                    f"order={order.estimated_notional:.2f}, capacity={capacity:.2f}",
                    order.symbol,
                )
            )
    classifications = industry_classifications or {}
    a_targets = [item for item in targets if item.asset_type == "CN_A_MAIN"]
    missing_industry = [item.symbol for item in a_targets if item.symbol not in classifications]
    if not industry_available or missing_industry:
        detail = (
            "point-in-time industry data unavailable"
            if not industry_available
            else "missing classifications: " + ",".join(missing_industry)
        )
        checks.append(RiskCheck("industry_concentration", "NOT_EVALUATED", detail))
    else:
        industry_weights: dict[str, float] = {}
        for target in a_targets:
            name = str(classifications[target.symbol].get("industry_l1", "UNKNOWN"))
            industry_weights[name] = industry_weights.get(name, 0.0) + target.target_weight
        if not industry_weights:
            checks.append(RiskCheck("industry_concentration", "PASS", "no A-share target weight"))
        for name, weight in sorted(industry_weights.items()):
            checks.append(
                RiskCheck(
                    "industry_concentration",
                    "PASS" if weight <= risk.max_industry_weight + 1e-12 else "BLOCKED",
                    f"{name} target={weight:.6f}, cap={risk.max_industry_weight:.6f}",
                )
            )
    checks.append(
        RiskCheck(
            "fee_model",
            "PASS" if account_config.fee_model != "unconfigured" else "NOT_EVALUATED",
            account_config.fee_model,
        )
    )
    report = RiskReport(tuple(checks))
    global_blocks = [check.message for check in checks if check.status == "BLOCKED" and check.symbol is None]
    by_symbol: dict[str, list[str]] = {}
    for check in checks:
        if check.status == "BLOCKED" and check.symbol is not None:
            by_symbol.setdefault(check.symbol, []).append(f"{check.rule}:{check.message}")
    resolved: list[OrderDraft] = []
    for order in orders:
        applicable = global_blocks
        if order.side == "SELL":
            applicable = [check.message for check in checks if check.status == "BLOCKED" and check.symbol is None and check.rule in {"external_execution", "account_cash"}]
        reasons = tuple(applicable + by_symbol.get(order.symbol, []))
        resolved.append(
            replace(
                order,
                status="BLOCKED" if reasons else "READY_LOCAL_SIMULATION",
                block_reasons=reasons,
            )
        )
    return report, tuple(resolved)
