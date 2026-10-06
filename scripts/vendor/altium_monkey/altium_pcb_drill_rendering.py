"""Shared PCB drill-hole rendering policy."""

from __future__ import annotations

from .altium_pcb_enums import PcbIpc4761ViaType


_FILLED_VIA_TYPES = frozenset(
    {
        int(PcbIpc4761ViaType.TYPE_5_FILLING),
        int(PcbIpc4761ViaType.TYPE_6A_FILLING_AND_COVERING),
        int(PcbIpc4761ViaType.TYPE_6B_FILLING_AND_COVERING),
        int(PcbIpc4761ViaType.TYPE_7_FILLING_AND_CAPPING),
    }
)


def should_render_via_drill_hole(via: object) -> bool:
    """Return true when a via should show an open drill hole."""

    try:
        hole_size_mils = float(getattr(via, "hole_size_mils", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False
    if hole_size_mils <= 0.0:
        return False
    return not via_drill_hole_is_filled(via)


def via_drill_hole_is_filled(via: object) -> bool:
    """Return true when IPC-4761 semantics cover/fill the drill opening."""

    try:
        via_type = int(getattr(via, "ipc4761_via_type", PcbIpc4761ViaType.NONE) or 0)
    except (TypeError, ValueError):
        return False
    return via_type in _FILLED_VIA_TYPES
