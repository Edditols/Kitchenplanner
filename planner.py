"""Pure scheduling engine for the kitchen rota planner.

This module holds no Streamlit code, performs no I/O and keeps no global
mutable state, so it can be imported and tested on its own.

Scheduling semantics are deliberately identical to the original single-file
application. The one thing removed is the objective function, which could not
change any result: staffing demand is enforced as a hard equality per role and
hour, so the total number of worked hours is a constant fixed by the demand.
The problem is a pure feasibility problem, and the solver therefore returns
*any* valid schedule. See PROJECT.md.

KNOWN LIMITATION, inherited unchanged from the original application and not
fixed by this task: the "no role change inside a day" rule is only enforced
between two *consecutive* hours. A worker whose day contains a gap between two
work blocks may therefore hold a different role in each block, which the rule
as written in the interface forbids. Fixing it changes which schedules the
solver accepts, so it is a correctness fix belonging to a later task.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

from ortools.sat.python import cp_model

# --- Calendar shape -------------------------------------------------------

DAYS = 7
HOURS_PER_DAY = 14  # 10:00 -> 23:00
START_HOUR = 10

ROLES: tuple[str, ...] = ("Cuisinier", "Pizzaiolo", "Plongeur")

DAY_NAMES: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

HOUR_LABELS: tuple[str, ...] = tuple(
    f"{(START_HOUR + h) % 24}:00" for h in range(HOURS_PER_DAY)
)

# --- Scheduling rules -----------------------------------------------------

MIN_BLOCK_HOURS = 3
MAX_HOURS_PER_DAY = 10
MIN_EMPLOYEES = 1
MAX_EMPLOYEES = 12
DEFAULT_SOLVER_TIME_LIMIT_S = 20.0

#: Column headers of the employee input table, in display order.
EMPLOYEE_FIELDS: tuple[str, ...] = (
    "Nom",
    *ROLES,
    "Heures Max",
    "Coupures Max",
)


class ValidationError(ValueError):
    """The problem description cannot be scheduled as given."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors: tuple[str, ...] = tuple(errors)
        super().__init__("; ".join(self.errors))


class SolverOutcome(str, Enum):
    """What the solver actually proved. Never claim more than this."""

    OPTIMAL = "optimal"
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    UNKNOWN = "unknown"
    MODEL_INVALID = "model_invalid"

    @property
    def solved(self) -> bool:
        """True only for an outcome that carries a usable schedule."""
        return self in (SolverOutcome.OPTIMAL, SolverOutcome.FEASIBLE)


# --- Data model -----------------------------------------------------------


@dataclass(frozen=True)
class Employee:
    """One member of staff and their contractual limits."""

    name: str
    skills: frozenset[str]
    max_hours: int
    max_splits: int

    def can(self, role: str) -> bool:
        return role in self.skills


@dataclass(frozen=True)
class Problem:
    """A complete scheduling request.

    ``needs[role][day][hour]`` is the number of people required for that role,
    on that day, at that hour.
    """

    employees: tuple[Employee, ...]
    needs: Mapping[str, Sequence[Sequence[int]]]

    def need(self, role: str, day: int, hour: int) -> int:
        return int(self.needs[role][day][hour])


@dataclass(frozen=True)
class EmployeeSummary:
    """Per-employee totals computed from a solved schedule."""

    name: str
    total_hours: int
    days_worked: int
    splits: int
    max_consecutive_days_off: int
    average_hours_per_day: float


@dataclass(frozen=True)
class Schedule:
    """The result of one solve attempt.

    ``assignments[employee][day][hour]`` is the role worked, or ``None``.
    It is empty unless :attr:`outcome` is a solved outcome.
    """

    outcome: SolverOutcome
    problem: Problem
    assignments: tuple[tuple[tuple[str | None, ...], ...], ...] = ()
    summaries: tuple[EmployeeSummary, ...] = ()
    diagnostics: Mapping[str, object] = field(default_factory=dict)

    @property
    def solved(self) -> bool:
        return self.outcome.solved

    def role_at(self, employee: int, day: int, hour: int) -> str | None:
        return self.assignments[employee][day][hour]


# --- Input parsing and validation -----------------------------------------


def _is_missing(value: object) -> bool:
    """True for a blank table cell, whatever a spreadsheet hands us."""
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # NaN
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _as_bool(value: object) -> bool:
    if _is_missing(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "vrai", "yes", "oui", "x"}
    return bool(value)


def _as_int(value: object) -> int | None:
    """Return an int, or None when the cell is blank or not a whole number."""
    if _is_missing(value):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            try:
                number = float(text)
            except ValueError:
                return None
            return int(number) if number.is_integer() else None
    return None


