"""Regression tests for the pure scheduling engine in :mod:`planner`.

The point of these tests is evidence, not decoration: the solver returns *any*
valid schedule, so every assertion here checks a property of the returned
assignments grid against the raw demand, the employee limits and the ten
scheduling rules. Nothing is asserted about a specific rota.

All solver calls use a small time limit and an explicit ``random_seed`` so runs
are reproducible, and every instance is tiny (at most three employees).

Inherited limitation of the engine, documented in ``planner.py`` and left
unchanged by this task: the "no role change inside a day" rule is enforced only
between two *consecutive worked hours*. A worker whose day contains a gap
between two blocks may therefore hold a different role in each block.
``assert_schedule_is_valid`` checks the rule as it is actually enforced, and
``test_a_role_may_change_across_a_gap_today`` characterises the gap case so the
behaviour cannot change unnoticed. The other instances here are built so the
solver has no reason to produce such a day.
"""

from __future__ import annotations

import pytest

import planner

TINY_TIME_LIMIT_S = 5.0


# --- Instance builders ----------------------------------------------------


def _empty_needs() -> dict[str, list[list[int]]]:
    """A mutable 7 x 14 grid of zeros for every role."""
    return {
        role: [
            [0 for _ in range(planner.HOURS_PER_DAY)] for _ in range(planner.DAYS)
        ]
        for role in planner.ROLES
    }


def _main_problem() -> planner.Problem:
    """A small, deliberately rigid but feasible instance.

    Each day demands four Cuisinier hours (slots 0-3) immediately followed by
    four Pizzaiolo hours (slots 4-7). Because demand is an exact equality, no
    worker may be on duty outside those slots. The two blocks are adjacent in
    time, so a single worker cannot cover both without an illegal role change;
    every day therefore needs two distinct workers, each doing one contiguous
    four-hour block.

    Feasibility of the extra constraints: 7 days x 2 workers = 14 worker-days,
    and a worker with a pair of consecutive days off can work at most 5 days,
    so 5 + 5 + 4 = 14 is attainable (for example off Sat/Sun, off Mon/Tue, and
    off Wed/Thu plus Sat). Weekly totals stay far below ``max_hours``.
    """
    employees = (
        planner.Employee("Alice", frozenset(planner.ROLES), 40, 1),
        planner.Employee("Bruno", frozenset(planner.ROLES), 40, 1),
        planner.Employee("Chloe", frozenset(planner.ROLES), 40, 1),
    )
    needs = _empty_needs()
    for day in range(planner.DAYS):
        for hour in range(0, 4):
            needs["Cuisinier"][day][hour] = 1
        for hour in range(4, 8):
            needs["Pizzaiolo"][day][hour] = 1
    return planner.Problem(employees=employees, needs=needs)


def _skill_problem() -> planner.Problem:
    """One role per worker: assignment must follow the declared skills.

    Works only on days 0 and 1, so each worker has five consecutive days off
    and one block per day.
    """
    employees = (
        planner.Employee("Cuisine Only", frozenset({"Cuisinier"}), 40, 0),
        planner.Employee("Pizza Only", frozenset({"Pizzaiolo"}), 40, 0),
        planner.Employee("Dish Only", frozenset({"Plongeur"}), 40, 0),
    )
    needs = _empty_needs()
    for day in (0, 1):
        for hour in range(0, 3):
            needs["Cuisinier"][day][hour] = 1
        for hour in range(3, 6):
            needs["Pizzaiolo"][day][hour] = 1
        for hour in range(6, 9):
            needs["Plongeur"][day][hour] = 1
    return planner.Problem(employees=employees, needs=needs)


