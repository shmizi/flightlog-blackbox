"""The team's Excel flight logbook.

    Flights      one row per analysed flight, one column per check
    Findings     one row per Warning or Critical finding
    Exceptions   rejected files, bench logs, incomplete logs and repeat uploads, with the reason
    Pre-flight   GO / NO-GO checks of .param files
    Trends       key numbers per flight, with charts across flights

The workbook is rebuilt from logbook.jsonl (one record per processed file) every time instead of
being edited in place. openpyxl drops existing charts when it re-saves a workbook, so rebuilding is
the only way to keep the Trends charts; it also makes E6 simple: if Excel has the file open, the
new version goes to flight_logbook_pending.xlsx, and the next successful save includes everything.
"""

import json
import time
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .checks import is_finding, limit_for
from .history import METRICS
from .pipeline import ANALYSED, NO_FLIGHT, PREFLIGHT, REJECTED
from .report import LIGHT

FILLS = {name: PatternFill("solid", fgColor=colour) for name, colour in LIGHT.items()}
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(bold=True, color="FFFFFF")
CHECK_NAMES = {1: "Vibration", 2: "Clipping", 3: "Cell voltage", 4: "Voltage sag", 5: "Board supply",
               6: "Hover throttle", 7: "Altitude hold", 8: "Attitude", 9: "Estimator", 10: "Motor balance",
               11: "Hard landing", 12: "Errors", 13: "External nav", 14: "Configuration",
               15: "Vibration spectrum", 16: "Compass interference"}
TREND_CHARTS = [("min_cell_v", "Lowest cell voltage (V)"), ("vibration", "Vibration (m/s²)"),
                ("batt_res_mohm", "Battery resistance (mΩ)"), ("motor_spread_us", "Motor imbalance (µs)")]


def record(res, processed, event=None):
    """One logbook line. event: None for a normal entry, or "repeat" for an E4 repeat upload."""
    return {"processed": processed, "event": event, "result": res.to_dict()}


