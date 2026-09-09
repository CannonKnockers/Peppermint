"""Explicit user-reported retests; tool output and prose cannot attest success."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import uuid

from peppermint.common.models import Status
from peppermint.common.retest import OUTCOMES, pending_retest, read_record
from peppermint.daemon.tools.registry import Ask, Context, INTERNAL_TOOLS, ToolError, tool


@dataclass
class RetestRequest(Ask):
    record: dict

    def to_record(self) -> str:
        return json.dumps(self.record, ensure_ascii=False)


@dataclass
class RetestReceipt:
    step_id: int
    request_id: str
    outcome: str
    target_description: str
    symptom: str

    def to_tool_result(self) -> str:
        return json.dumps({
            'evidence_step_id': self.step_id, 'outcome': self.outcome,
            'target_description': self.target_description, 'symptom': self.symptom,
            'evidence_kind': 'user_reported', 'independently_verified': False,
        }, ensure_ascii=False)


@tool(
    name='request_retest',
    description=(
        'Ask the user to retest the original symptom for a current unfinished verification '
        'plan step, then wait for Passed / Still failing / Not tested. Specify the 1-based '
        'verification_step and a concrete symptom to retest. This asks only; it never '
        'launches an application or runs a command. Only the user\'s structured Passed '
        'submission completes that exact verification target as user-reported, never '
        'independently verified. Prose, tool logs and model claims cannot report a pass.'
    ),
    parameters={'type': 'object', 'properties': {
        'verification_step': {'type': 'integer', 'minimum': 1, 'maximum': 6},
        'symptom': {'type': 'string', 'maxLength': 500},
    }, 'required': ['verification_step', 'symptom']},
)
def request_retest(verification_step: int, symptom: str, ctx: Context = None):
    if not ctx or not ctx.db:
        raise ToolError('The task database is unavailable.')
    plan = ctx.db.get_plan(ctx.task_id)
    available = [{'verification_step': index, 'description': row['description'][:240]}
                 for index, row in enumerate(plan[:6], 1)
                 if row.get('kind') == 'verification'
                 and row.get('status') in ('pending', 'in_progress')
                 and isinstance(row.get('description'), str)]
    target_hint = (' Use the 1-based position in the whole plan, not an evidence_step_id. '
                   'Available verification targets: ' + json.dumps(available, ensure_ascii=False))
    if (not isinstance(verification_step, int) or isinstance(verification_step, bool)
            or not 1 <= verification_step <= 6):
        raise ToolError('verification_step must be a plan step number from 1 to 6.' + target_hint)
    if not isinstance(symptom, str) or not symptom.strip() or len(symptom) > 500:
        raise ToolError('Describe the specific original symptom to retest in 1-500 characters.')
    if verification_step > len(plan):
        raise ToolError('That verification plan step does not exist.' + target_hint)
    target = plan[verification_step - 1]
    if target.get('kind') != 'verification' or target.get('status') not in ('pending', 'in_progress'):
        raise ToolError('Retests must name an unfinished verification plan step.' + target_hint)
    # Add an identity to legacy verification rows without rewriting their prose.
    if not target.get('verification_target_id'):
        target['verification_target_id'] = uuid.uuid4().hex
        ctx.db.set_plan(ctx.task_id, plan)
    symptom = symptom.strip()
    question = f'Retest: {symptom}\nReport what happened for: {target["description"]}'
    record = {
        'version': 1, 'request_id': uuid.uuid4().hex,
        'verification_target_id': target['verification_target_id'],
        'verification_step': verification_step,
        'target_description': target['description'], 'symptom': symptom,
        'question': question, 'outcome': None,
        'requested_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
    }
    return RetestRequest(question=question, record=record)


def consume_retest(db, task_id: int, request_id: str, outcome: str) -> RetestReceipt | None:
    """Consume once under a write lock and update only the request's exact target."""
    if outcome not in OUTCOMES or not isinstance(request_id, str):
        return None
    conn = db.connection()
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        task = db.get_task(task_id, with_steps=False)
        if task is None or task.status != Status.AWAITING_INPUT.value:
            return None
        steps, plan = db.get_steps(task_id), db.get_plan(task_id)
        current = pending_retest(steps, plan)
        if current is None or current['request_id'] != request_id:
            return None
        step = next(s for s in reversed(steps) if s.tool == 'request_retest')
        if _has_later_action(steps, step.id):
            return None
        record = read_record(step.output)
        record['outcome'] = outcome
        record['reported_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        updated = conn.execute(
            "UPDATE steps SET output = ?, status = 'user_reported' "
            "WHERE id = ? AND task_id = ? AND status = 'asked'",
            (json.dumps(record, ensure_ascii=False), step.id, task_id),
        )
        if updated.rowcount != 1:
            return None
        if outcome == 'passed':
            target = plan[record['verification_step'] - 1]
            target.update(status='done', evidence_step_id=step.id,
                          evidence_kind='user_reported', evidence_tool='request_retest')
            conn.execute('UPDATE task_plans SET steps = ? WHERE task_id = ?',
                         (json.dumps(plan), task_id))
        receipt = RetestReceipt(step.id, request_id, outcome,
                                record['target_description'], record['symptom'])
        label = {'passed': 'Passed', 'failed': 'Still failing', 'not_tested': 'Not tested'}[outcome]
        messages = [
            {'role': 'tool', 'name': 'request_retest', 'content': receipt.to_tool_result()},
            {'role': 'user', 'content': f'Retest report: {label}. Target: {receipt.target_description}. '
             f'Symptom checked: {receipt.symptom}'},
        ]
        for message in messages:
            conn.execute('INSERT INTO messages (task_id, role, content, ts) VALUES (?, ?, ?, ?)',
                         (task_id, message['role'], json.dumps(message), record['reported_at']))
        conn.execute("UPDATE tasks SET status = 'queued', question = '', updated_at = ? WHERE id = ?",
                     (record['reported_at'], task_id))
        return receipt


