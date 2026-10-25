"""Pre-flight GO / NO-GO from a parameter file, before the drone flies.

Save the parameters in Mission Planner (Config > Full Parameter List > Save to file) and drop the
.param file into the watched folder. The same battery rules as check 14 run on it, plus the
settings a post-flight analysis depends on, and the email says GO, GO with warnings, or NO-GO.

The 24-09 crash is the reason this exists: its BATT_LOW_VOLT was 1.77 V per cell, so the
low-battery failsafe could never fire. This check would have said NO-GO before takeoff.
"""

from .checks import CheckResult, Status, battery_param_problems
from .config import cells_for
from .pipeline import PREFLIGHT
from . import verdict as verdict_mod
from .validate import PARAM_LINE

GO, GO_WARN, NO_GO = "GO", "GO with warnings", "NO-GO"
VERDICTS = {verdict_mod.HEALTHY: GO, verdict_mod.WARNING: GO_WARN, verdict_mod.CRITICAL: NO_GO}

# LOG_BITMASK bits the post-flight checks need (ArduCopter numbering).
NEEDED_LOGGING = {0: "ATTITUDE_FAST", 1: "ATTITUDE_MED", 4: "CTUN", 7: "IMU", 9: "CURRENT", 10: "RCOUT"}


def read_params(path):
    params = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or not PARAM_LINE.match(line):
            continue
        parts = line.replace(",", " ").split()
        name, value = (parts[2], parts[3]) if parts[0].isdigit() else (parts[0], parts[1])
        params[name] = float(value)
    return params


def run(path, cfg, res):
    p = read_params(path)
    res.status = PREFLIGHT
    res.checks = [battery(p, cfg), battery_monitor(p), radio_failsafe(p), arming_checks(p), logging(p),
                  vibration_filter(p)]
    res.verdict = VERDICTS[verdict_mod.decide(res.checks)]
    res.actions = actions(res)
    res.notes.append(f"Pre-flight check of {len(p)} parameters. It checks settings only: inspect the "
                     "airframe, props and battery as usual.")
    return res


def battery(p, cfg):
    cid, name = 101, "Battery failsafe"
    cells, source = cells_for(cfg, 0, p)
    if cells is None:
        return CheckResult(cid, name, Status.WARNING, detail="Pack size unknown: set MOT_BAT_VOLT_MAX "
                           "(4.2 V x cells) or pin battery.cells in thresholds.yaml to check the failsafe")
    problems, fixes = battery_param_problems(p, cells, cfg["checks"]["configuration"]["min_failsafe_cell_v"])
    if not problems:
        return CheckResult(cid, name, Status.PASS, detail=f"Failsafe settings suit a {cells}S pack ({source})")
    # A failsafe that cannot fire is a NO-GO: the drone keeps flying until the pack collapses.
    status = Status.CRITICAL if any("failsafe" in pr or "nothing happens" in pr for pr in problems) else Status.WARNING
    return CheckResult(cid, name, status, len(problems), unit="issues",
                       detail=f"{cells}S pack ({source}): " + "; ".join(problems), fixes=fixes)


def battery_monitor(p):
    cid, name = 102, "Battery monitor"
    if p.get("BATT_MONITOR", 1) == 0:
        return CheckResult(cid, name, Status.CRITICAL, detail="BATT_MONITOR = 0: the drone cannot see its battery "
                           "voltage, so no battery failsafe can work", fixes={"BATT_MONITOR": (4, "4 = analog voltage and current")})
    return CheckResult(cid, name, Status.PASS, detail=f"BATT_MONITOR = {p.get('BATT_MONITOR', 'not in file')}")


def radio_failsafe(p):
    cid, name = 103, "Radio failsafe"
    if p.get("FS_THR_ENABLE") == 0:
        return CheckResult(cid, name, Status.WARNING, detail="FS_THR_ENABLE = 0: nothing happens if the radio link is lost",
                           fixes={"FS_THR_ENABLE": (1, "1 = RTL, or 3 = Land without GPS")})
    return CheckResult(cid, name, Status.PASS, detail="Radio failsafe is on")


def arming_checks(p):
    cid, name = 104, "Pre-arm checks"
    if p.get("ARMING_CHECK") == 0:
        return CheckResult(cid, name, Status.WARNING, detail="ARMING_CHECK = 0: all pre-arm safety checks are off",
                           fixes={"ARMING_CHECK": (1, "1 = all checks")})
    return CheckResult(cid, name, Status.PASS, detail="Pre-arm checks are on")


def logging(p):
    cid, name = 105, "Logging for post-flight analysis"
    mask = int(p.get("LOG_BITMASK", -1))
    if mask < 0:
        return CheckResult(cid, name, Status.PASS, detail="LOG_BITMASK not in the file")
    missing = [label for bit, label in NEEDED_LOGGING.items() if bit not in (0, 1) and not mask & (1 << bit)]
    if not mask & 0b11:
        missing.insert(0, "ATTITUDE_MED")
    notes = "" if p.get("INS_LOG_BAT_MASK", 1) else "; INS_LOG_BAT_MASK = 0, so no vibration spectrum (check 15)"
    if missing:
        return CheckResult(cid, name, Status.WARNING,
                           detail=f"LOG_BITMASK is missing {', '.join(missing)}: those post-flight checks will "
                                  f"show Not available{notes}")
    return CheckResult(cid, name, Status.PASS, detail=f"Everything the post-flight checks need is logged{notes}")


def vibration_filter(p):
    cid, name = 106, "Vibration filter"
    if not p.get("INS_HNTCH_ENABLE"):
        return CheckResult(cid, name, Status.PASS, detail="Harmonic notch is off: after the first flight, check 15 "
                           "measures the vibration frequency and suggests settings")
    if p.get("INS_HNTCH_MODE", 0) == 1 and p.get("INS_HNTCH_REF", 0) == 0:
        hover = p.get("MOT_THST_HOVER") or 0.35
        return CheckResult(cid, name, Status.WARNING,
                           detail="Harmonic notch is in throttle mode but INS_HNTCH_REF = 0 turns tracking off, so "
                                  f"it stays fixed at {p.get('INS_HNTCH_FREQ', 0):g} Hz",
                           fixes={"INS_HNTCH_REF": (round(hover, 3), "hover throttle (MOT_THST_HOVER)")})
    return CheckResult(cid, name, Status.PASS, detail="Harmonic notch is set up")


def actions(res):
    bad = verdict_mod.findings(res.checks)
    if not bad:
        return ["Parameters look safe to fly."]
    out = []
    if res.verdict == NO_GO:
        out.append("Do not fly until the NO-GO items are fixed: the drone could not protect itself.")
    for c in bad:
        out.append(f"{c.name}: {c.detail}.")
    if any(c.fixes for c in bad):
        out.append("The corrected values are in the attached suggested_fixes.param: review it, load it in Mission "
                   "Planner (Config > Full Parameter List > Load from file), Write Params, then save a new .param "
                   "file and check it again.")
    return out
