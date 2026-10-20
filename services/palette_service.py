"""Settings-backed colour scheme.

``config/palette_workbook.json`` holds the defaults extracted from the sample
workbook; this service layers the user's overrides on top and keeps the
process-wide active scheme in sync with the ``colors`` settings section.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from services import palette as palette_module
from services.palette import build_scheme, default_palette, normalise_hex
from services.settings_service import (
    VALID_COLOR_SCHEMES,
    VALID_TEXT_MODES,
    SettingsService,
)

SECTION = "colors"
DEFAULT_SCHEME = "workbook"
SCHEMES = VALID_COLOR_SCHEMES
TEXT_MODES = VALID_TEXT_MODES


def _clean_fill(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return normalise_hex(value)


def _clean_text(value: Any) -> Optional[str]:
    if value in (None, "", "auto"):
        return None
    return normalise_hex(value)


def _diff(
    defaults: Mapping[str, Any],
    groups: Optional[Mapping[str, Any]],
    chrome: Optional[Mapping[str, Any]],
    series: Optional[Any],
) -> Dict[str, Any]:
    """Only the entries that differ from the workbook defaults."""
    overrides: Dict[str, Any] = {}

    for group, labels in (groups or {}).items():
        for label, raw in (labels or {}).items():
            base = ((defaults.get("groups") or {}).get(group) or {}).get(label)
            base_fill = base.get("fill") if isinstance(base, Mapping) else None
            base_text = base.get("text") if isinstance(base, Mapping) else None
            fill = _clean_fill((raw or {}).get("fill")) if raw is not None else None
            text = _clean_text((raw or {}).get("text")) if raw is not None else None
            if fill != base_fill or text != base_text:
                overrides.setdefault("groups", {}).setdefault(group, {})[label] = {
                    "fill": fill,
                    "text": text,
                }

    chrome_diff: Dict[str, str] = {}
    for key, value in (chrome or {}).items():
        base = (defaults.get("chrome") or {}).get(key)
        cleaned = _clean_fill(value)
        if cleaned is None:
            # A cleared field means "use the workbook value"; do not store a
            # null chrome colour the resolver cannot paint with.
            continue
        if cleaned != base:
            chrome_diff[str(key)] = cleaned
    if chrome_diff:
        overrides["chrome"] = chrome_diff

    if series is not None:
        cleaned_series = [_clean_fill(value) for value in series]
        if cleaned_series != list(defaults.get("series") or []):
            overrides["series"] = cleaned_series

    return overrides


class PaletteService:
    """Read, validate and persist the dashboard colour scheme."""

    def __init__(self, settings: SettingsService):
        self.settings = settings
        self._last_scheme: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # current values
    # ------------------------------------------------------------------

    def section(self) -> Dict[str, Any]:
        return self.settings.section(SECTION)

    def name(self) -> str:
        value = str(self.settings.get(f"{SECTION}.scheme", DEFAULT_SCHEME) or "")
        return value if value in SCHEMES else DEFAULT_SCHEME

    def text_mode(self) -> str:
        value = str(self.settings.get(f"{SECTION}.text_mode", "auto") or "")
        return value if value in TEXT_MODES else "auto"

    def show_legend(self) -> bool:
        return bool(self.settings.get(f"{SECTION}.show_legend", True))

    def overrides(self) -> Dict[str, Any]:
        value = self.settings.get(f"{SECTION}.overrides", {})
        return copy.deepcopy(value) if isinstance(value, dict) else {}

    def defaults(self) -> Dict[str, Any]:
        return default_palette()

    def scheme(self) -> Optional[Dict[str, Any]]:
        """The resolved scheme, or ``None`` when the classic look is selected."""
        if self.name() == "classic":
            return None
        return build_scheme(self.overrides(), self.text_mode(), defaults=self.defaults())

    def entries(self) -> Dict[str, Dict[str, Any]]:
        """Raw (unresolved-text) entries, for the editor."""
        document = self.defaults()
        for group, labels in (self.overrides().get("groups") or {}).items():
            for label, raw in (labels or {}).items():
                document.setdefault("groups", {}).setdefault(group, {})[label] = dict(raw or {})
        for key, value in (self.overrides().get("chrome") or {}).items():
            document.setdefault("chrome", {})[str(key)] = value
        if self.overrides().get("series") is not None:
            document["series"] = list(self.overrides()["series"])
        return document

    # ------------------------------------------------------------------
    # applying
    # ------------------------------------------------------------------

    def apply(self) -> Optional[Dict[str, Any]]:
        """Push the resolved scheme into :mod:`services.palette`."""
        scheme = self.scheme()
        palette_module.set_scheme(scheme)
        self._last_scheme = scheme
        return scheme

    # ------------------------------------------------------------------
    # editing
    # ------------------------------------------------------------------

    def set_entry(self, group: str, label: str, fill: Optional[str] = None,
                  text: Optional[str] = None) -> None:
        overrides = self.overrides()
        overrides.setdefault("groups", {}).setdefault(group, {})[label] = {
            "fill": _clean_fill(fill), "text": _clean_text(text),
        }
        self.settings.set(f"{SECTION}.overrides", overrides)

    def reset_entry(self, group: str, label: str) -> None:
        overrides = self.overrides()
        bucket = overrides.get("groups", {}).get(group, {})
        bucket.pop(label, None)
        if not bucket:
            overrides.get("groups", {}).pop(group, None)
        if not overrides.get("groups"):
            overrides.pop("groups", None)
        self.settings.set(f"{SECTION}.overrides", overrides)

    def save_edits(
        self,
        *,
        scheme_name: str,
        text_mode: str,
        show_legend: bool,
        groups: Optional[Mapping[str, Any]] = None,
        chrome: Optional[Mapping[str, Any]] = None,
        series: Optional[Any] = None,
    ) -> None:
        """Persist a full editor state, storing only the differences."""
        if scheme_name not in SCHEMES:
            raise ValueError(f"scheme must be one of {', '.join(SCHEMES)}")
        if text_mode not in TEXT_MODES:
            raise ValueError(f"text mode must be one of {', '.join(TEXT_MODES)}")
        overrides = _diff(self.defaults(), groups, chrome, series)
        self.settings.update({
            f"{SECTION}.scheme": scheme_name,
            f"{SECTION}.text_mode": text_mode,
            f"{SECTION}.show_legend": bool(show_legend),
            f"{SECTION}.overrides": overrides,
        })

    def reset(self) -> None:
        """Back to the workbook palette as shipped."""
        self.settings.reset(SECTION)

    # ------------------------------------------------------------------
    # import / export
    # ------------------------------------------------------------------

    def export_json(self, path: str | Path) -> Path:
        target = Path(path)
        payload = {
            "version": 1,
            "scheme": self.name(),
            "text_mode": self.text_mode(),
            "show_legend": self.show_legend(),
            "overrides": self.overrides(),
        }
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return target

    def import_json(self, path: str | Path) -> None:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("a colour scheme must be a JSON object")
        scheme = payload.get("scheme", DEFAULT_SCHEME)
        if scheme not in SCHEMES:
            raise ValueError(f"scheme must be one of {', '.join(SCHEMES)}")
        text_mode = payload.get("text_mode", "auto")
        if text_mode not in TEXT_MODES:
            raise ValueError(f"text mode must be one of {', '.join(TEXT_MODES)}")
        overrides = payload.get("overrides") or {}
        if not isinstance(overrides, dict):
            raise ValueError("overrides must be an object")
        # Validate every colour before touching the database.
        build_scheme(overrides, text_mode)
        self.settings.update({
            f"{SECTION}.scheme": scheme,
            f"{SECTION}.text_mode": text_mode,
            f"{SECTION}.show_legend": bool(payload.get("show_legend", True)),
            f"{SECTION}.overrides": overrides,
        })


__all__ = ["PaletteService", "SECTION", "SCHEMES", "TEXT_MODES", "DEFAULT_SCHEME"]
