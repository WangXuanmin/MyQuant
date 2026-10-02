"""One SQLite transaction is the source of truth for every paper session."""
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import math
import shutil
import sqlite3

from ..domain import AccountState, json_value
from ..runtime.models import RuntimeState, account_from_dict


class LedgerConflict(RuntimeError):
    pass


def encode(value):
    return json.dumps(json_value(value), ensure_ascii=False, sort_keys=True, allow_nan=False)


class PaperLedger:
    def __init__(self, state_dir, account_id='paper'):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / 'paper_ledger.sqlite3'
        self.account_id = account_id

    def connect(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA synchronous=FULL')
        connection.executescript('''
        CREATE TABLE IF NOT EXISTS accounts(account_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, state_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(account_id TEXT NOT NULL, session TEXT NOT NULL, config_hash TEXT NOT NULL,
          input_hash TEXT NOT NULL, result_json TEXT NOT NULL, export_json TEXT NOT NULL, exported INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(account_id, session));
        CREATE TABLE IF NOT EXISTS fills(account_id TEXT NOT NULL, order_id TEXT NOT NULL, session TEXT NOT NULL, payload TEXT NOT NULL,
          PRIMARY KEY(account_id, order_id));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, account_id TEXT NOT NULL, session TEXT NOT NULL, payload TEXT NOT NULL);
        ''')
        return connection

    def initialize(self, config):
        if config.initial_positions:
            raise LedgerConflict('INITIAL_POSITION_RECONCILIATION_REQUIRED: only an empty new account is supported')
        if not self.path.exists() and (self.state_dir / 'paper_account.json').exists():
            raise LedgerConflict('LEGACY_MIGRATION_REQUIRED: use migrate-ledger; never reset an existing account')
        with closing(self.connect()) as con, con:
            con.execute('INSERT OR IGNORE INTO accounts VALUES(?,?,?)',
                (self.account_id, 0, encode(RuntimeState(AccountState(config.currency, config.initial_cash, {})))))
        return self.load()

    def load(self):
        with closing(self.read_connection()) as con:
            row = con.execute('SELECT state_json FROM accounts WHERE account_id=?', (self.account_id,)).fetchone()
        if row is None:
            raise LedgerConflict('account has not been initialized')
        return RuntimeState.from_dict(json.loads(row[0]))

    def read_connection(self):
        if not self.path.exists():
            raise LedgerConflict('ledger has not been initialized')
        return sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True, timeout=10)

    def get_run(self, session):
        if not self.path.exists():
            return None
        with closing(self.read_connection()) as con:
            row = con.execute('SELECT config_hash,input_hash,result_json,export_json,exported FROM runs WHERE account_id=? AND session=?',
                (self.account_id, session)).fetchone()
        return None if row is None else dict(config_hash=row[0], input_hash=row[1], result=json.loads(row[2]), exports=json.loads(row[3]), exported=bool(row[4]))

    @staticmethod
    def validate(state):
        account = state.account
        if not math.isfinite(account.cash) or account.cash < 0:
            raise LedgerConflict('invalid account cash')
        for symbol, position in account.positions.items():
            if symbol != position.symbol or position.shares <= 0 or not math.isfinite(position.average_cost):
                raise LedgerConflict('invalid position')
            if position.lots and sum(int(lot['shares']) for lot in position.lots) != position.shares:
                raise LedgerConflict('position lot quantities do not reconcile')
            if any(int(lot['shares']) <= 0 for lot in position.lots):
                raise LedgerConflict('position lot quantities must be positive')

    def commit(self, result, config_hash, input_hash, exports, *, fault=None):
        self.validate(result.state)
        session = result.state.last_session
        expected_revision = result.state.revision
        with closing(self.connect()) as con:
            try:
                con.execute('BEGIN IMMEDIATE')
                previous = con.execute('SELECT revision FROM accounts WHERE account_id=?', (self.account_id,)).fetchone()
                if con.execute('SELECT 1 FROM runs WHERE account_id=? AND session=?', (self.account_id, session)).fetchone():
                    raise LedgerConflict('SESSION_ALREADY_COMMITTED')
                if previous is None or previous[0] != expected_revision:
                    raise LedgerConflict('ACCOUNT_REVISION_CONFLICT')
                result.state.revision = expected_revision + 1
                if 'files' in exports and 'runtime_result.json' in exports['files']:
                    exports['files']['runtime_result.json']['payload']['state']['revision'] = result.state.revision
                con.execute('UPDATE accounts SET revision=?,state_json=? WHERE account_id=?',
                    (result.state.revision, encode(result.state), self.account_id))
                if fault:
                    fault('after_state_write')
                for fill in result.fills:
                    con.execute('INSERT INTO fills VALUES(?,?,?,?)', (self.account_id, fill.order_id, session, encode(fill)))
                for event in result.events:
                    con.execute('INSERT INTO events(account_id,session,payload) VALUES(?,?,?)', (self.account_id, session, encode(event)))
                con.execute('INSERT INTO runs(account_id,session,config_hash,input_hash,result_json,export_json) VALUES(?,?,?,?,?,?)',
                    (self.account_id, session, config_hash, input_hash, encode(result), encode(exports)))
                if fault:
                    fault('before_commit')
                con.commit()
            except BaseException:
                con.rollback()
                result.state.revision = expected_revision
                raise
        if fault:
            fault('after_commit')

    def mark_exported(self, session):
        with closing(self.connect()) as con, con:
            con.execute('UPDATE runs SET exported=1 WHERE account_id=? AND session=?', (self.account_id, session))

    def history(self):
        if not self.path.exists():
            return []
        with closing(self.read_connection()) as con:
            return [json.loads(row[0]) for row in con.execute('SELECT result_json FROM runs WHERE account_id=? ORDER BY session', (self.account_id,))]

    def health(self):
        if not self.path.exists():
            return {'status': 'NOT_INITIALIZED', 'legacy_present': (self.state_dir/'paper_account.json').exists()}
        with closing(self.read_connection()) as con:
            integrity = con.execute('PRAGMA integrity_check').fetchone()[0]
            count = con.execute('SELECT COUNT(*) FROM fills WHERE account_id=?', (self.account_id,)).fetchone()[0]
            not_exported = con.execute('SELECT COUNT(*) FROM runs WHERE account_id=? AND exported=0', (self.account_id,)).fetchone()[0]
        state = self.load()
        self.validate(state)
        return {'status': 'PASS' if integrity == 'ok' else 'BLOCKED', 'integrity': integrity,
            'revision': state.revision, 'last_session': state.last_session, 'pending': len(state.pending), 'fills': count,
            'reports_pending_export':not_exported, 'needs_reconciliation':state.needs_reconciliation,
            'pending_orders':[{'id':x.order_id,'valid_from':x.valid_from,'expires_at':x.expires_at,'attempts':x.attempts,'last_attempt':x.last_attempt_session} for x in state.pending]}

    def migrate(self, config, *, cancel_pending=False):
        if self.path.exists():
            raise LedgerConflict('ledger already exists; migration must not overwrite it')
        source = self.state_dir / 'paper_account.json'
        if not source.exists():
            return self.initialize(config)
        account = account_from_dict(json.loads(source.read_text(encoding='utf-8')))
        # Existing nonempty portfolios need a separately reviewed lot/metadata reconstruction.
        if account.positions:
            raise LedgerConflict('REVIEW_REQUIRED: nonempty legacy positions require lot and metadata reconciliation')
        old_orders = json.loads((self.state_dir / 'pending_orders.json').read_text(encoding='utf-8')).get('orders', []) if (self.state_dir/'pending_orders.json').exists() else []
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = self.state_dir.parent / 'backups' / ('ledger-migration-' + stamp)
        backup.mkdir(parents=True)
        hashes = {}
        for path in self.state_dir.iterdir():
            if path.is_file():
                shutil.copy2(path, backup/path.name)
                hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (backup/'manifest.json').write_text(encode({'sha256': hashes, 'policy': 'cancel' if cancel_pending else 'needs_reconciliation'}), encoding='utf-8')
        state = RuntimeState(account)
        history_path = self.state_dir / 'performance_history.csv'
        if history_path.exists():
            import csv
            with history_path.open(encoding='utf-8-sig', newline='') as stream:
                history = list(csv.DictReader(stream))
            values = [float(row['total_value']) for row in history]
            if any(not math.isfinite(value) or value <= 0 for value in values):
                raise LedgerConflict('REVIEW_REQUIRED: invalid legacy performance history')
            state.peak = max([account.cash, *values])
        if abs(account.cash - config.initial_cash - account.realized_pnl) > .005:
            raise LedgerConflict('REVIEW_REQUIRED: legacy cash does not reconcile with initial cash and realized PnL')
        state.needs_reconciliation = bool(old_orders and not cancel_pending)
        self.validate(state)
        with closing(self.connect()) as con, con:
            con.execute('INSERT INTO accounts VALUES(?,?,?)', (self.account_id, 0, encode(state)))
            for order in old_orders:
                con.execute('INSERT INTO events(account_id,session,payload) VALUES(?,?,?)', (self.account_id, order['signal_date'],
                    encode({'type': 'LEGACY_ORDER', 'order': order, 'status': 'CANCELLED' if cancel_pending else 'NEEDS_RECONCILIATION',
                        'reason': 'user_approved_cancel_unvalidated_legacy_drafts' if cancel_pending else 'historical_inputs_missing'})))
        return state
