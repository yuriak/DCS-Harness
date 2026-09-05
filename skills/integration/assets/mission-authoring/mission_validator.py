"""Task-local structural validator for a Human-approved pydcs mission.

Copy this file beside the current authorer under runtime/workspace/ and supply
only the invariants material to that mission. The specification dictionaries
below are inputs to this helper, not a Harness Mission Contract schema.
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zipfile import BadZipFile, ZipFile


START_POINT_TYPES = {
    "cold": "TakeOffParking",
    "warm": "TakeOffParkingHot",
    "runway": "TakeOff",
    "ground_cold": "TakeOffGround",
    "ground_hot": "TakeOffGroundHot",
    "in_flight": "Turning Point",
}


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _nonnegative(value: Any, label: str) -> float:
    result = _finite(value, label)
    if result < 0:
        raise ValueError(f"{label} must be non-negative")
    return result


def _point_xy(point: Any, label: str) -> tuple[float, float]:
    position = getattr(point, "position", point)
    if isinstance(position, Mapping):
        try:
            return _finite(position["x"], f"{label}.x"), _finite(
                position["y"], f"{label}.y"
            )
        except KeyError as error:
            raise TypeError(f"{label} must contain x and y coordinates") from error
    try:
        return _finite(position.x, f"{label}.x"), _finite(
            position.y, f"{label}.y"
        )
    except AttributeError as error:
        raise TypeError(f"{label} must expose x and y coordinates") from error


def _failure(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **details}


def _unit_type(unit: Any) -> str:
    value = getattr(unit, "type", None)
    if value is None:
        value = getattr(getattr(unit, "unit_type", None), "id", "")
    return str(value)


def _find_group(mission: Any, name: str) -> Any:
    finder = getattr(mission, "find_group", None)
    if not callable(finder):
        raise TypeError("mission must provide find_group(name, search)")
    return finder(name, "exact")


def _expected_type_counts(spec: Mapping[str, Any]) -> Optional[Counter[str]]:
    if "unit_types" not in spec:
        return None
    raw = spec["unit_types"]
    if isinstance(raw, Mapping):
        result: Counter[str] = Counter()
        for type_id, count in raw.items():
            if not isinstance(type_id, str) or not type_id:
                raise ValueError("unit_types keys must be non-empty type IDs")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("unit_types counts must be non-negative integers")
            result[type_id] = count
        return result
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        if any(not isinstance(item, str) or not item for item in raw):
            raise ValueError("unit_types entries must be non-empty type IDs")
        return Counter(raw)
    raise TypeError("unit_types must be a type/count object or a list of type IDs")


def _validate_units(group: Any, spec: Mapping[str, Any]) -> tuple[list[dict], list[dict]]:
    failures: list[dict] = []
    units = list(getattr(group, "units", ()) or ())
    expected_count = spec.get("unit_count")
    if expected_count is not None:
        if isinstance(expected_count, bool) or not isinstance(expected_count, int):
            raise TypeError("unit_count must be an integer")
        if len(units) != expected_count:
            failures.append(
                _failure(
                    "UNIT_COUNT_MISMATCH",
                    "group unit count differs from the task contract",
                    expected=expected_count,
                    actual=len(units),
                )
            )

    expected_types = _expected_type_counts(spec)
    actual_types = Counter(_unit_type(unit) for unit in units)
    if expected_types is not None and actual_types != expected_types:
        failures.append(
            _failure(
                "UNIT_TYPES_MISMATCH",
                "group unit type counts differ from the task contract",
                expected=dict(sorted(expected_types.items())),
                actual=dict(sorted(actual_types.items())),
            )
        )

    coordinates: list[dict] = []
    origin_limit = spec.get("suspicious_origin_radius_m", 1.0)
    if origin_limit is not None:
        origin_limit = _nonnegative(origin_limit, "suspicious_origin_radius_m")
    for index, unit in enumerate(units):
        name = str(getattr(unit, "name", ""))
        try:
            x, y = _point_xy(getattr(unit, "position", None), f"unit[{index}]")
        except (TypeError, ValueError) as error:
            failures.append(
                _failure(
                    "INVALID_UNIT_POSITION",
                    str(error),
                    unit_index=index,
                    unit_name=name,
                )
            )
            continue
        coordinates.append({"unit_index": index, "unit_name": name, "x": x, "y": y})
        distance = math.hypot(x, y)
        if origin_limit is not None and distance <= origin_limit:
            failures.append(
                _failure(
                    "SUSPICIOUS_WORLD_ORIGIN",
                    "unit is at or suspiciously near local world origin",
                    unit_index=index,
                    unit_name=name,
                    distance_from_origin_m=distance,
                    limit_m=origin_limit,
                )
            )
    return failures, coordinates


def _validate_geometry(coordinates: Sequence[Mapping[str, Any]], spec: Mapping[str, Any]) -> tuple[list[dict], dict]:
    failures: list[dict] = []
    if not coordinates:
        return failures, {"formation_radius_m": None, "max_anchor_distance_m": None}

    anchor = spec.get("anchor")
    if anchor is None:
        anchor_x = float(coordinates[0]["x"])
        anchor_y = float(coordinates[0]["y"])
        anchor_source = "lead_unit"
    else:
        anchor_x, anchor_y = _point_xy(anchor, "anchor")
        anchor_source = "supplied"

    anchor_distances = [
        math.hypot(float(item["x"]) - anchor_x, float(item["y"]) - anchor_y)
        for item in coordinates
    ]
    max_anchor = spec.get("max_anchor_distance_m")
    if max_anchor is not None:
        max_anchor = _nonnegative(max_anchor, "max_anchor_distance_m")
        for item, distance in zip(coordinates, anchor_distances):
            if distance > max_anchor:
                failures.append(
                    _failure(
                        "ANCHOR_DISTANCE_EXCEEDED",
                        "unit exceeds the task-supplied anchor distance",
                        unit_index=item["unit_index"],
                        unit_name=item["unit_name"],
                        distance_m=distance,
                        limit_m=max_anchor,
                    )
                )

    centroid_x = sum(float(item["x"]) for item in coordinates) / len(coordinates)
    centroid_y = sum(float(item["y"]) for item in coordinates) / len(coordinates)
    radius = max(
        math.hypot(float(item["x"]) - centroid_x, float(item["y"]) - centroid_y)
        for item in coordinates
    )
    max_radius = spec.get("max_formation_radius_m")
    if max_radius is not None:
        max_radius = _nonnegative(max_radius, "max_formation_radius_m")
        if radius > max_radius:
            failures.append(
                _failure(
                    "FORMATION_EXTENT_EXCEEDED",
                    "formation radius exceeds the task-supplied limit",
                    radius_m=radius,
                    limit_m=max_radius,
                )
            )
    return failures, {
        "anchor": {"x": anchor_x, "y": anchor_y, "source": anchor_source},
        "max_anchor_distance_m": max(anchor_distances),
        "centroid": {"x": centroid_x, "y": centroid_y},
        "formation_radius_m": radius,
    }


def validate_ground_group(mission: Any, spec: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one ground group's identity, every unit position, and extent."""

    name = spec.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("ground group name must be a non-empty string")
    group = _find_group(mission, name)
    if group is None:
        failure = _failure("GROUP_NOT_FOUND", "expected ground group is absent", group=name)
        return {"ok": False, "group": name, "failures": [failure]}
    failures, coordinates = _validate_units(group, spec)
    geometry_failures, geometry = _validate_geometry(coordinates, spec)
    failures.extend(geometry_failures)
    return {
        "ok": not failures,
        "group": name,
        "category": "ground",
        "unit_count": len(list(getattr(group, "units", ()) or ())),
        "unit_positions": coordinates,
        "geometry": geometry,
        "failures": failures,
    }


