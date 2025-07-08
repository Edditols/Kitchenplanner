import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model

# ───────────────────────── FIXED PARAMETERS ─────────────────────────
DAYS = 7
HOURS_PER_DAY = 14  # 10:00 -> 23:00 (14 hours)
START_HOUR = 10
ROLES = ["Cuisinier", "Pizzaiolo", "Plongeur"]

# Generate labels for UI display
hour_labels = [f"{(START_HOUR + h) % 24}:00" for h in range(HOURS_PER_DAY)]
day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# --- Streamlit UI Configuration ---
st.set_page_config(page_title="Planning Cuisine", layout="wide")
st.title("👨‍🍳 Optimized Kitchen Scheduler")

# --- All inputs are now wrapped in a form ---
with st.form(key="schedule_form"):
    # ────────── Employee Count Input ──────────
    num_workers = st.slider("Number of employees", 2, 12, 6)

    # ────────── Employee Skills Table ──────────
    # Initialize or update the employee dataframe based on the slider
    if "kitchen_df" not in st.session_state or st.session_state.kitchen_df.shape[0] != num_workers:
        st.session_state.kitchen_df = pd.DataFrame({
            "Nom": [f"Emp{i+1}" for i in range(num_workers)],
            "Cuisinier": [True] * num_workers, # Default to True for easier setup
            "Pizzaiolo": [True] * num_workers,
            "Plongeur": [True] * num_workers,
            "Heures Max": [42] * num_workers,
            "Coupures Max": [3] * num_workers,
        })

    st.subheader("💼 Employee Skills & Constraints")
    # Use the data editor for interactive input
    edited_df = st.data_editor(
        st.session_state.kitchen_df, num_rows="dynamic", key="kitchen_editor")
    
    # ────────── Hourly Needs Table ──────────

    def default_needs():
        """Generates a default staffing needs dictionary."""
        needs = {}
        for role in ROLES:
            daily = []
            for h in range(HOURS_PER_DAY):
                # Peak hours: 10:00-15:00 and 18:00-22:00
                if 0 <= h <= 5 or 8 <= h <= 12:
                    need = 1
                # Off-peak: 16:00-18:00 (only cook needed)
                elif 6 <= h <= 7:
                    need = 1 if role == "Cuisinier" else 0
                else:
                    need = 0
                daily.append(need)
            needs[role] = {d: daily.copy() for d in day_names}
        return needs

    # Initialize needs in session state if not present
    if "role_needs" not in st.session_state:
        st.session_state.role_needs = default_needs()

    st.subheader("📊 Hourly Staffing Needs")
    edited_role_needs = {}
    for role in ROLES:
        st.markdown(f"### {role}")
        df_role = pd.DataFrame(st.session_state.role_needs[role], index=hour_labels)
        edited_df_role = st.data_editor(df_role, key=f"needs_{role}")
        edited_role_needs[role] = {d: edited_df_role[d].tolist() for d in day_names}

    # --- The submit button for the form ---
    submitted = st.form_submit_button("✅ Generate Kitchen Schedule")


