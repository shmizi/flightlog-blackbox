// Builds docs/FlightLog_Black_Box.pptx, the project presentation.
//   node docs/build_deck.js            (needs pptxgenjs: npm install pptxgenjs)
// Figures come from docs/figures (python docs/make_figures.py).

const path = require("path");
const pptxgen = require("pptxgenjs");

const FIG = path.join(__dirname, "figures");
const OUT = path.join(__dirname, "FlightLog_Black_Box.pptx");
const APPLY_THEME = process.env.APPLY_THEME;   // path to the pptx skill's apply_theme.js, if available

// Flight-recorder orange on charcoal: real "black boxes" are orange so they can be found after a crash.
const THEME = {
  name: "FlightLog Black Box",
  headFontFace: "Arial",
  bodyFontFace: "Calibri",
  colors: {
    dk1: "1B1F2A", lt1: "FFFFFF", dk2: "3A4254", lt2: "F2F3F5",
    accent1: "F26B1D", accent2: "1E8E3E", accent3: "E3A008", accent4: "D93025",
    accent5: "5B6577", accent6: "2A78D6", hlink: "2A78D6", folHlink: "6250D6",
  },
};
const HEX = THEME.colors;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";          // 13.333 x 7.5 in
pres.title = "FlightLog Black Box";
pres.author = "Aarushi Rudra, Zahra Modi, Harsh Narain";
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
const C = pres.SchemeColor;

const W = 13.333, M = 0.6;

// ---------------------------------------------------------------- layouts
pres.defineSlideMaster({
  title: "Title dark",
  background: { color: C.text1 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: M, y: 2.35, w: 7.4, h: 1.3, fontSize: 54, bold: true, align: "left",
                                color: C.background1, fontFace: THEME.headFontFace, valign: "bottom", margin: 0 },
                     text: "Title" } },
    { placeholder: { options: { name: "body", type: "body", x: M, y: 3.8, w: 7.0, h: 1.2, fontSize: 20, align: "left",
                                color: "C9CED8", valign: "top", margin: 0 }, text: "Subtitle" } },
  ],
});
pres.defineSlideMaster({
  title: "Content",
  background: { color: C.background1 },
  margin: [0.5, M, 0.6, M],
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: M, y: 0.35, w: W - 2 * M, h: 0.85, fontSize: 32, align: "left",
                                bold: true, color: C.text1, fontFace: THEME.headFontFace, valign: "middle", margin: 0 },
                     text: "Title" } },
    { text: { text: "FlightLog Black Box  ·  CA3 Software Automation Lab",
              options: { x: M, y: 7.0, w: 6, h: 0.3, fontSize: 10, color: HEX.accent5, margin: 0 } } },
  ],
  slideNumber: { x: W - M - 0.6, y: 7.0, w: 0.6, h: 0.3, fontSize: 10, color: HEX.accent5, align: "right" },
});
pres.defineSlideMaster({
  title: "Closing dark",
  background: { color: C.text1 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: M, y: 0.55, w: W - 2 * M, h: 0.9, fontSize: 36, align: "left",
                                bold: true, color: C.background1, fontFace: THEME.headFontFace, margin: 0 },
                     text: "Title" } },
  ],
});

// ---------------------------------------------------------------- helpers
let n = 0;
const name = (s) => `${s}-${++n}`;

function content(title, section) {
  const s = pres.addSlide({ masterName: "Content", sectionTitle: section });
  s.addText(title, { placeholder: "title" });
  return s;
}

function img(s, file, x, y, w, h, alt) {
  s.addImage({ path: path.join(FIG, file), x, y, w, h, altText: alt, objectName: name("figure") });
}

function card(s, x, y, w, h, fill = C.background2) {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, fill: { color: fill }, line: { type: "none" },
                                             rectRadius: 0.08, objectName: name("card") });
}

function badge(s, x, y, label, fill = C.accent1, d = 0.46) {
  s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: fill }, line: { type: "none" },
                                objectName: name("badge") });
  s.addText(String(label), { x, y, w: d, h: d, fontSize: d > 0.5 ? 18 : 14, bold: true, color: C.background1,
                             align: "center", valign: "middle", margin: 0, isTextBox: true, objectName: name("badge-text") });
}

function text(s, t, opts) {
  s.addText(t, { isTextBox: true, margin: 0, color: C.text1, fontSize: 16, valign: "top", objectName: name("text"), ...opts });
}

