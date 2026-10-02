"""Compatibility names without the legacy multi-file write path."""
from pathlib import Path
import json
from ..runtime.models import account_from_dict


class AccountStore:
    """Read-only view; operational writes must use PaperLedger.commit."""
    def __init__(self,state_dir):
        self.state_dir=Path(state_dir)

    def load_or_initialize(self,config):
        from .ledger import PaperLedger
        ledger=PaperLedger(self.state_dir)
        if ledger.path.exists():
            return ledger.load().account
        path=self.state_dir/'paper_account.json'
        if path.exists():
            return account_from_dict(json.loads(path.read_text(encoding='utf-8')))
        return ledger.initialize(config).account


class LocalSimulator:
    """Pure session simulation; persistence belongs to the transaction ledger."""
    def __init__(self,config,calendar):
        from ..runtime.engine import SessionEngine
        self.engine=SessionEngine(config,calendar)

    def process_session(self,state,inputs):
        return self.engine.step(state,inputs)
