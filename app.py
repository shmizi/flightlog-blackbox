"""FlightLog Black Box dashboard (Streamlit).

    .venv\\Scripts\\streamlit run app.py

Two tabs:
  Analyse a log   upload a .bin, .tlog or .param and see the verdict, findings, actions, graphs and map.
                  When n8n is running the upload is saved into the watched folder, so it is logged and
                  emailed exactly like a dropped file. Without n8n it goes straight to the API (logged,
                  no email), and with neither it is analysed here only.
  Fleet logbook   every processed file so far, trends across flights, exceptions and pre-flight checks.
"""

import json
import math
import os
import tempfile
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

from blackbox import analyze, exports, history, logbook, report
from blackbox.checks import is_finding, limit_for
from blackbox.history import METRICS
from blackbox.pipeline import ANALYSED, PREFLIGHT
from blackbox.validate import sha256

ROOT = Path(__file__).resolve().parent
API = os.environ.get("FLIGHTLOG_API_URL", "http://127.0.0.1:8000")
N8N = os.environ.get("FLIGHTLOG_N8N_URL", "http://127.0.0.1:5678")
OUTPUT = Path(os.environ.get("FLIGHTLOG_OUTPUT_DIR", ROOT / "output")).resolve()
WATCH = Path(os.environ.get("FLIGHTLOG_WATCH_FOLDER", ROOT / "flight-logs")).resolve()

st.set_page_config(page_title="FlightLog Black Box", page_icon="🛩️", layout="wide")


# ----------------------------------------------------------------------------- helpers
def api_up():
    try:
        return requests.get(f"{API}/health", timeout=2).ok
    except requests.RequestException:
        return False


def n8n_up():
    try:
        return requests.get(f"{N8N}/healthz", timeout=2).ok
    except requests.RequestException:
        return False


def drop_in_folder(name, data):
    """Write an upload into the watched folder for n8n, and return the file name used.

    n8n reacts to new files only, so an existing name gets " (2)", " (3)"... instead of being
    overwritten. The bytes go to a .part file first (n8n ignores those) and are renamed when
    complete, so n8n never reads half a file.
    """
    target, n = WATCH / name, 2
    while target.exists():
        target = WATCH / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    part = target.with_name(target.name + ".part")
    part.write_bytes(data)
    part.replace(target)
    return target.name


def hand_over(name, data):
    """Send an upload the way a folder drop would go: ("folder", file name), ("api", result),
    ("error", message) or ("local", None)."""
    if WATCH.is_dir() and n8n_up():
        return "folder", drop_in_folder(name, data)
    if api_up():
        try:
            r = requests.post(f"{API}/analyze", files={"file": (name, data)}, timeout=180)
            r.raise_for_status()
            return "api", r.json()["result"]
        except requests.RequestException as exc:
            return "error", str(exc)
    return "local", None


def banner(title, subtitle, colour):
    st.markdown(
        f'<div style="background:{colour};color:#fff;padding:14px 20px;border-radius:8px;margin-bottom:8px">'
        f'<div style="font-size:13px;opacity:.9">FlightLog Black Box</div>'
        f'<div style="font-size:26px;font-weight:700">{title}</div>'
        f'<div style="font-size:14px">{subtitle}</div></div>', unsafe_allow_html=True)


def colour_status(df, col):
    # Dark text on the light status colours, so the cell stays readable in Streamlit's dark theme too.
    return df.style.map(lambda v: f"background-color:#{report.LIGHT[v]};color:#202124" if v in report.LIGHT else "",
                        subset=[col])


def flight_label(r):
    """Short x-axis label (month-day time), or the file name when the log has no date.

    Month first so the labels sort in date order: Streamlit charts sort text labels alphabetically.
    """
    date = r["result"].get("flight_date")
    label = f"{date[5:10]} {date[11:16]}" if date else r["result"]["file_name"][:16]
    # The .bin and the .tlog of one flight start in the same minute: keep both points apart.
    return label + (" tlog" if r["result"].get("source") == "telemetry" else "")


