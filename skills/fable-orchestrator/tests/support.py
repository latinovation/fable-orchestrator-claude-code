"""Shared helpers for the orchestrator regression tests.

Importing this module redirects ``CLAUDE_MODEL_FEEDBACK_PATH`` to a private
temporary directory so that no test can ever touch the real routing history.
"""

from __future__ import annotations

import atexit
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAKE_DOCTOR_VERSION = "0.9.12"

_HISTORY_DIR = tempfile.TemporaryDirectory(prefix="fable-tests-")
os.environ["CLAUDE_MODEL_FEEDBACK_PATH"] = str(Path(_HISTORY_DIR.name) / "history.jsonl")
atexit.register(_HISTORY_DIR.cleanup)


def load(name: str, filename: str):
    """Load a helper script as a module, reusing the entry in ``sys.modules``."""
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


feedback = load("model_feedback", "model_feedback.py")
doctor = load("react_doctor", "react_doctor.py")

FAKE_DOCTOR_SCRIPT = f"""#!/bin/sh
if [ "$1" = "--version" ]; then
  echo "react-doctor {FAKE_DOCTOR_VERSION}"
  exit 0
fi
echo "$@" >> doctor-args.txt
echo "scanning project (noise line)"
echo "$FAKE_DOCTOR_JSON"
"""


def completed(run_id: str, *, model: str = "sonnet", tags: list[str] | None = None, version: str = "not-run"):
    return feedback.validate(
        {
            "run_id": run_id,
            "stage": "complete",
            "task_class": "routine",
            "tags": tags or ["ui"],
            "planned_model": model,
            "actual_model": model,
            "planner_actual_model": "fable",
            "auditor_actual_model": "fable",
            "outcome": "pass",
            "model_fit": "right-sized",
            "planner_quality": "strong",
            "executor_quality": "strong",
            "auditor_quality": "no-findings",
            "react_doctor_version": version,
        }
    )


def fake_doctor_binary(project: Path) -> Path:
    """Install a POSIX-shell stand-in for React Doctor inside ``project``."""
    binary = project / "node_modules" / ".bin" / "react-doctor"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(FAKE_DOCTOR_SCRIPT)
    binary.chmod(0o755)
    return binary


def parse_frontmatter(path: Path) -> dict[str, object]:
    """Parse a flat ``key: value`` YAML frontmatter block without a YAML parser."""
    lines = path.read_text().splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"missing frontmatter: {path.name}")
    result: dict[str, object] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return result
        key, separator, value = line.partition(":")
        if not separator or key != key.strip() or not key.strip():
            continue
        cleaned = value.strip().strip('"').strip("'")
        result[key.strip()] = (
            [item.strip() for item in cleaned.split(",") if item.strip()]
            if key.strip() == "tools"
            else cleaned
        )
    raise ValueError(f"unterminated frontmatter: {path.name}")
