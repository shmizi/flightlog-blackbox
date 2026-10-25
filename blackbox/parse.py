"""Read a DataFlash .bin log into one pandas DataFrame per message type."""

from dataclasses import dataclass, field
import contextlib
import io
import re

import pandas as pd
from pymavlink import DFReader

TYPES = [
    "VIBE", "BAT", "POWR", "CTUN", "ATT", "XKF4", "NKF4", "RCOU", "IMU",
    "ERR", "EV", "ARM", "MODE", "MSG", "PARM", "VISP", "VISV", "VISO",
    "GPS", "POS", "MAG", "ISBH", "ISBD",
]

GPS_EPOCH = 315964800      # 1980-01-06 in Unix time
GPS_LEAP_SECONDS = 18      # GPS time runs ahead of UTC by this much (since 2017)
BOARD_LINE = re.compile(r"^([A-Za-z0-9_\-]+) ([0-9A-F]{8} [0-9A-F]{8} [0-9A-F]{8})\s*$")
FIRMWARE_LINE = re.compile(r"Ardu\w+ V\d")


def firmware(messages):
    """'ArduCopter V4.5.7 (...)' from the boot messages, or None."""
    return next((str(t) for t in messages if FIRMWARE_LINE.match(str(t))), None)


def airframe_id(messages):
    """'BOTWINGF405 …38303936' from the boot message that names the board and its unique CPU ID."""
    for text in messages:
        m = BOARD_LINE.match(str(text))
        if m:
            return f"{m.group(1)} …{m.group(2)[-8:]}"
    return None


@dataclass
class ParsedLog:
    tables: dict = field(default_factory=dict)   # message name -> DataFrame with a "t" column (s)
    params: dict = field(default_factory=dict)   # PARM name -> last value
    error: str | None = None                     # exception text if the parser stopped early
    bad_bytes: int = 0                           # bytes the reader had to skip as corrupt
    firmware: str | None = None
    airframe: str | None = None                  # board name + CPU serial: the same drone across logs
    source: str = "dataflash"                    # "dataflash" (.bin) or "telemetry" (.tlog)
    start_unix: float | None = None              # wall-clock start, when the log carries it
    max_link_gap_s: float = 0.0                  # telemetry only: longest gap in autopilot heartbeats while armed

    @property
    def log_start(self):
        starts = [df["t"].iloc[0] for df in self.tables.values() if len(df)]
        return min(starts) if starts else 0.0

    @property
    def log_end(self):
        ends = [df["t"].iloc[-1] for df in self.tables.values() if len(df)]
        return max(ends) if ends else 0.0


def load(path):
    """Parse a .bin log.

    Opens DFReader_binary directly instead of mavutil.mavlink_connection,
    which picks the reader by file extension and would misread an upload
    saved under a temp name without ".bin".
    """
    parsed = ParsedLog()
    rows = {t: [] for t in TYPES}
    noise = io.StringIO()   # DFReader prints "Skipped N bad bytes" to stderr instead of raising
    log = None
    try:
        with contextlib.redirect_stderr(noise), contextlib.redirect_stdout(noise):
            log = DFReader.DFReader_binary(path, zero_time_base=False)
            while (m := log.recv_match(type=TYPES)) is not None:
                d = m.to_dict()
                d.pop("mavpackettype", None)
                rows[m.get_type()].append(d)
    except Exception as exc:  # keep whatever parsed before the broken record (E2)
        parsed.error = f"{type(exc).__name__}: {exc}"
    finally:
        if log is not None:
            # Release the mmap so Windows can delete or overwrite the uploaded file.
            with contextlib.suppress(Exception):
                log.close()

    parsed.bad_bytes = sum(int(n) for n in re.findall(r"Skipped (\d+) bad bytes", noise.getvalue()))

    for name, r in rows.items():
        if not r:
            continue
        df = pd.DataFrame(r)
        if "TimeUS" not in df:
            continue
        df["t"] = df["TimeUS"].astype("float64") / 1e6
        parsed.tables[name] = df.sort_values("t", kind="stable").reset_index(drop=True)

    if "PARM" in parsed.tables:
        p = parsed.tables["PARM"]
        parsed.params = {str(n): float(v) for n, v in zip(p["Name"], p["Value"])}
    if "MSG" in parsed.tables:
        parsed.firmware = firmware(parsed.tables["MSG"]["Message"])
        parsed.airframe = airframe_id(parsed.tables["MSG"]["Message"])
    parsed.start_unix = _gps_start_time(parsed)
    return parsed


def _gps_start_time(parsed):
    """Wall-clock time of the log start, from the first GPS fix (week + ms of week)."""
    gps = parsed.tables.get("GPS")
    if gps is None or not {"Status", "GWk", "GMS"} <= set(gps.columns):
        return None
    fix = gps[(gps["Status"] >= 3) & (gps["GWk"] > 0)]
    if fix.empty:
        return None
    row = fix.iloc[0]
    unix = GPS_EPOCH + float(row["GWk"]) * 604800 + float(row["GMS"]) / 1000 - GPS_LEAP_SECONDS
    return unix - (float(row["t"]) - parsed.log_start)