function stat(s, x, y, w, big, label, colour = C.accent1) {
  text(s, big, { x, y, w, h: 0.75, fontSize: 40, bold: true, color: colour, fontFace: THEME.headFontFace, valign: "bottom" });
  text(s, label, { x, y: y + 0.8, w, h: 0.75, fontSize: 14, color: C.text2 });
}

function caption(s, t, x, y, w) {
  text(s, t, { x, y, w, h: 0.35, fontSize: 12, color: HEX.accent5, italic: true });
}

const tableOpts = (x, y, w, colW) => ({
  x, y, w, colW, fontSize: 12, fontFace: THEME.bodyFontFace, color: HEX.dk1, valign: "middle",
  border: { type: "solid", pt: 0.75, color: "D9DCE3" }, margin: [3, 6, 3, 6], objectName: name("table"),
});
const head = (cells) => cells.map((t) => ({ text: t, options: { bold: true, color: HEX.lt1, fill: { color: HEX.dk1 } } }));

// ================================================================ 1. title
pres.addSection({ title: "Introduction" });
{
  const s = pres.addSlide({ masterName: "Title dark", sectionTitle: "Introduction" });
  text(s, "CA3  ·  SOFTWARE AUTOMATION LAB  ·  PROJECT 29", { x: M, y: 1.6, w: 7, h: 0.4, fontSize: 14, bold: true,
                                                             color: C.accent1, charSpacing: 2 });
  s.addText("FlightLog Black Box", { placeholder: "title" });
  s.addText("Every ArduPilot flight log checked automatically: a verdict, the reasons and what to fix, "
            + "in the team's inbox within a minute.", { placeholder: "body" });
  text(s, "Aarushi Rudra  ·  Zahra Modi  ·  Harsh Narain", { x: M, y: 5.6, w: 7, h: 0.4, fontSize: 16, color: C.background1 });
  // The three verdicts every log ends in.
  const verdicts = [["Healthy", HEX.accent2, "all available checks passed"],
                    ["Warning", HEX.accent3, "fly, but fix this soon"],
                    ["Critical", HEX.accent4, "ground the drone until fixed"]];
  verdicts.forEach(([v, col, sub], i) => {
    const y = 1.9 + i * 1.3;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 8.75, y, w: 3.95, h: 1.05, fill: { color: col }, line: { type: "none" },
                                                rectRadius: 0.1, objectName: name("verdict") });
    text(s, v, { x: 9.05, y: y + 0.12, w: 3.5, h: 0.5, fontSize: 24, bold: true, color: "FFFFFF", fontFace: THEME.headFontFace });
    text(s, sub, { x: 9.05, y: y + 0.6, w: 3.5, h: 0.35, fontSize: 14, color: "FFFFFF" });
  });
  s.addNotes("FlightLog Black Box takes the flight log our drone writes on every flight and turns it into one of three "
    + "verdicts, with the reasons and the fixes, emailed to the team. Nobody has to open a graph.");
}

// ================================================================ 2. problem
pres.addSection({ title: "Problem and workflow" });
{
  const s = content("Today, someone eyeballs every log by hand", "Problem and workflow");
  img(s, "workflow_existing.png", M, 1.45, 12.1, 12.1 / 4.807, "Existing manual log-review process in five steps");
  const pains = [["Slow", "Each graph opened and read by hand, after every flight"],
                 ["Inconsistent", "No fixed limits: the verdict depends on who looks"],
                 ["No record", "Nothing is written down per flight, so flights can't be compared"],
                 ["Trends missed", "A loosening prop or an ageing battery builds up unseen"]];
  const cw = (W - 2 * M - 3 * 0.3) / 4;
  pains.forEach(([h, d], i) => {
    const x = M + i * (cw + 0.3);
    card(s, x, 4.45, cw, 2.15);
    badge(s, x + 0.25, 4.7, i + 1);
    text(s, h, { x: x + 0.85, y: 4.72, w: cw - 1.0, h: 0.45, fontSize: 18, bold: true, valign: "middle" });
    text(s, d, { x: x + 0.25, y: 5.35, w: cw - 0.5, h: 1.1, fontSize: 14, color: C.text2 });
  });
  s.addNotes("This is the existing process. After landing, someone copies the log, opens it in Mission Planner or UAV "
    + "Log Viewer, picks graphs from memory and decides by eye. There are no fixed limits, nothing is recorded, and "
    + "slow problems such as an ageing battery are only noticed when something goes wrong.");
}

