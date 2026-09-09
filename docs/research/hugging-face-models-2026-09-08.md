# Hugging Face models for Peppermint

Reviewed September 8, 2026. Hugging Face can provide model documentation and
compatible weights for Peppermint's existing local Ollama backend. The next
useful step is a controlled comparison of the two models already installed.
Creating an account does not connect Peppermint to it or train Peppermint.

## Access and compatibility

Public model cards and documentation were readable during this review without
logging in. Public repositories and private repositories have different access
rules: private repositories are hidden from unauthorized users. Gated model
files require an access request under the user's account, sometimes manual
approval, and authenticated downloading. Creating an account alone does not
grant gated access. See Hugging Face's [repository visibility](https://huggingface.co/docs/hub/en/repositories-settings)
and [gated-model documentation](https://huggingface.co/docs/hub/en/models-gated).

If private or gated weights become necessary, configure the appropriate local
authentication separately; a narrowly scoped read token is sufficient for
authorized downloads. Do not put a token in chat or project files. Hugging Face
also documents adding Ollama's public SSH key to an account for private GGUF
access. No authentication, account changes or model downloads were performed
for this research. See [access tokens](https://huggingface.co/docs/hub/en/security-tokens)
and [Hugging Face's Ollama integration](https://huggingface.co/docs/hub/en/ollama).

For a supported architecture, Ollama can use a Hub GGUF repository through
`hf.co/<publisher>/<repository>:<quantization>` or import a local GGUF through
a Modelfile. A Transformers/Safetensors repository is not automatically an
interchangeable GGUF; check architecture support and the available conversion
or import path. Qwen publishes an [official Qwen3-8B GGUF repository](https://huggingface.co/Qwen/Qwen3-8B-GGUF);
community conversions should retain a clear upstream model and revision.
See [Hugging Face's Ollama integration](https://huggingface.co/docs/hub/en/ollama)
and [Ollama model import](https://docs.ollama.com/import).

Loading weights is only the first compatibility check. Peppermint needs the
correct chat template, structured tool-call parsing, tool results in message
history, and compatible thinking controls. Hugging Face describes template
selection from GGUF metadata, and Ollama documents the tool-call/result loop.
Test a complete call and follow-up answer, not just a greeting. See
[GGUF templates](https://huggingface.co/docs/hub/en/ollama),
[Ollama tool calling](https://docs.ollama.com/capabilities/tool-calling) and
[Hugging Face tool use](https://huggingface.co/docs/transformers/en/chat_extras).

## This machine and a small comparison

Read-only local checks found an **NVIDIA GeForce RTX 3060 Ti with 8,192 MiB
VRAM**, Ollama **0.32.7**, and the following installed models through
`GET /api/tags`. These are local observations, not vendor memory estimates.
No model was loaded at the `GET /api/ps` snapshot. The checked source default in
[config.py](../../peppermint/config.py) is `qwen3:8b`, 16,384 context tokens,
1,024 output tokens and thinking disabled; environment overrides can differ.

| Candidate | Evidence and local footprint | Evaluation role |
| --- | --- | --- |
| `qwen3:8b` | Installed Q4_K_M, 5.23 GB stored; digest starts `500a1f067a9f`. Its [official card](https://huggingface.co/Qwen/Qwen3-8B) describes text generation, tool use and thinking controls. | Current baseline. Measure troubleshooting decisions and regression behavior. |
| `qwen3.5:4b` | Installed Q4_K_M, 3.39 GB stored; digest starts `2a654d98e6fb`. Its [official card](https://huggingface.co/Qwen/Qwen3.5-4B) documents image/text input and agentic use; local metadata advertises vision and tools. | First challenger. Its smaller stored weights suggest more memory room for context, but correctness and latency require measurement. |
| `qwen3.5:9b` | Not installed. The [Ollama tag](https://ollama.com/library/qwen3.5:9b) lists Q4_K_M at 6.6 GB; the [official card](https://huggingface.co/Qwen/Qwen3.5-9B) describes a language model with a vision encoder. | Optional later comparison if the first pair misses important cases; tight GPU margin makes this conditional on measured memory and offloading. |

Stored weight size is not peak VRAM usage. Context, runtime workspaces, image
processing and the desktop also need memory. Ollama states that increasing
context increases memory requirements and recommends checking CPU/GPU
offloading. A model's advertised maximum context is therefore not a promise
that it fits this GPU. See [Ollama context length](https://docs.ollama.com/context-length).

The Qwen3.5 Ollama pages currently include benchmark tables labeled
**Qwen3.5-397B-A17B**. Those scores do not measure the 4B or 9B candidates.
Use the exact model card and our own quantized local evaluation; do not infer
a small model's Linux troubleshooting or screen-control reliability from
the larger model's scores. See the [9B download page's table headings](https://ollama.com/library/qwen3.5:9b).

## Recommended next evaluation

1. Compare the installed 8B and 4B models using the same captured prompt, tool
   schemas and fixtures. The existing [evaluation script](../../scripts/evaluate_model.py)
   supports `support`, `clarity-holdout` and regression suites and records proposed
   calls without executing them. Keep model digests, Ollama version and options
   with results so later downloads do not silently change the comparison.
2. Start with the current application settings, then separately test any
   model-specific settings. Qwen3.5's card uses API/template settings to disable
   thinking and does not support Qwen3's prompt-text soft switch. Check the actual
   backend behavior and output budget. See [Qwen3.5 thinking controls](https://huggingface.co/Qwen/Qwen3.5-4B#instruct-or-non-thinking-mode).
3. Grade vague game identification, Steam/Proton evidence, SMB authentication,
   NTFS hibernation, Flatpak permissions, Windows paths and missing information.
   Require source-backed explanations, valid tool arguments, preservation of
   saves/credentials, and honest distinctions between diagnosis and verified repair.
4. Repeat trials and add held-out multi-step tool-result fixtures. Record
   response latency, malformed calls, false success claims, repeated failing
   actions and peak GPU/RAM use. One selected-tool test does not establish
   successful end-to-end repairs. Test interruption with independent backend tests.
5. Keep the current default until a challenger improves the relevant workload
   without material regressions or unacceptable resource use. Try the 9B model
   only if its extra resource cost is justified by a specific unresolved gap.

## How this supports the next capabilities

These are proposed engineering steps, not capabilities supplied by a download:

- **Linux and cross-OS troubleshooting:** combine model reasoning with the
  [reviewed reference layer](linux-reference-sources-2026-09-08.md), fresh targeted
  diagnostics, and verified outcomes. Retrieved documentation supplies context;
  it does not update model weights.
- **Diagnostic charts:** collect timestamped measurements in tools and render
  charts from those values. Have the model explain measured trends rather than
  invent values from a symptom description.
- **Screens and applications:** first evaluate explicit screenshot input and
  visual understanding. Vision models require images to be supplied through
  the API; screen capture, accessible UI targeting, input actions and stop
  controls are separate integrations. See [Ollama vision input](https://docs.ollama.com/capabilities/vision).
- **Cancellation:** the backend must own and stop the relevant tasks/processes
  reliably, regardless of model cooperation. Canceling generation does not
  undo an action already completed or automatically stop every launched program.

A model requests tool calls; the application implements and handles them, as
Hugging Face's [tool-use documentation](https://huggingface.co/docs/transformers/en/chat_extras)
explains. Future fine-tuning would be a separate project using reviewed examples,
training and held-out evaluation; ordinary conversations and a Hugging Face
account do not perform that training. See [Hugging Face fine-tuning](https://huggingface.co/docs/transformers/en/training).
