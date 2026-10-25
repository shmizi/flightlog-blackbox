# FlightLog Black Box: Project Guide

The whole project in one place: what it does, how it is built, and how to run it on your own PC,
including email and n8n. Read it top to bottom the first time; after that, use the headings as a
checklist.

---

## 1. What it does

After every test flight someone used to open the ArduPilot log in Mission Planner and judge the
graphs by eye. FlightLog Black Box does that automatically:

> Drop a flight log into a folder. Seconds later the team gets an email with a
> **Healthy / Warning / Critical** verdict, the reasons and the report files.

```
flight-logs\ folder ──► n8n ──► FastAPI (api.py) ──► blackbox.analyze() ──► verdict + reports
  (new log file)        │                                                        │
                        └────────────── email (Gmail) with attachments ◄─────────┘
```

- **n8n** watches the folder, reads each new file, sends it to the API and emails the result.
- **api.py** (FastAPI) receives the file, runs the analysis and returns JSON plus report files.
- **blackbox/** (Python) does the actual work: validation, parsing, 16 health checks, the verdict and recommended actions.

---

## 2. What is in the repository

```
flightlog-blackbox/
├── blackbox/              the analysis core (Python package)
│   ├── validate.py        file type detection (.bin / .tlog / .rlog), SHA-256, duplicates
│   ├── parse.py           .bin (DataFlash) → tables
│   ├── tlog.py            .tlog (telemetry) → the same tables, reduced detail
│   ├── windows.py         armed / airborne / hover / altitude-hold time windows
│   ├── checks.py          the 16 health checks
│   ├── advice.py          recommended actions for the findings
│   ├── exports.py         flight_path.kml and suggested_fixes.param
│   ├── history.py         comparison with the same drone's earlier flights
│   ├── preflight.py       GO / NO-GO from a .param file before flying
│   ├── logbook.py         the Excel flight logbook (Flights, Findings, Exceptions, Pre-flight, Trends)
│   ├── verdict.py         Healthy / Warning / Critical
│   ├── pipeline.py        analyze(log_path): the one entry point
│   ├── report.py          summary text and email
│   └── __main__.py        command line: python -m blackbox LOG
├── api.py                 FastAPI: POST /analyze, GET /files/..., /logbook, /ledger
├── app.py                 Streamlit dashboard
├── thresholds.yaml        every limit the checks use (tune here, not in code)
├── n8n/
│   ├── flightlog_workflow.json         the workflow: folder → API → email (template)
│   ├── flightlog_workflow_gdrive.json  alternative: Google Drive → API → Gmail (needs Google Cloud)
│   └── validate_workflow.js            checks a workflow against n8n's node definitions
├── scripts/
│   ├── setup_n8n.py       one-time setup: folder, email password, import, switch on
│   └── start.ps1          starts the API, n8n and the dashboard in three windows
├── flight-logs/           the watched folder, created by setup (not in git)
├── tests/                 pytest: 88 tests on synthetic logs, plus real-log tests
│   ├── dflog.py           writes synthetic .bin logs
│   ├── tlogsim.py         writes synthetic .tlog / .rlog files
│   └── data/              put real logs here (not in git)
├── output/                analysis results land here (not in git)
├── requirements.txt
└── README.md              short technical reference
```

The flight logs themselves are **not** in the repository (they are large and personal). Get them
from your partner separately and put them anywhere, for example `tests/data/`.

---

## 3. The checks

Each check looks only at the right part of the flight. Time on the bench before arming and the
takeoff and landing moments are ignored. Any Critical makes the flight Critical, any Warning makes
it Warning; "Not available" (the log lacks that data) does not change the verdict.

| # | Check | Measured from | Warning | Critical |
|---|---|---|---|---|
| 1 | Vibration | VIBE X/Y/Z, sustained max | > 30 m/s² | > 60 m/s² |
| 2 | Accelerometer clipping | VIBE.Clip rise while airborne | any | > 100 |
| 3 | Cell voltage under load | BAT.Volt ÷ cells, sustained min | < 3.5 V | < 3.3 V |
| 4 | Voltage sag | VoltR − Volt per cell | > 0.3 V | > 0.5 V |
| 5 | Board supply | POWR.Vcc while armed | < 4.8 V | < 4.5 V |
| 6 | Hover throttle | median CTUN.ThO in hover | > 0.60 | > 0.75 |
| 7 | Altitude hold | \|DAlt − Alt\| 95th percentile, altitude modes | > 0.3 m | > 0.6 m |
| 8 | Attitude tracking | roll/pitch error 95th percentile | > 5° | > 10° |
| 9 | Estimator health | XKF4 SV/SP/SH held > 1 s | > 0.8 | > 1.0 |
| 10 | Motor balance | spread of motor outputs, worst 5 s of hover | > 100 µs | > 200 µs |
| 11 | Hard landing | impact g and descent rate at touchdown | > 3 g / 1 m/s | > 5 g / 2 m/s |
| 12 | Errors and failsafes | ERR records while armed | any | crash, EKF, thrust loss |
| 13 | External navigation gaps | VISP/VISV/VISO gaps | > 0.2 s | > 0.5 s |
| 14 | Configuration | battery parameters vs the pack fitted | any mismatch | – |
| 15 | Vibration spectrum | FFT of IMU batch samples vs the harmonic notch | peak the notch misses | – |
| 16 | Compass interference | field change with current, % of Earth's field | > 30% | > 60% |

All limits live in `thresholds.yaml`. Vibration and EKF limits come from the ArduPilot docs; the
rest are team-set starting values to tune on clean flights.

**File types** (detected from the content, not the extension):

| File | Result |
|---|---|
| `.bin` from the SD card | Full analysis |
| `.tlog` from Mission Planner | Analysed with reduced detail (2–4 Hz, no impact data) |
| `.rlog` | Rejected: no timestamps; use the matching `.tlog` |
| `.param` | Pre-flight GO / NO-GO check of the parameters |
| Anything else | Rejected as "not an ArduPilot log" |

**Exceptions handled:** E1 not a log · E2 truncated or corrupt (analysed, marked Incomplete) ·
E3 missing messages (check shows Not available) · E4 repeat upload (analysed again, flagged with a note) · E5 never armed ·
E7 API offline (n8n retries, then emails). E6 (logbook open in Excel) belongs to the Excel
logbook, which is not built yet.

---

## 4. Install (once per PC)

You need:

- **Git**: https://git-scm.com/download/win
- **Python 3.11** or newer: https://www.python.org/downloads/ (tick "Add python.exe to PATH")
- **Node.js 24** or newer: https://nodejs.org (needed for n8n)

Get the code (replace the URL with the one your partner sends):

```bash
git clone https://github.com/<owner>/flightlog-blackbox.git
```

```bash
cd flightlog-blackbox
```

Later, to get your partner's newest changes:

```bash
git pull
```

Create the Python environment and install the packages (from inside `flightlog-blackbox`):

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

If PowerShell refuses to run scripts later, run this once in that terminal:

```bash
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
```

---

## 5. Check that it works

Run the tests (about 25 seconds; "83 passed, 5 skipped" is correct, the skips need real logs):

```bash
.venv\Scripts\python -m pytest -q
```

Analyse a log from the command line:

```bash
.venv\Scripts\python -X utf8 -m blackbox "C:\path\to\2026-09-28 17-17-40.bin"
```

You get the verdict and one line per check. Add `--json` for the full result.

---

## 6. Set up the automation (once, about 5 minutes)

No Google Cloud project is needed. Logs come in through a folder on your PC, and results go out
through Gmail using an **App Password**.

**6.1 Make a Gmail App Password.** In the Gmail account the emails should come *from*:

1. Go to https://myaccount.google.com/security and turn on **2-Step Verification** if it is off.
2. Go to https://myaccount.google.com/apppasswords, type a name such as `FlightLog`, and click
   **Create**.
3. Google shows a 16-letter password (`abcd efgh ijkl mnop`). Keep the page open; you need it in
   the next step. It is shown only once. Never put it in the repository or a chat.

**6.2 Run the setup script** (in `flightlog-blackbox`):

```bash
.venv\Scripts\python scripts\setup_n8n.py
```

It asks three things: your Gmail address, who should get the emails (press Enter for yourself;
separate several addresses with commas), and the App Password (typing is hidden, spaces are fine).
Then it creates the `flight-logs` folder, stores the password inside n8n (encrypted), imports the
workflow and switches it on. The first run downloads n8n and takes a few minutes. It ends with
"Done. The workflow is on."

Run it again any time to change the addresses or the password.

---

## 7. Start it

```bash
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

Three windows open: **FlightLog API**, **FlightLog n8n** and **FlightLog dashboard** (which opens
http://localhost:8501 in your browser). Wait until the n8n window prints
`Editor is now accessible via: http://localhost:5678`. Keep the windows open while you work;
closing them stops the system.

---

## 8. Use it

Copy a `.bin` or `.tlog` file into the `flight-logs` folder. Within about 10 seconds an email
arrives, for example *"[FlightLog] Critical: 2026-09-28 17-17-40.bin"*, with the summary, the
flight map, the suggested parameter fixes and the Excel logbook attached. The dashboard shows the
same flight with graphs and a map, and its **Fleet logbook** tab shows every flight so far.

- A file that is not a flight log (or an `.rlog`) is rejected and the email says why (E1).
- The same file a second time is analysed again; the email notes "This exact file was already analysed" (E4).
- **Offline test (E7):** close the **FlightLog API** window and drop a log. After 3 tries, 5 s
  apart, the team gets *"[FlightLog] Analysis service offline"*. Run `start.ps1` again afterwards.
- Drop new files only after n8n says it is ready: files already in the folder when n8n starts are
  ignored.

**Seeing the workflow (optional, for screenshots and the demo).** Open http://localhost:5678.
The first time, n8n asks you to create its owner account: email, name and a password of your
choice, which only exists on your PC. Then open **FlightLog Black Box** to see the nodes:

| Node | Job |
|---|---|
| Watch log folder | Fires when a new file appears in `flight-logs` |
| Read log | Reads the file |
| Analyze log | Sends it to the API (one at a time, 3 tries) |
| Attach reports | Turns the API's results into email attachments |
| Email debrief | Emails the verdict to the team |
| Email: service offline | Warns the team if the API did not answer |

**Executions** (left menu) lists every run; click one to see what each node received and sent.

---

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `npx` shows nothing for minutes | The first run is downloading n8n. If it ends with `ECONNRESET`, run it again. |
| No email after dropping a file | Is the n8n window ready? Files that were already in the folder at start-up are ignored, so copy the file in again. Check **Executions** in n8n. |
| Email "Analysis service offline" | The API window is closed or crashed. Run `start.ps1` again. |
| n8n window shows `EBUSY: resource busy or locked` | Windows locked the file while it was being copied. The workflow uses polling to avoid this; if you set it up before 21 Oct, run `setup_n8n.py --keep-credential`, then restart. |
| Email node error "Invalid login" / 535 | Wrong App Password, or 2-Step Verification is off. Make a new App Password and run `setup_n8n.py` again. |
| PowerShell: "running scripts is disabled" | Use the `powershell -ExecutionPolicy Bypass -File ...` form shown above. |

After editing the workflow you can check it against n8n's own node definitions:

```bash
node n8n/validate_workflow.js %LOCALAPPDATA%\npm-cache\_npx\<hash>\node_modules n8n/flightlog_workflow.json
```

(`<hash>` is the folder under `npm-cache\_npx` that contains `node_modules\n8n`.)

**Why `127.0.0.1` and not `localhost`?** Node.js 24 resolves `localhost` to the IPv6 address `::1`,
while the API listens on IPv4 `127.0.0.1`. With `localhost`, n8n cannot reach the API.

---

## 10. How this was tested

The whole chain was run on a separate n8n instance, with a local test mail server standing in for
Gmail (nothing was sent over the internet):

| Dropped into the folder | Email received |
|---|---|
| `.bin` (28-09 flight) | "Critical", summary.txt + result.json, same findings as the command line |
| `.tlog` (15-10 flight) | "Critical", marked telemetry, with the flight date |
| `.rlog` | "Rejected" (E1) with the reason |
| `.bin` with the API stopped | "Analysis service offline" after 3 retries (E7) |

Three files dropped at once produced three emails in about 5 seconds.

---

## 11. Optional: Google Drive instead of a local folder

`n8n/flightlog_workflow_gdrive.json` watches a Google Drive folder and sends through the Gmail API
instead. It needs a Google Cloud project with an OAuth client, and its sign-ins expire every 7 days
in testing mode, so the local-folder version above is recommended. To use it anyway: create a
Google Cloud project, enable the Drive and Gmail APIs, set up the consent screen (External, add
yourself as a test user), and create a *Web application* OAuth client with the redirect URI
`http://localhost:5678/rest/oauth2-credential/callback`. Then import the file in n8n and connect
Google Drive and Gmail credentials in its four Google nodes. In **Analyze log**, use
`http://127.0.0.1:8000/analyze?inline=true`.

---

## 12. What the API returns (for the Excel logbook and PowerPoint debrief)

`POST /analyze` returns JSON like this (shortened):

```json
{
  "flight_id": "2026-09-28_17-17-40_c0190b2e",
  "status": "Analysed",
  "verdict": "Critical",
  "incomplete": false,
  "exception_id": null,
  "reason": "",
  "source": "dataflash",
  "flight_date": null,
  "email": {"subject": "[FlightLog] Critical: ...", "body": "..."},
  "files": [{"name": "summary.txt", "url": "/files/<flight_id>/summary.txt"}, ...],
  "result": {
    "file_name": "...", "sha256": "...", "armed_s": 142.0, "airborne_s": 132.0, "firmware": "...",
    "checks": [{"id": 3, "name": "Cell voltage under load", "status": "Critical", "value": 2.876,
                "warning": 3.5, "critical": 3.3, "unit": "V", "t_flight_s": 31.0, "detail": "..."}, ...],
    "findings": [3, 4, 7, 8, 10],
    "notes": []
  }
}
```

`status` is one of `Analysed`, `Rejected`, `Duplicate`, `No flight`. In Python the same data
comes from `blackbox.analyze(path)` (an `AnalysisResult`; `.to_dict()` gives the JSON above).

**Where the outputs plug in:**

- Write the deck and the logbook in `api.py` → `_write_reports()` and add their file names to
  `REPORT_FILES`; n8n then attaches them automatically.
- The **Flights** sheet maps to `result` (one row per flight, one column per check status).
  **Findings** maps to the checks whose status is Warning or Critical. **Exceptions** maps to
  results whose `status` is not `Analysed`, plus `reason`.
- Once the Flights sheet stores SHA-256 hashes, pass them to `analyze(..., known_hashes=...)` in
  place of `output/processed.json`.
- openpyxl drops existing charts when it re-saves a workbook, so rebuild the Trends charts on
  every save.

---

## 13. Status

**Done**
- Core: validation (E1–E5), `.bin` and `.tlog` parsing, flight windows, 16 checks, verdict, recommended actions, HTML email, flight map, parameter fix file, comparison with earlier flights, pre-flight GO / NO-GO, tamper-evident ledger.
- API with download endpoint, duplicate registry and request lock.
- n8n workflow (watched folder → API → Gmail), set up by one script and tested end to end.
- 88 tests (83 run on synthetic logs); tested on the team's real logs (3 `.bin`, 13 `.tlog`, 12 `.rlog`).

**Still to do**
- PowerPoint debrief (8 slides). Optional: the dashboard and email already cover the same content.
- Tune `thresholds.yaml` on 2–3 clean flights of the airframe in the report and record the values.
- Report, workflow diagrams, screenshots, demo video.

**Findings on the real logs so far** (same BOTWINGF405 GPS quad in every file):
- Heavy voltage sag on every flight; on 24-09 and 28-09 17:31 the voltage collapsed, ArduPilot
  logged "Potential Thrust Loss" and the drone came down. Check the pack, connector and wiring.
- The 15-10 flights use a 3S pack while `MOT_BAT_VOLT_MAX` is still 25.2 V (6S).
- The quad is so overpowered it climbs at minimum throttle, which shows up as altitude-hold errors.