# ───────────────────────── Schedule Generation (runs only on submission) ─────────────────────────
if submitted:
    # Update session state with the edited data after submission
    st.session_state.kitchen_df = edited_df
    st.session_state.role_needs = edited_role_needs
    df_emp = st.session_state.kitchen_df.fillna(False)
    role_needs = st.session_state.role_needs

    model = cp_model.CpModel()
    idx = lambda d, h: d * HOURS_PER_DAY + h  # Helper to flatten day/hour index
    SLOTS = DAYS * HOURS_PER_DAY
    W = len(df_emp)

    # --- Variable Definitions ---
    # shifts[(w,r)][t] is true if worker w performs role r at time slot t
    shifts = {(w, r): [model.NewBoolVar(f"w{w}_{r}_{t}") for t in range(SLOTS)]
              for w in range(W) for r in ROLES}

    # is_off[w][d] is true if worker w is off on day d
    is_off = {w: [] for w in range(W)}
    for w in range(W):
        for d in range(DAYS):
            off = model.NewBoolVar(f"off_{w}_{d}")
            total_day = sum(shifts[(w, r)][idx(d, h)] for r in ROLES for h in range(HOURS_PER_DAY))
            # Link the 'off' variable to the daily work hours
            model.Add(total_day == 0).OnlyEnforceIf(off)
            model.Add(total_day >= 3).OnlyEnforceIf(off.Not())  # If working, must work at least 3 hours
            is_off[w].append(off)

    # --- General Constraints ---
    # ▸ A worker can perform at most one role per hour
    for w in range(W):
        for t in range(SLOTS):
            model.Add(sum(shifts[(w, r)][t] for r in ROLES) <= 1)

    # ▸ A worker cannot change roles within the same day
    for w in range(W):
        for d in range(DAYS):
            for h in range(HOURS_PER_DAY - 1):
                t = idx(d, h)
                t1 = idx(d, h + 1)
                for r1 in ROLES:
                    for r2 in ROLES:
                        if r1 != r2:
                            # Prohibit switching from r1 at hour h to r2 at hour h+1
                            model.Add(shifts[(w, r1)][t] + shifts[(w, r2)][t1] <= 1)

    # ▸ Daily hour limits, work block rules, and break counting
    for w in range(W):
        max_coup = int(df_emp.iloc[w]['Coupures Max'])
        for d in range(DAYS):
            total_day = sum(shifts[(w, r)][idx(d, h)] for r in ROLES for h in range(HOURS_PER_DAY))
            model.Add(total_day <= 10)  # Max 10 hours per day

            # Detect the start of a work block to count breaks ('coupures')
            starts = []
            for h in range(HOURS_PER_DAY):
                t = idx(d, h)
                curr = sum(shifts[(w, r)][t] for r in ROLES)
                start = model.NewBoolVar(f'start_{w}_{d}_{h}')

                if h == 0:
                    # A start at the first hour of the day only depends on current work status
                    model.Add(curr == 1).OnlyEnforceIf(start)
                    model.Add(curr != 1).OnlyEnforceIf(start.Not())
                else:
                    prev = sum(shifts[(w, r)][idx(d, h - 1)] for r in ROLES)
                    # A "start" is a transition from not working (prev=0) to working (curr=1).
                    model.Add(curr - prev == 1).OnlyEnforceIf(start)
                    model.Add(curr - prev != 1).OnlyEnforceIf(start.Not())

                # If a block starts, it must be at least 3 hours long
                if h <= HOURS_PER_DAY - 3:
                    min_block_sum = sum(shifts[(w, r)][idx(d, hh)] for r in ROLES for hh in range(h, h + 3))
                    model.Add(min_block_sum >= 3).OnlyEnforceIf(start)
                else:
                    # Cannot start a block if less than 3 hours remain in the day
                    model.Add(start == 0)
                starts.append(start)
            
            # The number of starts is one more than the number of breaks
            model.Add(sum(starts) <= max_coup + 1)

    # ▸ At least one block of two consecutive days off
    for w in range(W):
        cons = []
        for d in range(DAYS - 1):
            bloc = model.NewBoolVar(f"2off_{w}_{d}")
            model.AddBoolAnd([is_off[w][d], is_off[w][d + 1]]).OnlyEnforceIf(bloc)
            model.AddBoolOr([is_off[w][d].Not(), is_off[w][d + 1].Not()]).OnlyEnforceIf(bloc.Not())
            cons.append(bloc)
        model.Add(sum(cons) >= 1)

    # ▸ Weekly maximum hours per employee
    for w in range(W):
        total_hours = sum(shifts[(w, r)][t] for r in ROLES for t in range(SLOTS))
        model.Add(total_hours <= int(df_emp.iloc[w]['Heures Max']))

    # ▸ Employee must have the required skill for the assigned role
    for w in range(W):
        for r in ROLES:
            if not bool(df_emp.iloc[w][r]):
                for t in range(SLOTS):
                    model.Add(shifts[(w, r)][t] == 0)

    # ▸ Meet the hourly staffing requirements for each role
    for d in range(DAYS):
        for h in range(HOURS_PER_DAY):
            t = idx(d, h)
            for r in ROLES:
                req = role_needs[r][day_names[d]][h]
                assigned = sum(shifts[(w, r)][t] for w in range(W))
                model.Add(assigned == req)

    # --- Objective Function ---
    # Minimize the total number of hours worked (while satisfying all constraints)
    total_shifts = sum(shifts[(w, r)][t] for w in range(W) for r in ROLES for t in range(SLOTS))
    model.Minimize(total_shifts)

    # ────────── Solve the Model ──────────
    with st.spinner("Finding the optimal schedule..."):
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 20.0  # Set a timeout
        status = solver.Solve(model)

    # ────────── Display Results ──────────
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        st.error("❌ No solution found. Try adjusting employee constraints or staffing needs.")
    else:
        st.success("✅ Schedule generated successfully!")

        # Process the solution to create a readable schedule
        planning = []
        summary = []
        for w in range(W):
            total_h = days_worked = coup = max_off_streak = curr_off = 0
            for d in range(DAYS):
                row = {"Employé": df_emp.iloc[w]['Nom'], "Jour": day_names[d]}
                work_hours = []
                for h in range(HOURS_PER_DAY):
                    role_here = ""
                    for r in ROLES:
                        if solver.Value(shifts[(w, r)][idx(d, h)]):
                            role_here = r
                    row[hour_labels[h]] = role_here
                    if role_here:
                        work_hours.append(h)
                planning.append(row)

                # Calculate summary stats for the day
                if work_hours:
                    days_worked += 1
                    total_h += len(work_hours)
                    # A break exists if the number of worked hours is less than the span of hours
                    if len(work_hours) < (work_hours[-1] - work_hours[0] + 1):
                        coup += 1
                    curr_off = 0
                else:
                    curr_off += 1
                    max_off_streak = max(max_off_streak, curr_off)

            avg_h = round(total_h / days_worked, 2) if days_worked else 0
            summary.append({
                "Employé": df_emp.iloc[w]['Nom'],
                "Heures/semaine": total_h,
                "Jours travaillés": days_worked,
                "Coupures/semaine": coup,
                "Jours OFF cons. max": max_off_streak,
                "H/jour en moyenne": avg_h
            })

        df_planning = pd.DataFrame(planning)
        df_summary = pd.DataFrame(summary)

        st.subheader("🗓️ Weekly Schedule")
        st.dataframe(df_planning.set_index(["Employé", "Jour"]))

        st.subheader("📊 Summary per Employee")
        st.dataframe(df_summary.set_index("Employé"))

