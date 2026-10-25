"""Plain-text and HTML summary and email for one AnalysisResult, plus the wording and colours
the Excel logbook and the Streamlit dashboard share with it.
"""

from html import escape

from .checks import Status
from .pipeline import ANALYSED, PREFLIGHT

COLOURS = {"Healthy": "#1e8e3e", "Warning": "#e37400", "Critical": "#d93025",
           "GO": "#1e8e3e", "GO with warnings": "#e37400", "NO-GO": "#d93025"}
# Light versions for table cells (Excel's standard good / neutral / bad fills).
LIGHT = {"Healthy": "C6EFCE", "Pass": "C6EFCE", "GO": "C6EFCE",
         "Warning": "FFEB9C", "GO with warnings": "FFEB9C",
         "Critical": "FFC7CE", "NO-GO": "FFC7CE", "Not available": "EDEDED"}
GREY = "#5f6368"


def title(status, verdict, incomplete=False):
    """'Critical', 'Healthy (incomplete log)', 'Pre-flight NO-GO', 'Rejected' or 'No flight'."""
    if status == ANALYSED:
        return verdict + (" (incomplete log)" if incomplete else "")
    if status == PREFLIGHT:
        return f"Pre-flight {verdict}"
    return status


def colour(status, verdict):
    """Banner colour: the verdict's colour for a full report, grey for rejected and bench logs."""
    return COLOURS.get(verdict, GREY) if status in (ANALYSED, PREFLIGHT) else GREY


def headline(res):
    return title(res.status, res.verdict, res.incomplete)


def fmt(value, unit):
    """'3.41 V', or '' when there is no value."""
    return "" if value is None else f"{value:g} {unit}".strip()


def _detailed(res):
    return res.status in (ANALYSED, PREFLIGHT)


def _source_line(res):
    if res.status == PREFLIGHT:
        return "parameter file checked before flight"
    src = "telemetry log, reduced detail" if res.source == "telemetry" else "SD-card log"
    drone = f"; drone {res.airframe}" if res.airframe else ""
    return (f"{src}; armed {res.armed_s:.0f} s, airborne {res.airborne_s:.0f} s; "
            f"{res.firmware or 'firmware unknown'}{drone}")


def _not_available(res):
    # Checks that do not apply to this drone (no vision system, no Vcc sensor) are left out.
    return [c for c in res.checks if c.status == Status.NOT_AVAILABLE and c.applies]


def summary_text(res, attachments=()):
    lines = [f"FlightLog Black Box: {headline(res)}", f"File: {res.file_name}"]
    if res.flight_date:
        lines.append(f"Flight date (UTC): {res.flight_date}")
    if res.reason:
        lines.append(f"{res.exception_id + ': ' if res.exception_id else ''}{res.reason}")
    if not _detailed(res):
        return "\n".join(lines) + "\n"

    lines.append(f"Source: {_source_line(res)}")
    found = res.findings
    lines.append("")
    lines.append("Findings:" if found else "Findings: none, all available checks passed.")
    for c in found:
        at = f" at {c.t_flight_s:.0f} s" if c.t_flight_s is not None else ""
        limit = f" (limit {fmt(c.limit, c.unit)})" if c.limit is not None else ""
        value = f" {fmt(c.value, c.unit)}{limit}{at}." if c.value is not None else ""
        lines.append(f"  [{c.status.value.upper()}] {c.id}. {c.name}:{value} {c.detail}")
    if res.actions:
        lines.append("")
        lines.append("Recommended actions:")
        lines += [f"  {i}. {a}" for i, a in enumerate(res.actions, 1)]
    if res.history:
        lines.append("")
        lines += [res.history[0]] + [f"  {h}" for h in res.history[1:]]
    na = _not_available(res)
    if na:
        lines.append("")
        lines.append("Not available:")
        lines += [f"  {c.id}. {c.name}: {c.detail}" for c in na]
    if res.notes:
        lines.append("")
        lines += [f"Note: {n}" for n in res.notes]
    if attachments:
        lines.append("")
        lines.append("Attached: " + ", ".join(attachments))
    return "\n".join(lines) + "\n"