def _split_problem() -> planner.Problem:
    """Only one worker can be a Plongeur, and their day has a four-hour gap.

    The demand asks for Plongeur slots 0-2 and 8-10 on day 0. A block is
    contiguous, so the single Plongeur-capable worker must take both blocks:
    the day necessarily contains a split. The other two workers cannot work the
    Plongeur role at all.
    """
    employees = (
        planner.Employee("Solo", frozenset({"Plongeur"}), 40, 1),
        planner.Employee("Ana", frozenset({"Cuisinier"}), 40, 1),
        planner.Employee("Bob", frozenset({"Pizzaiolo"}), 40, 1),
    )
    needs = _empty_needs()
    for hour in (0, 1, 2, 8, 9, 10):
        needs["Plongeur"][0][hour] = 1
    return planner.Problem(employees=employees, needs=needs)


def _infeasible_but_valid_problem() -> planner.Problem:
    """Passes ``validate`` yet has no solution: a 3-hour block exceeds 2 hours.

    One worker must cover the three consecutive Cuisinier slots (the minimum
    block is three hours), but every worker's weekly cap is two hours. The
    total weekly capacity (4) is still above the total demand (3), so no
    necessary condition in ``validate`` is violated.
    """
    employees = (
        planner.Employee("Tiny One", frozenset(planner.ROLES), 2, 1),
        planner.Employee("Tiny Two", frozenset(planner.ROLES), 2, 1),
    )
    needs = _empty_needs()
    for hour in range(0, 3):
        needs["Cuisinier"][0][hour] = 1
    return planner.Problem(employees=employees, needs=needs)


def _one_worker_problem(worked_hours: tuple[int, ...]) -> planner.Problem:
    """One fully-skilled worker, Cuisinier demand only at ``worked_hours``."""
    employees = (planner.Employee("Only", frozenset(planner.ROLES), 40, 1),)
    needs = _empty_needs()
    for hour in worked_hours:
        needs["Cuisinier"][0][hour] = 1
    return planner.Problem(employees=employees, needs=needs)


# --- Verification helpers -------------------------------------------------


