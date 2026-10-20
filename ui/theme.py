"""Application theme: palette, chart colours and the Qt stylesheet.

Two themes ship: ``dark`` (default) and ``light``.  The palette is exposed as a
plain dictionary so the Plotly charts and the Qt widgets always agree on colour.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from services import palette as workbook

DARK: Dict[str, str] = {
    "bg": "#111418",
    "bg_alt": "#171B21",
    "panel": "#1B2027",
    "panel_alt": "#222833",
    "border": "#2C3440",
    "text": "#E6EAF0",
    "text_dim": "#9AA6B6",
    "accent": "#4C9AFF",
    "accent_dim": "#2F6FBF",
    "good": "#3FB950",
    "warning": "#D29922",
    "danger": "#F85149",
    "neutral": "#8B949E",
    "grid": "#232A34",
    "selection": "#1F3B57",
}

LIGHT: Dict[str, str] = {
    "bg": "#F5F7FA",
    "bg_alt": "#FFFFFF",
    "panel": "#FFFFFF",
    "panel_alt": "#EEF2F6",
    "border": "#CBD5E1",
    "text": "#101828",
    "text_dim": "#5A6675",
    "accent": "#1667D1",
    "accent_dim": "#7FA8E8",
    "good": "#1A7F37",
    "warning": "#9A6700",
    "danger": "#CF222E",
    "neutral": "#6B7280",
    "grid": "#E2E8F0",
    "selection": "#DCE9FB",
}

#: Categorical series colours (colour-blind friendly ordering).
SERIES_COLORS: List[str] = [
    "#4C9AFF", "#3FB950", "#D29922", "#F85149", "#A371F7",
    "#2DD4BF", "#F0883E", "#EC6CB9", "#8B949E", "#58A6FF",
]

#: Keys the data-presentation widgets read.  They are always present in
#: :func:`palette`; the workbook scheme replaces the fallbacks.
WORKBOOK_KEYS = (
    "table_header", "table_header_text", "highlight",
    "date_label", "date_value", "sheet", "data_accent",
)

_PALETTE_CACHE: Dict[tuple, Dict[str, str]] = {}


def set_scheme(document: Optional[dict]) -> None:
    """Install the active colour scheme (``None`` restores the classic look)."""
    workbook.set_scheme(document)


def signal(group: str, label: str) -> Optional[Dict[str, str]]:
    """Resolved fill/text for a classification label, or ``None``."""
    return workbook.entry(group, label)


def scheme_version() -> int:
    return workbook.version()


def palette(theme: str = "dark") -> Dict[str, str]:
    """Theme chrome plus the workbook data colours (cached per scheme version)."""
    name = "light" if str(theme).lower() == "light" else "dark"
    key = (name, workbook.version())
    cached = _PALETTE_CACHE.get(key)
    if cached is not None:
        return cached

    base = dict(LIGHT if name == "light" else DARK)
    base.setdefault("table_header", base["panel_alt"])
    base.setdefault("table_header_text", base["text_dim"])
    base.setdefault("highlight", base["panel_alt"])
    base.setdefault("date_label", base["panel_alt"])
    base.setdefault("date_value", base["selection"])
    base.setdefault("sheet", base["panel"])
    base.setdefault("data_accent", base["accent"])
    for key_name in ("section",):
        base.setdefault(key_name, base["panel_alt"])

    document = workbook.active()
    if document:
        chrome = document.get("chrome") or {}
        base["table_header"] = chrome.get("table_header", base["table_header"])
        base["table_header_text"] = chrome.get("table_header_text", base["table_header_text"])
        base["highlight"] = chrome.get("highlight", base["highlight"])
        base["date_label"] = chrome.get("date_label", base["date_label"])
        base["date_value"] = chrome.get("date_value", base["date_value"])
        base["sheet"] = chrome.get("surface", base["sheet"])
        base["data_accent"] = chrome.get("accent", base["data_accent"])
        base["section"] = chrome.get("section", base["section"])

    _PALETTE_CACHE.clear()
    _PALETTE_CACHE[key] = base
    return base


def series_palette(theme: str = "dark") -> List[str]:
    """The chart colourway: workbook series when active, else the theme's."""
    document = workbook.active()
    if document:
        series = [str(value) for value in (document.get("series") or []) if value]
        if series:
            return series
    return SERIES_COLORS


def series_color(index: int, theme: str = "dark") -> str:
    colors = series_palette(theme)
    return colors[index % len(colors)]


