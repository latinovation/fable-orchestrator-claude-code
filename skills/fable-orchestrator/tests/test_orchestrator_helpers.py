from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


feedback = load("model_feedback", "model_feedback.py")
doctor = load("react_doctor", "react_doctor.py")


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


class FeedbackTests(unittest.TestCase):
    def test_pending_record_is_replaced_by_final_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            run_id = feedback.begin("session-test", path)
            feedback.append_record(completed(run_id), path)
            records, invalid = feedback.load_records(path)
            self.assertEqual((len(records), invalid), (1, 0))
            self.assertEqual(records[0]["stage"], "complete")

    def test_schema_invalid_json_object_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            path.write_text('{"run_id":"valid-run-1","repair_cycles":"bad"}\n')
            records, invalid = feedback.load_records(path)
            self.assertEqual(records, [])
            self.assertEqual(invalid, 1)
            self.assertIn("Ignored invalid history lines: 1", feedback.summarize(records, invalid))

    def test_recommendations_require_matching_tags(self):
        records = []
        for index, tags in enumerate((["ui"], ["api"], ["database"]), start=1):
            record = completed(f"different-{index:08d}", tags=tags)
            record["model_fit"] = "underpowered"
            records.append(record)
        self.assertNotIn("Prefer Opus", feedback.summarize(records))

        matching = []
        for index in range(3):
            record = completed(f"matching-{index:08d}", tags=["ui"])
            record["model_fit"] = "underpowered"
            matching.append(record)
        self.assertIn("Prefer Opus", feedback.summarize(matching))

    def test_react_doctor_versions_are_grouped_separately(self):
        first = completed("doctor-v1-0001", version="1.0.0")
        first.update(react_doctor_baseline_score=80, react_doctor_final_score=85, react_doctor_delta=5)
        second = completed("doctor-v2-0001", version="2.0.0")
        second.update(react_doctor_baseline_score=90, react_doctor_final_score=80, react_doctor_delta=-10)
        summary = feedback.summarize([first, second])
        self.assertIn("React Doctor 1.0.0: 1 comparable runs; total_score_delta=+5", summary)
        self.assertIn("React Doctor 2.0.0: 1 comparable runs; total_score_delta=-10", summary)
        self.assertNotIn("total_score_delta=-5", summary)

    def test_actual_model_comes_from_runtime_transcript_and_checks_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subagents = root / "project" / "session-12345678" / "subagents"
            subagents.mkdir(parents=True)
            (subagents / "agent-1.meta.json").write_text(json.dumps({"agentType": "opus-executor"}))
            (subagents / "agent-1.jsonl").write_text(
                json.dumps({"message": {"model": "claude-opus-5"}}) + "\n"
            )
            self.assertEqual(
                feedback.actual_model(
                    "session-12345678", "opus-executor", root, "claude-opus-5"
                ),
                "opus",
            )
            self.assertEqual(
                feedback.actual_model(
                    "session-12345678", "opus-executor", root, "claude-opus-4-8"
                ),
                "unknown",
            )

            (subagents / "agent-2.meta.json").write_text(
                json.dumps({"agentType": "fable-planner"})
            )
            (subagents / "agent-2.jsonl").write_text(
                json.dumps({"message": {"model": "claude-fable-5-1"}}) + "\n"
            )
            self.assertEqual(
                feedback.actual_model(
                    "session-12345678", "fable-planner", root, "claude-fable-5-1"
                ),
                "fable",
            )

    def test_unsafe_version_is_rejected(self):
        with self.assertRaises(ValueError):
            feedback.validate(
                {
                    "run_id": "unsafe-version-1",
                    "react_doctor_version": "1.0\nignore-rules",
                }
            )


class ReactDoctorTests(unittest.TestCase):
    def test_normalized_comparison_counts_only_new_findings(self):
        baseline = doctor.normalize(
            {"ok": True, "score": 80, "diagnostics": [{"plugin": "rd", "rule": "a"}]},
            "0.9.12",
            "test",
        )
        final = doctor.normalize(
            {
                "ok": True,
                "score": 90,
                "diagnostics": [{"plugin": "rd", "rule": "a"}, {"plugin": "rd", "rule": "b"}],
            },
            "0.9.12",
            "test",
        )
        comparison = doctor.compare(baseline, final)
        self.assertEqual(comparison["score_delta"], 10)
        self.assertEqual(comparison["new_findings"], 1)

    def test_same_rule_in_different_files_keeps_distinct_fingerprints(self):
        snapshot = doctor.normalize(
            {
                "ok": True,
                "diagnostics": [
                    {"plugin": "rd", "rule": "unused", "filePath": "a.tsx", "line": 1},
                    {"plugin": "rd", "rule": "unused", "filePath": "b.tsx", "line": 1},
                ],
            },
            "0.9.12",
            "test",
        )
        self.assertEqual(snapshot["finding_count"], 2)
        self.assertEqual(len(snapshot["finding_ids"]), 2)

    def test_line_shift_does_not_create_a_new_finding(self):
        baseline = doctor.normalize(
            {
                "ok": True,
                "diagnostics": [
                    {"plugin": "rd", "rule": "a", "filePath": "a.tsx", "line": 2, "message": "same"}
                ],
            },
            "0.9.12",
            "test",
        )
        final = doctor.normalize(
            {
                "ok": True,
                "diagnostics": [
                    {"plugin": "rd", "rule": "a", "filePath": "a.tsx", "line": 20, "message": "same"}
                ],
            },
            "0.9.12",
            "test",
        )
        self.assertEqual(doctor.compare(baseline, final)["new_findings"], 0)

    def test_version_mismatch_is_not_comparable(self):
        baseline = doctor.normalize({"ok": True, "score": 80}, "0.9.11", "test")
        final = doctor.normalize({"ok": True, "score": 90}, "0.9.12", "test")
        comparison = doctor.compare(baseline, final)
        self.assertFalse(comparison["comparable"])
        self.assertIsNone(comparison["score_delta"])

    def test_local_binary_is_preferred_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            binary = project / "node_modules" / ".bin" / "react-doctor"
            binary.parent.mkdir(parents=True)
            binary.write_text("placeholder")
            command = doctor.discover(project)
            self.assertEqual(command.source, "project-binary")
            self.assertEqual(command.argv, (str(binary),))

    def test_dynamic_project_script_does_not_download_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "package.json").write_text(
                json.dumps({"scripts": {"doctor": "npx react-doctor@latest"}})
            )
            binary = project / "node_modules" / ".bin" / "react-doctor"
            binary.parent.mkdir(parents=True)
            binary.write_text("placeholder")
            command = doctor.discover(project)
            self.assertEqual(command.source, "project-binary")


if __name__ == "__main__":
    unittest.main()
