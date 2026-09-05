"""Task-local pydcs helpers for explicit multi-unit ground geometry.

Copy this file into the current runtime/workspace task and adapt the caller's
offsets and validation thresholds to that mission. It deliberately contains
no battery layouts, role doctrine, or scenario defaults.
"""

from __future__ import annotations

import math
from typing import Any, Optional, Sequence, Tuple


Offset = Tuple[float, float]


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _nonnegative_optional(value: Optional[float], label: str) -> Optional[float]:
    if value is None:
        return None
    result = _finite_number(value, label)
    if result < 0:
        raise ValueError(f"{label} must be non-negative")
    return result


def _xy(point: Any, label: str) -> Tuple[float, float]:
    try:
        x = point.x
        y = point.y
    except AttributeError as exc:
        raise TypeError(f"{label} must expose x and y coordinates") from exc
    return _finite_number(x, f"{label}.x"), _finite_number(y, f"{label}.y")


def place_group_units(
    group: Any,
    anchor: Any,
    offsets_m: Sequence[Offset],
    *,
    headings_deg: Optional[Sequence[float]] = None,
) -> Any:
    """Assign every unit an explicit point relative to ``anchor``.

    Offsets use pydcs route axes: ``(x/east metres, y/north metres)``. Include
    an offset for the lead unit, normally ``(0, 0)``. The exact number of
    offsets must match the exact number of group units so newly appended units
    cannot silently retain pydcs's default ``(0, 0)``.
    """

    units = list(getattr(group, "units", ()) or ())
    if not units:
        raise ValueError("group must contain at least one unit")
    if len(offsets_m) != len(units):
        raise ValueError(
            f"offset count {len(offsets_m)} does not match unit count {len(units)}"
        )
    if headings_deg is not None and len(headings_deg) != len(units):
        raise ValueError(
            f"heading count {len(headings_deg)} does not match unit count {len(units)}"
        )

    anchor_x, anchor_y = _xy(anchor, "anchor")
    point_factory = getattr(anchor, "new_in_same_map", None)
    if not callable(point_factory):
        raise TypeError("anchor must provide new_in_same_map(x, y)")

    placements = []
    for index, offset in enumerate(offsets_m):
        if not isinstance(offset, (tuple, list)) or len(offset) != 2:
            raise TypeError(f"offsets_m[{index}] must be a two-item sequence")
        dx = _finite_number(offset[0], f"offsets_m[{index}][0]")
        dy = _finite_number(offset[1], f"offsets_m[{index}][1]")
        heading = None
        if headings_deg is not None:
            heading = _finite_number(headings_deg[index], f"headings_deg[{index}]")
        placements.append((point_factory(anchor_x + dx, anchor_y + dy), heading))

    # Validate the complete request before mutating any pydcs unit.
    for unit, (position, heading) in zip(units, placements):
        unit.position = position
        if heading is not None:
            unit.heading = heading % 360.0
    return group


