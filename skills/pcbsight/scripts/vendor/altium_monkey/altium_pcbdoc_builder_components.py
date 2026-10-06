"""
Component-authoring helpers for `PcbDocBuilder`.

This module authors board-level component records separately from the child
primitives they own. The builder currently covers the minimal component record
surface needed for placed-footprint workflows:

- one `Components6/Data` record
- child primitive ownership through each primitive's `component_index`
- one board `PrimitiveGuids/Data` record for the component object itself

Authored component `UNIQUEID` values are stable deterministic 8-character
tokens derived from component placement inputs.
"""

from __future__ import annotations

import struct
import uuid
from collections import OrderedDict
from enum import IntEnum
from typing import Sequence

from .altium_component_kind import parse_component_kind
from .altium_pcb_stream_helpers import format_mil_value as _format_mil_value
from .altium_pcb_component import AltiumPcbComponent
from .altium_pcb_enums import PcbLibIdentifierKind, PcbTextAutoposition
from .altium_record_types import PcbLayer
from .altium_utilities import (
    create_stream_from_records,
    decode_byte_array,
    parse_byte_record,
)


def _format_legacy_scientific(value: float) -> str:
    text = f"{float(value): .14E}"
    mantissa, exponent = text.split("E", 1)
    return f"{mantissa}E{int(exponent):+05d}"


def _normalize_component_layer(layer: str | PcbLayer | int) -> str:
    if isinstance(layer, PcbLayer):
        if layer.is_copper():
            return layer.to_json_name()
    if isinstance(layer, int):
        try:
            candidate = PcbLayer(layer)
        except ValueError:
            candidate = None
        if candidate is not None and candidate.is_copper():
            return candidate.to_json_name()
    text = str(layer).strip().upper()
    if text in {"TOP", "TOPLAYER", "TOP LAYER"}:
        return "TOP"
    if text in {"BOTTOM", "BOTTOMLAYER", "BOTTOM LAYER"}:
        return "BOTTOM"
    try:
        candidate = PcbLayer.from_json_name(text.replace(" ", ""))
    except ValueError:
        candidate = None
    if candidate is not None and candidate.is_copper():
        return candidate.to_json_name()
    raise ValueError(f"Unsupported component layer: {layer!r}")


def _coerce_optional_enum(
    value: object | None, enum_type: type[IntEnum], field_name: str
) -> IntEnum | None:
    if value is None:
        return None
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        valid_values = ", ".join(str(int(member)) for member in enum_type)
        raise ValueError(
            f"{field_name} must be a {enum_type.__name__} value "
            f"({valid_values}), got {value!r}"
        ) from exc


def _add_optional_text(
    raw_record: OrderedDict[str, str], key: str, value: object
) -> None:
    text = "" if value is None else str(value)
    if text:
        raw_record[key] = text


def _add_optional_bool(
    raw_record: OrderedDict[str, str], key: str, value: bool | None
) -> None:
    if value is not None:
        raw_record[key] = "TRUE" if value else "FALSE"


