"""Recommended actions: turn findings into what to do next.

Rules are plain if-statements on the check results, most specific first. A few rules link
findings into one cause (voltage collapse -> thrust loss -> hard landing) so the team fixes the
cause instead of three symptoms.
"""

import re

from .checks import FINDING, Status


def recommend(res):
    """List of action sentences for an AnalysisResult, most important first."""
    checks = {c.id: c for c in res.checks}

    def crit(i):
        return i in checks and checks[i].status == Status.CRITICAL

    def bad(i):
        return i in checks and checks[i].status in FINDING

    def detail(i):
        return checks[i].detail if i in checks else ""

    actions, covered = [], set()
    thrust_loss = "thrust loss" in detail(12).lower()

    # Linked causes first.
    if (crit(3) or crit(4)) and thrust_loss:
        actions.append(
            "Ground this battery. Its voltage collapsed under load and that caused the thrust loss"
            + (" and the hard landing" if bad(11) else "")
            + ". Measure the pack's internal resistance, check the battery connector and leads for heat "
              "damage, and repeat the flight with a known-good pack.")
        covered |= {3, 4, 12, 11}
    if bad(7) and 6 in checks and checks[6].value is not None and checks[6].value < 0.2:
        actions.append(
            f"The drone is over-powered (hover at {checks[6].value:.0%} throttle), so it cannot descend "
            "smoothly and altitude hold suffers. Lower MOT_SPIN_MIN, or fit smaller props or lower-KV motors.")
        covered.add(7)

    rules = {
        1: "Reduce vibration: check the props for chips and balance, tighten the motor screws and soft-mount "
           "the flight controller. Above 60 m/s² position and altitude hold stop working.",
        2: "The accelerometer is clipping (saturating): soft-mount the flight controller and balance the props.",
        3: "The pack dropped below a safe voltage under load: land earlier and check the pack's health.",
        4: "High voltage sag: the pack or its connector has high internal resistance. Test the pack, check "
           "the connector and consider a higher-C battery.",
        5: "Board voltage was low: check the BEC / power module and the servo load.",
        6: "Hover throttle is high: the drone is heavy for its motors and props and has little margin for "
           "gusts. Reduce weight or fit bigger props or stronger motors.",
        7: "Altitude hold was poor: check that the barometer is shielded from prop wash, then tune the "
           "altitude controller (PSC_ACCZ_P / PSC_ACCZ_I).",
        8: "Attitude tracking was poor: re-run AutoTune in calm air and check for loose props or arms.",
        9: "The position estimate disagreed with the IMU: check GPS or vision quality and the vibration level.",
        11: "Hard landing: inspect the frame, arms, landing gear and battery for damage before the next flight.",
        14: "Fix the battery parameters listed under check 14: the corrected values are in the attached "
            "suggested_fixes.param (review it, then load it in Mission Planner > Config > Full Parameter List).",
        15: "Set up the harmonic notch filter for the measured vibration frequency: the values are in the attached "
            "suggested_fixes.param. Reboot the flight controller after loading them.",
        16: "The compass is disturbed by the power wiring: run the compass/motor calibration (Mission Planner > "
            "Initial Setup > CompassMot), twist the battery leads together, or move the compass away from the ESCs "
            "and power module (a GPS-mast compass helps).",
    }
    for level in (Status.CRITICAL, Status.WARNING):
        for i in sorted(checks):
            c = checks[i]
            if c.status != level or i in covered:
                continue
            if i == 10:
                m = re.search(r"highest (C\d+)", c.detail)
                motor = f"the motor on output {m.group(1)[1:]}" if m else "the hardest-working motor"
                actions.append(f"Motor outputs are unbalanced and {motor} works hardest: check the centre of "
                               "gravity, look for a bent arm or twisted motor mount, and inspect that motor and prop.")
            elif i == 12:
                actions.append(_error_action(c.detail))
            elif i in rules:
                actions.append(rules[i].format(detail=c.detail))
            covered.add(i)

    if res.incomplete:
        actions.append("The log was cut short: copy the complete .bin from the SD card and analyse it again.")
    if res.source == "telemetry" and actions:
        actions.append("This was a telemetry log: analyse the .bin from the SD card for full detail.")
    if not actions:
        actions.append("No action needed. Keep this flight as a reference for the next ones.")
    return actions


def _error_action(detail):
    d = detail.lower()
    if "crash" in d:
        return "A crash was detected: inspect the airframe, props and battery before flying again."
    if "thrust loss" in d:
        return ("Potential thrust loss: a motor reached full output. Check the motors, ESCs, props and "
                "the battery voltage under load.")
    if "ekf" in d:
        return "The estimator raised a failsafe: check GPS or vision quality, compass calibration and vibration."
    return f"Review these errors in the log: {detail}."