// ================================================================ 3. proposed workflow
{
  const s = content("The automation: drop a log, get a verdict", "Problem and workflow");
  img(s, "workflow_proposed.png", M, 1.5, 8.8, 8.8 / 2.318, "Proposed automated workflow");
  stat(s, 9.95, 1.55, 2.8, "< 1 s", "to analyse a log (measured on our real .bin and .tlog files)");
  stat(s, 9.95, 3.35, 2.8, "16", "health checks on every flight, with limits from one YAML file");
  stat(s, 9.95, 5.15, 2.8, "0", "graphs opened by hand; a person steps in only when a flight is flagged");
  s.addNotes("The new process: drop the log in a folder, or upload it in the dashboard. n8n notices the file and posts "
    + "it to our FastAPI service, which runs one Python function, analyze. Out come the email, the Excel logbook, a map "
    + "and suggested parameter fixes, and the dashboard shows the same result.");
}

// ================================================================ 4. architecture
pres.addSection({ title: "How it works" });
{
  const s = content("One analyze() function, two ways to call it", "How it works");
  const layers = [
    ["Trigger and delivery", "n8n 2.43 (self-hosted, npx): watches the folder, posts the file, emails the team over Gmail SMTP"],
    ["Web front end", "Streamlit dashboard: upload a log, see graphs and map, browse the fleet logbook"],
    ["API", "FastAPI + uvicorn: POST /analyze, GET /files, /logbook, /ledger"],
    ["Core", "Python, pymavlink, pandas, numpy: validate, parse, flight windows, 16 checks, verdict, actions"],
    ["Outputs", "openpyxl Excel logbook, HTML email, KML flight map, Mission Planner .param fixes"],
    ["Config and tests", "thresholds.yaml holds every limit; pytest runs 88 tests on synthetic and real logs"],
  ];
  layers.forEach(([l, d], i) => {
    const y = 1.5 + i * 0.86;
    const core = l === "Core";
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: M, y, w: 2.7, h: 0.7, fill: { color: core ? C.accent1 : C.text1 },
                                                line: { type: "none" }, rectRadius: 0.08, objectName: name("layer") });
    text(s, l, { x: M + 0.15, y, w: 2.4, h: 0.7, fontSize: 15, bold: true, color: C.background1, valign: "middle" });
    card(s, M + 2.85, y, 5.95, 0.7);
    text(s, d, { x: M + 3.05, y, w: 5.6, h: 0.7, fontSize: 14, valign: "middle" });
  });
  card(s, 9.85, 1.5, 2.88, 4.4, C.background2);
  text(s, "Why one function?", { x: 10.1, y: 1.75, w: 2.4, h: 0.5, fontSize: 18, bold: true });
  text(s, [
    { text: "n8n and the dashboard both call analyze(log), so a folder drop and an upload always give the same "
            + "verdict.", options: { breakLine: true } },
    { text: " ", options: { breakLine: true, fontSize: 8 } },
    { text: "No UiPath: parsing binary logs and running numerical checks needs Python libraries; n8n does the "
            + "orchestration an RPA bot would do." },
  ], { x: 10.1, y: 2.35, w: 2.4, h: 4.0, fontSize: 14, color: C.text2 });
  s.addNotes("The stack in layers. The core is plain Python. FastAPI wraps it as a web service. n8n and Streamlit are "
    + "two ways of calling it. All limits live in thresholds.yaml, so tuning needs no code change.");
}

// ================================================================ 5. inside analyze()
{
  const s = content("Inside analyze(): validate, parse, window, check", "How it works");
  const steps = [["Validate", "File type from its bytes, not its name (E1)"],
                 ["Parse", ".bin via pymavlink; .tlog mapped to the same tables"],
                 ["Windows", "Armed, airborne, steady hover, takeoff trimmed"],
                 ["16 checks", "Each runs only in its window, per sensor"],
                 ["Verdict", "Any Critical → Critical, any Warning → Warning"],
                 ["Actions", "Linked causes, fixes, map, logbook entry"]];
  const sw = (W - 2 * M - 5 * 0.2) / 6;
  steps.forEach(([h, d], i) => {
    const x = M + i * (sw + 0.2);
    card(s, x, 1.45, sw, 1.75);
    badge(s, x + 0.18, 1.62, i + 1, C.accent1, 0.42);
    text(s, h, { x: x + 0.7, y: 1.62, w: sw - 0.8, h: 0.42, fontSize: 16, bold: true, valign: "middle" });
    text(s, d, { x: x + 0.18, y: 2.18, w: sw - 0.3, h: 0.95, fontSize: 13, color: C.text2 });
  });
  img(s, "windows.png", 1.75, 3.45, 9.8, 9.8 / 2.753, "Flight windowing on the 28-09 flight");
  s.addNotes("Validation looks at the bytes, so a renamed photo is rejected and a .bin saved without its extension "
    + "still works. The key idea is flight windowing: bench time before arming and the takeoff and landing "
    + "transients would otherwise cause false alarms, so each check only looks at its part of the flight.");
}

