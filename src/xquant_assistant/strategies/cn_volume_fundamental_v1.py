"""Price-volume-led strategy with fundamental filters and industry context."""

from __future__ import annotations

from collections import defaultdict
from typing import Mapping

import numpy as np
import pandas as pd

from ..data.fundamentals import FundamentalSnapshot
from ..data.industry import IndustrySnapshot
from ..domain import CandidateScore, Security
from ..factors.price_volume import calculate_price_volume_factors, score_price_volume
from ..settings import StrategyConfig


class VolumeFundamentalStrategy:
    """Rank ETF and A-share sleeves separately with auditable degradation."""

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self.strategy_id = config.strategy_id
        self.version = config.version

    def rank_candidates(
        self,
        securities: tuple[Security, ...],
        market_data: Mapping[str, pd.DataFrame],
        fundamentals: FundamentalSnapshot,
        industry: IndustrySnapshot,
    ) -> tuple[CandidateScore, ...]:
        """Rank on the combined available score and apply fundamental hard filters."""

        groups: dict[str, list[Security]] = defaultdict(list)
        for security in securities:
            groups[security.asset_type].append(security)
        output: list[CandidateScore] = []
        for asset_type, group in groups.items():
            raw = {
                security.symbol: calculate_price_volume_factors(market_data[security.symbol])
                for security in group
            }
            normalized, price_scores = score_price_volume(
                raw, self.config.price_volume_weights
            )
            provisional: list[dict] = []
            for security in group:
                price_score = price_scores[security.symbol]
                configured = self.config.component_weights[asset_type]
                available_components: dict[str, float] = {"price_volume": price_score}
                reasons: list[str] = []
                fundamental_pass = True
                if fundamentals.available and security.symbol in fundamentals.values:
                    values = fundamentals.values[security.symbol]
                    value = values.get("score")
                    if value is not None:
                        available_components["fundamental"] = float(value)
                    fundamental_pass = bool(values.get("passes_filter", 1.0))
                    if not fundamental_pass:
                        reasons.append("fundamental_filter_failed")
                elif asset_type == "CN_A_MAIN":
                    reasons.append("fundamental_not_evaluated")
                if industry.available and security.symbol in industry.scores:
                    available_components["industry_cycle"] = float(
                        industry.scores[security.symbol]
                    )
                elif asset_type == "CN_A_MAIN":
                    reasons.append("industry_not_evaluated")
                if asset_type == "CN_ETF":
                    reasons.append("etf_quality_not_evaluated")
                    reasons.append("industry_not_evaluated")
                usable_weights = {
                    name: weight
                    for name, weight in configured.items()
                    if name in available_components
                    and np.isfinite(available_components[name])
                }
                denominator = sum(usable_weights.values())
                effective = {
                    name: weight / denominator for name, weight in usable_weights.items()
                }
                total_score = sum(
                    available_components[name] * weight
                    for name, weight in effective.items()
                )
                factor_mode = (
                    "FULL"
                    if len(effective) == len(configured)
                    else "DEGRADED_PRICE_VOLUME_ONLY"
                    if set(effective) == {"price_volume"}
                    else "DEGRADED_PARTIAL"
                )
                if factor_mode != "FULL":
                    reasons.append(factor_mode.lower())
                provisional.append(
                    {
                        "security": security,
                        "components": available_components,
                        "effective": effective,
                        "total_score": float(total_score),
                        "factor_mode": factor_mode,
                        "fundamental_pass": fundamental_pass,
                        "reasons": reasons,
                    }
                )
            ranked = sorted(
                provisional,
                key=lambda item: (
                    not item["fundamental_pass"],
                    -item["total_score"] if np.isfinite(item["total_score"]) else float("inf"),
                    item["security"].symbol,
                ),
            )
            for rank, item in enumerate(ranked, start=1):
                security = item["security"]
                selected = rank <= self.config.new_buy_top_n and item["fundamental_pass"]
                reasons = list(item["reasons"])
                reasons.append("selected_top_n" if selected else "outside_top_n_or_filtered")
                output.append(
                    CandidateScore(
                        symbol=security.symbol,
                        name=security.name,
                        asset_type=asset_type,
                        raw_factors=raw[security.symbol],
                        normalized_factors=normalized[security.symbol],
                        component_scores={
                            "price_volume": item["components"].get("price_volume"),
                            "fundamental": item["components"].get("fundamental"),
                            "industry_cycle": item["components"].get("industry_cycle"),
                            "etf_quality": item["components"].get("etf_quality"),
                        },
                        effective_component_weights=item["effective"],
                        total_score=item["total_score"],
                        rank=rank,
                        selected=selected,
                        factor_mode=item["factor_mode"],
                        reasons=tuple(reasons),
                    )
                )
        return tuple(sorted(output, key=lambda item: (item.asset_type, item.rank)))
