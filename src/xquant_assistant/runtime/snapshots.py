"""Immutable, hash-verified offline inputs and rebuildable report payloads."""
from dataclasses import asdict
from pathlib import Path
import json
import re
import pandas as pd

from ..domain import Security, json_value, DataQualityIssue
from ..storage import atomic_write_json, hash_payload, hash_source_tree
from .models import SessionInput, OpenState


def freeze_input(root, inputs, state, config, calendar):
    payload = json_value({'session': inputs.session, 'securities': inputs.securities, 'open_states': inputs.open_states,
        'fundamental_records': inputs.fundamental_records, 'industry_classifications': inputs.industry_classifications,
        'buy_symbols': inputs.buy_symbols, 'coverage': inputs.coverage, 'quality_issues': inputs.quality_issues,
        'initial_state': state, 'config': config.raw, 'calendar': asdict(calendar)})
    payload['source_hash'] = hash_source_tree(Path(__file__).resolve().parents[2])
    frames = {}
    for symbol, frame in sorted(inputs.market_data.items()):
        if not re.fullmatch(r'(sh|sz|bj)\d{6}', symbol):
            raise ValueError('invalid snapshot symbol')
        visible = frame.loc[:inputs.session]
        frames[symbol] = {'dates': [day.isoformat() for day in visible.index], 'columns': list(visible.columns), 'values': visible.values.tolist()}
    payload['market_data'] = json_value(frames)
    digest = hash_payload(payload)
    path = Path(root) / 'data/snapshots' / digest / 'input.json'
    if path.exists():
        load_input(path)
    else:
        atomic_write_json(path, {'sha256': digest, 'payload': payload})
    return digest, path


def load_input(path):
    document = json.loads(Path(path).read_text(encoding='utf-8'))
    payload = document['payload']
    if hash_payload(payload) != document['sha256']:
        raise ValueError('snapshot hash mismatch')
    frames = {symbol: pd.DataFrame(item['values'], index=pd.DatetimeIndex(item['dates'], name='date'), columns=item['columns']) for symbol,item in payload['market_data'].items()}
    inputs = SessionInput(payload['session'], tuple(Security(**x) for x in payload['securities']), frames,
        {key: OpenState(**value) for key,value in payload['open_states'].items()}, payload['fundamental_records'],
        payload['industry_classifications'], None if payload['buy_symbols'] is None else tuple(payload['buy_symbols']), payload['coverage'],
        tuple(DataQualityIssue(**x) for x in payload['quality_issues']))
    return inputs, payload