def _deterministic_component_unique_id(
    designator: str,
    footprint: str,
    x_mils: float,
    y_mils: float,
    layer: str,
    rotation_degrees: float,
) -> str:
    seed = (
        f"pcbdoc-builder-component|{designator}|{footprint}|"
        f"{x_mils:g}|{y_mils:g}|{layer}|{rotation_degrees:g}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, seed).hex[:8].upper()


def parse_component_stream(data: bytes) -> tuple[AltiumPcbComponent, ...]:
    """
    Parse `Components6/Data` into component records.
    """
    components: list[AltiumPcbComponent] = []
    offset = 0
    while offset < len(data):
        if len(data) < offset + 4:
            raise ValueError("Invalid Components6/Data stream")
        record_len = struct.unpack("<I", data[offset : offset + 4])[0]
        offset += 4
        if len(data) < offset + record_len:
            raise ValueError("Invalid Components6/Data stream")
        raw_record = data[offset : offset + record_len]
        offset += record_len
        fields: OrderedDict[str, str] = OrderedDict()
        for part in parse_byte_record(raw_record):
            decoded = decode_byte_array(part)
            if "=" not in decoded:
                continue
            key, value = decoded.split("=", 1)
            fields[key] = value
        components.append(
            AltiumPcbComponent(
                designator=fields.get("SOURCEDESIGNATOR", ""),
                footprint=fields.get("PATTERN", ""),
                layer=fields.get("LAYER", ""),
                x=fields.get("X", ""),
                y=fields.get("Y", ""),
                rotation=fields.get("ROTATION", ""),
                unique_id=fields.get("UNIQUEID", ""),
                union_index=int(fields.get("UNIONINDEX", "0") or "0"),
                description=fields.get("SOURCEDESCRIPTION", ""),
                raw_record=dict(fields),
                component_kind=parse_component_kind(fields),
            )
        )
    if offset != len(data):
        raise ValueError("Unexpected trailing bytes in Components6/Data")
    return tuple(components)


def build_component_stream(components: Sequence[AltiumPcbComponent]) -> bytes:
    """
    Serialize component records back into `Components6/Data`.
    """
    return create_stream_from_records(
        [dict(component.raw_record or {}) for component in components]
    )


def build_authored_component(
    *,
    designator: str,
    footprint: str,
    position_mils: tuple[float, float],
    layer: str | PcbLayer | int = "TOP",
    rotation_degrees: float = 0.0,
    source_footprint_library: str = "",
    name_on: bool = True,
    comment_on: bool = False,
    name_auto_position: PcbTextAutoposition | None = None,
    comment_auto_position: PcbTextAutoposition | None = None,
    description: str = "",
    unique_id: str | None = None,
    channel_offset: int | None = None,
    source_designator: str | None = None,
    source_unique_id: str = "",
    source_hierarchical_path: str = "",
    source_component_library: str = "",
    source_component_library_identifier_kind: PcbLibIdentifierKind | None = None,
    source_component_library_identifier: str = "",
    source_lib_reference: str = "",
    footprint_description: str = "",
    lock_strings: bool | None = None,
    enable_pin_swapping: bool | None = None,
    enable_part_swapping: bool | None = None,
    jumpers_visible: bool | None = True,
) -> AltiumPcbComponent:
    """
    Create the smallest useful authored component record.
    """
    layer_token = _normalize_component_layer(layer)
    name_auto_position_value = _coerce_optional_enum(
        name_auto_position, PcbTextAutoposition, "name_auto_position"
    )
    comment_auto_position_value = _coerce_optional_enum(
        comment_auto_position, PcbTextAutoposition, "comment_auto_position"
    )
    source_component_library_identifier_kind_value = _coerce_optional_enum(
        source_component_library_identifier_kind,
        PcbLibIdentifierKind,
        "source_component_library_identifier_kind",
    )
    x_mils, y_mils = position_mils
    unique_id = unique_id or _deterministic_component_unique_id(
        designator=designator,
        footprint=footprint,
        x_mils=x_mils,
        y_mils=y_mils,
        layer=layer_token,
        rotation_degrees=rotation_degrees,
    )
    raw_record: OrderedDict[str, str] = OrderedDict(
        (
            ("SELECTION", "FALSE"),
            ("LAYER", layer_token),
            ("LOCKED", "FALSE"),
            ("POLYGONOUTLINE", "FALSE"),
            ("USERROUTED", "TRUE"),
            ("KEEPOUT", "FALSE"),
            ("PRIMITIVELOCK", "TRUE"),
            ("X", _format_mil_value(x_mils)),
            ("Y", _format_mil_value(y_mils)),
            ("PATTERN", footprint),
            ("NAMEON", "TRUE" if name_on else "FALSE"),
            ("COMMENTON", "TRUE" if comment_on else "FALSE"),
            ("GROUPNUM", "0"),
            ("COUNT", "0"),
            ("ROTATION", _format_legacy_scientific(rotation_degrees)),
            ("UNIONINDEX", "0"),
            ("SOURCEFOOTPRINTLIBRARY", source_footprint_library),
            ("UNIQUEID", unique_id),
        )
    )
    if name_auto_position_value is not None:
        raw_record["NAMEAUTOPOSITION"] = str(int(name_auto_position_value))
    if comment_auto_position_value is not None:
        raw_record["COMMENTAUTOPOSITION"] = str(int(comment_auto_position_value))
    if channel_offset is not None:
        raw_record["CHANNELOFFSET"] = str(int(channel_offset))
    source_designator = designator if source_designator is None else source_designator
    _add_optional_text(raw_record, "SOURCEDESIGNATOR", source_designator)
    _add_optional_text(raw_record, "SOURCEUNIQUEID", source_unique_id)
    _add_optional_text(raw_record, "SOURCEHIERARCHICALPATH", source_hierarchical_path)
    _add_optional_text(raw_record, "SOURCECOMPONENTLIBRARY", source_component_library)
    if source_component_library_identifier_kind_value is not None:
        raw_record["SOURCECOMPLIBIDENTIFIERKIND"] = str(
            int(source_component_library_identifier_kind_value)
        )
    _add_optional_text(
        raw_record,
        "SOURCECOMPLIBRARYIDENTIFIER",
        source_component_library_identifier,
    )
    _add_optional_text(raw_record, "SOURCELIBREFERENCE", source_lib_reference)
    if description:
        raw_record["SOURCEDESCRIPTION"] = description
    _add_optional_text(raw_record, "FOOTPRINTDESCRIPTION", footprint_description)
    _add_optional_bool(raw_record, "LOCKSTRINGS", lock_strings)
    _add_optional_bool(raw_record, "ENABLEPINSWAPPING", enable_pin_swapping)
    _add_optional_bool(raw_record, "ENABLEPARTSWAPPING", enable_part_swapping)
    _add_optional_bool(raw_record, "JUMPERSVISIBLE", jumpers_visible)

    return AltiumPcbComponent(
        designator=designator,
        footprint=footprint,
        layer=layer_token,
        x=raw_record["X"],
        y=raw_record["Y"],
        rotation=raw_record["ROTATION"],
        unique_id=unique_id,
        union_index=int(raw_record["UNIONINDEX"]),
        description=description,
        raw_record=dict(raw_record),
    )


def make_authored_component_guid(
    component: AltiumPcbComponent, ordinal: int
) -> uuid.UUID:
    seed = (
        f"pcbdoc-builder-component-guid|{component.unique_id}|"
        f"{component.footprint}|{component.x}|{component.y}|{ordinal}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, seed)
