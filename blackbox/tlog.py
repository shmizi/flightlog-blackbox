"""Read a ground-station telemetry log (.tlog) into the same tables as parse.load.

A .tlog is the MAVLink stream the ground station recorded over the radio,
each packet prefixed with an 8-byte big-endian receive time in microseconds.
It arrives at only 2-4 Hz, so the tables built here carry the DataFlash
names the checks expect, but with less detail:

    VIBE  <- VIBRATION                 BAT  <- SYS_STATUS (no VoltR)
    POWR  <- POWER_STATUS              CTUN <- VFR_HUD + GLOBAL_POSITION_INT + NAV_CONTROLLER_OUTPUT
    ATT   <- ATTITUDE + NAV_CONTROLLER_OUTPUT (desired angles)
    XKF4  <- EKF_STATUS_REPORT         RCOU <- SERVO_OUTPUT_RAW
    ARM, MODE <- HEARTBEAT             ERR, MSG <- STATUSTEXT
    PARM  <- PARAM_VALUE (only if the ground station downloaded parameters)

No IMU table: 2 Hz is far too slow to catch a landing impact.
"""

from collections import Counter, defaultdict
import contextlib
import io
import math
import re

import numpy as np
import pandas as pd
from pymavlink import mavutil

from .parse import ParsedLog

WANTED = [
    "HEARTBEAT", "VIBRATION", "SYS_STATUS", "POWER_STATUS", "VFR_HUD", "GLOBAL_POSITION_INT",
    "NAV_CONTROLLER_OUTPUT", "ATTITUDE", "EKF_STATUS_REPORT", "SERVO_OUTPUT_RAW", "STATUSTEXT",
    "PARAM_VALUE",
]

MAV_TYPE_GCS = 6
MAV_AUTOPILOT_INVALID = 8
MAV_MODE_FLAG_SAFETY_ARMED = 128

# ArduPilot STATUSTEXT messages that stand in for the ERR records a .bin would have:
# (pattern matched at the start of the text, ERR subsystem, ERR code). PreArm text never matches.
STATUSTEXT_ERRORS = [
    (r"Potential Thrust Loss", 25, 1),
    (r"Crash", 12, 1),
    (r"EKF variance", 16, 2),
    (r"EKF Failsafe", 17, 1),
    (r"Radio Failsafe Cleared", 5, 0),
    (r"Radio Failsafe", 5, 1),
    (r"Battery \d+ is (low|critical)", 6, 1),
    (r"GPS Glitch", 11, 2),
    (r"Glitch cleared", 11, 0),
    (r"Vibration compensation ON", 29, 1),
]
_ERROR_PATTERNS = [(re.compile(p, re.I), sub, code) for p, sub, code in STATUSTEXT_ERRORS]


def load(path):
    parsed = ParsedLog(source="telemetry")
    raw = []                                   # (type, (sysid, compid), t, fields)
    noise = io.StringIO()
    try:
        with contextlib.redirect_stderr(noise), contextlib.redirect_stdout(noise):
            # mavmmaplog directly: mavlink_connection would pick the DataFlash reader for a ".bin" name.
            log = mavutil.mavmmaplog(str(path))
            while (msg := log.recv_match(type=WANTED)) is not None:
                raw.append((msg.get_type(), (msg.get_srcSystem(), msg.get_srcComponent()),
                            float(msg._timestamp), msg.to_dict()))
    except Exception as exc:
        parsed.error = f"{type(exc).__name__}: {exc}"
    finally:
        with contextlib.suppress(Exception):
            log.close()

    autopilot = _autopilot_source(raw)
    if autopilot is None:
        return parsed
    by_type = defaultdict(list)
    for mtype, src, t, d in raw:
        if src == autopilot or (mtype == "PARAM_VALUE" and src[0] == autopilot[0]):
            by_type[mtype].append((t, d))

    tables = parsed.tables
    _heartbeat(by_type["HEARTBEAT"], tables, parsed)
    _statustext(by_type["STATUSTEXT"], tables, parsed)
    _simple(tables, "VIBE", by_type["VIBRATION"], _vibe_rows)
    _simple(tables, "BAT", by_type["SYS_STATUS"], _bat_rows)
    _simple(tables, "POWR", by_type["POWER_STATUS"], _powr_rows)
    _simple(tables, "XKF4", by_type["EKF_STATUS_REPORT"], _ekf_rows)
    _simple(tables, "RCOU", [(t, d) for t, d in by_type["SERVO_OUTPUT_RAW"] if d.get("port", 0) == 0], _rcou_rows)
    if by_type["PARAM_VALUE"]:
        parsed.params = {str(d["param_id"]): float(d["param_value"]) for _, d in by_type["PARAM_VALUE"]}
    _ctun(by_type, tables, parsed.params)
    _att(by_type, tables)

    if tables:
        parsed.start_unix = parsed.log_start
    return parsed


def _autopilot_source(raw):
    """The (sysid, compid) of the flight controller: most heartbeats from a non-GCS autopilot."""
    beats = Counter(src for mtype, src, _, d in raw
                    if mtype == "HEARTBEAT" and d["type"] != MAV_TYPE_GCS and d["autopilot"] != MAV_AUTOPILOT_INVALID)
    return beats.most_common(1)[0][0] if beats else None


def _frame(rows):
    df = pd.DataFrame(rows)
    return df.sort_values("t", kind="stable").reset_index(drop=True)


