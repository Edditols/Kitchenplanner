# Kitchenplanner — Project Context

## What this is

Kitchenplanner generates an optimised weekly rota for kitchen staff in a restaurant.
It is a single-file Streamlit web application, `kitchen_app.py`, that models the
staffing problem with Google OR-Tools CP-SAT and displays the resulting schedule.

The repository is `Edditols/Kitchenplanner`.

## Who it is for

The intended user is the restaurant operator (the repository owner) who needs a
feasible weekly plan for kitchen staff without building it by hand.

Who must be able to reach the planner, and from where, is not yet decided. That
decision belongs to the owner.

## What it models

- Planning horizon: 7 days, Monday to Sunday.
- Each day covers 14 hourly slots, 10:00 to 23:00.
- Roles: Cuisinier, Pizzaiolo, Plongeur. A worker holds at most one role in any hour.
- Employees are entered in a data editor: name, skill flags per role, weekly maximum
  hours, and maximum breaks ("Coupures Max").
- Hourly staffing demand per role is entered in a separate editor, one table per role.
- Employee count is chosen with a slider between 2 and 12; the default is 6 workers,
  all skills enabled, 42 weekly hours, 3 maximum breaks.

Constraints encoded in the model: staffing demand met exactly per role and hour, one
role per worker per hour, no role change within a day, a maximum of 10 hours per day,
a minimum work block of 3 hours, a limit on the number of work blocks per day derived
from "Coupures Max", a weekly hour cap per employee, at least one pair of consecutive
days off, and skill eligibility.

The solver runs with a 20-second time limit. The interface text is English while most
data column labels and the page title are French.

## Current state

After `task/harden-app`:

- `planner.py` holds the whole scheduling engine: the data model, input parsing and
  validation, the CP-SAT model, the solve call and the reporting helpers. It imports
  no Streamlit and holds no session state.
- `kitchen_app.py` is a thin Streamlit adapter: it collects input, calls the engine
  and renders the result. It contains no constraint code.
- `tests/test_planner.py` holds 30 pytest regression tests. Run them with
  `.venv/bin/python -m pytest tests`, through the `run-check` wrapper.
- `PROJECT.md`, `AGENTS.md` and `.gitignore` exist. The virtualenv in `.venv/` is
  ignored and never committed.
- Still absent: continuous integration, packaging, persistence, authentication and a
  licence.
- `requirements.txt` declares `streamlit`, `ortools==9.14.6206`, `pandas` and
  `openpyxl`. Only `ortools` is pinned, no Python version is pinned, and pytest is not
  declared at all.
- `README.md` is still a single line: "Planning cuisine automatisé avec Streamlit +
  OR-Tools."
- The GitHub repository is currently public.

## Known defects and their status

Status as of `task/harden-app`.

1. **Degenerate objective — removed by this task.** Demand is a hard equality per role
   and hour, so the total number of worked shifts is a constant fixed by the demand.
   `Minimize(total_shifts)` therefore could not change any result. It has been removed,
   and the module now documents that this is a pure feasibility problem: any valid
   schedule may be returned, and different runs may legitimately return different ones.
2. **Blank numeric inputs — fixed by this task.** A blank "Heures Max" or "Coupures
   Max" used to reach `int()` as NaN and raise. `planner.parse_employees` now rejects
   blank, non-numeric and negative values with a readable per-employee message, and the
   solver is not invoked.
3. **Input edits are discarded — open.** The employee dataframe is still rebuilt
   whenever the employee-count slider changes, so names and edits are lost. That is
   `kitchen_app.py` behaviour, deliberately preserved by this task, and it belongs to
   the "correctness fixes and session-state preservation" task.
4. **Role changes across a split — open, inherited.** The "no role change within a day"
   rule is only enforced between two consecutive *worked* hours, so a worker whose day
   contains a gap between two blocks may hold a different role in each block.
   `tests/test_planner.py` characterises this in
   `test_a_role_may_change_across_a_gap_today`. Fixing it changes which schedules are
   accepted and belongs to a later correctness task.
5. **Unused dependency — open.** `openpyxl` is declared in `requirements.txt` but never
   imported anywhere in the code.
6. **Unpinned environment — open.** Only `ortools` is pinned; `streamlit`, `pandas` and
   `openpyxl` float, and no Python version is declared. pytest is a test dependency and
   is not declared at all.

## Sequencing of work

Work is split into separate tasks. Each task gets its own fresh branch and produces
one merge. A task's scope must never be extended into another task.

The current task, `task/harden-app`, is scoped to exactly three things:

1. Extract the solver into a pure, importable module.
2. Add input validation.
3. Add regression tests.

Existing scheduling semantics must be preserved by this task. It is not a correctness
fix and not a feature change.

Later tasks, in this order:

1. Correctness fixes and session-state preservation.
2. Packaging, reproducible dependencies, continuous integration, documentation.
3. Privacy and deployment requirements, then approved access controls.
4. Export/import and French user experience.
5. Approved persistence and deployment.

Critical path: extract the solver, characterise the constraints, fix verified defects,
add CI, obtain approval of privacy requirements, then deploy. Privacy containment
applies immediately; features are not prerequisites for containment.

## Technical decisions in force

- **Module boundary.** Extract a pure solver module with a thin Streamlit adapter.
  Input validation, model construction and solving, and result presentation are
  separate concerns. The solver returns structured results including solver status and
  diagnostics. The solver module must not depend on Streamlit session state.
- **Testing.** pytest is the single test runner. The first evidence-bearing test must
  solve a small deterministic feasible instance and independently verify the returned
  assignments against demand, skills, employee limits and applicable scheduling
  constraints — not merely assert that the solver succeeded. Further tests cover
  deliberately infeasible demand, blank and invalid numeric inputs, minimum-block
  boundaries, split counting, and consecutive days off. Solver status handling must
  include timeout and unknown; an unproven result must never be labelled "infeasible".
- **Persistence.** Starts as session state plus explicit export and import, with a
  versioned JSON format for round-trip data. CSV and Excel are reporting formats only,
  unless a validated import contract exists. Uploaded data must be validated for
  structure, types, ranges and size. Employee data must never be silently written to
  server disk. SQLite is appropriate only after the owner approves durable storage,
  deployment topology, retention, backup and multi-user behaviour. Cloud storage is
  out of scope without approval.
- **Deployment and privacy.** Default to loopback-only operation and no remote
  deployment. A shared-password gate is not an assumed privacy solution and must not
  be introduced as one. If remote access is required, the owner must approve an
  existing identity provider or an authenticated hosting or reverse-proxy solution.
  Secrets stay outside version control; ignore rules must cover secret files, exports
  and local data stores.

## Open decisions owned by the owner

These are not for an agent to decide or act on:

- The visibility of the GitHub repository, currently public. It must not be changed
  without the owner's approval.
- Who must access the planner, and from where.
- Whether employee names may ever leave the owner's machines.
- Whether backups or logs may contain personal data.
- What the planner should actually optimise — fairness of hours, weekend load,
  equalising splits — or nothing at all.
- Whether a licence should be added. No licence is to be invented.
