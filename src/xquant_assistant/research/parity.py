"""Compare full business events, rather than only final portfolio return."""
from pathlib import Path
from tempfile import TemporaryDirectory
from ..domain import json_value
from ..execution.ledger import PaperLedger
from ..runtime.engine import SessionEngine
from ..storage import atomic_write_json, hash_payload


def first_difference(left, right, path=''):
    if isinstance(left, dict) and isinstance(right, dict):
        if left.keys() != right.keys():
            return {'field': path, 'daily': sorted(left), 'research': sorted(right)}
        for key in sorted(left):
            diff = first_difference(left[key], right[key], path+'.'+key)
            if diff:
                return diff
        return None
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return {'field': path+'.length', 'daily': len(left), 'research': len(right)}
        for index,(a,b) in enumerate(zip(left,right)):
            diff = first_difference(a,b,path+f'[{index}]')
            if diff:
                return diff
        return None
    if type(left) in (int,float) and type(right) in (int,float):
        tolerance = 0 if path.endswith(('.cash','.fee','.total_value','.positions_value','.shares','.attempts','.revision')) else 1e-10 if 'score' in path else 1e-8
        if abs(left-right) <= tolerance:
            return None
    elif left == right:
        return None
    return {'field': path, 'daily': left, 'research': right}


def compare_results(daily, research):
    if len(daily) != len(research):
        return {'status': 'FAIL', 'difference': {'field':'sessions.length', 'daily':len(daily), 'research':len(research)}}
    for left,right in zip(daily,research):
        a,b = json_value(left),json_value(right)
        difference = first_difference(a,b)
        if difference:
            return {'status':'FAIL', 'first_different_session':a['state']['last_session'], 'difference':difference}
    return {'status':'PASS', 'sessions':len(daily), 'field_scope':'full_session_result'}


def validate_frozen_sequence(root, config, calendar, entries, initial_state, *, output_path=None):
    """Use a throwaway persisted ledger and an independent in-memory adapter."""
    from copy import deepcopy
    daily, research, hashes = [], [], []
    engine = SessionEngine(config, calendar)
    state = deepcopy(initial_state)
    with TemporaryDirectory(prefix='xquant-parity-') as directory:
        ledger = PaperLedger(Path(directory)/'state')
        ledger.initialize(config.account)
        # Test/research state only; never touches the project's operational ledger.
        from contextlib import closing
        from ..execution.ledger import encode
        with closing(ledger.connect()) as con, con:
            con.execute('UPDATE accounts SET revision=?,state_json=? WHERE account_id=?', (initial_state.revision,encode(initial_state),ledger.account_id))
        for inputs in entries:
            before = ledger.load()
            from ..runtime.snapshots import freeze_input
            digest, _ = freeze_input(root,inputs,before,config,calendar)
            left = engine.step(before,inputs)
            ledger.commit(left,hash_payload(config.raw),digest,{})
            daily.append(ledger.get_run(inputs.session)['result'])
            right = engine.step(state,inputs)
            right.state.revision += 1
            state = right.state
            research.append(json_value(right))
            hashes.append(digest)
    report = compare_results(daily,research)
    report.update({'input_hashes':hashes, 'starting_state_hash':hash_payload(initial_state), 'config_hash':hash_payload(config.raw),
        'calendar_hash':calendar.digest, 'forward_validation':False})
    if output_path:
        atomic_write_json(output_path,report)
    return report, daily, research
