"""analyze(log_path): the one entry point n8n, FastAPI and Streamlit all call."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import parse, tlog, validate, verdict
from .checks import Ctx, run_all
from .config import load_config
from .windows import segment

ANALYSED = "Analysed"
REJECTED = "Rejected"
DUPLICATE = "Duplicate"
NO_FLIGHT = "No flight"


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
    source: str | None = None           # "dataflash" (.bin) or "telemetry" (.tlog, reduced detail)
    flight_date: str | None = None      # UTC, ISO 8601; telemetry logs carry wall-clock time
    firmware: str | None = None
    armed_s: float = 0.0
    airborne_s: float = 0.0
    checks: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def findings(self):
        return verdict.findings(self.checks)

    def to_dict(self):
        d = {k: v for k, v in self.__dict__.items() if k != "checks"}
        d["checks"] = [c.to_dict() for c in self.checks]
        d["findings"] = [c.id for c in self.findings]
        return d


def analyze(log_path, config=None, known_hashes=None):
    """Validate, parse and check one log.

    config: dict or path to a thresholds file (default thresholds.yaml).
    known_hashes: {sha256: date} of logs already in the logbook, for E4.
    """
    cfg = config if isinstance(config, dict) else load_config(config)
    path = Path(log_path)
    res = AnalysisResult(file_name=path.name, size_bytes=path.stat().st_size)

    kind = validate.log_kind(path)
    if kind == validate.RAW_LINK:
        return _reject(res, "E1", "Raw link capture (.rlog) has no timestamps; upload the matching .tlog "
                                  "or the .bin from the SD card instead")
    if kind is None:
        return _reject(res, "E1", "Not an ArduPilot log: neither a DataFlash .bin nor a MAVLink .tlog")
    res.source = kind

    res.sha256 = validate.sha256(path)
    seen = validate.find_duplicate(res.sha256, known_hashes)
    if seen is not None:
        res.status, res.exception_id, res.reason = DUPLICATE, "E4", f"Already processed on {seen}"
        return res

    parsed = parse.load(path) if kind == validate.DATAFLASH else tlog.load(path)
    res.firmware = parsed.firmware
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
    return res


def _reject(res, exception_id, reason):
    res.status, res.exception_id, res.reason = REJECTED, exception_id, reason
    return res
