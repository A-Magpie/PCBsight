"""Read-only PcbDoc union stream models."""

from __future__ import annotations

import struct
from collections import defaultdict
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

SMART_UNION_TYPE_NAMES: Mapping[int, str] = MappingProxyType(
    {
        1: "drill_table",
        2: "via_stitching",
        3: "layer_stack_table",
        4: "length_tuning",
        5: "metadata_ole_object",
        6: "via_shielding",
        9: "rectangle",
    }
)

PCB_USER_UNION_MEMBER_COLLECTIONS: tuple[tuple[str, str], ...] = (
    ("components", "union_index"),
    ("tracks", "union_index"),
    ("arcs", "union_index"),
    ("fills", "union_index"),
    ("pads", "union_index"),
    ("vias", "union_index"),
    ("texts", "text_union_index"),
    ("component_bodies", "union_index"),
    ("shapebased_component_bodies", "union_index"),
    ("regions", "union_index"),
    ("shapebased_regions", "union_index"),
    ("polygons", "union_index"),
)


@dataclass(frozen=True)
class PcbUnionNameRecord:
    """Decoded `UnionNames/Data` catalog entry."""

    union_index: int
    name: str
    byte_count: int


@dataclass(frozen=True)
class PcbSmartUnionRecord:
    """Decoded read-only `SmartUnions/Data` property record."""

    record_index: int
    byte_count: int
    properties: Mapping[str, str]
    raw_text: str

    @property
    def union_index(self) -> int:
        """Return the semantic union id, falling back to `FUNIONINDEX`."""

        value = int(self.properties.get("UNIONINDEX", "0") or "0")
        if value != 0:
            return value
        return int(self.properties.get("FUNIONINDEX", "0") or "0")

    @property
    def union_type(self) -> int:
        """Return Altium's typed smart-union kind value."""

        return int(self.properties.get("UNIONTYPE", "0") or "0")

    @property
    def kind_name(self) -> str:
        """Return a stable lowercase name for known typed smart unions."""

        return SMART_UNION_TYPE_NAMES.get(self.union_type, "unknown")


@dataclass(frozen=True)
class PcbUnionMemberRef:
    """Reference to one parsed primitive that participates in a user union."""

    collection: str
    object_index: int
    union_index: int
    obj: object = field(compare=False, repr=False)


@dataclass(frozen=True)
class PcbUserUnion:
    """Read-only semantic view of one user-defined PCB union."""

    union_index: int
    name: str
    members: tuple[PcbUnionMemberRef, ...]

    @property
    def member_count(self) -> int:
        """Return the number of parsed member references."""

        return len(self.members)

    @property
    def members_by_collection(self) -> dict[str, tuple[PcbUnionMemberRef, ...]]:
        """Group member references by their source PcbDoc collection."""

        grouped: dict[str, list[PcbUnionMemberRef]] = defaultdict(list)
        for member in self.members:
            grouped[member.collection].append(member)
        return {
            collection: tuple(members)
            for collection, members in sorted(grouped.items())
        }


def _read_u32(data: bytes, offset: int, stream_name: str) -> tuple[int, int]:
    if offset + 4 > len(data):
        raise ValueError(f"{stream_name} ended before a uint32 at offset {offset}")
    return struct.unpack_from("<I", data, offset)[0], offset + 4


def decode_pcb_union_name_records(data: bytes) -> tuple[PcbUnionNameRecord, ...]:
    """Decode Altium `UnionNames/Data` bytes."""

    if not data:
        return ()

    pos = 0
    record_count, pos = _read_u32(data, pos, "UnionNames/Data")
    records: list[PcbUnionNameRecord] = []

    for record_index in range(record_count):
        union_index, pos = _read_u32(data, pos, "UnionNames/Data")
        byte_count, pos = _read_u32(data, pos, "UnionNames/Data")
        end = pos + byte_count
        if end > len(data):
            raise ValueError(
                f"UnionNames/Data record {record_index} extends past stream length"
            )
        raw_name = data[pos:end]
        pos = end
        if not raw_name.endswith(b"\x00\x00"):
            raise ValueError(
                f"UnionNames/Data record {record_index} is not null-terminated"
            )
        records.append(
            PcbUnionNameRecord(
                union_index=union_index,
                name=raw_name.decode("utf-16le").rstrip("\x00"),
                byte_count=byte_count,
            )
        )

    if pos != len(data):
        raise ValueError(f"UnionNames/Data has {len(data) - pos} trailing byte(s)")
    return tuple(records)


def decode_pcb_smart_union_records(data: bytes) -> tuple[PcbSmartUnionRecord, ...]:
    """Decode Altium `SmartUnions/Data` length-prefixed property records."""

    records: list[PcbSmartUnionRecord] = []
    pos = 0
    while pos < len(data):
        byte_count, pos = _read_u32(data, pos, "SmartUnions/Data")
        end = pos + byte_count
        if end > len(data):
            raise ValueError(
                f"SmartUnions/Data record {len(records)} extends past stream length"
            )
        raw_record = data[pos:end]
        pos = end
        raw_text = raw_record.decode("ascii")
        properties: dict[str, str] = {}
        for item in raw_text.split("|"):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            if key:
                properties[key] = value
        records.append(
            PcbSmartUnionRecord(
                record_index=len(records),
                byte_count=byte_count,
                properties=MappingProxyType(dict(properties)),
                raw_text=raw_text,
            )
        )
    return tuple(records)


