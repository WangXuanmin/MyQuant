"""I/O adapter around the shared session engine and transactional ledger."""
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import html
import json
import uuid
import pandas as pd

from ..data.fundamentals import DisabledFundamentalProvider, TdxQuantFundamentalProvider
from ..data.industry import DisabledIndustryProvider, SinaIndustryProvider
from ..data.market import SinaMarketDataAdapter
from ..data.security_master import SinaSecurityMasterProvider
from ..domain import RunManifest, json_value
from ..execution.ledger import PaperLedger
from ..storage import atomic_write_json, hash_payload, hash_source_tree
from ..research.evidence import disabled_evidence_cards
from .calendar import load_calendar
from .engine import SessionEngine
from .models import SessionInput, OpenState
from .snapshots import freeze_input, load_input


def open_states_from_archive(root, session):
    path = Path(root)/'data/opening_status'/f'{session}.json'
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding='utf-8'))
    return {item['symbol']: OpenState(**item) for item in payload['observations']}


def artifacts(result, manifest, universe, security_statuses, snapshot_path, coverage):
    rows = [asdict(x) for x in result.candidates]
    factor_rows = []
    for item in result.candidates:
        row = {key:value for key,value in asdict(item).items() if not isinstance(value, (dict, list, tuple))}
        for prefix, values in (('raw', item.raw_factors), ('normalized', item.normalized_factors), ('component', item.component_scores), ('effective_weight', item.effective_component_weights)):
            row.update({prefix+'_'+key:value for key,value in values.items()})
        factor_rows.append(row)
    payload = {
        'run_manifest.json': ('json', manifest),
        'runtime_result.json': ('json', result),
        'input_manifest.json': ('json', {'path': str(snapshot_path), 'coverage': coverage}),
        'data_quality.json': ('json', result.quality),
        'risk_report.json': ('json', result.risk),
        'schedule.json': ('json', result.schedule),
        'order_events.json': ('json', result.events),
        'pending_orders.json': ('json', {'orders': result.state.pending}),
        'security_status.csv': ('csv', [asdict(x) for x in security_statuses]),
        'universe.csv': ('csv', [asdict(x) for x in universe.included]),
        'excluded_instruments.csv': ('csv', [{'symbol': x.security.symbol, 'reasons': ';'.join(x.reasons)} for x in universe.excluded]),
        'factor_scores.parquet': ('parquet', factor_rows),
        'candidate_ranking.csv': ('csv', rows),
        'fundamental_scores.csv': ('csv', [{'symbol': symbol, **values} for symbol,values in result.fundamentals.values.items()]),
        'industry_classification.csv': ('csv', [{'symbol': symbol, **values} for symbol,values in result.industry.classifications.items()]),
        'industry_metrics.csv': ('csv', [{'industry_l1': key, **values} for key,values in result.industry.metrics.items()]),
        'target_weights.csv': ('csv', [asdict(x) for x in result.targets]),
        'orders_draft.csv': ('csv', [asdict(x) for x in result.orders]),
        'fills.csv': ('csv', [asdict(x) for x in result.fills]),
        'positions_after.json': ('json', result.state.account),
        'performance_snapshot.json': ('json', result.valuation),
        'reconciliation.json': ('json', result.valuation),
        'cash_ledger.csv': ('csv', [{'date': manifest.as_of_date, 'cash': result.state.account.cash, 'execution_mode': 'LOCAL_SIMULATED'}]),
        'evidence_cards.json': ('json', disabled_evidence_cards(manifest.as_of_date)),
    }
    report = json_value({'运行': manifest, '调仓计划': result.schedule, '账户与估值': result.valuation,
        '订单': result.orders, '待处理订单': result.state.pending, '风险检查': result.risk, '历史数据覆盖': coverage})
    payload['daily_report.html'] = ('text', '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>模拟交易日报</title><style>body{font-family:system-ui;max-width:1100px;margin:36px auto}pre{white-space:pre-wrap;background:#f5f7fa;padding:20px}</style><h1>模拟交易日报</h1><p>本地模拟；原始价格；暂不处理分红等公司行动。15% 回撤仅提示，继续模拟成交。</p><pre>'+html.escape(json.dumps(report, ensure_ascii=False, indent=2))+'</pre></html>')
    return json_value({name:{'kind': kind, 'payload': value} for name,(kind,value) in payload.items()})


def export_artifacts(root, exports):
    run_dir = Path(root)/exports['relative_run_dir']
    if not run_dir.resolve().is_relative_to((Path(root)/'runs').resolve()):
        raise ValueError('invalid export directory')
    load_input(exports['snapshot_path'])
    run_dir.mkdir(parents=True, exist_ok=True)
    for name, entry in exports['files'].items():
        if Path(name).name != name:
            raise ValueError('invalid artifact name')
        destination = run_dir/name
        temp = destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.tmp')
        kind, payload = entry['kind'], entry['payload']
        if kind == 'json':
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        elif kind == 'csv':
            pd.DataFrame(payload).to_csv(temp, index=False, encoding='utf-8-sig')
        elif kind == 'parquet':
            pd.DataFrame(payload).to_parquet(temp, index=False)
        else:
            temp.write_text(payload, encoding='utf-8')
        temp.replace(destination)
    return run_dir


