import streamlit as st
import pandas as pd
import json
from io import BytesIO

def format_time_hhmm(value):
    try:
        if value == "-" or value is None or pd.isna(value):
            return "-"
        negative = value < 0
        value = abs(value)
        hours = int(value)
        minutes = int(round((value - hours) * 60))
        if minutes == 60:
            hours += 1
            minutes = 0
        return f"{'-' if negative else ''}{hours:02d}:{minutes:02d}"
    except:
        return "-"

def _html_ontime(val):
    try:
        if val == "-" or val is None or pd.isna(val):
            return "-"
        if val >= 0:
            return "<span style='color:green'><strong>YES</strong></span>"
        else:
            return "<span style='color:red'><strong>NO</strong></span>"
    except:
        return "-"

def draw_table(routes, total_cost, time_limit):
    all_rows = []
    late_deliveries = []
    vehicule_folosite = len(routes)
    pas_global = 1

    for idx, r in enumerate(routes):
        vehicul = r.get("vehicul", f"Vehicle {idx+1}")
        cumulative_time = 0.0
        delivery_done = False
        num_daily_rests = 0

        last_delivery_idx = max((j for j, p in enumerate(r["traseu"]) if p["tip"] == "delivery"), default=-1)
        def get_crew_flag(vehicul):
            if isinstance(vehicul, dict):
                return int(bool(vehicul.get("echipaj", False)))
            elif isinstance(vehicul, str):
                return int("crew" in vehicul.lower())
            return 0

        crew = get_crew_flag(vehicul)

        drive_since_break = 0.0
        drive_since_rest = 0.0
        drive_since_aptitude = 0.0
        last_break_time = 0.0
        last_rest_time = 0.0
        last_aptitude_time = 0.0
        nr_breaks = 0 #nr de pauze intre 2 daily rest
        nr_deliveries = 0 #nr de livrari intre 2 daily rest

        pas0 = r["traseu"][0]
        all_rows.append({
            "Step": pas_global,
            "Vehicle": vehicul,
            "Description": "Depart depot",
            "City": pas0["oras"],
            "Distance (km)": "-",
            "Time elapsed (h)": cumulative_time,
            "Time left (h)": time_limit - cumulative_time,
            "On time?": _html_ontime(time_limit - cumulative_time)
        })
        pas_global += 1

        i = 1
        while i < len(r["traseu"]):
            pas = r["traseu"][i]
            tip_pas = pas["tip"]
            oras = pas["oras"]
            distanta = pas.get("distanta", 0)
            if "durata" not in pas:
                raise ValueError(f"Lipsește durata la pasul {pas.get('oras')} ({pas})")
            durata = pas["durata"]
            if crew:
                rest_limit = 18
                aptitude_limit = 21
                break_limit = 4.5
            else:
                rest_limit = 9
                aptitude_limit = 15 if num_daily_rests <= 2 else 13
                break_limit = 4.5

            after_delivery = (i > last_delivery_idx and last_delivery_idx != -1)
            is_last = (i == len(r["traseu"]) - 1)
            next_drive_time = durata

            if i < len(r["traseu"]) - 1:
                next_pas = r["traseu"][i+1]
                if (tip_pas == "intermediar" and next_pas["tip"] == "intoarcere" and next_pas["oras"] == oras):
                    # La pasul de intoarcere, transferă distanța și durata la depot
                    i += 1
                    # Acum next_pas e "intoarcere" în depot; folosește distanța/durata pentru acest pas
                    pas_intoarcere = next_pas
                    tip_pas2 = pas_intoarcere["tip"]
                    oras2 = pas_intoarcere["oras"]
                    # Folosește datele de distanță și durată pentru "Arrive depot"
                    descr = "Arrive depot"
                    cumulative_time += durata
                    drive_since_break += durata
                    drive_since_rest += durata
                    drive_since_aptitude += durata

                    all_rows.append({
                        "Step": pas_global,
                        "Vehicle": vehicul,
                        "Description": descr,
                        "City": oras2,
                        "Distance (km)": round(distanta, 2),
                        "Time elapsed (h)": cumulative_time,
                        "Time left (h)": "-",
                        "On time?": "-"
                    })
                    pas_global += 1
                    i += 1
                    continue

            if drive_since_aptitude + next_drive_time > aptitude_limit:
                pauza = 11 if num_daily_rests >= 2 else 9
                pause_time = last_rest_time + aptitude_limit + pauza + (nr_breaks * 0.75) + (nr_deliveries *2)
                drive_since_rest = drive_since_aptitude - aptitude_limit
                all_rows.append({
                    "Step": pas_global,
                    "Vehicle": vehicul,
                    "Description": f"Daily Rest ({pauza}h) (Aptitude reached)",
                    "City": "On Route",
                    "Distance (km)": "-",
                    "Time elapsed (h)": pause_time,
                    "Time left (h)": "-" if after_delivery else time_limit - cumulative_time,
                    "On time?": "-" if after_delivery else _html_ontime(time_limit - cumulative_time)
                })
                pas_global += 1
                cumulative_time += pauza
                drive_since_break = 0
                drive_since_aptitude = 0
                nr_breaks = 0
                nr_deliveries = 0
                last_break_time = cumulative_time
                last_rest_time = cumulative_time
                last_aptitude_time = cumulative_time
                num_daily_rests += 1
                continue

            if drive_since_rest + next_drive_time > rest_limit:
                pauza = 11 if num_daily_rests >= 2 else 9
                pause_time = last_rest_time + rest_limit + pauza + (nr_breaks * 0.75) + (nr_deliveries *2)
                drive_since_rest = drive_since_rest - rest_limit
                all_rows.append({
                    "Step": pas_global,
                    "Vehicle": vehicul,
                    "Description": f"Daily Rest ({pauza}h)",
                    "City": "On Route",
                    "Distance (km)": "-",
                    "Time elapsed (h)": pause_time,
                    "Time left (h)": "-" if after_delivery else time_limit - cumulative_time,
                    "On time?": "-" if after_delivery else _html_ontime(time_limit - cumulative_time)
                })
                pas_global += 1
                cumulative_time += pauza
                drive_since_break = 0
                drive_since_aptitude = 0
                nr_breaks = 0
                nr_deliveries = 0
                last_break_time = cumulative_time
                last_rest_time = cumulative_time
                last_aptitude_time = cumulative_time
                num_daily_rests += 1
                continue

            if drive_since_break + next_drive_time > break_limit:
                drive_since_break = drive_since_break - break_limit
                pause_time = last_break_time + 5.25
                all_rows.append({
                    "Step": pas_global,
                    "Vehicle": vehicul,
                    "Description": "Driver Break (45min)",
                    "City": "On Route",
                    "Distance (km)": "-",
                    "Time elapsed (h)": pause_time,
                    "Time left (h)": "-" if after_delivery else time_limit - cumulative_time,
                    "On time?": "-" if after_delivery else _html_ontime(time_limit - cumulative_time)
                })
                pas_global += 1
                drive_since_aptitude += 0.75
                cumulative_time += 0.75
                nr_breaks += 1
                last_break_time = pause_time
                continue

            cumulative_time += durata
            drive_since_break += durata
            drive_since_rest += durata
            drive_since_aptitude += durata

            descr = "Transit"
            if tip_pas in ["pickup", "delivery"]:
                descr = f"Arrive order {pas.get('comanda','')} ({tip_pas})"
            elif tip_pas == "intoarcere":
                # Dacă e ultimul pas sau următorul are același oraș, înseamnă că ajungem la depozit
                if is_last or (i + 1 < len(r["traseu"]) and r["traseu"][i + 1]["oras"] == oras):
                    descr = "Arrive depot"
                else:
                    descr = "Transit"

            all_rows.append({
                "Step": pas_global,
                "Vehicle": vehicul,
                "Description": descr,
                "City": oras,
                "Distance (km)": round(distanta, 2),
                "Time elapsed (h)": cumulative_time,
                "Time left (h)": "-" if after_delivery else time_limit - cumulative_time,
                "On time?": "-" if after_delivery else _html_ontime(time_limit - cumulative_time)
            })
            pas_global += 1

            if tip_pas in ["pickup", "delivery"]:
                cumulative_time += 2
                drive_since_break = 0
                nr_deliveries +=1
                drive_since_aptitude += 2
                last_break_time = cumulative_time
                last_rest_time = cumulative_time
                last_aptitude_time = cumulative_time
                all_rows.append({
                    "Step": pas_global,
                    "Vehicle": vehicul,
                    "Description": f"Depart order {pas.get('comanda','')} ({tip_pas})",
                    "City": oras,
                    "Distance (km)": "-",
                    "Time elapsed (h)": cumulative_time,
                    "Time left (h)": "-" if after_delivery else (time_limit - cumulative_time),
                    "On time?": "-" if after_delivery else _html_ontime(time_limit - cumulative_time)
                })
                pas_global += 1

                if tip_pas == "delivery" and (time_limit - cumulative_time +2 ) < 0 and not after_delivery:
                    late_deliveries.append({
                        "Vehicle": vehicul,
                        "Order": pas.get('comanda', ""),
                        "Delay (h)": abs(round(time_limit - cumulative_time, 2)) - 2
                    })
                if i == last_delivery_idx:
                    delivery_done = True

            i += 1

    # Formatăm coloanele oră/minute
    df: pd.DataFrame = pd.DataFrame(all_rows)
    df["Vehicle_label"] = df["Vehicle"].apply(
        lambda v: v["nume"] if isinstance(v, dict) and "nume" in v else str(v)
    )
    df = df.drop(columns=["Vehicle"])  # elimină dicturile
    df = df.rename(columns={"Vehicle_label": "Vehicle"})

    col_order = ["Step", "Vehicle", "Description", "City", "Distance (km)", "Time elapsed (h)", "Time left (h)", "On time?"]
    df = pd.DataFrame(df[[col for col in col_order if col in df.columns]])

    for col in ["Time elapsed (h)", "Time left (h)"]:
        df[col] = df[col].apply(format_time_hhmm)

    st.subheader("📋 Routing Table")

    order_by_options = {
        "No filter": None,
        "By vehicle": df["Vehicle"].unique().tolist(),
        "By order": sorted([str(x).split()[2] for x in df["Description"] if "order" in x]),
        "By city": df["City"].unique().tolist(),
        "By delay": ["On time", "Delayed"],
        "By step type": list(set([x.split()[0] for x in df["Description"]]))
    }

    order_by = st.selectbox("Order by:", list(order_by_options.keys()), index=0)
    filter_val = None
    df_filtered = df.copy()

    if order_by != "No filter":
        values = order_by_options[order_by]
        if values:
            filter_val = st.selectbox("Filter value:", values)
            if order_by == "By vehicle":
                df_filtered = df[df["Vehicle"] == filter_val]
            elif order_by == "By order":
                df_filtered = df[df["Description"].str.contains(f"order {filter_val}")]
            elif order_by == "By city":
                df_filtered = df[df["City"] == filter_val]
            elif order_by == "By delay":
                if filter_val == "On time":
                    df_filtered = df[df["On time?"].str.contains("green")]
                else:
                    df_filtered = df[df["On time?"].str.contains("red")]
            elif order_by == "By step type":
                df_filtered = df[df["Description"].str.startswith(filter_val)]

    st.write(
        df_filtered.to_html(escape=False, index=False,
            col_space=70,
            justify="center"
        ),
        unsafe_allow_html=True
    )

    # Calculate total distance, Pyright-friendly
    dist_col = df["Distance (km)"].replace({"": 0, "-": 0})
    distances = pd.to_numeric(dist_col, errors="coerce")

    # Ensure we have a Series for fillna/astype
    if not isinstance(distances, pd.Series):
        distances = pd.Series(distances)

    distances = distances.fillna(0).astype(float)
    dist_total: float = float(distances.sum())
    st.markdown(f"**Estimated total distance:** `{round(dist_total, 2)} km`")

    if late_deliveries:
        # Curăță numele vehiculului și formatează Delay(h) în HH:MM
        for d in late_deliveries:
            vehicul = d.get("Vehicle") or d.get("vehicul")
            if isinstance(vehicul, dict):
                d["Vehicle"] = vehicul.get("nume", str(vehicul))
            elif vehicul is not None:
                d["Vehicle"] = str(vehicul)

            # Delay poate avea denumiri diferite
            if "Delay (h)" in d:
                d["Delay (h)"] = format_time_hhmm(d["Delay (h)"])
            elif "Delay" in d:
                d["Delay"] = format_time_hhmm(d["Delay"])

        orders_delayed = ", ".join(
            sorted(set([f"Order {d['Order']}" for d in late_deliveries]))
        )
        st.error(f"⚠️ Delayed orders: {orders_delayed}")
        st.subheader("📊 Delay details")
        st.dataframe(pd.DataFrame(late_deliveries), use_container_width=True)
    else:
        st.success("✅ All deliveries on time")

    # Export scenario
    scenario_json = BytesIO()
    scenario_data = {
        "fleet": st.session_state.get("vehicle_profiles", []),
        "orders": st.session_state.get("requests", []),
        "routes": routes,
    }
    scenario_json.write(json.dumps(scenario_data, indent=2).encode())
    scenario_json.seek(0)
    st.download_button(
        label="💾 Save current scenario",
        data=scenario_json,
        file_name="scenario_export.json",
        mime="application/json"
    )

    # Export Excel
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:  # type: ignore
        df.to_excel(writer, sheet_name='Routing', index=False)
        if late_deliveries:
            pd.DataFrame(late_deliveries).to_excel(writer, sheet_name='Delays', index=False)
        output.seek(0)
    st.download_button(
        label="📥 Export table to Excel",
        data=output.getvalue(),
        file_name="routing_table.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
