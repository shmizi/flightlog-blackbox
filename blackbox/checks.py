"""The health checks. Each takes a Ctx and returns one CheckResult.

Multi-instance messages (VIBE.IMU, IMU.I, BAT.Inst, XKF4.C) are evaluated
per instance and the worst instance is reported, so interleaved rows from
different sensors are never mixed.
"""

from dataclasses import dataclass, asdict
from enum import Enum

import numpy as np
import pandas as pd

from .config import cells_for
from .windows import climb_rate, mask, rolling, runs, total

G = 9.80665

ERR_SUBSYSTEMS = {
    1: "MAIN", 2: "RADIO", 3: "COMPASS", 4: "OPTFLOW", 5: "FAILSAFE_RADIO", 6: "FAILSAFE_BATT",
    7: "FAILSAFE_GPS", 8: "FAILSAFE_GCS", 9: "FAILSAFE_FENCE", 10: "FLIGHT_MODE", 11: "GPS",
    12: "CRASH_CHECK", 13: "FLIP", 14: "AUTOTUNE", 15: "PARACHUTES", 16: "EKFCHECK",
    17: "FAILSAFE_EKFINAV", 18: "BARO", 19: "CPU", 20: "FAILSAFE_ADSB", 21: "TERRAIN",
    22: "NAVIGATION", 23: "FAILSAFE_TERRAIN", 24: "EKF_PRIMARY", 25: "THRUST_LOSS_CHECK",
    26: "FAILSAFE_SENSORS", 27: "FAILSAFE_LEAK", 28: "PILOT_INPUT", 29: "FAILSAFE_VIBE",
    30: "INTERNAL_ERROR", 31: "FAILSAFE_DEADRECKON",
}

# Plain-language names for the ERR subsystems people actually meet.
ERROR_TEXT = {
    5: "Radio failsafe", 6: "Battery failsafe", 7: "GPS failsafe", 8: "Ground-station failsafe",
    9: "Geofence breach", 11: "GPS glitch", 12: "Crash detected", 16: "EKF variance too high",
    17: "EKF failsafe", 18: "Barometer error", 24: "EKF lane switch", 25: "Potential thrust loss",
    26: "Sensor failsafe", 29: "Vibration failsafe", 30: "Internal error", 31: "Dead-reckoning failsafe",
}

# What to turn on when a check's messages are missing (E3).
LOGGING_HINTS = {
    "VIBE": "enable IMU in LOG_BITMASK",
    "IMU": "enable IMU in LOG_BITMASK",
    "BAT": "set BATT_MONITOR and enable CURRENT in LOG_BITMASK",
    "POWR": "board power (POWR) is not in this log",
    "CTUN": "enable CTUN in LOG_BITMASK",
    "ATT": "enable ATTITUDE_MED or ATTITUDE_FAST in LOG_BITMASK",
    "XKF4": "check AHRS_EKF_TYPE and EK3_LOG_LEVEL (0 logs everything)",
    "RCOU": "enable RCOUT in LOG_BITMASK",
    "ERR": "ERR is always logged; this log may be cut short",
    "VISP": "the Jetson is not sending vision data over MAVLink, or VISO_TYPE is 0",
}


class Status(str, Enum):
    PASS = "Pass"
    WARNING = "Warning"
    CRITICAL = "Critical"
    NOT_AVAILABLE = "Not available"


FINDING = (Status.CRITICAL, Status.WARNING)     # the statuses that count as a finding


def is_finding(status):
    """True for Warning and Critical; takes a Status or its text (as stored in result.json)."""
    return Status(status) in FINDING


def limit_for(status, warning, critical):
    """The limit a value is reported against: the critical one for a Critical result, else the warning one."""
    return critical if status == Status.CRITICAL else warning


@dataclass
class CheckResult:
    id: int
    name: str
    status: Status
    value: float | None = None
    warning: float | None = None
    critical: float | None = None
    unit: str = ""
    t_flight_s: float | None = None   # time since takeoff of the worst sample
    detail: str = ""
    applies: bool = True               # False: not relevant to this drone (left out of the email)
    fixes: dict | None = None          # suggested parameter values {name: (value, reason)}

    @property
    def limit(self):
        return limit_for(self.status, self.warning, self.critical)

    def to_dict(self):
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class Ctx:
    tables: dict
    params: dict
    win: object
    cfg: dict

    def table(self, name, *cols):
        df = self.tables.get(name)
        if df is None or df.empty or not set(cols) <= set(df.columns):
            return None
        return df


def grade(value, warning, critical, below=False):
    if value is None or not np.isfinite(value):
        return Status.NOT_AVAILABLE
    if below:
        return Status.CRITICAL if value < critical else Status.WARNING if value < warning else Status.PASS
    return Status.CRITICAL if value > critical else Status.WARNING if value > warning else Status.PASS