def dismiss_retest(db, task_id: int) -> None:
    """Ordinary prose can continue the conversation but records no test outcome."""
    with db.connection() as conn:
        conn.execute("UPDATE steps SET status = 'superseded' WHERE task_id = ? "
                     "AND tool = 'request_retest' AND status = 'asked'", (task_id,))


def invalidate_retests_for_action(db, task_id: int, tool_name: str) -> None:
    """Any subsequent potential change expires earlier reports, before execution."""
    from peppermint.daemon.tools.planning import INSPECTION_TOOLS

    if tool_name in INSPECTION_TOOLS or tool_name in INTERNAL_TOOLS:
        return
    conn = db.connection()
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute("UPDATE steps SET status = 'superseded' WHERE task_id = ? "
                     "AND tool = 'request_retest' AND status IN ('asked', 'user_reported')", (task_id,))
        plan = db.get_plan(task_id)
        changed = False
        for target in plan:
            if target.get('kind') == 'verification' and target.get('evidence_kind') == 'user_reported':
                target['status'] = 'pending'
                for key in ('evidence_step_id', 'evidence_kind', 'evidence_tool'):
                    target.pop(key, None)
                changed = True
        if changed:
            conn.execute('UPDATE task_plans SET steps = ? WHERE task_id = ?', (json.dumps(plan), task_id))


def completion_evidence(db, task_id: int, observed, target: dict, index: int) -> dict:
    """Validate an already consumed report when the model updates a plan."""
    record = read_record(observed.output) if observed.tool == 'request_retest' else None
    if (observed.status != 'user_reported' or record is None or record.get('outcome') != 'passed'
            or record['verification_step'] != index + 1
            or record['verification_target_id'] != target.get('verification_target_id')
            or record['target_description'] != target['description']):
        raise ToolError('Verification needs a structured Passed retest for this exact plan target. '
                        'Use request_retest; a successful tool or prose reply is not evidence.')
    # Defense in depth if a caller recorded an action without using the Agent hook.
    if _has_later_action(db.get_steps(task_id), observed.id):
        raise ToolError('A later action makes this retest stale. Request a new retest.')
    if any(s.id > observed.id and s.tool == 'request_retest'
           and (newer := read_record(s.output)) is not None
           and newer['verification_target_id'] == record['verification_target_id']
           for s in db.get_steps(task_id)):
        raise ToolError('A newer retest supersedes this outcome. Use the latest retest.')
    return {'evidence_kind': 'user_reported', 'evidence_tool': 'request_retest'}


def _has_later_action(steps, step_id: int) -> bool:
    from peppermint.daemon.tools.planning import INSPECTION_TOOLS

    return any(s.id > step_id and s.tool not in INSPECTION_TOOLS | INTERNAL_TOOLS
               and s.status not in ('denied', 'refused', 'pending', 'blocked') for s in steps)
