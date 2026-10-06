"""Internal PCB pad projection bound helpers."""

from __future__ import annotations

from collections.abc import Iterable
from math import cos, radians, sin

from .altium_record_pcb__pad import AltiumPcbPad
from .altium_record_types import PcbLayer

_INTERNAL_UNITS_PER_MIL = 10000.0


def _fallback_projection_layer(pad: AltiumPcbPad) -> PcbLayer:
    try:
        layer = PcbLayer(int(pad.layer))
    except (TypeError, ValueError):
        return PcbLayer.TOP

    if layer == PcbLayer.BOTTOM:
        return PcbLayer.BOTTOM
    return PcbLayer.TOP


def _pad_axis_aligned_bounds_mils(
    pad: AltiumPcbPad,
) -> tuple[float, float, float, float] | None:
    layer = _fallback_projection_layer(pad)
    width_iu, height_iu = pad._layer_size(layer)
    if width_iu <= 0 or height_iu <= 0:
        return None

    center_x_mils, center_y_mils = pad.pad_center_mils(layer)
    half_width_mils = (float(width_iu) / _INTERNAL_UNITS_PER_MIL) / 2.0
    half_height_mils = (float(height_iu) / _INTERNAL_UNITS_PER_MIL) / 2.0
    angle = radians(float(pad.rotation or 0.0))
    cosine = abs(cos(angle))
    sine = abs(sin(angle))
    x_extent = cosine * half_width_mils + sine * half_height_mils
    y_extent = sine * half_width_mils + cosine * half_height_mils
    return (
        center_x_mils - x_extent,
        center_y_mils - y_extent,
        center_x_mils + x_extent,
        center_y_mils + y_extent,
    )


def pad_projection_bounds_mils(
    pads: Iterable[AltiumPcbPad],
) -> tuple[float, float, float, float] | None:
    """
    Return an axis-aligned projection around all SMD/through-hole pads.

    This is a recovery path for STEP-bound inference failures. It intentionally
    uses first-order copper pad extents rather than mask or paste openings.
    """
    bounds: list[tuple[float, float, float, float]] = []
    for pad in pads:
        if not (pad.is_smt or pad.is_through_hole):
            continue
        pad_bounds = _pad_axis_aligned_bounds_mils(pad)
        if pad_bounds is not None:
            bounds.append(pad_bounds)

    if not bounds:
        return None

    return (
        min(item[0] for item in bounds),
        min(item[1] for item in bounds),
        max(item[2] for item in bounds),
        max(item[3] for item in bounds),
    )
