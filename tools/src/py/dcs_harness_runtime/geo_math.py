"""Deterministic geographic, planar, and unit-conversion primitives."""

from __future__ import annotations

import math
from typing import Any, Mapping

from .result import ErrorCode, HarnessError


EARTH_MEAN_RADIUS_M = 6_371_008.8
METRES_PER_NAUTICAL_MILE = 1_852.0
METRES_PER_FOOT = 0.3048

UNIT_DEFINITIONS: dict[str, tuple[str, float]] = {
    "m": ("distance", 1.0),
    "km": ("distance", 1_000.0),
    "nm": ("distance", METRES_PER_NAUTICAL_MILE),
    "ft": ("distance", METRES_PER_FOOT),
    "m/s": ("speed", 1.0),
    "km/h": ("speed", 1_000.0 / 3_600.0),
    "knot": ("speed", METRES_PER_NAUTICAL_MILE / 3_600.0),
}

UNIT_ALIASES = {
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
    "kilometer": "km",
    "kilometers": "km",
    "kilometre": "km",
    "kilometres": "km",
    "nauticalmile": "nm",
    "nauticalmiles": "nm",
    "nmi": "nm",
    "foot": "ft",
    "feet": "ft",
    "mps": "m/s",
    "kmh": "km/h",
    "kph": "km/h",
    "kt": "knot",
    "kts": "knot",
    "knots": "knot",
}


def finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        valid = False
    else:
        try:
            value = float(value)
            valid = math.isfinite(value)
        except (TypeError, ValueError):
            valid = False
    if not valid:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            f"{name} must be a finite number.",
            details={"reason": "INVALID_NUMBER", "field": name},
        )
    return value


def geographic_point(value: Any, name: str = "point") -> tuple[float, float]:
    if not isinstance(value, Mapping):
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            f"{name} must be a geographic coordinate object.",
            details={"reason": "INVALID_COORDINATE", "field": name},
        )
    allowed = {"latitude_deg", "longitude_deg"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            f"{name} contains unsupported coordinate fields.",
            details={
                "reason": "INVALID_COORDINATE",
                "field": name,
                "unknown": unknown,
                "allowed": sorted(allowed),
            },
        )
    latitude = finite_number(value.get("latitude_deg"), f"{name}.latitude_deg")
    longitude = finite_number(
        value.get("longitude_deg"), f"{name}.longitude_deg"
    )
    if not -90.0 <= latitude <= 90.0:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            f"{name}.latitude_deg must be between -90 and 90.",
            details={"reason": "INVALID_COORDINATE", "field": name},
        )
    if not -180.0 <= longitude <= 180.0:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            f"{name}.longitude_deg must be between -180 and 180.",
            details={"reason": "INVALID_COORDINATE", "field": name},
        )
    return latitude, normalize_longitude(longitude)


def local_point(value: Any, name: str = "point") -> tuple[float, float]:
    if not isinstance(value, Mapping):
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            f"{name} must be a DCS local x/z coordinate object.",
            details={"reason": "INVALID_COORDINATE", "field": name},
        )
    allowed = {"x_m", "z_m"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            f"{name} contains unsupported coordinate fields.",
            details={
                "reason": "INVALID_COORDINATE",
                "field": name,
                "unknown": unknown,
                "allowed": sorted(allowed),
            },
        )
    return (
        finite_number(value.get("x_m"), f"{name}.x_m"),
        finite_number(value.get("z_m"), f"{name}.z_m"),
    )


def normalize_bearing(value: Any, name: str = "bearing_deg") -> float:
    return finite_number(value, name) % 360.0


def normalize_longitude(value: float) -> float:
    normalized = (value + 180.0) % 360.0 - 180.0
    return 0.0 if normalized == -0.0 else normalized