def parse_employees(
    rows: Iterable[Mapping[str, object]],
) -> tuple[tuple[Employee, ...], list[str]]:
    """Turn raw input rows into Employees, collecting readable errors.

    Accepts whatever a Streamlit data editor produces, including blank cells
    and NaN, and never raises on bad input: it returns every problem it found
    so the caller can show them all at once.
    """
    employees: list[Employee] = []
    errors: list[str] = []

    for position, row in enumerate(rows, start=1):
        label = f"Row {position}"
        raw_name = row.get("Nom")
        name = str(raw_name).strip() if not _is_missing(raw_name) else ""
        if not name:
            name = f"Emp{position}"
            errors.append(f"{label}: the name is empty, using '{name}'.")
        label = f"'{name}'"

        max_hours = _as_int(row.get("Heures Max"))
        if max_hours is None:
            errors.append(f"{label}: 'Heures Max' is blank or not a whole number.")
            max_hours = 0
        elif max_hours < 0:
            errors.append(f"{label}: 'Heures Max' cannot be negative.")

        max_splits = _as_int(row.get("Coupures Max"))
        if max_splits is None:
            errors.append(f"{label}: 'Coupures Max' is blank or not a whole number.")
            max_splits = 0
        elif max_splits < 0:
            errors.append(f"{label}: 'Coupures Max' cannot be negative.")

        skills = frozenset(role for role in ROLES if _as_bool(row.get(role)))
        if not skills:
            errors.append(f"{label}: no role is selected, this person cannot work.")

        employees.append(
            Employee(
                name=name,
                skills=skills,
                max_hours=max(max_hours, 0),
                max_splits=max(max_splits, 0),
            )
        )

    duplicate_names = sorted(
        {name for name in (e.name for e in employees) if [e.name for e in employees].count(name) > 1}
    )
    for name in duplicate_names:
        errors.append(f"'{name}' appears more than once, names must be unique.")

    return tuple(employees), errors


def validate(problem: Problem) -> list[str]:
    """Return every reason this problem cannot be scheduled.

    These are necessary conditions, reported before the solver runs so the
    user gets a readable message instead of a bare "no solution". Passing
    them does not guarantee a schedule exists.
    """
    errors: list[str] = []
    employees = problem.employees

    if not employees:
        errors.append("There is no employee to schedule.")
    if len(employees) > MAX_EMPLOYEES:
        errors.append(f"At most {MAX_EMPLOYEES} employees are supported.")

    for role in ROLES:
        if role not in problem.needs:
            errors.append(f"The staffing needs for '{role}' are missing.")

    if errors:
        return errors

    for role in ROLES:
        grid = problem.needs[role]
        if len(grid) != DAYS:
            errors.append(
                f"'{role}': expected {DAYS} days of needs, found {len(grid)}."
            )
            continue
        for day_index, row in enumerate(grid):
            if len(row) != HOURS_PER_DAY:
                errors.append(
                    f"'{role}', {DAY_NAMES[day_index]}: expected "
                    f"{HOURS_PER_DAY} hours, found {len(row)}."
                )
                continue
            for hour_index, value in enumerate(row):
                if _as_int(value) is None or int(value) < 0:
                    errors.append(
                        f"'{role}', {DAY_NAMES[day_index]} "
                        f"{HOUR_LABELS[hour_index]}: the need must be a "
                        "whole number and cannot be negative."
                    )

    if errors:
        return errors

    # Necessary capacity conditions, so an impossible request is named
    # precisely instead of surfacing later as an unexplained infeasibility.
    demand_by_role = {role: 0 for role in ROLES}
    demand_total = 0
    for day in range(DAYS):
        for hour in range(HOURS_PER_DAY):
            hour_total = 0
            for role in ROLES:
                need = problem.need(role, day, hour)
                demand_by_role[role] += need
                hour_total += need
                capable = sum(1 for e in employees if e.can(role))
                if need > capable:
                    errors.append(
                        f"{DAY_NAMES[day]} {HOUR_LABELS[hour]}: '{role}' needs "
                        f"{need} people but only {capable} have that skill."
                    )
            demand_total += hour_total
            if hour_total > len(employees):
                errors.append(
                    f"{DAY_NAMES[day]} {HOUR_LABELS[hour]}: {hour_total} people "
                    f"are needed but there are only {len(employees)} employees."
                )

    total_capacity = sum(e.max_hours for e in employees)
    if demand_total > total_capacity:
        errors.append(
            f"The week needs {demand_total} worked hours but the team can only "
            f"work {total_capacity}."
        )

    for role in ROLES:
        if demand_by_role[role] and not any(e.can(role) for e in employees):
            errors.append(f"'{role}' is needed but nobody has that skill.")

    return errors


# --- Default staffing needs ----------------------------------------------