def worse(a, b):
    order = [Status.NOT_AVAILABLE, Status.PASS, Status.WARNING, Status.CRITICAL]
    return a if order.index(a) >= order.index(b) else b


def na(cid, name, why, applies=True):
    return CheckResult(cid, name, Status.NOT_AVAILABLE, detail=why, applies=applies)


def missing(cid, name, msg):
    return na(cid, name, f"No {msg} messages; {LOGGING_HINTS.get(msg, 'enable its logging')}")


def instances(df, col):
    if col in df:
        for inst, g in df.groupby(col, sort=True):
            yield int(inst), g
    else:
        yield 0, df


def _result(cid, name, cfg_key, ctx, value, t_worst, unit, detail, below=False):
    th = ctx.cfg["checks"][cfg_key]
    status = grade(value, th["warning"], th["critical"], below)
    return CheckResult(cid, name, status, _round(value), th["warning"], th["critical"], unit,
                       _round(ctx.win.flight_time(t_worst)), detail)


def _round(x, nd=3):
    return None if x is None else round(float(x), nd)


# 1 ---------------------------------------------------------------------------
def vibration(ctx):
    cid, name = 1, "Vibration"
    df = ctx.table("VIBE", "VibeX", "VibeY", "VibeZ")
    if df is None:
        return missing(cid, name, "VIBE")
    if not ctx.win.trimmed:
        return na(cid, name, "No airborne data outside takeoff/landing")
    best = (-np.inf, None, None, None)
    for inst, g in instances(df, "IMU"):
        g = g[mask(g["t"], ctx.win.trimmed)]
        if g.empty:
            continue
        for axis in ("VibeX", "VibeY", "VibeZ"):
            v = rolling(g["t"], g[axis], ctx.cfg["windows"]["sustain_s"])
            if np.isnan(v).all():     # telemetry gives one vibration value, on instance 0 only
                continue
            i = int(np.nanargmax(v))
            if v[i] > best[0]:
                best = (v[i], g["t"].iloc[i], inst, axis)
    if best[1] is None:
        return na(cid, name, "No VIBE samples while airborne")
    value, t, inst, axis = best
    return _result(cid, name, "vibration", ctx, value, t, "m/s²", f"{axis} on IMU {inst}, sustained max")


# 2 ---------------------------------------------------------------------------
def clipping(ctx):
    cid, name = 2, "Accelerometer clipping"
    df = ctx.table("VIBE")
    if df is None:
        return missing(cid, name, "VIBE")
    if not ctx.win.trimmed:
        return na(cid, name, "No airborne data outside takeoff/landing")
    # Current firmware: one Clip counter per IMU instance. Old firmware: Clip0..Clip2 in one row.
    if "Clip" in df:
        counters = [(f"IMU {inst}", g["t"], g["Clip"]) for inst, g in instances(df, "IMU")]
    else:
        counters = [(c, df["t"], df[c]) for c in ("Clip0", "Clip1", "Clip2") if c in df]
    if not counters:
        return na(cid, name, "VIBE has no Clip field")
    worst, label, t_worst = -1, None, None
    for lab, t, clip in counters:
        t, clip = t.to_numpy(float), clip.to_numpy(float)
        rise, t_rise = 0.0, None
        for s, e in ctx.win.trimmed:   # counters are cumulative: count the rise inside each window
            sel = (t >= s) & (t <= e)
            if sel.sum() >= 2:
                c = clip[sel]
                if c[-1] - c[0] > 0:
                    rise += c[-1] - c[0]
                    t_rise = t_rise or t[sel][int(np.argmax(np.diff(c) > 0))]
        if rise > worst:
            worst, label, t_worst = rise, lab, t_rise
    return _result(cid, name, "clipping", ctx, worst, t_worst, "events",
                   f"{label}, rise while airborne (landing impact excluded)")


# 3 ---------------------------------------------------------------------------
def cell_voltage(ctx):
    cid, name = 3, "Cell voltage under load"
    df = ctx.table("BAT", "Volt")
    if df is None:
        return missing(cid, name, "BAT")
    if not ctx.win.airborne:
        return na(cid, name, "No airborne data")
    best = (np.inf, None, None)
    notes = []
    for inst, g in instances(df, "Inst"):
        if not (g["Volt"] > 1.0).any():
            continue
        v_arm = volt_at_arm(ctx, g)
        cells, source = cells_for(ctx.cfg, inst, ctx.params, v_arm)
        if cells is None:
            continue
        if v_arm and not 3.0 <= v_arm / cells <= 4.4:
            notes.append(f"BAT {inst} reads {v_arm / cells:.2f} V/cell at arming: is cells={cells} right?")
        g = g[mask(g["t"], ctx.win.airborne) & (g["Volt"] > 1.0)]
        if g.empty:
            continue
        v = rolling(g["t"], g["Volt"] / cells, ctx.cfg["windows"]["sustain_s"])
        i = int(np.nanargmin(v))
        if v[i] < best[0]:
            best = (v[i], g["t"].iloc[i], f"{cells}S pack (cell count from {source}), lowest sustained voltage")
    if best[1] is None:
        return na(cid, name, "BAT.Volt is 0: no battery monitor configured (BATT_MONITOR)")
    detail = "; ".join([best[2]] + notes)
    return _result(cid, name, "cell_voltage", ctx, best[0], best[1], "V", detail, below=True)