def assert_schedule_is_valid(problem: planner.Problem, schedule: planner.Schedule) -> None:
    """Assert rules 1-10 against the raw ``assignments`` grid.

    This never trusts ``schedule.solved``, the summaries or the diagnostics: it
    recomputes everything from the grid and the problem description.
    """
    assert schedule.solved, f"the schedule is not solved: {schedule.outcome}"
    assignments = schedule.assignments
    assert assignments, "a solved schedule must carry assignments"
    assert len(assignments) == len(problem.employees)

    employees = problem.employees
    day_count = planner.DAYS
    hour_count = planner.HOURS_PER_DAY

    # --- Rule 1: demand is met exactly, per day, hour and role. -----------
    for day in range(day_count):
        for hour in range(hour_count):
            for role in planner.ROLES:
                assigned = sum(
                    1
                    for w in range(len(employees))
                    if assignments[w][day][hour] == role
                )
                assert assigned == problem.need(role, day, hour), (
                    f"{planner.DAY_NAMES[day]} {planner.HOUR_LABELS[hour]}: "
                    f"role {role!r} has {assigned} assigned but "
                    f"{problem.need(role, day, hour)} are needed"
                )

    for w, employee in enumerate(employees):
        week_hours = 0
        for day in range(day_count):
            grid = assignments[w][day]
            assert len(grid) == hour_count
            day_hours = 0

            for hour in range(hour_count):
                role = grid[hour]
                # Rule 2: at most one role in any given hour. The grid holds a
                # single value per slot, so the check is that it is a role or
                # None, and that `role_at` agrees with the grid.
                assert role is None or role in planner.ROLES, (
                    f"{employee.name}, {planner.DAY_NAMES[day]} "
                    f"{planner.HOUR_LABELS[hour]}: invalid role {role!r}"
                )
                assert schedule.role_at(w, day, hour) == role
                if role is None:
                    continue
                day_hours += 1
                week_hours += 1
                # Rule 9: never assign a role the worker has no skill for.
                assert employee.can(role), (
                    f"{employee.name} is assigned {role!r} on "
                    f"{planner.DAY_NAMES[day]} {planner.HOUR_LABELS[hour]} "
                    "without that skill"
                )

            # Rule 3, as the engine actually enforces it: a worker never
            # changes role between two consecutive worked hours. This is
            # deliberately NOT "one role per day": a day split into two blocks
            # separated by a gap may hold a different role in each block. See
            # the module docstring and test_a_role_may_change_across_a_gap_today.
            for hour in range(hour_count - 1):
                here, then = grid[hour], grid[hour + 1]
                if here is not None and then is not None:
                    assert here == then, (
                        f"{employee.name} switches from {here!r} to {then!r} "
                        f"between {planner.HOUR_LABELS[hour]} and "
                        f"{planner.HOUR_LABELS[hour + 1]} on "
                        f"{planner.DAY_NAMES[day]}"
                    )

            # Rule 5: a worked day lasts at least the minimum block.
            if day_hours:
                assert day_hours >= planner.MIN_BLOCK_HOURS, (
                    f"{employee.name} works only {day_hours} hour(s) on "
                    f"{planner.DAY_NAMES[day]}"
                )
            # Rule 4: at most MAX_HOURS_PER_DAY per day.
            assert day_hours <= planner.MAX_HOURS_PER_DAY, (
                f"{employee.name} works {day_hours} hours on "
                f"{planner.DAY_NAMES[day]}, above the daily maximum"
            )

            # Rules 6 and 7: every block lasts at least MIN_BLOCK_HOURS, no
            # block starts in the last MIN_BLOCK_HOURS - 1 slots, and the
            # number of blocks is at most max_splits + 1.
            blocks = 0
            hour = 0
            while hour < hour_count:
                if grid[hour] is None:
                    hour += 1
                    continue
                start = hour
                while hour < hour_count and grid[hour] is not None:
                    hour += 1
                blocks += 1
                length = hour - start
                assert length >= planner.MIN_BLOCK_HOURS, (
                    f"{employee.name} has a {length}-hour block starting at "
                    f"{planner.HOUR_LABELS[start]} on {planner.DAY_NAMES[day]}"
                )
                assert start <= hour_count - planner.MIN_BLOCK_HOURS, (
                    f"{employee.name} starts a block at "
                    f"{planner.HOUR_LABELS[start]} on {planner.DAY_NAMES[day]}, "
                    "too late in the day for a whole block"
                )
            assert blocks <= employee.max_splits + 1, (
                f"{employee.name} has {blocks} blocks on "
                f"{planner.DAY_NAMES[day]} but max_splits is "
                f"{employee.max_splits}"
            )

        # Rule 8: weekly hours never exceed the worker's cap.
        assert week_hours <= employee.max_hours, (
            f"{employee.name} works {week_hours} hours this week but the cap "
            f"is {employee.max_hours}"
        )

    # Rule 10: every worker has at least one pair of consecutive days off.
    for w, employee in enumerate(employees):
        worked_days = {
            day
            for day in range(day_count)
            if any(role is not None for role in assignments[w][day])
        }
        assert any(
            day not in worked_days and day + 1 not in worked_days
            for day in range(day_count - 1)
        ), f"{employee.name} has no two consecutive days off"


def _expected_summaries(
    problem: planner.Problem, schedule: planner.Schedule
) -> list[tuple[str, int, int, int, int, float]]:
    """Recompute the per-employee figures from the grid, independently."""
    expected = []
    for w, employee in enumerate(problem.employees):
        total_hours = 0
        days_worked = 0
        splits = 0
        best_off = 0
        run_off = 0
        for day in range(planner.DAYS):
            hours = [
                hour
                for hour in range(planner.HOURS_PER_DAY)
                if schedule.assignments[w][day][hour] is not None
            ]
            if hours:
                days_worked += 1
                total_hours += len(hours)
                if len(hours) != hours[-1] - hours[0] + 1:
                    splits += 1
                run_off = 0
            else:
                run_off += 1
                best_off = max(best_off, run_off)
        average = round(total_hours / days_worked, 2) if days_worked else 0.0
        expected.append(
            (employee.name, total_hours, days_worked, splits, best_off, average)
        )
    return expected


