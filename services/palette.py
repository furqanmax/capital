"""Workbook colour palette — pure, dependency-free helpers.

The sample workbook (``Saif-Stock Market Data File-Sample.xlsm``) paints its
dashboard with conditional formatting rather than static fills.  The exact
colours were extracted from the sheet XML and live in
``config/palette_workbook.json`` so they can be audited and regenerated.

This module owns the data model and the small amount of colour maths:

* loading the workbook defaults,
* merging user overrides on top,
* validating ``#RRGGBB`` values,
* choosing a readable text colour for a fill.

It deliberately imports nothing from the database or Qt layers, so both the
services and the widgets can share it.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

_FILENAME = "palette_workbook.json"
_CACHE: Optional[Dict[str, Any]] = None

#: Version counter bumped by :func:`set_scheme`; widgets use it to invalidate
#: their resolved-colour caches.
_VERSION = 0

#: The scheme the UI should paint with; ``None`` means "classic" (the theme's
#: own semantic colours, i.e. the pre-workbook look).
_ACTIVE: Optional[Dict[str, Any]] = None

#: Preferred order for the Settings editor and the legend.
GROUP_LABELS: Dict[str, str] = {
    "d_ind": "D.Ind — trade count (Main!H)",
    "dv1": "DV1 — volume vs average (Main!I)",
    "dv2": "DV2 — delivery level (Main!J)",
    "range": "Above / below average (Analysis!N, R, W)",
    "v1": "V1 jackpot (uncoloured in the workbook)",
}

CHROME_LABELS: Dict[str, str] = {
    "table_header": "Table header band",
    "table_header_text": "Table header text",
    "section": "Section header",
    "accent": "Accent / chart primary",
    "highlight": "Highlight chip",
    "date_label": "Date label",
    "date_value": "Date value",
    "surface": "Sheet surface",
}


# --------------------------------------------------------------------------
# loading / validation
# --------------------------------------------------------------------------


def palette_candidates() -> list[Path]:
    """Where ``palette_workbook.json`` may live, most specific first."""
    roots = [Path(__file__).resolve().parent.parent]
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        roots.append(Path(bundle))
    return [root / "config" / _FILENAME for root in roots]


def _read_defaults() -> Dict[str, Any]:
    for candidate in palette_candidates():
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return {"name": "classic", "groups": {}, "chrome": {}, "series": []}


def default_palette(reload: bool = False) -> Dict[str, Any]:
    """A deep copy of the workbook palette as shipped."""
    global _CACHE
    if _CACHE is None or reload:
        _CACHE = _read_defaults()
    return copy.deepcopy(_CACHE)


def normalise_hex(value: Any) -> str:
    """Return ``#RRGGBB`` in upper case, or raise :class:`ValueError`.

    Accepts ``#abc``, ``abc``, ``#aabbcc``, ``aabbcc`` and Excel's
    ``FFAABBCC`` form (the alpha byte is dropped).
    """
    if not isinstance(value, str):
        raise ValueError(f"colour must be a hex string, got {value!r}")
    text = value.strip().lstrip("#")
    if len(text) == 8:
        text = text[2:]
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in text):
        raise ValueError(f"{value!r} is not a #RRGGBB colour")
    return "#" + text.upper()


def is_hex(value: Any) -> bool:
    try:
        normalise_hex(value)
        return True
    except ValueError:
        return False


def luminance(colour: str) -> float:
    """Relative luminance (WCAG 2.x) of a ``#RRGGBB`` colour."""
    text = normalise_hex(colour).lstrip("#")
    channels = []
    for index in (0, 2, 4):
        raw = int(text[index:index + 2], 16) / 255.0
        channels.append(raw / 12.92 if raw <= 0.03928
                        else ((raw + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two colours (1.0 … 21.0)."""
    high, low = sorted((luminance(foreground), luminance(background)), reverse=True)
    return round((high + 0.05) / (low + 0.05), 2)


def auto_text(fill: str) -> str:
    """Pick black or white text for ``fill``.

    The rule is a luminance split rather than "whichever ratio is larger", so
    the workbook's white text survives on the saturated red / green / purple
    badges (where white is part of the visual code) while the amber, silver,
    cyan and light-green fills switch to black, which is the only readable
    choice there.
    """
    return "#000000" if luminance(fill) >= 0.35 else "#FFFFFF"


# --------------------------------------------------------------------------
# scheme resolution
# --------------------------------------------------------------------------


def _apply_group(
    target: Dict[str, Any], group: str, labels: Mapping[str, Any]
) -> None:
    bucket = target.setdefault(group, {})
    for label, raw in labels.items():
        if raw is None:
            bucket[label] = None
            continue
        if not isinstance(raw, Mapping):
            raise ValueError(f"{group}/{label}: expected an object, got {raw!r}")
        entry: Dict[str, Any] = {"fill": None, "text": None}
        if raw.get("fill") is not None:
            entry["fill"] = normalise_hex(raw["fill"])
        if raw.get("text") is not None:
            entry["text"] = normalise_hex(raw["text"])
        bucket[label] = entry


def build_scheme(
    overrides: Optional[Mapping[str, Any]] = None,
    text_mode: str = "auto",
    defaults: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge ``overrides`` onto the workbook defaults and resolve text colours.

    ``text_mode`` is ``"auto"`` (black or white by luminance) or ``"exact"``
    (white, as the workbook does).  An explicit ``text`` in the palette always
    wins, so the dark-red "Low Del" label survives either mode.
    """
    document = copy.deepcopy(dict(defaults)) if defaults is not None else default_palette()
    overrides = overrides or {}

    for group, labels in (overrides.get("groups") or {}).items():
        _apply_group(document.setdefault("groups", {}), str(group), labels or {})

    chrome = document.setdefault("chrome", {})
    for key, value in (overrides.get("chrome") or {}).items():
        if value in (None, ""):
            continue
        chrome[str(key)] = normalise_hex(value)

    if overrides.get("series") is not None:
        series = [normalise_hex(value) for value in overrides["series"] if value]
        if series:
            document["series"] = series

    mode = "exact" if str(text_mode).lower() == "exact" else "auto"
    for labels in document.get("groups", {}).values():
        if not isinstance(labels, Mapping):
            continue
        for entry in labels.values():
            if not isinstance(entry, dict) or not entry.get("fill"):
                continue
            if not entry.get("text"):
                entry["text"] = auto_text(entry["fill"]) if mode == "auto" else "#FFFFFF"

    document["text_mode"] = mode
    document.setdefault("series", [])
    document.setdefault("groups", {})
    document.setdefault("chrome", {})
    return document


def scheme_entry(document: Optional[Mapping[str, Any]], group: str,
                 label: str) -> Optional[Dict[str, str]]:
    """Look up one label, tolerating case differences.  ``None`` if uncoloured."""
    if not document or not label:
        return None
    labels = (document.get("groups") or {}).get(group)
    if not isinstance(labels, Mapping):
        return None
    entry = labels.get(label)
    if entry is None:
        folded = str(label).casefold()
        for key, candidate in labels.items():
            if str(key).casefold() == folded:
                entry = candidate
                break
    if not isinstance(entry, Mapping) or not entry.get("fill"):
        return None
    fill = str(entry["fill"])
    return {
        "fill": fill,
        "text": str(entry.get("text") or auto_text(fill)),
    }


# --------------------------------------------------------------------------
# active scheme (process-wide, set once at start-up and on save)
# --------------------------------------------------------------------------


def set_scheme(document: Optional[Mapping[str, Any]]) -> None:
    global _ACTIVE, _VERSION
    _ACTIVE = copy.deepcopy(dict(document)) if document else None
    _VERSION += 1


def active() -> Optional[Dict[str, Any]]:
    return _ACTIVE


def version() -> int:
    return _VERSION


def entry(group: str, label: str) -> Optional[Dict[str, str]]:
    return scheme_entry(_ACTIVE, group, label)


def chrome(key: str, fallback: Optional[str] = None) -> Optional[str]:
    if not _ACTIVE:
        return fallback
    return (_ACTIVE.get("chrome") or {}).get(key, fallback)


__all__ = [
    "GROUP_LABELS",
    "CHROME_LABELS",
    "default_palette",
    "palette_candidates",
    "normalise_hex",
    "is_hex",
    "luminance",
    "contrast_ratio",
    "auto_text",
    "build_scheme",
    "scheme_entry",
    "set_scheme",
    "active",
    "version",
    "entry",
    "chrome",
]
