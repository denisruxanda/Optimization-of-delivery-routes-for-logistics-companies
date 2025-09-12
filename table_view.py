import streamlit as st
import pandas as pd
import json
from io import BytesIO

__all__ = ["draw_table"]

# ---- constants ----
SERVICE_TIME = 2.0                # h per pickup/delivery
DRIVER_BREAK_ = 0.75              # 45 min
SINGLE_DRIVER_DAILY_LIMIT = 9
CREW_DRIVER_DAILY_LIMIT = 18
SINGLE_DRIVER_APTITUDE = 15
SINGLE_DRIVER_APTITUDE_REDUCED = 13
CREW_DRIVER_APTITUDE = 21
DAILY_REST = 9
DAILY_REST_EXTENDED = 11
BREAK_WINDOW = 4.5                # h driving before 45m break
DECIMALS_KM = 2
TABLE_COL_SPACE = 70

# ---------- helpers ----------
def _fmt_hhmm(x):
    try:
        if x is None or x == "-" or pd.isna(x):
            return "-"
        v = float(x)
        neg = v < 0
        v = abs(v)
        h = int(v)
        m = int(round((v - h) * 60))
        if m == 60:
            h += 1
            m = 0
        return f"{'-' if neg else ''}{h:02d}:{m:02d}"
    except Exception:
        return "-"

def _veh_name(v):
    if isinstance(v, dict):
        return v.get("nume", "Vehicle")
    return str(v)

def _round_km(x):
    try:
        return round(float(x), DECIMALS_KM)
    except Exception:
        return x

def _driver_limits(veh_dict, rests_done):
    crew = bool(veh_dict.get("echipaj", False)) if isinstance(veh_dict, dict) else False
    rest_limit = CREW_DRIVER_DAILY_LIMIT if crew else SINGLE_DRIVER_DAILY_LIMIT
    apt_limit = CREW_DRIVER_APTITUDE if crew else (
        SINGLE_DRIVER_APTITUDE_REDUCED if rests_done > 2 else SINGLE_DRIVER_APTITUDE
    )
    return rest_limit, apt_limit

def _html_status(slack):
    try:
        if slack is None or slack == "-" or pd.isna(slack):
            return "-"
        return "<span style='color:green'><strong>YES</strong></span>" if float(slack) >= 0 \
               else "<span style='color:red'><strong>NO</strong></span>"
    except Exception:
        return "-"

def _find_delivery_deadline(steps, start_idx, order_id):
    for j in range(start_idx + 1, len(steps)):
        sp = steps[j] or {}
        if sp.get("tip") == "delivery":
            oid = sp.get("order_id", sp.get("comanda", ""))
            if oid == order_id:
                tl = sp.get("time_limit", None)
                if isinstance(tl, (int, float)):
                    return float(tl)
    return None

def _nearest_future_deadline(steps, cur_idx, onboard_deadlines, last_delivery_idx):
    cands = []
    if onboard_deadlines:
        cands.extend([float(v) for v in onboard_deadlines.values() if isinstance(v, (int, float))])
    end_idx = last_delivery_idx if last_delivery_idx != -1 else len(steps) - 1
    for j in range(cur_idx, end_idx + 1):
        sp = steps[j] or {}
        if sp.get("tip") in ("pickup", "delivery"):
            tl = sp.get("time_limit", None)
            if not isinstance(tl, (int, float)) and sp.get("tip") == "pickup":
                tl = _find_delivery_deadline(steps, j, sp.get("order_id", sp.get("comanda", "")))
            if isinstance(tl, (int, float)):
                cands.append(float(tl))
    if not cands:
        return None
    return min(cands)

def _add_row(rows, step_no, veh, descr, city, dist_km, elapsed, time_left, ontime_html):
    rows.append({
        "Step": step_no,
        "Vehicle": veh,
        "Description": descr,
        "City": city,
        "Distance (km)": "-" if dist_km == "-" else _round_km(dist_km),
        "Time elapsed (h)": _fmt_hhmm(elapsed),
        "Time left (h)": _fmt_hhmm(time_left) if not isinstance(time_left, str) else time_left,
        "On time?": ontime_html
    })

