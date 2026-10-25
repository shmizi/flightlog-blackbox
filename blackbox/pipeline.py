"""analyze(log_path): the one entry point n8n, FastAPI and Streamlit all call."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import advice, history, parse, tlog, validate, verdict
from .checks import Ctx, run_all
from .config import load_config
from .windows import mask, segment

ANALYSED = "Analysed"
REJECTED = "Rejected"
NO_FLIGHT = "No flight"
PREFLIGHT = "Pre-flight check"
MAX_TRACK_POINTS = 1500


@dataclass
class AnalysisResult:
    file_name: str
    sha256: str | None = None
    size_bytes: int = 0
    status: str = ANALYSED
    verdict: str | None = None          # Healthy / Warning / Critical, only when Analysed
    incomplete: bool = False            # E2: analysed, but the log is cut short or corrupt
    exception_id: str | None = None     # E1..E5 for the Exceptions sheet
    reason: str = ""
    repeat_of: str | None = None        # E4: this exact file was analysed before ("<date> as <name>")
    source: str | None = None           # "dataflash" (.bin) or "telemetry" (.tlog, reduced detail)
    flight_date: str | None = None      # UTC, ISO 8601, from GPS time (.bin) or the ground station clock (.tlog)
    firmware: str | None = None
    airframe: str | None = None         # board + CPU serial: recognises the same drone across logs
    armed_s: float = 0.0
    airborne_s: float = 0.0
    checks: list = field(default_factory=list)
    actions: list = field(default_factory=list)   # recommended next steps (blackbox.advice)
    history: list = field(default_factory=list)   # comparison with this drone's earlier flights (set by the API)
    metrics: dict = field(default_factory=dict)   # numbers tracked across flights (blackbox.history)
    notes: list = field(default_factory=list)
    track: list = field(default_factory=list, repr=False)   # [(t, lat, lon, alt above takeoff)] for the map
    takeoff_t: float | None = None
    parsed: object = field(default=None, repr=False)        # the ParsedLog, for graphs (not saved)

    @property
    def findings(self):
        return verdict.findings(self.checks)

    def to_dict(self):
        d = {k: v for k, v in self.__dict__.items() if k not in ("checks", "track", "takeoff_t", "parsed")}
        d["checks"] = [c.to_dict() for c in self.checks]
        d["findings"] = [c.id for c in self.findings]
        return d


def analyze(log_path, config=None, known_hashes=None):
    """Validate, parse and check one log.

    config: dict or path to a thresholds file (default thresholds.yaml).
    known_hashes: {sha256: date} of logs already in the logbook, for E4. A repeat upload is
    analysed again in full and flagged with a note, so re-running a log always gives a report.
    """
    cfg = config if isinstance(config, dict) else load_config(config)
    path = Path(log_path)
    res = AnalysisResult(file_name=path.name, size_bytes=path.stat().st_size)

    kind = validate.log_kind(path)
    if kind == validate.PARAMS:
        from . import preflight      # imported here: preflight builds on this module's AnalysisResult
        res.source = kind
        return preflight.run(path, cfg, res)
    if kind == validate.RAW_LINK:
        return _reject(res, "E1", "Raw link capture (.rlog) has no timestamps; upload the matching .tlog "
                                  "or the .bin from the SD card instead")
    if kind is None:
        return _reject(res, "E1", "Not an ArduPilot log: neither a DataFlash .bin nor a MAVLink .tlog")
    res.source = kind

    res.sha256 = validate.sha256(path)
    res.repeat_of = (known_hashes or {}).get(res.sha256)
    if res.repeat_of:
        res.notes.append(f"This exact file was already analysed ({res.repeat_of}). It was analysed again, "
                         "but is counted only once in the logbook and in comparisons.")

    parsed = res.parsed = load_log(path, kind)
    res.firmware, res.airframe = parsed.firmware, parsed.airframe
    if parsed.start_unix:
        res.flight_date = datetime.fromtimestamp(parsed.start_unix, timezone.utc).isoformat(timespec="seconds")
    if not parsed.tables:
        return _reject(res, "E2", f"Log could not be parsed ({parsed.error or 'no records'})")

    win = segment(parsed, cfg)
    res.armed_s, res.airborne_s = round(win.armed_s, 1), round(win.airborne_s, 1)
    if not win.armed:
        res.status, res.exception_id, res.reason = NO_FLIGHT, "E5", "Never armed (bench log)"
        return res

    corrupt = []
    if parsed.error:
        corrupt.append(f"parser stopped early: {parsed.error}")
    if parsed.bad_bytes:
        corrupt.append(f"{parsed.bad_bytes} corrupt bytes skipped")
    if win.ends_armed:
        corrupt.append("log ends while still armed")
    res.incomplete = bool(corrupt)

    min_s = cfg["validation"]["min_flight_s"]
    if win.airborne_s < min_s:
        if res.incomplete:
            return _reject(res, "E2", f"Only {win.airborne_s:.0f} s of flight data remains (< {min_s} s); "
                                      + "; ".join(corrupt))
        return _reject(res, None, f"Flight too short: {win.airborne_s:.0f} s airborne (< {min_s} s)")

    if res.incomplete:
        res.exception_id = "E2"
        res.reason = "Incomplete: " + "; ".join(corrupt)
    if kind == validate.TELEMETRY:
        res.notes.append("Telemetry log (2-4 Hz): reduced detail. Spikes shorter than about a second can be "
                         "missed, hard landing uses descent rate only, and errors come from STATUSTEXT.")
        if parsed.max_link_gap_s > 3.0:
            res.notes.append(f"Telemetry link dropped for up to {parsed.max_link_gap_s:.0f} s while armed")
    if win.arming_inferred:
        res.notes.append("No ARM/EV records: arming inferred from throttle output")
    if win.airborne_inferred:
        res.notes.append("No NOT_LANDED events: takeoff and landing inferred from altitude")

    res.checks = run_all(Ctx(parsed.tables, parsed.params, win, cfg))
    res.verdict = verdict.decide(res.checks)
    res.actions = advice.recommend(res)
    res.takeoff_t = win.takeoff
    res.track = _track(parsed, win)
    res.metrics = history.metrics(res, _battery_resistance(parsed, win))
    return res


def load_log(path, kind=None):
    """Parse a .bin or .tlog into the shared table format (ParsedLog)."""
    kind = kind or validate.log_kind(path)
    return parse.load(path) if kind == validate.DATAFLASH else tlog.load(path)


def _track(parsed, win):
    """Downsampled GPS track while armed, altitude relative to the ground at takeoff."""
    pos = parsed.tables.get("POS")
    if pos is None or not {"Lat", "Lng"} <= set(pos.columns):
        return []
    alt_col = next((c for c in ("RelOriginAlt", "RelHomeAlt", "Alt") if c in pos), None)
    g = pos[mask(pos["t"], win.armed) & (pos["Lat"].abs() + pos["Lng"].abs() > 0)]
    if len(g) < 2 or alt_col is None:
        return []
    alt = g[alt_col].to_numpy(float)
    ground = alt[g["t"].to_numpy(float) <= win.takeoff]
    alt = alt - (np.median(ground[-20:]) if len(ground) else alt[0])
    step = max(1, len(g) // MAX_TRACK_POINTS)
    return [(float(t), float(la), float(lo), float(a))
            for t, la, lo, a in zip(g["t"].to_numpy()[::step], g["Lat"].to_numpy()[::step],
                                    g["Lng"].to_numpy()[::step], alt[::step])]


def _battery_resistance(parsed, win):
    """ArduPilot's estimate of pack internal resistance while airborne, in milliohms."""
    bat = parsed.tables.get("BAT")
    if bat is None or "Res" not in bat:
        return None
    r = bat.loc[mask(bat["t"], win.airborne), "Res"]
    r = r[(r > 0) & (r < 5)]
    return round(float(r.median()) * 1000, 1) if len(r) else None


def _reject(res, exception_id, reason):
    res.status, res.exception_id, res.reason = REJECTED, exception_id, reason
    return res
