"""Command-line entry point for the local trading-assistant MVP."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
import socket
from urllib.parse import urlparse

from .backtest_runner import ResearchBacktestRunner
from .runner import DailyRunner
from .runtime.daily import forward_validation_status
from .settings import default_project_root, load_config
from .storage import read_json
from .execution.ledger import PaperLedger
from .runtime.calendar import load_calendar, TradingCalendar, refresh_calendar
from .runtime.snapshots import load_input
from .runtime.models import RuntimeState
from .research.parity import validate_frozen_sequence
from .storage import hash_payload, hash_source_tree


def _endpoint_reachable(endpoint: str, timeout: float = 0.5) -> bool:
    parsed = urlparse(endpoint)
    if not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xquant-assistant")
    parser.add_argument(
        "--project-root", type=Path, default=default_project_root()
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    web = subparsers.add_parser("web", help="start the local browser control panel")
    web.add_argument("--port", type=int, default=8765)
    run = subparsers.add_parser("run-daily", help="run the local dry-run pipeline")
    run.add_argument("--as-of", default=None)
    run.add_argument("--analysis-revision", action="store_true")
    run.add_argument(
        "--symbols",
        help="comma-separated sh/sz symbols for an explicit trial universe",
    )
    run.add_argument("--offline", action="store_true")
    subparsers.add_parser("doctor", help="show resolved configuration and boundaries")
    subparsers.add_parser("account", help="show the local simulated account")
    subparsers.add_parser("status", help="show the latest run status")
    subparsers.add_parser("refresh-calendar", help="cache the Sina exchange calendar")
    capture = subparsers.add_parser("capture-opening-status", help="read and timestamp free TQ opening restrictions")
    capture.add_argument("--symbols", required=True)
    migration = subparsers.add_parser("migrate-ledger", help="backup and migrate the legacy account")
    migration.add_argument("--cancel-pending", action="store_true")
    rebuild = subparsers.add_parser("rebuild-report", help="rebuild a committed report without trading")
    rebuild.add_argument("--as-of", required=True)
    parity = subparsers.add_parser("validate-parity", help="compare adapters on frozen input files")
    parity.add_argument("--inputs", nargs="+", required=True, type=Path)
    parity.add_argument("--output", required=True, type=Path)
    backtest = subparsers.add_parser(
        "backtest", help="run an explicit-symbol research backtest"
    )
    backtest.add_argument("--start", required=True)
    backtest.add_argument("--end", required=True)
    backtest.add_argument("--symbols", required=True)
    backtest.add_argument("--benchmark")
    backtest.add_argument("--offline", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.project_root.resolve()
    config = load_config(root / "configs")
    if args.command == "web":
        from .web.server import serve
        serve(root,args.port)
        return 0
    if args.command == "run-daily":
        symbols = None
        if args.symbols:
            symbols = tuple(
                item.strip().lower() for item in args.symbols.split(",") if item.strip()
            )
        manifest, run_dir = DailyRunner(root, config).run(
            args.as_of,
            symbols=symbols,
            allow_network=not args.offline,
            analysis_revision=args.analysis_revision,
        )
        print(
            json.dumps(
                {
                    "run_id": manifest.run_id,
                    "status": manifest.status,
                    "run_dir": str(run_dir),
                    "symbols": len(manifest.symbols),
                    "warnings": list(manifest.warnings),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2 if manifest.status == "BLOCKED" else 0
    if args.command == "backtest":
        symbols = tuple(
            item.strip().lower() for item in args.symbols.split(",") if item.strip()
        )
        run_dir, manifest = ResearchBacktestRunner(root, config).run(
            args.start,
            args.end,
            symbols,
            benchmark_symbol=args.benchmark,
            allow_network=not args.offline,
        )
        print(
            json.dumps(
                {
                    "status": manifest["status"],
                    "run_id": manifest["run_id"],
                    "run_dir": str(run_dir),
                    "limitations": list(manifest["limitations"]),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "refresh-calendar":
        path = root / "data/calendar/sessions.json"
        calendar = refresh_calendar(root)
        print(json.dumps({"path": str(path), "coverage_end": calendar.coverage_end, "hash": calendar.digest}))
        return 0
    if args.command == "capture-opening-status":
        from .data.opening_status import capture_opening_status
        result = capture_opening_status(root, config, load_calendar(root), tuple(x.strip().lower() for x in args.symbols.split(',')))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['status'] == 'CAPTURED' else 2
    if args.command == "migrate-ledger":
        state = PaperLedger(root / "data/state").migrate(config.account, cancel_pending=args.cancel_pending)
        print(json.dumps(asdict(state.account), ensure_ascii=False, indent=2))
        return 0
    if args.command == "rebuild-report":
        print(DailyRunner(root, config).rebuild_report(args.as_of))
        return 0
    if args.command == "validate-parity":
        loaded = [load_input(path) for path in args.inputs]
        first = loaded[0][1]
        if any(hash_payload(item[1]["config"]) != hash_payload(config.raw) for item in loaded):
            raise ValueError("frozen configuration differs from current configuration")
        if any(item[1]["source_hash"] != hash_source_tree(Path(__file__).resolve().parents[1]) for item in loaded):
            raise ValueError("frozen source differs from current source; use the archived implementation")
        if any(hash_payload(item[1]["calendar"]) != hash_payload(first["calendar"]) for item in loaded):
            raise ValueError("frozen calendars differ")
        calendar = TradingCalendar(**{**first["calendar"], "sessions": tuple(first["calendar"]["sessions"])})
        report, _, _ = validate_frozen_sequence(root, config, calendar, [x[0] for x in loaded],
            RuntimeState.from_dict(first["initial_state"]), output_path=args.output)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "PASS" else 2
    if args.command == "doctor":
        try:
            calendar = load_calendar(root)
            calendar_health = {"status": "PASS", "coverage_start": calendar.coverage_start, "coverage_end": calendar.coverage_end, "hash": calendar.digest}
            forward_health = forward_validation_status(PaperLedger(root / "data/state"), calendar)
            from .runtime.health import data_health
            data_health_report = data_health(root, config, calendar)
        except ValueError as exc:
            calendar_health = {"status": "BLOCKED", "reason": str(exc)}
            forward_health = {"status": "NOT_YET_VALIDATED", "reason": str(exc)}
            data_health_report = {"status": "BLOCKED", "reason": str(exc)}
        print(
            json.dumps(
                {
                    "project_root": str(root),
                    "market_source": config.data.market_source,
                    "price_adjustment": config.data.price_adjustment,
                    "run_mode": config.account.mode,
                    "external_execution_enabled": config.account.external_execution_enabled,
                    "fundamental_provider": config.data.fundamental_provider,
                    "tdxquant_endpoint": config.data.tdxquant_endpoint,
                    "tdxquant_endpoint_reachable": _endpoint_reachable(
                        config.data.tdxquant_endpoint
                    ),
                    "tdxquant_tqcenter_path": config.data.tdxquant_tqcenter_path,
                    "tdxquant_tqcenter_exists": Path(
                        config.data.tdxquant_tqcenter_path
                    ).is_file(),
                    "tdxquant_financial_dat_files": len(
                        list(
                            Path(config.data.tdxquant_tqcenter_path).parents[2]
                            .joinpath("vipdoc", "cw")
                            .glob("gpcw*.dat")
                        )
                    )
                    if len(Path(config.data.tdxquant_tqcenter_path).parents) >= 3
                    else 0,
                    "tdxquant_access_mode": "direct_tqcenter"
                    if Path(config.data.tdxquant_tqcenter_path).is_file()
                    else "http",
                    "industry_provider": config.data.industry_provider,
                    "initial_cash": config.account.initial_cash,
                    "ledger": PaperLedger(root / "data/state").health(),
                    "calendar": calendar_health,
                    "forward_validation": forward_health,
                    "data_health": data_health_report,
                    "runtime": asdict(config.runtime),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "account":
        ledger = PaperLedger(root / "data/state")
        state = asdict(ledger.load().account) if ledger.path.exists() else read_json(root / "data/state/paper_account.json")
        print(json.dumps(state, ensure_ascii=False, indent=2, default=str))
        return 0
    status_path = root / "data" / "monitoring" / "status.json"
    payload = read_json(status_path) if status_path.exists() else {"status": "NO_REPORT"}
    ledger = PaperLedger(root / "data/state")
    if ledger.path.exists() and ledger.load().last_session is None:
        payload = {"status": "MIGRATED_WAITING_FIRST_SESSION", "ledger": ledger.health(), "previous_report": payload}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