def append(path, rec):
    """Add one record to a JSON-lines file (logbook.jsonl, history.jsonl)."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load(path):
    """All records of a JSON-lines file, or [] if it does not exist yet."""
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def save(records, path, retries=3, wait_s=10.0):
    """Write the workbook; if it is open in Excel (PermissionError), retry, then save a pending copy (E6).

    Returns (path written, True if it is the pending copy).
    """
    path = Path(path)
    pending = path.with_name(path.stem + "_pending.xlsx")
    wb = build(records)
    for attempt in range(retries):
        try:
            wb.save(path)
            pending.unlink(missing_ok=True)      # everything in it is in the main file now
            return path, False
        except PermissionError:
            if attempt < retries - 1:
                time.sleep(wait_s)
    wb.save(pending)
    return pending, True


def flights(records):
    """Analysed flights, each counted once (repeat uploads left out), in processing order."""
    return [r for r in records if r["result"]["status"] == ANALYSED and r.get("event") != "repeat"]


def by_flight_date(records):
    return sorted(records, key=lambda r: r["result"].get("flight_date") or r["processed"])


def preflights(records):
    return [r for r in records if r["result"]["status"] == PREFLIGHT]


EXCEPTION_HEADERS = ["Processed", "File", "Exception", "Outcome", "Reason"]


def exceptions(records):
    """[processed, file, exception id, outcome, reason] for every file that was not a clean flight."""
    rows = []
    for r in records:
        res = r["result"]
        if r.get("event") == "repeat":
            rows.append([r["processed"], res["file_name"], "E4", "Analysed again (counted once)",
                         f"Same file as {res.get('repeat_of')}"])
        elif res["status"] in (REJECTED, NO_FLIGHT):
            rows.append([r["processed"], res["file_name"], res.get("exception_id") or "", res["status"], res["reason"]])
        elif res["status"] == ANALYSED and res.get("incomplete"):
            rows.append([r["processed"], res["file_name"], "E2", "Analysed (incomplete)", res["reason"]])
    return rows


def issues(result):
    """'Check: detail' for each Warning or Critical check of a stored result."""
    return [f"{c['name']}: {c['detail']}" for c in result["checks"] if is_finding(c["status"])]


def build(records):
    wb = Workbook()
    flown = flights(records)
    _flights(wb.active, flown)
    _findings(wb.create_sheet("Findings"), flown)
    _sheet(wb.create_sheet(), "Exceptions", EXCEPTION_HEADERS, exceptions(records), widths={"Reason": 90})
    _preflight(wb.create_sheet("Pre-flight"), preflights(records))
    _trends(wb.create_sheet("Trends"), flown)
    return wb


def _sheet(ws, title, headers, rows, widths=None, fill_cols=()):
    ws.title = title
    ws.append(headers)
    for cell in ws[1]:
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for row in rows:
        ws.append(row)
    for col in fill_cols:
        for (cell,) in ws.iter_rows(min_row=2, min_col=col, max_col=col):
            if cell.value in FILLS:
                cell.fill = FILLS[cell.value]
    for i, h in enumerate(headers, 1):
        width = (widths or {}).get(h) or min(max(len(str(h)), *(len(str(r[i - 1] or "")) for r in rows), 10) + 2, 60)
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = ws.dimensions


def _flights(ws, flights):
    headers = ["Processed", "Flight date (UTC)", "File", "Drone", "Source", "Verdict", "Incomplete",
               "Airborne (s)", *[f"{i} {n}" for i, n in CHECK_NAMES.items()], "Findings", "SHA-256"]
    rows = []
    for r in flights:
        res = r["result"]
        status = {c["id"]: c["status"] for c in res["checks"]}
        rows.append([r["processed"], res.get("flight_date"), res["file_name"], res.get("airframe"),
                     res.get("source"), res.get("verdict"), "Yes" if res.get("incomplete") else "",
                     res.get("airborne_s"), *[status.get(i, "") for i in CHECK_NAMES],
                     len(res.get("findings", [])), res.get("sha256")])
    _sheet(ws, "Flights", headers, rows, widths={"SHA-256": 20},
           fill_cols=[6, *range(9, 9 + len(CHECK_NAMES))])
    for col in range(9, 9 + len(CHECK_NAMES)):
        ws.column_dimensions[get_column_letter(col)].width = 13


def _findings(ws, flights):
    headers = ["Processed", "Flight date (UTC)", "File", "Check", "Status", "Value", "Unit", "Limit",
               "Time in flight (s)", "Detail"]
    rows = []
    for r in flights:
        res = r["result"]
        for c in res["checks"]:
            if is_finding(c["status"]):
                rows.append([r["processed"], res.get("flight_date"), res["file_name"], f"{c['id']}. {c['name']}",
                             c["status"], c["value"], c["unit"], limit_for(c["status"], c["warning"], c["critical"]),
                             c["t_flight_s"], c["detail"]])
    _sheet(ws, "Findings", headers, rows, widths={"Detail": 90}, fill_cols=[5])


def _preflight(ws, records):
    rows = [[r["processed"], r["result"]["file_name"], r["result"].get("verdict"),
             "; ".join(issues(r["result"])) or "None"] for r in records]
    _sheet(ws, "Pre-flight", ["Processed", "File", "Verdict", "Issues"], rows, widths={"Issues": 100}, fill_cols=[3])


def _trends(ws, flights):
    keys = list(METRICS)
    headers = ["Flight", "Flight date (UTC)", "Drone", *[f"{METRICS[k][0]} ({METRICS[k][1]})".replace(" ()", "")
                                                        for k in keys]]
    rows = []
    for r in by_flight_date(flights):
        res, m = r["result"], r["result"].get("metrics", {})
        rows.append([res["file_name"], res.get("flight_date"), res.get("airframe"), *[m.get(k) for k in keys]])
    _sheet(ws, "Trends", headers, rows)
    if len(rows) < 2:
        ws.cell(row=len(rows) + 3, column=1, value="Charts appear once the logbook has two or more flights.")
        return
    first_metric_col = 4
    for n, (key, title) in enumerate(TREND_CHARTS):
        col = first_metric_col + keys.index(key)
        chart = LineChart()
        chart.title, chart.height, chart.width = title, 7, 14
        chart.y_axis.title, chart.x_axis.title = title, "Flight"
        chart.add_data(Reference(ws, min_col=col, min_row=1, max_row=len(rows) + 1), titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=len(rows) + 1))
        chart.legend = None
        anchor_row = len(rows) + 4 + (n // 2) * 16
        ws.add_chart(chart, f"{'A' if n % 2 == 0 else 'H'}{anchor_row}")
