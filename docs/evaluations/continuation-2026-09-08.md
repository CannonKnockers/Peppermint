# Window controls and troubleshooting continuation — 2026-09-08

Compact window controls are implemented and loaded in the desktop app.
Approval fingerprints and the graphics diagnostic contract are improved in
source. The new backend remains pending; model comparisons still show failures
and no model/default changes were made. This continues the
[previous support checkpoint](linux-support-2026-09-08.md).

## Completed changes

Native GTK minimize, maximize and close controls are contiguous, with 43×60 px
hit areas in this desktop configuration. The header has no top/right inset
around those controls. In an isolated maximized window, moving the pointer by
`(+10000, -10000)` clamped to the active desktop's top-right coordinate
`(1833, 0)`; clicking hid the window and preserved its unsent draft and guide.
Minimize and maximize/restore also passed. The nominal 1920×1080 framebuffer has
an NVIDIA underscan viewport of 1834×1031; the bottom panel removes another
40 px. Direct pointer testing confirms there is no practical corner gap and
no display setting needed changing. See the [preview](peppermint-window-controls.png).

The live UI was reloaded after checking that its editable fields were empty.
All eight saved task-table hashes matched before/after reload. The daemon was
not restarted; the final health check was healthy with an empty queue and
35 saved tasks.

Filesystem approval fingerprints now include nanosecond modification/change
times and device identity. Tests reproduce and reject same-size rewrites within
one second and changes with a restored modification time. A format version
requires a fresh approval for older filesystem-target records; target-free
approvals retain normal token/expiry behavior. No database rows are rewritten.
Precision depends on the filesystem; these are metadata checks, not content
hashing or file locking.

Steam graphics output now uses `vulkan_probe`, identifies the diagnostic command
and its availability, and reports `command_missing` when it is absent from fixed
system paths. Game runtime health and game Vulkan support remain `unverified`
for missing, successful, failed, truncated and timed-out host probes. The guarded
workflow fixture matches this contract. Historical artifacts retain their exact
older shape for reproducibility.

Final automated verification: **949 tests passed in 3.36 seconds**, including
the existing GTK checks. Source compilation and `git diff --check` passed.

## Model comparison

Eight new synthetic reasoning cases were written before evaluation, with the
criteria retained in the raw report. They exercise missing probes, stale logs,
host versus Flatpak runtime, installation versus repair, SMB uncertainty,
Windows hibernation, malicious application logs and task/application cancellation.
They are targeted development checks, not a broad capability benchmark.

| Model and settings | Check | Strict score |
| --- | --- | --- |
| Qwen3:8b, thinking off, 1,024 output tokens | New reasoning | 6/8 |
| Qwen3.5:4b, same settings | New reasoning | 7/8 |
| Qwen3.5:4b, thinking on, 4,096 output tokens | Support | 11/12 |
| Same thinking configuration | New reasoning / existing regression | 5/8, 16/18 |
| Qwen3:8b, defaults, clarified diagnostic fixture | Guarded workflows | 3/3 protocol checks |

All runs used temperature 0.2 and a 16,384-token context. Evaluations ran
sequentially while the live daemon was idle. Direct checks execute no proposed
tools. Guarded workflows use a transient database and synthetic Steam evidence;
only internal tools and the fixture diagnostic can run. No game was inspected,
launched or fixed. Qwen3:8b was returned to the model cache for the live app.

Manual review matters more than the headline counts:

- The 8B model missed the age of an old log and offered driver changes without
  establishing its connection to today's failure. Its other strict failure
  correctly said game status had not been checked; that was a phrase-checker
  false negative, with an unnecessary follow-up question.
- The 4B default run also acknowledged that a fix was unverified in its sole
  strict failure. It nevertheless suggested further diagnostics as a way to
  determine whether the game now works, and asked unnecessary details.
- The thinking configuration produced empty final answers on several supplied
  tool-result cases, requested an inventory when asked only for an answer,
  supplied the wrong Downloads path, and said it had not received a game log
  that was present. More reasoning/output allowance did not improve this run.
- With the clearer diagnostic fields, the 8B known-game workflow explicitly
  recognized that missing `vulkaninfo` does not establish a broken GPU/runtime.
  That observed improvement is limited to this fixture. It still suggested
  broad driver/configuration checks without matching the log to the failing
  run or retrieving guidance, then ended a completed task with an ordinary
  question. The SMB response retrieved sources but added claims not established
  by those notes. A 3/3 protocol score does not certify grounded diagnosis.

Scores were not regraded to hide wording mismatches. No candidate was promoted.
The earlier tool-result injection failure and semantic plan-evidence limitation
also remain activation blockers. The whole-second approval gap is now fixed in
source; the new metadata checks still need a backend reload to become live.

## Next work and reproduction

The [roadmap](../ROADMAP.md) records diagnostic graphs, a process view,
application cancellation and administrative screen control as staged work.
[Hugging Face research](../research/hugging-face-models-2026-09-08.md) records
public/gated access, local compatibility, exact installed candidates and their
hardware implications. No Hugging Face authentication, model download or
default change occurred in this continuation.

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/evaluate_model.py --model qwen3:8b --suite support-reasoning --output /tmp/8b.json
.venv/bin/python scripts/evaluate_model.py --model qwen3.5:4b --suite support-reasoning --output /tmp/4b.json
PEPPERMINT_THINK=1 PEPPERMINT_MAX_RESPONSE_TOKENS=4096 .venv/bin/python scripts/evaluate_model.py --model qwen3.5:4b --suite regression --output /tmp/thinking.json
.venv/bin/python scripts/evaluate_support_workflow.py --model qwen3:8b --output /tmp/workflow.json
```

[Raw evidence](continuation-2026-09-08.json) preserves all six runs, prompts,
replies, settings, model digests, tool schemas, workflow fixture source and
window-control measurements. Current status and the next bounded work are in
[PROJECT_STATUS.md](../PROJECT_STATUS.md).
