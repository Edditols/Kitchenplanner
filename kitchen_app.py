import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model

# ───────────────────────── PARAMÈTRES FIXES ─────────────────────────
DAYS = 7
HOURS_PER_DAY = 14
START_HOUR = 10
ROLES = ["Cuisinier", "Pizzaiolo", "Plongeur"]

hour_labels = [f"{(START_HOUR + h)%24}:00" for h in range(HOURS_PER_DAY)]
day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

st.set_page_config(page_title="Planning Cuisine", layout="wide")
st.title("👨‍🍳 Planning Cuisine Optimisé")

# ───── Nombre d'employés ─────
num_workers = st.slider("Nombre d'employés", 2, 12, 6)

# ───── Table des rôles par employé ─────
if "kitchen_df" not in st.session_state or st.session_state.kitchen_df.shape[0] != num_workers:
    st.session_state.kitchen_df = pd.DataFrame({
        "Nom": [f"Emp{i+1}" for i in range(num_workers)],
        "Cuisinier": [False]*num_workers,
        "Pizzaiolo": [False]*num_workers,
        "Plongeur": [False]*num_workers,
        "Heures Max": [42]*num_workers,
        "Coupures Max": [3]*num_workers
    })

st.subheader("💼 Compétences des employés")
st.session_state.kitchen_df = st.data_editor(st.session_state.kitchen_df, num_rows="dynamic", key="kitchen_editor")
df_emp = st.session_state.kitchen_df.fillna(False)

# ───── Table des besoins horaires par poste ─────
st.subheader("📋 Besoin horaire par poste (modifiable)")
def default_needs():
    needs = {}
    for role in ROLES:
        daily = []
        for h in range(HOURS_PER_DAY):
            if 0 <= h <= 5 or 8 <= h <= 12:  # 10-15 or 18-22
                need = 1 if role == "Plongeur" else 1
            elif 6 <= h <= 7:  # 15-18
                need = 1 if role == "Cuisinier" else 0
            else:
                need = 0
            daily.append(need)
        needs[role] = {day: daily.copy() for day in day_names}
    return needs

if "role_needs" not in st.session_state:
    st.session_state.role_needs = default_needs()

role_needs = {}
for role in ROLES:
    st.markdown(f"### {role}")
    role_df = pd.DataFrame(st.session_state.role_needs[role], index=hour_labels)
    edited = st.data_editor(role_df, key=f"{role}_needs")
    role_needs[role] = {day: edited[day].tolist() for day in day_names}
    st.session_state.role_needs[role] = role_needs[role]

if st.button("✅ Générer le planning cuisine"):
    model = cp_model.CpModel()
    idx = lambda d,h: d * HOURS_PER_DAY + h
    num_slots = DAYS * HOURS_PER_DAY
    num_emps = len(df_emp)

    # Créer les variables de shift : worker x role x time
    shifts = {(w,r): [model.NewBoolVar(f"w{w}_{r}_{t}") for t in range(num_slots)] for w in range(num_emps) for r in ROLES}
    is_off = {w: [] for w in range(num_emps)}

    for w in range(num_emps):
        for d in range(DAYS):
            off = model.NewBoolVar(f"off_{w}_{d}")
            total_shifts_day = sum(shifts[(w,r)][idx(d,h)] for r in ROLES for h in range(HOURS_PER_DAY))
            model.Add(total_shifts_day == 0).OnlyEnforceIf(off)
            model.Add(total_shifts_day >= 1).OnlyEnforceIf(off.Not())
            is_off[w].append(off)

    # 1 seul rôle par heure par employé
    for w in range(num_emps):
        for t in range(num_slots):
            model.Add(sum(shifts[(w,r)][t] for r in ROLES) <= 1)

    # Max 10h par jour et respect coupures
    for w in range(num_emps):
        for d in range(DAYS):
            total_day = sum(shifts[(w,r)][idx(d,h)] for r in ROLES for h in range(HOURS_PER_DAY))
            model.Add(total_day <= 10)
            block_starts = []
            for h in range(HOURS_PER_DAY):
                current = sum(shifts[(w,r)][idx(d,h)] for r in ROLES)
                if h == 0:
                    block_starts.append(current)
                else:
                    prev = sum(shifts[(w,r)][idx(d,h-1)] for r in ROLES)
                    start_var = model.NewBoolVar(f'start_{w}_{d}_{h}')
                    model.AddBoolAnd([current == 1, prev == 0]).OnlyEnforceIf(start_var)
                    model.AddBoolOr([current != 1, prev != 0]).OnlyEnforceIf(start_var.Not())
                    block_starts.append(start_var)
            model.Add(sum(block_starts) <= int(df_emp.iloc[w]['Coupures Max']) + 1)

    for w in range(num_emps):
        twos = []
        for d in range(DAYS-1):
            b = model.NewBoolVar(f"2off_{w}_{d}")
            model.AddBoolAnd([is_off[w][d], is_off[w][d+1]]).OnlyEnforceIf(b)
            model.AddBoolOr([is_off[w][d].Not(), is_off[w][d+1].Not()]).OnlyEnforceIf(b.Not())
            twos.append(b)
        model.Add(sum(twos) >= 1)

    for w in range(num_emps):
        model.Add(sum(shifts[(w,r)][t] for r in ROLES for t in range(num_slots)) <= int(df_emp.iloc[w]['Heures Max']))

    # Contraintes de compétence
    for w in range(num_emps):
        for r in ROLES:
            if not df_emp.iloc[w][r]:
                for t in range(num_slots):
                    model.Add(shifts[(w,r)][t] == 0)

    # Couvrir tous les besoins
    for d in range(DAYS):
        for h in range(HOURS_PER_DAY):
            t = idx(d,h)
            for r in ROLES:
                required = role_needs[r][day_names[d]][h]
                model.Add(sum(shifts[(w,r)][t] for w in range(num_emps)) == required)

    model.Minimize(sum(shifts[(w,r)][t] for w in range(num_emps) for r in ROLES for t in range(num_slots)))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        st.error("❌ Aucune solution trouvée. Vérifiez que chaque poste est bien couvert et que les contraintes sont possibles.")
    else:
        st.success("✅ Planning cuisine généré !")
        planning = []
        for w in range(num_emps):
            for d in range(DAYS):
                row = {"Employé": df_emp.iloc[w]['Nom'], "Jour": day_names[d]}
                for h in range(HOURS_PER_DAY):
                    found = ""
                    for r in ROLES:
                        if solver.Value(shifts[(w,r)][idx(d,h)]):
                            found = r
                            break
                    row[hour_labels[h]] = found
                planning.append(row)
        st.dataframe(pd.DataFrame(planning))
