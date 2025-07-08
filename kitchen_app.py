import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model

# ───────────────────────── PARAMÈTRES FIXES ─────────────────────────
DAYS = 7
HOURS_PER_DAY = 14      # 10h → 23h
START_HOUR = 10
ROLES = ["Cuisinier", "Pizzaiolo", "Plongeur"]

hour_labels = [f"{(START_HOUR + h) % 24}:00" for h in range(HOURS_PER_DAY)]
day_names   = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

st.set_page_config(page_title="Planning Cuisine", layout="wide")
st.title("👨‍🍳 Planning Cuisine Optimisé")

# ────────── Saisie du nombre d'employés ──────────
num_workers = st.slider("Nombre d'employés", 2, 12, 6)

# ────────── Tableau compétences employés ──────────
if "kitchen_df" not in st.session_state or st.session_state.kitchen_df.shape[0] != num_workers:
    st.session_state.kitchen_df = pd.DataFrame({
        "Nom"          : [f"Emp{i+1}" for i in range(num_workers)],
        "Cuisinier"    : [False]*num_workers,
        "Pizzaiolo"    : [False]*num_workers,
        "Plongeur"     : [False]*num_workers,
        "Heures Max"   : [42]*num_workers,
        "Coupures Max" : [3]*num_workers,
    })

st.subheader("💼 Compétences des employés")
st.session_state.kitchen_df = st.data_editor(
    st.session_state.kitchen_df, num_rows="dynamic", key="kitchen_editor")
df_emp = st.session_state.kitchen_df.fillna(False)

# ────────── Tableau besoins horaires ──────────

def default_needs():
    needs = {}
    for role in ROLES:
        daily = []
        for h in range(HOURS_PER_DAY):
            if 0 <= h <= 5 or 8 <= h <= 12:      # 10-15 et 18-22
                need = 1 if role == "Plongeur" else 1
            elif 6 <= h <= 7:                    # 15-18 : seulement cuisinier
                need = 1 if role == "Cuisinier" else 0
            else:
                need = 0
            daily.append(need)
        needs[role] = {d: daily.copy() for d in day_names}
    return needs

if "role_needs" not in st.session_state:
    st.session_state.role_needs = default_needs()

role_needs = {}
for role in ROLES:
    st.markdown(f"### {role}")
    df_role = pd.DataFrame(st.session_state.role_needs[role], index=hour_labels)
    edited = st.data_editor(df_role, key=f"needs_{role}")
    st.session_state.role_needs[role] = {d: edited[d].tolist() for d in day_names}
    role_needs[role] = st.session_state.role_needs[role]

