"""Free Sina industry classification with price-trend and breadth scoring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

import akshare as ak
import numpy as np
import pandas as pd

from ..domain import Security


@dataclass(frozen=True)
class IndustrySnapshot:
    available: bool
    classifications: dict[str, dict[str, str]]
    scores: dict[str, float]
    metrics: dict[str, dict[str, float]]
    standard: str
    provider: str
    reason: str | None = None
    warnings: tuple[str, ...] = ()


def _period_return(close: pd.Series, sessions: int) -> float | None:
    if len(close) <= sessions:
        return None
    base = float(close.iloc[-sessions - 1])
    return None if base <= 0 else float(close.iloc[-1] / base - 1.0)


def score_industry_cycle(
    classifications: Mapping[str, Mapping[str, str]],
    market_data: Mapping[str, pd.DataFrame],
    *,
    min_members: int,
    min_coverage: float,
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """Score industries from relative 20/60-day trend and member breadth."""

    symbol_metrics: dict[str, dict[str, float]] = {}
    for symbol, classification in classifications.items():
        frame = market_data.get(symbol)
        if frame is None or len(frame) < 61:
            continue
        close = frame["close"].astype(float)
        r20 = _period_return(close, 20)
        r60 = _period_return(close, 60)
        if r20 is None or r60 is None:
            continue
        ma20 = float(close.tail(20).mean())
        ma60 = float(close.tail(60).mean())
        symbol_metrics[symbol] = {
            "return_20": r20,
            "return_60": r60,
            "ma_state": float(close.iloc[-1] / ma60 - 1.0),
            "above_ma20": float(close.iloc[-1] > ma20),
            "above_ma60": float(close.iloc[-1] > ma60),
            "positive_return_20": float(r20 > 0),
        }
    if not symbol_metrics:
        return {}, {}
    market_r20 = float(np.mean([item["return_20"] for item in symbol_metrics.values()]))
    market_r60 = float(np.mean([item["return_60"] for item in symbol_metrics.values()]))
    industries: dict[str, list[str]] = {}
    for symbol, classification in classifications.items():
        industry = classification.get("industry_l1")
        if industry:
            industries.setdefault(industry, []).append(symbol)
    rows: list[dict[str, float | str]] = []
    for industry, members in industries.items():
        valid = [symbol_metrics[symbol] for symbol in members if symbol in symbol_metrics]
        coverage = len(valid) / len(members) if members else 0.0
        if len(valid) < min_members or coverage < min_coverage:
            continue
        rows.append(
            {
                "industry": industry,
                "member_count": float(len(members)),
                "valid_count": float(len(valid)),
                "coverage": coverage,
                "relative_return_20": float(np.mean([x["return_20"] for x in valid]) - market_r20),
                "relative_return_60": float(np.mean([x["return_60"] for x in valid]) - market_r60),
                "ma_state": float(np.mean([x["ma_state"] for x in valid])),
                "breadth_above_ma20": float(np.mean([x["above_ma20"] for x in valid])),
                "breadth_above_ma60": float(np.mean([x["above_ma60"] for x in valid])),
                "breadth_positive_return_20": float(np.mean([x["positive_return_20"] for x in valid])),
            }
        )
    if not rows:
        return {}, {}
    metrics = pd.DataFrame(rows).set_index("industry")
    trend = (
        0.30 * metrics["relative_return_20"].rank(pct=True)
        + 0.50 * metrics["relative_return_60"].rank(pct=True)
        + 0.20 * metrics["ma_state"].rank(pct=True)
    )
    breadth = (
        0.40 * metrics["breadth_above_ma20"]
        + 0.40 * metrics["breadth_above_ma60"]
        + 0.20 * metrics["breadth_positive_return_20"]
    )
    score = 0.60 * trend.rank(pct=True) + 0.40 * breadth.rank(pct=True)
    metrics["trend_score"] = trend
    metrics["breadth_score"] = breadth
    metrics["industry_score"] = score
    industry_metrics = {
        str(industry): {key: float(value) for key, value in row.items()}
        for industry, row in metrics.to_dict(orient="index").items()
    }
    scores = {
        symbol: float(score[classification["industry_l1"]])
        for symbol, classification in classifications.items()
        if classification.get("industry_l1") in score.index
    }
    return scores, industry_metrics


def build_industry_snapshot(
    classifications: Mapping[str, Mapping[str, str]],
    market_data: Mapping[str, pd.DataFrame],
    *,
    min_members: int,
    min_coverage: float,
    provider: str = "sina",
) -> IndustrySnapshot:
    """Score one point-in-time view from an already archived classification."""

    copied = {symbol: dict(values) for symbol, values in classifications.items()}
    scores, metrics = score_industry_cycle(
        copied,
        market_data,
        min_members=min_members,
        min_coverage=min_coverage,
    )
    available = bool(scores)
    return IndustrySnapshot(
        available,
        copied,
        scores,
        metrics,
        "SINA_INDUSTRY",
        provider,
        None if available else "no industry met member/history coverage thresholds",
    )


class SinaIndustryProvider:
    """Archive free Sina industry membership and compute causal market scores."""

    def __init__(self, cache_dir: str | Path) -> None:
        self.cache_dir = Path(cache_dir)

    def load(
        self,
        securities: tuple[Security, ...],
        as_of_date: str,
        market_data: Mapping[str, pd.DataFrame],
        *,
        allow_network: bool,
        min_members: int,
        min_coverage: float,
    ) -> IndustrySnapshot:
        symbols = {item.symbol for item in securities if item.asset_type == "CN_A_MAIN"}
        warnings = [
            "Sina industry membership is archived from collection date and does not reconstruct earlier history"
        ]
        try:
            mapping = self._classification(as_of_date, allow_network=allow_network)
        except Exception as exc:
            return IndustrySnapshot(
                False,
                {},
                {},
                {},
                "SINA_INDUSTRY",
                "sina",
                f"industry classification unavailable: {type(exc).__name__}: {exc}",
                tuple(warnings),
            )
        classifications = {
            symbol: values for symbol, values in mapping.items() if symbol in symbols
        }
        scored = build_industry_snapshot(
            classifications,
            market_data,
            min_members=min_members,
            min_coverage=min_coverage,
        )
        return IndustrySnapshot(
            scored.available,
            scored.classifications,
            scored.scores,
            scored.metrics,
            scored.standard,
            scored.provider,
            scored.reason,
            tuple(warnings),
        )

    def _classification(
        self, as_of_date: str, *, allow_network: bool
    ) -> dict[str, dict[str, str]]:
        exact = self.cache_dir / f"sina-industry-{as_of_date}.parquet"
        if exact.exists():
            frame = pd.read_parquet(exact)
        elif allow_network:
            frame = self._fetch_live(as_of_date)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(exact, index=False)
        else:
            eligible = [
                path
                for path in self.cache_dir.glob("sina-industry-*.parquet")
                if path.stem.removeprefix("sina-industry-") <= as_of_date
            ] if self.cache_dir.exists() else []
            if not eligible:
                raise RuntimeError("offline industry classification cache missing")
            frame = pd.read_parquet(max(eligible))
        return {
            row["symbol"]: {
                "industry_l1": row["industry_l1"],
                "industry_code": row["industry_code"],
                "classification_as_of": str(row.get("as_of_date") or as_of_date),
            }
            for row in frame.to_dict(orient="records")
        }

    @staticmethod
    def _fetch_live(as_of_date: str) -> pd.DataFrame:
        sectors = ak.stock_sector_spot(indicator="新浪行业")
        rows: list[dict[str, str]] = []
        observed_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
        for label, name in sectors[["label", "板块"]].itertuples(index=False, name=None):
            detail = ak.stock_sector_detail(sector=str(label))
            for symbol in detail["symbol"].astype(str).str.lower():
                rows.append(
                    {
                        "as_of_date": as_of_date,
                        "symbol": symbol,
                        "industry_l1": str(name),
                        "industry_code": str(label),
                        "observed_at": observed_at,
                        "source": "sina_industry",
                    }
                )
        if not rows:
            raise RuntimeError("Sina returned no industry members")
        return pd.DataFrame(rows).drop_duplicates(subset=["symbol"], keep="first")


class DisabledIndustryProvider:
    """Explicit disabled provider."""

    def load(self, symbols, as_of_date: str, *args, **kwargs) -> IndustrySnapshot:
        return IndustrySnapshot(
            available=False,
            classifications={},
            scores={},
            metrics={},
            standard="SINA_INDUSTRY",
            provider="disabled",
            reason="no industry provider configured",
        )