// ================================================================ 6. 16 checks
{
  const s = content("16 checks, every limit in one YAML file", "How it works");
  const rows = [
    ["1", "Vibration", "> 30 / > 60 m/s²"], ["2", "Accelerometer clipping", "> 0 / > 100"],
    ["3", "Cell voltage under load", "< 3.5 / < 3.3 V"], ["4", "Voltage sag", "> 0.3 / > 0.5 V"],
    ["5", "Board supply", "< 4.8 / < 4.5 V"], ["6", "Hover throttle", "> 0.60 / > 0.75"],
    ["7", "Altitude hold (p95 error)", "> 0.3 / > 0.6 m"], ["8", "Attitude tracking (p95)", "> 5 / > 10°"],
    ["9", "Estimator (EKF ratio, 1 s)", "> 0.8 / > 1.0"], ["10", "Motor balance", "> 100 / > 200 µs"],
    ["11", "Hard landing", "> 3 g, 1 m/s / > 5 g, 2 m/s"], ["12", "Errors and failsafes", "any / crash, EKF, thrust loss"],
    ["13", "External navigation gaps", "> 0.2 / > 0.5 s"], ["14", "Configuration (battery params)", "mismatch → Warning"],
    ["15", "Vibration spectrum (FFT)", "peak outside notch"], ["16", "Compass interference", "> 30 / > 60 %"],
  ];
  const mk = (part) => [head(["#", "Check", "Warning / Critical"]), ...part.map(([i, c, t]) => {
    const extra = Number(i) >= 13;
    return [{ text: i, options: { bold: true, color: extra ? HEX.accent1 : HEX.dk1, align: "center" } },
            { text: c, options: { fill: { color: extra ? "FDEEE4" : "FFFFFF" } } }, { text: t }];
  })];
  s.addTable(mk(rows.slice(0, 8)), { ...tableOpts(M, 1.45, 5.95, [0.5, 2.95, 2.5]), rowH: 0.5 });
  s.addTable(mk(rows.slice(8)), { ...tableOpts(M + 6.2, 1.45, 5.95, [0.5, 2.95, 2.5]), rowH: 0.5 });
  caption(s, "Shaded: checks 13 to 16 were added beyond the project plan's 12. A check whose data is missing shows "
             + "\"Not available\" and names the logging to switch on (E3).", M, 6.2, 12.1);
  s.addNotes("Vibration and EKF limits come from the ArduPilot documentation; the rest are team-set starting values in "
    + "thresholds.yaml. We added four checks beyond the plan: external navigation, configuration, vibration "
    + "spectrum and compass interference.");
}

// ================================================================ 7. 24-09 crash
pres.addSection({ title: "Results" });
{
  const s = content("24-09: the battery collapsed, then thrust was lost", "Results");
  img(s, "crash_battery.png", M, 1.4, 8.4, 8.4 / 1.719, "Cell voltage and current for both 24-09 flights");
  card(s, 9.35, 1.45, 3.38, 5.25);
  stat(s, 9.6, 1.55, 3.0, "1.18 V", "lowest cell voltage under load (limit 3.3 V)", C.accent4);
  stat(s, 9.6, 3.2, 3.0, "1.77 V", "per cell: where the low-battery failsafe was set, too low to ever fire", C.accent4);
  stat(s, 9.6, 4.85, 3.0, "2×", "\"potential thrust loss\" errors, one in each flight", C.accent4);
  s.addNotes("This is a real log from 24 September. In both flights the pack voltage fell from 3.6 to about 1.2 volts per "
    + "cell under 15 to 18 amps, the motors went to full output and the flight controller logged potential thrust "
    + "loss. The low-battery failsafe was set at 10.6 V on a 6S pack, 1.77 V per cell, so it could never have "
    + "triggered. The verdict is Critical, and the tool links cell voltage, sag, thrust loss and the hard landing into one cause: the battery.");
}

