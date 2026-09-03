from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from support import feedback, parse_frontmatter

PACKAGE_ROOT = Path(__file__).resolve().parents[3]
AGENTS_DIR = PACKAGE_ROOT / "agents"
SKILL_PATH = Path(__file__).resolve().parents[1] / "SKILL.md"
README_PATH = PACKAGE_ROOT / "README.md"
PLANNING_TOOLS = {"Read", "Grep", "Glob"}
EXECUTION_TOOLS = PLANNING_TOOLS | {"Edit", "Write", "Bash"}
EXPECTED_TOOLS = {
    "fable-planner": PLANNING_TOOLS,
    "fable-auditor": PLANNING_TOOLS,
    "opus-executor": EXECUTION_TOOLS,
    "sonnet-executor": EXECUTION_TOOLS,
}
EXPECTED_MODEL_ID_FLAG = re.compile(r"--expected-model-id ([A-Za-z0-9._-]+)")
NEGATIVE_FIXTURE = """---
name: opus-executor
description: Agent definition that forgot to pin a model.
tools: Read, Grep, Glob, Edit, Write, Bash
maxTurns: 64
---

Body.
"""


def check_agent(path: Path, expected_model_id: str, expected_tools: set[str]) -> dict[str, object]:
    """Assert that an agent definition pins the expected model, tools and turn budget."""
    assert path.is_file(), f"missing agent definition: {path.name}"
    front = parse_frontmatter(path)
    assert front.get("name") == path.stem, f"{path.name}: name {front.get('name')!r} != {path.stem!r}"
    model = front.get("model")
    assert model == expected_model_id, f"{path.name}: model {model!r} != {expected_model_id!r}"
    tools = set(front.get("tools") or [])
    assert tools == expected_tools, f"{path.name}: tools {sorted(tools)} != {sorted(expected_tools)}"
    max_turns = front.get("maxTurns")
    assert isinstance(max_turns, str) and max_turns.isdigit() and int(max_turns) > 0, (
        f"{path.name}: maxTurns must be a positive integer, got {max_turns!r}"
    )
    return front


class AgentFrontmatterTests(unittest.TestCase):
    def test_every_expected_agent_pins_its_model_tools_and_turns(self):
        for name, expected_model_id in feedback.EXPECTED_MODEL_IDS.items():
            with self.subTest(agent=name):
                check_agent(AGENTS_DIR / f"{name}.md", expected_model_id, EXPECTED_TOOLS[name])

    def test_model_ids_match_constants(self):
        self.assertEqual(
            feedback.EXPECTED_MODEL_IDS,
            {
                "fable-planner": feedback.FABLE_MODEL_ID,
                "fable-auditor": feedback.FABLE_MODEL_ID,
                "opus-executor": feedback.OPUS_MODEL_ID,
                "sonnet-executor": feedback.SONNET_MODEL_ALIAS,
            },
        )

    def test_agent_directory_holds_exactly_the_expected_agents(self):
        found = sorted(path.stem for path in AGENTS_DIR.glob("*.md"))
        self.assertEqual(found, sorted(feedback.EXPECTED_MODEL_IDS))

    def test_the_check_rejects_an_agent_without_a_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "opus-executor.md"
            path.write_text(NEGATIVE_FIXTURE)
            with self.assertRaisesRegex(AssertionError, "model None != 'claude-opus-5'"):
                check_agent(path, feedback.OPUS_MODEL_ID, EXECUTION_TOOLS)

    def test_the_check_rejects_a_missing_agent_and_a_broken_frontmatter(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "fable-planner.md"
            with self.assertRaisesRegex(AssertionError, "missing agent definition"):
                check_agent(missing, feedback.FABLE_MODEL_ID, PLANNING_TOOLS)
            missing.write_text("no frontmatter here\n")
            with self.assertRaisesRegex(ValueError, "missing frontmatter"):
                check_agent(missing, feedback.FABLE_MODEL_ID, PLANNING_TOOLS)


class SkillDocumentTests(unittest.TestCase):
    def test_skill_frontmatter_is_explicit_and_fable_backed(self):
        front = parse_frontmatter(SKILL_PATH)
        self.assertEqual(front.get("name"), "fable-orchestrator")
        self.assertEqual(front.get("disable-model-invocation"), "true")
        self.assertEqual(front.get("model"), feedback.FABLE_MODEL_ID)

    def test_skill_only_verifies_the_declared_model_ids(self):
        found = set(EXPECTED_MODEL_ID_FLAG.findall(SKILL_PATH.read_text()))
        self.assertEqual(found, {feedback.FABLE_MODEL_ID, feedback.OPUS_MODEL_ID})

    def test_readme_documents_both_verified_model_ids(self):
        if not README_PATH.is_file():
            self.skipTest("README.md is not part of an installed skill")
        text = README_PATH.read_text()
        for model_id in (feedback.FABLE_MODEL_ID, feedback.OPUS_MODEL_ID):
            with self.subTest(model_id=model_id):
                self.assertIn(model_id, text)


if __name__ == "__main__":
    unittest.main()
