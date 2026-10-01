# AGENTS.md — Operating Instructions

Instructions for any coding agent working in this repository. They are not optional.

## Start here

1. Read `PROJECT.md` before any work. It holds the project context, the sequencing of
   tasks, the technical decisions in force, the open owner decisions and the known
   defects.
2. Read the code you are about to change before changing it.

## Scope and branches

- One task, one branch, one merge. Never extend the current task's scope into another
  task. If you believe the scope must grow, stop and ask the owner.
- Name every branch `task/<short-name>`, created from the project's working branch.
- Do not create folders, worktrees or clones.

## Running commands

- Every check, test, build check or script that measures or asserts a result must be
  run through the wrapper, never directly:

  `~/ai-tools/ask-architect/run-check <name> -- <command>`

- Run only the checks that cover your change.
- Never wait for CI. Never run the full test suite more than once per task, at the end.

## Escalation

- When `run-check` prints `ARCHITECT_REQUIRED`, it refuses every further check until
  the architect is consulted. Call the architect before making any other change, and
  follow its decision.
- Escalate also when a failure survives two different fixes, or when an architecture
  question is unclear.
- If the architect prints `ARCHITECT_BUDGET_EXHAUSTED`, stop and ask the owner.

## Tests

- pytest is the single test runner.
- Never change what a test expects in order to make it pass. That requires the owner's
  approval.
- An unproven result must never be labelled "infeasible". Handle timeout and unknown
  solver statuses explicitly.

## Forbidden actions

- Never force-push. Never push to main.
- Never use `--force`, `--no-verify`, `rm -rf`, or any command that can discard
  uncommitted work or skip a gate.
- Never remove or edit a git hook.
- Never commit secrets.
- Run one destructive step per command, and only after verifying that the previous
  step succeeded.

## Before pushing

1. Commit your work.
2. Send a short change summary to the architect for review.
3. Push.
4. If the review raises a problem, fix it, commit, and send a new summary. After two
   review rounds that still raise a problem, stop and ask the owner.

## Environment setup

An interpreter inside the project keeps the environment local and reproducible.

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt pytest
```

Run the app with:

```
.venv/bin/streamlit run kitchen_app.py
```

List `pytest` on the install line explicitly, because it is a test dependency and is
not declared in `requirements.txt`. Do not edit configuration files just to run a
preview; pass command-line options instead.

## Privacy and data

- Default to loopback-only operation. Do not deploy remotely.
- Do not introduce a shared-password gate as a privacy solution.
- Never write employee data silently to server disk.
- Keep secrets out of version control, and keep ignore rules covering secret files,
  exports and local data stores.
- Decisions about repository visibility, who may access the planner, whether employee
  names may leave the owner's machines, and whether backups or logs may hold personal
  data belong to the owner alone. Do not act on them unilaterally.

## Reporting

Report when done, and after 15 minutes without a finished result. State what changed,
what is left, which checks were run and their results, and any questions for the owner.