// ================================================================ 8. outputs
{
  const s = content("The email says what happened and what to do", "Results");
  img(s, "email.png", M, 1.4, 5.3 * 0.795, 5.3, "The email the team receives for the 24-09 flight");
  const x0 = 5.3;
  text(s, "Attached to every email", { x: x0, y: 1.45, w: 7.4, h: 0.45, fontSize: 20, bold: true });
  const files = [["summary.txt", "the same report as plain text"],
                 ["suggested_fixes.param", "corrected parameters to review and load in Mission Planner"],
                 ["flight_path.kml", "the flight in Google Earth, each finding pinned where it happened"],
                 ["flight_logbook.xlsx", "the team's logbook of every flight so far"]];
  files.forEach(([f, d], i) => {
    const y = 2.05 + i * 0.68;
    badge(s, x0, y + 0.05, i + 1, C.accent1, 0.4);
    text(s, [{ text: f + "  ", options: { bold: true } }, { text: d, options: { color: HEX.dk2 } }],
         { x: x0 + 0.6, y, w: 6.8, h: 0.55, fontSize: 15, valign: "middle" });
  });
  card(s, x0, 4.95, 7.43, 1.75);
  text(s, "Pre-flight GO / NO-GO", { x: x0 + 0.3, y: 5.12, w: 6.8, h: 0.4, fontSize: 18, bold: true });
  text(s, "Drop the drone's .param file before flying. Run on the 24-09 settings, it says NO-GO: the battery "
          + "failsafe can never fire. That crash would have been caught on the ground.",
       { x: x0 + 0.3, y: 5.58, w: 6.85, h: 1.0, fontSize: 14, color: C.text2 });
  s.addNotes("The email starts with the verdict, then the findings with measured value, limit and time, then recommended "
    + "actions. Actions are rules: linked causes first, so the team fixes the battery instead of three symptoms. "
    + "The pre-flight check runs the same battery rules on a parameter file before the drone flies.");
}

// ================================================================ 9. causes
{
  const s = content("Checks 15 and 16 look for causes, not symptoms", "Results");
  img(s, "spectrum.png", M, 1.45, 6.6, 6.6 / 2.455, "Gyro vibration spectrum against the notch filter");
  img(s, "compass.png", 7.55, 1.45, 5.18, 5.18 / 1.739, "Compass field strength against battery current");
  card(s, M, 4.55, 6.6, 2.1);
  text(s, "Vibration spectrum", { x: M + 0.3, y: 4.72, w: 6.0, h: 0.4, fontSize: 18, bold: true });
  text(s, "An FFT of the IMU's raw samples puts the vibration peak at 151 Hz, inside the notch's 160 Hz harmonic, "
          + "so it passes. The report still warns that throttle tracking is off (INS_HNTCH_REF = 0).",
       { x: M + 0.3, y: 5.17, w: 6.0, h: 1.4, fontSize: 14, color: C.text2 });
  card(s, 7.55, 4.55, 5.18, 2.1);
  text(s, "Compass interference", { x: 7.85, y: 4.72, w: 4.6, h: 0.4, fontSize: 18, bold: true });
  text(s, "The field grows 10 mGauss per amp: 36% of the Earth's field at full current, above the 30% limit. "
          + "Fix: CompassMot calibration, twisted leads.",
       { x: 7.85, y: 5.17, w: 4.6, h: 1.4, fontSize: 14, color: C.text2 });
  s.addNotes("Two checks go beyond pass or fail. The spectrum check runs a Fourier transform on the IMU's batch samples "
    + "and compares the peak with the harmonic notch filter; here the notch covers it, so it passes. The compass check fits field strength against current: "
    + "the Earth's field doesn't change with current, so any slope is interference from the power wiring.");
}

// ================================================================ 10. all real flights
{
  const s = content("Every real flight came back Critical or Warning", "Results");
  img(s, "results_matrix.png", 0.9, 1.35, 11.5, 11.5 / 2.808, "Status of all 16 checks on the six real flights");
  const stats = [["28", "real files tested (.bin, .tlog, .rlog)"], ["6", "flight logs analysed: 5 Critical, 1 Warning"],
                 ["9", "ground sessions classed No flight (E5)"], ["12", ".rlog files rejected, with what to upload instead"]];
  const cw = (W - 2 * M - 3 * 0.3) / 4;
  stats.forEach(([b, l], i) => stat(s, M + i * (cw + 0.3), 5.25, cw, b, l));
  s.addNotes("We ran every file the team had. The six flight logs cover five flights: 28-09 17:17 was recorded both as a .bin "
    + "and a .tlog. All have real problems, mainly the battery: cell voltage and sag are Critical in five of six. "
    + "Configuration is flagged on all of them because the low-battery failsafe is set far too low for the pack. Telemetry logs show fewer checks because they carry less data.");
}

