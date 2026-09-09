#!/usr/bin/env python3
"""Phase 0 gate: does the model make correct tool calls?

Run:  .venv/bin/python scripts/test_toolcall.py
"""

import json
import sys
import time

import ollama

MODEL = sys.argv[1] if len(sys.argv) > 1 else "spark"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "gsettings_get",
            "description": "Read one desktop setting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "schema": {"type": "string", "description": "For example org.cinnamon.desktop.interface"},
                    "key": {"type": "string", "description": "For example gtk-theme"},
                },
                "required": ["schema", "key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List the files in a directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
]

CASES = [
    ("What GTK theme am I using right now?", "gsettings_get"),
    ("What is in my Downloads folder?", "list_dir"),
]

SYSTEM = (
    "You are Peppermint, a helper on Linux Mint with Cinnamon. "
    "Use a tool to find facts. Do not guess."
)


def main() -> int:
    client = ollama.Client(host="http://127.0.0.1:11434")
    passed = 0

    for prompt, expected in CASES:
        start = time.time()
        reply = client.chat(
            model=MODEL,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": prompt}],
            tools=TOOLS,
            think=False,
            options={"num_ctx": 16384, "temperature": 0.2, "top_p": 0.9},
        )
        elapsed = time.time() - start
        calls = reply.message.tool_calls or []
        names = [c.function.name for c in calls]
        ok = expected in names
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] {prompt}")
        print(f"       want={expected} got={names or 'no tool call'} ({elapsed:.1f}s)")
        for c in calls:
            print(f"       args={json.dumps(c.function.arguments)}")
        if not ok and reply.message.content:
            print(f"       text={reply.message.content[:200]!r}")

    # A second turn must use the tool result instead of calling the tool again.
    reply = client.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "What GTK theme am I using?"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "gsettings_get",
                                          "arguments": {"schema": "org.cinnamon.desktop.interface",
                                                        "key": "gtk-theme"}}}]},
            {"role": "tool", "name": "gsettings_get", "content": "'Mint-Y-Dark-Aqua'"},
        ],
        tools=TOOLS,
        think=False,
        options={"num_ctx": 16384, "temperature": 0.2},
    )
    text = (reply.message.content or "").strip()
    ok = "Mint-Y-Dark-Aqua" in text
    passed += ok
    print(f"[{'PASS' if ok else 'FAIL'}] uses the tool result in the answer")
    print(f"       text={text[:200]!r}")

    total = len(CASES) + 1
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
