#!/usr/bin/env python3
"""Persist, validate, and summarize model-routing outcomes."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path.home() / ".claude" / "model-routing" / "history.jsonl"
FABLE_MODEL_ID = "claude-fable-5-1"
OPUS_MODEL_ID = "claude-opus-5"
SONNET_MODEL_ALIAS = "sonnet"
EXPECTED_MODEL_IDS = {
    "fable-planner": FABLE_MODEL_ID,
    "fable-auditor": FABLE_MODEL_ID,
    "opus-executor": OPUS_MODEL_ID,
    "sonnet-executor": SONNET_MODEL_ALIAS,
}
MODEL_VALUES = {"sonnet", "opus", "fable", "haiku", "unknown"}
ENUMS = {
    "task_class": {"routine", "complex", "high-risk", "unknown"},
    "planned_model": {"sonnet", "opus", "unknown"},
    "actual_model": MODEL_VALUES,
    "planner_actual_model": MODEL_VALUES,
    "auditor_actual_model": MODEL_VALUES,
    "outcome": {"pass", "fix_required", "blocked", "incomplete"},
    "model_fit": {"underpowered", "right-sized", "overpowered", "unknown"},
    "planner_quality": {"strong", "adequate", "weak", "unknown"},
    "executor_quality": {"strong", "adequate", "weak", "unknown"},
    "auditor_quality": {"useful", "no-findings", "false-positive", "unknown"},
}
STAGES = {"started", "planner", "executor", "auditor", "complete"}
COUNTS = {"repair_cycles", "checks_failed", "material_findings", "files_changed"}
REACT_DOCTOR_SCORES = {"react_doctor_baseline_score", "react_doctor_final_score"}
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
SAFE_VERSION = re.compile(r"^(?:not-run|[A-Za-z0-9][A-Za-z0-9._+-]{0,31})$")
RECORDED_AT = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?\+00:00$")
TAG = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def state_path() -> Path:
    return Path(os.environ.get("CLAUDE_MODEL_FEEDBACK_PATH", DEFAULT_PATH))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate(raw: object, *, legacy_run_id: str | None = None) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ValueError("record must be a JSON object")

    run_id = raw.get("run_id", legacy_run_id)
    if not isinstance(run_id, str) or not SAFE_ID.fullmatch(run_id):
        raise ValueError("run_id must be 8-80 safe characters")

    stage = raw.get("stage", "complete")
    if stage not in STAGES:
        raise ValueError(f"invalid stage: {stage!r}")

    record: dict[str, object] = {"schema_version": 2, "run_id": run_id, "stage": stage}
    for key, allowed in ENUMS.items():
        value = raw.get(key, "unknown")
        if not isinstance(value, str) or value not in allowed:
            raise ValueError(f"missing {key}" if key not in raw else f"invalid {key}: {value!r}")
        record[key] = value

    tags = raw.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 8:
        raise ValueError("tags must be a list of at most 8 controlled labels")
    clean_tags = []
    for tag in tags:
        lowered = tag.lower() if isinstance(tag, str) else ""
        if not TAG.fullmatch(lowered):
            raise ValueError(f"invalid tag: {tag!r}")
        clean_tags.append(lowered)
    record["tags"] = sorted(set(clean_tags))

    for key in COUNTS:
        value = raw.get(key, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{key} must be a non-negative integer")
        record[key] = value

    applicable = raw.get("react_doctor_applicable", False)
    if not isinstance(applicable, bool):
        raise ValueError("react_doctor_applicable must be boolean")
    record["react_doctor_applicable"] = applicable

    for key in REACT_DOCTOR_SCORES:
        value = raw.get(key)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100
        ):
            raise ValueError(f"{key} must be null or an integer from 0 to 100")
        record[key] = value

    new_findings = raw.get("react_doctor_new_findings", 0)
    if not isinstance(new_findings, int) or isinstance(new_findings, bool) or new_findings < 0:
        raise ValueError("react_doctor_new_findings must be a non-negative integer")
    record["react_doctor_new_findings"] = new_findings

    version = raw.get("react_doctor_version", "not-run")
    if not isinstance(version, str) or not SAFE_VERSION.fullmatch(version):
        raise ValueError("react_doctor_version must be a safe short version")
    record["react_doctor_version"] = version
    baseline = record["react_doctor_baseline_score"]
    final = record["react_doctor_final_score"]
    record["react_doctor_delta"] = (
        final - baseline if isinstance(baseline, int) and isinstance(final, int) else None
    )

    recorded_at = raw.get("recorded_at")
    if recorded_at is None:
        record["recorded_at"] = now_iso()
    elif isinstance(recorded_at, str) and RECORDED_AT.fullmatch(recorded_at):
        record["recorded_at"] = recorded_at
    else:
        raise ValueError("recorded_at must be an ISO-8601 UTC timestamp")
    record["planner_agent"] = "fable-planner"
    record["planner_requested_model"] = "fable"
    record["auditor_agent"] = "fable-auditor"
    record["auditor_requested_model"] = "fable"
    planned = record["planned_model"]
    record["executor_agent"] = f"{planned}-executor" if planned in {"sonnet", "opus"} else "not-run"
    return record


def append_record(record: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n").encode()
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)
    path.chmod(0o600)


def load_records(path: Path) -> tuple[list[dict[str, object]], int]:
    if not path.exists():
        return [], 0

    latest: dict[str, dict[str, object]] = {}
    order: list[str] = []
    invalid = 0
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        try:
            value = json.loads(line)
            record = validate(value, legacy_run_id=f"legacy-{line_number:08d}")
        except (json.JSONDecodeError, ValueError, TypeError):
            invalid += 1
            continue
        run_id = str(record["run_id"])
        if run_id not in latest:
            order.append(run_id)
        latest[run_id] = record
    return [latest[run_id] for run_id in order], invalid


def comparable_key(record: dict[str, object]) -> tuple[str, str, tuple[str, ...]]:
    return (
        str(record["task_class"]),
        str(record["actual_model"]),
        tuple(str(tag) for tag in record["tags"]),
    )


def summarize(records: list[dict[str, object]], invalid_count: int = 0, limit: int = 10) -> str:
    if not records:
        suffix = f" Ignored invalid history lines: {invalid_count}." if invalid_count else ""
        return "No historical routing evidence yet. Use the base routing rules." + suffix

    completed = [record for record in records if record["stage"] == "complete"]
    pending = len(records) - len(completed)
    by_model: dict[str, list[dict[str, object]]] = defaultdict(list)
    comparable: dict[tuple[str, str, tuple[str, ...]], list[dict[str, object]]] = defaultdict(list)
    for record in records:
        by_model[str(record["actual_model"])].append(record)
    for record in completed:
        if record["actual_model"] in {"sonnet", "opus"}:
            comparable[comparable_key(record)].append(record)

    lines = [
        f"Historical runs: {len(records)} (complete={len(completed)}, incomplete={pending}, ignored_invalid={invalid_count})",
        "Model outcomes:",
    ]
    for model, items in sorted(by_model.items()):
        passed = sum(item["outcome"] == "pass" for item in items)
        repairs = sum(int(item["repair_cycles"]) for item in items)
        findings = sum(int(item["material_findings"]) for item in items)
        fits = Counter(str(item["model_fit"]) for item in items)
        lines.append(
            f"- {model}: {passed}/{len(items)} pass; repairs={repairs}; "
            f"material_findings={findings}; fit={dict(fits)}"
        )

    doctor_by_version: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in completed:
        if isinstance(item["react_doctor_delta"], int):
            doctor_by_version[str(item["react_doctor_version"])].append(item)
    for version, items in sorted(doctor_by_version.items()):
        total_delta = sum(int(item["react_doctor_delta"]) for item in items)
        new_findings = sum(int(item["react_doctor_new_findings"]) for item in items)
        lines.append(
            f"React Doctor {version}: {len(items)} comparable runs; "
            f"total_score_delta={total_delta:+d}; new_findings={new_findings}."
        )

    recommendations = []
    for (task_class, model, tags), items in sorted(comparable.items()):
        if len(items) < 3:
            continue
        underpowered = sum(item["model_fit"] == "underpowered" for item in items)
        overpowered = sum(item["model_fit"] == "overpowered" for item in items)
        clean_passes = sum(
            item["outcome"] == "pass" and int(item["material_findings"]) == 0 for item in items
        )
        label = f"{task_class}/{','.join(tags) or 'untagged'}"
        if model == "sonnet" and underpowered >= 2:
            recommendations.append(f"Prefer Opus for future {label} tasks comparable to these Sonnet runs.")
        if model == "opus" and overpowered >= 3 and clean_passes == len(items):
            recommendations.append(
                f"Consider Sonnet for low-risk {label} tasks; hard-risk rules still win."
            )

    lines.append("Learned recommendations:")
    lines.extend(f"- {item}" for item in recommendations)
    if not recommendations:
        lines.append("- Insufficient comparable evidence; keep the base routing rules.")

    lines.append(f"Recent runs (up to {limit}):")
    for item in records[-limit:]:
        tags = ",".join(str(tag) for tag in item["tags"]) or "none"
        lines.append(
            f"- {item['task_class']}/{tags}: stage={item['stage']}, planned={item['planned_model']}, "
            f"actual={item['actual_model']}, outcome={item['outcome']}, fit={item['model_fit']}, "
            f"repairs={item['repair_cycles']}, planner={item['planner_quality']}, "
            f"executor={item['executor_quality']}, auditor={item['auditor_quality']}, "
            f"react_doctor_delta={item['react_doctor_delta']}"
        )
    return "\n".join(lines)


def begin(session_id: str, path: Path) -> str:
    safe_session = re.sub(r"[^A-Za-z0-9_-]", "", session_id)[:48] or "session"
    run_id = f"{safe_session}-{uuid.uuid4().hex[:12]}"
    append_record(
        validate(
            {
                "run_id": run_id,
                "stage": "started",
                "task_class": "unknown",
                "planned_model": "unknown",
                "actual_model": "unknown",
                "planner_actual_model": "unknown",
                "auditor_actual_model": "unknown",
                "outcome": "incomplete",
                "model_fit": "unknown",
                "planner_quality": "unknown",
                "executor_quality": "unknown",
                "auditor_quality": "unknown",
            }
        ),
        path,
    )
    return run_id


def model_alias(model_id: object) -> str | None:
    if not isinstance(model_id, str):
        return None
    lowered = model_id.lower()
    for alias in ("fable", "opus", "sonnet", "haiku"):
        if alias in lowered:
            return alias
    return None


def model_id_matches(model_id: object, expected_model_id: str) -> bool:
    if not isinstance(model_id, str):
        return False
    return re.search(
        rf"(?<![a-z0-9-]){re.escape(expected_model_id.lower())}(?![a-z0-9-])",
        model_id.lower(),
    ) is not None


def transcript_models(jsonl_path: Path) -> list[object]:
    """Return every ``model`` candidate found in a subagent transcript."""
    candidates: list[object] = []
    for line in jsonl_path.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        candidates.append(event.get("model"))
        message = event.get("message")
        if isinstance(message, dict):
            candidates.append(message.get("model"))
    return candidates


def detect_model(
    session_id: str,
    agent_type: str,
    projects_root: Path | None = None,
    expected_model_id: str | None = None,
) -> tuple[str, str]:
    """Return the runtime model alias and, when unknown, the reason why."""
    if not SAFE_ID.fullmatch(session_id):
        return "unknown", "unsafe session id"
    root = projects_root or Path.home() / ".claude" / "projects"
    found: set[str] = set()
    mismatched: set[str] = set()
    transcripts = 0
    for meta_path in sorted(root.glob(f"*/{session_id}/subagents/*.meta.json")):
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            print(f"warning: unreadable metadata {meta_path.name}", file=sys.stderr)
            continue
        if not isinstance(meta, dict) or meta.get("agentType") != agent_type:
            continue
        jsonl_path = meta_path.with_name(meta_path.name.removesuffix(".meta.json") + ".jsonl")
        try:
            candidates = transcript_models(jsonl_path)
        except OSError:
            print(f"warning: missing transcript {jsonl_path.name}", file=sys.stderr)
            continue
        transcripts += 1
        for candidate in candidates:
            alias = model_alias(candidate)
            if not alias:
                continue
            found.add(alias)
            if expected_model_id and not model_id_matches(candidate, expected_model_id):
                mismatched.add(str(candidate))
    return decide_model(found, mismatched, transcripts, agent_type, expected_model_id)


def decide_model(
    found: set[str],
    mismatched: set[str],
    transcripts: int,
    agent_type: str,
    expected_model_id: str | None,
) -> tuple[str, str]:
    if not transcripts:
        return "unknown", f"no subagent transcript with agentType '{agent_type}'"
    if not found:
        return "unknown", "transcript has no model field"
    if len(found) > 1:
        return "unknown", f"multiple model aliases found: {sorted(found)}"
    if mismatched:
        return (
            "unknown",
            f"model id {sorted(mismatched)} does not match expected {expected_model_id}",
        )
    return sorted(found)[0], ""


def actual_model(
    session_id: str,
    agent_type: str,
    projects_root: Path | None = None,
    expected_model_id: str | None = None,
) -> str:
    alias, reason = detect_model(session_id, agent_type, projects_root, expected_model_id)
    if reason:
        print(f"actual-model unknown: {reason}", file=sys.stderr)
    return alias


def self_test() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "history.jsonl"
        run_id = begin("test-session", path)
        final = validate(
            {
                "run_id": run_id,
                "stage": "complete",
                "task_class": "routine",
                "tags": ["ui"],
                "planned_model": "sonnet",
                "actual_model": "sonnet",
                "planner_actual_model": "fable",
                "auditor_actual_model": "fable",
                "outcome": "pass",
                "model_fit": "right-sized",
                "planner_quality": "strong",
                "executor_quality": "strong",
                "auditor_quality": "no-findings",
                "files_changed": 2,
                "react_doctor_applicable": True,
                "react_doctor_version": "0.9.12",
                "react_doctor_baseline_score": 82,
                "react_doctor_final_score": 94,
            }
        )
        append_record(final, path)
        with path.open("a") as handle:
            handle.write(
                '{"run_id":"self-test-broken-1","outcome":"pass","repair_cycles":"broken"}\n'
            )
        loaded, invalid = load_records(path)
        assert len(loaded) == 1 and loaded[0]["stage"] == "complete"
        assert invalid == 1 and loaded[0]["react_doctor_delta"] == 12
        assert "sonnet: 1/1 pass" in summarize(loaded, invalid)
    print("self-test passed")


def read_stdin_record() -> dict[str, object]:
    try:
        raw = json.load(sys.stdin)
    except ValueError as error:
        print(f"feedback NOT recorded: invalid JSON on stdin: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    if not isinstance(raw, dict):
        print("feedback NOT recorded: stdin must contain a JSON object", file=sys.stderr)
        raise SystemExit(1)
    return raw


def store(raw: dict[str, object], path: Path) -> None:
    try:
        record = validate(raw)
    except ValueError as error:
        print(f"feedback NOT recorded: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    try:
        append_record(record, path)
    except OSError as error:
        print(f"feedback NOT recorded: {error}", file=sys.stderr)
        raise SystemExit(1) from error


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    summary_parser = commands.add_parser("summary")
    summary_parser.add_argument("--exclude-run-id")
    commands.add_parser("self-test")

    begin_parser = commands.add_parser("begin")
    begin_parser.add_argument("--session-id", required=True)

    record_parser = commands.add_parser("record")
    record_parser.add_argument("--run-id", required=True)
    record_parser.add_argument("--planner-actual-model", choices=sorted(MODEL_VALUES))
    record_parser.add_argument("--actual-model", choices=sorted(MODEL_VALUES))
    record_parser.add_argument("--auditor-actual-model", choices=sorted(MODEL_VALUES))

    fallback = commands.add_parser("fallback")
    fallback.add_argument("--run-id", required=True)
    fallback.add_argument("--stage", choices=sorted(STAGES - {"complete"}), required=True)
    fallback.add_argument("--task-class", choices=sorted(ENUMS["task_class"]), default="unknown")
    fallback.add_argument("--planned-model", choices=sorted(ENUMS["planned_model"]), default="unknown")
    fallback.add_argument("--actual-model", choices=sorted(MODEL_VALUES), default="unknown")
    fallback.add_argument("--planner-actual-model", choices=sorted(MODEL_VALUES), default="unknown")
    fallback.add_argument("--outcome", choices=("blocked", "incomplete"), default="incomplete")

    detected = commands.add_parser("actual-model")
    detected.add_argument("--session-id", required=True)
    detected.add_argument("--agent-type", required=True)
    detected.add_argument("--expected-model-id")
    return result


def record_command(args: argparse.Namespace, path: Path) -> None:
    raw = read_stdin_record()
    overrides = {
        key: value
        for key in ("planner_actual_model", "actual_model", "auditor_actual_model")
        if (value := getattr(args, key)) is not None
    }
    without_timestamp = {key: value for key, value in raw.items() if key != "recorded_at"}
    store({**without_timestamp, "run_id": args.run_id, "stage": "complete", **overrides}, path)
    print("feedback recorded")


def fallback_command(args: argparse.Namespace, path: Path) -> None:
    store(
        {
            "run_id": args.run_id,
            "stage": args.stage,
            "task_class": args.task_class,
            "planned_model": args.planned_model,
            "actual_model": args.actual_model,
            "planner_actual_model": args.planner_actual_model,
            "auditor_actual_model": "unknown",
            "outcome": args.outcome,
            "model_fit": "unknown",
            "planner_quality": "unknown",
            "executor_quality": "unknown",
            "auditor_quality": "unknown",
        },
        path,
    )
    print("fallback feedback recorded")


def summary_command(args: argparse.Namespace, path: Path) -> None:
    records, invalid = load_records(path)
    if args.exclude_run_id:
        records = [record for record in records if record["run_id"] != args.exclude_run_id]
    print(summarize(records, invalid))


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    path = state_path()
    if args.command == "begin":
        print(begin(args.session_id, path))
    elif args.command == "record":
        record_command(args, path)
    elif args.command == "fallback":
        fallback_command(args, path)
    elif args.command == "actual-model":
        print(actual_model(args.session_id, args.agent_type, expected_model_id=args.expected_model_id))
    elif args.command == "summary":
        summary_command(args, path)
    else:
        self_test()


if __name__ == "__main__":
    main()
