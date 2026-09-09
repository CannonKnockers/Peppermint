# Hugging Face candidate and protocol checkpoint — 2026-09-08

Hugging Face CLI authentication succeeded locally. A pinned Qwen3.5-9B candidate
was downloaded and evaluated, but it still failed required clarification steps.
No model or configuration was promoted. The existing Qwen3:8b daemon remains
running, and its model was restored to memory after evaluation. Work stopped
here at the user's requested checkpoint.

## Candidate and scope

The installed HF CLI is 1.30.0. The public, nongated candidate is
`unsloth/Qwen3.5-9B-GGUF`, revision
`3885219b6810b007914f3a7950a8d1b469d598a5`, file `Qwen3.5-9B-Q4_K_M.gguf`.
Its 5,680,522,464 downloaded bytes matched SHA-256
`03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8`.
The [provenance research](../research/hf-next-candidate-2026-09-08.md) records
license metadata and the limitation on tracing the quantizer's exact base revision.

Ollama 0.32.7 imported it as `peppermint-qwen35-9b-eval:latest`, using the native
`qwen3.5` renderer/parser and `TEMPLATE {{ .Prompt }}`. Capabilities reported
completion, tools, and thinking. No vision projector was installed. The download
remains in the HF cache and Ollama keeps an imported copy. No cloud job, account
write, private repository access, or training was involved.

On the RTX 3060 Ti, Ollama reported all model residency in GPU memory:
5,578,225,090 bytes at 16,384 context and 5,762,774,466 bytes at 24,576 context.
These are residency snapshots, not peak memory or concurrent gaming measurements.

## Application fixes

- Native tool results now translate legacy `name` to `tool_name` without mutating
  history. Python Ollama 0.6.2 previously discarded the legacy field during
  serialization. Both synchronous and cancellable requests use the correction.
- Empty-response recovery now reaches the model. Context preparation previously
  removed the internal recovery message; it now keeps explicitly marked recovery
  with its original user turn and strips internal markers before transport.
- Native thinking is preserved separately for protocol continuation, while
  exported evaluation replies omit its text. Per-call metadata records stop
  reasons, timing, token counts and output lengths without message contents.
- The single-turn evaluator uses production context preparation and freezes tool
  schemas per run. Both evaluators flag incomplete generation.

Protocol tests exercise the installed Ollama serializer with a mock HTTP
transport. Agent tests cover recovery, later-history filtering, and continuation.
Earlier model reports remain historical evidence: protocol and schema changes
mean their scores are not controlled comparisons of model weights alone.

## Plan evidence

Plan rows declare inspection, action, or verification. Completed rows require a
successful computer-tool step from the same task and an allowed evidence category.
References, questions and internal planning cannot establish completion. Evidence
labels are derived by the application; commands receive an explicit command label.
The [UI preview](peppermint-plan-evidence.png) shows an inspection completion.

This validates evidence categories, not arbitrary prose describing a step. A
model could still call a repair an inspection. No current tool establishes
outcome verification, and user retest replies do not yet create a structured
attestation. Verification therefore stays pending. Legacy rows display an
explicit indication that evidence scope is unavailable; no database migration
or saved-task rewrite was performed.

## Recorded results

These are strict, narrow fixture scores from one pass, not general intelligence
ratings. Default runs used temperature 0.2, 16,384 context, 1,024 output tokens,
and thinking disabled. Guarded workflows used the real agent, an in-memory task
database and simulated diagnostics; other computer actions were blocked.

| Suite | Qwen3:8b | Qwen3.5-9B Q4_K_M |
| --- | ---: | ---: |
| Regression | 17/18 | 16/18 |
| Support | 11/12 | 7/12 |
| Support reasoning | 6/8 | 7/8 |
| Clarification | Not rerun | 2/4 |
| General holdout | Not rerun | 5/6 |
| Clarification holdout | Not rerun | 3/6 |
| Guarded workflow protocol | 3/3 | 2/3 |

9B thinking, tested only on support with 24,576 context and 4,096 output tokens,
scored **8/12**. Four questions still appeared as completed text instead of
`ask_user` transitions, so further thinking suites were not run. Every recorded
generation stopped normally; these failures were not output-budget exhaustion.

Manual review found phrase-grader false negatives: both models correctly said
installing a utility did not establish game repair; 9B also correctly qualified
the disk-capacity example. Recorded scores retain those failures transparently.

Other failures were substantive. 8B still followed the fake-system instruction
inside synthetic tool data and ignored stale-log age. 9B resisted that injection
but invented a missing-theme explanation for a user denial, fabricated an
equivalent Linux location for a Windows path, and inspected cleanup targets before
clarifying scope. Its vague-game workflow stopped at a plain question. Its known
game answer overstated an uncorrelated Vulkan log as the immediate cause and
suggested driver changes. A passing workflow protocol score does not certify the
diagnosis. No actual game was inspected, repaired or retested.

## Validation and resume

**1,012 tests passed in 4.24 seconds**; source compilation and whitespace checks
passed. Backend fixes and the new plan captions remain in source, pending reload.
The live daemon was healthy with an empty queue and 35 saved tasks; task 14 still
awaited confirmation. No live conversation or pending action was used for testing.

The [raw artifact](hf-protocol-2026-09-08.json) contains all 12 runs, exact prompts,
schemas, evaluator sources, metadata, manual-review notes and final runtime state.
Resume with an explicit clarification protocol and structured outcome/retest
evidence, then rerun held-out workflows before selecting a model or activating
the pending backend. See the [project checkpoint](../PROJECT_STATUS.md).

To reproduce the candidate support checks without executing proposed actions:

```bash
.venv/bin/python scripts/evaluate_model.py --model peppermint-qwen35-9b-eval:latest --suite support --output /tmp/9b-support.json
PEPPERMINT_THINK=1 PEPPERMINT_NUM_CTX=24576 PEPPERMINT_MAX_RESPONSE_TOKENS=4096 .venv/bin/python scripts/evaluate_model.py --model peppermint-qwen35-9b-eval:latest --suite support --output /tmp/9b-thinking-support.json
.venv/bin/python scripts/evaluate_support_workflow.py --model peppermint-qwen35-9b-eval:latest --output /tmp/9b-workflow.json
```