def _validate_range(value: Any, spec: Mapping[str, Any], label: str) -> list[dict]:
    if not isinstance(spec, Mapping):
        raise TypeError(f"{label} range must be a min/max object")
    failures: list[dict] = []
    actual = _finite(value, label)
    minimum = (
        _finite(spec["min"], f"{label}.min") if "min" in spec else None
    )
    maximum = (
        _finite(spec["max"], f"{label}.max") if "max" in spec else None
    )
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError(f"{label} range minimum must not exceed maximum")
    if minimum is not None and actual < minimum:
        failures.append(_failure("VALUE_BELOW_MINIMUM", f"{label} is below its task-supplied minimum", field=label, actual=actual, minimum=minimum))
    if maximum is not None and actual > maximum:
        failures.append(_failure("VALUE_ABOVE_MAXIMUM", f"{label} exceeds its task-supplied maximum", field=label, actual=actual, maximum=maximum))
    return failures


def _normalize_expected_payload(raw: Mapping[Any, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for key, value in raw.items():
        if isinstance(key, bool) or not str(key).isascii() or not str(key).isdigit():
            raise TypeError("payload keys must be positive pylon integers")
        pylon = int(key)
        if pylon < 1 or pylon in result:
            raise ValueError("payload keys must be unique positive pylon integers")
        if isinstance(value, str):
            stored = {"CLSID": value}
        elif isinstance(value, Mapping):
            clsid = value.get("clsid")
            if not isinstance(clsid, str) or not clsid:
                raise ValueError("payload clsid must be a non-empty string")
            stored = {"CLSID": clsid}
            if "settings" in value:
                stored["settings"] = value["settings"]
        else:
            raise TypeError("payload values must be CLSID strings or objects")
        result[pylon] = stored
    return result


def validate_aircraft_group(mission: Any, spec: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one aircraft group's structural route and unit configuration."""

    name = spec.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("aircraft group name must be a non-empty string")
    group = _find_group(mission, name)
    if group is None:
        failure = _failure("GROUP_NOT_FOUND", "expected aircraft group is absent", group=name)
        return {"ok": False, "group": name, "failures": [failure]}

    failures, coordinates = _validate_units(group, spec)
    units = list(getattr(group, "units", ()) or ())
    expected_payload = spec.get("payload")
    if expected_payload is not None:
        if not isinstance(expected_payload, Mapping):
            raise TypeError("payload must be an object keyed by pylon")
        normalized_payload = _normalize_expected_payload(expected_payload)
        for index, unit in enumerate(units):
            actual = dict(getattr(unit, "pylons", {}) or {})
            if actual != normalized_payload:
                failures.append(_failure("PAYLOAD_MISMATCH", "stored pylon payload differs from the task contract", unit_index=index, unit_name=str(getattr(unit, "name", "")), expected=normalized_payload, actual=actual))

    fuel_spec = spec.get("fuel")
    if fuel_spec is not None and not isinstance(fuel_spec, Mapping):
        raise TypeError("fuel must be a min/max object")
    fuel_values = []
    for index, unit in enumerate(units):
        try:
            fuel = _finite(getattr(unit, "fuel", None), f"unit[{index}].fuel")
            fuel_values.append(fuel)
            if fuel_spec is not None:
                failures.extend(_validate_range(fuel, fuel_spec, f"unit[{index}].fuel"))
        except (TypeError, ValueError) as error:
            failures.append(_failure("INVALID_FUEL", str(error), unit_index=index, unit_name=str(getattr(unit, "name", ""))))

    points = list(getattr(group, "points", ()) or ())
    route_spec = spec.get("route", {})
    if not isinstance(route_spec, Mapping):
        raise TypeError("route must be an object")
    exact_points = route_spec.get("point_count")
    min_points = route_spec.get("min_point_count")
    max_points = route_spec.get("max_point_count")
    for label, value in (
        ("point_count", exact_points),
        ("min_point_count", min_points),
        ("max_point_count", max_points),
    ):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise ValueError(f"route.{label} must be a non-negative integer")
    if exact_points is not None and len(points) != exact_points:
        failures.append(_failure("ROUTE_POINT_COUNT_MISMATCH", "route point count differs from the task contract", expected=exact_points, actual=len(points)))
    if min_points is not None and len(points) < min_points:
        failures.append(_failure("ROUTE_TOO_SHORT", "route has fewer points than required", minimum=min_points, actual=len(points)))
    if max_points is not None and len(points) > max_points:
        failures.append(_failure("ROUTE_TOO_LONG", "route has more points than allowed", maximum=max_points, actual=len(points)))

    origin_limit = route_spec.get("suspicious_origin_radius_m", 1.0)
    if origin_limit is not None:
        origin_limit = _nonnegative(origin_limit, "route.suspicious_origin_radius_m")
    point_results = []
    for index, point in enumerate(points):
        try:
            x, y = _point_xy(point, f"route[{index}]")
            altitude = _finite(getattr(point, "alt", None), f"route[{index}].altitude")
            speed = _finite(getattr(point, "speed", None), f"route[{index}].speed")
        except (TypeError, ValueError) as error:
            failures.append(_failure("INVALID_ROUTE_POINT", str(error), point_index=index))
            continue
        point_results.append({"index": index, "name": str(getattr(point, "name", "")), "type": str(getattr(point, "type", "")), "x": x, "y": y, "altitude_m": altitude, "speed_mps": speed})
        if origin_limit is not None and math.hypot(x, y) <= origin_limit:
            failures.append(_failure("SUSPICIOUS_ROUTE_ORIGIN", "route point is at or suspiciously near local world origin", point_index=index, distance_from_origin_m=math.hypot(x, y), limit_m=origin_limit))
        if "altitude" in route_spec:
            failures.extend(_validate_range(altitude, route_spec["altitude"], f"route[{index}].altitude_m"))
        if "speed" in route_spec:
            failures.extend(_validate_range(speed, route_spec["speed"], f"route[{index}].speed_mps"))

    late_expected = spec.get("late_activation")
    if late_expected is not None and not isinstance(late_expected, bool):
        raise TypeError("late_activation must be boolean")
    if late_expected is not None and bool(getattr(group, "late_activation", False)) != late_expected:
        failures.append(_failure("LATE_ACTIVATION_MISMATCH", "late-activation state differs from the task contract", expected=late_expected, actual=bool(getattr(group, "late_activation", False))))

    first = points[0] if points else None
    start_mode = spec.get("start_mode")
    if start_mode is not None:
        if start_mode not in START_POINT_TYPES:
            raise ValueError(f"unsupported start_mode {start_mode!r}")
        actual_type = getattr(first, "type", None)
        if actual_type != START_POINT_TYPES[start_mode]:
            failures.append(_failure("START_MODE_MISMATCH", "first route point does not match the intended start mode", expected=start_mode, expected_point_type=START_POINT_TYPES[start_mode], actual_point_type=actual_type))

    airport_id = getattr(first, "airdrome_id", None)
    expected_airport = spec.get("home_base")
    airport_name = None
    if airport_id is not None:
        airport = mission.terrain.airport_by_id(airport_id)
        airport_name = str(getattr(airport, "name", "")) if airport else None
    if expected_airport is not None:
        if isinstance(expected_airport, int):
            matches_airport = airport_id == expected_airport
        elif isinstance(expected_airport, str):
            matches_airport = airport_name == expected_airport
        else:
            raise TypeError("home_base must be an airport name or numeric ID")
        if not matches_airport:
            failures.append(_failure("HOME_BASE_MISMATCH", "first route point airport differs from the task contract", expected=expected_airport, actual_id=airport_id, actual_name=airport_name))

    expected_task = spec.get("task")
    actual_task = getattr(group, "task", None)
    if expected_task is not None and actual_task != expected_task:
        failures.append(_failure("ROLE_TASK_MISMATCH", "inspectable group task differs from the task contract", expected=expected_task, actual=actual_task))

    return {
        "ok": not failures,
        "group": name,
        "category": "aircraft",
        "unit_count": len(units),
        "unit_positions": coordinates,
        "fuel": fuel_values,
        "route_points": point_results,
        "late_activation": bool(getattr(group, "late_activation", False)),
        "start_mode": start_mode,
        "home_base": {"id": airport_id, "name": airport_name},
        "task": actual_task,
        "failures": failures,
    }


def validate_geography(mission: Any, specs: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Validate explicit task-supplied points against areas or airports."""

    results = []
    for spec in specs:
        label = spec.get("label")
        if not isinstance(label, str) or not label:
            raise ValueError("geography label must be a non-empty string")
        failures = []
        try:
            x, y = _point_xy(spec.get("point"), label)
        except (TypeError, ValueError) as error:
            results.append({"ok": False, "label": label, "failures": [_failure("INVALID_GEOGRAPHY_POINT", str(error))]})
            continue
        origin_limit = spec.get("suspicious_origin_radius_m", 1.0)
        if origin_limit is not None and math.hypot(x, y) <= _nonnegative(origin_limit, "suspicious_origin_radius_m"):
            failures.append(_failure("SUSPICIOUS_WORLD_ORIGIN", "geography point is at or suspiciously near local world origin", distance_from_origin_m=math.hypot(x, y), limit_m=float(origin_limit)))
        intended = spec.get("intended_area")
        if intended is not None:
            if not isinstance(intended, Mapping):
                raise TypeError("intended_area must be an object")
            ax, ay = _point_xy(intended, f"{label}.intended_area")
            limit = _nonnegative(intended.get("max_distance_m"), "intended_area.max_distance_m")
            distance = math.hypot(x - ax, y - ay)
            if distance > limit:
                failures.append(_failure("INTENDED_AREA_DISTANCE_EXCEEDED", "point lies outside the task-supplied intended area", distance_m=distance, limit_m=limit))
        expected_airport = spec.get("expected_airport")
        if expected_airport is not None:
            airport = mission.terrain.airports.get(expected_airport) if isinstance(expected_airport, str) else mission.terrain.airport_by_id(expected_airport)
            if airport is None:
                failures.append(_failure("EXPECTED_AIRPORT_NOT_FOUND", "expected airport is absent from the mission terrain", expected=expected_airport))
            else:
                limit = _nonnegative(spec.get("max_airport_distance_m"), "max_airport_distance_m")
                ax, ay = _point_xy(airport.position, f"airport {expected_airport}")
                distance = math.hypot(x - ax, y - ay)
                if distance > limit:
                    failures.append(_failure("AIRPORT_DISTANCE_EXCEEDED", "point lies too far from the expected airport", expected=expected_airport, distance_m=distance, limit_m=limit))
        results.append({"ok": not failures, "label": label, "point": {"x": x, "y": y}, "failures": failures})
    return results


def validate_startup(mission: Any, spec: Mapping[str, Any], *, miz_path: Optional[Path] = None) -> dict[str, Any]:
    """Validate ordered Mission Start DoScriptFile resources and compiled text."""

    expected = spec.get("resources", ())
    if not isinstance(expected, Sequence) or isinstance(expected, (str, bytes)):
        raise TypeError("startup resources must be an ordered list of basenames")
    if any(not isinstance(item, str) or not item for item in expected):
        raise ValueError("startup resource names must be non-empty strings")
    failures = []
    triggers = [trigger for trigger in getattr(mission.triggerrules, "triggers", ()) if getattr(trigger, "predicate", "") == "triggerStart"]
    trigger_index = spec.get("trigger_index", 0)
    if isinstance(trigger_index, bool) or not isinstance(trigger_index, int) or trigger_index < 0:
        raise ValueError("startup trigger_index must be a non-negative integer")
    if trigger_index >= len(triggers):
        return {"ok": False, "resources": [], "failures": [_failure("MISSION_START_TRIGGER_NOT_FOUND", "expected Mission Start trigger is absent", trigger_index=trigger_index)]}
    trigger = triggers[trigger_index]
    actions = list(getattr(trigger, "actions", ()) or ())
    resource_files = mission.map_resource.files.get("DEFAULT", {})
    actual = []
    keys = []
    for index, action in enumerate(actions):
        predicate = getattr(action, "predicate", None)
        if predicate != "a_do_script_file":
            failures.append(_failure("STARTUP_ACTION_NOT_SCRIPT_FILE", "startup action is not resource-backed DoScriptFile", action_index=index, predicate=predicate))
            continue
        key = getattr(getattr(action, "file_res_key", None), "key", None)
        keys.append(key)
        path = resource_files.get(key)
        if path is None:
            failures.append(_failure("STARTUP_RESOURCE_KEY_NOT_FOUND", "DoScriptFile key is absent from mapResource", action_index=index, resource_key=key))
            actual.append(None)
        else:
            actual.append(Path(path).name)
    if actual != list(expected):
        failures.append(_failure("STARTUP_RESOURCE_ORDER_MISMATCH", "ordered startup resources differ from the task contract", expected=list(expected), actual=actual))

    compiled_checked = False
    if miz_path is not None:
        compiled_checked = True
        try:
            with ZipFile(miz_path) as archive:
                mission_text = archive.read("mission").decode("utf-8")
                archive_names = set(archive.namelist())
        except (BadZipFile, KeyError, OSError, UnicodeDecodeError) as error:
            failures.append(
                _failure(
                    "MIZ_ARCHIVE_UNREADABLE",
                    "could not inspect the compiled mission archive",
                    detail=str(error),
                )
            )
        else:
            for index, (key, basename) in enumerate(zip(keys, actual)):
                expressions = (
                    f'a_do_script_file(getValueResourceByKey("{key}"))',
                    f'a_do_script_file(getValueResourceByKey(\\"{key}\\"))',
                )
                if key is None or not any(
                    expression in mission_text for expression in expressions
                ):
                    failures.append(_failure("COMPILED_STARTUP_ACTION_MISSING", "compiled mission trigger does not contain the expected resource-backed action", action_index=index, resource_key=key))
                if basename is not None and f"l10n/DEFAULT/{basename}" not in archive_names:
                    failures.append(_failure("EMBEDDED_STARTUP_RESOURCE_MISSING", "mapResource target is absent from the mission archive", action_index=index, resource_key=key, basename=basename))
    return {"ok": not failures, "resources": actual, "resource_keys": keys, "compiled_checked": compiled_checked, "failures": failures}


def validate_mission_structure(
    mission: Any,
    *,
    ground_groups: Sequence[Mapping[str, Any]] = (),
    aircraft_groups: Sequence[Mapping[str, Any]] = (),
    geography: Sequence[Mapping[str, Any]] = (),
    startup: Optional[Mapping[str, Any]] = None,
    miz_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Run only the structural checks selected by the task's Mission Contract."""

    ground = [validate_ground_group(mission, spec) for spec in ground_groups]
    aircraft = [validate_aircraft_group(mission, spec) for spec in aircraft_groups]
    geo = validate_geography(mission, geography)
    startup_result = validate_startup(mission, startup, miz_path=miz_path) if startup is not None else None
    sections = [*ground, *aircraft, *geo]
    if startup_result is not None:
        sections.append(startup_result)
    failure_count = sum(len(section["failures"]) for section in sections)
    return {
        "ok": failure_count == 0,
        "summary": {
            "ground_groups": len(ground),
            "aircraft_groups": len(aircraft),
            "geography_points": len(geo),
            "startup_checked": startup_result is not None,
            "failure_count": failure_count,
        },
        "ground_groups": ground,
        "aircraft_groups": aircraft,
        "geography": geo,
        "startup": startup_result,
    }