class DailyRunner:
    def __init__(self, project_root, config, *, clock=None):
        self.project_root, self.config = Path(project_root), config
        self.test_clock = clock is not None
        self.clock = clock or (lambda: datetime.now(ZoneInfo('Asia/Shanghai')))

    def rebuild_report(self, session):
        ledger = PaperLedger(self.project_root/'data/state')
        record = ledger.get_run(session)
        if record is None:
            raise ValueError('no committed session to rebuild')
        path = export_artifacts(self.project_root, record['exports'])
        ledger.mark_exported(session)
        return path

    def run(self, as_of_date=None, **kwargs):
        try:
            return self._run(as_of_date, **kwargs)
        except Exception as exc:
            atomic_write_json(self.project_root/'data/monitoring/last_failure.json', {
                'requested_session': as_of_date, 'observed_at': datetime.now(timezone.utc).isoformat(),
                'error_type': type(exc).__name__, 'reason': str(exc),
                'recovery': 'check ledger commit before retry; committed runs rebuild reports only'})
            raise

    def _run(self, as_of_date=None, *, symbols=None, allow_network=None, analysis_revision=False, fault=None):
        network = self.config.data.allow_network if allow_network is None else allow_network
        calendar = load_calendar(self.project_root, allow_network=network)
        now = self.clock().astimezone(ZoneInfo('Asia/Shanghai'))
        day = as_of_date or calendar.latest_completed(now)
        calendar.check_range(day)
        if not calendar.is_session(day):
            raise ValueError('NON_TRADING_DAY: report/doctor only; account unchanged')
        today = now.date().isoformat()
        if day > calendar.latest_completed(now):
            raise ValueError('SESSION_NOT_COMPLETED')
        ledger = PaperLedger(self.project_root/'data/state')
        existing = ledger.get_run(day)
        if analysis_revision and not existing:
            raise ValueError('ANALYSIS_REVISION_REQUIRES_COMMITTED_SESSION')
        if existing and not analysis_revision:
            record_manifest = existing['exports']['files']['run_manifest.json']['payload']
            values = {**record_manifest, 'symbols': tuple(record_manifest['symbols']), 'warnings': tuple(record_manifest['warnings'])}
            manifest = RunManifest(**values)
            manifest = RunManifest(**{**asdict(manifest), 'warnings': manifest.warnings+('SESSION_ALREADY_COMMITTED; report rebuilt; account unchanged',)})
            return manifest, self.rebuild_report(day)
        if not analysis_revision:
            if not calendar.is_session(today):
                raise ValueError('NON_TRADING_DAY: viewing and isolated replay only')
            if day != calendar.latest_completed(now):
                raise ValueError('HISTORICAL_SESSION_REQUIRES_ISOLATED_REPLAY')
        state = ledger.initialize(self.config.account) if not existing else RuntimeStateForRevision(existing)
        requested = None if symbols is None else tuple(dict.fromkeys(x.lower() for x in symbols))
        management = set(state.account.positions) | {x.symbol for x in state.pending}
        fetch_symbols = None if requested is None else tuple(sorted(set(requested)|management))
        securities, statuses, warnings = SinaSecurityMasterProvider(self.project_root/self.config.data.security_master_dir,
            self.project_root/self.config.data.security_status_dir).fetch_snapshot(day, symbols=fetch_symbols, allow_network=network and day == today)
        known = {x.symbol:x for x in securities}
        for symbol in management:
            if (symbol not in known or known[symbol].classification_method == 'explicit_symbol_inference') and symbol in state.metadata:
                known[symbol] = state.metadata[symbol]
        securities = tuple(known.values())
        from ..universe.providers import build_universe, refine_universe
        initial = build_universe(securities, day, self.config.universe, source_warnings=warnings)
        required = {x.symbol: x for x in initial.included}
        required.update({symbol: known[symbol] for symbol in management if symbol in known})
        data, fetch_issues = SinaMarketDataAdapter(self.project_root/self.config.data.cache_dir).fetch_many(tuple(required.values()),
            self.config.data.history_start, day, allow_network=network)
        universe = refine_universe(initial, data, self.config.universe)
        buy_symbols = tuple(x.symbol for x in universe.included if requested is None or x.symbol in requested)
        buy_securities = tuple(x for x in universe.included if x.symbol in buy_symbols)
        industry = SinaIndustryProvider(self.project_root/self.config.data.industry_cache_dir).load(buy_securities, day, data,
            allow_network=network and day == today, min_members=self.config.strategy.industry_min_members,
            min_coverage=self.config.strategy.industry_min_coverage) if self.config.data.industry_provider == 'sina' else DisabledIndustryProvider().load(buy_symbols, day)
        a_symbols = tuple(x.symbol for x in buy_securities if x.asset_type == 'CN_A_MAIN')
        fundamentals = TdxQuantFundamentalProvider(self.project_root/self.config.data.fundamental_cache_dir,
            self.config.data.tdxquant_endpoint, tqcenter_path=self.config.data.tdxquant_tqcenter_path).load(a_symbols, day,
            classifications=industry.classifications, allow_network=network) if self.config.data.fundamental_provider == 'tdxquant' else DisabledFundamentalProvider().load(a_symbols, day)
        opening_states = open_states_from_archive(self.project_root, day)
        coverage = {'universe': 'collected_snapshot_no_complete_historical_universe',
            'opening_status': 'dated_archive' if opening_states else 'missing',
            'financial_revisions': 'unverified', 'industry_versions': 'collected_versions_only'}
        inputs = SessionInput(day, securities, data, opening_states, fundamentals.records,
            industry.classifications, buy_symbols, coverage, quality_issues=fetch_issues)
        digest, frozen = freeze_input(self.project_root, inputs, state, self.config, calendar)
        result = SessionEngine(self.config, calendar).step(state, inputs)
        timestamp = datetime.now(timezone.utc)
        run_id = day+'-'+timestamp.strftime('%H%M%S-%f')
        status = 'BLOCKED' if result.quality.blocked or result.risk.blocked else ('NO_ACTION' if not result.candidates else
            ('READY' if all(x.factor_mode == 'FULL' for x in result.candidates) else
             'DEGRADED_PRICE_VOLUME_ONLY' if all(x.factor_mode == 'DEGRADED_PRICE_VOLUME_ONLY' for x in result.candidates) else 'DEGRADED_PARTIAL'))
        extra = ('opening_status_archive_missing; affected orders cannot fill',) if not inputs.open_states else ()
        manifest = RunManifest(run_id, day, timestamp.isoformat(), self.config.strategy.strategy_id, self.config.strategy.version,
            self.config.account.mode, hash_payload(self.config.raw), hash_source_tree(Path(__file__).resolve().parents[2]),
            self.config.data.market_source, self.config.data.price_adjustment, self.config.account.fee_model,
            self.config.account.fee_schedule_as_of, buy_symbols, 'ANALYSIS_REVISION' if analysis_revision else status, tuple(warnings)+extra)
        from dataclasses import replace
        manifest = replace(manifest, account_revision_before=state.revision,
            account_revision_after=state.revision if analysis_revision else state.revision+1,
            input_hash=digest, commit_status='ANALYSIS_ONLY' if analysis_revision else 'COMMITTED')
        exports = {'relative_run_dir': 'runs/'+run_id, 'snapshot_path': str(frozen), 'files': artifacts(result, manifest, universe, statuses, frozen, inputs.coverage)}
        exports['forward_observation'] = {'eligible': not self.test_clock and not analysis_revision and day == timestamp.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
            and calendar.source.startswith('https://finance.sina.com.cn/'), 'actual_run_at': timestamp.isoformat()}
        if not analysis_revision:
            ledger.commit(result, manifest.config_hash, digest, exports, fault=fault)
        path = export_artifacts(self.project_root, exports)
        if not analysis_revision:
            ledger.mark_exported(day)
            atomic_write_json(self.project_root/'runs/latest.json', {'run_id': run_id, 'status': status, 'path': str(path)})
            atomic_write_json(self.project_root/'data/monitoring/status.json', {'last_run_id': run_id, 'last_run_date': day,
                'status': status, 'revision': result.state.revision, 'warnings': manifest.warnings,
                'forward_validation': forward_validation_status(ledger, calendar)})
        return manifest, path


def RuntimeStateForRevision(record):
    from .models import RuntimeState
    _, payload = load_input(record['exports']['snapshot_path'])
    return RuntimeState.from_dict(payload['initial_state'])


def forward_validation_status(ledger, calendar):
    actual = []
    for result in ledger.history():
        record = ledger.get_run(result['state']['last_session'])
        if record['exports'].get('forward_observation', {}).get('eligible'):
            actual.append(result)
    days = [x['state']['last_session'] for x in actual]
    anchor = next((x['schedule']['anchor'] for x in actual if x['schedule']['anchor']), None)
    required = [] if anchor is None else [calendar.shift(anchor, i) for i in range(12)]
    complete = bool(required) and set(required) <= set(days) and ledger.health()['status'] == 'PASS'
    return {'status': 'FORWARD_VALIDATED' if complete else 'NOT_YET_VALIDATED', 'actual_sessions': len(days),
        'required_sessions': 12, 'signal_anchor': anchor,
        'missing_sessions': [x for x in required if x not in days], 'dates': days,
        'scope': 'operational_reliability_only_not_profitability'}
