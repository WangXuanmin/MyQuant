"""Sina raw daily-bar adapter with local Parquet cache."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import akshare as ak
import numpy as np
import pandas as pd

from ..domain import DataQualityIssue, Security
from ..storage import atomic_write_json


REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume", "amount")


class SinaMarketDataAdapter:
    """Load unadjusted daily data without silently changing providers."""

    source = "sina"
    price_adjustment = "raw"

    def __init__(self, cache_dir: str | Path) -> None:
        self.cache_dir = Path(cache_dir)

    def fetch(
        self,
        security: Security,
        start_date: str,
        end_date: str,
        *,
        allow_network: bool = True,
    ) -> pd.DataFrame:
        """Fetch, normalize, merge, cache, and date-filter one symbol."""

        cached = self._read_cache(security.symbol)
        if allow_network:
            live = self._fetch_live(security, start_date, end_date)
            combined = self._merge(cached, live)
            self._write_cache(security, combined)
        elif cached is not None:
            combined = cached
        else:
            raise RuntimeError(f"offline cache missing for {security.symbol}")
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        return combined.loc[(combined.index >= start) & (combined.index <= end)].copy()

    def fetch_many(
        self,
        securities: Iterable[Security],
        start_date: str,
        end_date: str,
        *,
        allow_network: bool = True,
    ) -> tuple[dict[str, pd.DataFrame], tuple[DataQualityIssue, ...]]:
        """Fetch each security independently and retain per-symbol failures."""

        data: dict[str, pd.DataFrame] = {}
        issues: list[DataQualityIssue] = []
        for security in securities:
            try:
                data[security.symbol] = self.fetch(
                    security, start_date, end_date, allow_network=allow_network
                )
            except Exception as exc:
                issues.append(
                    DataQualityIssue(
                        code="market_fetch_failed",
                        message=f"{type(exc).__name__}: {exc}",
                        severity="ERROR",
                        symbol=security.symbol,
                    )
                )
        return data, tuple(issues)

    def _fetch_live(
        self, security: Security, start_date: str, end_date: str
    ) -> pd.DataFrame:
        if security.asset_type == "CN_ETF":
            frame = ak.fund_etf_hist_sina(symbol=security.symbol)
        else:
            frame = ak.stock_zh_a_daily(
                symbol=security.symbol,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                adjust="",
            )
        return self._normalize(frame)

    def _normalize(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame is None or frame.empty:
            return pd.DataFrame(columns=REQUIRED_COLUMNS, index=pd.DatetimeIndex([]))
        normalized = frame.copy()
        normalized.columns = [str(column).strip() for column in normalized.columns]
        if "date" in normalized.columns:
            normalized["date"] = pd.to_datetime(normalized["date"], errors="raise")
            normalized = normalized.set_index("date")
        else:
            normalized.index = pd.DatetimeIndex(normalized.index)
        if not normalized.index.is_unique:
            raise ValueError("duplicate market-data dates")
        normalized = normalized.sort_index()
        missing = set(REQUIRED_COLUMNS) - set(normalized.columns)
        if missing:
            raise ValueError(f"missing Sina columns: {sorted(missing)}")
        keep = list(REQUIRED_COLUMNS) + [
            column
            for column in ("outstanding_share", "turnover")
            if column in normalized.columns
        ]
        normalized = normalized[keep].apply(pd.to_numeric, errors="coerce")
        required = normalized[list(REQUIRED_COLUMNS)].to_numpy(dtype="float64")
        if not np.isfinite(required).all():
            raise ValueError("non-finite required market values")
        normalized.index.name = "date"
        return normalized.astype("float64")

    @staticmethod
    def _merge(
        cached: pd.DataFrame | None, live: pd.DataFrame
    ) -> pd.DataFrame:
        if cached is None or cached.empty:
            return live
        return pd.concat([cached, live]).loc[lambda frame: ~frame.index.duplicated(keep="last")].sort_index()

    def _cache_path(self, symbol: str) -> Path:
        return self.cache_dir / f"{symbol.lower()}.parquet"

    def _read_cache(self, symbol: str) -> pd.DataFrame | None:
        path = self._cache_path(symbol)
        if not path.exists():
            return None
        frame = pd.read_parquet(path)
        frame.index = pd.DatetimeIndex(frame.index)
        frame.index.name = "date"
        return frame.sort_index()

    def _write_cache(self, security: Security, frame: pd.DataFrame) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(self._cache_path(security.symbol))
        atomic_write_json(
            self.cache_dir / f"{security.symbol.lower()}.meta.json",
            {
                "symbol": security.symbol,
                "asset_type": security.asset_type,
                "source": self.source,
                "price_adjustment": self.price_adjustment,
                "first_date": None if frame.empty else frame.index[0],
                "last_date": None if frame.empty else frame.index[-1],
                "rows": len(frame),
            },
        )

