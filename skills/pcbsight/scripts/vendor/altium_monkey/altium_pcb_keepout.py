"""
Altium PCB keepout restriction helpers.

Altium stores object-specific keepout restrictions as a bit mask. The confirmed
bit order is via, track, copper, SMD pad, and through-hole pad.
"""

from __future__ import annotations

from enum import IntFlag
from typing import Iterable


class PcbKeepoutRestriction(IntFlag):
    """Named bits in Altium's object-specific keepout restriction mask."""

    VIA = 0x01
    TRACK = 0x02
    COPPER = 0x04
    SMD_PAD = 0x08
    TH_PAD = 0x10


PCB_KEEPOUT_RESTRICTION_KNOWN_MASK = 0x1F
PCB_KEEPOUT_RESTRICTION_ALL_MASK = PCB_KEEPOUT_RESTRICTION_KNOWN_MASK

_PCB_KEEPOUT_RESTRICTION_ORDER: tuple[tuple[PcbKeepoutRestriction, str], ...] = (
    (PcbKeepoutRestriction.VIA, "via"),
    (PcbKeepoutRestriction.TRACK, "track"),
    (PcbKeepoutRestriction.COPPER, "copper"),
    (PcbKeepoutRestriction.SMD_PAD, "smd_pad"),
    (PcbKeepoutRestriction.TH_PAD, "th_pad"),
)

_PCB_KEEPOUT_RESTRICTION_BY_NAME: dict[str, PcbKeepoutRestriction] = {
    name: restriction for restriction, name in _PCB_KEEPOUT_RESTRICTION_ORDER
}


def decode_pcb_keepout_restrictions(mask: int) -> tuple[PcbKeepoutRestriction, ...]:
    """Return known keepout restriction flags set in ``mask``."""

    raw_mask = int(mask)
    return tuple(
        restriction
        for restriction, _name in _PCB_KEEPOUT_RESTRICTION_ORDER
        if raw_mask & int(restriction)
    )


def encode_pcb_keepout_restrictions(
    restrictions: Iterable[PcbKeepoutRestriction | str],
    *,
    unknown_bits: int = 0,
) -> int:
    """Return the raw Altium mask for named restrictions plus optional unknown bits."""

    mask = int(unknown_bits)
    for restriction in restrictions:
        if isinstance(restriction, str):
            mask |= int(_restriction_from_name(restriction))
        else:
            mask |= int(restriction)
    return mask


def pcb_keepout_restriction_names(mask: int) -> tuple[str, ...]:
    """Return stable public names for known keepout restriction bits."""

    raw_mask = int(mask)
    return tuple(
        name
        for restriction, name in _PCB_KEEPOUT_RESTRICTION_ORDER
        if raw_mask & int(restriction)
    )


def pcb_keepout_restriction_unknown_bits(mask: int) -> int:
    """Return restriction bits outside the confirmed Altium five-bit field."""

    return int(mask) & ~PCB_KEEPOUT_RESTRICTION_KNOWN_MASK


def _restriction_from_name(name: str) -> PcbKeepoutRestriction:
    normalized = name.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"smdpad", "smd_pad"}:
        normalized = "smd_pad"
    elif normalized in {"thpad", "th_pad", "through_hole_pad"}:
        normalized = "th_pad"
    try:
        return _PCB_KEEPOUT_RESTRICTION_BY_NAME[normalized]
    except KeyError as exc:
        raise ValueError(f"unknown PCB keepout restriction name: {name!r}") from exc
