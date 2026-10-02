"""Artifact-producing wrapper for the explicit-universe research backtest."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from .data.fundamentals import DisabledFundamentalProvider, TdxQuantFundamentalProvider
from .data.industry import DisabledIndustryProvider, SinaIndustryProvider
from .data.market import SinaMarketDataAdapter
from .data.quality import validate_market_data
from .data.security_master import SinaSecurityMasterProvider
from .metrics import BacktestMetrics, MetricConfig
from .research.benchmark import build_composite_benchmark
from .research.backtest import PriceVolumeResearchBacktester
from .settings import AppConfig
from .storage import atomic_write_json, write_csv
from .universe import build_universe, refine_universe
from .runtime.calendar import load_calendar
from .runtime.daily import open_states_from_archive


class ResearchBacktestRunner:
    """Run and export a research-only, current-universe backtest."""

    def __init__(self, project_root: str | Path, config: AppConfig) -> None:
        self.project_root = Path(project_root)
        self.config = config

    def run(
        self,
        start_date: str,
        end_date: str,
        symbols: Iterable[str],
        *,
        benchmark_symbol: str | None = None,
        allow_network: bool = True,
    ) -> tuple[Path, dict]:
        """Run the backtest and write metrics, equity, trades, and limitations."""

        explicit = tuple(dict.fromkeys(symbol.lower() for symbol in symbols))
        if not explicit:
            raise ValueError("backtest requires explicit --symbols")
        securities, master_warnings = SinaSecurityMasterProvider(
            self.project_root / self.config.data.security_master_dir
        ).fetch(end_date, symbols=explicit, allow_network=allow_network)
        initial = build_universe(
            securities,
            end_date,
            self.config.universe,
            source_warnings=master_warnings,
        )
        adapter = SinaMarketDataAdapter(self.project_root / self.config.data.cache_dir)
        market_data, fetch_issues = adapter.fetch_many(
            initial.included,
            self.config.data.history_start,
            end_date,
            allow_network=allow_network,
        )
        quality = validate_market_data(
            market_data,
            end_date,
            min_history_sessions=self.config.universe.min_history_sessions,
            stale_after_calendar_days=self.config.data.stale_after_calendar_days,
            fetch_issues=fetch_issues,
        )
        if any(x.severity == 'ERROR' and x.symbol is None for x in quality.issues):
            raise RuntimeError("backtest market-data validation failed")
        universe = refine_universe(initial, market_data, self.config.universe)
        symbols_tuple = tuple(security.symbol for security in universe.included)
        if self.config.data.industry_provider == "sina":
            industry = SinaIndustryProvider(
                self.project_root / self.config.data.industry_cache_dir
            ).load(
                universe.included,
                end_date,
                market_data,
                allow_network=allow_network,
                min_members=self.config.strategy.industry_min_members,
                min_coverage=self.config.strategy.industry_min_coverage,
            )
        else:
            industry = DisabledIndustryProvider().load(symbols_tuple, end_date)
        a_symbols = tuple(
            security.symbol for security in universe.included if security.asset_type == "CN_A_MAIN"
        )
        if self.config.data.fundamental_provider == "tdxquant":
            fundamentals = TdxQuantFundamentalProvider(
                self.project_root / self.config.data.fundamental_cache_dir,
                self.config.data.tdxquant_endpoint,
                tqcenter_path=self.config.data.tdxquant_tqcenter_path,
            ).load(
                a_symbols,
                end_date,
                classifications=industry.classifications,
                allow_network=allow_network,
            )
        else:
            fundamentals = DisabledFundamentalProvider().load(a_symbols, end_date)
        calendar = load_calendar(self.project_root, allow_network=allow_network)
        backtester = PriceVolumeResearchBacktester(self.config)
        result = backtester.run(
            universe.included,
            market_data,
            start_date,
            end_date,
            fundamental_records=fundamentals.records,
            industry_classifications=industry.classifications,
            calendar=calendar,
            opening_states={day: open_states_from_archive(self.project_root, day) for day in calendar.sessions if start_date <= day <= end_date},
        )
        composite_benchmark = build_composite_benchmark(
            universe.included,
            market_data,
            start_date,
            end_date,
            rebalance_sessions=self.config.strategy.rebalance_sessions,
        )
        benchmark_returns = composite_benchmark["composite_return"].dropna()
        if benchmark_symbol:
            symbol = benchmark_symbol.lower()
            if symbol not in market_data:
                raise ValueError("benchmark must be included in --symbols")
            market_reference = (
                market_data[symbol]["close"]
                .reindex(composite_benchmark.index)
                .pct_change(fill_method=None)
            )
            composite_benchmark["market_reference_return"] = market_reference
        metrics = BacktestMetrics.from_run_result(
            result,
            config=MetricConfig(),
            benchmark_returns=benchmark_returns,
        )
        timestamp = datetime.now(UTC)
        run_id = f"backtest-{start_date}-{end_date}-{timestamp.strftime('%H%M%S-%f')}"
        run_dir = self.project_root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        atomic_write_json(run_dir / "runtime_events.json", backtester.last_results)
        atomic_write_json(run_dir / "execution_coverage.json", {
            "model": "shared_session_engine",
            "calendar_hash": calendar.digest,
            "missing_opening_status_sessions": [x.state.last_session for x in backtester.last_results
                if not open_states_from_archive(self.project_root, x.state.last_session)],
            "invalid_valuation_sessions": [x.state.last_session for x in backtester.last_results if not x.valuation["valid"]],
            "historical_financial_revisions": "unverified",
        })
        equity = pd.DataFrame(result.equity_curve, columns=["date", "equity"])
        equity.to_csv(run_dir / "equity_curve.csv", index=False, encoding="utf-8-sig")
        composite_benchmark.to_csv(
            run_dir / "composite_benchmark.csv", encoding="utf-8-sig"
        )
        atomic_write_json(
            run_dir / "benchmark_summary.json",
            {
                "model": "50pct_etf_equal_weight_plus_50pct_a_main_equal_weight",
                "rebalance_sessions": self.config.strategy.rebalance_sessions,
                "gross_of_costs": True,
                "composite_total_return": float(
                    composite_benchmark["composite_equity"].iloc[-1] - 1.0
                ),
                "etf_sleeve_total_return": float(
                    composite_benchmark["etf_sleeve_equity"].iloc[-1] - 1.0
                ),
                "a_share_sleeve_total_return": float(
                    composite_benchmark["a_share_sleeve_equity"].iloc[-1] - 1.0
                ),
                "market_reference_symbol": benchmark_symbol,
            },
        )
        trade_rows = [
            {
                "filled_at": fill.filled_at,
                "symbol": fill.order.symbol,
                "side": fill.order.side,
                "shares": fill.order.shares,
                "filled_price": float(fill.filled_price),
                "fee": float(fill.fee),
            }
            for fill in result.trades
        ]
        write_csv(run_dir / "trades.csv", trade_rows)
        write_csv(
            run_dir / "fundamental_scores.csv",
            [{"symbol": symbol, **values} for symbol, values in fundamentals.values.items()],
        )
        write_csv(
            run_dir / "industry_classification.csv",
            [
                {"symbol": symbol, **classification}
                for symbol, classification in industry.classifications.items()
            ],
        )
        write_csv(
            run_dir / "industry_metrics.csv",
            [{"industry_l1": name, **values} for name, values in industry.metrics.items()],
        )
        write_csv(
            run_dir / "drawdown_events.csv",
            [asdict(event) for event in metrics.drawdown_events()],
        )
        atomic_write_json(run_dir / "metrics.json", metrics.to_json_dict())
        limitations = [
            "research_only_current_universe",
            "sina_current_lists_cannot_reconstruct_historical_membership",
            "fixed_2026_fee_schedule_applied_to_all_sessions",
            "sina_industry_membership_is_archived_only_from_collection_date",
            "missing_dated_opening_status_blocks_affected_simulated_fills",
            "financial_announcement_dates_do_not_prove_complete_revision_history",
        ]
        if not fundamentals.available:
            limitations.append("fundamental_component_unavailable:" + str(fundamentals.reason))
        if not industry.available:
            limitations.append("industry_component_unavailable:" + str(industry.reason))
        manifest = {
            "run_id": run_id,
            "status": "RESEARCH_ONLY_CURRENT_UNIVERSE",
            "start_date": start_date,
            "end_date": end_date,
            "strategy_id": self.config.strategy.strategy_id,
            "strategy_version": self.config.strategy.version,
            "fee_model": self.config.account.fee_model,
            "fee_schedule_as_of": self.config.account.fee_schedule_as_of,
            "after_cost": self.config.account.fee_model != "unconfigured",
            "symbols": [security.symbol for security in universe.included],
            "benchmark_symbol": benchmark_symbol,
            "primary_benchmark": "50pct_etf_equal_weight_plus_50pct_a_main_equal_weight",
            "created_at": timestamp,
            "limitations": tuple(limitations),
            "universe_warnings": universe.warnings,
            "fundamental_provider": fundamentals.provider,
            "industry_provider": industry.provider,
            "fundamental_warnings": fundamentals.warnings,
            "industry_warnings": industry.warnings,
        }
        atomic_write_json(run_dir / "backtest_manifest.json", manifest)
        atomic_write_json(run_dir / "data_quality.json", quality)
        return run_dir, manifest
