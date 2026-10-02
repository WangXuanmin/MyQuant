"""Point-in-time fundamental data from the free TdxQuant local interface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import importlib.util
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.request import Request, urlopen

import pandas as pd


FIELDS = ("FN183", "FN191", "FN197", "FN210", "FN228", "FN232", "FN234", "FN336")
FIELD_NAMES = {
    "FN183": "revenue_growth_pct",
    "FN191": "adjusted_profit_growth_pct",
    "FN197": "roe_pct",
    "FN210": "debt_ratio_pct",
    "FN228": "operating_cash_to_profit",
    "FN232": "parent_net_profit",
    "FN234": "operating_cash_flow",
    "FN336": "audit_opinion",
}
FINANCIAL_INDUSTRY_WORDS = ("银行", "保险", "证券", "多元金融")


@dataclass(frozen=True)
class FundamentalSnapshot:
    available: bool
    values: dict[str, dict[str, Any]]
    provider: str
    reason: str | None = None
    warnings: tuple[str, ...] = ()
    records: dict[str, tuple[dict[str, Any], ...]] | None = None


def _internal_symbol(value: str) -> str:
    text = value.strip().upper()
    for suffix, prefix in ((".SH", "sh"), (".SZ", "sz"), (".BJ", "bj")):
        if text.endswith(suffix):
            return prefix + text[:6]
    return value.strip().lower()


def _tdx_symbol(value: str) -> str:
    text = value.strip().lower()
    for prefix, suffix in (("sh", ".SH"), ("sz", ".SZ"), ("bj", ".BJ")):
        if text.startswith(prefix):
            return text[2:8] + suffix
    return value.strip().upper()


def _finite_number(value: Any) -> float | None:
    if value in (None, "", "--", "None", "null"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normalize_date(value: Any) -> str | None:
    if value in (None, "", 0, "0"):
        return None
    digits = "".join(character for character in str(value) if character.isdigit())
    if len(digits) != 8:
        return None
    try:
        return date.fromisoformat(f"{digits[:4]}-{digits[4:6]}-{digits[6:]}").isoformat()
    except ValueError:
        return None


def normalize_tdxquant_response(payload: Mapping[str, Any]) -> dict[str, tuple[dict[str, Any], ...]]:
    """Normalize the HTTP wrapper's column-oriented result into dated records."""

    result = payload.get("result", payload)
    if not isinstance(result, Mapping):
        raise ValueError("TdxQuant response has no result mapping")
    error_id = str(result.get("ErrorId", result.get("error_id", "0")))
    if error_id not in {"0", "None", ""}:
        message = result.get("ErrorMsg", result.get("error_msg", "unknown error"))
        raise RuntimeError(f"TdxQuant ErrorId={error_id}: {message}")
    value = result.get("Value", result.get("value", result))
    if not isinstance(value, Mapping):
        raise ValueError("TdxQuant response has no Value mapping")
    normalized: dict[str, tuple[dict[str, Any], ...]] = {}
    for source_symbol, raw in value.items():
        if str(source_symbol) in {"ErrorId", "ErrorMsg", "KlinePaged"}:
            continue
        rows: list[dict[str, Any]] = []
        if isinstance(raw, list):
            rows = [dict(item) for item in raw if isinstance(item, Mapping)]
        elif isinstance(raw, Mapping):
            columns = {
                str(key): list(item) if isinstance(item, (list, tuple)) else [item]
                for key, item in raw.items()
                if str(key) not in {"ErrorId", "ErrorMsg"}
            }
            row_count = max((len(item) for item in columns.values()), default=0)
            rows = [
                {key: items[index] if index < len(items) else None for key, items in columns.items()}
                for index in range(row_count)
            ]
        cleaned: list[dict[str, Any]] = []
        for row in rows:
            announce = _normalize_date(row.get("announce_time", row.get("AnnounceTime")))
            tag = _normalize_date(row.get("tag_time", row.get("TagTime")))
            if announce is None:
                continue
            record: dict[str, Any] = {"announce_time": announce, "tag_time": tag}
            record.update({field: _finite_number(row.get(field)) for field in FIELDS})
            cleaned.append(record)
        if cleaned:
            normalized[_internal_symbol(str(source_symbol))] = tuple(
                sorted(cleaned, key=lambda item: (item["announce_time"], item.get("tag_time") or ""))
            )
    return normalized


