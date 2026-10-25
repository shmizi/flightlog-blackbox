"""Figures for the presentation and the report, drawn from the real logs.

    .venv\\Scripts\\python docs\\make_figures.py [folder with the real logs, default: ..]

Writes PNGs to docs/figures/. Every plot is computed from the logs with the same code the
checks use, so the numbers in the figures match the analysis.
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from blackbox import analyze, logbook  # noqa: E402
from blackbox.config import load_config  # noqa: E402
from blackbox.windows import mask, segment  # noqa: E402

LOGS = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent
OUT = ROOT / "docs" / "figures"
OUT.mkdir(parents=True, exist_ok=True)
CFG = load_config()

# Reference palette (dataviz skill): categorical slots in fixed order, reserved status colours.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
STATUS = {"Pass": "#0ca30c", "Warning": "#fab219", "Critical": "#d03b3b", "Not available": "#d9d8d2"}
SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
NAVY = "#1f3864"

plt.rcParams.update({
    "font.family": ["Segoe UI", "DejaVu Sans"], "font.size": 10,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "axes.titlecolor": INK, "axes.titlesize": 11,
    "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.spines.top": False,
    "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK2, "ytick.labelcolor": INK2,
    "lines.linewidth": 1.6, "legend.frameon": False, "legend.labelcolor": INK2,
})


def save(fig, name):
    fig.savefig(OUT / name, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print("wrote", name)


def load(name):
    res = analyze(LOGS / name)
    return res, res.parsed, segment(res.parsed, CFG)


def limit_line(ax, y, text, colour):
    ax.axhline(y, color=colour, lw=1.1, ls=(0, (4, 3)))
    ax.text(ax.get_xlim()[1], y, f" {text}", color=INK2, va="center", ha="left", fontsize=8.5)


# ----------------------------------------------------------------------------- 24-09 crash
def crash_figures():
    """Both flights of the 24-09 log side by side (the time between them is on the ground)."""
    res, p, w = load("2026-09-24 19-56-27.bin")
    cells = 6
    errs = p.tables["ERR"].query("Subsys == 25")["t"].to_numpy()
    bat, rcou = p.tables["BAT"], p.tables["RCOU"]

    def window(df, s, e):
        return df[(df["t"] >= s - 6) & (df["t"] <= e + 1)]

    fig, axes = plt.subplots(2, 2, figsize=(8.6, 5.0), sharey="row", gridspec_kw={"height_ratios": [3, 2]})
    for col, (s, e) in enumerate(w.airborne):
        b = window(bat[bat["Volt"] > 1], s, e)
        tt = b["t"] - s
        top, bottom = axes[0, col], axes[1, col]
        for ax in (top, bottom):
            ax.axvspan(0, e - s, color="#eef3fb", lw=0, zorder=0)
            for x in errs[(errs >= s) & (errs <= e)] - s:
                ax.axvline(x, color=STATUS["Critical"], lw=1.2)
        top.plot(tt, b["Volt"] / cells, color=SERIES[0])
        bottom.plot(tt, b["Curr"], color=SERIES[1])
        top.set_title(f"Flight {col + 1}", fontsize=10)
        bottom.set_xlabel("Time since takeoff (s)")
        for x in errs[(errs >= s) & (errs <= e)] - s:
            top.annotate("thrust loss", (x, 4.3), ha="right", va="top", fontsize=8.5, color=INK2,
                         xytext=(-4, 0), textcoords="offset points")
    top = axes[0, 0]
    top.set_ylim(0.8, 4.4)
    top.set_ylabel("Cell voltage (V)")
    axes[1, 0].set_ylabel("Current (A)")
    for ax in axes[0]:
        ax.axhline(3.5, color=STATUS["Warning"], lw=1.1, ls=(0, (4, 3)))
        ax.axhline(3.3, color=STATUS["Critical"], lw=1.1, ls=(0, (4, 3)))
        ax.axhline(p.params["BATT_LOW_VOLT"] / cells, color=MUTED, lw=1.1, ls=(0, (4, 3)))
    right = axes[0, 1]
    for y, text in ((3.5, "warning 3.5 V"), (3.3, "critical 3.3 V"), (p.params["BATT_LOW_VOLT"] / cells,
                                                                       "failsafe set\nat 1.77 V/cell")):
        right.text(right.get_xlim()[1], y, f" {text}", color=INK2, va="center", fontsize=8.5)
    fig.suptitle("24-09: the battery voltage collapsed under load in both flights (6S pack)", x=0.02, ha="left",
                 fontweight="bold", fontsize=11, color=INK)
    fig.tight_layout()
    save(fig, "crash_battery.png")

    fig, axes = plt.subplots(1, 2, figsize=(8.6, 3.2), sharey=True)
    for col, (s, e) in enumerate(w.airborne):
        ax = axes[col]
        r = window(rcou, s, e)
        ax.axvspan(0, e - s, color="#eef3fb", lw=0, zorder=0)
        for i, ch in enumerate(["C1", "C2", "C3", "C4"]):
            ax.plot(r["t"] - s, r[ch], color=SERIES[i], lw=1.2, label=f"Motor {ch[1:]}")
        ax.axhline(p.params["MOT_PWM_MAX"], color=MUTED, lw=1.1, ls=(0, (4, 3)))
        for x in errs[(errs >= s) & (errs <= e)] - s:
            ax.axvline(x, color=STATUS["Critical"], lw=1.2)
        ax.set_title(f"Flight {col + 1}", fontsize=10)
        ax.set_xlabel("Time since takeoff (s)")
    axes[0].set_ylabel("Motor output (µs)")
    axes[1].text(axes[1].get_xlim()[1], p.params["MOT_PWM_MAX"], " full output", color=INK2, va="center", fontsize=8.5)
    axes[0].legend(ncol=2, loc="upper left", fontsize=8.5)
    fig.suptitle("24-09: all four motors were driven to full output just before each thrust-loss error",
                 x=0.02, ha="left", fontweight="bold", fontsize=11, color=INK)
    fig.tight_layout()
    save(fig, "crash_motors.png")


# ----------------------------------------------------------------------------- 28-09 flight
def windows_figure():
    res, p, w = load("2026-09-28 17-17-40.bin")
    ctun = p.tables["CTUN"]
    t0 = w.takeoff
    fig, ax = plt.subplots(figsize=(8.2, 3.2))
    sel = mask(ctun["t"], [(w.armed[0][0] - 5, w.armed[-1][1] + 5)])
    shade = [("armed", w.armed, "#f1f0ec"), ("airborne", w.airborne, "#dce8f8"),
             ("trimmed (tracking checks)", w.trimmed, "#b7d3f6"), ("steady hover", w.hover, "#86b6ef")]
    for label, spans, colour in shade:
        for i, (s, e) in enumerate(spans):
            ax.axvspan(s - t0, e - t0, color=colour, lw=0, zorder=0, label=label if i == 0 else None)
    ax.plot(ctun["t"][sel] - t0, ctun["Alt"][sel], color=INK, lw=1.4, label="altitude")
    ax.set_xlim(w.airborne[-1][0] - t0 - 8, w.armed[-1][1] - t0 + 6)
    ax.set_title("Flight windowing: each check only looks at the right part of the flight (28-09)")
    ax.set_xlabel("Time since takeoff (s)")
    ax.set_ylabel("Altitude (m)")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.25), ncol=5, fontsize=8.5)
    fig.tight_layout()
    save(fig, "windows.png")


def spectrum_figure():
    res, p, w = load("2026-09-28 17-17-40.bin")
    head, data = p.tables["ISBH"], p.tables["ISBD"]
    head = head[mask(head["t"], w.trimmed) & (head["type"] == 1) & (head["instance"] == 0)]
    rows = {n: g.sort_values("seqno") for n, g in data[data["N"].isin(head["N"])].groupby("N")}
    spectra, rate, n_fft = [], None, None
    for _, b in head.iterrows():
        g, n = rows.get(b["N"]), int(b["smp_cnt"])
        if g is None or n < 256:
            continue
        axes = [np.concatenate([np.asarray(a, float) for a in g[ax]])[:n] / float(b["mul"]) for ax in "xyz"]
        if len(axes[0]) < n:
            continue
        spectra.append(sum(np.abs(np.fft.rfft((a - a.mean()) * np.hanning(n))) for a in axes))
        rate, n_fft = float(b["smp_rate"]), n
    spec = np.mean([s for s in spectra if len(s) == n_fft // 2 + 1], axis=0)
    f = np.fft.rfftfreq(n_fft, 1 / rate)
    band = (f >= 20) & (f <= min(400, 0.45 * rate))
    peak = f[band][np.argmax(spec[band])]
    freq, bw = p.params.get("INS_HNTCH_FREQ", 80), p.params.get("INS_HNTCH_BW", 40)
    harmonics = [h + 1 for h in range(8) if int(p.params.get("INS_HNTCH_HMNCS", 1)) & (1 << h)] or [1]

    fig, ax = plt.subplots(figsize=(8.2, 3.4))
    for h in harmonics:                       # the notch is applied at each enabled harmonic of its frequency
        c = freq * h
        ax.axvspan(c - bw / 2, c + bw / 2, color="#fde7c2", lw=0, zorder=0)
        ax.text(c, 0.04, f"notch {c:g} Hz", ha="center", va="bottom", fontsize=8.5, color=INK2,
                transform=ax.get_xaxis_transform())
    ax.plot(f[band], spec[band], color=SERIES[0])
    ax.annotate(f"vibration peak {peak:.0f} Hz", (peak, spec[band].max()), xytext=(14, -6),
                textcoords="offset points", fontsize=9, color=INK)
    ax.set_title(f"Check 15: the {peak:.0f} Hz vibration peak falls inside the notch's 2nd harmonic (28-09)")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Gyro amplitude (relative)")
    ax.set_yticklabels([])
    fig.tight_layout()
    save(fig, "spectrum.png")


def compass_figure():
    res, p, w = load("2026-09-28 17-17-40.bin")
    mag = p.tables["MAG"]
    mag = mag[(mag["I"] == 0) & mask(mag["t"], w.armed)] if "I" in mag else mag[mask(mag["t"], w.armed)]
    bat = p.tables["BAT"]
    field = np.sqrt(mag["MagX"] ** 2 + mag["MagY"] ** 2 + mag["MagZ"] ** 2).to_numpy()
    curr = np.interp(mag["t"], bat["t"], bat["Curr"])
    slope, earth = np.polyfit(curr, field, 1)
    pct = abs(slope) * curr.max() / earth * 100
    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    ax.scatter(curr[::3], field[::3], s=9, color=SERIES[0], alpha=0.45, lw=0)
    x = np.linspace(0, curr.max(), 50)
    ax.plot(x, earth + slope * x, color=SERIES[1], lw=2)
    ax.text(0.03, 0.95, f"fit: {slope:+.1f} mGauss per amp\n= {pct:.0f}% of the Earth's field at {curr.max():.0f} A",
            transform=ax.transAxes, va="top", fontsize=9, color=INK)
    ax.set_title("Check 16: compass field strength changes with current")
    ax.set_xlabel("Battery current (A)")
    ax.set_ylabel("Field strength (mGauss)")
    fig.tight_layout()
    save(fig, "compass.png")


# ----------------------------------------------------------------------------- all real flights
FLIGHTS = ["2026-09-24 19-56-27.bin", "2026-09-28 17-17-40.bin", "2026-09-28 17-17-09.tlog",
           "2026-09-28 17-31-19.tlog", "2026-10-15 16-50-18.tlog", "2026-10-15 16-58-32.tlog"]
SHORT = {1: "Vibration", 2: "Clipping", 3: "Cell voltage", 4: "Voltage sag", 5: "Board supply", 6: "Hover throttle",
         7: "Altitude hold", 8: "Attitude", 9: "Estimator", 10: "Motor balance", 11: "Hard landing", 12: "Errors",
         13: "External nav", 14: "Configuration", 15: "Vib. spectrum", 16: "Compass"}


def results_matrix():
    results = [analyze(LOGS / n) for n in FLIGHTS]
    fig, ax = plt.subplots(figsize=(9.6, 3.5))
    ax.grid(False)
    letters = {"Pass": "✓", "Warning": "!", "Critical": "✕", "Not available": "–"}
    for row, res in enumerate(results):
        for c in res.checks:
            col = c.id - 1
            ax.add_patch(plt.Rectangle((col + 0.04, row + 0.06), 0.92, 0.88, color=STATUS[c.status.value], lw=0))
            ax.text(col + 0.5, row + 0.52, letters[c.status.value], ha="center", va="center", fontsize=10,
                    color="#ffffff" if c.status.value in ("Pass", "Critical") else INK, fontweight="bold")
    ax.set_xlim(0, 16)
    ax.set_ylim(len(results), 0)
    ax.set_xticks(np.arange(16) + 0.5, [SHORT[i] for i in range(1, 17)], rotation=40, ha="right", fontsize=8.5)
    labels = [f"{r.file_name[:16].replace('2026-', '')}  {'.bin' if r.source == 'dataflash' else '.tlog'}  "
              f"{r.verdict}" for r in results]
    ax.set_yticks(np.arange(len(results)) + 0.5, labels, fontsize=8.5)
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("16 checks on every real flight      ✓ pass   ! warning   ✕ critical   – not available")
    fig.tight_layout()
    save(fig, "results_matrix.png")
    return results


def logbook_figure(results):
    """The Flights sheet of the generated Excel logbook, drawn as a table."""
    recs = [logbook.record(r, f"2026-10-21 10:{i:02d}") for i, r in enumerate(results)]
    wb = logbook.build(recs)
    ws = wb["Flights"]
    rows = list(ws.iter_rows(values_only=True))
    head = ["File", "Source", "Verdict", "Air (s)"] + [str(i) for i in range(1, 17)]
    body = [[r[2][:19], "bin" if r[4] == "dataflash" else "tlog", r[5], f"{r[7]:.0f}"] + [r[i] for i in range(8, 24)]
            for r in rows[1:]]
    fig, ax = plt.subplots(figsize=(10.5, 2.4))
    ax.axis("off")
    cells = [[v if j < 4 else "" for j, v in enumerate(row)] for row in body]
    tbl = ax.table(cellText=cells, colLabels=head, loc="center", cellLoc="center",
                   colWidths=[0.17, 0.05, 0.07, 0.05] + [0.04] * 16)
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(8)
    tbl.scale(1, 1.45)
    fills = {"Healthy": "#C6EFCE", "Pass": "#C6EFCE", "Warning": "#FFEB9C", "Critical": "#FFC7CE",
             "Not available": "#EDEDED"}
    for (r, c), cell in tbl.get_celld().items():
        cell.set_edgecolor("#d0d0d0")
        if r == 0:
            cell.set_facecolor(NAVY)
            cell.get_text().set_color("white")
            cell.get_text().set_fontweight("bold")
        else:
            v = body[r - 1][c]
            if c == 2 or c >= 4:
                cell.set_facecolor(fills.get(v, "white"))
    ax.set_title("Excel logbook, Flights sheet: one row per flight, one coloured column per check", loc="left")
    save(fig, "logbook_flights.png")


# ----------------------------------------------------------------------------- diagrams
def box(ax, x, y, w, h, text, fill="#ffffff", edge=AXIS, ink=INK, size=9.5, bold=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
                                fc=fill, ec=edge, lw=1.2))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size, color=ink,
            fontweight="bold" if bold else "normal", wrap=True)


def arrow(ax, a, b, colour=MUTED, text=None):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=12, color=colour, lw=1.4,
                                 shrinkA=2, shrinkB=2))
    if text:
        ax.text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + 0.12, text, ha="center", fontsize=8, color=INK2)


def canvas(w, h):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0, w)
    ax.set_ylim(0, h)
    ax.axis("off")
    return fig, ax


def existing_workflow():
    fig, ax = canvas(11, 2.1)
    steps = ["Land, pull the SD card\nor save the .tlog", "Open it in Mission\nPlanner or\nUAV Log Viewer",
             "Pick graphs from\nmemory and judge\nthem by eye", "Decide by feel:\nfly again?",
             "Maybe a line in\nthe team chat"]
    for i, s in enumerate(steps):
        box(ax, 0.2 + i * 2.2, 0.55, 1.85, 1.15, s, fill="#f1f0ec", size=9)
        if i:
            arrow(ax, (0.2 + i * 2.2 - 0.35, 1.12), (0.2 + i * 2.2, 1.12))
    ax.text(0.2, 0.12, "Slow · depends on who looks · no fixed limits · no record per flight · slow trends "
                       "(loose prop, ageing battery) go unnoticed", fontsize=9, color=STATUS["Critical"])
    save(fig, "workflow_existing.png")


def proposed_workflow():
    fig, ax = canvas(10, 4.2)
    box(ax, 0.2, 2.75, 1.7, 0.9, "Drop .bin / .tlog\n/ .param in folder", fill="#eef3fb")
    box(ax, 0.2, 1.35, 1.7, 0.9, "Upload in the\nStreamlit dashboard", fill="#eef3fb")
    box(ax, 2.4, 2.75, 1.5, 0.9, "n8n\nwatches folder", fill="#fdeee6")
    box(ax, 4.4, 1.9, 2.0, 1.6, "FastAPI\nPOST /analyze\n\nanalyze(log)", fill=NAVY, edge=NAVY, ink="white", bold=True)
    arrow(ax, (1.9, 3.2), (2.4, 3.2))
    arrow(ax, (3.9, 3.2), (4.4, 3.0))
    arrow(ax, (1.9, 1.8), (4.4, 2.3))
    core = ["validate (E1)", "parse .bin / .tlog", "flight windows", "16 checks", "verdict + actions"]
    for i, c in enumerate(core):
        ax.text(5.4, 1.65 - i * 0.3, c, ha="center", fontsize=8, color=INK2)
    outs = [("Email: verdict, findings,\nactions + attachments", "#e3f4e8"),
            ("Excel logbook\n(5 sheets, trends)", "#e3f4e8"),
            ("Map (KML) + suggested\nparameter fixes", "#e3f4e8"),
            ("Dashboard: graphs,\nmap, fleet trends", "#e3f4e8")]
    for i, (o, fill) in enumerate(outs):
        y = 3.25 - i * 0.95
        box(ax, 7.6, y, 2.2, 0.8, o, fill=fill)
        arrow(ax, (6.4, 2.7), (7.6, y + 0.4))
    ax.text(0.2, 0.05, "Analysis in under a second · email within a minute · same limits every time · every flight "
                       "logged and comparable", fontsize=9, color=STATUS["Pass"])
    save(fig, "workflow_proposed.png")


def n8n_figure():
    fig, ax = canvas(10, 2.35)
    nodes = [("Watch log\nfolder", "local file trigger"), ("Read log\nfile", "read binary"),
             ("Analyze log\nHTTP POST", "3 tries, 5 s apart"), ("Attach\nreports", "Code node"),
             ("Email\ndebrief", "SMTP (Gmail)")]
    for i, (n, sub) in enumerate(nodes):
        x = 0.2 + i * 1.95
        box(ax, x, 1.3, 1.5, 0.85, n, fill="#ffffff", edge="#ff6d5a")
        ax.text(x + 0.75, 2.22, sub, ha="center", fontsize=8, color=MUTED)
        if i:
            arrow(ax, (x - 0.45, 1.72), (x, 1.72))
    box(ax, 5.6, 0.1, 1.8, 0.65, "Email: service\noffline (E7)", fill="#fde2e1", edge=STATUS["Critical"], size=8.5)
    arrow(ax, (5.2, 1.3), (5.9, 0.75), colour=STATUS["Critical"])
    ax.text(5.45, 1.0, "error output", fontsize=8, color=INK2, ha="right")
    save(fig, "n8n_workflow.png")


if __name__ == "__main__":
    existing_workflow()
    proposed_workflow()
    n8n_figure()
    crash_figures()
    windows_figure()
    spectrum_figure()
    compass_figure()
    logbook_figure(results_matrix())
