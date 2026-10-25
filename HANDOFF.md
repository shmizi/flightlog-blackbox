# FlightLog Black Box: Handoff

**As of 20 Oct 2026.** Repository: https://github.com/shmizi/flightlog-blackbox (private).
For setup steps see [PROJECT_GUIDE.md](PROJECT_GUIDE.md); for the technical reference see [README.md](README.md).

---

## 1. Where things stand

| Area | Status |
|---|---|
| Analysis core (validation, parsing, 16 checks, verdict, recommended actions) | **Done**, 83 tests passing (88 with the real-log ones) |
| Extras: flight map, parameter fix file, earlier-flight comparison, pre-flight GO / NO-GO, ledger | **Done**, tested on the real logs |
| `.bin` (SD card) and `.tlog` (telemetry) logs | **Done**; `.rlog` rejected with a clear message |
| Exception handling E1–E5, E7 | **Done** (E7 lives in the n8n workflow) |
| FastAPI service (`api.py`) | **Done**, tested live on real logs |
| n8n workflow (watched folder → API → Gmail) | **Done and tested end to end** with a local test mail server. Needs your Gmail App Password (`scripts/setup_n8n.py`) to send real email |
| Excel logbook (5 sheets, trend charts), E6 | **Done**, attached to every email |
| PowerPoint debrief | **Not built** (dashboard and email cover the content) |
| Streamlit dashboard (analyse a log + fleet logbook) | **Done**, checked in the browser on the real logs |
| Threshold tuning on clean flights | **Not started** |
| Report, diagrams, screenshots, video, viva prep | **Not started** |

The deadline pressure from the plan still applies: lab exams start in the last week of October.

---

## 2. What was done

### 2.1 Plan review
The original plan (CA3 Project Plan PDF) was checked against the ArduPilot and pymavlink source
before building. These mistakes were found and corrected in the build:

- **No flight windowing.** Bench time before arming (0 V battery on USB, radio failsafe without a
  receiver) would have produced false findings. Checks now look only at armed or airborne time.
- **Sensor instances mixed.** VIBE, IMU, BAT and XKF4 have one row per sensor or EKF core. They are
  now checked per instance.
- **Hover throttle could never reach Critical.** ArduPilot caps the learned `ThH` at 0.6875, so a
  0.75 limit could not fire. The check uses `ThO` in hover instead.
- **Hard landing looked at the wrong moment.** "5 s before disarm" is on the ground with the default
  10 s `DISARM_DELAY`. It now looks at touchdown.
- **pymavlink picks its reader by file extension.** An upload saved under a temp name would be
  misread. The parser opens the right reader directly.
- **Smaller fixes:** voltage sag from `VoltR − Volt`; altitude hold only in altitude-controlled
  modes; motor channels from `SERVOn_FUNCTION`; resolved errors (ECode 0) ignored; truncation
  detected by "log ends while armed".
- **n8n design fixes:** reports returned as attachments (n8n cannot attach a server file path);
  one log at a time; retry then "offline" email; Docker vs localhost address; 7-day Google token
  expiry noted.

### 2.2 Core (Tier 1)
- `blackbox/` package with one entry point, `analyze(log_path)`.
- 16 checks: the plan's 12, plus external-navigation gaps (the Jetson's vision data), a
  configuration check of the battery parameters against the pack actually fitted, a vibration
  spectrum (FFT of the IMU batch samples, compared with the harmonic notch) and compass
  interference (field strength against current, as in ArduPilot's CompassMot).
- Emails attach a flight map (`flight_path.kml`) and a `suggested_fixes.param`, and compare the
  flight with the same drone's earlier flights (recognised by the board's CPU serial).
