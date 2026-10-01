"""Streamlit adapter for the kitchen rota planner.

All scheduling logic lives in planner.py. This file only collects input from
the user, hands a Problem to the engine, and renders the result. It holds no
model code and no constraint code.
"""

import pandas as pd
import streamlit as st

import planner

st.set_page_config(page_title="Planning Cuisine", layout="wide")
st.title("👨‍🍳 Optimized Kitchen Scheduler")


def empty_employee_frame(count: int) -> pd.DataFrame:
    """The default employee table for a given headcount."""
    return pd.DataFrame(
        {
            "Nom": [f"Emp{i + 1}" for i in range(count)],
            "Cuisinier": [True] * count,
            "Pizzaiolo": [True] * count,
            "Plongeur": [True] * count,
            "Heures Max": [42] * count,
            "Coupures Max": [3] * count,
        }
    )


def needs_frames() -> dict[str, pd.DataFrame]:
    """The default staffing-needs table for each role.

    Rows are hours and columns are days, matching the editors on screen.
    """
    defaults = planner.default_needs()
    return {
        role: pd.DataFrame(
            defaults[role], index=planner.DAY_NAMES, columns=planner.HOUR_LABELS
        ).T
        for role in planner.ROLES
    }


def frames_to_needs(
    frames: dict[str, pd.DataFrame],
) -> dict[str, tuple[tuple[object, ...], ...]]:
    """Read the edited tables back into the engine's day-major shape."""
    return {
        role: tuple(
            tuple(frames[role][day].tolist()) for day in planner.DAY_NAMES
        )
        for role in planner.ROLES
    }


def render(schedule: planner.Schedule) -> None:
    """Show the outcome, or the schedule when there is one."""
    if schedule.outcome is planner.SolverOutcome.INFEASIBLE:
        st.error(
            "❌ No valid schedule exists for these constraints. Try relaxing "
            "Heures Max, Coupures Max or the staffing needs."
        )
        return

    if schedule.outcome is planner.SolverOutcome.UNKNOWN:
        st.warning(
            "⏳ The solver stopped before it could prove a schedule. This is "
            "not proof that none exists. Try again with a longer time limit."
        )
        return

    if not schedule.solved:
        st.error(f"❌ The solver returned '{schedule.outcome.value}'.")
        return

    st.success("✅ Schedule generated successfully!")

    st.subheader("🗓️ Weekly Schedule")
    weekly = pd.DataFrame(planner.weekly_rows(schedule.problem, schedule))
    st.dataframe(weekly.set_index(["Employé", "Jour"]))

    st.subheader("📊 Summary per Employee")
    summaries = pd.DataFrame(planner.summary_rows(schedule))
    st.dataframe(summaries.set_index("Employé"))


with st.form(key="schedule_form"):
    num_workers = st.slider("Number of employees", 2, 12, 6)

    if (
        "kitchen_df" not in st.session_state
        or st.session_state.kitchen_df.shape[0] != num_workers
    ):
        st.session_state.kitchen_df = empty_employee_frame(num_workers)

    st.subheader("💼 Employee Skills & Constraints")
    edited_df = st.data_editor(
        st.session_state.kitchen_df, num_rows="dynamic", key="kitchen_editor"
    )

    if "role_needs" not in st.session_state:
        st.session_state.role_needs = needs_frames()

    st.subheader("📊 Hourly Staffing Needs")
    edited_frames: dict[str, pd.DataFrame] = {}
    for role in planner.ROLES:
        st.markdown(f"### {role}")
        edited_frames[role] = st.data_editor(
            st.session_state.role_needs[role], key=f"needs_{role}"
        )

    submitted = st.form_submit_button("✅ Generate Kitchen Schedule")


if submitted:
    st.session_state.kitchen_df = edited_df
    st.session_state.role_needs = edited_frames

    employees, input_errors = planner.parse_employees(
        edited_df.to_dict(orient="records")
    )
    problem = planner.Problem(
        employees=employees, needs=frames_to_needs(edited_frames)
    )

    errors = input_errors + planner.validate(problem)
    if errors:
        st.error("❌ The schedule cannot be built from these inputs:")
        for error in errors:
            st.markdown(f"- {error}")
    else:
        with st.spinner("Finding a valid schedule..."):
            schedule = planner.solve(problem)
        render(schedule)
