# FlightLog Black Box

Turns an ArduPilot `.bin` DataFlash log into a Healthy / Warning / Critical verdict with reasons.

**New here?** Read [PROJECT_GUIDE.md](PROJECT_GUIDE.md): the whole project and step-by-step setup,
including email and n8n. [HANDOFF.md](HANDOFF.md) covers what is done, what is next and the
future scope.
This is the Tier 1 core from the CA3 plan: validation (E1–E5), parsing, flight windowing,
16 checks, the verdict and recommended actions, with the API, n8n automation, Excel logbook and
Streamlit dashboard built on top. (The PowerPoint debrief is not built.)

## Setup

```bash
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## Use

```bash
.venv\Scripts\python -X utf8 -m blackbox path\to\00000042.BIN
.venv\Scripts\python -m blackbox path\to\00000042.BIN --json
```

```python
from blackbox import analyze
res = analyze("00000042.BIN", known_hashes={"<sha256>": "2026-10-18"})
res.status      # Analysed / Rejected / Duplicate / No flight
res.verdict     # Healthy / Warning / Critical (Analysed only)
res.incomplete  # E2: analysed, but cut short or corrupt
res.findings    # Warning and Critical checks, Critical first
res.to_dict()   # JSON-ready, for FastAPI and the logbook
```

`known_hashes` is the SHA-256 → date map from the logbook's Flights sheet (E4).

## Log types

The file type is detected from its content, not its extension.

| File | Source | Result |
|---|---|---|
| `.bin` | DataFlash log from the SD card | Full analysis |
| `.tlog` | Ground-station telemetry (Mission Planner) | Analysed with reduced detail (`source: "telemetry"`) |
| `.rlog` | Raw radio-link capture, no timestamps | Rejected (E1): upload the matching `.tlog` or `.bin` |
| `.param` | Parameter list saved from Mission Planner / QGroundControl | Pre-flight GO / NO-GO |

How telemetry differs from a `.bin`:

- MAVLink arrives at only 2–4 Hz, so spikes shorter than about a second can be missed.
- There is no IMU data, so hard landing is judged by descent rate alone.
- Errors come from STATUSTEXT messages ("Potential Thrust Loss", "Crash"…) rather than ERR records.
- External navigation (check 13) is not in the telemetry stream.
- `.bin` logs get `flight_date` from GPS time; `.tlog` files from the ground station's clock.
- On the 2026-09-28 flight recorded both ways, the `.tlog` and `.bin` gave the same verdict and nearly
  identical values.

## What each email contains

- **Verdict banner, findings table and recommended actions** (HTML, with a plain-text copy).
- **Earlier flights:** the same drone is recognised by its board's CPU serial number, and the email
  compares vibration, lowest cell voltage, sag, battery resistance, hover throttle, motor imbalance,
  altitude error and compass interference with its last 5 flights.
- **`flight_path.kml`:** the flight on a map (Google Earth), with each finding pinned where it happened.
  Only when the log has GPS.
- **`suggested_fixes.param`:** corrected parameter values for the findings (battery failsafe, notch filter),
  in Mission Planner's `NAME,VALUE` format with the reason above each line. Review before loading.
- `result.json` is stored and downloadable for software (logbook, debrief, Streamlit) but not emailed.

**Pre-flight GO / NO-GO:** drop a `.param` file saved from Mission Planner (Config > Full Parameter List >
Save to file) into the folder. It checks the battery failsafe, battery monitor, radio failsafe, pre-arm
checks, logging and notch filter, and emails GO, GO with warnings, or NO-GO with a fix file. The 24-09
crash drone's own parameters give NO-GO.

**Tamper-evident ledger:** each processed log's entry in `output/processed.json` includes a hash of the
previous entry, so editing, deleting or reordering a record breaks the chain. `GET /ledger` checks it.

## Excel logbook and dashboard

**`output/flight_logbook.xlsx`** is rebuilt after every processed file and attached to every email
(also `GET /logbook`). Sheets: **Flights** (one row per flight, one coloured column per check),
**Findings**, **Exceptions** (E1, E2, E4, E5), **Pre-flight** and **Trends** (key numbers per flight
with four charts). It is generated from `output/logbook.jsonl` each time, because openpyxl drops
charts when it edits an existing workbook. **E6:** if the logbook is open in Excel, the save is retried
3 times, 10 s apart, then written to `flight_logbook_pending.xlsx`; the next save includes everything.

**Dashboard** (`app.py`, Streamlit, started by `start.ps1` at http://localhost:8501):

- **Analyse a log:** upload a `.bin`, `.tlog` or `.param` (or pick a file already in the watched
  folder) and see the verdict, findings, recommended actions, the comparison with earlier flights,
  battery/vibration/altitude/motor graphs, the flight path on a map, and downloads. When n8n is running,
  an upload is saved into the watched folder, so it is logged and emailed like any dropped file. If n8n
  is off it goes straight to the API (logged, no email); with neither running it is only analysed.
- **Fleet logbook:** flight counts by verdict, ledger status, all flights, trend charts across flights,
  exceptions and pre-flight checks, and a download of the Excel logbook.

```bash
.venv\Scripts\python -m streamlit run app.py
```

## API

```bash
.venv\Scripts\python -m uvicorn api:app --host 0.0.0.0 --port 8000
```

| Endpoint | Does |
|---|---|
| `POST /analyze` | Multipart field `file`. Returns status, verdict, ready-made `email` (subject and body), `files` (download URLs) and the full `result`. Add `?inline=true` to also get the report files base64-encoded in `attachments`. |
| `GET /files/{flight_id}/{name}` | Downloads one report file. |
| `GET /ledger` | Verifies the tamper-evident chain of processed logs. |
| `GET /health` | Liveness check. |

- **What is stored:** analysed and bench logs go in `output/<flight_id>/` with `summary.txt` and `result.json`. Rejected files and duplicates are not stored.
- **Repeat uploads (E4):** `output/processed.json` maps each SHA-256 to its first processing. The same file
  uploaded again is analysed in full with a note ("already analysed on …"), but gets no second ledger
  entry or history line, so nothing is counted twice.
- **Concurrency:** requests are serialised by a file lock, so two copies of one log uploaded together count once.
- **Configuration:** set `FLIGHTLOG_OUTPUT_DIR` to move the output folder.

## n8n workflow

n8n runs locally through npx: nothing is installed globally, and its data lives in
`%USERPROFILE%\.n8n`. No Google Cloud project is needed. Logs come in through a watched folder and
results go out through Gmail with an App Password.

One-time setup. It asks for your Gmail address, the recipients and the App Password, then
imports and switches on the workflow:

```bash
.venv\Scripts\python scripts\setup_n8n.py
```

Start the API and n8n, each in its own window:

```bash
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