// ================================================================ 11. dashboard
pres.addSection({ title: "Interfaces" });
{
  const s = content("Dashboard: analyse one flight", "Interfaces");
  img(s, "dashboard_analyse.png", M, 1.4, 5.25 * 1.226, 5.25, "Dashboard: analysing the 24-09 log");
  img(s, "dashboard_charts.png", 7.35, 1.4, 5.38, 5.38 / 1.527, "Dashboard: battery, vibration, altitude and motor graphs");
  text(s, [
    { text: "Upload a .bin, .tlog or .param, or pick a file from the watched folder.", options: { breakLine: true } },
    { text: " ", options: { breakLine: true, fontSize: 6 } },
    { text: "Uploads are saved into the watched folder, so n8n logs and emails them like any dropped file." },
  ], { x: 7.35, y: 5.15, w: 5.38, h: 1.5, fontSize: 14, color: C.text2 });
  s.addNotes("The first tab analyses one log. Here it is the 24-09 crash: the verdict banner, the findings with "
    + "value, limit and time, the actions, the comparison with this drone's earlier flights, and the raw graphs.");
}

{
  const s = content("Dashboard: the whole fleet at a glance", "Interfaces");
  img(s, "dashboard_fleet_top.png", M, 1.4, 5.25 * 1.483, 5.25, "Dashboard: fleet logbook with trends");
  const items = [["Verdict counts", "how many flights were Critical, Warning or Healthy"],
                 ["Ledger check", "recomputes the hash chain; any edited entry shows as broken"],
                 ["Trends", "cell voltage, vibration, battery resistance and motor imbalance across flights"],
                 ["Exceptions and pre-flight", "every rejected file and every GO / NO-GO check"]];
  items.forEach(([h, d], i) => {
    const y = 1.45 + i * 1.3;
    badge(s, 8.75, y + 0.02, i + 1, C.accent1, 0.42);
    text(s, h, { x: 9.35, y, w: 3.4, h: 0.45, fontSize: 17, bold: true, valign: "middle" });
    text(s, d, { x: 9.35, y: y + 0.48, w: 3.4, h: 0.75, fontSize: 14, color: C.text2 });
  });
  s.addNotes("The second tab is the fleet view, read from the same logbook as the Excel file. The ledger check "
    + "recomputes the SHA-256 chain over all processed files, so quietly editing a past verdict is detected.");
}

// ================================================================ 12. logbook
{
  const s = content("The Excel logbook records every flight", "Interfaces");
  img(s, "logbook_flights.png", M, 1.4, 12.1, 12.1 / 3.713, "Flights sheet of the Excel logbook");
  const sheets = [["Flights", "one row per flight, one coloured column per check"],
                  ["Findings", "every Warning and Critical, with value, limit and time"],
                  ["Exceptions", "rejected, bench, incomplete and repeat uploads"],
                  ["Pre-flight", "GO / NO-GO results of .param files"],
                  ["Trends", "key numbers per flight, with four line charts"]];
  const cw = (W - 2 * M - 4 * 0.25) / 5;
  sheets.forEach(([h, d], i) => {
    const x = M + i * (cw + 0.25);
    card(s, x, 4.95, cw, 1.75);
    text(s, h, { x: x + 0.2, y: 5.1, w: cw - 0.4, h: 0.4, fontSize: 17, bold: true, color: C.accent1 });
    text(s, d, { x: x + 0.2, y: 5.55, w: cw - 0.4, h: 1.1, fontSize: 13, color: C.text2 });
  });
  s.addNotes("The workbook is rebuilt from a JSON-lines record after every file, because openpyxl drops charts when it "
    + "edits a workbook. If someone has it open in Excel, the save is tried three times, 10 seconds apart, and then written to a "
    + "pending copy, which is exception E6.");
}