def default_needs() -> dict[str, tuple[tuple[int, ...], ...]]:
    """The demand curve the application ships with.

    Busy from 10:00 to 15:00 and from 18:00 to 22:00; only a cook is needed
    from 16:00 to 18:00; nothing after 22:00.
    """
    needs: dict[str, tuple[tuple[int, ...], ...]] = {}
    for role in ROLES:
        hours: list[int] = []
        for hour in range(HOURS_PER_DAY):
            if 0 <= hour <= 5 or 8 <= hour <= 12:
                hours.append(1)
            elif 6 <= hour <= 7:
                hours.append(1 if role == "Cuisinier" else 0)
            else:
                hours.append(0)
        needs[role] = tuple(tuple(hours) for _ in range(DAYS))
    return needs


# --- Model and solve ------------------------------------------------------


def _build_model(problem: Problem) -> tuple[cp_model.CpModel, dict]:
    """Build the CP-SAT model. Returns the model and its variable maps."""
    employees = problem.employees
    worker_count = len(employees)
    slots = DAYS * HOURS_PER_DAY

    def index(day: int, hour: int) -> int:
        return day * HOURS_PER_DAY + hour

    model = cp_model.CpModel()

    # shifts[(w, role)][t] is true when worker w works that role at slot t.
    shifts = {
        (w, role): [model.NewBoolVar(f"w{w}_{role}_{t}") for t in range(slots)]
        for w in range(worker_count)
        for role in ROLES
    }

    # is_off[w][d] is true when worker w does not work at all on day d.
    is_off: dict[int, list] = {w: [] for w in range(worker_count)}
    for w in range(worker_count):
        for day in range(DAYS):
            off = model.NewBoolVar(f"off_{w}_{day}")
            day_total = sum(
                shifts[(w, role)][index(day, hour)]
                for role in ROLES
                for hour in range(HOURS_PER_DAY)
            )
            model.Add(day_total == 0).OnlyEnforceIf(off)
            # Anyone who works at all that day works at least one full block.
            model.Add(day_total >= MIN_BLOCK_HOURS).OnlyEnforceIf(off.Not())
            is_off[w].append(off)

    for w in range(worker_count):
        # At most one role in any given hour.
        for t in range(slots):
            model.Add(sum(shifts[(w, role)][t] for role in ROLES) <= 1)

        # No role change inside a day.
        for day in range(DAYS):
            for hour in range(HOURS_PER_DAY - 1):
                here, then = index(day, hour), index(day, hour + 1)
                for first in ROLES:
                    for second in ROLES:
                        if first != second:
                            model.Add(
                                shifts[(w, first)][here]
                                + shifts[(w, second)][then]
                                <= 1
                            )

        max_splits = employees[w].max_splits
        for day in range(DAYS):
            day_total = sum(
                shifts[(w, role)][index(day, hour)]
                for role in ROLES
                for hour in range(HOURS_PER_DAY)
            )
            model.Add(day_total <= MAX_HOURS_PER_DAY)

            starts = []
            for hour in range(HOURS_PER_DAY):
                t = index(day, hour)
                current = sum(shifts[(w, role)][t] for role in ROLES)
                start = model.NewBoolVar(f"start_{w}_{day}_{hour}")

                if hour == 0:
                    model.Add(current == 1).OnlyEnforceIf(start)
                    model.Add(current != 1).OnlyEnforceIf(start.Not())
                else:
                    previous = sum(
                        shifts[(w, role)][index(day, hour - 1)] for role in ROLES
                    )
                    model.Add(current - previous == 1).OnlyEnforceIf(start)
                    model.Add(current - previous != 1).OnlyEnforceIf(start.Not())

                if hour <= HOURS_PER_DAY - MIN_BLOCK_HOURS:
                    # A block that starts here must run at least MIN_BLOCK_HOURS.
                    block = sum(
                        shifts[(w, role)][index(day, other)]
                        for role in ROLES
                        for other in range(hour, hour + MIN_BLOCK_HOURS)
                    )
                    model.Add(block >= MIN_BLOCK_HOURS).OnlyEnforceIf(start)
                else:
                    # Too little of the day is left to fit a whole block.
                    model.Add(start == 0)

                starts.append(start)

            # One block per allowed split, so starts <= splits + 1.
            model.Add(sum(starts) <= max_splits + 1)

    for w in range(worker_count):
        # At least one run of two consecutive days off.
        pairs = []
        for day in range(DAYS - 1):
            pair = model.NewBoolVar(f"two_off_{w}_{day}")
            model.AddBoolAnd([is_off[w][day], is_off[w][day + 1]]).OnlyEnforceIf(pair)
            model.AddBoolOr(
                [is_off[w][day].Not(), is_off[w][day + 1].Not()]
            ).OnlyEnforceIf(pair.Not())
            pairs.append(pair)
        model.Add(sum(pairs) >= 1)

        # Weekly cap.
        week_total = sum(
            shifts[(w, role)][t] for role in ROLES for t in range(slots)
        )
        model.Add(week_total <= employees[w].max_hours)

        # Skills are hard: no assignment outside the declared skills.
        for role in ROLES:
            if not employees[w].can(role):
                for t in range(slots):
                    model.Add(shifts[(w, role)][t] == 0)

    # Demand is met exactly.
    for day in range(DAYS):
        for hour in range(HOURS_PER_DAY):
            t = index(day, hour)
            for role in ROLES:
                required = problem.need(role, day, hour)
                assigned = sum(shifts[(w, role)][t] for w in range(worker_count))
                model.Add(assigned == required)

    return model, {"shifts": shifts, "worker_count": worker_count}


