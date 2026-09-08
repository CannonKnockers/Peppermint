# Video + AI workload and application upgrade

The reference request is now supported by an explicit, permission-gated diagnostic
workflow: ask for solutions for two video windows plus one or two AI prompts,
covering local and browser-hosted AI. The model selects `performance_snapshot`.
After approval, Peppermint samples the system and constructs the options report
from the observed metrics. The application preserves numbers, unknown values,
tradeoffs and verification instructions rather than relying on the small model
to rewrite those parts. The task then finishes without another generation.

This verifies the **advice workflow**, not smooth simultaneous playback. No
YouTube/Netflix/X playback test, login, browser configuration change, model
replacement or hardware purchase was performed. General error-free complex
problem solving is not established.

## What changed

- Sampled CPU use, available memory, swap I/O rates and Linux pressure stalls;
  optional NVIDIA memory/decoder metrics, loaded Ollama models, free storage,
  and top process memory use. Unavailable probes remain unknown.
- Three approaches are distinguished: queue local AI to prioritize video;
  test two genuinely overlapping local requests; or use browser-hosted AI for
  one or both prompts. Capacity advice depends on measured headroom. Server
  parallelism alone does not change Peppermint's single-worker task queue.
- Complex work can use a persistent two-to-six-step plan. A completed step must
  reference a successful tool-step ID from the same task. This checks recorded
  evidence existence, not semantic proof that the referenced result satisfies
  every requirement. An unfinished plan cannot receive the Completed badge;
  the task pauses for an explicit next instruction instead of automatically looping.
- Stop cancels the model request and releases its HTTP client. Cancelled tasks
  cannot be revived by a late response or a stale approval. A quick follow-up
  cannot make the previous cancelled generation overwrite the new turn.
- Approval buttons identify the exact displayed confirmation. Queued duplicate
  decisions cannot approve a subsequent action.
- Only one proposed tool call is retained from a model batch; further actions
  must be proposed after the first outcome. This prevents orphan tool calls at
  permission pauses; it does not silently authorize a batch.
- Model-call budgets survive permission pauses; a genuine follow-up resets its
  budget and can repeat a successful inspection. Changing a shell purpose string
  does not bypass the repeated-command guard.
- A failed Bash exit is recorded as an error with stdout/stderr preserved.
  Timeouts terminate the shell process group and report possible partial changes.
  Stop does not undo completed side effects or instantly terminate an already
  approved non-model tool; those tools retain their own bounded timeouts.
- The GTK window now uses charcoal greys and mint accents, rounded buttons,
  consistent conversation cards, visible plan progress, collapsible activity,
  explicit approval controls and a Stop button. Follow-up drafts survive refresh.

## Verification

- 849 automated tests passed, including GTK rendering, draft preservation,
  cancellation races, stale approval IDs, per-turn budgets, shell failure and
  timeout behavior, metric parsing and the full permission/report workflow.
- The real local Qwen3 8B completed the reference advice workflow with a successful
  performance snapshot and a final options report. See `video-ai-workflow.json`.
- A real model request stopped in approximately 0.42 seconds in a local smoke test.
  This is one observation, not a general cancellation latency guarantee.
- The broader small model regression suite passed 34/36 checks. Its two remaining
  failures concern describing pending execution in synthetic model prose. The
  actual pending-action state is managed by the application and pauses generation.
  The memory-diagnosis grader now accepts the new dedicated performance tool as
  well as `system_info`; this is not a directly identical score to earlier runs.
- GTK CSS loaded successfully and a fixture window was rendered and inspected.
  `peppermint-ui.png` is a visual preview with illustrative conversation content.

## Reproduce

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python scripts/evaluate_workload.py --approve-readonly-snapshot --output /tmp/video-ai-report.json
```

The second command explicitly permits only the read-only snapshot, uses a
transient database, and does not approve other computer tools. It does not start
videos or modify settings.

## Engineering references

- [Linux /proc memory metrics](https://www.kernel.org/doc/html/latest/filesystems/proc.html)
  and [pressure stall information](https://docs.kernel.org/accounting/psi.html)
  explain the distinction between available memory, stored swap pages and active
  contention. Full swap alone does not establish current thrashing.
- [Ollama concurrency and memory guidance](https://docs.ollama.com/faq) and
  [context allocation](https://docs.ollama.com/context-length) explain why parallel
  requests and longer contexts require additional memory. A smaller chat context
  is not automatically sufficient for Peppermint's full set of tool definitions.
- [NVIDIA monitoring](https://docs.nvidia.com/deploy/nvidia-smi/index.html) provides
  device metrics; aggregate decoder use does not identify each browser stream.
- [Chromium video acceleration diagnostics](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/gpu/vaapi.md)
  describes checking actual decoder use. Browser/driver/codec combinations need
  verification before prescribing flags or claiming accelerated playback.
