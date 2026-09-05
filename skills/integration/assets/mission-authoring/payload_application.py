"""Task-local application and read-only validation of cataloged pydcs loadouts.

Copy this file beside the current authorer under runtime/workspace/. Validate
the proposed aircraft/pylon/CLSID plan through the catalog capability first.
This helper converts that plan to the pinned pydcs shape; it does not decide
which weapons a mission should carry.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Mapping


MAX_ASSIGNMENTS = 64
MAX_SETTINGS_DEPTH = 4


def _pylon_number(value: Any) -> int:
    if isinstance(value, bool):
        raise TypeError("pylon keys must be positive integers")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.isascii() and value.isdigit():
        if value.startswith("0"):
            raise ValueError("string pylon keys must use canonical positive integers")
        number = int(value)
    else:
        raise TypeError("pylon keys must be positive integers")
    if number < 1:
        raise ValueError("pylon keys must be positive integers")
    return number


def _json_value(value: Any, depth: int = 0) -> None:
    if depth > MAX_SETTINGS_DEPTH:
        raise ValueError("settings nesting is too deep")
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            finite = math.isfinite(float(value))
        except OverflowError as error:
            raise ValueError("settings numbers must be finite") from error
        if not finite:
            raise ValueError("settings numbers must be finite")
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item, depth + 1)
        return
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("settings object keys must be strings")
        for item in value.values():
            _json_value(item, depth + 1)
        return
    raise TypeError("settings must contain JSON-safe values")


def normalize_loadout_plan(pylons: Mapping[Any, Any]) -> dict[int, dict[str, Any]]:
    """Normalize a catalog-validated plan without mutating a pydcs group."""

    if not isinstance(pylons, Mapping):
        raise TypeError("pylons must be an object keyed by pylon number")
    if len(pylons) > MAX_ASSIGNMENTS:
        raise ValueError(f"pylons may contain at most {MAX_ASSIGNMENTS} assignments")
    normalized = {}
    for raw_pylon, raw_store in pylons.items():
        pylon = _pylon_number(raw_pylon)
        if pylon in normalized:
            raise ValueError(f"duplicate normalized pylon {pylon}")
        if isinstance(raw_store, str):
            clsid = raw_store
            settings = None
        elif isinstance(raw_store, Mapping):
            unknown = sorted(set(raw_store) - {"clsid", "settings"})
            if unknown:
                raise ValueError(f"pylon {pylon} has unknown fields: {unknown}")
            clsid = raw_store.get("clsid")
            settings = raw_store.get("settings")
            if settings is not None and not isinstance(settings, Mapping):
                raise TypeError(f"pylon {pylon} settings must be an object")
        else:
            raise TypeError(f"pylon {pylon} store must be a CLSID string or object")
        if not isinstance(clsid, str) or not clsid:
            raise ValueError(f"pylon {pylon} clsid must be a non-empty string")
        payload = {"clsid": clsid}
        if settings is not None:
            _json_value(settings)
            payload["settings"] = copy.deepcopy(dict(settings))
        normalized[pylon] = payload
    return dict(sorted(normalized.items()))


def apply_group_loadout(
    group: Any, pylons: Mapping[Any, Any], *, replace: bool = True
) -> Any:
    """Apply one validated plan to every flying unit in a pydcs group."""

    if not isinstance(replace, bool):
        raise TypeError("replace must be boolean")
    units = list(getattr(group, "units", ()) or ())
    if not units:
        raise ValueError("group must contain at least one unit")
    normalized = normalize_loadout_plan(pylons)

    # Check every unit/pylon before clearing or changing any existing loadout.
    for unit in units:
        declared = getattr(getattr(unit, "unit_type", None), "pylons", set())
        missing = sorted(set(normalized) - set(declared))
        if missing:
            name = str(getattr(unit, "name", ""))
            raise ValueError(f"unit {name!r} does not declare pylons {missing}")
        if not callable(getattr(unit, "load_pylon", None)):
            raise TypeError("every group unit must provide load_pylon")
        if replace and not isinstance(getattr(unit, "pylons", None), dict):
            raise TypeError("every group unit must expose a mutable pylons dictionary")

    for unit in units:
        if replace:
            unit.pylons.clear()
        for pylon, payload in normalized.items():
            unit.load_pylon((pylon, copy.deepcopy(payload)))
    return group


def validate_group_loadout(
    group: Any, expected_pylons: Mapping[Any, Any], *, exact: bool = True
) -> dict[str, Any]:
    """Compare every unit's stored CLSID/settings with an intended plan."""

    if not isinstance(exact, bool):
        raise TypeError("exact must be boolean")
    expected = normalize_loadout_plan(expected_pylons)
    expected_stored = {
        pylon: {
            "CLSID": payload["clsid"],
            **(
                {"settings": copy.deepcopy(payload["settings"])}
                if "settings" in payload
                else {}
            ),
        }
        for pylon, payload in expected.items()
    }
    units = list(getattr(group, "units", ()) or ())
    failures = []
    unit_results = []
    if not units:
        failures.append({"code": "EMPTY_GROUP", "message": "group contains no units"})

    for index, unit in enumerate(units):
        name = str(getattr(unit, "name", ""))
        raw_actual = getattr(unit, "pylons", None)
        if not isinstance(raw_actual, Mapping):
            failures.append(
                {
                    "code": "INVALID_UNIT_PYLONS",
                    "unit_index": index,
                    "unit_name": name,
                    "message": "unit pylons are not an object",
                }
            )
            continue
        actual = {}
        malformed = False
        for raw_pylon, value in raw_actual.items():
            try:
                pylon = _pylon_number(raw_pylon)
            except (TypeError, ValueError) as error:
                failures.append(
                    {
                        "code": "INVALID_STORED_PYLON",
                        "unit_index": index,
                        "unit_name": name,
                        "pylon": str(raw_pylon),
                        "message": str(error),
                    }
                )
                malformed = True
                continue
            actual[pylon] = copy.deepcopy(value)

        missing = sorted(set(expected_stored) - set(actual))
        unexpected = sorted(set(actual) - set(expected_stored)) if exact else []
        mismatched = []
        for pylon in sorted(set(expected_stored).intersection(actual)):
            if actual[pylon] != expected_stored[pylon]:
                mismatched.append(
                    {
                        "pylon": pylon,
                        "expected": expected_stored[pylon],
                        "actual": actual[pylon],
                    }
                )
        if missing:
            failures.append(
                {
                    "code": "MISSING_PYLONS",
                    "unit_index": index,
                    "unit_name": name,
                    "pylons": missing,
                    "message": "intended pylon entries are absent",
                }
            )
        if unexpected:
            failures.append(
                {
                    "code": "UNEXPECTED_PYLONS",
                    "unit_index": index,
                    "unit_name": name,
                    "pylons": unexpected,
                    "message": "unit contains pylon entries outside the exact plan",
                }
            )
        if mismatched:
            failures.append(
                {
                    "code": "PYLON_VALUE_MISMATCH",
                    "unit_index": index,
                    "unit_name": name,
                    "mismatches": mismatched,
                    "message": "CLSID or settings differ from the intended plan",
                }
            )
        unit_results.append(
            {
                "unit_index": index,
                "unit_name": name,
                "stored_pylon_count": len(actual),
                "matches": not malformed and not missing and not unexpected and not mismatched,
            }
        )

    return {
        "ok": not failures,
        "group_name": str(getattr(group, "name", "")),
        "unit_count": len(units),
        "exact": exact,
        "expected_pylons": expected_stored,
        "units": unit_results,
        "failures": failures,
    }
