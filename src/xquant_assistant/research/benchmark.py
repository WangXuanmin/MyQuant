"""Policy benchmark matching the strategy's ETF/A-share sleeve structure."""

from __future__ import annotations

from typing import Mapping

import pandas as pd

from ..domain import Security


def _sleeve_equity(
    symbols: tuple[str, ...],
    closes: pd.DataFrame,
    rebalance_sessions: int,
) -> pd.Series:
    """Simulate a gross equal-weight sleeve, rebalanced on the strategy schedule."""

    equity = 1.0
    weights: dict[str, float] = {}
    values: list[float] = []
    returns = closes[list(symbols)].pct_change(fill_method=None) if symbols else pd.DataFrame(index=closes.index)
    for session, current_date in enumerate(closes.index):
        if not weights or session % rebalance_sessions == 0:
            eligible = [
                symbol
                for symbol in symbols
                if pd.notna(closes.at[current_date, symbol])
            ]
            weights = (
                {symbol: 1.0 / len(eligible) for symbol in eligible}
                if eligible
                else {}
            )
        daily_return = sum(
            weight
            * (
                0.0
                if pd.isna(returns.at[current_date, symbol])
                else float(returns.at[current_date, symbol])
            )
            for symbol, weight in weights.items()
        )
        equity *= 1.0 + daily_return
        values.append(equity)
        if weights and 1.0 + daily_return > 0:
            weights = {
                symbol: weight
                * (
                    1.0
                    + (
                        0.0
                        if pd.isna(returns.at[current_date, symbol])
                        else float(returns.at[current_date, symbol])
                    )
                )
                / (1.0 + daily_return)
                for symbol, weight in weights.items()
            }
    return pd.Series(values, index=closes.index, dtype="float64")


def build_composite_benchmark(
    securities: tuple[Security, ...],
    market_data: Mapping[str, pd.DataFrame],
    start_date: str,
    end_date: str,
    *,
    rebalance_sessions: int,
) -> pd.DataFrame:
    """Build a 50% domestic-ETF and 50% main-board equal-weight benchmark."""

    dates = pd.DatetimeIndex([])
    for security in securities:
        frame = market_data.get(security.symbol)
        if frame is not None and not frame.empty:
            dates = dates.union(frame.index)
    dates = dates[(dates >= pd.Timestamp(start_date)) & (dates <= pd.Timestamp(end_date))]
    dates = dates.sort_values()
    if dates.empty:
        raise ValueError("composite benchmark has no dates")
    closes = pd.DataFrame(index=dates)
    for security in securities:
        frame = market_data.get(security.symbol)
        if frame is not None and not frame.empty:
            closes[security.symbol] = frame["close"].reindex(dates).ffill()
    etf_symbols = tuple(
        security.symbol
        for security in securities
        if security.asset_type == "CN_ETF" and security.symbol in closes
    )
    stock_symbols = tuple(
        security.symbol
        for security in securities
        if security.asset_type == "CN_A_MAIN" and security.symbol in closes
    )
    etf_equity = _sleeve_equity(etf_symbols, closes, rebalance_sessions)
    stock_equity = _sleeve_equity(stock_symbols, closes, rebalance_sessions)
    composite_equity = 0.5 * etf_equity + 0.5 * stock_equity
    result = pd.DataFrame(
        {
            "etf_sleeve_equity": etf_equity,
            "a_share_sleeve_equity": stock_equity,
            "composite_equity": composite_equity,
        },
        index=dates,
    )
    result["etf_sleeve_return"] = result["etf_sleeve_equity"].pct_change(fill_method=None)
    result["a_share_sleeve_return"] = result["a_share_sleeve_equity"].pct_change(fill_method=None)
    result["composite_return"] = result["composite_equity"].pct_change(fill_method=None)
    result.index.name = "date"
    return result
