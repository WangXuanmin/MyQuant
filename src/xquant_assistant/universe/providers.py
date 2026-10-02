"""Configurable domestic ETF and A-share main-board universe."""

from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping

import pandas as pd

from ..domain import ExcludedSecurity, Security, UniverseSnapshot
from ..settings import UniverseConfig


def build_universe(
    securities: Iterable[Security],
    as_of_date: str,
    config: UniverseConfig,
    *,
    source: str = "sina",
    source_warnings: tuple[str, ...] = (),
) -> UniverseSnapshot:
    """Apply master-data rules and preserve every exclusion reason."""

    included: list[Security] = []
    excluded: list[ExcludedSecurity] = []
    include_set = {symbol.lower() for symbol in config.custom_include}
    exclude_set = {symbol.lower() for symbol in config.custom_exclude}
    as_of = date.fromisoformat(as_of_date)
    missing_list_dates = False
    for security in securities:
        reasons: list[str] = []
        if security.asset_type not in config.asset_types:
            reasons.append("asset_type_not_enabled")
        if security.exchange not in config.exchanges:
            reasons.append("exchange_not_enabled")
        if security.board in config.exclude_boards:
            reasons.append("board_excluded")
        if security.status in config.exclude_status:
            reasons.append(f"status_excluded:{security.status}")
        if not security.tradable:
            reasons.append("not_tradable")
        if security.symbol.lower() in exclude_set:
            reasons.append("custom_exclude")
        if include_set and security.symbol.lower() not in include_set:
            reasons.append("not_in_custom_include")
        if security.list_date:
            listed = date.fromisoformat(str(security.list_date)[:10])
            if (as_of - listed).days < config.min_listing_days:
                reasons.append("listing_age_below_minimum")
        else:
            missing_list_dates = True
        if reasons:
            excluded.append(ExcludedSecurity(security, tuple(reasons)))
        else:
            included.append(security)
    warnings = list(source_warnings)
    if missing_list_dates:
        warnings.append(
            "listing_date_unavailable_from_sina; minimum age is enforced by market-history sessions"
        )
    return UniverseSnapshot(
        as_of_date=as_of_date,
        included=tuple(sorted(included, key=lambda item: item.symbol)),
        excluded=tuple(excluded),
        source=source,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def refine_universe(
    snapshot: UniverseSnapshot,
    market_data: Mapping[str, pd.DataFrame],
    config: UniverseConfig,
) -> UniverseSnapshot:
    """Enforce history, price, and liquidity rules using raw Sina bars."""

    included: list[Security] = []
    excluded = list(snapshot.excluded)
    for security in snapshot.included:
        frame = market_data.get(security.symbol)
        reasons: list[str] = []
        if frame is None or frame.empty:
            reasons.append("market_data_unavailable")
        else:
            if len(frame) < config.min_history_sessions:
                reasons.append("history_sessions_below_minimum")
            if float(frame["close"].iloc[-1]) < config.min_price:
                reasons.append("price_below_minimum")
            median_turnover = float(frame["amount"].tail(20).median())
            if median_turnover < config.min_median_turnover_20d:
                reasons.append("turnover_below_minimum")
        if reasons:
            excluded.append(ExcludedSecurity(security, tuple(reasons)))
        else:
            included.append(security)
    return UniverseSnapshot(
        as_of_date=snapshot.as_of_date,
        included=tuple(included),
        excluded=tuple(excluded),
        source=snapshot.source,
        warnings=snapshot.warnings,
    )

