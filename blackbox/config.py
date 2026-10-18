from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "thresholds.yaml"


def load_config(path=None):
    """Load thresholds.yaml (or the given path) into a plain dict."""
    with open(path or DEFAULT_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def cells_for(cfg, instance, params=None, volt_at_arm=None):
    """Cell count for a BAT instance and where it came from.

    Config holds an int, a list per instance, or "auto". The log does not store
    cell count, so "auto" uses MOT_BAT_VOLT_MAX (set for thrust compensation,
    4.2 V per cell) and falls back to the voltage at arming.
    """
    cells = cfg["battery"]["cells"]
    if isinstance(cells, list):
        cells = cells[instance] if instance < len(cells) else cells[-1]
    if cells != "auto":
        return int(cells), "thresholds.yaml"
    vmax = (params or {}).get("MOT_BAT_VOLT_MAX", 0.0)
    if vmax > 0:
        n = max(1, round(vmax / 4.2))
        # A parameter left over from another pack (6S setting, 3S battery) must not win.
        if not volt_at_arm or 3.0 <= volt_at_arm / n <= 4.45:
            return n, "MOT_BAT_VOLT_MAX"
    if volt_at_arm:
        return max(1, round(volt_at_arm / 4.0)), "voltage at arming (set cells in thresholds.yaml to be sure)"
    return None, "unknown"
