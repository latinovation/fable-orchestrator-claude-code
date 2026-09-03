#!/usr/bin/env python3
"""Run a pinned/installed React Doctor and compare normalized snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

PINNED_NPX_SPEC = "react-doctor@0.9.12"
VERSION_PATTERN = re.compile(r"\b(?:v)?(\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?)\b")
NETWORK_SCRIPT = re.compile(
    r"(?:\b(?:npx|pnpx|bunx|dlx)\b|\b(?:npm|bun)\s+(?:exec|x)\b|@latest)"
)


@dataclass(frozen=True)
class DoctorCommand:
    source: str
    argv: tuple[str, ...]


def package_manager(project: Path, package: dict[str, object]) -> str:
    configured = str(package.get("packageManager", "")).split("@", 1)[0]
    if configured in {"npm", "pnpm", "yarn", "bun"}:
        return configured
    for lockfile, manager in (
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lock", "bun"),
        ("bun.lockb", "bun"),
        ("package-lock.json", "npm"),
    ):
        if (project / lockfile).exists():
            return manager
    return "npm"


def script_command(manager: str, name: str) -> tuple[str, ...]:
    if manager == "npm":
        return (manager, "run", "--silent", name, "--")
    if manager == "pnpm":
        return (manager, "--silent", "run", name, "--")
    if manager == "yarn":
        return (manager, "--silent", "run", name)
    return (manager, "run", "--silent", name, "--")


def discover(project: Path, allow_pinned_npx: bool = False) -> DoctorCommand:
    package_path = project / "package.json"
    package: dict[str, object] = {}
    if package_path.exists():
        try:
            value = json.loads(package_path.read_text())
            if isinstance(value, dict):
                package = value
        except (OSError, json.JSONDecodeError):
            pass

    scripts = package.get("scripts", {})
    if isinstance(scripts, dict):
        manager = package_manager(project, package)
        if shutil.which(manager):
            candidates = sorted(
                (
                    (name, command)
                    for name, command in scripts.items()
                    if isinstance(name, str)
                    and isinstance(command, str)
                    and "react-doctor" in command
                    and not NETWORK_SCRIPT.search(command)
                ),
                key=lambda item: (item[0] not in {"react-doctor", "doctor"}, item[0]),
            )
            if candidates:
                name = candidates[0][0]
                return DoctorCommand(f"package-script:{name}", script_command(manager, name))

    local_binary = project / "node_modules" / ".bin" / "react-doctor"
    if local_binary.is_file():
        return DoctorCommand("project-binary", (str(local_binary),))

    installed = shutil.which("react-doctor")
    if installed:
        return DoctorCommand("installed-command", (installed,))

    if allow_pinned_npx:
        npx = shutil.which("npx")
        if npx:
            return DoctorCommand("approved-pinned-npx", (npx, "--yes", PINNED_NPX_SPEC))

    raise RuntimeError(
        "React Doctor is unavailable. Install it locally or explicitly approve the pinned npx fallback "
        f"({PINNED_NPX_SPEC})."
    )


def run(command: DoctorCommand, args: list[str], project: Path, timeout: int = 240) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*command.argv, *args],
        cwd=project,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def command_version(command: DoctorCommand, project: Path) -> str:
    result = run(command, ["--version"], project, timeout=30)
    match = VERSION_PATTERN.search(result.stdout + "\n" + result.stderr)
    return match.group(1) if match else "unknown"


def parse_json_output(output: str) -> dict[str, object]:
    try:
        value = json.loads(output)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("React Doctor did not emit a JSON object")


def score_value(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100:
        return value
    if isinstance(value, float) and 0 <= value <= 100:
        return round(value)
    if isinstance(value, dict):
        for key in ("score", "value"):
            candidate = score_value(value.get(key))
            if candidate is not None:
                return candidate
    return None


def report_score(report: dict[str, object]) -> int | None:
    for candidate in (report.get("score"), report.get("summary")):
        score = score_value(candidate)
        if score is not None:
            return score
    projects = report.get("projects")
    if isinstance(projects, list):
        scores = [score_value(item.get("score")) for item in projects if isinstance(item, dict)]
        known = [score for score in scores if score is not None]
        if known:
            return min(known)
    return None


def diagnostics(report: dict[str, object]) -> list[dict[str, object]]:
    top_level = report.get("diagnostics")
    if isinstance(top_level, list):
        return [item for item in top_level if isinstance(item, dict)]
    result = []
    projects = report.get("projects")
    if isinstance(projects, list):
        for project in projects:
            if not isinstance(project, dict) or not isinstance(project.get("diagnostics"), list):
                continue
            result.extend(item for item in project["diagnostics"] if isinstance(item, dict))
    return result


def diagnostic_key(diagnostic: dict[str, object]) -> str:
    controlled = {
        "plugin": diagnostic.get("plugin"),
        "rule": diagnostic.get("rule") or diagnostic.get("ruleId"),
        "file": (
            diagnostic.get("file")
            or diagnostic.get("filePath")
            or diagnostic.get("normalizedFilePath")
            or diagnostic.get("filename")
            or diagnostic.get("path")
        ),
        "severity": diagnostic.get("severity"),
        "message": diagnostic.get("message"),
    }
    payload = json.dumps(controlled, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()[:20]


def normalize(report: dict[str, object], version: str, source: str) -> dict[str, object]:
    found = diagnostics(report)
    rule_counts = Counter(
        f"{item.get('plugin', 'unknown')}/{item.get('rule') or item.get('ruleId') or 'unknown'}"
        for item in found
    )
    return {
        "schema_version": 1,
        "ok": report.get("ok") is not False,
        "version": version,
        "source": source,
        "score": report_score(report),
        "finding_count": len(found),
        "finding_ids": sorted(diagnostic_key(item) for item in found),
        "rule_counts": dict(sorted(rule_counts.items())),
    }


def compare(baseline: dict[str, object], final: dict[str, object]) -> dict[str, object]:
    same_version = baseline.get("version") == final.get("version") and baseline.get("version") != "unknown"
    comparable = bool(baseline.get("ok") and final.get("ok") and same_version)
    baseline_score = baseline.get("score") if comparable else None
    final_score = final.get("score") if comparable else None
    delta = (
        final_score - baseline_score
        if isinstance(baseline_score, int) and isinstance(final_score, int)
        else None
    )
    baseline_ids = Counter(baseline.get("finding_ids", []))
    final_ids = Counter(final.get("finding_ids", []))
    new_ids = sorted((final_ids - baseline_ids).elements()) if comparable else []
    return {
        "comparable": comparable,
        "version": baseline.get("version") if same_version else "version-mismatch",
        "baseline_score": baseline_score,
        "final_score": final_score,
        "score_delta": delta,
        "baseline_findings": baseline.get("finding_count"),
        "final_findings": final.get("finding_count"),
        "new_findings": len(new_ids),
        "new_finding_ids": new_ids,
    }


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    path.chmod(0o600)


def load_snapshot(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"snapshot is not a JSON object: {path.name}")
    return value


def scan(args: argparse.Namespace) -> int:
    project = args.project.resolve()
    command = discover(project, args.allow_pinned_npx)
    version = command_version(command, project)
    scan_args = [
        "--json",
        "--json-compact",
        "--yes",
        "--blocking",
        "none",
        "--no-telemetry",
        "--no-supply-chain",
        "--max-duration",
        str(args.max_duration),
    ]
    if args.base:
        scan_args.extend(["--scope", "changed", "--base", args.base, "--include-untracked"])
    else:
        scan_args.extend(["--scope", "full"])
    result = run(command, scan_args, project, timeout=args.max_duration + 60)
    try:
        report = parse_json_output(result.stdout)
    except ValueError as error:
        raise RuntimeError(f"{error}; stderr={result.stderr.strip()[:300]}") from error
    snapshot = normalize(report, version, command.source)
    write_json(args.output, snapshot)
    print(json.dumps(snapshot, sort_keys=True))
    return 0 if snapshot["ok"] else 2


def self_test() -> None:
    baseline = normalize(
        {"ok": True, "score": {"score": 80}, "diagnostics": [{"plugin": "react-doctor", "rule": "a"}]},
        "0.9.12",
        "test",
    )
    final = normalize(
        {
            "ok": True,
            "score": {"score": 85},
            "diagnostics": [
                {"plugin": "react-doctor", "rule": "a"},
                {"plugin": "react-doctor", "rule": "b"},
            ],
        },
        "0.9.12",
        "test",
    )
    result = compare(baseline, final)
    assert result["comparable"] and result["score_delta"] == 5 and result["new_findings"] == 1
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "snapshot.json"
        write_json(output, baseline)
        assert json.loads(output.read_text())["score"] == 80
    print("self-test passed")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)

    discover_parser = commands.add_parser("discover")
    discover_parser.add_argument("--project", type=Path, default=Path.cwd())
    discover_parser.add_argument("--allow-pinned-npx", action="store_true")

    scan_parser = commands.add_parser("scan")
    scan_parser.add_argument("--project", type=Path, default=Path.cwd())
    scan_parser.add_argument("--base")
    scan_parser.add_argument("--output", type=Path, required=True)
    scan_parser.add_argument("--max-duration", type=int, default=180)
    scan_parser.add_argument("--allow-pinned-npx", action="store_true")

    compare_parser = commands.add_parser("compare")
    compare_parser.add_argument("baseline", type=Path)
    compare_parser.add_argument("final", type=Path)
    commands.add_parser("self-test")
    return result


def dispatch(args: argparse.Namespace) -> int:
    if args.command == "discover":
        command = discover(args.project.resolve(), args.allow_pinned_npx)
        print(json.dumps({"source": command.source, "argv": command.argv}))
        return 0
    if args.command == "scan":
        return scan(args)
    if args.command == "compare":
        baseline = load_snapshot(args.baseline)
        final = load_snapshot(args.final)
        print(json.dumps(compare(baseline, final), indent=2, sort_keys=True))
        return 0
    self_test()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return dispatch(args)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"react-doctor wrapper error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
