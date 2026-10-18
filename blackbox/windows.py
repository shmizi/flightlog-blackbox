"""Split a log into armed, airborne, hover and altitude-controlled windows.

Every check is evaluated only inside the right window, so bench time before
arming (USB power, 0 V battery, radio failsafe with no receiver) and the
takeoff/landing transients do not produce false findings.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

EV_ARMED = 10
EV_DISARMED = 11
EV_LAND_COMPLETE_MAYBE = 17
EV_LAND_COMPLETE = 18
EV_NOT_LANDED = 28


@dataclass
class FlightWindows:
    log_start: float
    log_end: float
    armed: list = field(default_factory=list)       # [(start, end)] in log seconds
    airborne: list = field(default_factory=list)
    trimmed: list = field(default_factory=list)     # airborne minus takeoff/landing transients
    hover: list = field(default_factory=list)
    alt_ctrl: list | None = None                    # None when the log has no MODE records
    touchdowns: list = field(default_factory=list)  # landings that ended on the ground
    disarmed_in_air: list = field(default_factory=list)
    ends_armed: bool = False                        # log stops while still armed (truncated)
    ends_airborne: bool = False
    arming_inferred: bool = False                   # no ARM/EV records; arming guessed from throttle
    airborne_inferred: bool = False                 # no NOT_LANDED events; airborne guessed from altitude

    @property
    def armed_s(self):
        return total(self.armed)

    @property
    def airborne_s(self):
        return total(self.airborne)

    @property
    def takeoff(self):
        if self.airborne:
            return self.airborne[0][0]
        return self.armed[0][0] if self.armed else self.log_start

    def flight_time(self, t):
        """Seconds since the first takeoff, as shown in reports."""
        return None if t is None else float(t) - self.takeoff


def total(intervals):
    return float(sum(e - s for s, e in intervals))


def mask(t, intervals):
    t = np.asarray(t, dtype=float)
    m = np.zeros(t.shape, dtype=bool)
    for s, e in intervals:
        m |= (t >= s) & (t <= e)
    return m


def intersect(a, b):
    out = []
    for s1, e1 in a:
        for s2, e2 in b:
            s, e = max(s1, s2), min(e1, e2)
            if e > s:
                out.append((s, e))
    return sorted(out)


def runs(t, flags, max_gap):
    """Contiguous runs of True samples as (first_t, last_t); a gap > max_gap splits a run."""
    out, start, prev = [], None, None
    for ti, f in zip(np.asarray(t, float), np.asarray(flags, bool)):
        if f:
            if start is not None and ti - prev > max_gap:
                out.append((start, prev))
                start = None
            if start is None:
                start = ti
            prev = ti
        elif start is not None:
            out.append((start, prev))
            start = None
    if start is not None:
        out.append((start, prev))
    return out


def rolling(t, values, window_s, how="median"):
    """Trailing time-window rolling statistic (t in seconds, assumed sorted)."""
    s = pd.Series(np.asarray(values, dtype=float), index=pd.to_timedelta(np.asarray(t, dtype=float), unit="s"))
    r = s.rolling(pd.Timedelta(seconds=window_s), min_periods=1)
    return getattr(r, how)().to_numpy()


def climb_rate(ctun, smooth_s=0.5):
    """Climb rate in m/s from CTUN.Alt.

    Derived from altitude rather than CTUN.CRt because CRt changed from
    int16 cm/s to float m/s between firmware versions.
    """
    t = ctun["t"].to_numpy(float)
    alt = ctun["Alt"].to_numpy(float)
    # Telemetry packets can share a receive timestamp; differentiate on unique times only.
    tu, idx = np.unique(t, return_index=True)
    if len(tu) < 3:
        return np.zeros_like(t)
    vz = np.interp(t, tu, np.gradient(alt[idx], tu))
    return rolling(t, vz, smooth_s)


def segment(parsed, cfg):
    w = cfg["windows"]
    tables = parsed.tables
    win = FlightWindows(log_start=parsed.log_start, log_end=parsed.log_end)
    events = _events(tables)

    # Armed intervals from EV ARMED/DISARMED and ARM.ArmState.
    armed_state, start = False, None
    for t, ev in events:
        if ev == EV_ARMED and not armed_state:
            armed_state, start = True, t
        elif ev == EV_DISARMED and armed_state:
            win.armed.append((start, t))
            armed_state = False
    if armed_state:
        win.armed.append((start, win.log_end))
        win.ends_armed = True

    if not any(ev in (EV_ARMED, EV_DISARMED) for _, ev in events):
        ctun = tables.get("CTUN")
        if ctun is not None and "ThO" in ctun:
            spinning = runs(ctun["t"], ctun["ThO"].to_numpy(float) > 0.05, max_gap=1.0)
            if total(spinning) > 5.0:
                win.armed = [(win.log_start, win.log_end)]
                win.arming_inferred = True

    # If the firmware logs land-detector events at all, an armed period without NOT_LANDED
    # simply never took off (e.g. arm, think better of it, disarm). Fall back to altitude
    # only for logs that carry no land-detector events anywhere.
    has_land_events = any(ev in (EV_NOT_LANDED, EV_LAND_COMPLETE) for _, ev in events)
    for a_start, a_end in win.armed:
        ended_by_disarm = not (win.ends_armed and a_end == win.log_end)
        # LAND_COMPLETE forced by a disarm is logged a few microseconds after it.
        inside = [(t, ev) for t, ev in events if a_start < t <= a_end + 0.05]
        if has_land_events:
            if any(ev == EV_NOT_LANDED for _, ev in inside):
                _airborne_from_events(win, inside, a_start, a_end, ended_by_disarm)
        else:
            _airborne_from_altitude(win, tables.get("CTUN"), a_start, a_end, ended_by_disarm, w)

    for s, e in win.airborne:
        s2, e2 = s + w["trim_takeoff_s"], e - w["trim_landing_s"]
        if e2 > s2:
            win.trimmed.append((s2, e2))

    win.alt_ctrl = _alt_controlled(tables.get("MODE"), win, w)
    win.hover = _hover(tables, win, w)
    return win


def _events(tables):
    events = []
    ev = tables.get("EV")
    if ev is not None and "Id" in ev:
        events += [(float(t), int(i)) for t, i in zip(ev["t"], ev["Id"])]
    arm = tables.get("ARM")
    if arm is not None and "ArmState" in arm:
        events += [(float(t), EV_ARMED if int(s) else EV_DISARMED) for t, s in zip(arm["t"], arm["ArmState"])]
    return sorted(events)


def _airborne_from_events(win, inside, a_start, a_end, ended_by_disarm):
    landed, takeoff, maybe = True, None, None
    for t, ev in inside:
        if ev == EV_DISARMED and not landed:
            break   # disarmed before the land detector said landed: a crash or a pilot cut
        if ev == EV_NOT_LANDED and landed:
            landed, takeoff, maybe = False, t, None
        elif ev == EV_LAND_COMPLETE_MAYBE and not landed:
            maybe = t
        elif ev == EV_LAND_COMPLETE and not landed:
            # LAND_COMPLETE fires about 1 s after touchdown; MAYBE is closer to the real moment.
            touchdown = maybe if maybe is not None and t - maybe <= 3.0 else max(takeoff, t - 1.0)
            win.airborne.append((takeoff, touchdown))
            win.touchdowns.append(touchdown)
            landed = True
    if not landed:
        win.airborne.append((takeoff, a_end))
        if ended_by_disarm:
            win.disarmed_in_air.append(a_end)
        else:
            win.ends_airborne = True


def _airborne_from_altitude(win, ctun, a_start, a_end, ended_by_disarm, w):
    if ctun is None or "Alt" not in ctun:
        # No way to tell when it flew: treat the whole armed time as airborne.
        win.airborne.append((a_start, a_end))
        win.airborne_inferred = True
        if not ended_by_disarm:
            win.ends_airborne = True
        return
    sel = ctun[(ctun["t"] >= a_start) & (ctun["t"] <= a_end)]
    if sel.empty:
        return
    t = sel["t"].to_numpy(float)
    alt = sel["Alt"].to_numpy(float)
    alt0 = np.median(alt[t <= t[0] + 1.0])
    win.airborne_inferred = True
    for s, e in runs(t, alt - alt0 > w["takeoff_alt_m"], max_gap=1.0):
        if e - s < 2.0:
            continue
        win.airborne.append((s, e))
        if e >= t[-1] - 0.5 and not ended_by_disarm:
            win.ends_airborne = True
        else:
            win.touchdowns.append(e)


def _alt_controlled(mode_df, win, w):
    if mode_df is None or mode_df.empty:
        return None
    col = "ModeNum" if "ModeNum" in mode_df else "Mode"
    times = mode_df["t"].to_numpy(float)
    modes = mode_df[col].astype(int).to_numpy()
    wanted = set(w["alt_controlled_modes"])
    spans = []
    for i, (t, m) in enumerate(zip(times, modes)):
        end = times[i + 1] if i + 1 < len(times) else win.log_end
        if m in wanted and end > t:
            spans.append((t, end))
    return intersect(win.trimmed, spans)


def _hover(tables, win, w):
    ctun = tables.get("CTUN")
    if ctun is None or "Alt" not in ctun or not win.trimmed:
        return []
    t = ctun["t"].to_numpy(float)
    flags = mask(t, win.trimmed) & (np.abs(climb_rate(ctun)) < w["hover_max_climb_ms"])
    att = tables.get("ATT")
    if att is not None and {"Roll", "Pitch"} <= set(att.columns):
        ta = att["t"].to_numpy(float)
        tilt = np.maximum(np.abs(att["Roll"].to_numpy(float)), np.abs(att["Pitch"].to_numpy(float)))
        flags &= np.interp(t, ta, tilt) < w["hover_max_tilt_deg"]
    # Telemetry arrives every ~0.5 s, so allow a few sample periods between hover samples.
    gap = max(0.5, 2.5 * float(np.median(np.diff(t)))) if len(t) > 1 else 0.5
    return [(s, e) for s, e in runs(t, flags, max_gap=gap) if e - s >= w["hover_min_s"]]
