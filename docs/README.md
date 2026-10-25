# Presentation and report

| File | What it is |
|---|---|
| `FlightLog_Black_Box.pptx` | The presentation (18 slides, speaker notes on every slide) |
| `report/report.tex` | The LaTeX project report; it uses the images in `figures/` |
| `figures/` | Every figure, drawn from the real logs and the running system |
| `make_figures.py` | Redraws the plots and diagrams from the real logs |
| `build_deck.js` | Rebuilds the presentation from `figures/` |

Rebuild:

```bash
.venv\Scripts\python -m pip install matplotlib pillow
.venv\Scripts\python docs\make_figures.py ..
npm install pptxgenjs
node docs\build_deck.js
```

Compile the report with `pdflatex report.tex` (twice, so the table of contents fills in) from `docs/report/`,
or upload `report.tex` and the `figures/` folder to Overleaf.

The dashboard and email screenshots (`dashboard_*.png`, `email.png`) were captured from a test instance fed
with the real logs; retake them the same way if the dashboard changes.
