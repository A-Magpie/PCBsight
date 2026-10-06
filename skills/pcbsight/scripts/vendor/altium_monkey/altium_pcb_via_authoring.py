"""Shared PCB via authoring helpers."""

from __future__ import annotations

from .altium_record_pcb__via import AltiumPcbVia

VIA_TENTING_DEFAULT_SOLDER_MASK_EXPANSION_IU = 40000


def mil_to_internal_units(value_mil: float) -> int:
    """Convert mils to Altium PCB internal units."""
    return int(round(float(value_mil) * 10000.0))


def apply_authored_via_surface_policy(
    via: AltiumPcbVia,
    *,
    is_tent_top: bool | None,
    is_tent_bottom: bool | None,
    solder_mask_expansion_top_mil: float | None,
    solder_mask_expansion_bottom_mil: float | None,
) -> None:
    """
    Apply public via tenting and per-side solder-mask expansion fields.
    """
    if is_tent_top is not None:
        via.is_tent_top = bool(is_tent_top)
    if is_tent_bottom is not None:
        via.is_tent_bottom = bool(is_tent_bottom)

    if via.is_tent_top or via.is_tent_bottom:
        via.solder_mask_expansion_mode = 2
        via.soldermask_expansion_front = VIA_TENTING_DEFAULT_SOLDER_MASK_EXPANSION_IU
        via.soldermask_expansion_back = VIA_TENTING_DEFAULT_SOLDER_MASK_EXPANSION_IU
        via._has_soldermask_expansion_front = True
        via._has_soldermask_expansion_back = True

    if solder_mask_expansion_top_mil is not None:
        via.solder_mask_expansion_mode = 2
        via.soldermask_expansion_front = mil_to_internal_units(
            solder_mask_expansion_top_mil
        )
        via._has_soldermask_expansion_front = True
    if solder_mask_expansion_bottom_mil is not None:
        via.solder_mask_expansion_mode = 2
        via.soldermask_expansion_back = mil_to_internal_units(
            solder_mask_expansion_bottom_mil
        )
        via._has_soldermask_expansion_back = True

    via.soldermask_expansion_linked = (
        via._has_soldermask_expansion_front
        and via._has_soldermask_expansion_back
        and via.soldermask_expansion_front == via.soldermask_expansion_back
    )
