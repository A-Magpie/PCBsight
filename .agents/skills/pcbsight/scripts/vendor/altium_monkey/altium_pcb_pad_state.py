"""Parser-owned PCB pad-state projections for native-oracle comparison."""

from __future__ import annotations

from collections.abc import Sequence
from enum import IntEnum
from typing import SupportsInt, cast


class AltiumPadTShape(IntEnum):
    """Raw Altium pad-shape values used by PAD records and native COM APIs."""

    eNoShape = 0
    eRounded = 1
    eRectangular = 2
    eOctagonal = 3
    eCircleShape = 4
    eArcShape = 5
    eTerminator = 6
    eRoundRectShape = 7
    eRotatedRectShape = 8
    eRoundedRectangular = 9
    eCustomShape = 10


def altium_pad_tshape_name(value: int | None) -> str:
    """Return the Altium enum name for a raw PAD shape value."""
    shape_value = _as_int(value)
    try:
        return AltiumPadTShape(shape_value).name
    except ValueError:
        return f"UNKNOWN_{shape_value}"


def build_pcblib_pad_state(pcblib: object) -> dict[str, object]:
    """
    Build a PcbLib pad-state projection from parsed records.

    This projection is intentionally parser-owned. It records raw shape bytes,
    dimensions, rotation, and parsed SubRecord 6 evidence without applying SVG
    rendering fallbacks or native-only defaults.
    """
    footprints: list[dict[str, object]] = []
    exported_pad_count = 0

    for footprint_index, footprint in enumerate(
        getattr(pcblib, "footprints", []) or []
    ):
        pads = [
            _build_pad_state(pad, footprint_index=footprint_index)
            for pad in (getattr(footprint, "pads", []) or [])
        ]
        exported_pad_count += len(pads)
        footprints.append(
            {
                "index": footprint_index,
                "pattern": str(getattr(footprint, "name", "") or ""),
                "pad_count": len(pads),
                "pads": pads,
            }
        )

    return {
        "schema": "wn.altium_monkey.pcblib_pad_state.v1",
        "footprint_count": len(footprints),
        "exported_pad_count": exported_pad_count,
        "footprints": footprints,
    }


def build_pcbdoc_pad_state(pcbdoc: object) -> dict[str, object]:
    """
    Build a PcbDoc pad-state projection from parsed records.

    This uses the same per-pad raw-shape projection as PcbLib pad-state so
    corpus coverage tools can compare PcbDoc and PcbLib pad encodings without
    duplicating shape interpretation logic.
    """
    pads = [
        _build_pad_state(pad, footprint_index=-1, pad_index=pad_index)
        for pad_index, pad in enumerate(getattr(pcbdoc, "pads", []) or [])
    ]

    return {
        "schema": "altium_monkey.pcbdoc_pad_state.a0",
        "pad_count": len(pads),
        "pads": pads,
    }


def _build_pad_state(
    pad: object, *, footprint_index: int, pad_index: int | None = None
) -> dict[str, object]:
    top_shape = _as_int(getattr(pad, "top_shape", 0))
    mid_shape = _as_int(getattr(pad, "mid_shape", 0))
    bottom_shape = _as_int(getattr(pad, "bot_shape", 0))
    subrecord6_data = getattr(pad, "_subrecord6_data", b"") or b""

    return {
        "name": str(getattr(pad, "designator", "") or ""),
        "footprint_index": footprint_index,
        "pad_index": pad_index,
        "rotation": float(getattr(pad, "rotation", 0.0) or 0.0),
        "hole_size": _as_int(getattr(pad, "hole_size", 0)),
        "hole_width": _as_int(getattr(pad, "hole_width", 0)),
        "plated": bool(getattr(pad, "is_plated", False)),
        "top_shape": altium_pad_tshape_name(top_shape),
        "top_shape_value": top_shape,
        "mid_shape": altium_pad_tshape_name(mid_shape),
        "mid_shape_value": mid_shape,
        "bottom_shape": altium_pad_tshape_name(bottom_shape),
        "bottom_shape_value": bottom_shape,
        "alt_shape": [
            _as_int(value) for value in (getattr(pad, "alt_shape", []) or [])
        ],
        "corner_radius": [
            _as_int(value) for value in (getattr(pad, "corner_radius", []) or [])
        ],
        "exact_corner_radius_percent_by_layer": {
            str(token): float(value)
            for token, value in (
                getattr(pad, "exact_corner_radius_percent_by_layer", {}) or {}
            ).items()
        },
        "full_stack_layer_entries": [
            _build_full_stack_layer_entry(entry)
            for entry in (getattr(pad, "full_stack_layer_entries", []) or [])
        ],
        "has_custom_shape": getattr(pad, "custom_shape", None) is not None,
        "custom_shape_layers": _custom_shape_layers(getattr(pad, "custom_shape", None)),
        "subrecord6_length": len(subrecord6_data),
        "layers": {
            "top": _build_layer_state(
                shape_value=top_shape,
                x_size=getattr(pad, "top_width", 0),
                y_size=getattr(pad, "top_height", 0),
            ),
            "mid1": _build_layer_state(
                shape_value=mid_shape,
                x_size=getattr(pad, "mid_width", 0),
                y_size=getattr(pad, "mid_height", 0),
            ),
            "bottom": _build_layer_state(
                shape_value=bottom_shape,
                x_size=getattr(pad, "bot_width", 0),
                y_size=getattr(pad, "bot_height", 0),
            ),
        },
    }


def _build_full_stack_layer_entry(entry: Sequence[object]) -> dict[str, int]:
    return {
        "layer_code": _as_int(entry[0] if len(entry) > 0 else 0),
        "mode_flags": _as_int(entry[1] if len(entry) > 1 else 0),
        "shape_value": _as_int(entry[2] if len(entry) > 2 else 0),
        "size_x": _as_int(entry[3] if len(entry) > 3 else 0),
        "size_y": _as_int(entry[4] if len(entry) > 4 else 0),
        "corner_percent": _as_int(entry[5] if len(entry) > 5 else 0),
    }


def _custom_shape_layers(custom_shape: object) -> list[int]:
    if custom_shape is None:
        return []
    layer_shapes = getattr(custom_shape, "layer_shapes", {}) or {}
    return sorted(_as_int(layer) for layer in layer_shapes)


def _build_layer_state(
    *, shape_value: int, x_size: object, y_size: object
) -> dict[str, object]:
    return {
        "shape": altium_pad_tshape_name(shape_value),
        "shape_value": _as_int(shape_value),
        "x_size": _as_int(x_size),
        "y_size": _as_int(y_size),
    }


def _as_int(value: object) -> int:
    if value is None:
        return 0
    return int(cast(SupportsInt, value))