def stylesheet(theme: str = "dark") -> str:
    """A single stylesheet for every widget in the application."""
    c = palette(theme)
    return f"""
    * {{
        font-family: "DejaVu Sans", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
        font-size: 12px;
    }}
    QMainWindow, QDialog, QWidget#Page {{ background: {c['bg']}; color: {c['text']}; }}

    QLabel {{ color: {c['text']}; }}
    QLabel#PageTitle {{ font-size: 19px; font-weight: 700; }}
    QLabel#PageSubtitle {{ color: {c['text_dim']}; font-size: 12px; }}
    QLabel#SectionTitle {{ font-size: 13px; font-weight: 700; color: {c['text']}; }}
    QLabel#Muted {{ color: {c['text_dim']}; }}
    QLabel#CardValue {{ font-size: 22px; font-weight: 700; }}
    QLabel#CardTitle {{ color: {c['text_dim']}; font-size: 11px; text-transform: uppercase; }}

    QFrame#Card, QGroupBox {{
        background: {c['panel']};
        border: 1px solid {c['border']};
        border-radius: 8px;
    }}
    QGroupBox {{ margin-top: 14px; padding: 10px 8px 8px 8px; font-weight: 700; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {c['text']}; }}

    QPushButton {{
        background: {c['panel_alt']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        padding: 5px 12px;
        color: {c['text']};
    }}
    QPushButton:hover {{ border-color: {c['accent']}; }}
    QPushButton:disabled {{ color: {c['text_dim']}; }}
    QPushButton#Primary {{
        background: {c['accent']};
        border-color: {c['accent']};
        color: #FFFFFF;
        font-weight: 600;
    }}
    QPushButton#Primary:hover {{ background: {c['accent_dim']}; }}
    QPushButton#Danger {{ color: {c['danger']}; border-color: {c['danger']}; }}

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTextEdit, QPlainTextEdit {{
        background: {c['bg_alt']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        padding: 4px 6px;
        color: {c['text']};
        selection-background-color: {c['selection']};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus,
    QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {c['accent']}; }}
    QComboBox QAbstractItemView {{
        background: {c['panel']};
        border: 1px solid {c['border']};
        selection-background-color: {c['selection']};
        selection-color: {c['text']};
    }}

    QListWidget, QListView, QTreeView, QTableView {{
        background: {c['bg_alt']};
        color: {c['text']};
        alternate-background-color: {c['bg']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        gridline-color: {c['grid']};
        selection-background-color: {c['selection']};
        selection-color: {c['text']};
    }}
    QHeaderView::section {{
        background: {c['panel_alt']};
        color: {c['text_dim']};
        border: none;
        border-right: 1px solid {c['border']};
        border-bottom: 1px solid {c['border']};
        padding: 5px 6px;
        font-weight: 600;
    }}
    QTableCornerButton::section {{ background: {c['panel_alt']}; border: none; }}

    QTabWidget::pane {{ border: 1px solid {c['border']}; border-radius: 8px; top: -1px; }}
    QTabBar::tab {{
        background: {c['bg_alt']};
        border: 1px solid {c['border']};
        border-bottom: none;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        padding: 6px 14px;
        color: {c['text_dim']};
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{ background: {c['panel']}; color: {c['text']}; font-weight: 600; }}

    QListWidget#Nav {{
        background: {c['panel']};
        border: none;
        border-right: 1px solid {c['border']};
        border-radius: 0;
        outline: 0;
        padding: 6px 0;
    }}
    QListWidget#Nav::item {{ padding: 9px 14px; margin: 1px 6px; border-radius: 6px; color: {c['text_dim']}; }}
    QListWidget#Nav::item:selected {{ background: {c['selection']}; color: {c['text']}; font-weight: 600; }}
    QListWidget#Nav::item:hover {{ color: {c['text']}; }}

    QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {c['border']}; min-height: 30px; border-radius: 5px; }}
    QScrollBar::handle:vertical:hover {{ background: {c['accent_dim']}; }}
    QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: {c['border']}; min-width: 30px; border-radius: 5px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QStatusBar {{ background: {c['panel']}; border-top: 1px solid {c['border']}; color: {c['text_dim']}; }}
    QStatusBar::item {{ border: none; }}
    QToolBar {{ background: {c['panel']}; border-bottom: 1px solid {c['border']}; spacing: 4px; padding: 4px; }}
    QMenuBar {{ background: {c['panel']}; border-bottom: 1px solid {c['border']}; }}
    QMenuBar::item:selected {{ background: {c['selection']}; }}
    QMenu {{ background: {c['panel']}; border: 1px solid {c['border']}; }}
    QMenu::item:selected {{ background: {c['selection']}; }}
    QProgressBar {{
        border: 1px solid {c['border']};
        border-radius: 6px;
        background: {c['bg_alt']};
        text-align: center;
        color: {c['text']};
        height: 16px;
    }}
    QProgressBar::chunk {{ background: {c['accent']}; border-radius: 5px; }}
    QSplitter::handle {{ background: {c['border']}; }}
    QCheckBox, QRadioButton {{ color: {c['text']}; spacing: 6px; }}
    QToolTip {{
        background: {c['panel_alt']};
        color: {c['text']};
        border: 1px solid {c['border']};
        padding: 4px;
    }}
    """


def plotly_layout(theme: str = "dark", height: int = 260) -> dict:
    """A Plotly layout template matching the Qt palette."""
    c = palette(theme)
    return {
        "height": height,
        "margin": {"l": 52, "r": 16, "t": 16, "b": 36},
        "paper_bgcolor": c["panel"],
        "plot_bgcolor": c["panel"],
        "font": {"color": c["text"], "size": 11},
        "xaxis": {
            "gridcolor": c["grid"], "zerolinecolor": c["grid"],
            "linecolor": c["border"], "tickfont": {"size": 10},
        },
        "yaxis": {
            "gridcolor": c["grid"], "zerolinecolor": c["grid"],
            "linecolor": c["border"], "tickfont": {"size": 10},
        },
        "legend": {"orientation": "h", "y": -0.18, "font": {"size": 10}},
        "colorway": series_palette(theme),
        "hovermode": "x unified",
    }


def plotly_config() -> dict:
    return {
        "displaylogo": False,
        "responsive": True,
        "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
    }


__all__ = [
    "DARK", "LIGHT", "SERIES_COLORS", "WORKBOOK_KEYS", "palette", "series_palette",
    "series_color", "signal", "set_scheme", "scheme_version",
    "stylesheet", "plotly_layout", "plotly_config",
]