def draw_table(routes, _total_cost, _deprecated_time_limit):
    if not routes:
        st.warning("No routes to display.")
        return

    rows = []
    late = []
    step_no = 1

    for r_idx, route in enumerate(routes):
        veh = route.get("vehicul", {"nume": f"Vehicle {r_idx+1}"})
        veh_label = _veh_name(veh)
        steps = route.get("traseu", [])
        if not steps:
            continue

        depot_city = steps[0].get("oras", "")
        # last delivery index for this vehicle
        last_del_idx = max((i for i, p in enumerate(steps) if (p or {}).get("tip") == "delivery"), default=-1)

        # clocks
        t = 0.0
        rests_done = 0
        since_break = 0.0
        since_rest = 0.0
        since_apt = 0.0

        # active deadlines for onboard orders
        onboard = {}

        # Depart depot
        active_deadline = _nearest_future_deadline(steps, 1, onboard, last_del_idx)
        slack0 = None if last_del_idx == -1 else (None if active_deadline is None else (active_deadline - t))
        _add_row(rows, step_no, veh_label, "Depart depot", steps[0].get("oras", ""), "-", t,
                 "-" if slack0 is None else slack0,
                 "-" if slack0 is None else _html_status(slack0))
        step_no += 1

        i = 1
        while i < len(steps):
            pas = steps[i] or {}
            tip_pas = pas.get("tip", "")
            city = pas.get("oras", "")
            dist = float(pas.get("distanta", 0) or 0.0)
            dur = float(pas.get("durata", 0) or 0.0)
            oid = pas.get("order_id", pas.get("comanda", ""))

            rest_limit, apt_limit = _driver_limits(veh, rests_done)

            # show time columns until the last delivery
            show_time_now = (i <= last_del_idx) if last_del_idx != -1 else True
 
            while True:
                apt_needed = (since_apt + dur) > apt_limit
                rest_needed = (since_rest + dur) > rest_limit
                break_needed = (since_break + dur) > BREAK_WINDOW

                if apt_needed:
                    rest_len = DAILY_REST_EXTENDED if rests_done >= 2 else DAILY_REST
                    t += rest_len
                    since_break = 0.0
                    since_rest = 0.0
                    since_apt = 0.0
                    rests_done += 1

                    active_deadline = _nearest_future_deadline(steps, i, onboard, last_del_idx) if show_time_now else None
                    slack = None if active_deadline is None else (active_deadline - t)
                    _add_row(
                        rows, step_no, veh_label,
                        f"Daily Rest ({rest_len}h) (Aptitude reached)",
                        "On Route", "-", t,
                        "-" if (not show_time_now or slack is None) else slack,
                        "-" if (not show_time_now or slack is None) else _html_status(slack)
                    )
                    step_no += 1
                    continue  # re-check in case consecutive rests are still needed

                if rest_needed:
                    rest_len = DAILY_REST_EXTENDED if rests_done >= 2 else DAILY_REST
                    t += rest_len
                    since_break = 0.0
                    since_rest = 0.0
                    since_apt = 0.0
                    rests_done += 1

                    active_deadline = _nearest_future_deadline(steps, i, onboard, last_del_idx) if show_time_now else None
                    slack = None if active_deadline is None else (active_deadline - t)
                    _add_row(
                        rows, step_no, veh_label,
                        f"Daily Rest ({rest_len}h)",
                        "On Route", "-", t,
                        "-" if (not show_time_now or slack is None) else slack,
                        "-" if (not show_time_now or slack is None) else _html_status(slack)
                    )
                    step_no += 1
                    continue

                if break_needed:
                    t += DRIVER_BREAK_
                    since_break = 0.0
                    since_apt += DRIVER_BREAK_

                    active_deadline = _nearest_future_deadline(steps, i, onboard, last_del_idx) if show_time_now else None
                    slack = None if active_deadline is None else (active_deadline - t)
                    _add_row(
                        rows, step_no, veh_label,
                        "Driver Break (45min)",
                        "On Route", "-", t,
                        "-" if (not show_time_now or slack is None) else slack,
                        "-" if (not show_time_now or slack is None) else _html_status(slack)
                    )
                    step_no += 1
                    continue

                break  

            t += dur
            since_break += dur
            since_rest += dur
            since_apt += dur

            # description
            if tip_pas == "pickup":
                descr = f"Arrive order {oid} (pickup)"
            elif tip_pas == "delivery":
                descr = f"Arrive order {oid} (delivery)"
            elif tip_pas == "intoarcere":
                descr = "Arrive depot"
            else:
                descr = "Transit"

            # at pickup: registers deadline for this order 
            if tip_pas == "pickup":
                dl = pas.get("time_limit", None)
                if not isinstance(dl, (int, float)):
                    dl = _find_delivery_deadline(steps, i, oid)
                if isinstance(dl, (int, float)) and oid != "":
                    onboard[oid] = float(dl)

            # computes time left for this row 
            time_left_cell = "-"
            ontime_cell = "-"
            if show_time_now:
                active_deadline = _nearest_future_deadline(steps, i, onboard, last_del_idx)
                if active_deadline is not None:
                    slack = active_deadline - t
                    time_left_cell = slack
                    ontime_cell = _html_status(slack)

            # for delivery row, also checks lateness vs its own deadline
            if tip_pas == "delivery":
                own_dl = pas.get("time_limit", onboard.get(oid, None))
                if isinstance(own_dl, (int, float)):
                    own_slack = own_dl - t
                    if own_slack < 0:
                        late.append({"Vehicle": veh_label, "Order": oid, "Delay (h)": _fmt_hhmm(abs(own_slack))})

            _add_row(rows, step_no, veh_label, descr, city, dist, t, time_left_cell, ontime_cell)
            step_no += 1

            # service time at pickup/delivery
            if tip_pas in ("pickup", "delivery"):
                t += SERVICE_TIME
                since_break = 0.0
                since_apt += SERVICE_TIME

                # after delivery: remove order from onboard
                if tip_pas == "delivery" and oid in onboard:
                    del onboard[oid]

                # special case: delivery in depot city 
                if tip_pas == "delivery" and i == last_del_idx and city == depot_city:
                    _add_row(rows, step_no, veh_label, "Arrive depot", city, "-", t, "-", "-")
                    step_no += 1
                else:
                    # depart row (still show time columns until last delivery)
                    show_after = (i < last_del_idx) if tip_pas == "delivery" else (i <= last_del_idx)
                    time_left_after = "-"
                    ontime_after = "-"
                    if show_after:
                        active_deadline = _nearest_future_deadline(steps, i, onboard, last_del_idx)
                        if active_deadline is not None:
                            slack_after = active_deadline - t
                            time_left_after = slack_after
                            ontime_after = _html_status(slack_after)

                    _add_row(rows, step_no, veh_label, f"Depart order {oid} ({tip_pas})",
                             city, "-", t, time_left_after, ontime_after)
                    step_no += 1

            i += 1

    # render
    df = pd.DataFrame(rows)
    col_order = ["Step", "Vehicle", "Description", "City", "Distance (km)", "Time elapsed (h)", "Time left (h)", "On time?"]
    df = df[[c for c in col_order if c in df.columns]]

    st.subheader("📋 Routing Table")
    df = pd.DataFrame(rows)
    col_order = ["Step", "Vehicle", "Description", "City", "Distance (km)",
                "Time elapsed (h)", "Time left (h)", "On time?"]
    df = df[[c for c in col_order if c in df.columns]]

    df["On time (flag)"] = df["On time?"].astype(str).str.contains("YES")
    df["On time?"] = df["On time (flag)"].map({True: "YES", False: "NO"})

    st.subheader("📋 Routing Table")

    with st.expander("Filters", expanded=False):
        sel_veh = st.multiselect("Vehicle", sorted(df["Vehicle"].unique()))
        sel_city = st.multiselect("City", sorted(df["City"].unique()))
        sel_status = st.multiselect("Status", ["On time", "Late"])
        if sel_veh:   df = df[df["Vehicle"].isin(sel_veh)]
        if sel_city:  df = df[df["City"].isin(sel_city)]
        if sel_status:
            want = {"On time": True, "Late": False}
            df = df[df["On time (flag)"].isin([want[s] for s in sel_status])]

    st.dataframe(
        df.drop(columns=["On time (flag)"]),
        use_container_width=True,
        hide_index=True
    )

    total_km = pd.to_numeric(df["Distance (km)"].replace({"": 0, "-": 0}),
                            errors="coerce").fillna(0).sum()
    st.markdown(f"**Estimated total distance:** `{round(total_km, DECIMALS_KM)} km`")
    st.markdown(f"**Vehicles used:** `{len(routes)}`")

    if (~df["On time (flag)"]).any():
        st.subheader("📊 Delay details")
        st.dataframe(pd.DataFrame(late), use_container_width=True)
    else:
        st.success("✅ All deliveries on time")

    total_km = pd.to_numeric(df["Distance (km)"].replace({"": 0, "-": 0}), errors="coerce").fillna(0).sum()
    st.markdown(f"**Estimated total distance:** `{round(total_km, DECIMALS_KM)} km`")
    st.markdown(f"**Vehicles used:** `{len(routes)}`")

    if any(str(x).find("color:red") != -1 for x in df["On time?"]):
        st.subheader("📊 Delay details")
        st.dataframe(pd.DataFrame(late), use_container_width=True)
    else:
        st.success("✅ All deliveries on time")

    # export scenario
    scenario_json = BytesIO()
    scenario_json.write(json.dumps({
        "fleet": st.session_state.get("vehicle_profiles", []),
        "orders": st.session_state.get("requests", []),
        "routes": routes,
    }, indent=2).encode("utf-8"))
    scenario_json.seek(0)
    st.download_button(
        "💾 Save current scenario",
        data=scenario_json,
        file_name="scenario_export.json",
        mime="application/json"
    )

    # export Excel
    out = BytesIO()
    with pd.ExcelWriter(out, engine='xlsxwriter') as w:
        df.to_excel(w, sheet_name='Routing', index=False)
        if late:
            pd.DataFrame(late).to_excel(w, sheet_name='Delays', index=False)
        w.sheets['Routing'].set_column(0, len(df.columns)-1, 18)
        if late:
            w.sheets['Delays'].set_column(0, 2, 18)
        out.seek(0)
    st.download_button(
        "📥 Export table to Excel",
        data=out.getvalue(),
        file_name="routing_table.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
