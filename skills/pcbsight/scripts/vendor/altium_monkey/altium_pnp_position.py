"""
Pick-and-place position mode helpers.
"""

from __future__ import annotations

from typing import Literal

PnpPositionMode = Literal["altium-pick-place", "component-origin"]

PNP_POSITION_MODE_ALTIUM_PICK_PLACE: PnpPositionMode = "altium-pick-place"
PNP_POSITION_MODE_COMPONENT_ORIGIN: PnpPositionMode = "component-origin"
PNP_POSITION_MODES: tuple[PnpPositionMode, PnpPositionMode] = (
    PNP_POSITION_MODE_ALTIUM_PICK_PLACE,
    PNP_POSITION_MODE_COMPONENT_ORIGIN,
)


def normalize_pnp_position_mode(
    position_mode: PnpPositionMode | str | None = None,
) -> PnpPositionMode:
    """
    Normalize a public pick-and-place position mode name.

    ``altium-pick-place`` matches Altium's Pick Place export by using the
    center of the bounding box of component-owned pad anchor points, with a
    component-origin fallback when no pads exist. ``component-origin`` uses the
    footprint placement origin directly.

    Args:
        position_mode: Public mode name or a small compatibility alias.

    Returns:
        Canonical public mode name.

    Raises:
        ValueError: If the mode is not recognized.
    """
    if position_mode is None:
        return PNP_POSITION_MODE_ALTIUM_PICK_PLACE

    mode = str(position_mode).strip().lower().replace("_", "-")
    aliases: dict[str, PnpPositionMode] = {
        "": PNP_POSITION_MODE_ALTIUM_PICK_PLACE,
        "altium": PNP_POSITION_MODE_ALTIUM_PICK_PLACE,
        "altium-pick-place": PNP_POSITION_MODE_ALTIUM_PICK_PLACE,
        "pick-place": PNP_POSITION_MODE_ALTIUM_PICK_PLACE,
        "component-origin": PNP_POSITION_MODE_COMPONENT_ORIGIN,
        "origin": PNP_POSITION_MODE_COMPONENT_ORIGIN,
        "part-origin": PNP_POSITION_MODE_COMPONENT_ORIGIN,
        "footprint-origin": PNP_POSITION_MODE_COMPONENT_ORIGIN,
    }
    try:
        return aliases[mode]
    except KeyError as exc:
        allowed = ", ".join(f"'{name}'" for name in PNP_POSITION_MODES)
        raise ValueError(
            f"Unknown PnP position mode: {position_mode!r}. Use {allowed}."
        ) from exc
