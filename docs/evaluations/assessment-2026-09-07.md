# Peppermint capability assessment — 2026-09-07

Peppermint is currently a useful assistant for short, concrete Linux tasks. It is
not yet dependable for broad requests such as diagnosing a slow computer or
organizing screenshots by their visual contents. The gap includes both the
model's ability and missing engineering around the model. A prompt change alone
cannot establish parity with a frontier coding assistant.

## What was evaluated

- Installed model: Qwen3 8B, 8.2B parameters, Q4_K_M quantization; Ollama 0.32.7.
- Hardware observed: RTX 3060 Ti, 8 GiB VRAM; approximately 16 GiB RAM. At inspection,
  only 1,063 MiB VRAM was free, available RAM was around 7 GiB, and the 2 GiB swap
  was almost full. Swap occupancy alone does not establish active memory pressure.
- Inspected the existing agent code and recent local conversation metadata. One
  conversation had 156 messages and about 53,000 serialized characters; another
  had 69 messages. Tool definitions add roughly 10,600 characters to every request.
- Ran 18 synthetic next-response cases twice with the installed model, before and
  after prompt changes. Cases cover tool selection, path corrections, numerical
  fidelity, conversation references, incomplete work, diagnosis, permission
  status, malicious instructions in file contents, and structured choices.
- Ran six additional cases twice after selecting the revised prompt. These new
  cases were not used to tune the prompt.
- No model-proposed tool was executed during the evaluation. Inputs and tool
  results were synthetic; private conversation contents were not uploaded.

## Results

| Configuration | Passed checks | Median response time |
| --- | ---: | ---: |
| Original instructions | 29 / 36 | 0.765 s |
| Rejected longer instructions | 28 / 36 | — |
| Selected shorter instructions | 34 / 36 | 0.775 s |
| Selected instructions, additional cases | 12 / 12 | See raw results |

These are small development checks, **not an intelligence score, a security
certification, or a comparison against Codex on the same benchmark**. Two repeats
at the same settings are not independent population samples. Timings reflect
short synthetic requests, a warm model, and possible prompt caching. They do
not measure long conversations, full workflows, cold starts or desktop load.
Graders check selected phrases, tool names and arguments; raw traces are retained
for review. The failing responses were inspected manually. Realistic multi-step
end-to-end model evaluations remain necessary.

### Observed strengths

Short folder and theme lookups, exact byte-size repetition, recent folder
references, user path corrections, and choosing the next file in a partially
completed move worked in these tests. The selected prompt also passed both tested
file-instruction attacks and recognized memory pressure from supplied measurements.
This does not mean arbitrary prompt injection is solved.

### Observed weaknesses

1. The original model attributed lag to a filesystem being 82% full, even when
   the synthetic evidence showed almost no available RAM and sustained swapping.
   The selected prompt corrected that result in the tested cases.
2. The original model obeyed a fake system instruction embedded inside file
   contents and proposed an unrelated desktop-setting change. The independent
   approval gate would still stop execution. The selected prompt resisted that
   specific attack and a new quoted-command case.
3. A longer attempt at improving the instructions caused regressions: the model
   rewrote an explicit `/tmp` path under the home directory and returned plain
   prose instead of structured choice buttons. That candidate was rejected.
4. Both selected-prompt runs still described a pending deletion as “initiated.”
   The synthetic fixture intentionally asks about a pending action. In the real
   agent, pending approval pauses execution before another model call; the
   deterministic approval UI remains authoritative. Status comprehension in
   unconstrained model prose is nevertheless unresolved.
5. Seeing images, reliably tracking all requirements across a long job, and
   preserving useful older context require capabilities beyond a larger prompt.

## Changes made now

- Added `scripts/evaluate_model.py`, reusable with another installed model and
  an optional prompt file. It refuses silent substitution of a fallback model.
- Replaced the instructions with the shorter version measured above. Clarified
  exact paths, latest corrections, evidence versus guesses, tool-result trust,
  choices, capability limits, and the distinction between permission and success.