# --- Fixtures -------------------------------------------------------------


@pytest.fixture(scope="module")
def main_problem() -> planner.Problem:
    return _main_problem()


@pytest.fixture(scope="module")
def main_schedule(main_problem: planner.Problem) -> planner.Schedule:
    return planner.solve(main_problem, time_limit_s=TINY_TIME_LIMIT_S, random_seed=0)


# --- Feasible instance, rules 1-10, summaries -----------------------------


def test_feasible_instance_solves_and_satisfies_every_rule(
    main_problem: planner.Problem, main_schedule: planner.Schedule
) -> None:
    assert main_schedule.outcome.solved is True
    assert main_schedule.outcome in (
        planner.SolverOutcome.OPTIMAL,
        planner.SolverOutcome.FEASIBLE,
    )
    assert_schedule_is_valid(main_problem, main_schedule)


def test_validate_accepts_the_feasible_instance(main_problem: planner.Problem) -> None:
    assert planner.validate(main_problem) == []


def test_summaries_match_the_assignments_grid(
    main_problem: planner.Problem, main_schedule: planner.Schedule
) -> None:
    expected = _expected_summaries(main_problem, main_schedule)
    assert len(main_schedule.summaries) == len(main_problem.employees)
    for summary, (name, total, days, splits, best_off, average) in zip(
        main_schedule.summaries, expected
    ):
        assert summary.name == name
        assert summary.total_hours == total
        assert summary.days_worked == days
        assert summary.splits == splits
        assert summary.max_consecutive_days_off == best_off
        assert summary.average_hours_per_day == pytest.approx(average)

    # The feasible instance works every day (two workers per day), so at least
    # one worker must have worked and the weekly totals must match the demand.
    demand_total = sum(
        main_problem.need(role, day, hour)
        for role in planner.ROLES
        for day in range(planner.DAYS)
        for hour in range(planner.HOURS_PER_DAY)
    )
    assert sum(s.total_hours for s in main_schedule.summaries) == demand_total


def test_summary_rows_match_schedule_summaries(
    main_schedule: planner.Schedule,
) -> None:
    rows = planner.summary_rows(main_schedule)
    assert len(rows) == len(main_schedule.summaries)
    for summary, row in zip(main_schedule.summaries, rows):
        assert row["Employé"] == summary.name
        assert row["Heures/semaine"] == summary.total_hours
        assert row["Jours travaillés"] == summary.days_worked
        assert row["Coupures/semaine"] == summary.splits
        assert row["Jours OFF cons. max"] == summary.max_consecutive_days_off
        assert row["H/jour en moyenne"] == pytest.approx(
            summary.average_hours_per_day
        )


def test_weekly_rows_match_the_assignments_grid(
    main_problem: planner.Problem, main_schedule: planner.Schedule
) -> None:
    rows = planner.weekly_rows(main_problem, main_schedule)
    assert len(rows) == len(main_problem.employees) * planner.DAYS
    position = 0
    for w, employee in enumerate(main_problem.employees):
        for day in range(planner.DAYS):
            row = rows[position]
            position += 1
            assert row["Employé"] == employee.name
            assert row["Jour"] == planner.DAY_NAMES[day]
            for hour in range(planner.HOURS_PER_DAY):
                label = planner.HOUR_LABELS[hour]
                expected = main_schedule.assignments[w][day][hour] or ""
                assert row[label] == expected


def test_main_instance_days_are_contiguous_so_there_is_no_split(
    main_schedule: planner.Schedule,
) -> None:
    # The demand is two adjacent blocks per day, so nobody can hold two blocks:
    # a split count of zero is a property of the instance, not of a rota choice.
    assert [s.splits for s in main_schedule.summaries] == [0, 0, 0]
    assert all(s.days_worked > 0 for s in main_schedule.summaries)


