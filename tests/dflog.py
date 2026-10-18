"""Write synthetic ArduPilot DataFlash logs for tests.

Message formats and labels are copied from ArduPilot master LogStructure
headers, so pymavlink's DFReader parses these files exactly like a real
.bin. The flight is a 4S quad that arms, climbs to 1.5 m in LOITER,
hovers, lands and disarms; keyword faults bend it into each test scenario.
"""

import struct

import numpy as np

STRUCT = {"b": "b", "B": "B", "h": "h", "H": "H", "i": "i", "I": "I", "f": "f", "d": "d",
          "n": "4s", "N": "16s", "Z": "64s", "c": "h", "C": "H", "e": "i", "E": "I",
          "L": "i", "M": "B", "q": "q", "Q": "Q"}
SCALE = {"c": 100, "C": 100, "e": 100, "E": 100, "L": 1e7}

FORMATS = {
    "PARM": ("QNff", "TimeUS,Name,Value,Default"),
    "MSG": ("QBHZ", "TimeUS,ID,Seq,Message"),
    "MODE": ("QMBB", "TimeUS,Mode,ModeNum,Rsn"),
    "EV": ("QB", "TimeUS,Id"),
    "ARM": ("QBIBB", "TimeUS,ArmState,ArmChecks,Forced,Method"),
    "ERR": ("QBB", "TimeUS,Subsys,ECode"),
    "VIBE": ("QBfffI", "TimeUS,IMU,VibeX,VibeY,VibeZ,Clip"),
    "IMU": ("QBffffffIIfBBHH", "TimeUS,I,GyrX,GyrY,GyrZ,AccX,AccY,AccZ,EG,EA,T,GH,AH,GHz,AHz"),
    "BAT": ("QBfffffcfBBB", "TimeUS,Inst,Volt,VoltR,Curr,CurrTot,EnrgTot,Temp,Res,RemPct,H,SH"),
    "POWR": ("QffHHB", "TimeUS,Vcc,VServo,Flags,AccFlags,Safety"),
    "CTUN": ("Qffffffffffff", "TimeUS,ThI,ABst,ThO,ThH,DAlt,Alt,BAlt,DSAlt,SAlt,TAlt,DCRt,CRt"),
    "ATT": ("QffffffB", "TimeUS,DesRoll,Roll,DesPitch,Pitch,DesYaw,Yaw,AEKF"),
    "XKF4": ("QBcccccfffHBIHb", "TimeUS,C,SV,SP,SH,SM,SVT,errRP,OFN,OFE,FS,TS,SS,GPS,PI"),
    "RCOU": ("QHHHHHHHHHHHHHH", "TimeUS,C1,C2,C3,C4,C5,C6,C7,C8,C9,C10,C11,C12,C13,C14"),
    "VISP": ("QQIffffffffBBb", "TimeUS,RTimeUS,CTimeMS,PX,PY,PZ,R,P,Y,PErr,AErr,Rst,Ign,Q"),
}


def _struct(fmt):
    return "<" + "".join(STRUCT[c] for c in fmt)


class DFWriter:
    def __init__(self):
        self.out = bytearray()
        self.ids = {}
        self._fmt(128, "FMT", "BBnNZ", "Type,Length,Name,Format,Columns")
        for i, (name, (fmt, labels)) in enumerate(FORMATS.items()):
            self.ids[name] = 129 + i
            self._fmt(129 + i, name, fmt, labels)

    def _fmt(self, mid, name, fmt, labels):
        length = 3 + struct.calcsize(_struct(fmt))
        self.out += b"\xa3\x95\x80" + struct.pack("<BB4s16s64s", mid, length, name.encode(),
                                                  fmt.encode(), labels.encode())

    def write(self, name, t, **vals):
        fmt, labels = FORMATS[name]
        vals["TimeUS"] = int(round(t * 1e6))
        args = []
        for c, lab in zip(fmt, labels.split(",")):
            v = vals.get(lab, 0)
            if c in "nNZ":
                v = v.encode() if isinstance(v, str) else b""
            elif c in SCALE:
                v = int(round(v * SCALE[c]))
            elif STRUCT[c] in "bBhHiIqQ":
                v = int(round(v))
            else:
                v = float(v)
            args.append(v)
        self.out += bytes((0xA3, 0x95, self.ids[name])) + struct.pack(_struct(fmt), *args)


# Timeline of the clean flight (seconds since boot).
ARM, TAKEOFF, CLIMB_END, DESCENT, TOUCHDOWN, DISARM, END = 5.0, 7.0, 10.0, 97.0, 100.0, 103.0, 105.0
CRUISE_ALT = 1.5
CELLS = 4


