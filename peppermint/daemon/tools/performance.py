"""Bounded, read-only measurements for desktop/video/local-model contention.

The assessment contains hypotheses and testable options, never a promise that
an idle snapshot validates a workload. Metric semantics follow Linux /proc and
PSI docs, NVIDIA nvidia-smi docs, and https://docs.ollama.com/faq.
"""

from __future__ import annotations

import csv
import http.client
import json
import math
import os
import re
from pathlib import Path
import shutil
import subprocess
import time
from datetime import datetime, timezone

from peppermint.daemon.tools.registry import ToolError, tool

MIB = 1024 * 1024
GIB = 1024 * MIB
PROC = Path("/proc")
MAX_READ = 262144
PROBE_TIMEOUT = 2


def _read(path: Path) -> str:
    with path.open(encoding="utf-8", errors="replace") as handle:
        return handle.read(MAX_READ)


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def parse_meminfo(text: str) -> dict[str, int]:
    """Return kernel kB memory fields in bytes; do not confuse free/available."""
    result = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        fields = value.split()
        if sep and len(fields) == 2 and fields[1] == "kB":
            number = _number(fields[0])
            if number is not None:
                result[key] = int(number * 1024)
    return result


def parse_cpu_stat(text: str) -> tuple[int, ...] | None:
    for line in text.splitlines():
        fields = line.split()
        if fields and fields[0] == "cpu":
            # guest/guest_nice are already included in user/nice.
            values = [_number(field) for field in fields[1:9]]
            if len(values) >= 4 and all(value is not None for value in values):
                return tuple(int(value) for value in values) + (0,) * (8 - len(values))
    return None


def parse_vmstat(text: str) -> dict[str, int]:
    result = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] in {"pswpin", "pswpout"}:
            number = _number(fields[1])
            if number is not None:
                result[fields[0]] = int(number)
    return result


def parse_pressure(text: str) -> dict:
    result = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0] not in {"some", "full"}:
            continue
        row = {}
        for field in fields[1:]:
            key, sep, value = field.partition("=")
            number = _number(value)
            if sep and key in {"avg10", "avg60", "avg300", "total"} and number is not None:
                row[key] = int(number) if key == "total" else number
        if row:
            result[fields[0]] = row
    return result


def counter_rate(before, after, elapsed: float, scale: float = 1) -> float | None:
    """A missing/reset counter is unknown, rather than a fabricated zero rate."""
    if before is None or after is None or elapsed <= 0 or after < before:
        return None
    return round((after - before) * scale / elapsed, 3)


def cpu_rates(before, after) -> dict:
    result = {"utilization_pct": None, "io_wait_pct": None}
    if before is None or after is None or any(b < a for a, b in zip(before, after)):
        return result
    delta = [b - a for a, b in zip(before, after)]
    total = sum(delta)
    if total > 0:
        result["utilization_pct"] = round(100 * (total - delta[3] - delta[4]) / total, 2)
        result["io_wait_pct"] = round(100 * delta[4] / total, 2)
    return result


def _proc_sample() -> dict:
    result = {}
    for key, path, parser in (
        ("cpu", PROC / "stat", parse_cpu_stat),
        ("memory", PROC / "meminfo", parse_meminfo),
        ("swap", PROC / "vmstat", parse_vmstat),
        *((f"pressure_{kind}", PROC / "pressure" / kind, parse_pressure)
          for kind in ("cpu", "memory", "io")),
    ):
        try:
            result[key] = parser(_read(path))
        except OSError:
            result[key] = None
    return result


def parse_gpu_csv(text: str, with_decoder: bool = True) -> list[dict]:
    rows = []
    for fields in csv.reader(text.splitlines()[:32]):
        if len(fields) != (6 if with_decoder else 5):
            continue
        values = [_number(value.strip()) for value in fields[1:]]
        rows.append({
            "name": fields[0].strip()[:120],
            "total_mib": values[0], "used_mib": values[1], "free_mib": values[2],
            "utilization_pct": values[3],
            "decoder_pct": values[4] if with_decoder else None,
        })
    return rows


