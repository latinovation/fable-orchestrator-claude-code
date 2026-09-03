---
name: fable-orchestrator
description: "Plan, implement, verify, audit, and learn from a coding task with strict model separation: Fable 5.1 orchestrates, plans, verifies, and audits; Sonnet or Opus 5 implements according to measured risk. Invoke explicitly when this workflow is wanted."
disable-model-invocation: true
argument-hint: "<task to implement>"
model: claude-fable-5-1
---

# Fable Orchestrator

Orchestrate `$ARGUMENTS`; never implement in the main conversation. If the argument is empty, use the immediately preceding explicit user request only when unambiguous; otherwise ask for the task and stop.

## Invariants

- Fable 5.1 owns orchestration, investigation, planning, independent verification, and audit. Fable never edits project files or implements fixes.
- Only `sonnet-executor` or the Opus 5-backed `opus-executor` may edit, following the approved plan or concrete audit findings.
- Never pass a per-invocation model override. If `CLAUDE_CODE_SUBAGENT_MODEL` is set, record a blocked run and stop because strict routing cannot be guaranteed.
- Runtime transcript metadata must confirm every subagent model. A mismatch or `unknown` result blocks acceptance.
- Start feedback before delegation. A terminated session must remain visible as incomplete rather than disappear from history.

## Workflow

1. Start the run before other work and retain the returned ID:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/model_feedback.py" begin --session-id "${CLAUDE_SESSION_ID}"
   ```

2. Record `git status --short`, `git rev-parse HEAD` when available, and relevant project instructions. Preserve pre-existing work.
3. Check `CLAUDE_CODE_SUBAGENT_MODEL`. On an override, use `model_feedback.py fallback --stage started --outcome blocked` with the run ID and stop.
4. Detect React/Next.js from `package.json`. When applicable, run a structured baseline:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/react_doctor.py" scan --project "$PWD" --base <starting-commit> --output "${TMPDIR:-/tmp}/<run-id>-react-baseline.json"
   ```

   Omit `--base` outside Git. The wrapper prefers a non-downloading project script, local binary, or installed command. If none exists, ask before retrying with `--allow-pinned-npx`; that fallback is pinned and may download a package. If unavailable or declined, continue with React Doctor marked `not-run`.
5. Run `model_feedback.py summary --exclude-run-id <run-id>` and include the complete output under `HISTORICAL ROUTING EVIDENCE` for `fable-planner`.
6. Delegate the original request, constraints, repository evidence, and history to `fable-planner`, without a model override. Require its classification, controlled tags, plan, and exact final routing token.
7. Confirm the planner runtime model:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/model_feedback.py" actual-model --session-id "${CLAUDE_SESSION_ID}" --agent-type fable-planner --expected-model-id claude-fable-5-1
   ```

   It must return `fable`; otherwise record a planner-stage fallback and stop. `actual-model` prints the reason for `unknown` on stderr; quote it in the blocked report.
8. Delegate the plan and original request to exactly one executor:
   - `EXECUTION: sonnet` -> `sonnet-executor`
   - `EXECUTION: opus` -> `opus-executor`
9. Confirm the executor with `actual-model` and its exact agent type. For `opus-executor`, also pass `--expected-model-id claude-opus-5`; Sonnet remains on its latest-model alias. A mismatch blocks acceptance; preserve its changes for review and never conceal the mismatch. `actual-model` prints the reason for `unknown` on stderr; quote it in the blocked report.
10. Fable independently reruns checks proportional to the risk. For React/Next where baseline ran, rerun `react_doctor.py scan` with the same base into the final snapshot in the same directory, then run:

    ```bash
    python3 "${CLAUDE_SKILL_DIR}/scripts/react_doctor.py" compare <baseline.json> <final.json>
    ```

    Only a comparable same-version result may supply a score delta. New finding fingerprints, not repository-wide debt, determine regression.
11. Delegate the request, plan, starting status, current diff, runtime-confirmed models, checks, repair history, and React Doctor comparison to a fresh `fable-auditor`. The auditor is statically read-only and receives evidence rather than Bash access.
12. Confirm `fable-auditor` with `actual-model --expected-model-id claude-fable-5-1`; require `fable`. `actual-model` prints the reason for `unknown` on stderr; quote it in the blocked report. On `FIX_REQUIRED`, send only concrete findings to the same selected executor model, rerun affected checks and React Doctor, then use a fresh auditor. Allow at most two repair cycles. `BLOCKED` ends the run: record it with `fallback --run-id <run-id> --stage auditor --outcome blocked` (never `record`). An unresolved second repair is recorded with `record` using the auditor's `PERFORMANCE_JSON` (`outcome: fix_required`). Neither may claim success.
13. Record the final `PERFORMANCE_JSON`, overriding self-reported runtime models with transcript evidence:

    ```bash
    python3 "${CLAUDE_SKILL_DIR}/scripts/model_feedback.py" record --run-id <run-id> --planner-actual-model fable --actual-model <sonnet-or-opus> --auditor-actual-model fable <<'JSON'
    {PERFORMANCE_JSON object only}
    JSON
    ```

    For any handled failure before final audit, or a final `BLOCKED` verdict, call `model_feedback.py fallback --run-id <run-id> --stage <started|planner|executor|auditor> --task-class <class-or-unknown> --planned-model <model-or-unknown> --actual-model <model-or-unknown> --planner-actual-model <model-or-unknown> --outcome <blocked|incomplete>`. If execution terminates unexpectedly, the initial incomplete record remains the fallback automatically.
14. Finish with requested and runtime-confirmed models, checks, React Doctor version/baseline/final/delta, verdict, repair count, and feedback status.

## Verification-only tasks

A request that only verifies existing work still follows the same workflow: the planner emits an executor token and exactly one executor runs the checks. That executor writes only inside the session scratchpad, and any proof-of-concept run of these scripts must point `CLAUDE_MODEL_FEEDBACK_PATH` at that scratchpad so the real history stays untouched. Record the run normally with `outcome: pass`, `files_changed: 0`, and a controlled tag such as `verification`.

## Routing

Use Sonnet only for bounded, familiar, low-risk work with clear acceptance criteria. Use Opus for any cross-cutting behavior, more than three implementation files, ambiguity, unfamiliar code, risky refactoring/debugging, weak tests, or architecture/data/migration/auth/security/privacy/concurrency/money-sensitive logic. When uncertain, use Opus.

History may promote a borderline task to Opus. It may suggest Sonnet only after at least three completed Opus runs with the same task class and exact controlled tag set that each passed with zero material findings and were rated `overpowered`. History never overrides a hard Opus trigger.

## Boundaries

- Do not push, publish, deploy, merge, modify credentials, or mutate third-party systems without explicit approval.
- Store only controlled metrics and tags in routing history—never prompts, task text, source code, paths, secrets, credentials, or personal data.
- React Doctor is an additional React-specific signal, not a replacement for tests, type checks, lint, builds, security review, or functional verification.
- Keep implementation and verification proportional; do not add speculative infrastructure.
