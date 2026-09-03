from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from support import feedback

SESSION_ID = "session-actual-model-1"
OPUS_ID = "claude-opus-5"
SONNET_ID = "claude-sonnet-5"
SYNTHETIC_ID = "<synthetic>"


def write_subagent(
    subagents: Path,
    name: str,
    *,
    agent_type: str = "opus-executor",
    models: list[str] | None = None,
    meta_text: str | None = None,
    write_transcript: bool = True,
) -> None:
    subagents.mkdir(parents=True, exist_ok=True)
    (subagents / f"{name}.meta.json").write_text(
        meta_text if meta_text is not None else json.dumps({"agentType": agent_type})
    )
    if not write_transcript:
        return
    lines = [json.dumps({"message": {"model": model}}) for model in models or []]
    (subagents / f"{name}.jsonl").write_text("\n".join(["not json", *lines]) + "\n")


@contextlib.contextmanager
def session_tree():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        yield root, root / "project" / SESSION_ID / "subagents"


def detect(root: Path, agent_type: str = "opus-executor", expected: str | None = None):
    """Return (alias, reason, stdout, stderr) for the public wrapper."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        alias = feedback.actual_model(SESSION_ID, agent_type, root, expected_model_id=expected)
        _, reason = feedback.detect_model(SESSION_ID, agent_type, root, expected)
    return alias, reason, out.getvalue(), err.getvalue()


class ActualModelTests(unittest.TestCase):
    def test_two_subagents_with_different_models_are_unknown(self):
        with session_tree() as (root, subagents):
            write_subagent(subagents, "agent-1", models=[OPUS_ID])
            write_subagent(subagents, "agent-2", models=[SONNET_ID])
            alias, reason, out, err = detect(root)
            self.assertEqual(alias, "unknown")
            self.assertIn("multiple model aliases found", reason)
            self.assertIn("opus", reason)
            self.assertIn("sonnet", reason)
            self.assertEqual(out, "")
            self.assertIn("actual-model unknown: multiple model aliases found", err)

    def test_two_subagents_with_the_same_model_are_known(self):
        with session_tree() as (root, subagents):
            write_subagent(subagents, "agent-1", models=[OPUS_ID])
            write_subagent(subagents, "agent-2", models=[OPUS_ID])
            alias, reason, out, err = detect(root, expected=OPUS_ID)
            self.assertEqual(alias, "opus")
            self.assertEqual(reason, "")
            self.assertEqual((out, err), ("", ""))

    def test_missing_subagents_report_the_agent_type(self):
        with session_tree() as (root, _):
            alias, reason, _, err = detect(root)
            self.assertEqual(alias, "unknown")
            self.assertEqual(reason, "no subagent transcript with agentType 'opus-executor'")
            self.assertIn("no subagent transcript", err)

    def test_unsafe_session_id_is_reported(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            alias = feedback.actual_model("bad.id", "opus-executor", Path("/nonexistent"))
        self.assertEqual(alias, "unknown")
        self.assertEqual(out.getvalue(), "")
        self.assertIn("actual-model unknown: unsafe session id", err.getvalue())

    def test_synthetic_model_entries_are_skipped(self):
        with session_tree() as (root, subagents):
            write_subagent(subagents, "agent-1", models=[SYNTHETIC_ID, OPUS_ID])
            alias, reason, _, err = detect(root, expected=OPUS_ID)
            self.assertEqual((alias, reason, err), ("opus", "", ""))

    def test_dated_model_id_does_not_match_the_expected_id(self):
        with session_tree() as (root, subagents):
            write_subagent(subagents, "agent-1", models=["claude-opus-5-20260101"])
            alias, reason, _, err = detect(root, expected=OPUS_ID)
            self.assertEqual(alias, "unknown")
            self.assertIn("does not match expected claude-opus-5", reason)
            self.assertIn("actual-model unknown:", err)

    def test_alias_is_returned_without_an_expected_model_id(self):
        with session_tree() as (root, subagents):
            write_subagent(subagents, "agent-1", agent_type="sonnet-executor", models=[SONNET_ID])
            alias, reason, _, err = detect(root, agent_type="sonnet-executor")
            self.assertEqual((alias, reason, err), ("sonnet", "", ""))

    def test_top_level_model_field_is_read(self):
        with session_tree() as (root, subagents):
            subagents.mkdir(parents=True, exist_ok=True)
            (subagents / "agent-1.meta.json").write_text(json.dumps({"agentType": "fable-planner"}))
            (subagents / "agent-1.jsonl").write_text(
                json.dumps({"model": "claude-fable-5-1"}) + "\n"
            )
            alias, reason, _, _ = detect(root, agent_type="fable-planner", expected="claude-fable-5-1")
            self.assertEqual((alias, reason), ("fable", ""))

    def test_transcript_without_model_field_is_reported(self):
        with session_tree() as (root, subagents):
            subagents.mkdir(parents=True, exist_ok=True)
            (subagents / "agent-1.meta.json").write_text(json.dumps({"agentType": "opus-executor"}))
            (subagents / "agent-1.jsonl").write_text(json.dumps({"type": "user"}) + "\n")
            alias, reason, _, _ = detect(root)
            self.assertEqual((alias, reason), ("unknown", "transcript has no model field"))

    def test_unreadable_metadata_warns_with_the_file_name_only(self):
        with session_tree() as (root, subagents):
            write_subagent(subagents, "agent-1", meta_text="{ broken", models=[OPUS_ID])
            alias, _, _, err = detect(root)
            self.assertEqual(alias, "unknown")
            self.assertIn("warning: unreadable metadata agent-1.meta.json", err)
            self.assertNotIn(str(root), err)

    def test_missing_transcript_warns_with_the_file_name_only(self):
        with session_tree() as (root, subagents):
            write_subagent(subagents, "agent-1", write_transcript=False)
            alias, _, _, err = detect(root)
            self.assertEqual(alias, "unknown")
            self.assertIn("warning: missing transcript agent-1.jsonl", err)
            self.assertNotIn(str(root), err)

    def test_metadata_that_is_not_an_object_is_ignored(self):
        with session_tree() as (root, subagents):
            write_subagent(subagents, "agent-1", meta_text=json.dumps([{"agentType": "opus-executor"}]))
            alias, reason, _, _ = detect(root)
            self.assertEqual(alias, "unknown")
            self.assertIn("no subagent transcript", reason)

    def test_transcript_models_collects_both_shapes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.jsonl"
            path.write_text(
                "\n".join(
                    [
                        "[]",
                        json.dumps({"model": OPUS_ID}),
                        json.dumps({"message": {"model": SONNET_ID}}),
                        json.dumps({"message": "text"}),
                    ]
                )
                + "\n"
            )
            self.assertIn(OPUS_ID, feedback.transcript_models(path))
            self.assertIn(SONNET_ID, feedback.transcript_models(path))


if __name__ == "__main__":
    unittest.main()