def _summaries(
    problem: Problem, assignments: tuple[tuple[tuple[str | None, ...], ...], ...]
) -> tuple[EmployeeSummary, ...]:
    summaries = []
    for w, employee in enumerate(problem.employees):
        total_hours = 0
        days_worked = 0
        splits = 0
        max_days_off = 0
        current_days_off = 0

        for day in range(DAYS):
            worked = [
                hour
                for hour in range(HOURS_PER_DAY)
                if assignments[w][day][hour] is not None
            ]
            if worked:
                days_worked += 1
                total_hours += len(worked)
                # A gap between the first and last hour worked is a split.
                if len(worked) < worked[-1] - worked[0] + 1:
                    splits += 1
                current_days_off = 0
            else:
                current_days_off += 1
                max_days_off = max(max_days_off, current_days_off)

        summaries.append(
            EmployeeSummary(
                name=employee.name,
                total_hours=total_hours,
                days_worked=days_worked,
                splits=splits,
                max_consecutive_days_off=max_days_off,
                average_hours_per_day=(
                    round(total_hours / days_worked, 2) if days_worked else 0.0
                ),
            )
        )
    return tuple(summaries)


def solve(
    problem: Problem,
    *,
    time_limit_s: float = DEFAULT_SOLVER_TIME_LIMIT_S,
    random_seed: int | None = None,
) -> Schedule:
    """Solve the rota. Raises ValidationError if the input is unusable.

    There is no objective: demand is a hard equality, so any solution already
    works exactly the number of hours the demand dictates. The solver returns
    the first valid schedule it finds, and different runs, solver versions or
    settings may legitimately return different valid schedules.
    """
    errors = validate(problem)
    if errors:
        raise ValidationError(errors)

    model, variables = _build_model(problem)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_s)
    if random_seed is not None:
        solver.parameters.random_seed = int(random_seed)

    status = solver.Solve(model)
    outcome = SolverOutcome(solver.StatusName(status).lower().replace(" ", "_"))

    diagnostics: dict[str, object] = {
        "status": solver.StatusName(status),
        "wall_time_s": round(solver.WallTime(), 3),
        "time_limit_s": float(time_limit_s),
        "num_conflicts": solver.NumConflicts(),
        "num_branches": solver.NumBranches(),
    }

    if not outcome.solved:
        return Schedule(outcome=outcome, problem=problem, diagnostics=diagnostics)

    shifts = variables["shifts"]
    worker_count = variables["worker_count"]

    assignments = tuple(
        tuple(
            tuple(
                next(
                    (
                        role
                        for role in ROLES
                        if solver.Value(shifts[(w, role)][day * HOURS_PER_DAY + hour])
                    ),
                    None,
                )
                for hour in range(HOURS_PER_DAY)
            )
            for day in range(DAYS)
        )
        for w in range(worker_count)
    )

    return Schedule(
        outcome=outcome,
        problem=problem,
        assignments=assignments,
        summaries=_summaries(problem, assignments),
        diagnostics=diagnostics,
    )


# --- Reporting helpers (pure Python, no pandas) ---------------------------


def weekly_rows(problem: Problem, schedule: Schedule) -> list[dict[str, object]]:
    """One row per employee and day, one column per hour label."""
    if not schedule.solved:
        return []
    rows: list[dict[str, object]] = []
    for w, employee in enumerate(problem.employees):
        for day in range(DAYS):
            row: dict[str, object] = {
                "Employé": employee.name,
                "Jour": DAY_NAMES[day],
            }
            for hour in range(HOURS_PER_DAY):
                row[HOUR_LABELS[hour]] = schedule.assignments[w][day][hour] or ""
            rows.append(row)
    return rows


def summary_rows(schedule: Schedule) -> list[dict[str, object]]:
    """One row per employee summarising their week."""
    return [
        {
            "Employé": summary.name,
            "Heures/semaine": summary.total_hours,
            "Jours travaillés": summary.days_worked,
            "Coupures/semaine": summary.splits,
            "Jours OFF cons. max": summary.max_consecutive_days_off,
            "H/jour en moyenne": summary.average_hours_per_day,
        }
        for summary in schedule.summaries
    ]
