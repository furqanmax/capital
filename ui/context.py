"""Shared application context passed to every page.

Holds the single :class:`~database.db.Database` connection and one instance of
each service, so every page sees the same caches and the same settings.  Pages
never construct services themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from database.db import Database
from services.data_service import DataService
from services.dashboard_service import DashboardService
from services.formula_service import FormulaService
from services.palette_service import PaletteService
from services.report_service import ReportService
from services.settings_service import SettingsService


@dataclass
class AppContext:
    """Everything a page needs, created once at start-up."""

    db: Database
    settings: SettingsService
    data: DataService
    formulas: FormulaService
    dashboard: DashboardService
    reports: ReportService
    palette: PaletteService
    theme: str = "dark"
    filters: Optional[object] = None

    @classmethod
    def create(cls, db_path: str | Path | None = None, theme: Optional[str] = None) -> "AppContext":
        db = Database(db_path) if db_path else Database()
        db.init_schema()
        settings = SettingsService(db)
        data = DataService(db, settings)
        themes = theme or settings.get("display.theme", "dark")
        palette = PaletteService(settings)
        context = cls(
            db=db,
            settings=settings,
            data=data,
            formulas=FormulaService(db),
            dashboard=DashboardService(db, data),
            reports=ReportService(db, data, settings=settings),
            palette=palette,
            theme=themes,
        )
        palette.apply()
        return context

    # ------------------------------------------------------------------

    def invalidate(self, reason: str = "") -> None:
        """Called after an import or a settings change."""
        self.data.invalidate(reason)

    def reload_settings(self) -> None:
        self.theme = self.settings.get("display.theme", "dark")
        self.palette.apply()
        self.invalidate("settings changed")


__all__ = ["AppContext"]
