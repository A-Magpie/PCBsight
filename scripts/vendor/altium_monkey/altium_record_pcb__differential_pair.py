"""
PCB differential-pair records from `DifferentialPairs6/Data`.
"""

from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence

from .altium_pcb_stream_helpers import format_bool_text
from .altium_record_pcb__net import AltiumPcbNet
from .altium_utilities import (
    create_stream_from_records,
    decode_byte_array,
    parse_byte_record,
)

if TYPE_CHECKING:
    from .altium_pcbdoc import AltiumPcbDoc


def _parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().upper() == "TRUE"


def _parse_int(value: str | None, *, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def make_authored_differential_pair_unique_id(
    name: str,
    positive_net_name: str,
    negative_net_name: str,
) -> str:
    """
    Return a stable synthetic 8-character ID for authored pair records.
    """
    seed = f"pcbdoc-builder-differential-pair|{name}|{positive_net_name}|{negative_net_name}"
    return uuid.uuid5(uuid.NAMESPACE_URL, seed).hex[:8].upper()


@dataclass
class AltiumPcbDifferentialPair:
    """
    PCB differential-pair object.

    Altium stores these in `DifferentialPairs6/Data`. Pair classes live
    separately in `Classes6/Data` records with `KIND=6`, and routing
    constraints live separately in `Rules6/Data`.
    """

    name: str = ""
    positive_net_name: str = ""
    negative_net_name: str = ""
    gather_control: bool = False
    unique_id: str = ""

    layer: str = "TOP"
    selected: bool = False
    locked: bool = False
    polygon_outline: bool = False
    user_routed: bool = True
    keepout: bool = False
    union_index: int = 0

    _raw_record: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def from_record(cls, record: dict[str, str]) -> "AltiumPcbDifferentialPair":
        """
        Parse one `DifferentialPairs6/Data` text record.
        """
        return cls(
            name=record.get("NAME", ""),
            positive_net_name=record.get("POSITIVENETNAME", ""),
            negative_net_name=record.get("NEGATIVENETNAME", ""),
            gather_control=_parse_bool(record.get("GATHERCONTROL")),
            unique_id=record.get("UNIQUEID", ""),
            layer=record.get("LAYER", "TOP"),
            selected=_parse_bool(record.get("SELECTION")),
            locked=_parse_bool(record.get("LOCKED")),
            polygon_outline=_parse_bool(record.get("POLYGONOUTLINE")),
            user_routed=_parse_bool(record.get("USERROUTED"), default=True),
            keepout=_parse_bool(record.get("KEEPOUT")),
            union_index=_parse_int(record.get("UNIONINDEX")),
            _raw_record=dict(record),
        )

    @classmethod
    def create(
        cls,
        *,
        name: str,
        positive_net_name: str,
        negative_net_name: str,
        gather_control: bool = False,
        unique_id: str | None = None,
    ) -> "AltiumPcbDifferentialPair":
        """
        Create a new differential-pair object from first principles.
        """
        clean_name = name.strip()
        clean_positive = positive_net_name.strip()
        clean_negative = negative_net_name.strip()
        if not clean_name:
            raise ValueError("Differential-pair name must not be empty")
        if not clean_positive:
            raise ValueError("Positive net name must not be empty")
        if not clean_negative:
            raise ValueError("Negative net name must not be empty")
        if clean_positive.upper() == clean_negative.upper():
            raise ValueError("Positive and negative net names must differ")

        resolved_unique_id = unique_id or make_authored_differential_pair_unique_id(
            clean_name,
            clean_positive,
            clean_negative,
        )
        return cls(
            name=clean_name,
            positive_net_name=clean_positive,
            negative_net_name=clean_negative,
            gather_control=bool(gather_control),
            unique_id=resolved_unique_id,
        )

    @property
    def net_names(self) -> tuple[str, str]:
        """
        Return `(positive_net_name, negative_net_name)`.
        """
        return (self.positive_net_name, self.negative_net_name)

    def uses_net_name(self, net_name: str) -> bool:
        """
        Return true when this pair references `net_name`.
        """
        normalized = net_name.strip().upper()
        return normalized in {
            self.positive_net_name.strip().upper(),
            self.negative_net_name.strip().upper(),
        }

    def resolve_positive_net(self, pcbdoc: "AltiumPcbDoc") -> AltiumPcbNet | None:
        """
        Resolve the positive net name against a parsed `AltiumPcbDoc`.
        """
        return _find_net_by_name(pcbdoc.nets, self.positive_net_name)

    def resolve_negative_net(self, pcbdoc: "AltiumPcbDoc") -> AltiumPcbNet | None:
        """
        Resolve the negative net name against a parsed `AltiumPcbDoc`.
        """
        return _find_net_by_name(pcbdoc.nets, self.negative_net_name)

    def to_record(self) -> dict[str, str]:
        """
        Serialize to a `DifferentialPairs6/Data` text-record dictionary.
        """
        record = dict(self._raw_record)
        if not record:
            record = {
                "SELECTION": format_bool_text(self.selected),
                "LAYER": self.layer,
                "LOCKED": format_bool_text(self.locked),
                "POLYGONOUTLINE": format_bool_text(self.polygon_outline),
                "USERROUTED": format_bool_text(self.user_routed),
                "KEEPOUT": format_bool_text(self.keepout),
                "UNIONINDEX": str(self.union_index),
                "POSITIVENETNAME": self.positive_net_name,
                "NEGATIVENETNAME": self.negative_net_name,
                "NAME": self.name,
                "GATHERCONTROL": format_bool_text(self.gather_control),
            }
            if self.unique_id:
                record["UNIQUEID"] = self.unique_id
            return record

        record["NAME"] = self.name
        record["POSITIVENETNAME"] = self.positive_net_name
        record["NEGATIVENETNAME"] = self.negative_net_name
        record["GATHERCONTROL"] = format_bool_text(self.gather_control)

        record["SELECTION"] = format_bool_text(self.selected)
        record["LAYER"] = self.layer
        record["LOCKED"] = format_bool_text(self.locked)
        record["POLYGONOUTLINE"] = format_bool_text(self.polygon_outline)
        record["USERROUTED"] = format_bool_text(self.user_routed)
        record["KEEPOUT"] = format_bool_text(self.keepout)
        record["UNIONINDEX"] = str(self.union_index)
        if self.unique_id or "UNIQUEID" in record:
            record["UNIQUEID"] = self.unique_id
        return record


def _find_net_by_name(
    nets: Sequence[AltiumPcbNet],
    name: str,
) -> AltiumPcbNet | None:
    normalized = name.strip().upper()
    for net in nets:
        if net.name.strip().upper() == normalized:
            return net
    return None


def parse_differential_pair_stream(
    data: bytes,
) -> tuple[AltiumPcbDifferentialPair, ...]:
    """
    Parse `DifferentialPairs6/Data` into typed pair objects.
    """
    pairs: list[AltiumPcbDifferentialPair] = []
    offset = 0
    while offset < len(data):
        if offset + 4 > len(data):
            raise ValueError("Invalid DifferentialPairs6/Data stream")
        record_len = struct.unpack("<I", data[offset : offset + 4])[0]
        offset += 4
        if record_len <= 0 or offset + record_len > len(data):
            raise ValueError("Invalid DifferentialPairs6/Data stream")
        raw_record = data[offset : offset + record_len]
        offset += record_len

        fields: dict[str, str] = {}
        for part in parse_byte_record(raw_record):
            decoded = decode_byte_array(part)
            if "=" not in decoded:
                continue
            key, value = decoded.split("=", 1)
            fields[key] = value
        if fields:
            pairs.append(AltiumPcbDifferentialPair.from_record(fields))
    return tuple(pairs)


def build_differential_pair_stream(
    pairs: Sequence[AltiumPcbDifferentialPair],
) -> bytes:
    """
    Serialize typed pair objects to `DifferentialPairs6/Data`.
    """
    return create_stream_from_records([pair.to_record() for pair in pairs])