def geographic_distance_m(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    d_lat = lat2 - lat1
    d_lon = math.radians(normalize_longitude(math.degrees(lon2 - lon1)))
    haversine = (
        math.sin(d_lat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2.0) ** 2
    )
    return 2.0 * EARTH_MEAN_RADIUS_M * math.asin(
        min(1.0, math.sqrt(haversine))
    )


def geographic_initial_bearing_deg(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    if geographic_distance_m(first, second) < 1e-9:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            "Bearing is undefined for coincident geographic points.",
            details={"reason": "BEARING_UNDEFINED"},
        )
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    delta_lon = lon2 - lon1
    east = math.sin(delta_lon) * math.cos(lat2)
    north = (
        math.cos(lat1) * math.sin(lat2)
        - math.sin(lat1) * math.cos(lat2) * math.cos(delta_lon)
    )
    return math.degrees(math.atan2(east, north)) % 360.0


def geographic_offset(
    origin: tuple[float, float], bearing_deg: float, distance_m: float
) -> tuple[float, float]:
    if distance_m < 0.0:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            "Offset distance must not be negative.",
            details={"reason": "INVALID_DISTANCE"},
        )
    latitude, longitude = map(math.radians, origin)
    bearing = math.radians(normalize_bearing(bearing_deg))
    angular_distance = distance_m / EARTH_MEAN_RADIUS_M
    destination_latitude = math.asin(
        math.sin(latitude) * math.cos(angular_distance)
        + math.cos(latitude) * math.sin(angular_distance) * math.cos(bearing)
    )
    destination_longitude = longitude + math.atan2(
        math.sin(bearing) * math.sin(angular_distance) * math.cos(latitude),
        math.cos(angular_distance)
        - math.sin(latitude) * math.sin(destination_latitude),
    )
    return (
        math.degrees(destination_latitude),
        normalize_longitude(math.degrees(destination_longitude)),
    )


def local_distance_m(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    return math.hypot(second[0] - first[0], second[1] - first[1])


def local_bearing_deg(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    delta_north = second[0] - first[0]
    delta_east = second[1] - first[1]
    if math.hypot(delta_north, delta_east) < 1e-9:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            "Bearing is undefined for coincident local points.",
            details={"reason": "BEARING_UNDEFINED"},
        )
    return math.degrees(math.atan2(delta_east, delta_north)) % 360.0


def canonical_unit(value: Any, name: str = "unit") -> str:
    if not isinstance(value, str) or not value.strip():
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            f"{name} must be a supported unit string.",
            details={"reason": "UNSUPPORTED_UNIT", "field": name},
        )
    compact = value.strip().casefold().replace(" ", "")
    unit = UNIT_ALIASES.get(compact, compact)
    if unit not in UNIT_DEFINITIONS:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            f"Unsupported unit: {value!r}.",
            details={
                "reason": "UNSUPPORTED_UNIT",
                "field": name,
                "supported": sorted(UNIT_DEFINITIONS),
            },
        )
    return unit


def convert_unit(value: Any, from_unit: Any, to_unit: Any) -> dict[str, Any]:
    number = finite_number(value, "value")
    source = canonical_unit(from_unit, "from_unit")
    target = canonical_unit(to_unit, "to_unit")
    source_dimension, source_factor = UNIT_DEFINITIONS[source]
    target_dimension, target_factor = UNIT_DEFINITIONS[target]
    if source_dimension != target_dimension:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            "Units from different dimensions cannot be converted.",
            details={
                "reason": "UNIT_DIMENSION_MISMATCH",
                "from_unit": source,
                "to_unit": target,
            },
        )
    return {
        "quantity": source_dimension,
        "input": {"value": number, "unit": source},
        "output": {
            "value": number / target_factor * source_factor,
            "unit": target,
        },
    }


def distance_input_m(value: Any, name: str = "distance") -> float:
    if not isinstance(value, Mapping):
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            f"{name} must contain value and unit.",
            details={"reason": "INVALID_DISTANCE", "field": name},
        )
    unknown = sorted(set(value) - {"value", "unit"})
    if unknown:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            f"{name} contains unsupported fields.",
            details={"reason": "INVALID_DISTANCE", "unknown": unknown},
        )
    converted = convert_unit(value.get("value"), value.get("unit"), "m")
    if converted["quantity"] != "distance":
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            f"{name} must use a distance unit.",
            details={"reason": "INVALID_DISTANCE"},
        )
    distance = float(converted["output"]["value"])
    if distance < 0.0:
        raise HarnessError(
            ErrorCode.INVALID_ARGUMENT,
            f"{name} must not be negative.",
            details={"reason": "INVALID_DISTANCE"},
        )
    return distance


def distance_result(distance_m: float) -> dict[str, float]:
    return {
        "distance_m": distance_m,
        "distance_km": distance_m / 1_000.0,
        "distance_nm": distance_m / METRES_PER_NAUTICAL_MILE,
    }