def encode_pcb_union_name_records(
    records: tuple[PcbUnionNameRecord, ...],
) -> bytes:
    """Encode Altium `UnionNames/Data` bytes."""

    data = bytearray(struct.pack("<I", len(records)))
    for record in records:
        raw_name = record.name.encode("utf-16le") + b"\x00\x00"
        data.extend(struct.pack("<II", int(record.union_index), len(raw_name)))
        data.extend(raw_name)
    return bytes(data)


def pcb_union_name_byte_count(name: str) -> int:
    """Return the stored UTF-16LE byte count for a union name."""

    return len(name.encode("utf-16le")) + 2


def decode_pcb_union_name_records_from_streams(
    raw_streams: Mapping[str, bytes],
) -> tuple[PcbUnionNameRecord, ...]:
    """Decode `UnionNames/Data` from a PcbDoc raw-stream map."""

    return decode_pcb_union_name_records(raw_streams.get("UnionNames/Data", b""))


def decode_pcb_smart_union_records_from_streams(
    raw_streams: Mapping[str, bytes],
) -> tuple[PcbSmartUnionRecord, ...]:
    """Decode `SmartUnions/Data` from a PcbDoc raw-stream map."""

    return decode_pcb_smart_union_records(raw_streams.get("SmartUnions/Data", b""))


def _coerce_union_index(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        union_index = value
    elif isinstance(value, str):
        try:
            union_index = int(value)
        except ValueError:
            return None
    else:
        return None
    if union_index in (0, 0xFFFFFFFF, -1):
        return None
    return union_index


def pcb_track_user_union_index(track: object) -> int | None:
    """
    Return the Altium-visible user-union id encoded on a track record.

    AD25/AD26 keep the TRACK header union field at the no-union sentinel for
    user unions and store the semantic id in the high bytes of the trailing
    field currently named `solder_mask_expansion`.
    """

    return pcb_packed_mask_user_union_index(track)


def pcb_packed_mask_user_union_index(primitive: object) -> int | None:
    """Return a user-union id packed into a primitive trailing mask field."""

    try:
        raw_value = int(getattr(primitive, "solder_mask_expansion", 0))
    except (TypeError, ValueError):
        return None
    if raw_value <= 0 or (raw_value & 0xFF):
        return None
    return _coerce_union_index(raw_value >> 8)


def pcb_pad_user_union_index(pad: object) -> int | None:
    """Return the Altium-visible user-union id encoded on a pad record."""

    return _coerce_union_index(getattr(pad, "pad_user_union_index", None))


def pcb_primitive_user_union_index(
    collection_name: str,
    primitive: object,
    union_attr: str,
) -> int | None:
    """Return the semantic user-union id for a parsed primitive."""

    if collection_name in {"tracks", "arcs", "fills"}:
        return pcb_packed_mask_user_union_index(primitive)
    if collection_name == "pads":
        return pcb_pad_user_union_index(primitive)
    return _coerce_union_index(getattr(primitive, union_attr, None))


def _typed_via_child_collision_ids(
    smart_unions: tuple[PcbSmartUnionRecord, ...],
) -> set[int]:
    collision_ids: set[int] = set()
    for record in smart_unions:
        if record.union_type not in {2, 6}:
            continue
        low_byte_index = record.union_index & 0xFF
        if low_byte_index:
            collision_ids.add(low_byte_index)
    return collision_ids


def _member_refs_by_union_index(
    pcbdoc: object,
    *,
    excluded_via_union_indexes: set[int],
) -> dict[int, list[PcbUnionMemberRef]]:
    members: dict[int, list[PcbUnionMemberRef]] = defaultdict(list)
    for collection_name, union_attr in PCB_USER_UNION_MEMBER_COLLECTIONS:
        primitives = getattr(pcbdoc, collection_name, ())
        for object_index, primitive in enumerate(primitives):
            union_index = pcb_primitive_user_union_index(
                collection_name,
                primitive,
                union_attr,
            )
            if union_index is None:
                continue
            if collection_name == "vias" and union_index in excluded_via_union_indexes:
                continue
            members[union_index].append(
                PcbUnionMemberRef(
                    collection=collection_name,
                    object_index=object_index,
                    union_index=union_index,
                    obj=primitive,
                )
            )
    return members


def build_pcb_user_unions(pcbdoc: object) -> tuple[PcbUserUnion, ...]:
    """Build read-only user-union views from a parsed PcbDoc object."""

    name_records = tuple(getattr(pcbdoc, "union_name_records", ()))
    smart_unions = tuple(getattr(pcbdoc, "smart_unions", ()))
    typed_union_ids = {record.union_index for record in smart_unions}
    typed_via_child_ids = _typed_via_child_collision_ids(smart_unions)
    members_by_index = _member_refs_by_union_index(
        pcbdoc,
        excluded_via_union_indexes=typed_via_child_ids,
    )

    user_unions: list[PcbUserUnion] = []
    for record in name_records:
        if record.union_index in typed_union_ids:
            continue
        members = tuple(members_by_index.get(record.union_index, ()))
        if not members:
            continue
        user_unions.append(
            PcbUserUnion(
                union_index=record.union_index,
                name=record.name,
                members=members,
            )
        )
    return tuple(user_unions)