# --- Skills ---------------------------------------------------------------


def test_assignments_respect_declared_skills() -> None:
    problem = _skill_problem()
    assert planner.validate(problem) == []
    schedule = planner.solve(problem, time_limit_s=TINY_TIME_LIMIT_S, random_seed=0)
    assert_schedule_is_valid(problem, schedule)
    expected_role = {
        "Cuisine Only": "Cuisinier",
        "Pizza Only": "Pizzaiolo",
        "Dish Only": "Plongeur",
    }
    for w, employee in enumerate(problem.employees):
        worked = {
            role
            for day in range(planner.DAYS)
            for role in schedule.assignments[w][day]
            if role is not None
        }
        assert worked == {expected_role[employee.name]}
    for summary in schedule.summaries:
        assert summary.days_worked == 2
        assert summary.total_hours == 6
        assert summary.splits == 0
        assert summary.max_consecutive_days_off == 5


# --- Minimum block boundary ----------------------------------------------


def test_minimum_block_boundary_accepts_exactly_three_hours() -> None:
    problem = _one_worker_problem((0, 1, 2))
    assert planner.validate(problem) == []
    schedule = planner.solve(problem, time_limit_s=TINY_TIME_LIMIT_S, random_seed=0)
    assert_schedule_is_valid(problem, schedule)
    summary = schedule.summaries[0]
    assert summary.total_hours == 3
    assert summary.days_worked == 1
    assert schedule.assignments[0][0][0] == "Cuisinier"
    assert schedule.assignments[0][0][2] == "Cuisinier"
    assert schedule.assignments[0][0][3] is None


def test_minimum_block_boundary_rejects_a_two_hour_request() -> None:
    # Two consecutive hours pass every necessary condition in `validate`, but
    # no legal block is that short, so no schedule can exist. The result must
    # not claim to be solved, and a timeout must never be labelled infeasible.
    problem = _one_worker_problem((0, 1))
    assert planner.validate(problem) == []
    schedule = planner.solve(problem, time_limit_s=TINY_TIME_LIMIT_S, random_seed=0)
    assert schedule.solved is False
    assert schedule.assignments == ()
    assert schedule.summaries == ()
    assert schedule.outcome in (
        planner.SolverOutcome.INFEASIBLE,
        planner.SolverOutcome.UNKNOWN,
    )


# --- Split counting -------------------------------------------------------


def test_a_gap_between_two_blocks_is_counted_as_a_split() -> None:
    problem = _split_problem()
    assert planner.validate(problem) == []
    schedule = planner.solve(problem, time_limit_s=TINY_TIME_LIMIT_S, random_seed=0)
    assert_schedule_is_valid(problem, schedule)

    summaries = {summary.name: summary for summary in schedule.summaries}
    solo = summaries["Solo"]
    assert solo.days_worked == 1
    assert solo.total_hours == 6
    assert solo.splits == 1
    assert solo.max_consecutive_days_off == 6
    assert solo.average_hours_per_day == pytest.approx(6.0)

    # The forced split shows up in `summary_rows` too.
    rows = {row["Employé"]: row for row in planner.summary_rows(schedule)}
    assert rows["Solo"]["Coupures/semaine"] == 1
    assert rows["Solo"]["Heures/semaine"] == 6

    # Workers who are not Plongeurs stay off the whole week: no split either.
    for name in ("Ana", "Bob"):
        assert summaries[name].days_worked == 0
        assert summaries[name].splits == 0
        assert summaries[name].total_hours == 0
        assert summaries[name].average_hours_per_day == pytest.approx(0.0)
        assert summaries[name].max_consecutive_days_off == planner.DAYS


# --- Characterisation of an inherited limitation -------------------------