def flight(
    *,
    bench=False,               # T8: powered on with LOG_DISARMED=1, never armed
    vibe=None,                 # T2: sustained VibeX/Y/Z level while airborne (m/s^2)
    clip_rate=0.0,             # T2: clipping events per second on IMU 0 while airborne
    rest_end_cell=3.90,        # T3: resting cell voltage at touchdown
    res_ohm=0.02,              # T3: pack internal resistance
    weak_motor_at=None,        # T4: motor 1 weakens here, fails 10 s later and the drone crashes
    hover_thr=0.42,            # CTUN.ThO in hover; logged ThH follows it but caps at 0.6875 like ArduPilot
    hard_landing=False,        # fast descent and impact at touchdown
    att_err=1.0,               # attitude tracking error amplitude (deg)
    ekf_sp=None,               # (start, end, value) to hold XKF4.SP high on core 0
    errors=(),                 # extra (t, subsys, ecode) ERR records
    ext_nav=False, ext_nav_gap=None,   # VISP at 15 Hz; gap = (start, duration)
    drop=(),                   # message names to leave out (E3)
    mode=5,                    # LOITER
    aborted_arm=False,         # an extra arm/disarm before the flight with no takeoff
    vcc_nan=False,             # flight controller without board-voltage sensing (POWR.Vcc NaN)
    params=None,               # extra PARM values, e.g. {"MOT_BAT_VOLT_MAX": 25.2}
    cells=CELLS,
    seed=1,
):
    rng = np.random.default_rng(seed)
    recs = []                  # (t, name, fields); sorted by time before writing

    def rec(t, name, **f):
        if name not in drop:
            recs.append((t, name, f))

    end = 30.0 if bench else END
    crash_at = None if weak_motor_at is None else weak_motor_at + 10.0
    disarm = None if bench else (crash_at + 2.6 if crash_at else DISARM)
    if crash_at:
        end = disarm + 2.0

    def armed(t):
        return disarm is not None and ARM <= t < disarm

    def alt_target(t):
        if bench or t < TAKEOFF:
            return 0.0
        if t < CLIMB_END:
            return CRUISE_ALT * (t - TAKEOFF) / (CLIMB_END - TAKEOFF)
        if t < DESCENT:
            return CRUISE_ALT
        if t < TOUCHDOWN:
            if hard_landing and t >= TOUCHDOWN - 0.6:
                return 0.0
            return CRUISE_ALT * (TOUCHDOWN - t) / (TOUCHDOWN - DESCENT)
        return 0.0

    def alt_actual(t):
        a = alt_target(t)
        if hard_landing and TOUCHDOWN - 1.2 <= t < TOUCHDOWN:
            a = max(0.0, 2.5 * (TOUCHDOWN - t))          # drops at 2.5 m/s
        if crash_at and t >= crash_at + 1.0:
            a = max(0.0, CRUISE_ALT - 3.0 * (t - crash_at - 1.0))
        return a

    def airborne(t):
        return alt_actual(t) > 0.02 or (TAKEOFF <= t < TOUCHDOWN and not bench)

    # Static records -----------------------------------------------------------
    rec(0.05, "MSG", Message="ArduCopter V4.5.7 (2a3dc4b7)")
    rec(0.06, "MODE", Mode=mode, ModeNum=mode, Rsn=0)
    for i, fn in enumerate((33, 34, 35, 36), start=1):
        rec(0.07, "PARM", Name=f"SERVO{i}_FUNCTION", Value=fn)
    rec(0.07, "PARM", Name="MOT_PWM_MIN", Value=1000)
    rec(0.07, "PARM", Name="MOT_PWM_MAX", Value=2000)
    for k, v in (params or {}).items():
        rec(0.07, "PARM", Name=k, Value=v)
    if aborted_arm:
        for t, ev, st in ((1.5, 10, 1), (3.0, 11, 0)):
            rec(t, "EV", Id=ev)
            rec(t, "ARM", ArmState=st, Method=2)
    # Bench quirks before arming: no RC receiver yet, USB power. Windowing must ignore them.
    rec(1.0, "ERR", Subsys=5, ECode=1)
    rec(4.5, "ERR", Subsys=5, ECode=0)

    if not bench:
        rec(ARM, "EV", Id=10)
        rec(ARM, "ARM", ArmState=1, Method=1)
        rec(TAKEOFF, "EV", Id=28)
        if crash_at:
            rec(weak_motor_at + 0.5, "ERR", Subsys=25, ECode=1)
            rec(crash_at + 2.5, "ERR", Subsys=12, ECode=1)
        else:
            rec(TOUCHDOWN + 0.2, "EV", Id=17)
            rec(TOUCHDOWN + 1.0, "EV", Id=18)
        rec(disarm, "EV", Id=11)
        rec(disarm, "ARM", ArmState=0, Method=2)
    for t, sub, code in errors:
        rec(t, "ERR", Subsys=sub, ECode=code)

    # 10 Hz records -------------------------------------------------------------
    clip0 = 0.0
    for t in np.arange(0.1, end, 0.1):
        t = float(t)
        air, arm = airborne(t), armed(t)
        on_usb = t < 4.0

        # Battery: resting voltage falls linearly over the flight; Volt sags with current.
        frac = np.clip((t - ARM) / (TOUCHDOWN - ARM), 0, 1)
        rest = (4.15 - frac * (4.15 - rest_end_cell)) * cells
        curr = 25.0 if air else (1.5 if arm else 0.4)
        if on_usb:
            rec(t, "BAT", Inst=0, Volt=0.0, VoltR=0.0, Curr=0.0, Temp=25)
            rec(t, "POWR", Vcc=np.nan if vcc_nan else 4.62, VServo=0.0)
        else:
            rec(t, "BAT", Inst=0, Volt=rest - res_ohm * curr * cells / CELLS, VoltR=rest, Curr=curr,
                Res=res_ohm, Temp=30)
            rec(t, "POWR", Vcc=np.nan if vcc_nan else 5.12 + rng.normal(0, 0.01), VServo=5.0)

        # Vibration: IMU 1's clip counter sits at 37 from handling before boot. Mixing instances
        # would read that as 37 clips; per-instance evaluation must see 0.
        base = 12.0 if air else 2.0
        level = vibe if (vibe and air) else base
        if air and clip_rate:
            clip0 += clip_rate * 0.1
        for imu, clip in ((0, clip0), (1, 37)):
            rec(t, "VIBE", IMU=imu, VibeX=level * 0.6 + rng.normal(0, 0.5), VibeY=level * 0.6 + rng.normal(0, 0.5),
                VibeZ=level + rng.normal(0, 0.8), Clip=int(clip))

        # Control tuning.
        tho = 0.0 if not arm else (hover_thr if air else 0.12)
        if weak_motor_at and t >= weak_motor_at and air:
            tho = hover_thr + 0.13
        dalt, alt = alt_target(t), alt_actual(t) + (rng.normal(0, 0.03) if air else 0.0)
        rec(t, "CTUN", ThI=tho, ThO=tho + rng.normal(0, 0.01) * arm, ThH=min(hover_thr - 0.01, 0.6875),
            DAlt=dalt, Alt=alt, BAlt=alt)

        # Attitude.
        des_r = 3.0 * np.sin(t / 3.0) if air else 0.0
        des_p = 2.0 * np.cos(t / 4.0) if air else 0.0
        err_amp = att_err if air else 0.2
        if crash_at and t >= crash_at:
            err_amp = 40.0
        rec(t, "ATT", DesRoll=des_r, Roll=des_r + rng.normal(0, err_amp / 2),
            DesPitch=des_p, Pitch=des_p + rng.normal(0, err_amp / 2), DesYaw=90.0, Yaw=90.0)

        # EKF innovations, one row per core.
        for core in (0, 1):
            sp = 0.15 + abs(rng.normal(0, 0.05))
            if ekf_sp and core == 0 and ekf_sp[0] <= t <= ekf_sp[1]:
                sp = ekf_sp[2]
            rec(t, "XKF4", C=core, SV=0.1 + abs(rng.normal(0, 0.03)), SP=sp, SH=0.2 + abs(rng.normal(0, 0.04)),
                SM=0.0, SVT=0.0)

        # Motor outputs.
        if not arm:
            pwm = [1000] * 4
        elif not air:
            pwm = [1100] * 4
        else:
            pwm = [1490, 1510, 1505, 1495]
            if weak_motor_at and t >= weak_motor_at:
                pwm = [1760, 1480, 1475, 1470] if t < crash_at else [1950, 1250, 1240, 1260]
        rec(t, "RCOU", **{f"C{i + 1}": p + rng.normal(0, 8) * (p > 1100) for i, p in enumerate(pwm)})

    # 25 Hz IMU, two instances ---------------------------------------------------
    impact = None
    if hard_landing:
        impact = (TOUCHDOWN, 6.0)
    if crash_at:
        impact = (crash_at + 1.0 + CRUISE_ALT / 3.0, 8.0)
    for t in np.arange(0.02, end, 0.04):
        t = float(t)
        az = -9.81
        if impact and abs(t - impact[0]) < 0.03:
            az = -9.81 * impact[1]
        elif TOUCHDOWN - 0.04 < t < TOUCHDOWN + 0.04 and not bench and not crash_at:
            az = -9.81 * 1.4                     # a normal, gentle touchdown
        for inst in (0, 1):
            rec(t, "IMU", I=inst, AccX=rng.normal(0, 0.3), AccY=rng.normal(0, 0.3), AccZ=az + rng.normal(0, 0.3),
                GyrX=rng.normal(0, 0.01), GyrY=rng.normal(0, 0.01), GyrZ=rng.normal(0, 0.01), T=40)

    # 15 Hz external navigation from the Jetson ---------------------------------
    if ext_nav and not bench:
        for t in np.arange(ARM, disarm, 1 / 15):
            t = float(t)
            if ext_nav_gap and ext_nav_gap[0] <= t < ext_nav_gap[0] + ext_nav_gap[1]:
                continue
            rec(t, "VISP", RTimeUS=int(t * 1e6), PZ=-alt_actual(t), PErr=0.05, Q=90)

    w = DFWriter()
    for t, name, f in sorted(recs, key=lambda r: r[0]):
        w.write(name, t, **f)
    return bytes(w.out)