def volt_at_arm(ctx, bat):
    """Pack voltage at the first armed sample (resting, before the motors load it), or None."""
    armed = bat[mask(bat["t"], ctx.win.armed) & (bat["Volt"] > 1.0)]
    return float(armed["Volt"].iloc[0]) if not armed.empty else None


# 4 ---------------------------------------------------------------------------
def voltage_sag(ctx):
    cid, name = 4, "Voltage sag"
    df = ctx.table("BAT", "Volt", "Curr")
    if df is None:
        return missing(cid, name, "BAT")
    if not ctx.win.airborne:
        return na(cid, name, "No airborne data")
    min_a = ctx.cfg["battery"]["current_min_a"]
    best = (-np.inf, None, None, "")
    for inst, g in instances(df, "Inst"):
        cells, _ = cells_for(ctx.cfg, inst, ctx.params, volt_at_arm(ctx, g))
        if cells is None or not g["Curr"].abs().max() >= min_a:
            continue
        air = g[mask(g["t"], ctx.win.airborne) & (g["Volt"] > 1.0)]
        if air.empty:
            continue
        if "VoltR" in air and (air["VoltR"] > 1.0).any():
            # VoltR is ArduPilot's resting-voltage estimate, so VoltR - Volt is the sag right now.
            sag = (air["VoltR"] - air["Volt"]) / cells
            how = "resting-voltage estimate minus measured voltage"
        else:
            # No VoltR (telemetry): compare the voltage just before each takeoff with the first
            # seconds of flight, before discharge lowers the resting voltage and inflates the sag.
            air, sag = _takeoff_sag(g, ctx.win.airborne, cells)
            if air is None:
                continue
            how = "voltage before takeoff vs the first 10 s airborne"
        v = rolling(air["t"], sag, ctx.cfg["windows"]["sustain_s"])
        if np.isnan(v).all():
            continue
        i = int(np.nanargmax(v))
        if v[i] > best[0]:
            best = (v[i], air["t"].iloc[i], inst, how)
    if best[1] is None:
        return na(cid, name, "BAT.Curr stays near 0: no current sensor, so sag cannot be measured")
    return _result(cid, name, "voltage_sag", ctx, best[0], best[1], "V/cell",
                   f"{best[3]}, worst at {_curr_at(df, best[1]):.0f} A")


def _takeoff_sag(bat, airborne, cells, window_s=10.0):
    t = bat["t"].to_numpy(float)
    volt = bat["Volt"].to_numpy(float)
    sag = pd.Series(dtype=float)
    for s, _ in airborne:
        rest = (t >= s - window_s) & (t < s) & (volt > 1.0)
        early = (t >= s) & (t <= s + window_s) & (volt > 1.0)
        if rest.any() and early.any():
            part = pd.Series((np.median(volt[rest]) - volt[early]) / cells, index=bat.index[early])
            # Takeoffs less than a window apart overlap: keep one value per sample, in time order.
            sag = part if sag.empty else pd.concat([sag, part]).groupby(level=0).max()
    if sag.empty:
        return None, None
    air = bat.loc[sag.index].sort_values("t", kind="stable")
    return air, sag.loc[air.index].to_numpy(float)


def _curr_at(df, t):
    return float(np.interp(t, df["t"].to_numpy(float), df["Curr"].to_numpy(float)))


# 5 ---------------------------------------------------------------------------
def board_supply(ctx):
    cid, name = 5, "Board supply"
    df = ctx.table("POWR", "Vcc")
    if df is None:
        return missing(cid, name, "POWR")
    g = df[mask(df["t"], ctx.win.armed)]
    if g.empty:
        return na(cid, name, "No POWR samples while armed")
    g = g[np.isfinite(g["Vcc"].to_numpy(float))]
    if g.empty:
        return na(cid, name, "This flight controller does not measure board voltage (POWR.Vcc is empty)",
                  applies=False)
    v = rolling(g["t"], g["Vcc"], ctx.cfg["windows"]["sustain_s"])
    i = int(np.argmin(v))
    return _result(cid, name, "board_supply", ctx, v[i], g["t"].iloc[i], "V", "POWR.Vcc sustained min while armed",
                   below=True)