def normalize_tdxquant_direct_response(
    payload: Mapping[str, Any],
) -> dict[str, tuple[dict[str, Any], ...]]:
    """Normalize the DataFrame mapping returned by the local tqcenter module."""

    value: dict[str, Any] = {}
    for symbol, raw in payload.items():
        if isinstance(raw, pd.DataFrame):
            value[str(symbol)] = raw.to_dict(orient="list")
        elif isinstance(raw, (Mapping, list)):
            value[str(symbol)] = raw
    return normalize_tdxquant_response({"result": {"ErrorId": "0", "Value": value}})


def _latest_records(
    records: Mapping[str, Iterable[Mapping[str, Any]]], as_of_date: str
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for symbol, history in records.items():
        visible = [dict(item) for item in history if str(item.get("announce_time", "")) <= as_of_date]
        if visible:
            output[symbol] = max(
                visible,
                key=lambda item: (str(item.get("announce_time", "")), str(item.get("tag_time", ""))),
            )
    return output


def score_fundamentals(
    records: Mapping[str, Iterable[Mapping[str, Any]]],
    as_of_date: str,
    classifications: Mapping[str, Mapping[str, str]] | None = None,
) -> FundamentalSnapshot:
    """Build cross-sectional scores from reports announced no later than as_of_date."""

    latest = _latest_records(records, as_of_date)
    saved_records = {symbol: tuple(dict(x) for x in history) for symbol, history in records.items()}
    if not latest:
        return FundamentalSnapshot(
            False, {}, "tdxquant", "no financial report announced by the as-of date", records=saved_records
        )
    frame = pd.DataFrame.from_dict(latest, orient="index")
    components: dict[str, pd.Series] = {}
    for field in ("FN183", "FN191", "FN197", "FN228"):
        if field in frame and frame[field].notna().any():
            components[field] = frame[field].rank(pct=True, method="average")
    if "FN210" in frame and frame["FN210"].notna().any():
        components["FN210"] = (-frame["FN210"]).rank(pct=True, method="average")
    scores = pd.DataFrame(components).mean(axis=1, skipna=True) if components else pd.Series(dtype=float)
    classification_map = classifications or {}
    values: dict[str, dict[str, Any]] = {}
    for symbol, row in frame.iterrows():
        industry = str(classification_map.get(symbol, {}).get("industry_l1", ""))
        financial = any(word in industry for word in FINANCIAL_INDUSTRY_WORDS)
        net_profit = _finite_number(row.get("FN232"))
        operating_cash = _finite_number(row.get("FN234"))
        audit = _finite_number(row.get("FN336"))
        failed: list[str] = []
        if net_profit is not None and net_profit <= 0:
            failed.append("nonpositive_parent_net_profit")
        if not financial and operating_cash is not None and operating_cash <= 0:
            failed.append("nonpositive_operating_cash_flow")
        if audit is not None and int(audit) not in {0, 1}:
            failed.append("qualified_or_adverse_audit_opinion")
        present = sum(_finite_number(row.get(field)) is not None for field in FIELDS)
        item: dict[str, Any] = {
            "announce_time": row.get("announce_time"),
            "tag_time": row.get("tag_time"),
            "score": float(scores.get(symbol)) if symbol in scores and pd.notna(scores.get(symbol)) else None,
            "passes_filter": not failed,
            "filter_reasons": ";".join(failed),
            "field_coverage": present / len(FIELDS),
            "is_financial_industry": financial,
            "industry_l1": industry or None,
        }
        item.update({FIELD_NAMES[field]: _finite_number(row.get(field)) for field in FIELDS})
        values[str(symbol)] = item
    available = any(item.get("score") is not None for item in values.values())
    return FundamentalSnapshot(
        available,
        values,
        "tdxquant",
        None if available else "financial rows contain no scoreable fields",
        records=saved_records,
    )


class TdxQuantFundamentalProvider:
    """Read TdxQuant directly or via HTTP and retain announcement-time history."""

    def __init__(
        self,
        cache_dir: str | Path,
        endpoint: str,
        *,
        tqcenter_path: str | Path | None = None,
        timeout_seconds: float = 4.0,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.endpoint = endpoint.strip()
        self.tqcenter_path = Path(tqcenter_path) if tqcenter_path else None
        self.timeout_seconds = timeout_seconds

    def load(
        self,
        symbols: tuple[str, ...],
        as_of_date: str,
        *,
        classifications: Mapping[str, Mapping[str, str]] | None = None,
        allow_network: bool,
    ) -> FundamentalSnapshot:
        a_symbols = tuple(symbol for symbol in symbols if symbol[:2] in {"sh", "sz", "bj"})
        if not a_symbols:
            return FundamentalSnapshot(False, {}, "tdxquant", "no A-share symbols require fundamentals")
        try:
            records, warnings = self._history(a_symbols, as_of_date, allow_network=allow_network)
        except Exception as exc:
            return FundamentalSnapshot(
                False, {}, "tdxquant", f"local TdxQuant service unavailable: {type(exc).__name__}: {exc}"
            )
        snapshot = score_fundamentals(records, as_of_date, classifications)
        return FundamentalSnapshot(
            snapshot.available,
            snapshot.values,
            snapshot.provider,
            snapshot.reason,
            tuple(warnings) + snapshot.warnings,
            snapshot.records,
        )

    def _history(
        self, symbols: tuple[str, ...], as_of_date: str, *, allow_network: bool
    ) -> tuple[dict[str, tuple[dict[str, Any], ...]], tuple[str, ...]]:
        exact = self.cache_dir / f"tdxquant-financial-{as_of_date}.parquet"
        warnings: list[str] = []
        if exact.exists():
            frame = pd.read_parquet(exact)
            cached_symbols = set(frame["symbol"].astype(str)) if "symbol" in frame else set()
            missing = tuple(symbol for symbol in symbols if symbol not in cached_symbols)
            if missing and allow_network:
                try:
                    additions = self._records_to_frame(self._fetch(missing, as_of_date))
                    frame = pd.concat([frame, additions], ignore_index=True).drop_duplicates(
                        subset=["symbol", "announce_time", "tag_time"], keep="last"
                    )
                    frame.to_parquet(exact, index=False)
                except Exception as exc:
                    warnings.append(
                        "TdxQuant refresh failed; using partial cache: "
                        f"{type(exc).__name__}: {exc}"
                    )
            elif missing:
                warnings.append("TdxQuant cache missing symbols: " + ",".join(missing))
        elif allow_network:
            records = self._fetch(symbols, as_of_date)
            frame = self._records_to_frame(records)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(exact, index=False)
        else:
            eligible = [
                path for path in self.cache_dir.glob("tdxquant-financial-*.parquet")
                if path.stem.removeprefix("tdxquant-financial-") <= as_of_date
            ] if self.cache_dir.exists() else []
            if not eligible:
                raise RuntimeError("offline TdxQuant cache missing")
            source = max(eligible)
            frame = pd.read_parquet(source)
            warnings.append(f"using earlier TdxQuant cache: {source.name}")
        records = self._frame_to_records(frame)
        return ({symbol: records[symbol] for symbol in symbols if symbol in records}, tuple(warnings))

    def _fetch(self, symbols: tuple[str, ...], as_of_date: str) -> dict[str, tuple[dict[str, Any], ...]]:
        errors: list[str] = []
        if self.tqcenter_path is not None:
            if self.tqcenter_path.is_file():
                try:
                    return self._fetch_direct(symbols, as_of_date)
                except Exception as exc:
                    errors.append(f"direct tqcenter failed: {type(exc).__name__}: {exc}")
            else:
                errors.append(f"tqcenter not found: {self.tqcenter_path}")
        if self.endpoint:
            try:
                return self._fetch_http(symbols, as_of_date)
            except Exception as exc:
                errors.append(f"HTTP endpoint failed: {type(exc).__name__}: {exc}")
        raise RuntimeError("; ".join(errors) or "no TdxQuant access method configured")

    def _fetch_direct(
        self, symbols: tuple[str, ...], as_of_date: str
    ) -> dict[str, tuple[dict[str, Any], ...]]:
        assert self.tqcenter_path is not None
        module_spec = importlib.util.spec_from_file_location(
            "_xquant_assistant_tqcenter", self.tqcenter_path
        )
        if module_spec is None or module_spec.loader is None:
            raise ImportError(f"cannot load tqcenter module: {self.tqcenter_path}")
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        client = getattr(module, "tq", None)
        if client is None:
            raise ImportError(f"tqcenter module has no tq client: {self.tqcenter_path}")

        output: dict[str, tuple[dict[str, Any], ...]] = {}
        try:
            client.initialize(str(Path(__file__).resolve()))
            for start in range(0, len(symbols), 100):
                batch = symbols[start : start + 100]
                payload = client.get_financial_data(
                    stock_list=[_tdx_symbol(symbol) for symbol in batch],
                    field_list=list(FIELDS),
                    start_time="20000101",
                    end_time=as_of_date.replace("-", ""),
                    report_type="announce_time",
                )
                if not isinstance(payload, Mapping):
                    raise ValueError("direct tqcenter response is not a mapping")
                output.update(normalize_tdxquant_direct_response(payload))
        finally:
            client.close()
        if not output:
            raise RuntimeError("TdxQuant returned no financial records")
        return output

    def _fetch_http(
        self, symbols: tuple[str, ...], as_of_date: str
    ) -> dict[str, tuple[dict[str, Any], ...]]:
        output: dict[str, tuple[dict[str, Any], ...]] = {}
        for start in range(0, len(symbols), 100):
            batch = symbols[start : start + 100]
            request_payload = {
                "id": start // 100 + 1,
                "method": "get_financial_data",
                "params": {
                    "stock_list": [_tdx_symbol(symbol) for symbol in batch],
                    "field_list": list(FIELDS),
                    "start_time": "20000101",
                    "end_time": as_of_date.replace("-", ""),
                    "report_type": "announce_time",
                },
            }
            request = Request(
                self.endpoint,
                data=json.dumps(request_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            output.update(normalize_tdxquant_response(payload))
        if not output:
            raise RuntimeError("TdxQuant returned no financial records")
        return output

    @staticmethod
    def _records_to_frame(records: Mapping[str, Iterable[Mapping[str, Any]]]) -> pd.DataFrame:
        rows = [{"symbol": symbol, **dict(item)} for symbol, history in records.items() for item in history]
        return pd.DataFrame(rows, columns=["symbol", "announce_time", "tag_time", *FIELDS])

    @staticmethod
    def _frame_to_records(frame: pd.DataFrame) -> dict[str, tuple[dict[str, Any], ...]]:
        output: dict[str, tuple[dict[str, Any], ...]] = {}
        if frame.empty:
            return output
        for symbol, group in frame.groupby("symbol", sort=False):
            rows: list[dict[str, Any]] = []
            for raw in group.to_dict(orient="records"):
                rows.append({key: (None if pd.isna(value) else value) for key, value in raw.items() if key != "symbol"})
            output[str(symbol)] = tuple(rows)
        return output


class DisabledFundamentalProvider:
    """Explicit disabled provider."""

    def load(self, symbols: tuple[str, ...], as_of_date: str, *args, **kwargs) -> FundamentalSnapshot:
        return FundamentalSnapshot(
            available=False,
            values={},
            provider="disabled",
            reason="no fundamental provider configured",
        )
