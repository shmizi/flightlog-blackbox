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


def na(cid, name, why):
    return CheckResult(cid, name, Status.NOT_AVAILABLE, detail=why)


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
    counters = []
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
        cells, source = battery_cells(ctx, inst, g)
        if cells is None:
            continue
        armed = g[mask(g["t"], ctx.win.armed) & (g["Volt"] > 1.0)]
        if not armed.empty:
            at_arm = armed["Volt"].iloc[0] / cells
            if not 3.0 <= at_arm <= 4.4:
                notes.append(f"BAT {inst} reads {at_arm:.2f} V/cell at arming: is cells={cells} right?")
        g = g[mask(g["t"], ctx.win.airborne) & (g["Volt"] > 1.0)]
        if g.empty:
            continue
        v = rolling(g["t"], g["Volt"] / cells, ctx.cfg["windows"]["sustain_s"])
        i = int(np.nanargmin(v))
        if v[i] < best[0]:
            best = (v[i], g["t"].iloc[i], f"BAT {inst}, {cells}S from {source}, sustained min per cell")
    if best[1] is None:
        return na(cid, name, "BAT.Volt is 0: no battery monitor configured (BATT_MONITOR)")
    detail = "; ".join([best[2]] + notes)
    return _result(cid, name, "cell_voltage", ctx, best[0], best[1], "V", detail, below=True)


def battery_cells(ctx, inst, bat):
    armed = bat[mask(bat["t"], ctx.win.armed) & (bat["Volt"] > 1.0)]
    v_arm = float(armed["Volt"].iloc[0]) if not armed.empty else None
    return cells_for(ctx.cfg, inst, ctx.params, v_arm)


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
        cells, _ = battery_cells(ctx, inst, g)
        if cells is None or not g["Curr"].abs().max() >= min_a:
            continue
        air = g[mask(g["t"], ctx.win.airborne) & (g["Volt"] > 1.0)]
        if air.empty:
            continue
        if "VoltR" in air and (air["VoltR"] > 1.0).any():
            # VoltR is ArduPilot's resting-voltage estimate, so VoltR - Volt is the sag right now.
            sag = (air["VoltR"] - air["Volt"]) / cells
            how = "VoltR - Volt"
        else:
            # No VoltR (telemetry): compare the voltage just before each takeoff with the first
            # seconds of flight, before discharge lowers the resting voltage and inflates the sag.
            air, sag = _takeoff_sag(g, ctx.win.airborne, cells)
            if air is None:
                continue
            how = "resting before takeoff vs first 10 s airborne"
        v = rolling(air["t"], sag, ctx.cfg["windows"]["sustain_s"])
        if np.isnan(v).all():
            continue
        i = int(np.nanargmax(v))
        if v[i] > best[0]:
            best = (v[i], air["t"].iloc[i], inst, how)
    if best[1] is None:
        return na(cid, name, "BAT.Curr stays near 0: no current sensor, so sag cannot be measured")
    return _result(cid, name, "voltage_sag", ctx, best[0], best[1], "V/cell",
                   f"BAT {best[2]}, {best[3]}, sustained max at {_curr_at(df, best[1]):.0f} A")


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
        return na(cid, name, "This flight controller does not measure board voltage (POWR.Vcc is empty)")
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
    detail = "|DAlt - Alt| 95th percentile"
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
                   "max of roll/pitch error, 95th percentile")


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
        s = worse(grade(peak_g, th["accel_warning_g"], th["accel_critical_g"]),
                  grade(descent, th["descent_warning_ms"], th["descent_critical_ms"]))
        if t_worst is None or worse(s, status) == s and s != status:
            status, t_worst = s, td
            if peak_g is not None:
                value = peak_g
            else:   # no IMU (telemetry logs): report the descent rate against its own limits
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
    status, labels, t_worst = Status.WARNING, [], g["t"].iloc[0]
    for t, sub, code in zip(g["t"], g["Subsys"].astype(int), g["ECode"].astype(int)):
        labels.append(f"{ERR_SUBSYSTEMS.get(sub, sub)}/{code}")
        if any(sub == s and (c is None or code == c) for s, c in th["critical"]) and status != Status.CRITICAL:
            status, t_worst = Status.CRITICAL, t
    return CheckResult(cid, name, status, len(g), None, None, "errors",
                       _round(ctx.win.flight_time(t_worst)), ", ".join(dict.fromkeys(labels)))


# 13 --------------------------------------------------------------------------
def external_nav(ctx):
    cid, name = 13, "External navigation gaps"
    present = [m for m in ("VISP", "VISV", "VISO") if ctx.table(m) is not None]
    if not present:
        return missing(cid, name, "VISP")
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


ALL_CHECKS = [
    vibration, clipping, cell_voltage, voltage_sag, board_supply, hover_throttle, altitude_hold,
    attitude_tracking, estimator, motor_balance, hard_landing, errors, external_nav,
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
