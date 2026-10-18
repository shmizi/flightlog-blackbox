# FlightLog Black Box

Turns an ArduPilot `.bin` DataFlash log into a Healthy / Warning / Critical verdict with reasons.

**New here?** Read [PROJECT_GUIDE.md](PROJECT_GUIDE.md): the whole project and step-by-step setup,
including Google sign-in and n8n.
This is the Tier 1 core from the CA3 plan: validation (E1–E5), parsing, flight windowing,
13 checks and the verdict. The Excel logbook, PowerPoint debrief, FastAPI, n8n and Streamlit
call `analyze()` and are built on top.

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

How telemetry differs from a `.bin`:

- MAVLink arrives at only 2–4 Hz, so spikes shorter than about a second can be missed.
- There is no IMU data, so hard landing is judged by descent rate alone.
- Errors come from STATUSTEXT messages ("Potential Thrust Loss", "Crash"…) rather than ERR records.
- External navigation (check 13) is not in the telemetry stream.
- On the plus side, a `.tlog` carries wall-clock time, so `flight_date` is set.
- On the 2026-09-28 flight recorded both ways, the `.tlog` and `.bin` gave the same verdict and nearly
  identical values.

## API

```bash
.venv\Scripts\python -m uvicorn api:app --host 0.0.0.0 --port 8000
```

| Endpoint | Does |
|---|---|
| `POST /analyze` | Multipart field `file`. Returns status, verdict, ready-made `email` (subject and body), `files` (download URLs) and the full `result`. Add `?inline=true` to also get the report files base64-encoded in `attachments`. |
| `GET /files/{flight_id}/{name}` | Downloads one report file. |
| `GET /health` | Liveness check. |

- **What is stored:** analysed and bench logs go in `output/<flight_id>/` with `summary.txt` and `result.json`. Rejected files and duplicates are not stored.
- **Duplicates (E4):** `output/processed.json` maps each SHA-256 to its first processing, until the Excel logbook takes over.
- **Concurrency:** requests are serialised by a file lock, so two copies of one log uploaded together count once.
- **Configuration:** set `FLIGHTLOG_OUTPUT_DIR` to move the output folder.

## n8n workflow

Run n8n locally with npx. Nothing is installed globally, and data lives in `%USERPROFILE%\.n8n`:

```bash
npx n8n@2.43.7 import:workflow --input=n8n/flightlog_workflow.json
```

```bash
npx n8n@2.43.7
```

The editor runs at http://localhost:5678. On first open, n8n asks you to create its owner account.

To check the workflow against n8n's own node definitions after editing it:

```bash
node n8n/validate_workflow.js %LOCALAPPDATA%\npm-cache\_npx\<hash>\node_modules n8n/flightlog_workflow.json
```

`<hash>` is the npx cache folder that contains `node_modules\n8n`.

The flow is:

1. **Drive trigger** watches a folder.
2. **Download log** fetches each new file.
3. **Analyze log** POSTs the file to `/analyze?inline=true`, one log at a time. It retries 3 times, 5 s apart.
4. **Attach reports** (Code node) turns the base64 reports into attachments.
5. **Email debrief** (Gmail) sends the email.

If the API still fails after the retries, the error branch sends "Analysis service offline" (E7).

Before activating:

- **Google Drive and Gmail:** pick your credentials in the Drive trigger, Download log and both Email nodes. Replace `REPLACE_WITH_FOLDER_ID` and `REPLACE_WITH_TEAM_EMAIL`.
- **API address:** the URL `http://host.docker.internal:8000` is for n8n in Docker. With `npx n8n`, use `http://localhost:8000`.
- **OAuth expiry:** a Google OAuth app in *Testing* mode issues refresh tokens that expire after 7 days. Reconnect the credentials right before recording the demo.
- **Several logs at once:** files that arrive between polls come in one execution as separate items. Each item gets its own analysis and its own email.

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
