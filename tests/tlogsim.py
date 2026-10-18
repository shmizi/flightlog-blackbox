"""Write synthetic ground-station telemetry logs (.tlog / .rlog) for tests.

Same flight as dflog.flight (arm, climb to 1.5 m, hover, land, disarm) but
streamed as MAVLink at 3 Hz from the autopilot (sysid 1), with a ground
station (sysid 255) also on the link, the way Mission Planner records it.
"""

import math
import struct

import numpy as np
from pymavlink.dialects.v20 import ardupilotmega as mavlink

from dflog import ARM, CLIMB_END, CRUISE_ALT, DESCENT, DISARM, END, TAKEOFF, TOUCHDOWN

T0 = 1_790_000_000.0          # 2026-09-21, wall clock
CELLS = 6


class _Sink:
    def __init__(self):
        self.packets = []      # (t, bytes)


def flight(*, rest_end_cell=3.95, res_ohm=0.03, statustexts=(), end=END, seed=2):
    rng = np.random.default_rng(seed)
    ap = mavlink.MAVLink(None, srcSystem=1, srcComponent=1)
    gcs = mavlink.MAVLink(None, srcSystem=255, srcComponent=190)
    out = []

    def send(t, mav, msg):
        out.append((T0 + t, msg.pack(mav)))

    def alt(t):
        if t < TAKEOFF or t >= TOUCHDOWN:
            return 0.0
        if t < CLIMB_END:
            return CRUISE_ALT * (t - TAKEOFF) / (CLIMB_END - TAKEOFF)
        if t < DESCENT:
            return CRUISE_ALT
        return CRUISE_ALT * (TOUCHDOWN - t) / (TOUCHDOWN - DESCENT)

    send(0.2, ap, ap.statustext_encode(6, b"ArduCopter V4.7.0 (1511f271)"))
    send(1.0, ap, ap.statustext_encode(3, b"PreArm: Radio failsafe on"))     # must not count
    for i, (name, value) in enumerate((("SERVO1_FUNCTION", 33), ("SERVO2_FUNCTION", 34), ("SERVO3_FUNCTION", 35),
                                       ("SERVO4_FUNCTION", 36), ("MOT_BAT_VOLT_MAX", 25.2))):
        send(0.3, ap, ap.param_value_encode(name.encode(), value, 9, 5, i))
    for t, text in statustexts:
        send(t, ap, ap.statustext_encode(2, text.encode()))

    for t in np.arange(0.0, end, 1 / 3):
        t = float(t)
        armed = ARM <= t < DISARM
        air = TAKEOFF <= t < TOUCHDOWN
        a = alt(t) + (rng.normal(0, 0.03) if air else 0.0)
        send(t, ap, ap.heartbeat_encode(2, 3, 81 | (128 if armed else 0), 5, 4 if armed else 3))
        if int(t * 3) % 3 == 0:
            send(t + 0.01, gcs, gcs.heartbeat_encode(6, 8, 0, 0, 0))

        frac = np.clip((t - ARM) / (TOUCHDOWN - ARM), 0, 1)
        rest = (4.15 - frac * (4.15 - rest_end_cell)) * CELLS
        curr = 14.0 if air else (0.8 if armed else 0.3)
        send(t, ap, ap.sys_status_encode(0, 0, 0, 200, int((rest - res_ohm * curr) * 1000), int(curr * 100),
                                         90, 0, 0, 0, 0, 0, 0))
        send(t, ap, ap.power_status_encode(0, 0, 0))                        # board without Vcc sensing
        vib = 14.0 if air else 2.0
        send(t, ap, ap.vibration_encode(int(t * 1e6), vib * 0.6 + rng.normal(0, 0.4), vib * 0.6 + rng.normal(0, 0.4),
                                        vib + rng.normal(0, 0.6), 0, 37, 0))
        send(t, ap, ap.vfr_hud_encode(0, 0, 90, (12 if air else (5 if armed else 0)), 100.0 + a, 0))
        send(t, ap, ap.global_position_int_encode(int(t * 1000), 0, 0, int((100 + a) * 1000), int(a * 1000),
                                                  0, 0, 0, 9000))
        des_r = 3.0 * math.sin(t / 3.0) if air else 0.0
        send(t, ap, ap.nav_controller_output_encode(des_r, 0.0, 90, 90, 0, alt(t) - a, 0, 0))
        send(t, ap, ap.attitude_encode(int(t * 1000), math.radians(des_r + rng.normal(0, 0.5)),
                                       math.radians(rng.normal(0, 0.5)), 1.57, 0, 0, 0))
        send(t, ap, ap.ekf_status_report_encode(0x1FF, 0.1, 0.15, 0.2, 0.0, 0.0))
        pwm = [1000] * 4 if not armed else ([1100] * 4 if not air else [1290, 1310, 1305, 1295])
        send(t, ap, ap.servo_output_raw_encode(int(t * 1e6), 0, *pwm, 0, 0, 0, 0))

    out.sort(key=lambda p: p[0])
    tlog = b"".join(struct.pack(">Q", int(ts * 1e6)) + pkt for ts, pkt in out)
    rlog = b"".join(pkt for _, pkt in out)
    return tlog, rlog
