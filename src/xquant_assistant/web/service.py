"""Read models and serialized user-requested jobs, without a second trading engine."""
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
import threading
import uuid
from zoneinfo import ZoneInfo

import pandas as pd
import yaml
from ..domain import json_value
from ..execution.ledger import PaperLedger
from ..settings import load_config
from ..storage import atomic_write_json, hash_payload
from ..runtime.calendar import load_calendar, refresh_calendar
from ..runtime.daily import DailyRunner, forward_validation_status
from ..runtime.health import data_health

TZ = ZoneInfo('Asia/Shanghai')


class BusyError(ValueError):
    pass


def symbol_list(value):
    if not isinstance(value, str):
        raise ValueError('证券列表必须是逗号分隔的 sh/sz 代码')
    symbols = tuple(dict.fromkeys(x.strip().lower() for x in re.split(r'[,，\s]+', value) if x.strip()))
    if not 1 <= len(symbols) <= 100 or any(not re.fullmatch(r'(sh|sz)\d{6}', x) for x in symbols):
        raise ValueError('请输入 1–100 个有效证券代码，例如 sh510300,sh600000')
    return symbols


def iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('日期格式须为 YYYY-MM-DD')
    return datetime.strptime(value, '%Y-%m-%d').date().isoformat()


def json_file(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


class WebService:
    def __init__(self, root, *, clock=None):
        self.root = Path(root).resolve()
        self.clock = clock or (lambda: datetime.now(TZ))
        self.test_clock = clock
        self.lock = threading.Lock()
        self.job_lock = threading.Lock()
        self.jobs = {}
        for path in sorted((self.root/'data/web/jobs').glob('*.json'),key=lambda x:x.stat().st_mtime)[-100:]:
            job = json_file(path)
            if job['status'] in {'QUEUED','RUNNING'}:
                job.update(status='INTERRUPTED',message='服务已重启。请查看账本与报告，或对同一日期幂等重跑；不会自动重试。')
            self.jobs[job['id']] = job

    def config(self):
        return load_config(self.root/'configs')

    def ledger(self):
        return PaperLedger(self.root/'data/state')

    def runs(self):
        rows = []
        for path in (self.root/'runs').glob('*'):
            if not path.is_dir() or not path.resolve().is_relative_to((self.root/'runs').resolve()):
                continue
            paper = json_file(path/'run_manifest.json')
            research = json_file(path/'backtest_manifest.json')
            manifest = paper or research
            if manifest is None:
                continue
            legacy = not (path/'runtime_result.json').exists() if paper else not (path/'runtime_events.json').exists()
            rows.append({'id': path.name, 'kind': 'paper' if paper else 'research', 'legacy': legacy,
                'date': manifest.get('as_of_date', manifest.get('end_date', '')),
                'status': manifest.get('status','UNKNOWN'), 'manifest': manifest})
        return sorted(rows, key=lambda x:x['id'], reverse=True)

    def run_path(self, run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', run_id):
            raise ValueError('无效报告编号')
        path = (self.root/'runs'/run_id).resolve()
        if not path.is_relative_to((self.root/'runs').resolve()) or not path.is_dir():
            raise ValueError('报告不存在')
        return path

    def csv_rows(self, path, limit=1000):
        if not path.exists() or path.stat().st_size < 3:
            return []
        try:
            return json_value(pd.read_csv(path).head(limit).to_dict(orient='records'))
        except pd.errors.EmptyDataError:
            return []

    def report(self, run_id):
        path = self.run_path(run_id)
        candidates=self.csv_rows(path/'candidate_ranking.csv')
        if (path/'factor_scores.parquet').exists():
            factors={x['symbol']:x for x in json_value(pd.read_parquet(path/'factor_scores.parquet').head(1000).to_dict(orient='records'))}
            candidates=[{**factors.get(x['symbol'],{}),**x} for x in candidates]
        return {'id':run_id, 'manifest':json_file(path/'run_manifest.json') or json_file(path/'backtest_manifest.json'),
            'legacy':not (path/'runtime_result.json').exists() and not (path/'runtime_events.json').exists(),
            'candidates':candidates, 'orders':self.csv_rows(path/'orders_draft.csv'),
            'risk':json_file(path/'risk_report.json',{}), 'quality':json_file(path/'data_quality.json',{}),
            'schedule':json_file(path/'schedule.json',{}), 'metrics':json_file(path/'metrics.json',{}),
            'equity':self.csv_rows(path/'equity_curve.csv',5000), 'coverage':json_file(path/'execution_coverage.json',{}),
            'files':[x.name for x in path.iterdir() if x.is_file() and x.suffix in {'.json','.csv','.html','.parquet'}]}

    def state(self):
        config, ledger = self.config(), self.ledger()
        state = ledger.load() if ledger.path.exists() else None
        history = ledger.history()
        latest = history[-1] if history else None
        account = asdict(state.account) if state else json_file(self.root/'data/state/paper_account.json',
            {'cash':config.account.initial_cash,'currency':'CNY','positions':{}})
        calendar_info, freshness, forward = {}, {}, {'status':'NOT_YET_VALIDATED','actual_sessions':0,'required_sessions':12}
        now = self.clock().astimezone(TZ)
        try:
            calendar = load_calendar(self.root)
            calendar_info = {'status':'PASS','is_trading_day':calendar.is_session(now.date().isoformat()),
                'latest_completed':calendar.latest_completed(now),'next_session':next((x for x in calendar.sessions if x>now.date().isoformat()),None),
                'coverage_end':calendar.coverage_end}
            forward = forward_validation_status(ledger,calendar)
            freshness = data_health(self.root,config,calendar,now)
        except (ValueError,OSError) as exc:
            calendar_info = {'status':'BLOCKED','reason':str(exc),'is_trading_day':False}
        equity = account['cash'] if not account['positions'] else (latest or {}).get('valuation',{}).get('total_value')
        curve = [{'date':x['state']['last_session'],'equity':x['valuation']['total_value'],'drawdown':x['valuation']['drawdown']}
            for x in history if x['valuation']['valid']]
        events = []
        if ledger.path.exists():
            with closing(ledger.read_connection()) as con:
                events = [json.loads(x[0]) for x in con.execute('SELECT payload FROM events WHERE account_id=? ORDER BY id DESC LIMIT 200',(ledger.account_id,))]
        runs = self.runs()
        paper_runs = [x for x in runs if x['kind']=='paper']
        source = paper_runs[0] if paper_runs else None
        # Use the committed account's report rather than a newer analysis-only revision.
        if state and state.last_session:
            record = ledger.get_run(state.last_session)
            report_id = Path(record['exports']['relative_run_dir']).name
            source = next((x for x in paper_runs if x['id']==report_id),source)
        candidates = self.report(source['id'])['candidates'] if source else []
        cached_symbols = sorted(x.stem for x in (self.root/config.data.cache_dir).glob('*.parquet'))
        validation_dirs = sorted((self.root/'runs').glob('acceptance-spec03-*'),reverse=True)
        validation = next((json_file(x/'acceptance_summary.json') for x in validation_dirs if (x/'acceptance_summary.json').exists()),None)
        web_dirs = sorted((self.root/'runs').glob('acceptance-web-*'),reverse=True)
        web_validation = next(({'id':x.name,'summary':json_file(x/'acceptance_summary.json')} for x in web_dirs if (x/'acceptance_summary.json').exists()),None)
        return json_value({'app':'xquant-local-console','project_root':str(self.root),'process_id':os.getpid(),'now':now.isoformat(),'calendar':calendar_info,'data_health':freshness,'config':config.raw,
            'config_hash':hash_payload(config.raw),'account':account,'equity':equity,'latest_valuation':None if not latest else latest['valuation'],
            'curve':curve,'pending':[] if not state else state.pending,'events':events,'schedule':{} if not latest else latest['schedule'],
            'ledger':ledger.health(),'forward':forward,'candidates':candidates,'candidate_source':source,
            'cached_symbols':cached_symbols,'runs':runs,'validation':validation,'web_validation':web_validation,'jobs':self.job_list()})

    def market(self,symbol,limit=120):
        symbol = symbol_list(symbol)
        if len(symbol)!=1:
            raise ValueError('一次仅查看一个证券')
        symbol=symbol[0]
        path=self.root/self.config().data.cache_dir/f'{symbol}.parquet'
        if not path.exists():
            return {'symbol':symbol,'bars':[],'source':'sina','adjustment':'raw'}
        frame=pd.read_parquet(path).sort_index()
        frame=frame.loc[:self.clock().date().isoformat()].tail(min(500,max(20,int(limit))))
        return json_value({'symbol':symbol,'source':'sina','adjustment':'raw',
            'bars':[{'date':day.date().isoformat(),**{key:float(row[key]) for key in ('open','high','low','close','volume','amount')}} for day,row in frame.iterrows()]})

    def job_list(self):
        with self.job_lock:
            return deepcopy(list(self.jobs.values()))[::-1]

    def save_job(self,job):
        with self.job_lock:
            self.jobs[job['id']]=deepcopy(job)
        atomic_write_json(self.root/'data/web/jobs'/f"{job['id']}.json",job)

    def submit(self,body):
        action=body.get('action')
        if action not in {'daily','analysis','rebuild','backtest','capture','refresh_data','refresh_calendar'}:
            raise ValueError('不支持的操作')
        params={}
        if action in {'daily','analysis','backtest','capture','refresh_data'}:
            params['symbols']=symbol_list(body.get('symbols',''))
        if action in {'analysis','rebuild'}:
            params['date']=iso_date(body.get('date'))
        elif body.get('date'):
            params['date']=iso_date(body['date'])
        if action=='backtest':
            params.update(start=iso_date(body.get('start')),end=iso_date(body.get('end')))
            if params['start']>=params['end']:
                raise ValueError('回测开始日期须早于结束日期')
            if body.get('benchmark') and body['benchmark'] not in params['symbols']:
                raise ValueError('基准须包含在本次证券列表中')
            params['benchmark']=body.get('benchmark') or None
        if 'offline' in body and not isinstance(body['offline'],bool):
            raise ValueError('offline 必须是布尔值')
        params['offline']=body.get('offline',False)
        if not self.lock.acquire(blocking=False):
            raise BusyError('已有任务正在执行，请等待完成后再运行或修改配置')
        job={'id':uuid.uuid4().hex,'action':action,'status':'QUEUED','submitted_at':self.clock().isoformat(),
            'params':params,'message':'任务已提交','result':None}
        try:
            self.save_job(job)
            threading.Thread(target=self._execute,args=(job,),daemon=True).start()
        except BaseException:
            self.lock.release()
            raise
        return json_value(job)

    def _execute(self,job):
        try:
            job.update(status='RUNNING',message='正在执行，请勿重复提交',started_at=self.clock().isoformat())
            self.save_job(job)
            config, params, action = self.config(),job['params'],job['action']
            runner=DailyRunner(self.root,config,clock=self.test_clock)
            if action in {'daily','analysis'}:
                manifest,path=runner.run(params.get('date'),symbols=params['symbols'],allow_network=not params['offline'],analysis_revision=action=='analysis')
                result={'run_id':path.name,'status':manifest.status,'commit_status':manifest.commit_status}
            elif action=='rebuild':
                path=runner.rebuild_report(params['date'])
                result={'run_id':path.name,'status':'REPORT_REBUILT_ACCOUNT_UNCHANGED'}
            elif action=='capture':
                from ..data.opening_status import capture_opening_status
                result=capture_opening_status(self.root,config,load_calendar(self.root),params['symbols'])
            elif action=='refresh_calendar':
                calendar=refresh_calendar(self.root)
                result={'status':'PASS','coverage_end':calendar.coverage_end}
            elif action=='backtest':
                from ..backtest_runner import ResearchBacktestRunner
                path,manifest=ResearchBacktestRunner(self.root,config).run(params['start'],params['end'],params['symbols'],
                    benchmark_symbol=params['benchmark'],allow_network=not params['offline'])
                result={'run_id':path.name,'status':manifest['status'],'limitations':manifest['limitations']}
            elif action=='refresh_data':
                from ..data.market import SinaMarketDataAdapter
                from ..data.security_master import SinaSecurityMasterProvider
                from ..data.quality import validate_market_data
                day=load_calendar(self.root).latest_completed(self.clock())
                securities,warnings=SinaSecurityMasterProvider(self.root/config.data.security_master_dir).fetch(day,symbols=params['symbols'],allow_network=False)
                data,issues=SinaMarketDataAdapter(self.root/config.data.cache_dir).fetch_many(securities,config.data.history_start,day,allow_network=not params['offline'])
                quality=validate_market_data(data,day,min_history_sessions=config.universe.min_history_sessions,stale_after_calendar_days=config.data.stale_after_calendar_days,fetch_issues=issues)
                result={'status':'BLOCKED' if quality.blocked else 'PASS','symbols':len(data),'quality':asdict(quality),'warnings':warnings}
            outcome=result.get('status')
            job.update(status='BLOCKED' if outcome in {'BLOCKED','PROBE_ONLY'} else 'SUCCEEDED',
                message='操作受数据或时间限制，详见结果' if outcome in {'BLOCKED','PROBE_ONLY'} else '操作完成',result=json_value(result))
        except Exception as exc:
            job.update(status='FAILED',message=str(exc),error_type=type(exc).__name__)
        finally:
            job['finished_at']=self.clock().isoformat()
            try:
                self.save_job(job)
            finally:
                self.lock.release()

    def update_config(self,body):
        if not self.lock.acquire(blocking=False):
            raise BusyError('任务执行期间不能更改配置')
        try:
            original=self.config()
            if body.get('expected_hash')!=hash_payload(original.raw):
                raise BusyError('配置已被其他操作修改，请刷新后重试')
            changes=body.get('changes')
            allowed={'universe':{'asset_types','min_price','min_median_turnover_20d','custom_include','custom_exclude'},
                'strategy':{'rebalance_sessions','new_buy_top_n','hold_buffer_top_n','price_volume_weights','component_weights'},
                'risk':{'max_single_stock_weight','max_single_etf_weight','max_industry_weight','min_cash_weight'}}
            if not isinstance(changes,dict) or not changes:
                raise ValueError('没有配置变更')
            raw=deepcopy(original.raw)
            for group,values in changes.items():
                if group not in allowed or not isinstance(values,dict) or not set(values)<=allowed[group]:
                    raise ValueError('存在不可修改的配置字段')
                raw[group].update(values)
            if not raw['universe']['asset_types'] or not set(raw['universe']['asset_types'])<={'CN_ETF','CN_A_MAIN'}:
                raise ValueError('当前仅支持境内 ETF 和 A 股主板')
            for key in ('custom_include','custom_exclude'):
                values=raw['universe'][key]
                if not isinstance(values,list) or (values and tuple(values)!=symbol_list(','.join(values))):
                    raise ValueError('自定义列表须为有效且不重复的证券代码')
            for key in ('rebalance_sessions','new_buy_top_n','hold_buffer_top_n'):
                if type(raw['strategy'][key]) is not int or not 1<=raw['strategy'][key]<=250:
                    raise ValueError('调仓间隔和排名名额须为 1–250 的整数')
            for key in ('min_price','min_median_turnover_20d'):
                if not isinstance(raw['universe'][key],(int,float)) or not math.isfinite(raw['universe'][key]) or raw['universe'][key]<0:
                    raise ValueError('价格和成交额门槛须为非负数')
            for value in raw['risk'].values():
                if not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
                    raise ValueError('风险限制须为有限非负数')
            if any(not 0<raw['risk'][key]<=1 for key in ('max_single_stock_weight','max_single_etf_weight','max_industry_weight')):
                raise ValueError('仓位上限须介于 0 和 100% 之间')
            for field in ('price_volume_weights','component_weights'):
                weights=raw['strategy'][field]
                if not isinstance(weights,dict) or set(weights)!=set(original.raw['strategy'][field]):
                    raise ValueError('因子键必须与当前实现匹配')
                groups=[weights] if field=='price_volume_weights' else list(weights.values())
                for index,group in enumerate(groups):
                    expected=original.raw['strategy'][field] if field=='price_volume_weights' else original.raw['strategy'][field][list(weights)[index]]
                    if not isinstance(group,dict) or set(group)!=set(expected) or any(type(x) not in (int,float) or not math.isfinite(x) or x<0 for x in group.values()) or abs(sum(group.values())-1)>1e-8:
                        raise ValueError('各组因子权重须为非负数并合计 100%')
            raw['strategy']['version']='web-'+self.clock().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]
            with TemporaryDirectory(prefix='xquant-config-') as directory:
                temp=Path(directory)
                for group,values in raw.items():
                    (temp/f'{group}.yaml').write_text(yaml.safe_dump(values,allow_unicode=True,sort_keys=False),encoding='utf-8')
                validated=load_config(temp)
                if body.get('preview'):
                    return {'status':'VALID','config_hash':hash_payload(validated.raw),'changes':changes}
                backup=self.root/'data/backups'/('web-config-'+uuid.uuid4().hex)
                shutil.copytree(self.root/'configs',backup)
                written=[]
                try:
                    for group in sorted(set(changes)|{'strategy'}):
                        target=self.root/'configs'/f'{group}.yaml'
                        temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
                        temporary.write_bytes((temp/f'{group}.yaml').read_bytes())
                        temporary.replace(target)
                        written.append(group)
                except BaseException:
                    for group in written:
                        shutil.copy2(backup/f'{group}.yaml',self.root/'configs'/f'{group}.yaml')
                    raise
            event={'status':'SAVED','config_hash':hash_payload(self.config().raw),'previous_hash':hash_payload(original.raw),
                'changes':changes,'saved_at':self.clock().isoformat(),'version':raw['strategy']['version'],
                'effect':'next_daily_run; pending_old_intents_cancelled_by_engine; anchor_preserved','backup':str(backup)}
            atomic_write_json(self.root/'data/web/config_events'/f'{uuid.uuid4().hex}.json',event)
            return event
        finally:
            self.lock.release()