def test_a_role_may_change_across_a_gap_today() -> None:
    """Characterises the inherited hole in the "no role change in a day" rule.

    The rule is enforced only between consecutive worked hours. This instance
    leaves the solver no alternative: a single worker must cover Cuisinier at
    10:00-12:00 and Pizzaiolo at 16:00-18:00 on the same day, separated by a
    three-hour gap, and nobody else can take either block. Demand is exact, so
    the returned grid is forced.

    This test records what the engine does today; it does not endorse it.
    Tightening the rule would change which schedules are accepted, so it
    belongs to a later correctness task. If that task lands, this test is
    expected to change.
    """
    employees = (planner.Employee("Solo", frozenset(planner.ROLES), 40, 1),)
    needs = _empty_needs()
    for hour in (0, 1, 2):
        needs["Cuisinier"][0][hour] = 1
    for hour in (6, 7, 8):
        needs["Pizzaiolo"][0][hour] = 1
    problem = planner.Problem(employees=employees, needs=needs)

    assert planner.validate(problem) == [], planner.validate(problem)
    schedule = planner.solve(problem, time_limit_s=TINY_TIME_LIMIT_S, random_seed=0)
    assert schedule.solved, schedule.outcome
    assert_schedule_is_valid(problem, schedule)

    grid = schedule.assignments[0][0]
    assert grid[0] == "Cuisinier" and grid[2] == "Cuisinier"
    assert grid[6] == "Pizzaiolo" and grid[8] == "Pizzaiolo"

    # The day holds two different roles, which the interface rule forbids but
    # the model permits, because the blocks are separated by a gap.
    roles_today = {role for role in grid if role is not None}
    assert roles_today == {"Cuisinier", "Pizzaiolo"}, roles_today


# --- validate: necessary conditions --------------------------------------


def test_validate_rejects_an_empty_employee_list() -> None:
    problem = planner.Problem(employees=(), needs=_empty_needs())
    errors = planner.validate(problem)
    assert any("no employee to schedule" in error for error in errors)


def test_validate_rejects_demand_for_a_role_nobody_has() -> None:
    employees = (planner.Employee("Cook", frozenset({"Cuisinier"}), 40, 1),)
    needs = _empty_needs()
    needs["Plongeur"][0][0] = 1
    errors = planner.validate(planner.Problem(employees=employees, needs=needs))
    assert any("nobody has that skill" in error for error in errors)


def test_validate_rejects_demand_exceeding_headcount_at_an_hour() -> None:
    employees = tuple(
        planner.Employee(f"W{w}", frozenset(planner.ROLES), 40, 1) for w in range(3)
    )
    needs = _empty_needs()
    needs["Cuisinier"][0][0] = 4
    errors = planner.validate(planner.Problem(employees=employees, needs=needs))
    assert any(
        "are needed but there are only 3 employees" in error for error in errors
    )


def test_validate_rejects_weekly_demand_exceeding_total_capacity() -> None:
    employees = tuple(
        planner.Employee(f"W{w}", frozenset(planner.ROLES), 2, 1) for w in range(3)
    )
    needs = _empty_needs()
    for day in range(planner.DAYS):
        for hour in (0, 1, 2):
            needs["Plongeur"][day][hour] = 1
    errors = planner.validate(planner.Problem(employees=employees, needs=needs))
    assert any("can only work 6" in error for error in errors)


def test_validate_rejects_a_grid_with_the_wrong_number_of_days() -> None:
    needs = _empty_needs()
    needs["Cuisinier"] = needs["Cuisinier"][:5]
    errors = planner.validate(planner.Problem(employees=_main_problem().employees, needs=needs))
    assert any("expected 7 days of needs" in error for error in errors)


def test_validate_rejects_a_grid_with_the_wrong_number_of_hours() -> None:
    needs = _empty_needs()
    needs["Cuisinier"][0] = needs["Cuisinier"][0][:13]
    errors = planner.validate(planner.Problem(employees=_main_problem().employees, needs=needs))
    assert any("expected 14 hours" in error for error in errors)


def test_validate_rejects_a_negative_need() -> None:
    needs = _empty_needs()
    needs["Plongeur"][0][0] = -1
    errors = planner.validate(planner.Problem(employees=_main_problem().employees, needs=needs))
    assert any("cannot be negative" in error for error in errors)


