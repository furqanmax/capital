"""Typed access to the application settings.

Settings live in the ``app_settings`` table as one JSON document per top-level
section (``import``, ``display``, ``dashboard`` …).  This service gives the rest
of the application a single, validated way to read and write them, so no page
has to know how the document is stored.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from sqlalchemy import select

from database.db import PROJECT_ROOT, Database, resolve_path

DEFAULTS_FILE = PROJECT_ROOT / "config" / "default_settings.json"

#: Default row count above which the aggregate queries stop materialising frames.
#: Kept in sync with :data:`services.query_engine.LARGE_DATASET_ROWS`.
LARGE_DATASET_THRESHOLD = 200_000

#: Settings the user may change from the Settings page, with a human label.
EDITABLE_SECTIONS: Dict[str, Dict[str, str]] = {
    "database": {
        "path": "Database file",
        "backup_dir": "Backup folder",
    },
    "import": {
        "duplicate_policy": "Duplicate handling",
        "sheet_priority": "Preferred sheets",
        "auto_map_columns": "Auto-map columns",
        "store_extra_columns": "Keep unmapped columns",
        "max_error_samples": "Validation samples kept",
    },
    "display": {
        "theme": "Theme",
        "date_format": "Date format",
        "number_format": "Number format",
        "percent_format": "Percentage format",
        "grid_lines": "Table grid lines",
    },
    "colors": {
        "scheme": "Colour scheme",
        "text_mode": "Text on coloured cells",
        "show_legend": "Show the colour legend",
        "overrides": "Colour overrides",
    },
    "filters": {
        "default_date_range_days": "Default date range (days)",
        "page_size": "Rows per page",
    },
    "analytics": {
        "duckdb": "Use the Columnar analytics index",
        "duckdb_path": "Analytics index file",
        "large_dataset_rows": "Answer queries with SQL above this many rows",
    },
    "formulas": {
        "mode": "Formula mode (original / corrected)",
        "moving_average_days": "Volume moving-average sessions",
        "delivery_baseline_days": "Delivery baseline sessions",
    },
    "dashboard": {
        "default_landing_page": "Landing page",
        "active_layout": "Active dashboard layout",
        "refresh_on_import": "Refresh dashboard after import",
    },
    "logging": {
        "level": "Log level",
        "file": "Log file",
        "max_bytes": "Rotate after (bytes)",
        "backup_count": "Rotated files kept",
    },
    "export": {
        "directory": "Export folder",
        "default_format": "Default export format",
    },
}

VALID_THEMES = ("dark", "light")
VALID_FORMULA_MODES = ("original", "corrected")
VALID_EXPORT_FORMATS = ("xlsx", "csv", "pdf")
VALID_DUPLICATE_POLICIES = ("skip", "update", "new_batch")
VALID_COLOR_SCHEMES = ("workbook", "classic")
VALID_TEXT_MODES = ("auto", "exact")

#: Guard rails so a typo in a settings dialog cannot poison the engine.
CHOICES: Dict[str, tuple] = {
    "display.theme": VALID_THEMES,
    "colors.scheme": VALID_COLOR_SCHEMES,
    "colors.text_mode": VALID_TEXT_MODES,
    "formulas.mode": VALID_FORMULA_MODES,
    "export.default_format": VALID_EXPORT_FORMATS,
    "import.duplicate_policy": VALID_DUPLICATE_POLICIES,
    "logging.level": ("DEBUG", "INFO", "WARNING", "ERROR"),
}


def default_settings_candidates() -> List[Path]:
    """Where ``default_settings.json`` may live, most specific first.

    The bundled copy (PyInstaller's ``_MEIPASS``) is only consulted when the
    project's own ``config/`` folder is absent, so a source checkout always wins.
    """
    roots = [PROJECT_ROOT]
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        roots.append(Path(bundle))
    return [root / "config" / "default_settings.json" for root in roots]


def load_defaults() -> Dict[str, Any]:
    for candidate in default_settings_candidates():
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return {}


class SettingsService:
    """Read/write access to every application setting."""

    def __init__(self, database: Database):
        self.db = database
        self._defaults = load_defaults()

    # ------------------------------------------------------------------
    # reading
    # ------------------------------------------------------------------

    def all(self) -> Dict[str, Any]:
        """Every section, with defaults filling in anything missing."""
        doc = copy.deepcopy(self._defaults)
        with self.db.session() as session:
            from models import AppSetting

            for row in session.scalars(select(AppSetting)):
                if isinstance(row.value, dict) and isinstance(doc.get(row.key), dict):
                    doc[row.key].update(row.value)
                else:
                    doc[row.key] = row.value
        return doc

    def section(self, name: str) -> Dict[str, Any]:
        defaults = self._defaults.get(name, {})
        with self.db.session() as session:
            from models import AppSetting

            row = session.get(AppSetting, name)
            value = row.value if row is not None else {}
        merged = dict(defaults)
        if isinstance(value, dict):
            merged.update(value)
        return merged

    def get(self, dotted: str, default: Any = None) -> Any:
        section, _, key = dotted.partition(".")
        if not key:
            return self.section(dotted) if dotted in self._defaults else default
        values = self.section(section)
        return values.get(key, default)

    def choices(self, dotted: str) -> Optional[tuple]:
        return CHOICES.get(dotted)

    # ------------------------------------------------------------------
    # writing
    # ------------------------------------------------------------------

    def set(self, dotted: str, value: Any) -> Any:
        """Persist one setting and return the coerced value."""
        section, _, key = dotted.partition(".")
        if not key:
            raise ValueError(f"setting {dotted!r} must be section.key")
        value = self._coerce(dotted, value)
        with self.db.session() as session:
            from models import AppSetting

            row = session.get(AppSetting, section)
            current = dict(row.value) if (row is not None and isinstance(row.value, dict)) else {}
            current[key] = value
            if row is None:
                session.add(AppSetting(key=section, value=current))
            else:
                row.value = current
        self.db.log("settings", f"{dotted} = {value!r}")
        return value

    def update(self, values: Mapping[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for dotted, value in values.items():
            out[dotted] = self.set(dotted, value)
        return out

    def reset(self, section: Optional[str] = None) -> None:
        with self.db.session() as session:
            from models import AppSetting

            if section is None:
                for key in list(self._defaults):
                    row = session.get(AppSetting, key)
                    if row is not None:
                        session.delete(row)
            else:
                row = session.get(AppSetting, section)
                if row is not None:
                    session.delete(row)
        self.db.log("settings", f"reset {'all sections' if section is None else section}",
                    severity="warning")

    def _coerce(self, dotted: str, value: Any) -> Any:
        allowed = CHOICES.get(dotted)
        defaults = self._defaults
        section, _, key = dotted.partition(".")
        default_value = defaults.get(section, {}).get(key)
        if allowed is not None and value not in allowed:
            raise ValueError(f"{dotted} must be one of {', '.join(map(str, allowed))}")
        if isinstance(default_value, bool):
            return bool(value)
        if isinstance(default_value, int) and not isinstance(default_value, bool):
            return int(value)
        if isinstance(default_value, float):
            return float(value)
        if isinstance(default_value, list):
            return list(value) if isinstance(value, Iterable) and not isinstance(value, str) else [value]
        return value

    # ------------------------------------------------------------------
    # resolved paths / modes used by the engine
    # ------------------------------------------------------------------

    def database_path(self) -> Path:
        return resolve_path(self.get("database.path", "data/capital.db"))

    def backup_dir(self) -> Path:
        return resolve_path(self.get("database.backup_dir", "data/backups"))

    def export_dir(self) -> Path:
        return resolve_path(self.get("export.directory", "data/exports"))

    def log_file(self) -> Path:
        return resolve_path(self.get("logging.file", "logs/capital.log"))

    def formula_mode(self) -> str:
        return self.get("formulas.mode", "original")

    def moving_average_days(self) -> int:
        return int(self.get("formulas.moving_average_days", 25) or 25)

    def preferred_sheets(self) -> list:
        value = self.get("import.sheet_priority", ["Data"])
        return [str(v) for v in value] if isinstance(value, list) else [str(value)]

    def default_date_range_days(self) -> int:
        return int(self.get("filters.default_date_range_days", 30) or 30)

    def page_size(self) -> int:
        return int(self.get("filters.page_size", 100) or 100)

    def duckdb_enabled(self) -> bool:
        """Whether the optional DuckDB mirror may be used."""
        return bool(self.get("analytics.duckdb", True))

    def analytics_path(self) -> Optional[Path]:
        """Where the mirror lives; ``None`` means "next to the database"."""
        value = str(self.get("analytics.duckdb_path", "") or "").strip()
        return resolve_path(value) if value else None

    def large_dataset_rows(self) -> int:
        """Row count above which aggregates are answered by SQL/DuckDB."""
        try:
            return max(0, int(self.get("analytics.large_dataset_rows",
                                       LARGE_DATASET_THRESHOLD)))
        except (TypeError, ValueError):
            return LARGE_DATASET_THRESHOLD

    # ------------------------------------------------------------------
    # colour scheme
    # ------------------------------------------------------------------

    def colour_scheme(self) -> str:
        value = str(self.get("colors.scheme", "workbook") or "workbook")
        return value if value in VALID_COLOR_SCHEMES else "workbook"

    def colour_text_mode(self) -> str:
        value = str(self.get("colors.text_mode", "auto") or "auto")
        return value if value in VALID_TEXT_MODES else "auto"

    def show_colour_legend(self) -> bool:
        return bool(self.get("colors.show_legend", True))


__all__ = [
    "SettingsService",
    "EDITABLE_SECTIONS",
    "CHOICES",
    "VALID_THEMES",
    "VALID_FORMULA_MODES",
    "VALID_EXPORT_FORMATS",
    "VALID_DUPLICATE_POLICIES",
    "VALID_COLOR_SCHEMES",
    "VALID_TEXT_MODES",
    "default_settings_candidates",
    "load_defaults",
]
