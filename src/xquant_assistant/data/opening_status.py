"""Read-only TQ observations; never relabel late or stale quotes as opening data."""
from dataclasses import asdict
from datetime import datetime, time
import importlib.util
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from ..runtime.models import OpenState
from ..storage import atomic_write_json, immutable_write_json, hash_payload


def normalize_opening(symbol, raw, observed_at, calendar, identity=None):
    if observed_at.tzinfo is None:
        raise ValueError('opening capture requires a timezone-aware clock')
    now = observed_at.astimezone(ZoneInfo('Asia/Shanghai'))
    day = now.date().isoformat()
    if not calendar.is_session(day):
        return None, 'NON_TRADING_DAY'
    if not time(9, 15) <= now.time().replace(tzinfo=None) <= time(9, 30):
        return None, 'OUTSIDE_PREOPEN_CAPTURE_WINDOW'
    if str(raw.get('HqDate')) != day.replace('-', ''):
        return None, 'STALE_OR_UNKNOWN_QUOTE_DATE'
    flag = str(raw.get('TPFlag', ''))
    # Only an explicit zero permits trading; other documented suspension flags block.
    suspended = False if flag == '0' else True if flag in {'1', '2'} else None
    if suspended is None:
        return None, 'SUSPENSION_FLAG_UNKNOWN'
    try:
        upper, lower = float(raw['ZTPrice']), float(raw['DTPrice'])
    except (KeyError, TypeError, ValueError):
        return None, 'PRICE_LIMIT_UNKNOWN'
    if not all(math.isfinite(x) and x > 0 for x in (upper, lower)) or lower >= upper:
        return None, 'PRICE_LIMIT_UNKNOWN'
    # Product settlement remains the explicit conservative T+1 assumption.
    identity = identity or {}
    flags = tuple(str(identity.get(key, '')) for key in ('IsSTGP','IsQuitGP'))
    buy_allowed = None if any(x not in {'0','1'} for x in flags) or not identity.get('Name') else all(x=='0' for x in flags)
    return OpenState(symbol, day, suspended, upper, lower, 'tdxquant.get_more_info+get_stock_info', now.isoformat(), buy_allowed=buy_allowed), None


def capture_opening_status(root, config, calendar, symbols, *, tq_client=None, clock=None):
    symbols = tuple(sorted(set(symbols)))
    if not symbols or any(not re.fullmatch(r'(sh|sz)\d{6}', x) for x in symbols):
        raise ValueError('explicit valid sh/sz symbols are required')
    if clock is not None and tq_client is None:
        raise ValueError('custom clocks require an injected test client')
    if tq_client is None:
        spec = importlib.util.spec_from_file_location('_assistant_opening_tq', config.data.tdxquant_tqcenter_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tq_client = module.tq
    own_client = clock is None
    observations, samples = [], []
    try:
        if own_client:
            tq_client.initialize(__file__)
        for symbol in symbols:
            code = symbol[2:] + ('.SH' if symbol.startswith('sh') else '.SZ')
            raw = tq_client.get_more_info(stock_code=code, field_list=['HqDate', 'TPFlag', 'ZTPrice', 'DTPrice', 'IsT0Fund'])
            identity = tq_client.get_stock_info(stock_code=code, field_list=['Name','IsSTGP','IsQuitGP','HSStockKind','Unit','MinPrice'])
            now = clock() if clock else datetime.now(ZoneInfo('Asia/Shanghai'))
            state, reason = normalize_opening(symbol, raw, now, calendar, identity)
            samples.append({'symbol': symbol, 'observed_at': now.isoformat(), 'raw': raw, 'identity': identity, 'rejection': reason})
            if state:
                observations.append(asdict(state))
    finally:
        if own_client:
            tq_client.close()
    payload = {'provider': 'tdxquant.get_more_info', 'samples': samples, 'observations': observations,
        'scope': 'READ_ONLY_CAPTURE', 'historical_backfill': False}
    digest = hash_payload(payload)
    atomic_write_json(Path(root)/'data/status_probes'/f'{digest}.json', {'sha256': digest, 'payload': payload})
    for session in sorted({x['session'] for x in observations}):
        path = Path(root)/'data/opening_status'/f'{session}.json'
        # Capture is immutable: late repeated runs cannot replace an earlier observation.
        if path.exists():
            raise ValueError('OPENING_ARCHIVE_ALREADY_EXISTS: original observations preserved')
        try:
            immutable_write_json(path, {'source_probe_hash': digest, 'observations': [x for x in observations if x['session'] == session]})
        except FileExistsError as exc:
            raise ValueError('OPENING_ARCHIVE_ALREADY_EXISTS: original observations preserved') from exc
    return {'status': 'CAPTURED' if observations else 'PROBE_ONLY', 'probe_hash': digest,
        'valid_opening_observations': len(observations), 'rejections': {x['symbol']: x['rejection'] for x in samples if x['rejection']}}
