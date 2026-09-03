# Fable Orchestrator for Claude Code

Portable export updated on 2026-09-03. It contains the skill, four global agents, deterministic helpers, and regression tests. It does not contain routing history, transcripts, project code, caches, snapshots, or credentials.

Licensed under the [MIT License](LICENSE).

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

Run everything from this directory; `SHA256SUMS` uses relative paths.

```bash
shasum -a 256 -c SHA256SUMS
PYTHONDONTWRITEBYTECODE=1 python3 skills/fable-orchestrator/scripts/model_feedback.py self-test
PYTHONDONTWRITEBYTECODE=1 python3 skills/fable-orchestrator/scripts/react_doctor.py self-test
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s skills/fable-orchestrator/tests -v
claude plugin validate --strict agents
claude plugin validate --strict skills
```

The unittest suite includes the agent frontmatter and model-id consistency tests. `claude plugin
validate` only catches unparsable YAML and a non-string `name`; it does not validate `model`,
`tools`, or `maxTurns` — the unittest suite does.

After installing, confirm the installed copies still match this export:

```bash
diff -rq -x __pycache__ skills/fable-orchestrator "$HOME/.claude/skills/fable-orchestrator"
for agent in agents/*.md; do cmp "$agent" "$HOME/.claude/agents/$(basename "$agent")"; done
```

The test suite also passes from the installed copy:
`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s "$HOME/.claude/skills/fable-orchestrator/tests" -v`.
The two package-only checks (exact `agents/` contents and this README) skip there because
`~/.claude/agents` holds other agents and `~/.claude/README.md` belongs to another project.

## Use

```text
/fable-orchestrator <task>
```

The skill is explicit-only. Fable 5.1 orchestrates, plans, verifies, and audits; Sonnet or Opus 5
implements according to risk. React Doctor prefers an existing local installation. Its network
fallback requires approval and is pinned to `react-doctor@0.9.12`.

A verification-only request follows the same workflow: the planner still emits an executor token and
one executor runs the checks, writing only inside the session scratchpad. Such a run is recorded with
`outcome: pass`, `files_changed: 0`, and a controlled tag such as `verification`.

## Environment

- `CLAUDE_MODEL_FEEDBACK_PATH` — routing history file. Defaults to
  `~/.claude/model-routing/history.jsonl`; it is append-only JSONL created with mode `0600`. Point it
  at a scratch file when testing so the real history stays untouched.
- `CLAUDE_CODE_SUBAGENT_MODEL` — must be unset. The skill records a blocked run and stops when a
  per-invocation model override is present, because strict routing cannot be guaranteed.
- `CLAUDE_SKILL_DIR` and `CLAUDE_SESSION_ID` are provided by Claude Code and are used to locate the
  scripts and the current session.
- `actual-model` reads `~/.claude/projects/<project>/<session>/subagents/*.meta.json` and the matching
  `.jsonl` transcript; it prints the reason for an `unknown` result on stderr and only the alias on
  stdout.

## Known limitations

- `actual-model` merges every subagent of the same `agentType` within a session, because `meta.json`
  carries no timestamp to tell one delegation from the next.
- Two executors of the same type running different models therefore return `unknown` on purpose: an
  ambiguous result must block acceptance instead of guessing.

## Changelog

### 2026-09-03

- `recorded_at` is validated as an ISO-8601 UTC timestamp and is always discarded from `record`
  stdin, so a caller cannot backdate history.
- Tags are restricted to lowercase ASCII labels (`^[a-z0-9][a-z0-9-]{0,31}$`).
- `actual-model` explains every `unknown` on stderr and warns about unreadable metadata or missing
  transcripts by file name; a `meta.json` that is not an object no longer crashes it.
- `record` and `fallback` fail cleanly with `feedback NOT recorded: <reason>` and exit 1 instead of
  raising a traceback, including unhashable enum values and a history file that cannot be written;
  a missing enum now reports `missing <key>`.
- `summary --exclude-run-id <run-id>` keeps the current run out of the evidence given to the planner.
- The React Doctor wrapper also treats `npm x` and `bun x` scripts as network runners.
- `compare` exits 2 with the wrapper error message on malformed or non-object snapshots.
- `write_json` creates snapshots directly with mode `0600` instead of relying on `chmod`.
- `write_json` refuses to follow a symlink at the snapshot path (`O_NOFOLLOW`).
- Model ids live in `EXPECTED_MODEL_IDS`, and the test suite checks the agent frontmatter, SKILL.md,
  and README against them.
- The frontmatter test suite detects whether it runs from the export or from an installed copy
  (`SHA256SUMS` marker) and skips the two package-only checks when installed.
- Test coverage of both helper scripts is above 80%.
- SKILL.md and README document the `BLOCKED` recording path, verification-only runs, the environment
  variables, and the known `actual-model` limitations.

## License

[MIT](LICENSE) &copy; 2026 Latinovation.