# ───────────────────────── Génération planning ─────────────────────────
if st.button("✅ Générer le planning cuisine"):

    model  = cp_model.CpModel()
    idx    = lambda d,h: d * HOURS_PER_DAY + h  # utilitaire index plat
    SLOTS  = DAYS * HOURS_PER_DAY
    W      = len(df_emp)

    # Variable shifts[w, r][t]
    shifts = {(w,r): [model.NewBoolVar(f"w{w}_{r}_{t}") for t in range(SLOTS)]
              for w in range(W) for r in ROLES}

    # Variable OFF par jour
    is_off = {w: [] for w in range(W)}
    for w in range(W):
        for d in range(DAYS):
            off = model.NewBoolVar(f"off_{w}_{d}")
            total_day = sum(shifts[(w,r)][idx(d,h)] for r in ROLES for h in range(HOURS_PER_DAY))
            model.Add(total_day == 0).OnlyEnforceIf(off)
            model.Add(total_day >= 3).OnlyEnforceIf(off.Not())  # présent ⇒ ≥3h
            is_off[w].append(off)

    # ▸ 1 seul rôle par heure par employé
    for w in range(W):
        for t in range(SLOTS):
            model.Add(sum(shifts[(w,r)][t] for r in ROLES) <= 1)

    # ▸ Interdire le changement de rôle d'une heure à l'autre (même journée)
    for w in range(W):
        for d in range(DAYS):
            for h in range(HOURS_PER_DAY-1):
                t  = idx(d,h)
                t1 = idx(d,h+1)
                for r1 in ROLES:
                    for r2 in ROLES:
                        if r1 != r2:
                            model.Add(shifts[(w,r1)][t] + shifts[(w,r2)][t1] <= 1)

    # ▸ Max 10h par jour + blocs ≥3h + nb coupures
    for w in range(W):
        max_coup = int(df_emp.iloc[w]['Coupures Max'])
        for d in range(DAYS):
            total_day = sum(shifts[(w,r)][idx(d,h)] for r in ROLES for h in range(HOURS_PER_DAY))
            model.Add(total_day <= 10)

            # Détection des débuts de blocs
            starts = []
            for h in range(HOURS_PER_DAY):
                t = idx(d,h)
                curr = sum(shifts[(w,r)][t] for r in ROLES)
                start = model.NewBoolVar(f'start_{w}_{d}_{h}')
                if h == 0:
                    model.Add(curr == 1).OnlyEnforceIf(start)
                    model.Add(curr != 1).OnlyEnforceIf(start.Not())
                else:
                    prev = sum(shifts[(w,r)][idx(d,h-1)] for r in ROLES)
                    # THIS IS THE CORRECTED BLOCK
                    model.Add(curr - prev == 1).OnlyEnforceIf(start)
                    model.Add(curr - prev != 1).OnlyEnforceIf(start.Not())
                    # END OF CORRECTION
                
                if h <= HOURS_PER_DAY-3:  # bloc doit faire ≥3h
                    model.Add(sum(shifts[(w,r)][idx(d,hh)] for r in ROLES for hh in range(h, h+3)) >= 3).OnlyEnforceIf(start)
                else:  # impossible de démarrer un bloc à <3h de la fin
                    model.Add(start == 0)
                starts.append(start)
            model.Add(sum(starts) <= max_coup + 1)

    # ▸ Deux jours OFF consécutifs min
    for w in range(W):
        cons = []
        for d in range(DAYS-1):
            bloc = model.NewBoolVar(f"2off_{w}_{d}")
            model.AddBoolAnd([is_off[w][d], is_off[w][d+1]]).OnlyEnforceIf(bloc)
            model.AddBoolOr([is_off[w][d].Not(), is_off[w][d+1].Not()]).OnlyEnforceIf(bloc.Not())
            cons.append(bloc)
        model.Add(sum(cons) >= 1)

    # ▸ Heures max semaine
    for w in range(W):
        model.Add(sum(shifts[(w,r)][t] for r in ROLES for t in range(SLOTS)) <= int(df_emp.iloc[w]['Heures Max']))

    # ▸ Respect des compétences
    for w in range(W):
        for r in ROLES:
            if not bool(df_emp.iloc[w][r]):
                for t in range(SLOTS):
                    model.Add(shifts[(w,r)][t] == 0)

    # ▸ Couverture des besoins horaires
    for d in range(DAYS):
        for h in range(HOURS_PER_DAY):
            t = idx(d,h)
            for r in ROLES:
                req = role_needs[r][day_names[d]][h]
                model.Add(sum(shifts[(w,r)][t] for w in range(W)) == req)

    model.Minimize(sum(shifts[(w,r)][t] for w in range(W) for r in ROLES for t in range(SLOTS)))

    # ────────── Résolution ──────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        st.error("❌ Aucune solution trouvée. Ajustez vos besoins ou contraintes.")
    else:
        st.success("✅ Planning cuisine généré !")

        # ----- Tableau planning -----
        planning = []
        summary  = []
        for w in range(W):
            total_h = days_worked = coup = max_off_streak = curr_off = 0
            for d in range(DAYS):
                row = {"Employé": df_emp.iloc[w]['Nom'], "Jour": day_names[d]}
                work_hours = []
                for h in range(HOURS_PER_DAY):
                    role_here = ""
                    for r in ROLES:
                        if solver.Value(shifts[(w,r)][idx(d,h)]):
                            role_here = r
                    row[hour_labels[h]] = role_here
                    if role_here:
                        work_hours.append(h)
                planning.append(row)

                if work_hours:
                    days_worked += 1
                    total_h     += len(work_hours)
                    if len(work_hours) > 0 and len(work_hours) < (work_hours[-1]-work_hours[0]+1):
                        coup += 1
                    curr_off = 0
                else:
                    curr_off += 1
                    max_off_streak = max(max_off_streak, curr_off)
            avg_h = round(total_h/days_worked,2) if days_worked else 0
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

        st.subheader("🗓️ Planning hebdomadaire")
        st.dataframe(df_planning.set_index(["Employé", "Jour"]))

        st.subheader("📊 Résumé par employé")
        st.dataframe(df_summary.set_index("Employé"))
