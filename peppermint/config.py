"""All tunable parameters for Peppermint live here.

Environment variables with the PEPPERMINT_ prefix override the defaults, so you can
try a different model without editing code:

    PEPPERMINT_MODEL=qwen2.5:7b-instruct-q4_K_M peppermint-daemon

To save usage during experimentation, you can also use:

    PEPPERMINT_MODEL=spark
    PEPPERMINT_MODEL_SPARK=qwen2.5:1.5b-instruct
"""

from __future__ import annotations

import os
from pathlib import Path

HOME = Path.home()


def _env(name: str, default: str) -> str:
    return os.environ.get(f"PEPPERMINT_{name}", default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(f"PEPPERMINT_{name}", default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(f"PEPPERMINT_{name}", default))
    except ValueError:
        return default


def _resolve_model_alias(value: str, fallback: str) -> str:
    model = (value or "").strip()
    if not model:
        return fallback
    lowered = model.lower()
    if lowered == "spark":
        return _env("MODEL_SPARK", "qwen2.5:1.5b-instruct")
    if lowered == "astra":
        return _env("MODEL_ASTRA", "qwen3:8b")
    return model


# --- Model -----------------------------------------------------------------

MODEL = _resolve_model_alias(_env("MODEL", "qwen3:8b"), _env("MODEL_ASTRA", "qwen3:8b"))
FALLBACK_MODEL = _resolve_model_alias(
    _env("FALLBACK_MODEL", "qwen2.5:7b-instruct-q4_K_M"),
    _env("MODEL_SPARK", "qwen2.5:1.5b-instruct"),
)
OLLAMA_HOST = _env("OLLAMA_HOST", "http://127.0.0.1:11434")

NUM_CTX = _env_int("NUM_CTX", 16384)
TEMPERATURE = _env_float("TEMPERATURE", 0.2)
TOP_P = _env_float("TOP_P", 0.9)
REPEAT_PENALTY = _env_float("REPEAT_PENALTY", 1.05)
THINK = _env("THINK", "0") == "1"
MODEL_TIMEOUT_S = _env_int("MODEL_TIMEOUT_S", 60)
MAX_RESPONSE_TOKENS = _env_int("MAX_RESPONSE_TOKENS", 1024)
KEEP_ALIVE = _env("KEEP_ALIVE", "10m")

LLM_OPTIONS = {
    "num_ctx": NUM_CTX,
    "num_predict": MAX_RESPONSE_TOKENS,
    "temperature": TEMPERATURE,
    "top_p": TOP_P,
    "repeat_penalty": REPEAT_PENALTY,
}

# --- Agent loop ------------------------------------------------------------

# A file-sorting task uses one call per file, plus the checking calls.
MAX_ITERATIONS = _env_int("MAX_ITERATIONS", 30)
MAX_REPAIRS = _env_int("MAX_REPAIRS", 3)
# The model repeats a failing call instead of learning from it. In one real
# task it asked for the same install 29 times. Block the third attempt.
MAX_SAME_CALL = _env_int("MAX_SAME_CALL", 2)
TASK_TIMEOUT_S = _env_int("TASK_TIMEOUT_S", 600)
SHELL_TIMEOUT_S = _env_int("SHELL_TIMEOUT_S", 30)
MAX_TOOL_OUTPUT = _env_int("MAX_TOOL_OUTPUT", 4000)
MAX_CONCURRENT_TASKS = 1  # one GPU holds one model; do not raise this

# An approval is an answer about the machine as it was when Peppermint asked.
# After this many seconds the question is stale and Peppermint asks again.
APPROVAL_TTL_S = _env_int("APPROVAL_TTL_S", 900)

# --- Storage ---------------------------------------------------------------

DATA_DIR = Path(_env("DATA_DIR", str(HOME / ".local/share/peppermint")))
DB_PATH = DATA_DIR / "peppermint.db"
LOG_PATH = DATA_DIR / "peppermint.log"

# --- Desktop ---------------------------------------------------------------

HOTKEY = _env("HOTKEY", "<Super>space")
APP_NAME = "Peppermint"
APP_ICON = "system-run"