def _simple(tables, name, msgs, convert):
    rows = [r for t, d in msgs for r in convert(t, d)]
    if rows:
        tables[name] = _frame(rows)


def _heartbeat(msgs, tables, parsed):
    arm, mode = [], []
    armed, custom, prev_t = None, None, None
    for t, d in msgs:
        a = bool(d["base_mode"] & MAV_MODE_FLAG_SAFETY_ARMED)
        if a and armed and prev_t is not None:   # gap between two armed heartbeats in a row
            parsed.max_link_gap_s = max(parsed.max_link_gap_s, t - prev_t)
        if a != armed:
            arm.append({"t": t, "ArmState": int(a)})
            armed = a
        if d["custom_mode"] != custom:
            custom = d["custom_mode"]
            mode.append({"t": t, "Mode": custom, "ModeNum": custom})
        prev_t = t
    # A heartbeat that starts out disarmed is the normal case, not an event.
    if arm and arm[0]["ArmState"] == 0:
        arm = arm[1:]
    if arm:
        tables["ARM"] = _frame(arm)
    if mode:
        tables["MODE"] = _frame(mode)


def _statustext(msgs, tables, parsed):
    texts, errs = [], []
    for t, d in msgs:
        text = str(d.get("text", "")).strip()
        texts.append({"t": t, "Message": text})
        for pat, sub, code in _ERROR_PATTERNS:
            if pat.match(text):
                errs.append({"t": t, "Subsys": sub, "ECode": code, "Message": text})
                break
        if parsed.firmware is None and re.match(r"Ardu\w+ V\d", text):
            parsed.firmware = text
    if texts:
        tables["MSG"] = _frame(texts)
    if errs:
        tables["ERR"] = _frame(errs)


def _vibe_rows(t, d):
    # One combined vibration value per axis, but a clip counter per accelerometer.
    yield {"t": t, "IMU": 0, "VibeX": d["vibration_x"], "VibeY": d["vibration_y"], "VibeZ": d["vibration_z"],
           "Clip": d["clipping_0"]}
    for i in (1, 2):
        yield {"t": t, "IMU": i, "VibeX": math.nan, "VibeY": math.nan, "VibeZ": math.nan, "Clip": d[f"clipping_{i}"]}


def _bat_rows(t, d):
    mv, ca = d["voltage_battery"], d["current_battery"]
    yield {"t": t, "Inst": 0, "Volt": math.nan if mv in (0, 65535) else mv / 1000.0,
           "Curr": math.nan if ca < 0 else ca / 100.0}


def _powr_rows(t, d):
    yield {"t": t, "Vcc": d["Vcc"] / 1000.0 if d["Vcc"] else math.nan}


def _ekf_rows(t, d):
    # ArduPilot sends the same innovation test ratios it logs in XKF4.
    yield {"t": t, "C": 0, "SV": d["velocity_variance"], "SP": d["pos_horiz_variance"],
           "SH": d["pos_vert_variance"], "SM": d["compass_variance"]}


def _rcou_rows(t, d):
    yield {"t": t, **{f"C{i}": d[f"servo{i}_raw"] for i in range(1, 15) if f"servo{i}_raw" in d}}


def _series(msgs, field, scale=1.0):
    if not msgs:
        return None
    t = np.array([m[0] for m in msgs])
    v = np.array([m[1][field] for m in msgs], dtype=float) * scale
    return t, v


def _hold(series, t):
    """Value of a slower series at times t, holding the last sample (no look-ahead)."""
    st, sv = series
    idx = np.searchsorted(st, t, side="right") - 1
    out = sv[np.clip(idx, 0, len(sv) - 1)]
    return np.where(idx >= 0, out, np.nan)


def _ctun(by_type, tables, params):
    hud = by_type["VFR_HUD"]
    if not hud:
        return
    t = np.array([m[0] for m in hud])
    df = pd.DataFrame({"t": t, "ThO": [m[1]["throttle"] / 100.0 for m in hud]})
    rel = _series(by_type["GLOBAL_POSITION_INT"], "relative_alt", 1e-3)
    if rel is not None:
        df["Alt"] = np.interp(t, *rel)
    else:   # VFR_HUD.alt is above sea level; make it relative to the start
        alt = np.array([m[1]["alt"] for m in hud], dtype=float)
        df["Alt"] = alt - alt[0]
    nco = _series(by_type["NAV_CONTROLLER_OUTPUT"], "alt_error")
    if nco is not None:
        # alt_error is the position controller's target minus current altitude.
        df["DAlt"] = df["Alt"] + _hold(nco, t)
    if "MOT_THST_HOVER" in params:
        df["ThH"] = params["MOT_THST_HOVER"]
    tables["CTUN"] = df.dropna(subset=["Alt"]).reset_index(drop=True)


def _att(by_type, tables):
    att = by_type["ATTITUDE"]
    if not att:
        return
    t = np.array([m[0] for m in att])
    df = pd.DataFrame({"t": t,
                       "Roll": [math.degrees(m[1]["roll"]) for m in att],
                       "Pitch": [math.degrees(m[1]["pitch"]) for m in att]})
    nav = by_type["NAV_CONTROLLER_OUTPUT"]
    if nav:
        df["DesRoll"] = _hold(_series(nav, "nav_roll"), t)
        df["DesPitch"] = _hold(_series(nav, "nav_pitch"), t)
        df = df.dropna(subset=["DesRoll", "DesPitch"]).reset_index(drop=True)
    tables["ATT"] = df
