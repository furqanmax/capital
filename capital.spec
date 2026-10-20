# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build recipe for Capital Analytics.

    pip install -r requirements.txt
    pyinstaller capital.spec            # -> dist/CapitalAnalytics/CapitalAnalytics

The build is a one-folder bundle: the SQLite database, the exports folder and the
logs live *next to* the executable (see ``database.db._project_root``), which a
one-file build's temporary folder cannot provide.  Only read-only assets are
bundled inside the application folder.

Nothing here executes a spreadsheet macro; workbooks are only ever read.
"""

import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve()

# --- read-only resources the application looks for at start-up ---------------
datas = [
    (str(ROOT / "config" / "default_settings.json"), "config"),
    (str(ROOT / "config" / "palette_workbook.json"), "config"),
    (str(ROOT / "docs" / "sample_dashboard_config.json"), "docs"),
    (str(ROOT / "database" / "schema.sql"), "database"),
]

# The sample workbook is optional; bundle it when present so a fresh install can
# run its own self-test straight away.
sample = ROOT / "data" / "Saif-Stock Market Data File-Sample.xlsm"
if sample.exists():
    datas.append((str(sample), "data"))

# --- modules that are imported dynamically or via Qt plugins -----------------
hiddenimports = [
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebChannel",
    "PySide6.QtPrintSupport",
    "PySide6.QtSvg",
    "sqlalchemy.dialects.sqlite",
    "plotly",
    "plotly.offline",
    "reportlab",
    "reportlab.pdfbase._fontdata",
    "xlsxwriter",
    "openpyxl",
    "oletools",
    "olefile",
]
hiddenimports += collect_submodules("reportlab")
hiddenimports += collect_submodules("oletools")

# Optional accelerators.  Both are imported defensively at run time (with a
# fallback), so PyInstaller cannot see them by following imports; bundle them
# only when they are installed in the build environment.
for _optional in ("python_calamine", "duckdb"):
    if importlib.util.find_spec(_optional) is not None:
        hiddenimports.append(_optional)
        hiddenimports += collect_submodules(_optional)

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Qt modules the application never uses — they only inflate the bundle.
        "PySide6.QtQuick3D", "PySide6.QtBluetooth", "PySide6.QtCharts",
        "PySide6.QtNfc", "PySide6.Qt3DCore", "PySide6.QtMultimedia",
        # The application is built on PySide6 only.  Other Qt bindings may be
        # present in the build environment; PyInstaller aborts if it collects
        # more than one, and none of them are wanted.
        "PyQt5", "PyQt6", "PySide2", "qtpy",
        # Heavy optional packages that some transitive dependency tries to
        # import but the application never uses.
        "torch", "tensorboard", "matplotlib", "tkinter", "IPython", "notebook",
        # ``pkg_resources`` is pulled in by a transitive dependency (wrapt), which
        # guards the import with try/except and does not need it.  Excluding it
        # avoids PyInstaller's ``pyi_rth_pkgres`` runtime hook, whose vendored
        # dependencies are not reliably collectable from a distro setuptools.
        "pkg_resources", "setuptools",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CapitalAnalytics",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CapitalAnalytics",
)
