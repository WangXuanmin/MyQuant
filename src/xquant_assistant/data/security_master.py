"""Daily domestic ETF and A-share master and tradability snapshots."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd

from ..domain import Security, SecurityStatus


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _first_column(frame: pd.DataFrame, names: tuple[str, ...]) -> str:
    for name in names:
        if name in frame.columns:
            return name
    raise ValueError(f"none of the expected columns exist: {names}")


def _optional_float(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return number if pd.notna(number) else None


def _optional_date(value) -> str | None:
    if value is None or pd.isna(value) or str(value).strip() in {"", "NaT", "nan"}:
        return None
    return pd.Timestamp(value).date().isoformat()


def _status_from_name(name: str) -> str:
    upper = name.upper().replace(" ", "")
    if "退" in name:
        return "DELISTING"
    if "*ST" in upper:
        return "STAR_ST"
    if "ST" in upper:
        return "ST"
    return "NORMAL"


def _symbol_from_code(code: object, exchange: str | None = None) -> str:
    raw = str(code).strip().lower()
    if raw.startswith(("sh", "sz", "bj")):
        return raw
    raw = raw.split(".")[0].zfill(6)
    if exchange == "SSE" or raw.startswith(("5", "6", "9")):
        return f"sh{raw}"
    if exchange == "BSE" or raw.startswith(("4", "8")):
        return f"bj{raw}"
    return f"sz{raw}"


def _limit_price(previous_close: float, rate: float, tick_size: float, up: bool) -> float:
    multiplier = Decimal("1") + Decimal(str(rate)) * (1 if up else -1)
    value = Decimal(str(previous_close)) * multiplier
    tick = Decimal(str(tick_size))
    ticks = (value / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(ticks * tick)


def classify_a_share(symbol: str) -> tuple[str, str]:
    """Return exchange and board, preserving that Sina lacks a board field."""

    normalized = symbol.lower()
    code = normalized[2:] if normalized[:2] in {"sh", "sz", "bj"} else normalized
    if normalized.startswith("sh"):
        if code.startswith(("600", "601", "603", "605")):
            return "SSE", "MAIN"
        if code.startswith(("688", "689")):
            return "SSE", "STAR"
        return "SSE", "OTHER"
    if normalized.startswith("sz"):
        if code.startswith(("000", "001", "002", "003")):
            return "SZSE", "MAIN"
        if code.startswith(("300", "301")):
            return "SZSE", "CHINEXT"
        return "SZSE", "OTHER"
    return "BSE", "BSE"


def infer_security(symbol: str, name: str | None = None) -> Security:
    """Infer a minimal record for an explicit symbol absent from a live list."""

    normalized = symbol.lower()
    exchange = "SSE" if normalized.startswith("sh") else "SZSE"
    code = normalized[2:]
    is_etf = code.startswith(("15", "16", "50", "51", "52", "56", "58"))
    if is_etf:
        return Security(
            instrument_id=f"CN_ETF:{normalized}",
            symbol=normalized,
            name=name or normalized,
            asset_type="CN_ETF",
            exchange=exchange,
            board="ETF",
            tick_size=0.001,
            classification_method="explicit_symbol_inference",
        )
    exchange, board = classify_a_share(normalized)
    return Security(
        instrument_id=f"CN_A:{normalized}",
        symbol=normalized,
        name=name or normalized,
        asset_type="CN_A_MAIN" if board == "MAIN" else "CN_A_OTHER",
        exchange=exchange,
        board=board,
        status=_status_from_name(name or normalized),
        classification_method="sina_code_rules_v1",
    )


def inferred_status(security: Security, as_of_date: str) -> SecurityStatus:
    """Build an explicit unknown-observation record for cached or inferred symbols."""

    status = security.status
    return SecurityStatus(
        as_of_date=as_of_date,
        symbol=security.symbol,
        asset_type=security.asset_type,
        name=security.name,
        status=status,
        is_st=status in {"ST", "STAR_ST"},
        is_delisting=status == "DELISTING",
        is_suspended=None,
        list_date=security.list_date,
        delist_date=security.delist_date,
        previous_close=None,
        last_price=None,
        volume=None,
        amount=None,
        limit_up_price=None,
        limit_down_price=None,
        is_limit_up_locked=None,
        is_limit_down_locked=None,
        tradable=security.tradable,
        observed_at=datetime.now(SHANGHAI_TZ).isoformat(),
        source="cached_or_inferred_without_live_status",
    )


class SinaSecurityMasterProvider:
    """Fetch and cache dated Sina lists enriched by exchange reference data."""

    def __init__(
        self,
        cache_dir: str | Path,
        status_cache_dir: str | Path | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.status_cache_dir = (
            Path(status_cache_dir)
            if status_cache_dir is not None
            else self.cache_dir.parent / "security_status"
        )

    def fetch(
        self,
        as_of_date: str,
        *,
        symbols: Iterable[str] | None = None,
        allow_network: bool = True,
    ) -> tuple[tuple[Security, ...], tuple[str, ...]]:
        securities, _, warnings = self.fetch_snapshot(
            as_of_date, symbols=symbols, allow_network=allow_network
        )
        return securities, warnings

    def fetch_snapshot(
        self,
        as_of_date: str,
        *,
        symbols: Iterable[str] | None = None,
        allow_network: bool = True,
    ) -> tuple[tuple[Security, ...], tuple[SecurityStatus, ...], tuple[str, ...]]:
        """Return master and status rows while persisting the full daily snapshots."""

        requested = None if symbols is None else {item.lower() for item in symbols}
        warnings: list[str] = [
            "A-share board classification uses recorded code rules",
            "daily snapshots accumulate history only from their collection date",
        ]
        securities: list[Security]
        statuses: list[SecurityStatus]
        if allow_network:
            try:
                previous = self._load_latest(as_of_date)
                securities, statuses, live_warnings = self._fetch_live(
                    as_of_date, previous or []
                )
                warnings.extend(live_warnings)
                self._save(as_of_date, securities, statuses)
            except Exception as exc:
                cached = self._load_latest(as_of_date)
                if cached is None:
                    raise RuntimeError(
                        "Sina security master failed and no cache exists"
                    ) from exc
                securities = cached
                statuses = self._load_latest_status(as_of_date) or [
                    inferred_status(item, as_of_date) for item in securities
                ]
                warnings.append(f"security_master_cache_fallback:{type(exc).__name__}")
        else:
            cached = self._load_latest(as_of_date)
            if cached is None:
                if requested is None:
                    raise RuntimeError("offline mode requires a cached security master")
                securities = [infer_security(symbol) for symbol in sorted(requested)]
                statuses = [inferred_status(item, as_of_date) for item in securities]
                warnings.append("security_master_inferred_for_offline_explicit_symbols")
            else:
                securities = cached
                statuses = self._load_latest_status(as_of_date) or [
                    inferred_status(item, as_of_date) for item in securities
                ]

        if requested is not None:
            found = {security.symbol for security in securities}
            securities = [item for item in securities if item.symbol in requested]
            statuses = [item for item in statuses if item.symbol in requested]
            missing = sorted(requested - found)
            inferred = [infer_security(symbol) for symbol in missing]
            securities.extend(inferred)
            statuses.extend(inferred_status(item, as_of_date) for item in inferred)
            if missing:
                warnings.append(
                    "explicit_symbols_missing_from_sina_list:" + ",".join(missing)
                )
        securities.sort(key=lambda item: (item.asset_type, item.symbol))
        statuses.sort(key=lambda item: (item.asset_type, item.symbol))
        return tuple(securities), tuple(statuses), tuple(dict.fromkeys(warnings))

    def _fetch_live(
        self,
        as_of_date: str,
        previous: list[Security],
    ) -> tuple[list[Security], list[SecurityStatus], list[str]]:
        observed = datetime.now(SHANGHAI_TZ)
        observation_matches = observed.date().isoformat() == as_of_date
        warnings: list[str] = []
        if not observation_matches:
            warnings.append(
                "live status observation date differs from requested as-of; "
                "intraday tradability fields left unknown"
            )
        list_dates, delist_dates, reference_warnings = self._exchange_reference()
        warnings.extend(reference_warnings)
        etf_frame = ak.fund_etf_category_sina(symbol="ETF基金")
        stock_frame = ak.stock_zh_a_spot()
        etf_code = _first_column(etf_frame, ("代码", "symbol", "code"))
        etf_name = _first_column(etf_frame, ("名称", "name"))
        stock_code = _first_column(stock_frame, ("代码", "symbol", "code"))
        stock_name = _first_column(stock_frame, ("名称", "name"))
        previous_close_column = _first_column(stock_frame, ("昨收", "settlement"))
        last_column = _first_column(stock_frame, ("最新价", "trade"))
        open_column = _first_column(stock_frame, ("今开", "open"))
        high_column = _first_column(stock_frame, ("最高", "high"))
        low_column = _first_column(stock_frame, ("最低", "low"))
        volume_column = _first_column(stock_frame, ("成交量", "volume"))
        amount_column = _first_column(stock_frame, ("成交额", "amount"))
        securities: list[Security] = []
        statuses: list[SecurityStatus] = []
        for row in etf_frame[[etf_code, etf_name]].itertuples(index=False, name=None):
            symbol, name = str(row[0]).lower(), str(row[1])
            if not symbol.startswith(("sh", "sz")):
                continue
            status = _status_from_name(name)
            security = Security(
                instrument_id=f"CN_ETF:{symbol}",
                symbol=symbol,
                name=name,
                asset_type="CN_ETF",
                exchange="SSE" if symbol.startswith("sh") else "SZSE",
                board="ETF",
                status=status,
                tick_size=0.001,
                tradable=status not in {"ST", "STAR_ST", "DELISTING"},
                classification_method="sina_etf_list",
            )
            securities.append(security)
            statuses.append(inferred_status(security, as_of_date))

        stock_columns = [
            stock_code,
            stock_name,
            previous_close_column,
            last_column,
            open_column,
            high_column,
            low_column,
            volume_column,
            amount_column,
        ]
        for row in stock_frame[stock_columns].itertuples(index=False, name=None):
            symbol, name = str(row[0]).lower(), str(row[1])
            if not symbol.startswith(("sh", "sz", "bj")):
                continue
            exchange, board = classify_a_share(symbol)
            status = _status_from_name(name)
            list_date = list_dates.get(symbol)
            delist_date = delist_dates.get(symbol)
            previous_close = _optional_float(row[2]) if observation_matches else None
            last_price = _optional_float(row[3]) if observation_matches else None
            open_price = _optional_float(row[4]) if observation_matches else None
            high = _optional_float(row[5]) if observation_matches else None
            low = _optional_float(row[6]) if observation_matches else None
            volume = _optional_float(row[7]) if observation_matches else None
            amount = _optional_float(row[8]) if observation_matches else None
            is_suspended = None
            if observation_matches and volume is not None and amount is not None:
                is_suspended = volume <= 0 and amount <= 0 and (open_price or 0) <= 0
            tradable = (
                status not in {"ST", "STAR_ST", "DELISTING"}
                and is_suspended is not True
                and delist_date is None
            )
            security = Security(
                instrument_id=f"CN_A:{symbol}",
                symbol=symbol,
                name=name,
                asset_type="CN_A_MAIN" if board == "MAIN" else "CN_A_OTHER",
                exchange=exchange,
                board=board,
                status="SUSPENDED" if is_suspended else status,
                list_date=list_date,
                delist_date=delist_date,
                tradable=tradable,
                classification_method="sina_spot+sse_szse_reference_v1",
            )
            securities.append(security)
            limit_up = None
            limit_down = None
            up_locked = None
            down_locked = None
            if board == "MAIN" and previous_close is not None and previous_close > 0:
                rate = 0.05 if status in {"ST", "STAR_ST"} else 0.10
                limit_up = _limit_price(previous_close, rate, security.tick_size, True)
                limit_down = _limit_price(previous_close, rate, security.tick_size, False)
                if high is not None and low is not None:
                    up_locked = abs(high - low) < 1e-9 and abs(high - limit_up) < 1e-9
                    down_locked = abs(high - low) < 1e-9 and abs(low - limit_down) < 1e-9
            statuses.append(
                SecurityStatus(
                    as_of_date=as_of_date,
                    symbol=symbol,
                    asset_type=security.asset_type,
                    name=name,
                    status=security.status,
                    is_st=status in {"ST", "STAR_ST"},
                    is_delisting=status == "DELISTING" or delist_date is not None,
                    is_suspended=is_suspended,
                    list_date=list_date,
                    delist_date=delist_date,
                    previous_close=previous_close,
                    last_price=last_price,
                    volume=volume,
                    amount=amount,
                    limit_up_price=limit_up,
                    limit_down_price=limit_down,
                    is_limit_up_locked=up_locked,
                    is_limit_down_locked=down_locked,
                    tradable=tradable,
                    observed_at=observed.isoformat(),
                    source="sina_spot+sse_szse_reference_v1",
                )
            )

        current_symbols = {item.symbol for item in securities}
        for old in previous:
            if old.symbol in current_symbols:
                continue
            delist_date = delist_dates.get(old.symbol)
            statuses.append(
                SecurityStatus(
                    as_of_date=as_of_date,
                    symbol=old.symbol,
                    asset_type=old.asset_type,
                    name=old.name,
                    status="DELISTED" if delist_date else "REMOVED_OR_SOURCE_MISSING",
                    is_st=False,
                    is_delisting=delist_date is not None,
                    is_suspended=None,
                    list_date=old.list_date,
                    delist_date=delist_date,
                    previous_close=None,
                    last_price=None,
                    volume=None,
                    amount=None,
                    limit_up_price=None,
                    limit_down_price=None,
                    is_limit_up_locked=None,
                    is_limit_down_locked=None,
                    tradable=False,
                    observed_at=observed.isoformat(),
                    source="daily_master_diff+sse_szse_delist_reference_v1",
                )
            )
        deduplicated = {record.instrument_id: record for record in securities}
        status_deduplicated = {record.symbol: record for record in statuses}
        return list(deduplicated.values()), list(status_deduplicated.values()), warnings

    def _exchange_reference(
        self,
    ) -> tuple[dict[str, str], dict[str, str], list[str]]:
        list_dates: dict[str, str] = {}
        delist_dates: dict[str, str] = {}
        warnings: list[str] = []
        calls = (
            ("sse_list", lambda: ak.stock_info_sh_name_code(symbol="主板A股"), "SSE", ("证券代码", "A股代码", "code"), ("上市日期", "A股上市日期", "list_date"), False),
            ("szse_list", lambda: ak.stock_info_sz_name_code(symbol="A股列表"), "SZSE", ("A股代码", "证券代码", "code"), ("A股上市日期", "上市日期", "list_date"), False),
            ("sse_delist", lambda: ak.stock_info_sh_delist(symbol="全部"), "SSE", ("公司代码", "证券代码", "code"), ("终止上市日期", "暂停上市日期", "delist_date"), True),
            ("szse_delist", lambda: ak.stock_info_sz_delist(symbol="终止上市公司"), "SZSE", ("证券代码", "公司代码", "code"), ("终止上市日期", "delist_date"), True),
        )
        for name, call, exchange, code_names, date_names, is_delist in calls:
            try:
                frame = call()
                code_col = _first_column(frame, code_names)
                date_col = _first_column(frame, date_names)
                destination = delist_dates if is_delist else list_dates
                for code, value in frame[[code_col, date_col]].itertuples(index=False, name=None):
                    parsed = _optional_date(value)
                    if parsed:
                        destination[_symbol_from_code(code, exchange)] = parsed
            except Exception as exc:
                warnings.append(f"{name}_reference_unavailable:{type(exc).__name__}")
        return list_dates, delist_dates, warnings

    def _save(
        self,
        as_of_date: str,
        securities: list[Security],
        statuses: list[SecurityStatus],
    ) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.status_cache_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(asdict(item) for item in securities).to_parquet(
            self.cache_dir / f"master-{as_of_date}.parquet", index=False
        )
        pd.DataFrame(asdict(item) for item in statuses).to_parquet(
            self.status_cache_dir / f"status-{as_of_date}.parquet", index=False
        )

    def _load_latest(self, as_of_date: str) -> list[Security] | None:
        if not self.cache_dir.exists():
            return None
        eligible = [path for path in self.cache_dir.glob("master-*.parquet") if path.stem.removeprefix("master-") <= as_of_date]
        if not eligible:
            return None
        frame = pd.read_parquet(max(eligible))
        rows = []
        for row in frame.to_dict(orient="records"):
            for field in ("list_date", "delist_date", "industry_l1", "industry_l2"):
                if field in row and pd.isna(row[field]):
                    row[field] = None
            rows.append(Security(**row))
        return rows

    def _load_latest_status(self, as_of_date: str) -> list[SecurityStatus] | None:
        if not self.status_cache_dir.exists():
            return None
        eligible = [path for path in self.status_cache_dir.glob("status-*.parquet") if path.stem.removeprefix("status-") <= as_of_date]
        if not eligible:
            return None
        frame = pd.read_parquet(max(eligible))
        rows = []
        nullable = {"is_suspended", "list_date", "delist_date", "previous_close", "last_price", "volume", "amount", "limit_up_price", "limit_down_price", "is_limit_up_locked", "is_limit_down_locked"}
        for row in frame.to_dict(orient="records"):
            for field in nullable:
                if field in row and pd.isna(row[field]):
                    row[field] = None
            rows.append(SecurityStatus(**row))
        return rows