// ================================================================ 13. n8n
{
  const s = content("n8n runs it hands-free", "Interfaces");
  img(s, "n8n_workflow.png", M, 1.4, 12.1, 12.1 / 3.965, "n8n workflow nodes");
  const cards = [["Trigger", "Local folder watch with polling, so a file still being copied by Windows Explorer is not "
                             + "read half-written."],
                 ["Resilience", "The HTTP node tries 3 times, 5 s apart. If the API is still down, the error branch "
                                + "emails \"service offline\" (E7)."],
                 ["Setup", "One script imports the workflow and stores the Gmail App Password inside n8n, encrypted. "
                           + "It is never written to the repository."]];
  const cw = (W - 2 * M - 2 * 0.3) / 3;
  cards.forEach(([h, d], i) => {
    const x = M + i * (cw + 0.3);
    card(s, x, 4.75, cw, 1.95);
    text(s, h, { x: x + 0.3, y: 4.92, w: cw - 0.6, h: 0.4, fontSize: 18, bold: true });
    text(s, d, { x: x + 0.3, y: 5.37, w: cw - 0.6, h: 1.25, fontSize: 14, color: C.text2 });
  });
  s.addNotes("We first planned a Google Drive trigger, but Google OAuth setup was heavy for a lab project, so the "
    + "workflow watches a local folder and sends mail over Gmail SMTP with an App Password. Each file runs as its own "
    + "execution, and the API's file lock processes them one at a time.");
}

// ================================================================ 14. exceptions
pres.addSection({ title: "Testing" });
{
  const s = content("Seven exception cases, all handled", "Testing");
  const rows = [
    ["E1", "Not a flight log (renamed photo)", "Content check of the first bytes", "Rejected, email explains why"],
    ["E2", "Corrupt or truncated log", "Parser stops early, log ends armed", "Analysed and marked Incomplete; under 30 s rejected"],
    ["E3", "Message types missing", "Needed table is empty", "Check shows Not available + logging to enable"],
    ["E4", "Same file uploaded again", "SHA-256 in the hash-chained ledger", "Analysed again with a note, counted once"],
    ["E5", "Bench log, never armed", "No arming event", "Classed No flight, no verdict"],
    ["E6", "Logbook open in Excel", "PermissionError on save", "3 tries, then flight_logbook_pending.xlsx"],
    ["E7", "Analysis service down", "n8n HTTP request fails", "3 tries, then \"service offline\" email"],
  ];
  s.addTable([head(["", "Scenario", "Detected by", "What happens"]),
              ...rows.map(([id, a, b, c]) => [{ text: id, options: { bold: true, color: HEX.accent1, align: "center" } },
                                              { text: a }, { text: b }, { text: c }])],
             { ...tableOpts(M, 1.45, 12.1, [0.7, 3.6, 3.6, 4.2]), fontSize: 14, rowH: 0.62 });
  s.addNotes("The brief asks for two exception cases; we handle seven, and every one ends up in the logbook's "
    + "Exceptions sheet. Duplicates were first skipped, but the team only had a few logs, so a repeat is now analysed "
    + "again with a note and still counted once.");
}

// ================================================================ 15. testing
{
  const s = content("88 automated tests, plus every real log", "Testing");
  const rows = [
    ["T1", "Clean flight", "Healthy"], ["T2", "High vibration", "Warning / Critical on 1–2"],
    ["T3", "Low battery and sag", "Critical on 3–4"], ["T4", "Motor failure, crash", "Critical on 10–12"],
    ["T5", "Truncated log", "Incomplete (E2)"], ["T6", "Not a log", "Rejected (E1)"],
    ["T7", "Duplicate upload", "Note, counted once (E4)"], ["T8", "Bench log", "No flight (E5)"],
    ["T9", "Logbook open in Excel", "Pending copy (E6)"], ["T10", "Service down", "Offline email (E7)"],
  ];
  s.addTable([head(["", "Scenario", "Expected and observed"]),
              ...rows.map(([id, a, b]) => [{ text: id, options: { bold: true, align: "center" } }, { text: a },
                                           { text: "✓  " + b, options: { color: HEX.accent2 } }])],
             { ...tableOpts(M, 1.45, 7.4, [0.75, 3.05, 3.6]), fontSize: 13, rowH: 0.45 });
  stat(s, 8.6, 1.45, 4.1, "83 pass", "on synthetic logs; 5 more run when real logs are placed in tests/data", C.accent2);
  stat(s, 8.6, 3.2, 4.1, "0 drones", "crashed for testing: a synthetic log writer builds each fault scenario");
  text(s, "Also tested: the n8n Code node runs on a real API reply, the dashboard renders, two copies uploaded "
          + "at once are counted once, and an edited ledger entry is detected.",
       { x: 8.6, y: 5.0, w: 4.1, h: 1.6, fontSize: 14, color: C.text2 });
  s.addNotes("Each scenario from the test plan is a pytest case. Instead of crashing a drone or setting up the "
    + "simulator, a small writer produces DataFlash logs with injected faults. The real logs are the end-to-end check.");
}

