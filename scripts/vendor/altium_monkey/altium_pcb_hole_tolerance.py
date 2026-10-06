"""Helpers for PCB drill-hole tolerance fields."""

from __future__ import annotations

from math import isfinite


PCB_HOLE_TOLERANCE_UNSET = 0x7FFFFFFF


def hole_tolerance_mils_from_internal(value: int) -> float | None:
    """
    Convert Altium internal hole-tolerance units to mils.

    Altium serializes an unset/N/A tolerance as signed int32 max.
    """
    value_int = int(value)
    if value_int == PCB_HOLE_TOLERANCE_UNSET:
        return None
    return value_int / 10000.0


def hole_tolerance_internal_from_mils(value: float | None, field_name: str) -> int:
    """
    Convert a public mil-unit tolerance to Altium internal units.
    """
    if value is None:
        return PCB_HOLE_TOLERANCE_UNSET
    value_float = float(value)
    if not isfinite(value_float) or value_float < 0.0:
        raise ValueError(f"{field_name} must be a non-negative finite mil value")
    return int(value_float * 10000.0)
