"""Cash-aware target weights and non-executable order drafts."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import math
from typing import Mapping

import pandas as pd

from ..domain import AccountState, CandidateScore, OrderDraft, TargetWeight
from ..fees import calculate_fee
from ..settings import AccountConfig, RiskConfig


SLEEVE_TARGETS = {"CN_ETF": 0.50, "CN_A_MAIN": 0.50}


def _portfolio_value(
    account: AccountState, reference_prices: Mapping[str, float]
) -> float:
    return float(
        account.cash
        + sum(
            position.shares * reference_prices.get(symbol, position.average_cost)
            for symbol, position in account.positions.items()
        )
    )


def build_target_weights(
    candidates: tuple[CandidateScore, ...],
    account: AccountState,
    market_data: Mapping[str, pd.DataFrame],
    risk: RiskConfig,
    *,
    securities: Mapping | None = None,
    total_equity: float | None = None,
) -> tuple[TargetWeight, ...]:
    """Allocate 95% investable capital equally inside 50/50 sleeves and caps."""

    reference_prices = {
        symbol: float(frame["close"].iloc[-1]) for symbol, frame in market_data.items()
    }
    total_value = _portfolio_value(account, reference_prices) if total_equity is None else total_equity
    if total_value <= 0:
        raise ValueError("account total value must be positive")
    selected_by_type: dict[str, list[CandidateScore]] = {
        asset_type: sorted(
            [item for item in candidates if item.asset_type == asset_type and item.selected],
            key=lambda item: item.rank,
        )
        for asset_type in SLEEVE_TARGETS
    }
    target_map: dict[str, tuple[str, float]] = {}
    investable = 1.0 - risk.min_cash_weight
    for asset_type, sleeve in SLEEVE_TARGETS.items():
        selected = selected_by_type[asset_type]
        if not selected:
            continue
        base_weight = investable * sleeve / len(selected)
        cap = (
            risk.max_single_etf_weight
            if asset_type == "CN_ETF"
            else risk.max_single_stock_weight
        )
        for candidate in selected:
            target_map[candidate.symbol] = (asset_type, min(base_weight, cap))

    all_symbols = set(target_map) | set(account.positions)
    targets: list[TargetWeight] = []
    for symbol in sorted(all_symbols):
        price = reference_prices.get(symbol)
        if price is None or price <= 0:
            continue
        current_value = account.positions.get(symbol).shares * price if symbol in account.positions else 0.0
        asset_type, target_weight = target_map.get(symbol, ("UNKNOWN", 0.0))
        if asset_type == "UNKNOWN":
            security = (securities or {}).get(symbol)
            asset_type = security.asset_type if security else account.positions[symbol].asset_type
        targets.append(
            TargetWeight(
                symbol=symbol,
                asset_type=asset_type,
                current_weight=float(current_value / total_value),
                target_weight=float(target_weight),
                reference_price=price,
                target_value=float(target_weight * total_value),
            )
        )
    return tuple(targets)


def build_orders(
    targets: tuple[TargetWeight, ...],
    account: AccountState,
    signal_date: str,
    *,
    lot_size: int,
    fee_config: AccountConfig,
) -> tuple[OrderDraft, ...]:
    """Translate target values into lot-rounded local order drafts."""

    orders: list[OrderDraft] = []
    for target in targets:
        current_shares = account.positions.get(target.symbol).shares if target.symbol in account.positions else 0
        target_shares = math.floor(target.target_value / target.reference_price / lot_size) * lot_size
        delta = target_shares - current_shares
        if delta == 0:
            continue
        side = "BUY" if delta > 0 else "SELL"
        shares = abs(delta)
        if side == "SELL":
            shares = min(shares, current_shares)
        if shares <= 0:
            continue
        estimated_notional = float(shares * target.reference_price)
        estimated_fee = (
            None
            if fee_config.fee_model == "unconfigured"
            else calculate_fee(
                estimated_notional,
                target.asset_type,
                side,
                fee_config,
            ).total
        )
        digest = hashlib.sha256(
            f"{signal_date}|{target.symbol}|{side}|{shares}".encode("utf-8")
        ).hexdigest()[:16]
        orders.append(
            OrderDraft(
                order_id=f"dry-{digest}",
                signal_date=signal_date,
                symbol=target.symbol,
                asset_type=target.asset_type,
                side=side,
                shares=shares,
                reference_price=target.reference_price,
                estimated_notional=estimated_notional,
                estimated_fee=estimated_fee,
                status="DRAFT",
            )
        )
    return tuple(orders)