// ================================================================ 16. limits and future
pres.addSection({ title: "Conclusion" });
{
  const s = content("Limits and what comes next", "Conclusion");
  const col = (x, title, items, colour) => {
    card(s, x, 1.45, 5.9, 3.3);
    text(s, title, { x: x + 0.35, y: 1.62, w: 5.2, h: 0.5, fontSize: 20, bold: true, color: colour });
    text(s, items.map((t, i) => ({ text: t, options: { bullet: true, breakLine: i < items.length - 1 } })),
         { x: x + 0.35, y: 2.2, w: 5.25, h: 2.45, fontSize: 15, color: C.text2, paraSpaceAfter: 6 });
  };
  col(M, "Limits", ["ArduPilot logs only (.bin and .tlog)",
                    "Team-set limits need tuning on 2–3 clean flights of each airframe",
                    "Rules flag symptoms; they link only the causes we wrote rules for",
                    ".tlog arrives at 2–4 Hz, so short spikes can be missed"], C.accent4);
  col(M + 6.2, "Next", ["PowerPoint debrief per flight (planned, not built yet)",
                        "ESC telemetry checks once our logs carry ESC data",
                        "Anomaly detection once there are enough healthy flights",
                        "PX4 .ulg support and limit profiles per airframe"], C.accent2);
  card(s, M, 5.0, 12.1, 1.7);
  text(s, "What the real logs taught us", { x: M + 0.35, y: 5.15, w: 11.4, h: 0.45, fontSize: 18, bold: true });
  text(s, "The plan assumed one drone and clean files. Real logs had a 6S setting on a 3S pack, aborted arms, bench "
          + "time with 0 V batteries and radio failsafes, and Windows file locks. Testing on them early changed "
          + "the design: flight windowing, cell count from the voltage, and content-based file detection.",
       { x: M + 0.35, y: 5.62, w: 11.4, h: 1.0, fontSize: 14, color: C.text2 });
  s.addNotes("Be honest about the limits: it only reads ArduPilot logs, the team-set limits still need tuning, and the "
    + "advice is rule-based. The next steps build on the logbook we now keep.");
}

// ================================================================ 17. closing
{
  const s = pres.addSlide({ masterName: "Closing dark", sectionTitle: "Conclusion" });
  s.addText("What we built", { placeholder: "title" });
  const points = [["Automatic", "Folder or dashboard in, verdict and fixes out, no graphs opened by hand"],
                  ["Explained", "Every finding has a measured value, a limit, a time and an action"],
                  ["Recorded", "Every flight in the logbook and a tamper-evident ledger, comparable over time"]];
  points.forEach(([h, d], i) => {
    const y = 1.85 + i * 1.3;
    badge(s, M, y, i + 1, C.accent1, 0.6);
    text(s, h, { x: M + 0.9, y: y - 0.05, w: 6, h: 0.45, fontSize: 22, bold: true, color: C.background1 });
    text(s, d, { x: M + 0.9, y: y + 0.42, w: 7.4, h: 0.45, fontSize: 16, color: "C9CED8" });
  });
  [["16", "health checks"], ["7", "exception cases"], ["88", "automated tests"]].forEach(([b, l], i) => {
    const y = 1.75 + i * 1.3;
    text(s, b, { x: 9.6, y, w: 1.4, h: 0.9, fontSize: 48, bold: true, color: C.accent1, fontFace: THEME.headFontFace,
                 align: "right", valign: "middle" });
    text(s, l, { x: 11.15, y, w: 1.6, h: 0.9, fontSize: 16, color: "C9CED8", valign: "middle" });
  });
  text(s, "Thank you. Questions?", { x: M, y: 5.95, w: 8, h: 0.7, fontSize: 30, bold: true, color: C.accent1,
                                     fontFace: THEME.headFontFace });
  text(s, "github.com/shmizi/flightlog-blackbox", { x: 8.6, y: 6.15, w: 4.13, h: 0.4, fontSize: 14, color: "C9CED8",
                                                    align: "right" });
  s.addNotes("Three things to remember: it is automatic, every finding is explained with a value, a limit and an action, "
    + "and every flight is recorded so problems that build up across flights become visible.");
}

(async () => {
  await pres.writeFile({ fileName: OUT });
  if (APPLY_THEME) {
    const { applyTheme } = require(APPLY_THEME);
    await applyTheme(OUT, THEME);
  }
  console.log("wrote", OUT);
})();