# --- ValidationError on solve --------------------------------------------


def test_solve_raises_validation_error_and_keeps_the_errors() -> None:
    employees = (planner.Employee("Cook", frozenset({"Cuisinier"}), 40, 1),)
    needs = _empty_needs()
    needs["Plongeur"][0][0] = 1
    problem = planner.Problem(employees=employees, needs=needs)
    errors = planner.validate(problem)
    assert errors

    with pytest.raises(planner.ValidationError) as excinfo:
        planner.solve(problem, time_limit_s=TINY_TIME_LIMIT_S, random_seed=0)
    assert list(excinfo.value.errors) == errors
    assert str(excinfo.value)


# --- Solver status handling ----------------------------------------------


def test_infeasible_but_valid_instance_is_never_labelled_solved() -> None:
    problem = _infeasible_but_valid_problem()
    # The necessary conditions all hold, so the solver has to prove the
    # impossibility itself. A timeout is an unproven result, not a proof.
    assert planner.validate(problem) == []
    schedule = planner.solve(problem, time_limit_s=TINY_TIME_LIMIT_S, random_seed=0)
    assert schedule.solved is False
    assert schedule.assignments == ()
    assert schedule.summaries == ()
    assert schedule.outcome in (
        planner.SolverOutcome.INFEASIBLE,
        planner.SolverOutcome.UNKNOWN,
    )
    if schedule.outcome is planner.SolverOutcome.UNKNOWN:
        # Never assert INFEASIBLE for an UNKNOWN result; the contract under
        # test is that an unsolved schedule stays empty and unsolved.
        assert schedule.diagnostics["status"] != "OPTIMAL"
    else:
        assert schedule.outcome is planner.SolverOutcome.INFEASIBLE


def test_solver_outcome_solved_flags() -> None:
    assert planner.SolverOutcome.OPTIMAL.solved is True
    assert planner.SolverOutcome.FEASIBLE.solved is True
    assert planner.SolverOutcome.INFEASIBLE.solved is False
    assert planner.SolverOutcome.UNKNOWN.solved is False
    assert planner.SolverOutcome.MODEL_INVALID.solved is False


def test_an_unsolved_schedule_carries_no_assignments(
    main_problem: planner.Problem,
) -> None:
    for outcome in (
        planner.SolverOutcome.UNKNOWN,
        planner.SolverOutcome.INFEASIBLE,
        planner.SolverOutcome.MODEL_INVALID,
    ):
        schedule = planner.Schedule(outcome=outcome, problem=main_problem)
        assert schedule.solved is False
        assert schedule.assignments == ()
        assert schedule.summaries == ()
        assert planner.weekly_rows(main_problem, schedule) == []
        assert planner.summary_rows(schedule) == []
        with pytest.raises(IndexError):
            schedule.role_at(0, 0, 0)


# --- parse_employees ------------------------------------------------------


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "Nom": "Ada",
        "Cuisinier": True,
        "Pizzaiolo": False,
        "Plongeur": False,
        "Heures Max": 35,
        "Coupures Max": 2,
    }
    row.update(overrides)
    return row


def test_parse_employees_accepts_a_valid_row() -> None:
    employees, errors = planner.parse_employees([_row()])
    assert errors == []
    assert len(employees) == 1
    assert employees[0].name == "Ada"
    assert employees[0].max_hours == 35
    assert employees[0].max_splits == 2
    assert employees[0].skills == frozenset({"Cuisinier"})
    assert employees[0].can("Cuisinier") is True
    assert employees[0].can("Plongeur") is False


def test_parse_employees_reports_a_blank_heures_max() -> None:
    employees, errors = planner.parse_employees([_row(**{"Heures Max": ""})])
    assert any("Heures Max" in error for error in errors)
    assert employees[0].max_hours == 0


