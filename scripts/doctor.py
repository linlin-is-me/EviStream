"""Check the Stage 0 Linux development prerequisites without exposing secrets."""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    command: tuple[str, ...]
    validate: Callable[[str], bool]
    expected: str


def _starts_with(pattern: str) -> Callable[[str], bool]:
    compiled = re.compile(pattern)
    return lambda output: bool(compiled.search(output))


CHECKS = (
    Check("Python", (sys.executable, "--version"), _starts_with(r"^Python 3\.11\."), "Python 3.11"),
    Check("Node", ("node", "--version"), _starts_with(r"^v24\."), "Node 24"),
    Check("pnpm", ("pnpm", "--version"), _starts_with(r"^11\."), "pnpm 11"),
    Check("FFmpeg", ("ffmpeg", "-version"), _starts_with(r"^ffmpeg version "), "FFmpeg"),
    Check("FFprobe", ("ffprobe", "-version"), _starts_with(r"^ffprobe version "), "ffprobe"),
    Check(
        "Docker Engine",
        ("docker", "info", "--format", "{{.ServerVersion}}"),
        lambda output: bool(output.strip()),
        "a reachable Linux Docker Engine",
    ),
    Check(
        "Docker Compose",
        ("docker", "compose", "version", "--short"),
        lambda output: bool(output.strip()),
        "Docker Compose",
    ),
)


def _run(check: Check) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            check.command,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    output = (completed.stdout or completed.stderr).strip().splitlines()
    summary = output[0] if output else f"exit {completed.returncode}"
    return completed.returncode == 0 and check.validate(summary), summary


def _platform_summary() -> tuple[bool, str]:
    if sys.platform != "linux":
        return False, f"unsupported platform: {sys.platform}"
    os_release = Path("/etc/os-release")
    description = platform.platform()
    if os_release.is_file():
        values = dict(
            line.split("=", 1)
            for line in os_release.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
        description = values.get("PRETTY_NAME", description).strip('"')
    if os.getenv("WSL_DISTRO_NAME"):
        description += f" (WSL2: {os.environ['WSL_DISTRO_NAME']})"
    return True, description


def _configuration_summary(project_root: Path) -> tuple[bool, str]:
    required = (
        project_root / "pyproject.toml",
        project_root / ".env.example",
        project_root / "configs/models/mock.yaml",
        project_root / "configs/models/dashscope-test.yaml",
    )
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        return False, f"missing: {', '.join(missing)}"

    dotenv = project_root / ".env"
    if not dotenv.is_file():
        return True, "templates present; local .env not configured"
    key_configured = any(
        line.startswith("EVISTREAM_MODEL_API_KEY=") and line.partition("=")[2].strip()
        for line in dotenv.read_text(encoding="utf-8").splitlines()
    )
    state = "configured" if key_configured else "blank"
    return True, f"templates present; local model key {state}"


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    results: list[tuple[str, bool, str]] = []
    platform_ok, platform_detail = _platform_summary()
    results.append(("Platform", platform_ok, platform_detail))
    for check in CHECKS:
        ok, detail = _run(check)
        results.append((check.name, ok, detail))
    config_ok, config_detail = _configuration_summary(project_root)
    results.append(("Configuration", config_ok, config_detail))

    for name, ok, detail in results:
        marker = "OK" if ok else "FAIL"
        print(f"[{marker}] {name}: {detail}")
    failures = sum(not ok for _, ok, _ in results)
    print(f"Doctor completed: {len(results) - failures} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
