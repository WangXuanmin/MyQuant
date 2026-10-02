"""Read-only probe for an installed TdxQuant client."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd


FIELDS = ["FN183", "FN191", "FN197", "FN210", "FN228", "FN232", "FN234", "FN336"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tdx-root", type=Path, default=Path(r"E:\tdx"))
    parser.add_argument("--financial", action="store_true")
    args = parser.parse_args()
    module_dir = args.tdx_root / "PYPlugins" / "user"
    sys.path.insert(0, str(module_dir))
    from tqcenter import tq

    payload: dict[str, object] = {
        "tdx_root": str(args.tdx_root),
        "tqcenter": str(module_dir / "tqcenter.py"),
    }
    try:
        tq.initialize(__file__)
        market = tq.get_market_data(
            field_list=["Close"],
            stock_list=["600000.SH"],
            count=1,
            dividend_type="none",
            period="1d",
        )
        payload["market_connected"] = bool(market)
        payload["market_fields"] = sorted(market) if isinstance(market, dict) else []
        if args.financial:
            financial = tq.get_financial_data(
                stock_list=["600000.SH"],
                field_list=FIELDS,
                start_time="20240101",
                end_time="20260929",
                report_type="announce_time",
            )
            payload["financial_symbols"] = sorted(financial) if isinstance(financial, dict) else []
            payload["financial_rows"] = {
                symbol: len(frame) if isinstance(frame, pd.DataFrame) else 0
                for symbol, frame in financial.items()
            } if isinstance(financial, dict) else {}
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1
    finally:
        tq.close()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