def summary_html(res, attachments=()):
    head = headline(res)
    td = "padding:6px 10px;border-bottom:1px solid #e0e0e0;vertical-align:top;"
    small = f'style="color:{GREY};font-size:12px"'
    out = [
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#202124;max-width:760px">',
        f'<div style="background:{colour(res.status, res.verdict)};color:#fff;padding:14px 18px;border-radius:6px">'
        f'<div style="font-size:12px;opacity:.9">FlightLog Black Box</div>'
        f'<div style="font-size:22px;font-weight:bold">{escape(head)}</div>'
        f'<div style="font-size:13px">{escape(res.file_name)}</div></div>',
    ]
    meta = []
    if res.flight_date:
        meta.append(f"Flight date (UTC): {escape(res.flight_date)}")
    if _detailed(res):
        meta.append(escape(_source_line(res)))
    if res.reason:
        meta.append(f"<b>{escape(res.exception_id or '')}</b> {escape(res.reason)}")
    if meta:
        out.append(f'<p style="color:{GREY};margin:12px 0">' + "<br>".join(meta) + "</p>")

    if _detailed(res):
        found = res.findings
        if found:
            out.append('<h3 style="margin:18px 0 6px">Findings</h3>'
                       '<table style="border-collapse:collapse;width:100%;font-size:13px">'
                       f'<tr style="background:#f1f3f4"><th style="{td}text-align:left">Check</th>'
                       f'<th style="{td}text-align:left">Measured</th><th style="{td}text-align:left">Limit</th>'
                       f'<th style="{td}text-align:left">When</th><th style="{td}text-align:left">Detail</th></tr>')
            for c in found:
                badge = COLOURS["Critical"] if c.status == Status.CRITICAL else COLOURS["Warning"]
                when = f"{c.t_flight_s:.0f} s" if c.t_flight_s is not None else ""
                out.append(
                    f'<tr><td style="{td}"><span style="color:{badge};font-weight:bold">{c.status.value}</span><br>'
                    f'{c.id}. {escape(c.name)}</td><td style="{td}">{escape(fmt(c.value, c.unit))}</td>'
                    f'<td style="{td}">{escape(fmt(c.limit, c.unit))}</td><td style="{td}">{when}</td>'
                    f'<td style="{td}">{escape(c.detail)}</td></tr>')
            out.append("</table>")
        else:
            out.append('<p><b>All available checks passed.</b></p>')
        if res.actions:
            out.append('<h3 style="margin:18px 0 6px">Recommended actions</h3><ol style="margin:0;padding-left:20px">')
            out += [f'<li style="margin:4px 0">{escape(a)}</li>' for a in res.actions]
            out.append("</ol>")
        if res.history:
            out.append(f'<h3 style="margin:18px 0 6px">Earlier flights</h3><p style="margin:0">{escape(res.history[0])}</p>')
            if len(res.history) > 1:
                out.append('<ul style="margin:4px 0;padding-left:20px">'
                           + "".join(f"<li>{escape(h)}</li>" for h in res.history[1:]) + "</ul>")
        passed = [c for c in res.checks if c.status == Status.PASS]
        if passed:
            out.append(f'<p {small}>Passed: ' + ", ".join(escape(c.name) for c in passed) + "</p>")
        na = _not_available(res)
        if na:
            out.append(f'<p {small}>Not available: '
                       + "; ".join(f"{escape(c.name)} ({escape(c.detail)})" for c in na) + "</p>")
        for n in res.notes:
            out.append(f'<p {small}>Note: {escape(n)}</p>')
    if attachments:
        out.append(f'<p style="color:{GREY};font-size:11px;margin-top:20px">Attached: '
                   + ", ".join(escape(a) for a in attachments) + "</p>")
    out.append("</div>")
    return "".join(out)


ATTACHMENT_NOTES = {
    "summary.txt": "summary.txt (this report as text)",
    "flight_path.kml": "flight_path.kml (open in Google Earth to see the flight and where each finding happened)",
    "suggested_fixes.param": "suggested_fixes.param (corrected parameters to review and load in Mission Planner)",
    "flight_logbook.xlsx": "flight_logbook.xlsx (the team's logbook of every flight so far, with trend charts)",
}


def email(res, attachments=()):
    """Subject plus plain-text and HTML bodies for the team email."""
    notes = [ATTACHMENT_NOTES.get(a, a) for a in attachments]
    return {"subject": f"[FlightLog] {headline(res)}: {res.file_name}",
            "body": summary_text(res, notes), "html": summary_html(res, notes)}
