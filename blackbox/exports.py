"""Files attached to the email besides the summary.

    flight_path.kml        the flight on a map (Google Earth), with each finding pinned where it happened
    suggested_fixes.param  parameter values that fix the findings, in Mission Planner's .param format

Both are optional: no GPS track means no map, no fixable findings means no .param file.
"""

from datetime import datetime, timezone
from xml.sax.saxutils import escape

from .checks import FINDING, Status

# KML colours are aabbggrr.
LINE_COLOURS = {"Healthy": "ff3d8e1e", "Warning": "ff0074e3", "Critical": "ff2530d9"}


def build(res):
    """{file name: bytes} of the extra files for this result."""
    files = {}
    params = param_fixes(res)
    if params:
        files["suggested_fixes.param"] = params.encode("utf-8")
    kml = flight_kml(res)
    if kml:
        files["flight_path.kml"] = kml.encode("utf-8")
    return files


def param_fixes(res):
    """Mission Planner .param text with every suggested value, or None if nothing needs changing."""
    # Comments go on their own lines: Mission Planner and MAVProxy skip lines starting with '#'.
    lines, seen = [], set()
    for c in sorted(res.checks, key=lambda c: c.id):
        if not c.fixes or c.status not in FINDING:
            continue
        lines.append(f"# Check {c.id} ({c.name}): {c.detail}")
        for name, (value, why) in c.fixes.items():
            if name not in seen:
                seen.add(name)
                lines += [f"#   {why}", f"{name},{value:g}"]
        lines.append("")
    if not lines:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    head = [f"# FlightLog Black Box: suggested parameter fixes for {res.file_name} ({stamp})",
            "# Review every line before loading: Mission Planner > Config > Full Parameter List > Load from file,",
            "# then Write Params. Values marked 'x cells' assume the pack the log shows.", ""]
    return "\n".join(head + lines).rstrip() + "\n"


def flight_kml(res):
    """KML of the airborne track with takeoff, landing and findings, or None without GPS."""
    track = res.track
    if len(track) < 2:
        return None
    colour = LINE_COLOURS.get(res.verdict, "ff888888")
    coords = " ".join(f"{lon:.7f},{lat:.7f},{max(alt, 0):.1f}" for _, lat, lon, alt in track)

    def at(t):
        best = min(track, key=lambda p: abs(p[0] - t))
        return f"{best[2]:.7f},{best[1]:.7f},{max(best[3], 0):.1f}"

    marks = [("Takeoff", "start", track[0][0], ""), ("Landing", "end", track[-1][0], "")]
    for c in res.findings:
        if c.t_flight_s is not None and res.takeoff_t is not None:
            style = "crit" if c.status == Status.CRITICAL else "warn"
            marks.append((f"{c.status.value}: {c.name}", style, res.takeoff_t + c.t_flight_s, c.detail))
    placemarks = "".join(
        f"<Placemark><name>{escape(name)}</name><description>{escape(desc)}</description>"
        f"<styleUrl>#{style}</styleUrl><Point><altitudeMode>relativeToGround</altitudeMode>"
        f"<coordinates>{at(t)}</coordinates></Point></Placemark>"
        for name, style, t, desc in marks)
    icon = "http://maps.google.com/mapfiles/kml/paddle/{}.png"
    styles = "".join(
        f'<Style id="{sid}"><IconStyle><Icon><href>{icon.format(img)}</href></Icon></IconStyle></Style>'
        for sid, img in (("start", "grn-circle"), ("end", "blu-square"), ("crit", "red-stars"), ("warn", "ylw-blank")))
    title = f"{res.file_name}: {res.verdict}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"<name>{escape(title)}</name>{styles}"
        f'<Style id="path"><LineStyle><color>{colour}</color><width>4</width></LineStyle></Style>'
        f"<Placemark><name>Flight path</name><styleUrl>#path</styleUrl><LineString><tessellate>1</tessellate>"
        f"<altitudeMode>relativeToGround</altitudeMode><coordinates>{coords}</coordinates></LineString></Placemark>"
        f"{placemarks}</Document></kml>")
