"""Command line: python -m blackbox LOG.bin [LOG2.bin ...] [--json] [--config thresholds.yaml]"""

import argparse
import json
import sys

from .pipeline import analyze


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m blackbox", description="Health-check ArduPilot .bin logs")
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--config", help="thresholds file (default: thresholds.yaml)")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = ap.parse_args(argv)

    for path in args.logs:
        res = analyze(path, config=args.config)
        if args.json:
            print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
            continue
        head = res.verdict or res.status
        print(f"\n{res.file_name}: {head}" + (" (incomplete)" if res.incomplete else ""))
        if res.reason:
            print(f"  {res.exception_id or ''} {res.reason}".rstrip())
        if res.checks:
            when = f", {res.flight_date}" if res.flight_date else ""
            print(f"  {res.source}{when}, armed {res.armed_s:.0f} s, airborne {res.airborne_s:.0f} s, "
                  f"{res.firmware or 'firmware unknown'}")
        for c in res.checks:
            val = "" if c.value is None else f"{c.value:g} {c.unit}".strip()
            print(f"  {c.id:>2}. {c.name:<26} {c.status.value:<13} {val:<16} {c.detail}")
        for n in res.notes:
            print(f"  note: {n}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
