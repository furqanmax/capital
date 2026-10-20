"""Application pages."""

from .audit_log import AuditLogPage
from .base import BasePage
from .dashboard import DashboardPage
from .explorer import StockExplorerPage
from .formula_lab import FormulaLabPage
from .history import HistoricalDataPage
from .import_page import ImportDataPage
from .reports import ReportsPage
from .settings_page import SettingsPage

#: navigation order; the key is the value stored in ``dashboard.default_landing_page``
PAGE_CLASSES = (
    ("dashboard", DashboardPage),
    ("explorer", StockExplorerPage),
    ("history", HistoricalDataPage),
    ("formula_lab", FormulaLabPage),
    ("reports", ReportsPage),
    ("import", ImportDataPage),
    ("settings", SettingsPage),
    ("audit", AuditLogPage),
)

__all__ = [
    "BasePage", "DashboardPage", "StockExplorerPage", "HistoricalDataPage",
    "FormulaLabPage", "ReportsPage", "ImportDataPage", "SettingsPage",
    "AuditLogPage", "PAGE_CLASSES",
]
