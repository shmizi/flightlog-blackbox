"""Plain-text summary and email for one AnalysisResult.

Shared by the API (email body, summary.txt) and, later, Streamlit.
The Excel logbook and PowerPoint debrief build on the same result.
"""

from .checks import Status
from .pipeline import ANALYSED, DUPLICATE, NO_FLIGHT, REJECTED


def headline(res):
    if res.status == ANALYSED:
        return res.verdict + (" (incomplete log)" if res.incomplete else "")
    return {DUPLICATE: "Already processed", NO_FLIGHT: "No flight", REJECTED: "Rejected"}[res.status]


def summary_text(res):
    lines = [f"FlightLog Black Box: {headline(res)}", f"File: {res.file_name}"]
    if res.flight_date:
        lines.append(f"Flight date (UTC): {res.flight_date}")
    if res.reason:
        lines.append(f"{res.exception_id + ': ' if res.exception_id else ''}{res.reason}")
    if res.status != ANALYSED:
        return "\n".join(lines) + "\n"

    src = "telemetry log, reduced detail" if res.source == "telemetry" else "SD-card log"
    lines.append(f"Source: {src}; armed {res.armed_s:.0f} s, airborne {res.airborne_s:.0f} s; "
                 f"{res.firmware or 'firmware unknown'}")
    found = res.findings
    lines.append("")
    lines.append("Findings:" if found else "Findings: none, all available checks passed.")
    for c in found:
        at = f" at {c.t_flight_s:.0f} s" if c.t_flight_s is not None else ""
        val = f"{c.value:g} {c.unit}".strip() if c.value is not None else ""
        lim = c.critical if c.status == Status.CRITICAL else c.warning
        limit = f" (limit {lim:g})" if lim is not None else ""
        lines.append(f"  [{c.status.value.upper()}] {c.id}. {c.name}: {val}{limit}{at}. {c.detail}")
    na = [c for c in res.checks if c.status == Status.NOT_AVAILABLE]
    if na:
        lines.append("")
        lines.append("Not available:")
        lines += [f"  {c.id}. {c.name}: {c.detail}" for c in na]
    if res.notes:
        lines.append("")
        lines += [f"Note: {n}" for n in res.notes]
    return "\n".join(lines) + "\n"


def email(res):
    """Subject and plain-text body for the team email."""
    return {"subject": f"[FlightLog] {headline(res)}: {res.file_name}", "body": summary_text(res)}
