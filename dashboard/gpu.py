"""GPU and host RAM snapshots. Soft-fail if nvidia-smi is missing."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

NVIDIA_QUERY = (
    "nvidia-smi",
    "--query-gpu=name,utilization.gpu,memory.used,memory.total,power.draw",
    "--format=csv,noheader,nounits",
)


def _f(value: str) -> float | None:
    text = value.strip()
    if not text or text.upper() in {"N/A", "[N/A]", "NA"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_nvidia_smi_csv(text: str) -> dict[str, Any] | None:
    """Parse one GPU line from nvidia-smi csv (noheader, nounits)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    parts = [p.strip() for p in lines[0].split(",")]
    if len(parts) < 5:
        return None
    return {
        "name": parts[0],
        "utilization_gpu": _f(parts[1]),
        "memory_used_mib": _f(parts[2]),
        "memory_total_mib": _f(parts[3]),
        "power_draw_w": _f(parts[4]),
    }


def read_meminfo(path: Path | str = "/proc/meminfo") -> dict[str, float | None]:
    """Return RAM totals in MiB from /proc/meminfo. Empty dict if unreadable."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {"mem_total_mib": None, "mem_available_mib": None}
    kib: dict[str, float] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        num = rest.strip().split()[0]
        try:
            kib[key] = float(num)
        except ValueError:
            continue
    def mib(k: str) -> float | None:
        v = kib.get(k)
        return round(v / 1024.0, 1) if v is not None else None
    return {
        "mem_total_mib": mib("MemTotal"),
        "mem_available_mib": mib("MemAvailable"),
    }


def gpu_snapshot(timeout: float = 2.0) -> dict[str, Any]:
    """Run nvidia-smi; return {ok, error?, ...fields}."""
    try:
        proc = subprocess.run(
            NVIDIA_QUERY,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "nvidia-smi not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "nvidia-smi timed out"}
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "nvidia-smi failed").strip()
        return {"ok": False, "error": err[:240]}
    parsed = parse_nvidia_smi_csv(proc.stdout or "")
    if parsed is None:
        return {"ok": False, "error": "nvidia-smi returned no GPU line"}
    parsed["ok"] = True
    return parsed


def host_snapshot() -> dict[str, Any]:
    gpu = gpu_snapshot()
    ram = read_meminfo()
    return {"gpu": gpu, "ram": ram}
