"""App-only task tracking; this tool never executes a computer action."""
import uuid

from peppermint.daemon.tools.registry import Context, INTERNAL_TOOLS, ToolError, tool


# These capabilities describe what a successful invocation establishes, not
# whether it fulfils an arbitrary natural-language goal. Unknown tools fail
# closed until their evidence scope is reviewed here. Tool output is never
# parsed for claims such as ``verified_fix: true``: logs and file contents are
# untrusted data. Structured user retests have a separate validated path below.
INSPECTION_TOOLS = frozenset({
    'list_dir', 'read_file', 'search_files', 'system_info', 'performance_snapshot',
    'steam_game_diagnostics', 'apt_query', 'list_apps', 'list_scheduled',
    'gsettings_get', 'gsettings_list', 'list_themes', 'list_keybindings',
})
ACTION_TOOLS = frozenset({
    'write_file', 'make_dir', 'move_file', 'sort_folder', 'delete_file',
    'gsettings_set', 'set_keybinding', 'apt_install', 'schedule', 'unschedule',
    'open_url', 'notify_user', 'run_shell',
})
PLAN_KINDS = ('inspection', 'action', 'verification')


def _completion_evidence(kind, observed) -> dict:
    """Return app-derived evidence metadata; never trust model-supplied labels."""
    if kind not in PLAN_KINDS:
        raise ToolError('A done step must declare kind: inspection, action, or verification.')
    if kind == 'verification':
        raise ToolError(
            'Verification needs a structured Passed retest for this exact plan target. '
            'Use request_retest; a successful tool or prose reply is not evidence.'
        )
    permitted = INSPECTION_TOOLS if kind == 'inspection' else ACTION_TOOLS
    if observed.tool not in permitted:
        raise ToolError(
            f'{observed.tool} cannot complete an {kind} step. Use evidence matching '
            'the declared kind. An inspection only establishes that an inspection '
            'ran; neither an inspection nor an action proves a verified outcome.'
        )
    return {
        'evidence_kind': 'command' if observed.tool == 'run_shell' else kind,
        'evidence_tool': observed.tool,
    }


@tool(
    name='set_plan',
    description=('Track a complex task in 2-6 short steps. Use pending, in_progress, done. '
                 'Declare each step kind: inspection, action, or verification. A done step '
                 'must cite a successful same-task computer-tool step with matching evidence '
                 'scope. Diagnostics can complete inspection only; an action or shell '
                 'command completing does not prove a repair. Use request_retest for '
                 'verification: only an explicit Passed submission completes the bound '
                 'target, labelled user-reported. Keep its exact description and position '
                 'when updating the plan; other user replies are not completion evidence. '
                 'Planning only updates this conversation; it never authorizes execution.'),
    parameters={
        'type': 'object',
        'properties': {'steps': {'type': 'array', 'minItems': 2, 'maxItems': 6,
            'items': {'type': 'object', 'properties': {
                'description': {'type': 'string'},
                'status': {'type': 'string', 'enum': ['pending', 'in_progress', 'done']},
                'kind': {'type': 'string', 'enum': list(PLAN_KINDS)},
                'evidence_step_id': {'type': 'integer'},
            }, 'required': ['description', 'status', 'kind']}}},
        'required': ['steps'],
    },
)
def set_plan(steps: list[dict], ctx: Context = None):
    if not ctx or not ctx.db:
        raise ToolError('The task database is unavailable.')
    if not isinstance(steps, list) or not 2 <= len(steps) <= 6:
        raise ToolError('Provide two to six plan steps.')
    evidence = {s.id: s for s in ctx.db.get_steps(ctx.task_id)}
    previous = ctx.db.get_plan(ctx.task_id)
    normalized = []
    for index, item in enumerate(steps):
        if (not isinstance(item, dict) or not isinstance(item.get('description'), str)
                or not item['description'].strip() or len(item['description']) > 240
                or item.get('status') not in ('pending', 'in_progress', 'done')):
            raise ToolError('Every step needs a short description and a valid status.')
        step_id = item.get('evidence_step_id', 0)
        if not isinstance(step_id, int) or isinstance(step_id, bool):
            raise ToolError('evidence_step_id must be an integer.')
        kind = item.get('kind')
        if kind is not None and kind not in PLAN_KINDS:
            raise ToolError('Step kind must be inspection, action, or verification.')
        row = dict(description=item['description'].strip(), status=item['status'],
                   evidence_step_id=step_id)
        # Old unfinished plans remain editable without inventing their scope.
        # Completion always requires an explicit kind, including for old rows.
        if kind is not None:
            row['kind'] = kind
        if kind == 'verification':
            old = previous[index] if index < len(previous) else {}
            unchanged = (old.get('kind') == kind and old.get('description') == row['description']
                         and not (old.get('status') == 'done' and row['status'] != 'done'))
            row['verification_target_id'] = (
                old.get('verification_target_id') if unchanged else None) or uuid.uuid4().hex
        if item['status'] == 'done':
            observed = evidence.get(step_id)
            if kind == 'verification' and observed:
                from peppermint.daemon.tools.retest import completion_evidence

                row.update(completion_evidence(ctx.db, ctx.task_id, observed, row, index))
            elif not observed or observed.status != 'ok' or observed.tool in INTERNAL_TOOLS:
                raise ToolError('A done step must cite a successful computer-tool step in this task.')
            else:
                row.update(_completion_evidence(kind, observed))
        normalized.append(row)
    ctx.db.set_plan(ctx.task_id, normalized)
    return 'Plan saved. Continue with the next unfinished step. Planning did not execute anything.'
