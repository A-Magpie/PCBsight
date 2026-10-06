"""STEP geometry helpers for PCB embedded model authoring."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from math import cos, radians, sin
from pathlib import Path
from typing import cast

MM_TO_MILS = 1000.0 / 25.4


@dataclass(frozen=True)
class PcbStepModelBounds:
    """
    Axis-aligned STEP model bounds converted to PCB public mil units.

    `bounds_mils` is the footprint-plane XY projection after model rotations
    and X/Y placement are applied. `overall_height_mils` follows Altium's 3D
    Body field and stores the transformed model `zmax` above the board plane.
    """

    bounds_mils: tuple[float, float, float, float]
    overall_height_mils: float
    min_z_mils: float
    max_z_mils: float


def _coerce_location_mils(location_mils: tuple[float, float]) -> tuple[float, float]:
    if len(location_mils) != 2:
        raise ValueError("location_mils must contain exactly two values")
    return float(location_mils[0]), float(location_mils[1])


def _sanitize_step_filename(filename_hint: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(filename_hint or "").strip())
    if not name:
        return "model.step"
    if not name.lower().endswith((".step", ".stp")):
        return f"{name}.step"
    return name


Matrix3 = tuple[
    tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]
]
Matrix4 = tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]


class _GeometerBoundsUnavailable(RuntimeError):
    pass


def _load_geometer_model_bounds() -> Callable[..., object]:
    try:
        import geometer
    except ImportError as exc:
        raise _GeometerBoundsUnavailable(
            "STEP bounds inference requires wn-geometer, which is an "
            "altium-monkey runtime dependency."
        ) from exc

    model_bounds = getattr(geometer, "model_bounds", None)
    if not callable(model_bounds):
        raise _GeometerBoundsUnavailable(
            "STEP bounds inference requires a wn-geometer build with "
            "model_bounds support."
        )
    return model_bounds


def _matrix3_multiply(left: Matrix3, right: Matrix3) -> Matrix3:
    return cast(
        Matrix3,
        tuple(
            tuple(
                sum(left[row][index] * right[index][column] for index in range(3))
                for column in range(3)
            )
            for row in range(3)
        ),
    )


def _rotation_x_matrix(degrees: float) -> Matrix3:
    angle = radians(float(degrees))
    cosine = cos(angle)
    sine = sin(angle)
    return ((1.0, 0.0, 0.0), (0.0, cosine, -sine), (0.0, sine, cosine))


def _rotation_y_matrix(degrees: float) -> Matrix3:
    angle = radians(float(degrees))
    cosine = cos(angle)
    sine = sin(angle)
    return ((cosine, 0.0, sine), (0.0, 1.0, 0.0), (-sine, 0.0, cosine))


def _rotation_z_matrix(degrees: float) -> Matrix3:
    angle = radians(float(degrees))
    cosine = cos(angle)
    sine = sin(angle)
    return ((cosine, -sine, 0.0), (sine, cosine, 0.0), (0.0, 0.0, 1.0))


def _altium_rotation_transform(
    *,
    rotation_x_degrees: float,
    rotation_y_degrees: float,
    rotation_z_degrees: float,
) -> Matrix4:
    # Column-vector convention: applying X, then Y, then Z is Rz * Ry * Rx.
    rotation = _matrix3_multiply(
        _rotation_z_matrix(rotation_z_degrees),
        _matrix3_multiply(
            _rotation_y_matrix(rotation_y_degrees),
            _rotation_x_matrix(rotation_x_degrees),
        ),
    )
    return (
        (rotation[0][0], rotation[0][1], rotation[0][2], 0.0),
        (rotation[1][0], rotation[1][1], rotation[1][2], 0.0),
        (rotation[2][0], rotation[2][1], rotation[2][2], 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def _bounds_vec3(
    bounds: Mapping[str, object],
    key: str,
    *,
    display_name: str,
) -> tuple[float, float, float]:
    value = bounds.get(key)
    if isinstance(value, Sequence) and not isinstance(value, str) and len(value) == 3:
        return float(value[0]), float(value[1]), float(value[2])
    raise ValueError(f"Could not infer STEP model bounds from {display_name}")


def _compute_step_bounds_with_geometer(
    model_data: bytes | bytearray | memoryview | str | Path,
    *,
    display_name: str,
    rotation_x_degrees: float,
    rotation_y_degrees: float,
    rotation_z_degrees: float,
    location_mils: tuple[float, float],
    z_offset_mils: float,
) -> PcbStepModelBounds:
    model_bounds = _load_geometer_model_bounds()
    transform = _altium_rotation_transform(
        rotation_x_degrees=rotation_x_degrees,
        rotation_y_degrees=rotation_y_degrees,
        rotation_z_degrees=rotation_z_degrees,
    )
    try:
        result = model_bounds(
            model_data,
            format="step",
            model_transform=transform,
        )
        bounds = getattr(result, "bounds")
        if not isinstance(bounds, Mapping):
            raise TypeError("geometer model_bounds result did not include bounds")
        min_mm = _bounds_vec3(bounds, "min", display_name=display_name)
        max_mm = _bounds_vec3(bounds, "max", display_name=display_name)
    except Exception as exc:
        raise ValueError(
            f"Could not infer STEP model bounds from {display_name}"
        ) from exc

    location_x_mils, location_y_mils = location_mils
    z_offset = float(z_offset_mils)
    bounds_mils = (
        min_mm[0] * MM_TO_MILS + location_x_mils,
        min_mm[1] * MM_TO_MILS + location_y_mils,
        max_mm[0] * MM_TO_MILS + location_x_mils,
        max_mm[1] * MM_TO_MILS + location_y_mils,
    )
    min_z_mils = min_mm[2] * MM_TO_MILS + z_offset
    max_z_mils = max_mm[2] * MM_TO_MILS + z_offset
    overall_height_mils = max_z_mils

    if bounds_mils[2] <= bounds_mils[0] or bounds_mils[3] <= bounds_mils[1]:
        raise ValueError(f"STEP model has invalid XY bounds: {display_name}")
    if max_z_mils < min_z_mils:
        raise ValueError(f"STEP model has invalid Z bounds: {display_name}")

    return PcbStepModelBounds(
        bounds_mils=bounds_mils,
        overall_height_mils=overall_height_mils,
        min_z_mils=min_z_mils,
        max_z_mils=max_z_mils,
    )


def compute_step_model_bounds_mils(
    model_data: bytes | bytearray | memoryview | str | Path,
    *,
    filename_hint: str = "model.step",
    rotation_x_degrees: float = 0.0,
    rotation_y_degrees: float = 0.0,
    rotation_z_degrees: float = 0.0,
    location_mils: tuple[float, float] = (0.0, 0.0),
    z_offset_mils: float = 0.0,
) -> PcbStepModelBounds:
    """
    Compute an Altium-style STEP model bounding box in PCB public mil units.

    The helper imports the STEP payload through the `wn-geometer` runtime
    dependency, applies model rotations around the STEP origin in Altium order
    (X, then Y, then Z), then applies `location_mils` to XY and
    `z_offset_mils` to Z. The returned projection is an axis-aligned bounding
    rectangle, not an HLR outline.

    Args:
        model_data: Uncompressed STEP payload bytes, or a path to a STEP file.
        filename_hint: Filename used in diagnostics and fallback staging when
            bytes are supplied.
        rotation_x_degrees: X-axis model rotation in degrees.
        rotation_y_degrees: Y-axis model rotation in degrees.
        rotation_z_degrees: Z-axis model rotation in degrees.
        location_mils: Footprint-plane model location `(x_mils, y_mils)`.
        z_offset_mils: 3D model Z offset in mils.

    Returns:
        Bounds as `(left, bottom, right, top)`, `zmin`, `zmax`, and Altium
        Overall Height (`zmax` above the board plane), all in mils.
    """
    location = _coerce_location_mils(location_mils)
    if isinstance(model_data, (str, Path)):
        model_path = Path(model_data)
        return _compute_step_bounds_with_geometer(
            model_path,
            display_name=model_path.name,
            rotation_x_degrees=float(rotation_x_degrees),
            rotation_y_degrees=float(rotation_y_degrees),
            rotation_z_degrees=float(rotation_z_degrees),
            location_mils=location,
            z_offset_mils=float(z_offset_mils),
        )

    payload = bytes(model_data)
    if not payload:
        raise ValueError("STEP model payload is empty")

    return _compute_step_bounds_with_geometer(
        payload,
        display_name=_sanitize_step_filename(filename_hint),
        rotation_x_degrees=float(rotation_x_degrees),
        rotation_y_degrees=float(rotation_y_degrees),
        rotation_z_degrees=float(rotation_z_degrees),
        location_mils=location,
        z_offset_mils=float(z_offset_mils),
    )