def test_parse_employees_reports_a_blank_coupures_max() -> None:
    employees, errors = planner.parse_employees([_row(**{"Coupures Max": None})])
    assert any("Coupures Max" in error for error in errors)
    assert employees[0].max_splits == 0


def test_parse_employees_reports_non_numeric_values() -> None:
    employees, errors = planner.parse_employees(
        [_row(**{"Heures Max": "abc", "Coupures Max": "3.5"})]
    )
    assert any("Heures Max" in error for error in errors)
    assert any("Coupures Max" in error for error in errors)
    assert employees[0].max_hours == 0
    assert employees[0].max_splits == 0


def test_parse_employees_falls_back_when_the_name_is_missing() -> None:
    row = _row()
    del row["Nom"]
    employees, errors = planner.parse_employees([row])
    assert any("the name is empty" in error for error in errors)
    assert employees[0].name == "Emp1"


def test_parse_employees_reports_a_row_with_no_skills() -> None:
    employees, errors = planner.parse_employees(
        [_row(**{"Cuisinier": False, "Pizzaiolo": False, "Plongeur": False})]
    )
    assert any("no role is selected" in error for error in errors)
    assert employees[0].skills == frozenset()


def test_parse_employees_reports_duplicate_names() -> None:
    employees, errors = planner.parse_employees([_row(), _row()])
    assert any("appears more than once" in error for error in errors)
    assert len(employees) == 2


def test_parse_employees_treats_nan_as_blank_and_never_raises() -> None:
    rows = [
        _row(**{"Nom": "Nan Hours", "Heures Max": float("nan")}),
        _row(**{"Nom": "Nan Splits", "Coupures Max": float("nan")}),
        _row(**{"Nom": "Mixed", "Heures Max": 40.0, "Coupures Max": "2"}),
        {},
        {
            "Nom": "Odd",
            "Cuisinier": object(),
            "Pizzaiolo": [],
            "Plongeur": {},
            "Heures Max": [],
            "Coupures Max": {"x": 1},
        },
    ]
    employees, errors = planner.parse_employees(rows)
    assert len(employees) == 5
    assert any("Heures Max" in error for error in errors)
    assert any("Coupures Max" in error for error in errors)
    assert any("the name is empty" in error for error in errors)
    assert employees[0].max_hours == 0
    assert employees[1].max_splits == 0
    assert employees[2].max_hours == 40
    assert employees[2].max_splits == 2


# --- default_needs --------------------------------------------------------


def test_default_needs_shape_and_documented_curve() -> None:
    needs = planner.default_needs()
    assert set(needs) == set(planner.ROLES)
    for role in planner.ROLES:
        grid = needs[role]
        assert len(grid) == planner.DAYS == 7
        for row in grid:
            assert len(row) == planner.HOURS_PER_DAY == 14
            assert all(isinstance(value, int) for value in row)

    # 16:00 and 17:00 only a cook is needed (slot indices 6 and 7).
    for day in range(planner.DAYS):
        assert needs["Cuisinier"][day][6] == 1
        assert needs["Cuisinier"][day][7] == 1
        assert needs["Pizzaiolo"][day][6] == 0
        assert needs["Pizzaiolo"][day][7] == 0
        assert needs["Plongeur"][day][6] == 0
        assert needs["Plongeur"][day][7] == 0
        # Nothing at 23:00, and nobody needed at the labels after 22:00.
        assert needs["Cuisinier"][day][13] == 0
        assert needs["Pizzaiolo"][day][13] == 0
        assert needs["Plongeur"][day][13] == 0

    # The document says nobody is needed after 22:00: the 22:00 slot is the
    # last staffed one.
    assert planner.HOUR_LABELS[12] == "22:00"
    assert planner.HOUR_LABELS[13] == "23:00"
    for role in planner.ROLES:
        assert needs[role][0][12] == 1

    # Busy from 10:00 to 15:00 for every role.
    for hour in range(0, 6):
        assert all(needs[role][0][hour] == 1 for role in planner.ROLES)
