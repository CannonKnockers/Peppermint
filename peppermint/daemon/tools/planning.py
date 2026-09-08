"""App-only task tracking; this tool never executes a computer action."""
from peppermint.daemon.tools.registry import Context, ToolError, tool


@tool(
    name='set_plan',
    description=('Track a complex task in 2-6 short steps. Use pending, in_progress, done. '
                 'A done step must cite the ID of a successful computer-tool step as evidence. '
                 'Planning only updates this conversation; it never authorizes execution.'),
    parameters={
        'type': 'object',
        'properties': {'steps': {'type': 'array', 'minItems': 2, 'maxItems': 6,
            'items': {'type': 'object', 'properties': {
                'description': {'type': 'string'},
                'status': {'type': 'string', 'enum': ['pending', 'in_progress', 'done']},
                'evidence_step_id': {'type': 'integer'},
            }, 'required': ['description', 'status']}}},
        'required': ['steps'],
    },
)
def set_plan(steps: list[dict], ctx: Context = None):
    if not ctx or not ctx.db:
        raise ToolError('The task database is unavailable.')
    if not isinstance(steps, list) or not 2 <= len(steps) <= 6:
        raise ToolError('Provide two to six plan steps.')
    evidence = {s.id: s for s in ctx.db.get_steps(ctx.task_id)}
    normalized = []
    for item in steps:
        if (not isinstance(item, dict) or not isinstance(item.get('description'), str)
                or not item['description'].strip() or len(item['description']) > 240
                or item.get('status') not in ('pending', 'in_progress', 'done')):
            raise ToolError('Every step needs a short description and a valid status.')
        step_id = item.get('evidence_step_id', 0)
        if not isinstance(step_id, int) or isinstance(step_id, bool):
            raise ToolError('evidence_step_id must be an integer.')
        if item['status'] == 'done':
            observed = evidence.get(step_id)
            if not observed or observed.status != 'ok' or observed.tool in ('set_plan', 'ask_user'):
                raise ToolError('A done step must cite a successful computer-tool step in this task.')
        normalized.append(dict(description=item['description'].strip(), status=item['status'],
                               evidence_step_id=step_id))
    ctx.db.set_plan(ctx.task_id, normalized)
    return 'Plan saved. Continue with the next unfinished step. Planning did not execute anything.'