def downsample(df, n=2000):
    return df.iloc[:: max(1, len(df) // n)]


def findings_frame(checks):
    rows = [{"Check": f"{c['id']}. {c['name']}", "Status": c["status"],
             "Measured": report.fmt(c["value"], c["unit"]),
             "Limit": report.fmt(limit_for(c["status"], c["warning"], c["critical"]), c["unit"]),
             "When (s)": c["t_flight_s"], "Detail": c["detail"]}
            for c in checks if is_finding(c["status"])]
    rows.sort(key=lambda r: r["Status"] != "Critical")      # stable: checks stay in id order
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- tab 1
def analyse_tab():
    st.subheader("Analyse a flight log")
    up = st.file_uploader("Drop a .bin (SD card), .tlog (Mission Planner telemetry) or .param (pre-flight check)",
                          type=None)
    in_folder = sorted((p for p in WATCH.glob("*") if p.suffix.lower() in (".bin", ".tlog", ".param", ".params")),
                       key=lambda p: p.stat().st_mtime, reverse=True) if WATCH.is_dir() else []
    pick = None
    if not up and in_folder:
        pick = st.selectbox("…or look at a file already in the watched folder", ["(choose a file)"] +
                            [p.name for p in in_folder])
        pick = None if pick == "(choose a file)" else WATCH / pick
    if not up and not pick:
        st.caption("The same analysis runs automatically on every file dropped into the watched folder.")
        return

    with tempfile.TemporaryDirectory() as tmp:
        if up:
            path = Path(tmp) / Path(up.name).name
            path.write_bytes(up.getvalue())
        else:
            path = pick
        local = analyze(path)                      # for graphs, map and downloads
        route = detail = None
        if up:
            # Streamlit reruns the script on every click, so each upload is handed over once per session.
            sent = st.session_state.setdefault("sent", {})
            key = sha256(path) + path.name
            if key not in sent:
                sent[key] = hand_over(path.name, up.getvalue())
            route, detail = sent[key]
        if route != "api" and local.status == ANALYSED:
            # Compare with the drone's earlier flights (the automation logs this one separately).
            local.history = history.compare(local.metrics, logbook.load(OUTPUT / "history.jsonl"))
        result = detail if route == "api" else local.to_dict()
        extras = exports.build(local)

    status = result["status"]
    banner(report.title(status, result.get("verdict"), result.get("incomplete")), path.name,
           report.colour(status, result.get("verdict")))
    if route == "folder":
        st.caption(f"Sent to the automation as {WATCH.name}\\{detail}: the email and the logbook entry follow "
                   "in about a minute.")
    elif route == "api":
        st.caption("Added to the team logbook. n8n is not running, so no email was sent.")
    elif route == "error":
        st.warning(f"The API did not answer ({detail}); showing a local analysis that is not logged or emailed.")
    elif route == "local":
        st.caption("n8n and the API are not running: analysed here only, not logged or emailed "
                   "(start them with scripts\\start.ps1).")
    else:
        st.caption("A file from the watched folder, shown read-only (the automation has already logged it).")

    if result.get("reason"):
        st.info(f"{result.get('exception_id') or ''} {result['reason']}".strip())
    if status not in (ANALYSED, PREFLIGHT):
        return

    if status == ANALYSED:
        a, b, c, d = st.columns(4)
        a.metric("Airborne", f"{result['airborne_s']:.0f} s")
        b.metric("Source", "Telemetry (.tlog)" if result["source"] == "telemetry" else "SD card (.bin)")
        c.metric("Flight date (UTC)", (result.get("flight_date") or "unknown")[:16].replace("T", " "))
        d.metric("Drone", result.get("airframe") or "unknown")

    found = findings_frame(result["checks"])
    st.markdown("#### Findings")
    if found.empty:
        st.success("All available checks passed.")
    else:
        st.dataframe(colour_status(found, "Status"), hide_index=True, width="stretch",
                     column_config={"When (s)": st.column_config.NumberColumn(format="%.0f"),
                                    "Detail": st.column_config.TextColumn(width="large")})

    left, right = st.columns(2)
    with left:
        st.markdown("#### Recommended actions")
        for i, act in enumerate(result.get("actions", []), 1):
            st.markdown(f"{i}. {act}")
    with right:
        if result.get("history"):
            st.markdown("#### Earlier flights")
            st.markdown(result["history"][0])
            for h in result["history"][1:]:
                st.markdown(f"- {h}")
        for n in result.get("notes", []):
            st.caption(f"Note: {n}")

    if local.parsed is not None and local.parsed.tables:
        st.markdown("#### Flight data")
        plot_flight(local.parsed.tables, local.parsed.log_start)
    if local.track:
        st.markdown("#### Flight path")
        track = pd.DataFrame(local.track, columns=["t", "lat", "lon", "alt"])
        span = max(track["lat"].max() - track["lat"].min(), track["lon"].max() - track["lon"].min(), 1e-5)
        zoom = int(min(19, max(3, math.log2(360 / span) - 1)))      # fit the flight, not the whole city
        st.map(track[["lat", "lon"]], size=1, zoom=zoom)

    st.markdown("#### Downloads")
    cols = st.columns(4)
    cols[0].download_button("summary.txt", report.summary_text(local), file_name="summary.txt")
    cols[1].download_button("result.json", json.dumps(result, indent=2, ensure_ascii=False), file_name="result.json")
    for col, name in zip(cols[2:], ("suggested_fixes.param", "flight_path.kml")):
        if name in extras:
            col.download_button(name, extras[name], file_name=name)


def plot_flight(tables, t0):
    def series(name, cols, rename=None, where=None, blank=None):
        """where drops rows (another sensor instance); blank hides them, so the line breaks there
        instead of joining across the time the drone sat disarmed."""
        df = tables.get(name)
        if df is None or not set(cols) <= set(df.columns):
            return None
        if where is not None:
            df = df[where(df)]
        out = df[["t", *cols]].copy()
        if blank is not None:
            out.loc[~blank(df), cols] = float("nan")
        gap = out["t"].diff() > 2.0                  # not logged for a while (disarmed): break the line
        if gap.any():
            filler = out.loc[gap].assign(**{c: float("nan") for c in cols})
            filler["t"] -= 1e-3
            out = pd.concat([out, filler]).sort_values("t", kind="stable")
        out = downsample(out)
        out["Time (s)"] = out["t"] - t0
        return out.drop(columns="t").rename(columns=rename or {}).set_index("Time (s)")

    c1, c2 = st.columns(2)
    bat = series("BAT", ["Volt", "Curr"], {"Volt": "Voltage (V)", "Curr": "Current (A)"},
                 blank=lambda d: d["Volt"] > 1)
    if bat is not None:
        c1.caption("Battery")
        c1.line_chart(bat)
    vibe = series("VIBE", ["VibeX", "VibeY", "VibeZ"],
                  where=lambda d: d["IMU"] == 0 if "IMU" in d else pd.Series(True, index=d.index))
    if vibe is not None:
        c2.caption("Vibration (m/s²)")
        c2.line_chart(vibe)
    alt_cols = [c for c in ("DAlt", "Alt") if c in tables.get("CTUN", pd.DataFrame()).columns]
    alt = series("CTUN", alt_cols, {"DAlt": "Target altitude (m)", "Alt": "Altitude (m)"}) if alt_cols else None
    if alt is not None:
        c1.caption("Altitude")
        c1.line_chart(alt)
    rcou = tables.get("RCOU")
    if rcou is not None:
        motors = [c for c in ("C1", "C2", "C3", "C4") if c in rcou.columns]
        out = series("RCOU", motors, blank=lambda d: d[motors].min(axis=1) > 1050)
        if out is not None and out.notna().any().any():
            c2.caption("Motor outputs (µs)")
            c2.line_chart(out)


# ----------------------------------------------------------------------------- tab 2
def logbook_tab():
    st.subheader("Fleet logbook")
    records = logbook.load(OUTPUT / "logbook.jsonl")
    if not records:
        st.info("No files processed yet. Drop a log into the watched folder or upload one in the first tab.")
        return
    flights = logbook.flights(records)
    verdicts = pd.Series([r["result"]["verdict"] for r in flights], dtype="object")
    a, b, c, d, e = st.columns(5)
    a.metric("Flights", len(flights))
    b.metric("Critical", int((verdicts == "Critical").sum()))
    c.metric("Warning", int((verdicts == "Warning").sum()))
    d.metric("Healthy", int((verdicts == "Healthy").sum()))
    ledger = None
    if api_up():
        try:
            ledger = requests.get(f"{API}/ledger", timeout=5).json()
        except requests.RequestException:
            pass
    e.metric("Ledger", "intact" if ledger and ledger.get("intact") else ("broken" if ledger else "API off"))

    drones = sorted({r["result"].get("airframe") or "unknown" for r in flights})
    pick = st.selectbox("Drone", ["All drones", *drones]) if len(drones) > 1 else "All drones"
    shown = [r for r in flights if pick == "All drones" or (r["result"].get("airframe") or "unknown") == pick]

    table = pd.DataFrame([{
        "Flight date (UTC)": (r["result"].get("flight_date") or "")[:16].replace("T", " "),
        "File": r["result"]["file_name"], "Verdict": r["result"]["verdict"],
        "Findings": len(r["result"].get("findings", [])), "Airborne (s)": r["result"]["airborne_s"],
        "Source": r["result"]["source"], "Processed": r["processed"]} for r in shown])
    st.dataframe(colour_status(table, "Verdict"), hide_index=True, width="stretch",
                 column_config={"Airborne (s)": st.column_config.NumberColumn(format="%.0f")})

    st.markdown("#### Trends across flights")
    trend = pd.DataFrame([{"Flight": flight_label(r),
                           **{METRICS[k][0]: r["result"].get("metrics", {}).get(k) for k in METRICS}}
                          for r in logbook.by_flight_date(shown)])
    if len(trend) < 2:
        st.caption("Trends appear once there are two or more flights.")
    else:
        trend = trend.set_index("Flight")
        cols = st.columns(2)
        for i, name in enumerate(["Lowest cell voltage", "Vibration", "Battery resistance", "Motor imbalance"]):
            values = trend[[name]].dropna()       # telemetry logs lack some numbers; join the rest
            if len(values) >= 2:
                cols[i % 2].caption(name)
                cols[i % 2].line_chart(values)

    with st.expander("Exceptions"):
        ex = logbook.exceptions(records)
        if ex:
            st.dataframe(pd.DataFrame(ex, columns=logbook.EXCEPTION_HEADERS), hide_index=True, width="stretch")
        else:
            st.caption("None.")
    with st.expander("Pre-flight checks"):
        pf = [{"Processed": r["processed"], "File": r["result"]["file_name"], "Verdict": r["result"]["verdict"],
               "Issues": "; ".join(logbook.issues(r["result"])) or "None"} for r in logbook.preflights(records)]
        if pf:
            st.dataframe(colour_status(pd.DataFrame(pf), "Verdict"), hide_index=True)
        else:
            st.caption("None.")

    book = OUTPUT / "flight_logbook.xlsx"
    if book.is_file():
        st.download_button("Download the Excel logbook", book.read_bytes(), file_name=book.name)


tab1, tab2 = st.tabs(["Analyse a log", "Fleet logbook"])
with tab1:
    analyse_tab()
with tab2:
    logbook_tab()
