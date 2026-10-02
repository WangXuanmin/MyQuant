from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

from xquant_assistant.domain import Security


def market_frame(end: str = "2026-09-28", periods: int = 140, slope: float = 0.05) -> pd.DataFrame:
    index = pd.bdate_range(end=end, periods=periods)
    close = 10 + np.arange(periods) * slope
    return pd.DataFrame(
        {
            "open": close - 0.02,
            "high": close + 0.10,
            "low": close - 0.10,
            "close": close,
            "volume": 10_000_000 + np.arange(periods) * 1000,
            "amount": 100_000_000 + np.arange(periods) * 10_000,
        },
        index=pd.DatetimeIndex(index, name="date"),
    )


def setup_offline_project(destination: Path, source_project: Path) -> tuple[Security, Security]:
    shutil.copytree(source_project / "configs", destination / "configs")
    from xquant_assistant.runtime.calendar import TradingCalendar
    sessions = tuple(day.date().isoformat() for day in pd.bdate_range('2025-01-01', '2027-01-01'))
    TradingCalendar(sessions, 'ENGINEERING_FIXTURE_NOT_EXCHANGE_DATA', sessions[0], sessions[-1]).save(destination / 'data/calendar/sessions.json')
    (destination / "src").mkdir(parents=True)
    (destination / "src" / "dummy.py").write_text("# source hash fixture\n", encoding="utf-8")
    etf = Security(
        instrument_id="CN_ETF:sh510300",
        symbol="sh510300",
        name="沪深300ETF",
        asset_type="CN_ETF",
        exchange="SSE",
        board="ETF",
        tick_size=0.001,
        classification_method="fixture",
    )
    stock = Security(
        instrument_id="CN_A:sh600000",
        symbol="sh600000",
        name="浦发银行",
        asset_type="CN_A_MAIN",
        exchange="SSE",
        board="MAIN",
        classification_method="fixture",
    )
    master_dir = destination / "data" / "security_master"
    master_dir.mkdir(parents=True)
    pd.DataFrame([asdict(etf), asdict(stock)]).to_parquet(
        master_dir / "master-2026-09-28.parquet", index=False
    )
    market_dir = destination / "data" / "market"
    market_dir.mkdir(parents=True)
    market_frame(slope=0.03).to_parquet(market_dir / "sh510300.parquet")
    market_frame(slope=0.05).to_parquet(market_dir / "sh600000.parquet")
    return etf, stock