def _gpu_snapshot() -> dict:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return {"status": "unavailable", "devices": [], "reason": "nvidia-smi is not installed"}
    query = "name,memory.total,memory.used,memory.free,utilization.gpu"
    # Older drivers may reject the optional decoder field; retain VRAM facts.
    for with_decoder in (True, False):
        try:
            proc = subprocess.run(
                [executable, "--query-gpu=" + query + (",utilization.decoder" if with_decoder else ""),
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=PROBE_TIMEOUT, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return {"status": "unavailable", "devices": [], "reason": "GPU query failed or timed out"}
        if proc.returncode == 0:
            devices = parse_gpu_csv(proc.stdout, with_decoder)
            if devices:
                complete = all(all(value is not None for value in row.values()) for row in devices)
                return {"status": "measured" if complete else "partial", "devices": devices}
    return {"status": "unavailable", "devices": [], "reason": "NVIDIA driver query unavailable"}


def _ollama_snapshot() -> dict:
    # Fixed loopback address: never honor proxy variables, follow redirects,
    # or send machine details to a configurable external model endpoint.
    connection = http.client.HTTPConnection("127.0.0.1", 11434, timeout=PROBE_TIMEOUT)
    try:
        connection.request("GET", "/api/ps")
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("unexpected local API response")
        raw = response.read(MAX_READ + 1)
        if len(raw) > MAX_READ:
            raise ValueError("oversized local API response")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            raise ValueError("invalid local API response")
        models = []
        for row in payload["models"][:8]:
            if not isinstance(row, dict):
                continue
            models.append({
                "name": str(row.get("name", row.get("model", "unknown")))[:120],
                "size_bytes": _number(row.get("size")),
                "size_vram_bytes": _number(row.get("size_vram")),
                "context_length": _number(row.get("context_length")),
            })
        return {"status": "measured", "models": models,
                "truncated": len(payload["models"]) > 8, "active_requests": None}
    except (OSError, ValueError, http.client.HTTPException):
        return {"status": "unavailable", "models": [],
                "reason": "Local Ollama /api/ps unavailable", "active_requests": None}
    finally:
        connection.close()


def _disk_snapshot() -> dict:
    rows, seen = [], set()
    for label, path in (("system", Path("/")), ("home", Path.home())):
        try:
            device = path.stat().st_dev
            if device in seen:
                continue
            usage = shutil.disk_usage(path)
            seen.add(device)
            rows.append({"location": label, "total_bytes": usage.total, "free_bytes": usage.free})
        except OSError:
            rows.append({"location": label, "total_bytes": None, "free_bytes": None})
    complete = all(row["free_bytes"] is not None for row in rows)
    return {"status": "measured" if complete else "partial", "filesystems": rows}


def _process_snapshot() -> dict:
    rows, scanned, skipped, limited = [], 0, 0, False
    deadline = time.monotonic() + 0.25
    try:
        with os.scandir(PROC) as entries:
            for entry in entries:
                if not entry.name.isdigit():
                    continue
                if scanned >= 2048 or time.monotonic() > deadline:
                    limited = True
                    break
                scanned += 1
                try:
                    content = _read(Path(entry.path) / "status")
                    memory = parse_meminfo(content)
                    name = next((line.partition(":")[2].strip() for line in content.splitlines()
                                 if line.startswith("Name:")), "unknown")
                    if "VmRSS" in memory:
                        rows.append({"name": name[:60], "rss_bytes": memory["VmRSS"]})
                except OSError:
                    skipped += 1
    except OSError:
        return {"status": "unavailable", "top_rss": []}
    return {"status": "partial" if limited or skipped else "measured",
            "top_rss": sorted(rows, key=lambda row: row["rss_bytes"], reverse=True)[:5],
            "scanned": scanned, "skipped": skipped, "limited": limited,
            "note": "RSS may count shared pages more than once; names only, no command lines."}


def assess_multitasking(snapshot: dict) -> dict:
    """Deterministic, conservative interpretation of the measured fields."""
    findings = []
    memory = snapshot.get("memory", {})
    total, available = memory.get("total_bytes"), memory.get("available_bytes")
    low_ram = bool(total and available is not None and available < max(GIB, total * 0.10))
    if available is not None:
        findings.append(f"Available RAM: {available / GIB:.2f} GiB"
                        + ("; limited headroom in this sample." if low_ram else "."))
    swap_in, swap_out = memory.get("swap_in_bytes_per_s"), memory.get("swap_out_bytes_per_s")
    if swap_in is not None and swap_out is not None:
        if swap_in > 0 or swap_out > 0:
            findings.append(f"Swap activity: {swap_in / MIB:.2f} MiB/s in, {swap_out / MIB:.2f} MiB/s out; "
                            "check sustained activity and memory stalls during playback.")
        elif memory.get("swap_used_bytes", 0):
            findings.append("Swap contains older pages, with no swap I/O in this sample; occupancy alone is not thrashing.")
    for resource in ("cpu", "memory", "io"):
        pressure = snapshot.get("pressure", {}).get(resource, {})
        some = pressure.get("some", {}).get("sample_pct")
        if some is not None and some >= 2:
            findings.append(f"{resource.upper()} stalls affected some tasks for {some:.1f}% of the sample; "
                            "repeat under the target workload to locate contention.")
    cpu = snapshot.get("cpu", {}).get("utilization_pct")
    if cpu is not None and cpu >= 85:
        findings.append(f"CPU utilization was {cpu:.1f}% during the sample; limited CPU headroom.")
    low_vram = False
    for device in snapshot.get("gpu", {}).get("devices", []):
        free, capacity = device.get("free_mib"), device.get("total_mib")
        if free is not None and capacity:
            tight = free < max(1024, capacity * 0.15)
            low_vram = low_vram or tight
            findings.append(f"{device.get('name', 'GPU')}: {free:.0f}/{capacity:.0f} MiB VRAM free"
                            + ("; limited GPU memory headroom." if tight else "."))
    for filesystem in snapshot.get("disks", {}).get("filesystems", []):
        free, capacity = filesystem.get("free_bytes"), filesystem.get("total_bytes")
        if free is not None and capacity and (free < 2 * GIB or free / capacity < 0.05):
            findings.append(f"{filesystem['location']} storage has {free / GIB:.2f} GiB free; "
                            "a capacity concern, not proof of the cause of video stalls.")
    limits = [
        "This short sample does not verify two videos plus two AI generations, or guarantee error-free operation.",
        "GPU decoder utilization is device-wide; zero does not prove browser software decoding. Check each video's decoder and dropped frames.",
        "Ollama loaded models do not reveal active requests, queue length, or configured parallelism.",
    ]
    unknown = [key for key in ("cpu", "memory", "gpu", "ollama")
               if snapshot.get(key, {}).get("status") in {None, "unavailable", "partial"}]
    if unknown:
        limits.append("Incomplete probes: " + ", ".join(unknown) + "; missing values remain unknown.")
    options = [
        {"id": "responsive", "title": "Prioritize smooth video",
         "reason": "One active local generation reduces competing inference allocations.",
         "steps": ["Play both videos at 1080p initially and verify browser hardware decoding.",
                   "Use one loaded local model with a modest context; queue a second AI prompt."],
         "tradeoff": "Two submitted prompts are allowed, but the second waits rather than generating simultaneously."},
        {"id": "parallel", "title": "Test two simultaneous local generations",
         "reason": "Two requests need model, context, and browser memory to fit at the same time.",
         "steps": ["Use the same smaller quantized model for both prompts. A simple chat client can test a 4096-token context; Peppermint's full toolset needs a larger context.",
                   "After permission, test OLLAMA_NUM_PARALLEL=2 and OLLAMA_MAX_LOADED_MODELS=1 using two concurrent clients. Peppermint currently queues its own tasks; server settings alone do not change that. Retest memory and latency."],
         "tradeoff": "Parallel requests increase context memory; 8 GiB VRAM is not a guarantee, and each answer can slow down."},
        {"id": "browser_ai", "title": "Use browser-hosted AI for one or both prompts",
         "reason": "Remote generation moves model compute and model memory off this computer.",
         "steps": ["Use an existing browser AI service alongside both videos, or combine one browser AI prompt with one local generation.",
                   "Compare playback and response latency using the same video quality and prompts."],
         "tradeoff": "Browser tabs still consume local CPU/RAM; network and service limits remain, and prompts go to that provider."},
    ]
    if low_ram or low_vram:
        options.append({"id": "capacity", "title": "Reduce memory demand before upgrades",
                        "reason": "This sample shows limited " + ("RAM and VRAM" if low_ram and low_vram
                                                                  else "RAM" if low_ram else "VRAM") + " headroom.",
                        "steps": ["Reduce model size, context, or unused applications with permission, then repeat the workload.",
                                  "Consider more system RAM only for sustained RAM pressure; system RAM does not add GPU VRAM."],
                        "tradeoff": "A hardware purchase needs sustained workload measurements and compatibility checks."})
    return {"findings": findings, "options": options,
            "verification": ["Run both intended video sites and one AI generation, then two overlapping generations, for several minutes.",
                             "Compare dropped frames, audio/video stalls, answer latency, available RAM, swap rates, PSI stalls and VRAM; verify overlap, not just queued requests.",
                             "Change one setting at a time with approval and keep it only if the comparison improves."],
            "limitations": limits}


def wants_performance_options(prompt: str) -> bool:
    """A bounded report workflow for requests to compare performance solutions."""
    return bool(re.search(r'\b(solutions?|options?|alternatives?)\b', prompt, re.I)
                and re.search(r'\b(videos?|youtube|netflix|multitask\w*|performance|lag\w*|slow|gpu|ram|memory)\b', prompt, re.I))


def render_assessment(snapshot: dict) -> str:
    """Render the evidence-backed plan even when a small model cannot do so."""
    assessment = snapshot.get("assessment") or assess_multitasking(snapshot)
    lines = ["Here are the measured facts and options for two video windows plus one or two AI prompts."]
    for finding in assessment["findings"]:
        lines.append("• " + finding)
    for index, option in enumerate(assessment["options"], 1):
        lines.extend(["", f"{index}. {option['title']}", option["reason"],
                      " ".join(option["steps"]), "Tradeoff: " + option["tradeoff"]])
    lines.extend(["", "How to verify: " + " ".join(assessment["verification"]),
                  "", "Limits: " + " ".join(assessment["limitations"]),
                  "", "This inspection changed no settings. Choose an option before any changes run."])
    return "\n".join(lines)


def collect_snapshot(sample_seconds: float = 1.0, include_processes: bool = False) -> dict:
    before = _proc_sample()
    started = time.monotonic()
    time.sleep(sample_seconds)
    after = _proc_sample()
    elapsed = max(time.monotonic() - started, 0.000001)
    rates = cpu_rates(before.get("cpu"), after.get("cpu"))
    try:
        load = [round(value, 2) for value in os.getloadavg()]
    except OSError:
        load = None
    cpu = {"status": "measured" if rates["utilization_pct"] is not None else "unavailable",
           "logical_cores": os.cpu_count(), "load_1_5_15": load, **rates}
    mem = after.get("memory") or {}
    swap_before, swap_after = before.get("swap") or {}, after.get("swap") or {}
    page_bytes = os.sysconf("SC_PAGE_SIZE")
    swap_total, swap_free = mem.get("SwapTotal"), mem.get("SwapFree")
    memory = {"total_bytes": mem.get("MemTotal"), "available_bytes": mem.get("MemAvailable"),
              "swap_total_bytes": swap_total,
              "swap_used_bytes": max(0, swap_total - swap_free) if swap_total is not None and swap_free is not None else None,
              "swap_in_bytes_per_s": counter_rate(swap_before.get("pswpin"), swap_after.get("pswpin"), elapsed, page_bytes),
              "swap_out_bytes_per_s": counter_rate(swap_before.get("pswpout"), swap_after.get("pswpout"), elapsed, page_bytes)}
    memory["status"] = "measured" if all(value is not None for value in memory.values()) else "partial" if mem else "unavailable"
    pressure = {}
    for kind in ("cpu", "memory", "io"):
        old, new = before.get(f"pressure_{kind}") or {}, after.get(f"pressure_{kind}") or {}
        row = {}
        for mode, values in new.items():
            rate = counter_rate(old.get(mode, {}).get("total"), values.get("total"), elapsed, 0.0001)
            row[mode] = {"avg10_pct": values.get("avg10"), "sample_pct": min(100.0, rate) if rate is not None else None}
        row["status"] = ("measured" if row and all(value["sample_pct"] is not None for value in row.values())
                         else "partial" if row else "unavailable")
        pressure[kind] = row
    snapshot = {"schema_version": 1, "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "sample_seconds": round(elapsed, 3), "cpu": cpu, "memory": memory, "pressure": pressure,
                "gpu": _gpu_snapshot(), "ollama": _ollama_snapshot(), "disks": _disk_snapshot(),
                "processes": _process_snapshot() if include_processes else {"status": "not_requested"}}
    snapshot["assessment"] = assess_multitasking(snapshot)
    return snapshot


@tool(
    name="performance_snapshot",
    description=("Read-only Linux desktop performance sample: CPU, available RAM, swap I/O rates, pressure stalls, "
                 "NVIDIA VRAM/video decoder use, local Ollama models, storage. Use for slow computers, browser video "
                 "plus AI multitasking, or memory problems. Returns measured facts and grounded solution options; "
                 "requires permission and changes no settings. Sample again while the target workload runs."),
    parameters={"type": "object", "properties": {
        "sample_seconds": {"type": "number", "minimum": 0.2, "maximum": 3,
                           "description": "CPU/swap/pressure measurement interval; default 1 second."},
        "include_processes": {"type": "boolean", "description": "Include five largest process RSS values and names only; default false."},
    }},
)
def performance_snapshot(sample_seconds: float = 1.0, include_processes: bool = False) -> str:
    if isinstance(sample_seconds, bool) or not isinstance(sample_seconds, (float, int)) or not math.isfinite(sample_seconds):
        raise ToolError("sample_seconds must be a finite number between 0.2 and 3.")
    if not 0.2 <= sample_seconds <= 3:
        raise ToolError("sample_seconds must be between 0.2 and 3.")
    if not isinstance(include_processes, bool):
        raise ToolError("include_processes must be true or false.")
    return json.dumps(collect_snapshot(float(sample_seconds), include_processes), separators=(",", ":"), allow_nan=False)
