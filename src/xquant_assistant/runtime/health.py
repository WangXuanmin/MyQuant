"""Read-only cache freshness and recorded TQ probe coverage."""
from pathlib import Path
import json
import pandas as pd


def data_health(root, config, calendar, now=None):
    root = Path(root)
    completed = calendar.latest_completed(now)
    rows = []
    for path in sorted((root/config.data.cache_dir).glob('*.parquet')):
        try:
            index = pd.read_parquet(path, columns=[]).index
            if not isinstance(index, pd.DatetimeIndex) or not len(index):
                raise ValueError('dated cache index unavailable')
            day = index[-1].date().isoformat()
            age = calendar.elapsed(day, completed)
            rows.append({'symbol': path.stem, 'latest_date': day, 'stale_sessions': age,
                'status': 'FRESH' if age == 0 else 'STALE' if age > 0 else 'FUTURE_CACHE_REQUIRES_ASOF_CUT'})
        except (ValueError, OSError) as exc:
            rows.append({'symbol': path.stem, 'status': 'UNVERIFIED', 'reason': str(exc)})
    paths = list((root/'data/status_probes').glob('*.json'))
    latest = max(paths, key=lambda path: path.stat().st_mtime) if paths else None
    probe = None if latest is None else json.loads(latest.read_text(encoding='utf-8'))
    return {'latest_completed_session': completed, 'market_caches': rows,
        'status': 'PASS' if rows and all(x['status']=='FRESH' for x in rows) else 'DATA_REFRESH_REQUIRED',
        'last_tq_status_probe': None if probe is None else {'hash': probe['sha256'],
            'samples': probe['payload']['samples'], 'valid_opening_observations': len(probe['payload']['observations'])},
        'opening_archives': len(list((root/'data/opening_status').glob('*.json')))}