Then drop a `.bin` or `.tlog` into `flight-logs\`. The editor runs at http://localhost:5678; it
asks for an owner account the first time and is optional.

`start.ps1` enables n8n's folder-watch node, which n8n 2.x disables by default
(`NODES_EXCLUDE`; command execution stays disabled). It also restricts n8n's file access to the
watched folder (`N8N_RESTRICT_FILE_ACCESS_TO`). The API URL is `http://127.0.0.1:8000`, not
`localhost`: Node 24 resolves `localhost` to IPv6 `::1`, where uvicorn is not listening.

To check the workflow against n8n's own node definitions after editing it:

```bash
node n8n/validate_workflow.js %LOCALAPPDATA%\npm-cache\_npx\<hash>\node_modules n8n/flightlog_workflow.json
```

`<hash>` is the npx cache folder that contains `node_modules\n8n`.

The flow is:

1. **Watch log folder** fires when a new file appears in `flight-logs\`.
2. **Read log** reads the file.
3. **Analyze log** POSTs the file to `/analyze?inline=true`, one log at a time. It retries 3 times, 5 s apart.
4. **Attach reports** (Code node) turns the base64 reports into attachments.
5. **Email debrief** (SMTP via Gmail) sends the email.

If the API still fails after the retries, the error branch sends "Analysis service offline" (E7).

`n8n/flightlog_workflow.json` is a template: `setup_n8n.py` fills in `__WATCH_FOLDER__`,
`__API_URL__`, `__FROM_EMAIL__` and `__TO_EMAIL__`. The older Google Drive + Gmail OAuth version is
kept as `n8n/flightlog_workflow_gdrive.json` (see PROJECT_GUIDE §11).

**Tested end to end** on a separate n8n instance with a local test mail server:
- a `.bin` dropped into the folder gave "Critical" with both attachments
- a `.tlog` gave "Critical", marked as telemetry
- an `.rlog` gave "Rejected"
- with the API stopped, the offline email was sent

Three files dropped at once gave three emails in about 5 s.

## Tests

```bash
.venv\Scripts\python -m pytest -q
```

Scenarios T1–T8 run on synthetic DataFlash logs from `tests/dflog.py`, which uses ArduPilot's real
message formats. Put real logs in `tests/data/` as `T1_clean.bin`, `T2_vibration.bin`,
`T3_battery.bin` and `T4_motor_fail.bin`, and the matching real-log tests run too.

## Differences from the plan

- **Flight windows.** Checks look only at armed or airborne time, from EV/ARM events
  (NOT_LANDED, LAND_COMPLETE_MAYBE). Time on the bench (USB power, no RC receiver) and
  takeoff/landing transients are ignored.
- **Per-instance evaluation.** VIBE, IMU, BAT and XKF4 are checked per sensor/core and the worst
  one is reported.
- **Cell count** defaults to `auto`: it reads `MOT_BAT_VOLT_MAX`, else the voltage at arming.
  Pin it in `thresholds.yaml` once you know the pack.
- **Check 5** shows Not available on boards that do not measure Vcc (e.g. BOTWINGF405).
- **Check 4** uses `VoltR - Volt`, ArduPilot's resting-voltage estimate, instead of an undefined
  baseline.
- **Check 6** uses median `ThO` in hover, because ArduPilot caps the learned `ThH` at 0.6875, so a
  0.75 limit on it could never fire.
- **Check 7** runs only in altitude-controlled modes.
- **Check 10** reads motor channels from `SERVOn_FUNCTION` and reports the worst 5 s chunk, so a
  motor that fails mid-flight is not averaged away.
- **Check 11** looks at touchdown, not "5 s before disarm". With `DISARM_DELAY` at its default of
  10 s, those 5 s are on the ground. It combines peak acceleration with descent rate.
- **Check 12** ignores resolved errors (ECode 0) and errors raised before arming.
- **Check 13** is new: the longest gap in the Jetson's VISP/VISV/VISO data. It shows Not available
  when the log has none.
- **Parsing** opens `DFReader_binary` directly. `mavutil.mavlink_connection` picks the reader by file
  extension and would misread an upload saved under a temp name.
- **E2** is detected by "log ends while armed" or skipped corrupt bytes. A cut-off file does not
  raise an error, so a parser exception is not a usable signal.

## Presentation and report

`docs/` holds the presentation (`FlightLog_Black_Box.pptx`), the LaTeX report (`docs/report/report.tex`)
and the figures both use, drawn from the real logs. See `docs/README.md` to rebuild them.