def validate_group_geometry(
    group: Any,
    *,
    anchor: Optional[Any] = None,
    max_anchor_distance_m: Optional[float] = None,
    min_pairwise_separation_m: Optional[float] = None,
    max_formation_radius_m: Optional[float] = None,
    suspicious_origin_radius_m: Optional[float] = 1.0,
) -> dict:
    """Return a JSON-safe structural report for one pydcs ground group.

    ``anchor`` defaults to the lead unit position. Distance thresholds are
    task-supplied contracts; ``None`` records the metric without enforcing it.
    Set ``suspicious_origin_radius_m`` to ``None`` only when the mission
    intentionally uses the local world origin.
    """

    max_anchor_distance_m = _nonnegative_optional(
        max_anchor_distance_m, "max_anchor_distance_m"
    )
    min_pairwise_separation_m = _nonnegative_optional(
        min_pairwise_separation_m, "min_pairwise_separation_m"
    )
    max_formation_radius_m = _nonnegative_optional(
        max_formation_radius_m, "max_formation_radius_m"
    )
    suspicious_origin_radius_m = _nonnegative_optional(
        suspicious_origin_radius_m, "suspicious_origin_radius_m"
    )

    units = list(getattr(group, "units", ()) or ())
    group_name = str(getattr(group, "name", ""))
    failures = []
    coordinates = []

    if not units:
        failures.append(
            {"code": "EMPTY_GROUP", "message": "group contains no units"}
        )

    for index, unit in enumerate(units):
        unit_name = str(getattr(unit, "name", ""))
        position = getattr(unit, "position", None)
        try:
            x, y = _xy(position, f"unit[{index}].position")
        except (TypeError, ValueError) as exc:
            failures.append(
                {
                    "code": "NONFINITE_UNIT_POSITION",
                    "unit_index": index,
                    "unit_name": unit_name,
                    "message": str(exc),
                }
            )
            continue
        coordinates.append((index, unit_name, x, y))
        origin_distance = math.hypot(x, y)
        if (
            suspicious_origin_radius_m is not None
            and origin_distance <= suspicious_origin_radius_m
        ):
            failures.append(
                {
                    "code": "SUSPICIOUS_WORLD_ORIGIN",
                    "unit_index": index,
                    "unit_name": unit_name,
                    "distance_from_origin_m": origin_distance,
                    "message": "unit is at or suspiciously near local world origin",
                }
            )

    anchor_source = "supplied"
    if anchor is None and coordinates:
        anchor_source = "lead_unit"
        anchor_x, anchor_y = coordinates[0][2], coordinates[0][3]
    elif anchor is not None:
        anchor_x, anchor_y = _xy(anchor, "anchor")
    else:
        anchor_x = anchor_y = None

    anchor_distances = []
    if anchor_x is not None and anchor_y is not None:
        for index, unit_name, x, y in coordinates:
            distance = math.hypot(x - anchor_x, y - anchor_y)
            anchor_distances.append(distance)
            if max_anchor_distance_m is not None and distance > max_anchor_distance_m:
                failures.append(
                    {
                        "code": "ANCHOR_DISTANCE_EXCEEDED",
                        "unit_index": index,
                        "unit_name": unit_name,
                        "distance_m": distance,
                        "limit_m": max_anchor_distance_m,
                        "message": "unit exceeds the task-supplied anchor distance",
                    }
                )

    pairwise = []
    for left_index in range(len(coordinates)):
        li, lname, lx, ly = coordinates[left_index]
        for right_index in range(left_index + 1, len(coordinates)):
            ri, rname, rx, ry = coordinates[right_index]
            distance = math.hypot(rx - lx, ry - ly)
            pairwise.append(distance)
            if (
                min_pairwise_separation_m is not None
                and distance < min_pairwise_separation_m
            ):
                failures.append(
                    {
                        "code": "PAIRWISE_SEPARATION_BELOW_MINIMUM",
                        "unit_indexes": [li, ri],
                        "unit_names": [lname, rname],
                        "distance_m": distance,
                        "limit_m": min_pairwise_separation_m,
                        "message": "units violate the task-supplied minimum separation",
                    }
                )

    formation_radius = None
    centroid = None
    if coordinates:
        centroid_x = sum(item[2] for item in coordinates) / len(coordinates)
        centroid_y = sum(item[3] for item in coordinates) / len(coordinates)
        centroid = {"x": centroid_x, "y": centroid_y}
        formation_radius = max(
            math.hypot(x - centroid_x, y - centroid_y)
            for _, _, x, y in coordinates
        )
        if (
            max_formation_radius_m is not None
            and formation_radius > max_formation_radius_m
        ):
            failures.append(
                {
                    "code": "FORMATION_RADIUS_EXCEEDED",
                    "radius_m": formation_radius,
                    "limit_m": max_formation_radius_m,
                    "message": "formation exceeds the task-supplied bounding radius",
                }
            )

    return {
        "ok": not failures,
        "group_name": group_name,
        "unit_count": len(units),
        "valid_position_count": len(coordinates),
        "anchor": (
            {"x": anchor_x, "y": anchor_y, "source": anchor_source}
            if anchor_x is not None and anchor_y is not None
            else None
        ),
        "metrics": {
            "max_anchor_distance_m": max(anchor_distances) if anchor_distances else None,
            "min_pairwise_separation_m": min(pairwise) if pairwise else None,
            "formation_centroid": centroid,
            "formation_bounding_radius_m": formation_radius,
        },
        "contract": {
            "max_anchor_distance_m": max_anchor_distance_m,
            "min_pairwise_separation_m": min_pairwise_separation_m,
            "max_formation_radius_m": max_formation_radius_m,
            "suspicious_origin_radius_m": suspicious_origin_radius_m,
        },
        "failures": failures,
    }