- Pre-flight GO / NO-GO from a `.param` file; the 24-09 crash drone's parameters give NO-GO.
- Tamper-evident ledger: each processed log's record is hash-chained to the previous one (`GET /ledger`).
- `.bin` logs now get their real flight date from GPS time.
- Recommended actions for every finding, with linked causes (for example "battery voltage
  collapsed → thrust loss → hard landing"), in a plain-text and an HTML email with a coloured
  verdict banner. Checks that do not apply to the drone (no vision system, no Vcc sensor) are
  left out of the email.
- All limits in `thresholds.yaml`, including the battery cell count. It defaults to `auto`, read
  from `MOT_BAT_VOLT_MAX` or the voltage at arming.
- Synthetic DataFlash log writer (`tests/dflog.py`) using ArduPilot's real message formats, so
  tests T1–T8 run without real flights.

### 2.3 Tested on the team's real logs
- **Files:** 2 `.bin`, 17 `.tlog`, 15 `.rlog`, 16 Mission Planner thumbnails.
- **Six bugs exposed by real data, now fixed, each with a regression test:**
  - wrong cell count (6S pack)
  - a board without Vcc sensing
  - takeoff detection after an aborted arm
  - errors logged after disarm
  - a crash in the sag check when two takeoffs came close together
  - `.rlog` files starting with serial noise
- **Results:** every verdict on the real logs matched what the raw data shows (see section 5).

### 2.4 Telemetry (`.tlog`) support
- `blackbox/tlog.py` maps MAVLink telemetry onto the same tables, so the checks are unchanged.
  Results carry a "reduced detail" note and a real flight date.
- **Cross-check:** the 28-09 flight was recorded both ways, and the `.tlog` and `.bin` gave the same
  verdict and nearly identical values (2.878 V vs 2.876 V min cell; 1.625 m vs 1.625 m altitude
  error; 113 µs vs 113 µs motor spread).

### 2.5 API and n8n (Tier 2)
- **`api.py` endpoints:** `POST /analyze` (with optional inline attachments) and
  `GET /files/{flight_id}/{name}`.
- **Repeat uploads (E4):** analysed again in full with a note, counted once (registry in
  `output/processed.json`, guarded by a file lock). Four simultaneous
  copies of one log count once.
- **`n8n/flightlog_workflow.json`:** Watch folder → Read → Analyze (one at a time, 3 retries)
  → Attach reports → Gmail (SMTP with an App Password), plus the offline-email branch. No Google Cloud
  project is needed. The Google Drive version is kept as `flightlog_workflow_gdrive.json`.
- **`scripts/setup_n8n.py`** (one-time: folder, email password, import, switch on) and **`scripts/start.ps1`**
  (starts the API and n8n). Tested end to end: `.bin`, `.tlog`, `.rlog` and API-offline each produced the
  right email.
- **Bug found by the end-to-end test:** n8n could not reach `localhost:8000` because Node 24 resolves
  localhost to IPv6. The workflow now uses `127.0.0.1`.
- **Validation:** imported with n8n's CLI and validated with `n8n/validate_workflow.js` against
  n8n's own node definitions.

### 2.6 Documentation and sharing
- `README.md` (technical reference), `PROJECT_GUIDE.md` (full setup, including email and n8n), this handoff.
- Pushed to GitHub. Flight logs are kept out of git and shared separately.

---

## 3. Known limitations (say these honestly in the report)

- **Thresholds are not tuned.** Vibration and EKF limits come from ArduPilot's docs; the rest are
  starting values.
- **The logs are from a different airframe than the plan describes.** Every log so far comes from
  a BOTWINGF405 GPS quad, not the 4 kg GPS-denied Jetson drone. Decide which airframe the report is
  about. If it is the quad, drop the GPS-denied notes and check 13 from the write-up.
- **Bad-flight tests use synthetic logs.** T2–T4 run on generated logs, not SITL. The real-log
  tests skip until files named `T1_clean.bin` … `T4_motor_fail.bin` are placed in `tests/data/`.
- **Telemetry logs are coarse.** At 2–4 Hz they can miss short spikes, have no impact data, and
  their errors come from text messages.
- **No real email sent yet.** The workflow was tested with a local mail server; sending through Gmail
  needs your App Password.
- **The duplicate registry is temporary.** It is a JSON file until the Excel Flights sheet exists.
- **GPS-denied flights have no map or date from a `.bin`.** The flight date and the map both come from
  GPS, so the indoor Jetson drone's `.bin` logs will have neither (its `.tlog` still gets a date).
- **Not a diagnosis.** The system flags symptoms by rule; it does not find root causes.

---

## 4. Next steps (in order)

| # | Task | Owner | Notes |
|---|---|---|---|
| 1 | Send the first real email | Either | Make a Gmail App Password, run `scripts/setup_n8n.py`, then `scripts/start.ps1`, and drop a log into `flight-logs/` (PROJECT_GUIDE §6–8). Take screenshots of the n8n execution and the received email for the Screenshots folder. |
| 2 | ~~Excel logbook~~ | Done | `blackbox/logbook.py`; E6 included. |
| 3 | E6: logbook open in Excel | Partner | Retry 3× 10 s on `PermissionError`, then write `flight_logbook_pending.xlsx` and merge on the next run. Add test T9. |
| 4 | Switch E4 from `processed.json` to the Flights sheet | Partner | Pass the sheet's hashes as `known_hashes`. |
| 5 | Plots and the PowerPoint debrief (8 slides) | Partner | Write the files in `api._write_reports()` and add them to `REPORT_FILES`; n8n then attaches them automatically. |
| 6 | ~~Streamlit page~~ | Done | `app.py`, started by `start.ps1` on port 8501. |
| 7 | Test T10 (API down) | Either | Stop the API, upload a log, screenshot the offline email. |
| 8 | Real-log fixtures | You | Copy logs into `tests/data/` as `T1_clean.bin` etc. A truly clean flight is still needed for T1. |
| 9 | Tune `thresholds.yaml` | You | Run 2–3 clean flights, set each Warning comfortably above the clean maximum, and record the final values in the report. |
| 10 | Optional: SITL fault logs for T2–T4 | You | ArduPilot SITL needs WSL2 on Windows. Synthetic logs already cover the logic. |
| 11 | Manual-review timing | You | Time one manual review in Mission Planner for the before/after figure. |
| 12 | Deliverables | Both | Workflow diagrams (existing, proposed, n8n), one screenshot per test, the side-by-side check against UAV Log Viewer, report, 5-minute video (timings in the plan), submission folder `24070127001_<partner PRN>_<Name>_CA3/`. |
| 13 | Viva swap | Both | Each person explains the other's half. Note: the API and n8n were built alongside the core, so both of you should walk through `api.py` and the workflow. |

**Still-open questions from the plan:** the CA3 submission deadline, the partner's name and PRN,
and which airframe the report covers.

### Viva answers that changed from the plan
- **"What if two logs arrive at once?"** They arrive as separate items in one n8n run, which sends
  them to the API one at a time. A file lock and the SHA-256 registry stop double counting.
- **"Where do thresholds come from?"** The same as the plan, plus: hover throttle uses `ThO`,
  because ArduPilot caps the learned `ThH` at 0.6875.
- **New: "Can it read Mission Planner telemetry logs?"** Yes, `.tlog` files with reduced detail.
  Verified against the `.bin` of the same flight.

---

## 5. Findings about the drone (from the real logs)

These are real problems, not software errors. They are worth raising with the team and good
material for the report:

- **The battery or power path collapses under load.**
  - On 24-09, the pack fell from 22 V to about 7.5 V at 14–17 A. The motors saturated, ArduPilot
    logged "Potential Thrust Loss", and the drone came down at 3.5 m/s. This happened on both
    flights in that log.
  - 28-09 17:31 and 15-10 16:58 show the same pattern.
  - Because it occurs with both a 6S and a 3S pack, check the connector and wiring as well as the
    packs.
- **Low-battery failsafe could never fire.** On 24-09, `BATT_LOW_VOLT` is 10.6 V on a 6S pack
  (1.77 V per cell) and `BATT_FS_LOW_ACT` is 0, so nothing could warn or land the drone before the
  voltage collapsed. Found by check 14.
- **The parameter is set for the wrong pack.** The 15-10 flights use a 3S pack while
  `MOT_BAT_VOLT_MAX` is still 25.2 V (6S), so thrust compensation is wrong.
- **The quad is over-powered.** It hovers at about 9–16% throttle and keeps climbing at minimum
  throttle, which produces large altitude-hold errors.
- **Motor imbalance** of 113–247 µs.
- **Magnetometer warnings.** "PreArm: Check mag field" appears repeatedly in several sessions.

---

## 6. Future scope (beyond CA3)

**More log sources**
- PX4 `.ulg` logs and ROS 2 bag files (the plan's Tier 3).
- Live monitoring: read the MAVLink stream during flight and warn before landing, not after.

**Smarter analysis**
- **Done already:** comparison with earlier flights, parameter checks (check 14 and pre-flight),
  vibration spectrum with notch suggestions, compass interference, flight map, fix file, ledger.
- **Predictive maintenance:** turn the earlier-flight comparison into forecasts (for example
  "battery resistance rising 4% per flight: replace within ~5 flights"), and track packs by ID.
- **Learned normal behaviour:** train an anomaly detector on healthy flights once there are enough.
- **More checks:** GPS quality, radio link quality (`RADIO_STATUS`), and ESC telemetry (RPM and
  temperature per motor) once the ESCs report it.
- **Live pre-flight check:** read the parameters straight from the drone over MAVLink before
  arming, instead of from a saved `.param` file.
- **Threshold profiles per airframe,** selected automatically from `FRAME_CLASS` or the board ID.
- **Root-cause hints** that link findings, for example "voltage collapse → thrust loss → hard
  landing".
- **Plain-language summaries** of the findings for non-experts.

**Product and deployment**
- A web dashboard with flight history, trend charts and the fleet's status at a glance.
- Fleet support: many drones, per-airframe logbooks and maintenance due dates.
- Docker Compose with n8n and the API together, deployable to a small server; API authentication.
- Slack, Telegram or WhatsApp alerts besides email.
- A regression suite built from SITL fault scenarios, run automatically on every code change.
- Export to maintenance systems, for example CSV or a work order raised when a flight is Critical.

---

## 7. Quick reference

```bash
.venv\Scripts\python -m pytest -q
```

```bash
.venv\Scripts\python -X utf8 -m blackbox "path\to\log.bin"
```

```bash
.venv\Scripts\python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

```bash
npx n8n@2.43.7
```

| Where | What |
|---|---|
| `blackbox/checks.py` | All checks; change logic here |
| `thresholds.yaml` | All limits; change numbers here |
| `api.py` → `_write_reports()` | Plug in the Excel and PowerPoint outputs |
| `n8n/flightlog_workflow.json` | The automation |
| `tests/` | `pytest -q`; synthetic log writers in `dflog.py` and `tlogsim.py` |