# 6 ---------------------------------------------------------------------------
def hover_throttle(ctx):
    cid, name = 6, "Hover throttle"
    df = ctx.table("CTUN", "ThO")
    if df is None:
        return missing(cid, name, "CTUN")
    if not ctx.win.hover:
        return na(cid, name, "No steady hover found in the flight")
    g = df[mask(df["t"], ctx.win.hover)]
    value = float(np.median(g["ThO"]))
    detail = f"median CTUN.ThO over {total(ctx.win.hover):.0f} s of hover"
    if "ThH" in df:
        detail += f"; learned ThH {df['ThH'].iloc[-1]:.2f} (capped at 0.6875 by ArduPilot)"
    return _result(cid, name, "hover_throttle", ctx, value, g["t"].iloc[len(g) // 2], "", detail)


# 7 ---------------------------------------------------------------------------
def altitude_hold(ctx):
    cid, name = 7, "Altitude hold"
    df = ctx.table("CTUN", "DAlt", "Alt")
    if df is None:
        return missing(cid, name, "CTUN")
    spans = ctx.win.alt_ctrl if ctx.win.alt_ctrl is not None else ctx.win.trimmed
    g = df[mask(df["t"], spans)]
    if g.empty:
        return na(cid, name, "No flight in an altitude-controlled mode (DAlt is not a target in STABILIZE/ACRO)")
    err = (g["DAlt"] - g["Alt"]).abs().to_numpy(float)
    value = float(np.percentile(err, 95))
    detail = "gap between target and actual altitude (95th percentile)"
    if ctx.win.alt_ctrl is None:
        detail += "; no MODE records, assumed altitude-controlled throughout"
    return _result(cid, name, "altitude_hold", ctx, value, g["t"].iloc[int(np.argmax(err))], "m", detail)


# 8 ---------------------------------------------------------------------------
def attitude_tracking(ctx):
    cid, name = 8, "Attitude tracking"
    df = ctx.table("ATT", "DesRoll", "Roll", "DesPitch", "Pitch")
    if df is None:
        return missing(cid, name, "ATT")
    g = df[mask(df["t"], ctx.win.trimmed)]
    if g.empty:
        return na(cid, name, "No airborne data outside takeoff/landing")
    err = np.maximum((g["DesRoll"] - g["Roll"]).abs(), (g["DesPitch"] - g["Pitch"]).abs()).to_numpy(float)
    value = float(np.percentile(err, 95))
    return _result(cid, name, "attitude_tracking", ctx, value, g["t"].iloc[int(np.argmax(err))], "°",
                   "gap between commanded and actual roll/pitch (95th percentile)")


# 9 ---------------------------------------------------------------------------
def estimator(ctx):
    cid, name = 9, "Estimator health"
    th = ctx.cfg["checks"]["estimator"]
    df = ctx.table("XKF4") if ctx.table("XKF4") is not None else ctx.table("NKF4")
    if df is None:
        return missing(cid, name, "XKF4")
    if not ctx.win.airborne:
        return na(cid, name, "No airborne data")
    fields = [f for f in th["fields"] if f in df]
    peak = (0.0, None, "")                 # highest ratio seen, for the report when nothing is held
    held_worst = (Status.PASS, 0.0, None, "", 0.0)
    for core, g in instances(df, "C"):
        g = g[mask(g["t"], ctx.win.airborne)]
        t = g["t"].to_numpy(float)
        for f in fields:
            v = g[f].to_numpy(float)
            if not len(v):
                continue
            i = int(np.argmax(v))
            if v[i] > peak[0]:
                peak = (float(v[i]), t[i], f"peak {f} on core {core}; nothing held above {th['warning']} "
                                          f"for {th['hold_s']} s")
            for level, limit in ((Status.CRITICAL, th["critical"]), (Status.WARNING, th["warning"])):
                held = [r for r in runs(t, v > limit, max_gap=1.0) if r[1] - r[0] >= th["hold_s"]]
                if not held:
                    continue
                s, e = max(held, key=lambda r: r[1] - r[0])
                if worse(level, held_worst[0]) == level and (level != held_worst[0] or e - s > held_worst[4]):
                    sel = (t >= s) & (t <= e)
                    held_worst = (level, float(v[sel].max()), s,
                                  f"{f} on core {core} above {limit} for {e - s:.1f} s", e - s)
                break
    status, value, t_worst, detail, _ = held_worst
    if status == Status.PASS:
        value, t_worst, detail = peak
    return CheckResult(cid, name, status, _round(value), th["warning"], th["critical"], "",
                       _round(ctx.win.flight_time(t_worst)), detail)


# 10 --------------------------------------------------------------------------
def motor_balance(ctx):
    cid, name = 10, "Motor balance"
    df = ctx.table("RCOU")
    if df is None:
        return missing(cid, name, "RCOU")
    channels = motor_channels(ctx)
    cols = [f"C{c}" for c in channels if f"C{c}" in df]
    if len(cols) < 2:
        return na(cid, name, "Motor outputs not found in RCOU (check SERVOn_FUNCTION)")
    spans, where = (ctx.win.hover, "hover") if ctx.win.hover else (ctx.win.trimmed, "flight (no steady hover)")
    # Worst spread over fixed chunks, not one mean over the whole flight, so a motor that
    # weakens halfway through is not averaged away by the healthy first half.
    chunk = ctx.cfg["checks"]["motor_balance"]["window_s"]
    t = df["t"].to_numpy(float)
    best = (-1.0, None, None)
    for s, e in spans:
        for cs in np.arange(s, max(e - chunk / 2, s + 1e-9), chunk):
            sel = (t >= cs) & (t < min(cs + chunk, e))
            if sel.sum() < 5:
                continue
            means = df.loc[sel, cols].mean()
            spread = float(means.max() - means.min())
            if spread > best[0]:
                best = (spread, float(cs), means)
    if best[1] is None:
        return na(cid, name, "No airborne data outside takeoff/landing")
    value, t_worst, means = best
    detail = (f"worst {chunk:g} s of {where}: " + ", ".join(f"{c} {means[c]:.0f}" for c in cols)
              + f"; highest {means.idxmax()}")
    return _result(cid, name, "motor_balance", ctx, value, t_worst, "µs", detail)


def motor_channels(ctx):
    """Output channels driving motors, from SERVOn_FUNCTION = 33..40 (Motor1..Motor8)."""
    found = {}
    for n in range(1, 33):
        fn = ctx.params.get(f"SERVO{n}_FUNCTION")
        if fn is not None and 33 <= int(fn) <= 40:
            found[int(fn)] = n
    if found:
        return [found[k] for k in sorted(found)]
    return ctx.cfg["checks"]["motor_balance"]["fallback_channels"]


# 11 --------------------------------------------------------------------------
def hard_landing(ctx):
    cid, name = 11, "Hard landing"
    th = ctx.cfg["checks"]["hard_landing"]
    landings = ([(t, "landing") for t in ctx.win.touchdowns]
                + [(t, "disarmed before landing was detected") for t in ctx.win.disarmed_in_air])
    if not landings:
        why = "Log ends while airborne" if ctx.win.ends_airborne else "No landing in this log"
        return na(cid, name, why)
    imu = ctx.table("IMU", "AccX", "AccY", "AccZ")
    ctun = ctx.table("CTUN", "Alt")
    if imu is None and ctun is None:
        return missing(cid, name, "IMU")
    status, value, t_worst, detail = Status.PASS, 0.0, None, ""
    unit, warn, crit = "g", th["accel_warning_g"], th["accel_critical_g"]
    vz_t, vz = (ctun["t"].to_numpy(float), climb_rate(ctun)) if ctun is not None else (None, None)
    for td, kind in landings:
        peak_g = None
        if imu is not None:
            for inst, g in instances(imu, "I"):
                g = g[(g["t"] >= td - 2.0) & (g["t"] <= td + 1.0)]
                if len(g):
                    a = np.sqrt(g["AccX"] ** 2 + g["AccY"] ** 2 + g["AccZ"] ** 2).max() / G
                    peak_g = a if peak_g is None else max(peak_g, a)
        descent = None
        if vz is not None:
            sel = (vz_t >= td - 1.5) & (vz_t <= td)
            if sel.any():
                descent = max(0.0, float(-vz[sel].min()))
        g_status = grade(peak_g, th["accel_warning_g"], th["accel_critical_g"])
        d_status = grade(descent, th["descent_warning_ms"], th["descent_critical_ms"])
        s = worse(g_status, d_status)
        if t_worst is None or worse(s, status) == s and s != status:
            status, t_worst = s, td
            # Report the measurement that set the status, against its own limits, so the value and
            # limit shown always explain the verdict (e.g. 3.5 m/s > 2 m/s, not 4.1 g vs 5 g).
            if peak_g is not None and (descent is None or worse(g_status, d_status) == g_status):
                value, unit, warn, crit = peak_g, "g", th["accel_warning_g"], th["accel_critical_g"]
            else:
                value, unit, warn, crit = descent, "m/s", th["descent_warning_ms"], th["descent_critical_ms"]
            parts = [kind]
            if peak_g is not None:
                parts.append(f"peak {peak_g:.1f} g")
            if descent is not None:
                parts.append(f"descent {descent:.1f} m/s before touchdown")
            detail = ", ".join(parts)
    return CheckResult(cid, name, status, _round(value), warn, crit, unit,
                       _round(ctx.win.flight_time(t_worst)), detail)


# 12 --------------------------------------------------------------------------
def errors(ctx):
    cid, name = 12, "Errors and failsafes"
    th = ctx.cfg["checks"]["errors"]
    df = ctx.table("ERR", "Subsys", "ECode")
    if df is None:
        return CheckResult(cid, name, Status.PASS, 0, detail="No errors logged")
    # Errors while armed only: boot-time errors (no RC receiver on the bench) and glitches after
    # disarm are ignored. CRASH_CHECK logs its ERR just before it disarms, so it stays inside.
    spans = [(s, e + 0.05) for s, e in ctx.win.armed]
    g = df[mask(df["t"], spans) & (df["ECode"] != 0) & ~df["Subsys"].isin(th["ignore_subsystems"])]
    if g.empty:
        return CheckResult(cid, name, Status.PASS, 0, detail="No errors while armed")
    status, counts, t_worst = Status.WARNING, {}, g["t"].iloc[0]
    for t, sub, code in zip(g["t"], g["Subsys"].astype(int), g["ECode"].astype(int)):
        key = (ERROR_TEXT.get(sub, ERR_SUBSYSTEMS.get(sub, f"subsystem {sub}")), ERR_SUBSYSTEMS.get(sub, sub), code)
        counts[key] = counts.get(key, 0) + 1
        if any(sub == s and (c is None or code == c) for s, c in th["critical"]) and status != Status.CRITICAL:
            status, t_worst = Status.CRITICAL, t
    labels = [f"{text}{f' ×{n}' if n > 1 else ''} ({raw}/{code})" for (text, raw, code), n in counts.items()]
    return CheckResult(cid, name, status, len(g), None, None, "error" if len(g) == 1 else "errors",
                       _round(ctx.win.flight_time(t_worst)), ", ".join(labels))


# 13 --------------------------------------------------------------------------
def external_nav(ctx):
    cid, name = 13, "External navigation gaps"
    present = [m for m in ("VISP", "VISV", "VISO") if ctx.table(m) is not None]
    if not present:
        viso = ctx.params.get("VISO_TYPE")
        if viso:
            return na(cid, name, f"Vision odometry is configured (VISO_TYPE = {viso:g}) but no vision data was "
                                 "logged: check the companion computer's MAVLink link, or set VISO_TYPE = 0")
        # No vision system on this drone (or no parameters to tell): nothing to report.
        return na(cid, name, "No vision odometry on this drone", applies=False)
    if not ctx.win.trimmed:
        return na(cid, name, "No airborne data outside takeoff/landing")
    worst, t_worst, src = 0.0, None, present[0]
    for m in present:
        for s, e in ctx.win.trimmed:
            t = ctx.tables[m]["t"].to_numpy(float)
            t = np.concatenate(([s], t[(t >= s) & (t <= e)], [e]))
            gaps = np.diff(t)
            i = int(np.argmax(gaps))
            if gaps[i] > worst:
                worst, t_worst, src = float(gaps[i]), float(t[i]), m
    return _result(cid, name, "external_nav", ctx, worst, t_worst, "s", f"longest gap in {src} while airborne")


# 14 --------------------------------------------------------------------------
def configuration(ctx):
    """Battery parameters checked against the pack that was actually fitted.

    Catches settings left over from another battery: thrust compensation set for 6S on a 3S
    pack, or a low-battery failsafe so low it can never fire before the pack collapses.
    """
    cid, name = 14, "Configuration"
    p = ctx.params
    if not p:
        return na(cid, name, "No parameters in this log (telemetry logs only have them if the "
                             "ground station downloaded them)")
    bat = ctx.table("BAT", "Volt")
    v_arm = volt_at_arm(ctx, bat) if bat is not None else None
    if v_arm is None:
        return na(cid, name, "No battery voltage at arming to compare the parameters with")
    th = ctx.cfg["checks"]["configuration"]
    # Pack size from thresholds.yaml or the voltage, never from the parameters being checked.
    cells, _ = cells_for(ctx.cfg, 0, None, v_arm)
    problems, fixes = battery_param_problems(p, cells, th["min_failsafe_cell_v"], v_arm)
    if not problems:
        return CheckResult(cid, name, Status.PASS, 0, None, None, "issues",
                           detail=f"Battery parameters match the {cells}S pack")
    return CheckResult(cid, name, Status.WARNING, len(problems), None, None,
                       "issue" if len(problems) == 1 else "issues", detail="; ".join(problems), fixes=fixes)


def battery_param_problems(p, cells, min_cell_v, v_arm=None):
    """Battery parameter problems for a pack of `cells`, plus suggested values.

    Shared by check 14 (after a flight) and the pre-flight .param check (before one).
    """
    problems, fixes = [], {}
    vmax = p.get("MOT_BAT_VOLT_MAX", 0.0)
    if vmax > 0 and round(vmax / 4.2) != cells:
        seen = f" ({v_arm:.1f} V at arming)" if v_arm else ""
        problems.append(f"MOT_BAT_VOLT_MAX = {vmax:g} V is set for a {round(vmax / 4.2)}S pack but a {cells}S "
                        f"pack was fitted{seen}, so thrust compensation is wrong")
        fixes["MOT_BAT_VOLT_MAX"] = (round(4.2 * cells, 1), f"4.2 V x {cells} cells")
        fixes["MOT_BAT_VOLT_MIN"] = (round(3.3 * cells, 1), f"3.3 V x {cells} cells")
    low = p.get("BATT_LOW_VOLT")
    if low is not None:
        if low <= 0:
            problems.append("BATT_LOW_VOLT = 0: the low-voltage battery failsafe is off")
        elif low / cells < min_cell_v:
            problems.append(f"BATT_LOW_VOLT = {low:g} V is only {low / cells:.2f} V per cell on a {cells}S pack, "
                            "so the low-battery failsafe fires far too late (or never)")
        if low <= 0 or low / cells < min_cell_v:
            fixes["BATT_LOW_VOLT"] = (round(3.5 * cells, 1), f"3.5 V x {cells} cells")
            fixes["BATT_CRT_VOLT"] = (round(3.3 * cells, 1), f"3.3 V x {cells} cells")
    if p.get("BATT_FS_LOW_ACT") == 0:
        problems.append("BATT_FS_LOW_ACT = 0: nothing happens when the battery runs low")
        fixes["BATT_FS_LOW_ACT"] = (1, "1 = Land (2 = RTL if GPS is available)")
    return problems, fixes


# 15 --------------------------------------------------------------------------
def vibration_spectrum(ctx):
    """Dominant vibration frequency from the IMU batch sampler, compared with the harmonic notch."""
    cid, name = 15, "Vibration spectrum"
    th = ctx.cfg["checks"]["vibration_spectrum"]
    head = ctx.table("ISBH", "N", "type", "instance", "mul", "smp_cnt", "smp_rate")
    data = ctx.table("ISBD", "N", "seqno", "x", "y", "z")
    if head is None or data is None:
        if ctx.params.get("INS_LOG_BAT_MASK") == 0:
            return na(cid, name, "IMU batch sampling is off: set INS_LOG_BAT_MASK = 1 to get a vibration spectrum")
        return na(cid, name, "No IMU batch samples in this log", applies=False)
    head = head[mask(head["t"], ctx.win.trimmed)]
    gyro = head[(head["type"] == 1) & (head["instance"] == 0)]
    batches = gyro if not gyro.empty else head[(head["type"] == 0) & (head["instance"] == 0)]
    if batches.empty:
        return na(cid, name, "No IMU batch samples while airborne")
    rows = {n: g.sort_values("seqno") for n, g in data[data["N"].isin(batches["N"])].groupby("N")}
    spectra, rate, n_fft = [], None, None
    for _, b in batches.iterrows():
        g = rows.get(b["N"])
        n = int(b["smp_cnt"])
        if g is None or n < 256:
            continue
        axes = [np.concatenate([np.asarray(a, float) for a in g[ax]])[:n] / float(b["mul"]) for ax in ("x", "y", "z")]
        if len(axes[0]) < n:
            continue
        window = np.hanning(n)
        spectra.append(sum(np.abs(np.fft.rfft((a - a.mean()) * window)) for a in axes))
        rate, n_fft = float(b["smp_rate"]), n
    if not spectra:
        return na(cid, name, "IMU batch samples were incomplete")
    spec = np.mean([s for s in spectra if len(s) == n_fft // 2 + 1], axis=0)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / rate)
    band = (freqs >= th["min_hz"]) & (freqs <= min(th["max_hz"], 0.45 * rate))
    peak_f = float(freqs[band][int(np.argmax(spec[band]))])
    prominence = float(spec[band].max() / np.median(spec[band]))
    kind = "gyro" if not gyro.empty else "accelerometer"
    base = f"strongest {kind} vibration at {peak_f:.0f} Hz ({prominence:.0f}x the noise floor)"
    if prominence < th["prominence"]:
        return CheckResult(cid, name, Status.PASS, _round(peak_f, 1), None, None, "Hz",
                           detail=f"No dominant vibration frequency; {base}")

    p = ctx.params
    hover = p.get("MOT_THST_HOVER") or 0.0
    suggested = {"INS_HNTCH_ENABLE": (1, "harmonic notch on"),
                 "INS_HNTCH_MODE": (1, "1 = track throttle"),
                 "INS_HNTCH_FREQ": (round(peak_f), "measured hover vibration frequency"),
                 "INS_HNTCH_BW": (round(peak_f / 2), "half the notch frequency")}
    if hover > 0:
        suggested["INS_HNTCH_REF"] = (round(hover, 3), "hover throttle (MOT_THST_HOVER)")
    if not p.get("INS_HNTCH_ENABLE"):
        return CheckResult(cid, name, Status.WARNING, _round(peak_f, 1), None, None, "Hz",
                           detail=f"{base}; the harmonic notch filter is off (INS_HNTCH_ENABLE = 0)", fixes=suggested)
    freq, bw = p.get("INS_HNTCH_FREQ", 0.0), p.get("INS_HNTCH_BW", 0.0)
    harmonics = [h + 1 for h in range(8) if int(p.get("INS_HNTCH_HMNCS", 1)) & (1 << h)] or [1]
    centres = [freq * h for h in harmonics]
    tracking = p.get("INS_HNTCH_MODE", 0) != 0 and p.get("INS_HNTCH_REF", 0) > 0
    covered = any(abs(peak_f - c) <= max(bw / 2, 5) for c in centres)
    if covered or tracking:
        how = "tracks throttle" if tracking else f"is fixed at {', '.join(f'{c:g}' for c in centres)} Hz"
        note = "" if covered else " (check post-filter logs)"
        if not tracking and p.get("INS_HNTCH_MODE", 0) != 0:
            note += "; throttle tracking is off (INS_HNTCH_REF = 0), so the notch will miss the peak as throttle changes"
        return CheckResult(cid, name, Status.PASS, _round(peak_f, 1), None, None, "Hz",
                           detail=f"{base}; the harmonic notch {how}{note}")
    why = " (INS_HNTCH_REF = 0 turns throttle tracking off)" if p.get("INS_HNTCH_MODE", 0) != 0 else ""
    return CheckResult(cid, name, Status.WARNING, _round(peak_f, 1), None, None, "Hz",
                       detail=f"{base}; the harmonic notch is fixed at {', '.join(f'{c:g}' for c in centres)} Hz{why} "
                              "and misses it", fixes=suggested)


# 16 --------------------------------------------------------------------------
def compass_interference(ctx):
    """How much the measured magnetic field changes with battery current (motor and wiring interference).

    The Earth's field has the same strength whatever the attitude, so a field strength that rises or
    falls with current is interference from the power wiring. ArduPilot's compass/motor calibration
    treats under 30% of the Earth's field as fine and over 60% as unusable.
    """
    cid, name = 16, "Compass interference"
    th = ctx.cfg["checks"]["compass_interference"]
    if ctx.params.get("COMPASS_ENABLE") == 0 or ctx.params.get("COMPASS_USE") == 0:
        return na(cid, name, "The compass is not used on this drone", applies=False)
    mag = ctx.table("MAG", "MagX", "MagY", "MagZ")
    bat = ctx.table("BAT", "Curr")
    if mag is None:
        return na(cid, name, "No compass data in this log", applies=False)
    if bat is None or bat["Curr"].abs().max() < ctx.cfg["battery"]["current_min_a"]:
        return na(cid, name, "No current sensor, so interference cannot be measured")
    best = None
    for inst, g in instances(mag, "I"):
        use_key = "COMPASS_USE" if inst == 0 else f"COMPASS_USE{inst + 1}"
        if ctx.params.get(use_key, 1) == 0:
            continue
        g = g[mask(g["t"], ctx.win.armed)]
        if len(g) < 50:
            continue
        field = np.sqrt(g["MagX"] ** 2 + g["MagY"] ** 2 + g["MagZ"] ** 2).to_numpy(float)
        curr = np.interp(g["t"].to_numpy(float), bat["t"].to_numpy(float), bat["Curr"].to_numpy(float))
        if curr.max() - curr.min() < th["min_current_span_a"]:
            continue
        slope, earth = np.polyfit(curr, field, 1)
        pct = abs(slope) * curr.max() / max(earth, 1.0) * 100
        if best is None or pct > best[0]:
            best = (pct, inst, slope, curr.max(), earth)
    if best is None:
        return na(cid, name, "Not enough change in current while armed to measure interference")
    pct, inst, slope, imax, earth = best
    detail = (f"compass {inst + 1}: field strength changes {slope:+.1f} mGauss per amp; at {imax:.0f} A that is "
              f"{pct:.0f}% of the Earth's field ({earth:.0f} mGauss)")
    return CheckResult(cid, name, grade(pct, th["warning"], th["critical"]), _round(pct, 1), th["warning"],
                       th["critical"], "%", detail=detail)


ALL_CHECKS = [
    vibration, clipping, cell_voltage, voltage_sag, board_supply, hover_throttle, altitude_hold,
    attitude_tracking, estimator, motor_balance, hard_landing, errors, external_nav, configuration,
    vibration_spectrum, compass_interference,
]


def run_all(ctx):
    results = []
    for check in ALL_CHECKS:
        try:
            results.append(check(ctx))
        except Exception as exc:  # one broken check must not sink the whole report
            cid = ALL_CHECKS.index(check) + 1
            results.append(na(cid, check.__name__.replace("_", " ").capitalize(),
                              f"Check failed: {type(exc).__name__}: {exc}"))
    return results
