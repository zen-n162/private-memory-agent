#!/usr/bin/env python3

"""GPU validation utilities for private-memory-agent."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class GPUCheckInfo:
    """One `nvidia-smi` GPU memory row."""

    name: str
    memory_total_mb: int
    memory_free_mb: int

    @property
    def memory_total_gb(self) -> float:
        return self.memory_total_mb / 1024

    @property
    def memory_free_gb(self) -> float:
        return self.memory_free_mb / 1024

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["memory_total_gb"] = round(self.memory_total_gb, 2)
        payload["memory_free_gb"] = round(self.memory_free_gb, 2)
        return payload


def query_nvidia_smi() -> tuple[GPUCheckInfo, ...]:
    """Return GPU memory info using `nvidia-smi` if it is available."""

    command = [
        "nvidia-smi",
        "--query-gpu=name,memory.total,memory.free",
        "--format=csv,noheader,nounits",
    ]
    output = subprocess.check_output(command, text=True)
    return parse_nvidia_smi_csv(output)


def parse_nvidia_smi_csv(output: str) -> tuple[GPUCheckInfo, ...]:
    """Parse `name,total,free` CSV rows from `nvidia-smi`."""

    rows: list[GPUCheckInfo] = []
    for raw_line in output.splitlines():
        if not raw_line.strip():
            continue
        parts = [part.strip() for part in raw_line.split(",")]
        if len(parts) != 3:
            raise ValueError("unexpected nvidia-smi CSV format")
        rows.append(
            GPUCheckInfo(
                name=parts[0],
                memory_total_mb=int(parts[1]),
                memory_free_mb=int(parts[2]),
            ),
        )
    return tuple(rows)


def format_gpu_check(infos: tuple[GPUCheckInfo, ...]) -> str:
    if not infos:
        return "No NVIDIA GPUs reported by nvidia-smi."
    lines = ["GPU check:"]
    for index, info in enumerate(infos):
        lines.append(
            " ".join(
                [
                    f"gpu={index}",
                    f"name={info.name}",
                    f"total_mb={info.memory_total_mb}",
                    f"free_mb={info.memory_free_mb}",
                    f"free_gb={info.memory_free_gb:.2f}",
                ],
            ),
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check local NVIDIA GPU memory.")
    parser.add_argument("--json", action="store_true", help="Print GPU info as JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        infos = query_nvidia_smi()
    except FileNotFoundError:
        print("nvidia-smi not found.")
        return 1
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"nvidia-smi check failed: {exc.__class__.__name__}")
        return 1

    if args.json:
        print(json.dumps([info.to_dict() for info in infos], indent=2, sort_keys=True))
    else:
        print(format_gpu_check(infos))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
