"""Read the app's bounded retest records without interpreting prose outcomes."""

from __future__ import annotations

import json
import re


OUTCOMES = ('passed', 'failed', 'not_tested')
_ID = re.compile(r'[0-9a-f]{32}\Z')


def read_record(output: str) -> dict | None:
    if not isinstance(output, str) or len(output) > 4000:
        return None
    try:
        record = json.loads(output)
    except (ValueError, TypeError):
        return None
    if not isinstance(record, dict) or record.get('version') != 1:
        return None
    for key in ('request_id', 'verification_target_id'):
        if not isinstance(record.get(key), str) or not _ID.fullmatch(record[key]):
            return None
    index = record.get('verification_step')
    if not isinstance(index, int) or isinstance(index, bool) or not 1 <= index <= 6:
        return None
    for key, limit in (('target_description', 240), ('symptom', 500), ('question', 1000)):
        if not isinstance(record.get(key), str) or not record[key].strip() or len(record[key]) > limit:
            return None
    if record.get('outcome') not in (None, *OUTCOMES):
        return None
    return record


def matches_target(record: dict, plan: list[dict]) -> bool:
    index = record['verification_step'] - 1
    if index >= len(plan):
        return False
    target = plan[index]
    return (target.get('kind') == 'verification'
            and target.get('description') == record['target_description']
            and target.get('verification_target_id') == record['verification_target_id'])


def pending_retest(steps, plan: list[dict]) -> dict | None:
    """Only the latest question can expose active retest controls."""
    latest = next((s for s in reversed(steps) if s.tool in ('ask_user', 'request_retest')), None)
    if latest is None or latest.tool != 'request_retest' or latest.status != 'asked':
        return None
    record = read_record(latest.output)
    if record is None or record.get('outcome') is not None or not matches_target(record, plan):
        return None
    target = plan[record['verification_step'] - 1]
    if target.get('status') not in ('pending', 'in_progress'):
        return None
    return {key: record[key] for key in (
        'request_id', 'question', 'symptom', 'verification_step', 'target_description')}
