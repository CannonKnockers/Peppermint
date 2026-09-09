# Clarification routing follow-up — 2026-09-08

The [earlier clarity evaluation](clarity-2026-09-08.md) found that vague cleanup
questions could finish as ordinary text or include too many choices. This
follow-up improved those observed cases with a small system-prompt and tool-schema
description change. The agent loop is unchanged: an ordinary final answer still
finishes immediately.

## Selected change

- Ask an open scope question through `ask_user` before selecting unspecified
  cleanup targets, including when the user delegates an undefined decision.
- Explain consistently in the prompt and tool description that `ask_user` waits
  for an answer, while ordinary text completes the task.
- Omit optional choices for open questions; retain two or three structured
  choices when the user requests them. Remove the competing instruction to use
  numbered text options.
- Check nonempty questions and choice strings in the evaluator, in addition to
  the existing option-count requirement.

No text-question detector or extra model call was added. The existing approval
gate remains the boundary for computer actions.

## Recorded checks

The live daemon was healthy, with an empty queue and no queued, planning, or
running tasks before evaluation. All calls used installed `qwen3:8b` with normal
application settings (`think=false`, temperature 0.2). Candidates were evaluated
sequentially; only three alternatives were tried in this follow-up.

| Check | Strict result |
| --- | --- |
| Current source before this follow-up, four clarity cases | 2/4 |
| Candidate 1: explain text completion and optional choice counts, two repeats | 4/8 |
| Candidate 2: explain routing and show an unrelated open-question example, two repeats | 4/8 |
| Candidate 3: align scope instructions and tool description, two repeats | 7/8 |
| Selected candidate, clarity suite repeated three more times | 12/12 |
| Existing regression suite, selected candidate | 18/18 |
| Existing holdout suite, selected candidate | 6/6 |
| New clarification holdout, selected candidate | 5/6 |
| Isolated real-agent runs, four cases repeated three times | 12/12 |

In the final clarity runs, both original cleanup requests called `ask_user` in
all three repeats, with nonempty questions and no options. All six hardware-limit
answers remained correct. The regression suite's explicit choice request still
provided valid structured choices.

The isolated real-agent runs used an in-memory database and a guard that blocked
every tool except `ask_user`. Both cleanup prompts reached `awaiting-input` in
one model call in all three repeats. Ordinary greetings and hardware-limit
answers reached `done` in one call in all three repeats. No proposed computer
action was executed or approved, and no live conversation was added or changed.

## Limits and remaining failures

Candidate 3's first development response to the original cleanup prompt still
asked in text. The later repeated passes do not establish reliable behavior;
outputs may be strongly correlated and these development prompts were already
known. The existing holdout was also used previously.

The six new holdout prompts were written before their evaluation, and no tuning
followed their results. Both new vague cleanup requests clarified correctly,
and both requests to delete an exact file proposed only that file's deletion.
The missing-destination request instead proposed listing the home directory;
it did not ask where the invoice should go. Listing would still need approval.

Manual review found another weakness: the explanation-only cleanup response
passed its text-only routing check but included unnecessary questions and a
follow-up request. The strict scores therefore measure narrow routing and
argument checks, not complete conversational quality or broad capability.

## Reproduction

```bash
.venv/bin/python scripts/evaluate_model.py --suite clarity --repeats 3 --output /tmp/clarity.json
.venv/bin/python scripts/evaluate_model.py --suite regression --output /tmp/regression.json
.venv/bin/python scripts/evaluate_model.py --suite holdout --output /tmp/holdout.json
.venv/bin/python scripts/evaluate_model.py --suite clarity-holdout --output /tmp/clarity-holdout.json
```

[Raw prompts, replies, model metadata, and agent checks](clarification-routing-2026-09-08.json)
preserve the baseline, all three candidates, and every validation run. Shared
tool schemas are stored once by hash; each run references its exact schema set.
The guarded agent-check script is included in the agent run record.