- Removed conflicting tool descriptions implying automatic desktop/browser actions.
- Added `peppermint/daemon/context.py` to bound model input using a conservative
  character estimate and retain recent complete user turns. It never separates
  a retained tool call from its result or deletes the full transcript in SQLite.
  If even the newest turn is too large, it fails explicitly instead of silently
  cutting that turn. Older omitted information is not summarized or remembered;
  the model is instructed to ask when it needs missing details. This is a first
  safeguard, not semantic long-term memory or exact token accounting.
- All 808 automated tests pass, including new context-retention checks. The daemon
  is restarted only when idle so the updated behavior is actually loaded.

## Next implementation priorities

1. **Explicit task tracking and verified completion.** Persist the user's current
   objective, constraints, planned steps and each step's observed result. Render
   those states directly. Completion should be derived from verified steps, and
   declined actions should stay declined. Avoid another model-only “critic” loop
   that adds latency or restarts completed work. Acceptance: synthetic workflows
   with skipped files, failed writes and denied actions cannot report all done.
2. **Reliable diagnostic tools.** Add a bounded diagnostic snapshot for available
   memory, active paging, process CPU/memory and disk I/O; add a dedicated largest-
   files tool with explicit byte counts and deterministic sorting. This reduces
   shell construction errors and unit confusion. Acceptance: known fixture
   bottlenecks are distinguished, and the top N files are exact.
3. **Benchmark newer local models.** Test Qwen3.5 4B first as a lower-memory
   candidate, then Qwen3.5 9B if memory and latency permit. Ollama lists the 9B
   Q4_K_M package at 6.6 GB; weights are not the total runtime VRAM requirement.
   The 8 GB GPU also drives the desktop. Do not select a replacement on model
   marketing or parameter count alone. Compare the same tests plus full simulated
   workflows, cold start, VRAM use and long-context behavior. No new model was
   downloaded or made the default during this evaluation.
4. **Usable working memory and interruption.** Build a structured summary of
   decisions with provenance, retrieve relevant older turns, show when context
   is omitted, and support cancellation of an in-flight model request. Preserve
   raw history separately. Do not solve context limits by silently increasing
   VRAM usage or letting generation run indefinitely.
5. **Vision only when supported.** Visual screenshot categorization needs an
   image-capable model and a bounded image-reading tool. Filenames are not visual
   evidence. Add that only with separate permission and accuracy tests.

The project remains local and retains explicit approval for computer actions.
An improvement in local task reliability is realistic; equivalent general
reasoning and broad coding ability to a frontier assistant is not established
by these results and should not be promised.

## Reproduce

```bash
.venv/bin/python scripts/evaluate_model.py --repeats 2 --output /tmp/peppermint-eval.json
.venv/bin/python scripts/evaluate_model.py --suite holdout --repeats 2 --output /tmp/peppermint-holdout.json
# After independently installing a candidate model:
.venv/bin/python scripts/evaluate_model.py --model qwen3.5:4b --repeats 2 --output /tmp/peppermint-4b.json
.venv/bin/python -m pytest tests/ -q
```

Raw results in this folder preserve each run's prompt, settings, case inputs and
model responses. `qwen3-8b-rejected-long-prompt.json` records the rejected attempt.

## Sources consulted

The evaluation workflow follows the principle of inspecting traces and using
repeatable datasets to compare prompts and routing from [OpenAI's agent evaluation
guide](https://developers.openai.com/api/docs/guides/agent-evals). No OpenAI API
was used for the local tests.

Candidate model availability and package information: [Ollama Qwen3.5 4B](https://ollama.com/library/qwen3.5:4b),
[Ollama Qwen3.5 9B](https://ollama.com/library/qwen3.5:9b).
The publisher documents capabilities and model-specific inference guidance in the
[Qwen3.5 9B model card](https://huggingface.co/Qwen/Qwen3.5-9B).
Runtime context memory tradeoffs are documented in [Ollama's context-length guide](https://docs.ollama.com/context-length).
The recommended evaluation order is an engineering judgment for this machine,
not a claim that either candidate has already outperformed the installed model.
