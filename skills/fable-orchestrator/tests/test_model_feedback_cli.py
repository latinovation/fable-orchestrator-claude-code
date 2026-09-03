from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import feedback

STDIN_RECORD: dict[str, object] = {
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
}
RUN_ID = "cli-run-000001"
OTHER_RUN_ID = "cli-run-000002"


def run_cli(argv: list[str], path: Path, stdin_text: str = "") -> tuple[object, str, str]:
    """Invoke the CLI in-process and return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    code: object = 0
    with patch.dict(os.environ, {"CLAUDE_MODEL_FEEDBACK_PATH": str(path)}), patch.object(
        sys, "stdin", io.StringIO(stdin_text)
    ), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            feedback.main(argv)
        except SystemExit as error:
            code = error.code
    return code, out.getvalue(), err.getvalue()


class RecordCommandTests(unittest.TestCase):
    def test_record_writes_a_complete_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            code, out, _ = run_cli(
                ["record", "--run-id", RUN_ID], path, json.dumps(STDIN_RECORD)
            )
            self.assertEqual(code, 0)
            self.assertIn("feedback recorded", out)
            written = json.loads(path.read_text().splitlines()[0])
            self.assertEqual(written["run_id"], RUN_ID)
            self.assertEqual(written["stage"], "complete")
            self.assertEqual(written["files_changed"], 2)

    def test_record_applies_runtime_model_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            code, _, _ = run_cli(
                ["record", "--run-id", RUN_ID, "--actual-model", "opus"],
                path,
                json.dumps(STDIN_RECORD),
            )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(path.read_text())["actual_model"], "opus")

    def test_record_discards_recorded_at_from_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            stdin_text = json.dumps({**STDIN_RECORD, "recorded_at": "LEAK"})
            code, _, _ = run_cli(["record", "--run-id", RUN_ID], path, stdin_text)
            self.assertEqual(code, 0)
            content = path.read_text()
            self.assertNotIn("LEAK", content)
            self.assertRegex(json.loads(content)["recorded_at"], feedback.RECORDED_AT.pattern)

    def test_record_rejects_malformed_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            code, _, err = run_cli(["record", "--run-id", RUN_ID], path, "{")
            self.assertEqual(code, 1)
            self.assertIn("feedback NOT recorded", err)
            self.assertFalse(path.exists())

    def test_record_rejects_non_object(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            code, _, err = run_cli(["record", "--run-id", RUN_ID], path, "[1,2]")
            self.assertEqual(code, 1)
            self.assertIn("feedback NOT recorded", err)

    def test_record_rejects_invalid_enum(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            stdin_text = json.dumps({**STDIN_RECORD, "outcome": "NO-EXISTE"})
            code, _, err = run_cli(["record", "--run-id", RUN_ID], path, stdin_text)
            self.assertEqual(code, 1)
            self.assertIn("feedback NOT recorded", err)
            self.assertIn("outcome", err)

    def test_record_rejects_unhashable_enum(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            stdin_text = json.dumps({**STDIN_RECORD, "outcome": ["pass"]})
            code, _, err = run_cli(["record", "--run-id", RUN_ID], path, stdin_text)
            self.assertEqual(code, 1)
            self.assertIn("feedback NOT recorded", err)
            self.assertIn("invalid outcome", err)
            self.assertFalse(path.exists())

    def test_record_reports_unwritable_history(self):
        with tempfile.TemporaryDirectory() as directory:
            blocker = Path(directory) / "archivo.txt"
            blocker.write_text("not a directory")
            path = blocker / "history.jsonl"
            code, _, err = run_cli(
                ["record", "--run-id", RUN_ID], path, json.dumps(STDIN_RECORD)
            )
            self.assertEqual(code, 1)
            self.assertIn("feedback NOT recorded", err)
            self.assertEqual(blocker.read_text(), "not a directory")


class FallbackCommandTests(unittest.TestCase):
    def test_fallback_records_a_blocked_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            code, out, _ = run_cli(
                ["fallback", "--run-id", RUN_ID, "--stage", "planner", "--outcome", "blocked"],
                path,
            )
            self.assertEqual(code, 0)
            self.assertIn("fallback feedback recorded", out)
            written = json.loads(path.read_text())
            self.assertEqual((written["stage"], written["outcome"]), ("planner", "blocked"))

    def test_fallback_rejects_unsafe_run_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            code, _, err = run_cli(
                ["fallback", "--run-id", "bad id", "--stage", "planner"], path
            )
            self.assertEqual(code, 1)
            self.assertIn("feedback NOT recorded", err)
            self.assertFalse(path.exists())


class BeginAndSummaryTests(unittest.TestCase):
    def test_begin_prints_a_safe_run_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            code, out, _ = run_cli(["begin", "--session-id", "session-abcdefgh"], path)
            self.assertEqual(code, 0)
            self.assertRegex(out.strip(), feedback.SAFE_ID.pattern)

    def test_begin_falls_back_to_a_default_session_label(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            run_id = feedback.begin("///", path)
            self.assertTrue(run_id.startswith("session-"))

    def test_summary_reports_empty_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            code, out, _ = run_cli(["summary"], path)
            self.assertEqual(code, 0)
            self.assertIn("No historical routing evidence yet", out)

    def test_summary_excludes_current_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            for run_id in (RUN_ID, OTHER_RUN_ID):
                feedback.append_record(
                    feedback.validate(
                        {
                            "run_id": run_id,
                            "stage": "complete",
                            "task_class": "routine",
                            "planned_model": "sonnet",
                            "actual_model": "sonnet",
                            "outcome": "pass",
                        }
                    ),
                    path,
                )
            code, out, _ = run_cli(["summary", "--exclude-run-id", OTHER_RUN_ID], path)
            self.assertEqual(code, 0)
            self.assertIn("Historical runs: 1", out)
            code, out, _ = run_cli(["summary"], path)
            self.assertIn("Historical runs: 2", out)

    def test_load_records_ignores_leaked_recorded_at(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            path.write_text(
                json.dumps({**STDIN_RECORD, "run_id": RUN_ID, "recorded_at": "LEAK"}) + "\n"
            )
            records, invalid = feedback.load_records(path)
            self.assertEqual(records, [])
            self.assertEqual(invalid, 1)


class DispatchTests(unittest.TestCase):
    def test_actual_model_command_prints_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            with patch.object(feedback, "actual_model", return_value="opus"):
                code, out, _ = run_cli(
                    [
                        "actual-model",
                        "--session-id",
                        "session-abcdefgh",
                        "--agent-type",
                        "opus-executor",
                        "--expected-model-id",
                        "claude-opus-5",
                    ],
                    path,
                )
            self.assertEqual(code, 0)
            self.assertEqual(out.strip().split(), ["opus"])

    def test_self_test_command_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.jsonl"
            code, out, _ = run_cli(["self-test"], path)
            self.assertEqual(code, 0)
            self.assertIn("self-test passed", out)

    def test_self_test_function_passes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            feedback.self_test()
        self.assertIn("self-test passed", out.getvalue())


if __name__ == "__main__":
    unittest.main()
