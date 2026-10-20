# Capital Analytics

A configurable desktop analytics application for the daily stock-market workbook
`Saif-Stock Market Data File-Sample.xlsm`. It imports the workbook into a local
SQLite database, reproduces the spreadsheet's own calculations **cell for cell**,
and puts a real user interface — dashboards, screeners, filters, a safe formula
language and reports — on top of the data.

Everything runs offline. There is no server, no cloud account, no paid service
and **no macro is ever executed**: workbooks are opened read-only and the VBA
project is only ever *inspected* for the audit report.

```
python3 main.py            # launch the desktop application
python3 main.py --check    # headless self-test (build database + import + report)
```

---

## Contents

1. [Why this exists](#why-this-exists)
2. [Install](#install)
3. [Launch](#launch)
4. [First run: import the workbook](#first-run-import-the-workbook)
5. [Screens](#screens)
6. [Adding a formula](#adding-a-formula)
7. [The dashboard builder](#the-dashboard-builder)
8. [Reports and exports](#reports-and-exports)
9. [Original vs corrected mode](#original-vs-corrected-mode)
10. [Command-line tools](#command-line-tools)
11. [Running the tests](#running-the-tests)
12. [Packaging with PyInstaller](#packaging-with-pyinstaller)
13. [Linux notes](#linux-notes)
14. [Project layout](#project-layout)
15. [Database and backups](#database-and-backups)
16. [Performance and scale](#performance-and-scale)
17. [Troubleshooting](#troubleshooting)

---

## Why this exists

The supplied workbook *works*, but it is only as reliable as its formulas — and
several of them are broken. The audit found ten confirmed defects, including a
volume moving average that returns `#VALUE!` for 182 of 186 sessions and a
classifier that reads the volume of the wrong row. Reproducing that faithfully
matters: the business has been reading these numbers for months.

So the application implements **two modes** for every rule:

| Mode | What it does |
|---|---|
| `original` *(default)* | Replays the workbook **exactly**, defects included. Verified against the workbook's own cached results. |
| `corrected` | Applies the documented intent of the rule, and logs every divergence. |

Nothing was silently "fixed". Every discrepancy is catalogued in
[`docs/formula_audit_report.md`](docs/formula_audit_report.md) with its evidence,
its business impact and its resolution, and shown in the in-app **Formula Audit
Log**.

**Parity (mode `original`)** — the application recomputes 9,498 calculated cells
and compares them to the values Excel cached inside the workbook:

| Scope | Compared | Matched | Failed | Verdict |
|---|---:|---:|---:|---|
| `Analysis!M15:W200` + summary block | 2,073 | 2,073 | 0 | **PASS** |
| `Main!B18:J842` (825 symbols) | 7,425 | 7,425 | 0 | **PASS** |
| **Total** | **9,498** | **9,498** | **0** | **100 %** |

Full detail: [`docs/parity_report.md`](docs/parity_report.md).

---

## Install

Requires **Python 3.10+** (developed and verified on 3.10.12). A virtual
environment is recommended.

**Linux / macOS**

```bash
cd capital
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

**Windows (PowerShell)**

```powershell
cd capital
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

The application uses: PySide6 (Qt 6), SQLAlchemy + SQLite, pandas, openpyxl,
XlsxWriter, reportlab, Plotly and — optionally — oletools for reading the
workbook's VBA project. Openpyxl **cannot** evaluate formulas, which is why the
calculation engine is implemented in `services/analytics.py` and verified
against the values the workbook already contains.

---

## Launch

```bash
python3 main.py                      # open the application
python3 main.py --theme light        # force the light theme for this run
python3 main.py --db /tmp/other.db   # use a different database file
python3 main.py -v                   # also log to the terminal
python3 main.py --check              # headless self-test, no window
```

If the database file does not exist it is created and seeded with the schema,
the default settings, the built-in formulas and the default dashboard.

On first launch the database is empty — go to **Import Data** (below) or run
`python3 main.py --check`, which imports the bundled sample workbook and prints a
summary.

---

## First run: import the workbook

Open **Import Data**. Pick a workbook (**Browse…**) — any `.xlsx` / `.xlsm` file;
macros are never run, the file is read as data. Then set the **Sheet**, the
**header row** and how **duplicates** should be handled, and check the four tabs
before pressing **Import**:

* **Preview** — the first rows exactly as they will be read.
* **Column mapping** — the importer guesses the mapping from the headers
  (`Symbol`, `Date`, `OPEN`, `HIGH`, `LOW`, `CLOSE`, `LAST`, `PREVCLOSE`,
  `TOTTRDQTY`, `TOTTRDVAL`, `TOTALTRADES`, `Delivery %`). Every guess is shown
  and editable, and the required columns are flagged if they are missing.
* **Validation issues** — rows are checked before anything is written. A row with
  a missing symbol/date or a non-numeric price is **rejected** and listed with its
  row number and the reason; softer problems (out-of-range price, `HIGH < LOW`, a
  duplicate key, a future date) are **warnings** and do not block.
* **Import history** — every earlier import, with its counts.

Duplicate handling:

* **skip** — keep the value already in the database,
* **update** — overwrite it in place,
* **new batch** — overwrite it and archive the previous value.

When the import finishes you get a report (rows read / new / updated / skipped /
rejected, with the error samples) and the dashboard refreshes. The same data is
available any time in **Historical Data**, and the event is written to the audit
log.

The equivalent from a terminal:

```bash
python3 scripts/import_workbook.py --db data/capital.db \
    --workbook "data/Saif-Stock Market Data File-Sample.xlsm" --sheet Data
python3 scripts/import_workbook.py --help        # every option
```

---

## Screens

| Screen | What it is for |
|---|---|
| **Dashboard** | The component dashboard you build yourself (KPI cards, tables, line/bar/pie charts). Filters on the page narrow every component at once. |
| **Stock Explorer** | One symbol at a time: KPI cards (close, % change, delivery, volume, trades, action), closing-price and quantity charts, the per-session calculated columns (delivery %, average price, the volume moving average, D-Indicator, DV1/DV2) and the lookback summary for 1/3/5/7/10 sessions. |
| **Historical Data** | The imported sessions with filters (symbols, dates), paging and export — calculated columns are always marked. |
| **Formula Lab** | Create, test, version and apply your own calculated columns (see below). |
| **Reports** | Build and export a report (screener, history, lookback, classification, formulas, dashboard snapshot) to Excel, CSV or PDF. |
| **Import Data** | The workbook import wizard described above. |
| **Settings** | Database location, backup/restore, import behaviour, date/number formats, formula management, dashboard layouts, chart preferences, refresh behaviour, theme, logging and export preferences. |
| **Formula Audit Log** | Every rule divergence and every recorded event, filterable by category and severity. |

Every destructive action (replace the database, restore a backup, delete a
formula, delete a dashboard layout, overwrite values on import) asks for
confirmation first, and every one of them is written to the audit log.

---

## Adding a formula

**Formula Lab → New formula.** A formula is a named, versioned calculated
column. Fields shown in the editor:

| Field | Meaning |
|---|---|
| Name / description / category | How you will find it later. |
| Output metric | The column name it produces on every row. |
| Formula | The expression (see the language below). |
| Data type | `float`, `int`, `str` or `bool`. |
| Null policy | `blank` (a missing input makes the row unavailable), `zero` (a missing input counts as 0), `error` (like `blank`, but the count of unusable rows is reported). |
| Display format | How the value is shown and exported. |
| Enabled | Disabled formulas are kept but not applied. |

**Test mode** evaluates the expression and shows the first rows side by side with
their inputs before you save. Every save creates a new version; **History**
lists them, shows the diff and restores any previous version.

### The formula language

A deliberately small, sandboxed language — **no `eval`, no Python, no file or
network access**. Only the fields, constants and functions below exist.

```
Operators   + - * /  % (remainder)  ^ (power)
            =  <>  <  >  <=  >=          (comparison; <> is "not equal")
            AND  OR  NOT                 (NOT binds looser than a comparison)

Fields      SYMBOL  DATE  OPEN  HIGH  LOW  CLOSE  LAST  PREVCLOSE
            TOTTRDQTY  TOTTRDVAL  TOTALTRADES  DELIVERY %
            (headings may be written as in the workbook: "Delivery %",
             "Total Traded Qty", "Prev Close", "Trade Date" …)

Constants   PI  E  TRUE  FALSE

Functions   AVERAGE(x, y, ...)          AVERAGE(FIELD, n)   trailing n sessions
            SUM / MIN / MAX(x, y, ...)  ABS  ROUND(x, digits)  SQRT  POWER
            IF(condition, a[, b])       AND / OR / NOT / COALESCE
            SAFE_DIV(a, b[, fallback])  division that returns the fallback at b = 0
            ROLLING_AVG / ROLLING_SUM / ROLLING_MIN / ROLLING_MAX / ROLLING_STD(FIELD, n)
            DELTA(FIELD, n)   PCT_CHANGE(FIELD, n)   RANK_PCT(x, values...)
```

Things worth knowing:

* Windows look **backwards**. `ROLLING_AVG(CLOSE, 5)` on the 10th session of a
  symbol averages sessions 6–10. Early rows simply use what exists rather than
  padding with zeros.
* A missing value **never** becomes a wrong number: it is skipped by aggregates
  and handled by the null policy.
* Division by zero yields a blank result, never an exception.
* `%` is Excel's `MOD` (remainder), not a percentage sign.
* Text in double quotes is never re-interpreted.

Examples:

```
(LAST - PREVCLOSE) / PREVCLOSE * 100
AVERAGE(CLOSE, 20)
IF(DELIVERY % > 60 AND TOTTRDQTY < ROLLING_AVG(TOTTRDQTY, 25), "HIGH", "")
(OPEN + HIGH + LOW + CLOSE) / 4
```

Seven formulas ship built in (`delivery_band`, `avg_price`, `pct_change`,
`close_ma_25`, `close_ma_5`, `deliverable_qty`, `qty_per_trade`) and can be
inspected, versioned or disabled like any other.

---

## The dashboard builder

**Settings → Dashboard Builder.** Add, edit, remove, hide and reorder
components; create as many named layouts as you want; pick the landing page; and
restore the shipped default at any time. Layouts persist across restarts.

Component kinds: **KPI card**, **table**, **line**, **bar**, **pie**.
Each component picks a metric, an aggregation (`sum`, `avg`, `min`, `max`,
`count`) and a source (`overview`, `prices`, `screener`, `derived`), plus an
optional dimension to group by, a row limit, chart options, default filters, a
display format and KPI thresholds.

Layouts can be exported to and imported from a JSON file, so a dashboard can be
shared, reviewed or version-controlled. A ready-made example lives in
[`docs/sample_dashboard_config.json`](docs/sample_dashboard_config.json) —
import it from the builder to see a volume-focused dashboard. It is regenerated
from the live service and round-trip tested, so it always imports cleanly.

---

## Colour scheme

The dashboard, the screener tables and the legend use the same colour code as
the sample workbook, extracted from its conditional-formatting rules
(`Main!N18:N29`). Defaults live in
[`config/palette_workbook.json`](config/palette_workbook.json) and are
regenerated from the `.xlsm` by:

```bash
python3 scripts/extract_palette.py --workbook "data/Saif-Stock Market Data File-Sample.xlsm"
```

| Group | Label | Fill | Text |
|---|---|---|---|
| D.Ind | V.High / High / Above / Low | `#7030A0` / `#00B050` / `#FFC000` / `#FF0000` | white (black on amber) |
| DV1 | GOLD / SILVER / BRONZE | `#FFC000` / `#AFABAB` / `#BF9000` | black / black / white |
| DV2 | 80% / 60% / 50% / 40% / Low Del | `#7030A0` / `#00B050` / `#92D050` / `#00B0F0` / `#FDD7D5` | white; dark red on Low Del |
| Above / Below | price and MA columns | `#C6EFCE` / `#FFC7CE` | `#006100` / `#9C0006` |
| Chrome | header band, section, accent | `#FFD966`, `#767171`, `#4472C4` | — |

**Settings → Colours** edits every entry with a colour picker, validates the
hex value, and shows a live preview. You can:

* switch between **Workbook colours** and **Classic** (the original theme),
* keep the workbook fills with **auto-contrast text**, or reproduce the
  workbook's white text exactly,
* show or hide the legend above the screener,
* export/import a scheme as JSON, or restore the workbook palette.

Only differences from the defaults are stored, so palette updates reach your
installation unless you deliberately overrode an entry. *Note*: "Low Del"
keeps its dark-red text in both modes, and the workbook's own low-contrast
combinations (white on amber, silver and cyan) become black in auto mode.

The same colours also drive the classification badges, the table cells in
**Stock Explorer** and **Reports**, and the chart colourway.

---

## Reports and exports

**Reports** builds a report for the current filters and exports it to **Excel
(`.xlsx`)**, **CSV** or **PDF**. Every file carries a provenance block: when it
was generated, the filter and import settings that were active, and which
columns are **calculated** — calculated metrics are always labelled (with `*`),
so an exported number can never be mistaken for source data.

```bash
python3 scripts/export_report.py --db data/capital.db \
    --kind screener --format xlsx --out /tmp/screener.xlsx
python3 scripts/export_report.py --help
```

Report kinds: `screener`, `history`, `lookback`, `classification`, `formulas`,
`dashboard`.

---

## Original vs corrected mode

The mode is a setting (**Settings → Formulas → Formula mode**) and can also be
forced per run. It only ever changes *how a rule is evaluated*, never the
imported data.

```bash
python3 scripts/parity_check.py --help
python3 scripts/parity_check.py --db data/capital.db
```

Read [`docs/formula_audit_report.md`](docs/formula_audit_report.md) before
switching to `corrected`: it lists all ten findings (B1–B10), what each one
breaks, and what the corrected behaviour is. `corrected` mode is intended for
once the business has signed off on the fix.

---

## Command-line tools

All under `scripts/`; every one accepts `--help`.

| Script | Purpose |
|---|---|
| `init_db.py` | Create (or re-create) the database and seed it. |
| `inspect_workbook.py` | Structural audit of a workbook: sheets, tables, dimensions, formula inventory. Writes `docs/workbook_inspection.md` and `docs/formula_inventory.csv`. |
| `audit_formulas.py` | Full formula audit: defects, evidence and the mapping document. Writes `docs/formula_audit_report.md` and `docs/formula_mapping.md`. |
| `import_workbook.py` | Import a sheet from the command line (supports `--dry-run`, `--map`, `--mode bulk`). |
| `import_folder.py` | Import every workbook in a folder, in name order, into one database — the multi-file backfill path. |
| `parity_check.py` | Recompute both oracle scopes and compare against the workbook's cached values. Writes `docs/parity_report.md`. Exit code `0` means 100 % parity. |
| `export_report.py` | Build and export a report headlessly. |
| `run_app.py` | Launch the GUI from anywhere in the project. |

---

## Running the tests

```bash
python3 -m pytest                     # everything
python3 -m pytest -m "not slow"      # skip the workbook-parity recomputation
python3 -m pytest -m slow            # only the parity tests
python3 -m pytest -m gui             # only the headless Qt smoke test
python3 -m pytest tests/test_analytics.py -v
```

The suite covers the schema and database layer, the import pipeline, filters,
the formula language and its sandbox, the calculation engine, the dashboard
service, exports and reports, end-to-end edge cases (zero denominators, missing
values, duplicate dates, too little history, empty datasets, a data-less
database) and the command-line tools. The `slow` tests recompute the entire
Analysis block and the 825-symbol Main screener and compare them to the workbook.

The GUI test builds the real main window with Qt's `offscreen` platform, so it
needs no display.

---

## Packaging with PyInstaller

```bash
python3 -m pip install -r requirements.txt      # includes pyinstaller
pyinstaller --noconfirm capital.spec
```

The result is a one-folder bundle:

```
dist/CapitalAnalytics/
├── CapitalAnalytics           # the executable
├── _internal/                 # Qt, Python and the read-only resources
└── ...
```

Run `dist/CapitalAnalytics/CapitalAnalytics`. On first start it creates `data/`,
`logs/` and `data/exports/` **next to the executable** (not inside `_internal/`,
which is read-only and temporary), so a bundled build behaves exactly like a
source checkout — the database, backups, exports and logs all sit beside the
program.

`capital.spec` bundles the default settings, the database schema, the sample
dashboard config and (when present) the sample workbook, and hides nothing you
need to configure by hand. To ship without the sample workbook, delete it from
`data/` before building.

---

## Linux notes

* **Qt/display libraries.** A minimal install may need the usual Qt/X11 runtime
  packages (`libxcb-cursor0`, `libxkbcommon-x11-0`, `libegl1`, `libgl1`,
  `libdbus-1-3`, and `libxcb-*`). On Debian/Ubuntu:

  ```bash
  sudo apt-get install -y libxcb-cursor0 libxkbcommon-x11-0 libegl1 \
      libgl1 libdbus-1-3 libfontconfig1
  ```

* **Headless servers / CI.** Set `QT_QPA_PLATFORM=offscreen` to build widgets
  without a display (the test suite does this for you). Charts then fall back to
  the native Qt painter if QtWebEngine cannot start.

* **Charts.** Line/bar/pie charts are drawn by Plotly inside a `QWebEngineView`.
  The Plotly bundle is written once to `data/web_assets/plotly.min.js` and loaded
  from disk — no network access is required. If QtWebEngine is unavailable the
  application automatically uses a native `QPainter` chart instead, so a chart
  is never simply missing.

* **Vulkan / GLX warnings.** Messages such as `QVulkanInstance` or
  `GLX: No XCB Connection` are harmless in a headless environment.

* **Fonts.** PDF export uses reportlab's built-in fonts and needs no system
  font configuration.

---

## Project layout

```
capital/
├── main.py                   # entry point (GUI and --check self-test)
├── capital.spec              # PyInstaller recipe
├── requirements.txt
├── pytest.ini
├── config/
│   └── default_settings.json # shipped defaults (seeded into the database)
├── database/
│   ├── db.py                 # engine, settings store, backup/restore
│   └── schema.sql            # the schema in plain SQL, for reference
├── models/                   # SQLAlchemy models
├── services/                 # all business logic, headless and testable
│   ├── workbook_reader.py    #   read-only workbook access + VBA inspection
│   ├── excel_import.py       #   column mapping, validation, import pipeline
│   ├── analytics.py          #   the calculation engine (the workbook's rules)
│   ├── safe_parser.py        #   the sandboxed formula language
│   ├── formula_service.py    #   formula CRUD, versioning, application
│   ├── dashboard_service.py  #   dashboard components and layouts
│   ├── reports / export      #   report building and file writing
│   ├── data_service.py       #   cached read layer used by the UI
│   ├── settings_service.py   #   typed settings access
│   ├── parity.py             #   the workbook comparison harness
│   └── audit.py              #   the formula audit
├── ui/                       # PySide6 interface (pages, widgets, theme, charts)
├── scripts/                  # command-line tools
├── tests/                    # pytest suite
├── docs/                     # generated reports and sample config
├── data/                     # database, backups, exports, web assets, workbook
└── logs/                     # rotating application log
```

The UI never contains business logic: every page talks to the services, which is
why the calculation engine and the import pipeline can be tested without a
window.

---

## Database and backups

The database is a single SQLite file (`data/capital.db` by default), created and
seeded on first use, with indexes on `(symbol, date)` and `date` for fast
filtering. **Settings → Database** lets you point the application at a different
file, take a backup, and restore one. A backup is a consistent copy taken with
SQLite's own backup API, so it is safe to take while the application is running.

Restoring a backup and replacing the database both require confirmation and are
recorded in the audit log.

---

## Performance and scale

The application is built for a laptop, not a server, and it stays usable when the
history grows from a few thousand rows to tens of millions of rows spread over
hundreds of daily files.

### Importing

Rows are streamed from the worksheet, validated one at a time and written in
batches, so memory stays flat regardless of file size:

* the workbook is read in read-only mode through
  [`python-calamine`](https://pypi.org/project/python-calamine/) when it is
  installed (a Rust parser) and falls back to `openpyxl` otherwise;
* validation keeps **exact** counters (accepted, rejected, errors, warnings,
  duplicates) while retaining at most 100 000 issue *samples*, so a file with a
  million bad cells cannot exhaust memory;
* inserts use `executemany` with SQLite deciding duplicates
  (`INSERT OR IGNORE` / `ON CONFLICT … DO UPDATE`) instead of a `SELECT` per row.

Two write modes are available in **Import → Write mode** (and `--mode` on the
command line):

| Mode | Guarantee | Use it when |
|---|---|---|
| `strict` (default) | One transaction. A failure leaves the database exactly as it was. | Normal daily imports. |
| `bulk` | Commits every 100 000 rows and records where it stopped, so an interrupted import resumes instead of restarting. | Very large files. |

On the development machine a synthetic 100 000-row sheet imports in about 3
seconds (strict or bulk) — roughly 35 000 rows per second, single-threaded. The
same read took minutes before the streaming rewrite, because the old reader asked
the worksheet for its column count once per row.

Equivalent command line:

```
python3 scripts/import_workbook.py --mode bulk --workbook big.xlsx
```

For a backfill of many daily files, point the folder importer at the directory —
it imports each workbook as its own batch, keeps going when one file fails, and
prints a per-file and total summary:

```
python3 scripts/import_folder.py --folder /data/daily --pattern '*.xlsx'
```

### Querying

Filters, the report groupings and the screener are answered by SQL once a table is
too large to hold in pandas. Above roughly 200 000 rows the dashboard and
screener switch to aggregate queries automatically; below that the original
pandas path stays in charge, which keeps small databases bit-identical to the
verified workbook replay.

If [`duckdb`](https://pypi.org/project/duckdb/) is installed, those same queries
run against a columnar **mirror** of the price table instead:

| 4 million rows | SQLite | DuckDB mirror |
|---|---|---|
| Dashboard totals | 1.5 s | 0.1 s |
| Trend of one column | 6.4 s | 0.01 s |
| Group by symbol / date | 0.2 – 6.4 s | 0.01 s |
| Screener | 0.7 s | 0.06 s |

The mirror is derived data: SQLite remains the single source of truth, the mirror
is updated incrementally after each import (a copy of a new daily file takes well
under a second), it rebuilds itself if it is ever out of step, and every failure
falls back to SQLite silently. Both engines share one SQL builder, so their
answers cannot drift apart.

**Settings → Analytics** turns the index on or off, moves its file, and sets the
row count at which queries switch from pandas to SQL (200 000 by default). The
index is a rebuildable cache: deleting it costs one rebuild, never any data.

### Optional extras

Neither package is required — without them everything works, only more slowly:

```
pip install python-calamine duckdb
```

`python-calamine` is under 1 MB; `duckdb` is about 21 MB. The PyInstaller build
bundles whichever of them are installed in the build environment.

### Memory

* Cached frames live in a size-bounded LRU cache, so opening several pages cannot
  accumulate gigabytes of DataFrames.
* The **Historical Data** page caps itself at 200 000 rows and says so; narrow the
  filters to see the rest.
* The screener never materialises the full frame at scale — it reads per-symbol
  aggregates instead.

---

## Troubleshooting

**The window does not appear / a Qt platform error appears.**
Run with `QT_QPA_PLATFORM=offscreen` to confirm the application itself starts;
otherwise a missing Qt system library is the usual cause (see *Linux notes*).

**"workbook not found" during `--check`.**
Pass one explicitly: `python3 main.py --check --workbook "/path/to/file.xlsm"`.

**Charts are blank.**
QtWebEngine could not start; the native chart fallback should appear instead. If
neither renders, check `logs/capital.log` and raise `Settings → Logging → Log
level` to `DEBUG`.

**The import rejects rows.**
The report lists each rejected row with its number and the reason. Missing
symbol/date and non-numeric prices reject a row; out-of-range prices, `HIGH <
LOW`, duplicates and future dates are warnings only.

**The numbers disagree with the workbook.**
Confirm the mode. In `original` mode parity is 100 % and verified by
`python3 -m pytest -m slow`; in `corrected` mode differences are *expected* and
each one is listed in the Formula Audit Log.

**Where is the log?**
`logs/capital.log`, rotating, with the level, size and retention set in
**Settings → Logging**.
