"""Lightweight usage analytics for the Story Engine.

Every generation call logs one structured JSON line (client, endpoint, model,
token usage, a short label). `summarize()` rolls those lines up per client and
per endpoint so the admin dashboard can answer "who generated what, how often,
and what did it cost."

Storage is a JSONL append file. Set ANALYTICS_LOG_PATH to a persistent location
(e.g. a Render disk mount) so history survives redeploys — the default lives
inside the repo's ephemeral working dir and resets on every deploy.
"""

import os
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(
    os.environ.get('ANALYTICS_LOG_PATH', Path(__file__).parent / 'data' / 'analytics.jsonl')
)

# Cost estimate rates in USD per million tokens. Defaults approximate the Opus
# tier (cache read ~= 0.1x input, cache write ~= 1.25x input). Override via env
# with your actual contracted rates — these are estimates, not billed figures.
INPUT_COST = float(os.environ.get('ANALYTICS_INPUT_COST_PER_MTOK', '15'))
OUTPUT_COST = float(os.environ.get('ANALYTICS_OUTPUT_COST_PER_MTOK', '75'))
CACHE_READ_COST = float(os.environ.get('ANALYTICS_CACHE_READ_COST_PER_MTOK', '1.5'))
CACHE_WRITE_COST = float(os.environ.get('ANALYTICS_CACHE_WRITE_COST_PER_MTOK', '18.75'))

_lock = threading.Lock()

_USAGE_FIELDS = (
    'input_tokens',
    'output_tokens',
    'cache_read_input_tokens',
    'cache_creation_input_tokens',
)


def _usage_dict(usage):
    """Normalise an Anthropic usage object (or None) into a plain dict."""
    if usage is None:
        return {f: 0 for f in _USAGE_FIELDS}
    return {f: (getattr(usage, f, 0) or 0) for f in _USAGE_FIELDS}


def log_event(client_id, endpoint, model=None, usage=None, meta=None, ok=True):
    """Append one analytics event. Never raises — logging must not break a request."""
    event = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'client_id': client_id,
        'endpoint': endpoint,
        'model': model,
        'ok': ok,
        **_usage_dict(usage),
    }
    if meta:
        event['meta'] = meta
    try:
        with _lock:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOG_PATH.open('a', encoding='utf-8') as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + '\n')
    except Exception as exc:  # pragma: no cover - logging is best effort
        print(f'analytics log error: {exc}')
    return event


def _estimate_cost(totals):
    return round(
        (
            totals['input_tokens'] * INPUT_COST
            + totals['output_tokens'] * OUTPUT_COST
            + totals['cache_read_input_tokens'] * CACHE_READ_COST
            + totals['cache_creation_input_tokens'] * CACHE_WRITE_COST
        )
        / 1_000_000,
        4,
    )


def _new_bucket():
    return {
        'generations': 0,
        'errors': 0,
        'by_endpoint': {},
        'first_ts': None,
        'last_ts': None,
        **{f: 0 for f in _USAGE_FIELDS},
    }


def _accumulate(bucket, event):
    bucket['generations'] += 1
    if not event.get('ok', True):
        bucket['errors'] += 1
    endpoint = event.get('endpoint', 'unknown')
    bucket['by_endpoint'][endpoint] = bucket['by_endpoint'].get(endpoint, 0) + 1
    for field in _USAGE_FIELDS:
        bucket[field] += event.get(field, 0) or 0
    ts = event.get('ts')
    if ts:
        if bucket['first_ts'] is None or ts < bucket['first_ts']:
            bucket['first_ts'] = ts
        if bucket['last_ts'] is None or ts > bucket['last_ts']:
            bucket['last_ts'] = ts


def summarize(client_id=None, since=None, recent_limit=25):
    """Roll the JSONL log up into per-client and overall totals.

    client_id: restrict to one client. since: ISO timestamp lower bound.
    """
    overall = _new_bucket()
    per_client = {}
    recent = []

    if not LOG_PATH.exists():
        return {
            'overall': {**overall, 'estimated_cost_usd': 0.0},
            'clients': {},
            'recent': [],
            'log_path': str(LOG_PATH),
            'note': 'No events logged yet.',
        }

    with LOG_PATH.open('r', encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since and (event.get('ts') or '') < since:
                continue
            cid = event.get('client_id') or 'unknown'
            if client_id and cid != client_id:
                continue
            _accumulate(overall, event)
            bucket = per_client.setdefault(cid, _new_bucket())
            _accumulate(bucket, event)
            recent.append(event)

    for bucket in (overall, *per_client.values()):
        bucket['estimated_cost_usd'] = _estimate_cost(bucket)

    recent = sorted(recent, key=lambda e: e.get('ts', ''), reverse=True)[:recent_limit]

    return {
        'overall': overall,
        'clients': per_client,
        'recent': recent,
        'log_path': str(LOG_PATH),
    }
