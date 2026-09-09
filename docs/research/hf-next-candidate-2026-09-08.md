# Next Hugging Face candidate: Qwen3.5-9B Q4_K_M

Reviewed 2026-09-08. This research pass read public model metadata and cards,
inspected the existing local Ollama model metadata, and ran a download dry run.
It did not download weights, run inference, change the active model, publish data,
or create a paid Hugging Face resource. Benchmark results belong in a separate
evaluation report.

## Recommendation and provenance

Evaluate the ordinary Q4_K_M quantization of the post-trained Qwen3.5-9B from
Unsloth. This keeps the model family comparable to the previously tested 4B
candidate while testing a larger model. Better Peppermint behavior remains a
hypothesis until measured with the same routing, grounding, injection, and guarded
workflow checks.

| Field | Pinned value |
| --- | --- |
| Publisher/base model | `Qwen/Qwen3.5-9B` |
| Base repository revision observed | `c202236235762e1c871ad0ccb60c8ee5ba337b9a` |
| Quantization repository | `unsloth/Qwen3.5-9B-GGUF` |
| Quantization repository revision | `3885219b6810b007914f3a7950a8d1b469d598a5` |
| File | `Qwen3.5-9B-Q4_K_M.gguf` |
| Size | `5680522464` bytes / 5.6805 GB / 5.2904 GiB |
| LFS SHA-256 | `03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8` |
| License metadata | Apache-2.0 |
| Access | Public, not gated |

The quantizer declares `Qwen/Qwen3.5-9B` as its base model; its metadata does not
pin the exact original base revision used during quantization. The base revision
above records what was visible during this review, rather than asserting a
reproducible conversion lineage. Provenance and checksum come from the [pinned
quantization repository](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/tree/3885219b6810b007914f3a7950a8d1b469d598a5).

The original publisher documents tool calling, a supplied chat template, and
thinking/non-thinking operation. These are capability claims and interface
definitions, not evidence that this quant passes Peppermint's tests. The large
family benchmark table should not be used as a score for this particular 9B Q4
file. See the [publisher's model card](https://huggingface.co/Qwen/Qwen3.5-9B/blob/c202236235762e1c871ad0ccb60c8ee5ba337b9a/README.md).

One alternative was inspected: `bartowski/Qwen_Qwen3.5-9B-GGUF`, revision
`182be2fd6c7bc44887d88a91cb03ff009cc9f549`, file
`Qwen_Qwen3.5-9B-Q4_K_M.gguf`, 6,169,341,984 bytes, SHA-256
`d784ce9eda1a5a7b51e8f705a9e6310844bf4f173654d115823c775fdea56d43`.
Its current card records llama.cpp b9222 quantization with added MTP layers. It
adds roughly 489 MB of weights relative to the selected file. This pass selected
the smaller existing quant to preserve headroom; it did not establish a quality
difference. See the [alternative's pinned card](https://huggingface.co/bartowski/Qwen_Qwen3.5-9B-GGUF/blob/182be2fd6c7bc44887d88a91cb03ff009cc9f549/README.md).

## Download and local import preparation

The installed `hf` CLI completed this command successfully:

```bash
hf download unsloth/Qwen3.5-9B-GGUF Qwen3.5-9B-Q4_K_M.gguf \
  --revision 3885219b6810b007914f3a7950a8d1b469d598a5 \
  --dry-run --format json
```

Output: `[{"file": "Qwen3.5-9B-Q4_K_M.gguf", "size": "5.7G"}]`.
An actual download should retain this revision and filename, then verify the
local SHA-256 before import. No projector is needed for the planned text/tool
benchmark. Vision and screen interaction require a separate compatible projector
and a separate evaluation.

An 8 GiB RTX 3060 Ti plausibly has room for these 5.29 GiB weights at a modest
context, but this is an estimate. Context state, runtime allocations, and the
desktop also consume GPU memory. Check measured allocation, GPU offload, and
latency under Peppermint's actual context limit; file size alone does not prove
full GPU residency.

[Ollama's import documentation](https://docs.ollama.com/import) supports a local
GGUF through `FROM /path/to/file.gguf` followed by `ollama create`. For this
specific model family, retain its native tool renderer and parser. The running
local Ollama reports version `0.32.7`; its existing `qwen3.5:4b` model's
`/api/show` response emitted these Modelfile directives:

```text
TEMPLATE {{ .Prompt }}
RENDERER qwen3.5
PARSER qwen3.5
```

Use those directives with the pinned local GGUF path and a distinct candidate
name. Do not copy the old Qwen3 JSON tool template. This is verified metadata from
the installed model, not an inference based on its name. The [create API also
accepts renderer and parser fields](https://docs.ollama.com/api/create).

The corresponding versioned implementations are
[Qwen3.5 renderer](https://github.com/ollama/ollama/blob/v0.32.7/model/renderers/qwen35.go)
and [Qwen3.5 parser](https://github.com/ollama/ollama/blob/v0.32.7/model/parsers/qwen35.go).
The architecture fallback in [v0.32.7 create.go](https://github.com/ollama/ollama/blob/v0.32.7/server/create.go)
does not contain `qwen35`, so automatic import selection should not be assumed.
After creation, inspect the resulting model metadata and verify a structured
tool call before running the larger suite.

Hugging Face also documents direct `ollama run hf.co/owner/repo:filename` and
automatic template selection from GGUF metadata. For reproducible evaluation,
the pinned CLI download plus local import makes the exact revision and renderer
reviewable. HF CLI authentication and Ollama access to private HF repositories
are separate mechanisms; this public candidate needs neither a private repository
nor a new account connection. See [HF's Ollama integration](https://huggingface.co/docs/hub/en/ollama).
