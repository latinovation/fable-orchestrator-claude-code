# Fable Orchestrator for Claude Code

Portable export updated on 2026-09-02. It contains the skill, four global agents, deterministic helpers, and regression tests. It does not contain routing history, transcripts, project code, caches, snapshots, or credentials.

## Requirements

- Claude Code with access to `claude-fable-5-1`, `claude-opus-5`, and the `sonnet` model alias.
- Python 3.10 or newer.
- Node/npm only if the user approves the pinned React Doctor fallback.

## Install

From this directory:

```bash
mkdir -p "$HOME/.claude/skills" "$HOME/.claude/agents"
cp -R skills/fable-orchestrator "$HOME/.claude/skills/"
cp agents/*.md "$HOME/.claude/agents/"
```

If any target already exists, compare or back it up before replacing it. Restart Claude Code after installation so it reloads the agent definitions.

## Verify

```bash
shasum -a 256 -c SHA256SUMS
python3 "$HOME/.claude/skills/fable-orchestrator/scripts/model_feedback.py" self-test
python3 "$HOME/.claude/skills/fable-orchestrator/scripts/react_doctor.py" self-test
python3 -m unittest discover -s "$HOME/.claude/skills/fable-orchestrator/tests" -v
claude plugin validate "$HOME/.claude/agents"
```

## Use

```text
/fable-orchestrator <task>
```

The skill is explicit-only. Fable 5.1 orchestrates, plans, verifies, and audits; Sonnet or Opus 5 implements according to risk. React Doctor prefers an existing local installation. Its network fallback requires approval and is pinned to `react-doctor@0.9.12`.
