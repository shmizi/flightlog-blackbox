"""Compare a flight with the same drone's earlier flights.

The drone is recognised by its flight controller's board name and CPU serial number, which the
firmware prints at boot, so this works across .bin and .tlog files without any manual labelling.
"""

import statistics

# metric key: (label, unit, True if a higher value is worse)
METRICS = {
    "vibration": ("Vibration", "m/s²", True),
    "min_cell_v": ("Lowest cell voltage", "V", False),
    "sag_v": ("Voltage sag", "V/cell", True),
    "batt_res_mohm": ("Battery resistance", "mΩ", True),
    "hover_throttle": ("Hover throttle", "", True),
    "motor_spread_us": ("Motor imbalance", "µs", True),
    "alt_error_m": ("Altitude error", "m", True),
    "compass_interf_pct": ("Compass interference", "%", True),
}
CHECK_FOR_METRIC = {"vibration": 1, "min_cell_v": 3, "sag_v": 4, "hover_throttle": 6, "motor_spread_us": 10,
                    "alt_error_m": 7, "compass_interf_pct": 16}


def metrics(res, batt_res_mohm=None):
    """The numbers worth tracking from one analysed flight."""
    by_id = {c.id: c for c in res.checks}
    m = {"airframe": res.airframe, "sha256": res.sha256, "file_name": res.file_name, "flight_date": res.flight_date,
         "verdict": res.verdict, "source": res.source, "airborne_s": res.airborne_s}
    for key, cid in CHECK_FOR_METRIC.items():
        c = by_id.get(cid)
        m[key] = c.value if c is not None and c.value is not None else None
    m["batt_res_mohm"] = batt_res_mohm
    return m


def compare(current, previous, window=5, min_change=0.2):
    """Plain-language lines comparing `current` with the drone's last `window` flights."""
    drone = current.get("airframe")
    if not drone:
        return []
    # Matched by drone, not file name: SD cards restart their numbering, so 00000001.BIN repeats.
    # A repeat upload of the same file is left out by its SHA-256, so it is never compared with itself.
    same = [p for p in previous if p.get("airframe") == drone
            and not (current.get("sha256") and p.get("sha256") == current.get("sha256"))]
    if not same:
        return [f"First flight on record for this drone ({drone})."]
    recent = same[-window:]
    lines = []
    for key, (label, unit, higher_is_worse) in METRICS.items():
        now = current.get(key)
        before = [p[key] for p in recent if p.get(key) is not None]
        if now is None or not before:
            continue
        typical = statistics.median(before)
        if typical == 0:
            continue
        change = (now - typical) / abs(typical)
        if abs(change) < min_change:
            continue
        worse = (change > 0) == higher_is_worse
        word = "worse" if worse else "better"
        lines.append(f"{label}: {_fmt(now, unit)} vs typical {_fmt(typical, unit)} ({change:+.0%}, {word})")
    head = f"Compared with the last {len(recent)} flight{'s' if len(recent) > 1 else ''} of this drone ({drone}):"
    return [head] + (lines or ["No notable change."])


def _fmt(value, unit):
    if unit == "":
        return f"{value:.0%}"
    return f"{value:.3g} {unit}".strip()
