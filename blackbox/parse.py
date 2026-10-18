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
]


@dataclass
class ParsedLog:
    tables: dict = field(default_factory=dict)   # message name -> DataFrame with a "t" column (s)
    params: dict = field(default_factory=dict)   # PARM name -> last value
    error: str | None = None                     # exception text if the parser stopped early
    bad_bytes: int = 0                           # bytes the reader had to skip as corrupt
    firmware: str | None = None
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
        for text in parsed.tables["MSG"]["Message"]:
            if re.match(r"Ardu\w+ V\d", str(text)):
                parsed.firmware = str(text)
                break
    return parsed
