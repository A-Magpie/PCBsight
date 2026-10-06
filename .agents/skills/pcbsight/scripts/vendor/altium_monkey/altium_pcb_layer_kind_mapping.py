"""Typed codec for PCB mechanical layer kind mappings."""

from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .altium_pcb_enums import MechanicalLayerKind
from .altium_record_types import PcbLayer

LAYER_KIND_MAPPING_DEFAULT_UNKNOWN = 0
LAYER_KIND_MAPPING_EXTENDED_LAYER_PREFIX = 0x04000000

_MECHANICAL_LAYER_KIND_TOKENS: dict[MechanicalLayerKind, str] = {
    MechanicalLayerKind.UNDEFINED: "Undefined",
    MechanicalLayerKind.ASSEMBLY_TOP: "AssemblyTop",
    MechanicalLayerKind.ASSEMBLY_BOTTOM: "AssemblyBottom",
    MechanicalLayerKind.ASSEMBLY_NOTES: "AssemblyNotes",
    MechanicalLayerKind.BOARD: "Board",
    MechanicalLayerKind.COATING_TOP: "CoatingTop",
    MechanicalLayerKind.COATING_BOTTOM: "CoatingBottom",
    MechanicalLayerKind.COMPONENT_CENTER_TOP: "ComponentCenterTop",
    MechanicalLayerKind.COMPONENT_CENTER_BOTTOM: "ComponentCenterBottom",
    MechanicalLayerKind.COMPONENT_OUTLINE_TOP: "ComponentOutlineTop",
    MechanicalLayerKind.COMPONENT_OUTLINE_BOTTOM: "ComponentOutlineBottom",
    MechanicalLayerKind.COURTYARD_TOP: "CourtyardTop",
    MechanicalLayerKind.COURTYARD_BOTTOM: "CourtyardBottom",
    MechanicalLayerKind.DESIGNATOR_TOP: "DesignatorTop",
    MechanicalLayerKind.DESIGNATOR_BOTTOM: "DesignatorBottom",
    MechanicalLayerKind.DIMENSIONS: "Dimensions",
    MechanicalLayerKind.DIMENSIONS_TOP: "DimensionsTop",
    MechanicalLayerKind.DIMENSIONS_BOTTOM: "DimensionsBottom",
    MechanicalLayerKind.FAB_NOTES: "FabNotes",
    MechanicalLayerKind.GLUE_POINTS_TOP: "GluePointsTop",
    MechanicalLayerKind.GLUE_POINTS_BOTTOM: "GluePointsBottom",
    MechanicalLayerKind.GOLD_PLATING_TOP: "GoldPlatingTop",
    MechanicalLayerKind.GOLD_PLATING_BOTTOM: "GoldPlatingBottom",
    MechanicalLayerKind.VALUE_TOP: "ValueTop",
    MechanicalLayerKind.VALUE_BOTTOM: "ValueBottom",
    MechanicalLayerKind.VCUT: "VCut",
    MechanicalLayerKind.BODY_3D_TOP: "3DBodyTop",
    MechanicalLayerKind.BODY_3D_BOTTOM: "3DBodyBottom",
    MechanicalLayerKind.ROUTE_TOOL_PATH: "RouteToolPath",
    MechanicalLayerKind.SHEET: "Sheet",
    MechanicalLayerKind.BOARD_SHAPE: "BoardShape",
    MechanicalLayerKind.OVERLAY_TOP: "OverlayTop",
    MechanicalLayerKind.OVERLAY_BOTTOM: "OverlayBottom",
    MechanicalLayerKind.SOLDER_TOP: "SolderTop",
    MechanicalLayerKind.SOLDER_BOTTOM: "SolderBottom",
    MechanicalLayerKind.PASTE_TOP: "PasteTop",
    MechanicalLayerKind.PASTE_BOTTOM: "PasteBottom",
    MechanicalLayerKind.TENTING_TOP: "TentingTop",
    MechanicalLayerKind.TENTING_BOTTOM: "TentingBottom",
    MechanicalLayerKind.COVERING_TOP: "CoveringTop",
    MechanicalLayerKind.COVERING_BOTTOM: "CoveringBottom",
    MechanicalLayerKind.PLUGGING_TOP: "PluggingTop",
    MechanicalLayerKind.PLUGGING_BOTTOM: "PluggingBottom",
    MechanicalLayerKind.FILLING: "Filling",
    MechanicalLayerKind.CAPPING: "Capping",
    MechanicalLayerKind.DIE_PADS_TOP: "DiePadsTop",
    MechanicalLayerKind.DIE_PADS_BOTTOM: "DiePadsBottom",
    MechanicalLayerKind.WIREBONDING_TOP: "WirebondingTop",
    MechanicalLayerKind.WIREBONDING_BOTTOM: "WirebondingBottom",
}


def mechanical_layer_number_to_kind_mapping_id(mechanical_number: int) -> int:
    """Return the `LayerKindMapping/Data` layer id for a mechanical layer."""

    number = int(mechanical_number)
    if not 1 <= number <= 32:
        raise ValueError(f"Mechanical layer number must be in range 1..32: {number}")
    if number <= 16:
        return PcbLayer.MECHANICAL_1.value + number - 1
    return LAYER_KIND_MAPPING_EXTENDED_LAYER_PREFIX | number


def mechanical_layer_number_to_legacy_layer_id(
    mechanical_number: int,
) -> int | None:
    """Return the classic PCB layer id for Mechanical 1..16."""

    number = int(mechanical_number)
    if 1 <= number <= 16:
        return PcbLayer.MECHANICAL_1.value + number - 1
    return None


def mechanical_layer_set_token(mechanical_number: int) -> str:
    """Return the layer-set token for a mechanical layer number."""

    return f"Mechanical{int(mechanical_number)}"


# Deterministic namespace for authored mechanical layer-cache row ids shared by
# the PcbDoc and PcbLib builders.
_MECHANICAL_LAYER_ROW_ID_NAMESPACE = uuid.UUID("c5a4fe4c-4c13-5f4b-83b5-9b6f72ec6d04")


def mechanical_layer_v9_cache_index(mechanical_number: int) -> int:
    """Return the V9 layer-cache group index for a mechanical layer number."""

    if mechanical_number <= 16:
        return 68 + mechanical_number
    return 69 + mechanical_number


def mechanical_layer_v8_index(mechanical_number: int) -> int:
    """Return the V8 layer-cache group index for a mechanical layer number."""

    if mechanical_number <= 16:
        return 10 + mechanical_number
    return 23 + mechanical_number


def authored_mechanical_layer_row_id(mechanical_number: int) -> str:
    """Return the braced deterministic row GUID for an authored mechanical layer."""

    row_id = uuid.uuid5(
        _MECHANICAL_LAYER_ROW_ID_NAMESPACE,
        f"mechanical-layer:{int(mechanical_number)}",
    )
    return "{" + str(row_id).upper() + "}"


def kind_mapping_id_to_mechanical_layer_number(layer_id: int) -> int | None:
    """Return the mechanical layer number represented by a mapping layer id."""

    layer_id = int(layer_id)
    if PcbLayer.MECHANICAL_1.value <= layer_id <= PcbLayer.MECHANICAL_16.value:
        return layer_id - PcbLayer.MECHANICAL_1.value + 1
    if layer_id & LAYER_KIND_MAPPING_EXTENDED_LAYER_PREFIX:
        number = layer_id & 0xFFFF
        if 17 <= number <= 32:
            return number
    return None


def _coerce_mechanical_layer_number_text(text: str) -> int | None:
    token = "".join(ch for ch in text.upper() if ch.isalnum())
    if token.startswith("MECHANICAL"):
        suffix = token.removeprefix("MECHANICAL")
        if suffix.isdigit():
            return int(suffix)
    if token.isdigit():
        return int(token)
    return None


def coerce_layer_kind_mapping_layer_id(layer: int | str | PcbLayer) -> int:
    """Coerce a mechanical layer token into a `LayerKindMapping/Data` layer id."""

    if isinstance(layer, PcbLayer):
        layer_id = int(layer)
        if kind_mapping_id_to_mechanical_layer_number(layer_id) is None:
            raise ValueError(f"Layer is not mechanical: {layer!r}")
        return layer_id

    if isinstance(layer, str):
        number = _coerce_mechanical_layer_number_text(layer)
        if number is None:
            try:
                parsed = PcbLayer.from_json_name(layer)
            except ValueError as exc:
                raise ValueError(f"Unsupported mechanical layer: {layer!r}") from exc
            return coerce_layer_kind_mapping_layer_id(parsed)
        return mechanical_layer_number_to_kind_mapping_id(number)

    layer_id = int(layer)
    if kind_mapping_id_to_mechanical_layer_number(layer_id) is not None:
        return layer_id
    if 1 <= layer_id <= 32:
        return mechanical_layer_number_to_kind_mapping_id(layer_id)
    raise ValueError(f"Layer is not mechanical: {layer!r}")


def split_layer_set_nonmechanical_parts(
    layers: tuple[str, ...],
) -> tuple[tuple[str, ...], bool, tuple[str, ...]]:
    """Split non-mechanical prefix/suffix tokens around layer-set controls."""

    first_control_index = next(
        (
            index
            for index, layer in enumerate(layers)
            if layer.upper().startswith("MECHANICAL") or layer.upper() == "DRILLDRAWING"
        ),
        len(layers),
    )
    prefix = layers[:first_control_index]
    suffix = tuple(
        layer
        for layer in layers[first_control_index:]
        if not layer.upper().startswith("MECHANICAL")
        and layer.upper() != "DRILLDRAWING"
    )
    has_drill_drawing = any(layer.upper() == "DRILLDRAWING" for layer in layers)
    return prefix, has_drill_drawing, suffix


def coerce_mechanical_layer_kind(
    kind: int | str | MechanicalLayerKind,
) -> MechanicalLayerKind:
    """Coerce a public mechanical layer kind value."""

    if isinstance(kind, MechanicalLayerKind):
        return kind
    if isinstance(kind, str):
        token = kind.strip().upper()
        if token.isdigit():
            return MechanicalLayerKind(int(token))
        normalized = "".join(ch if ch.isalnum() else "_" for ch in token)
        while "__" in normalized:
            normalized = normalized.replace("__", "_")
        normalized = normalized.strip("_")
        try:
            return MechanicalLayerKind[normalized]
        except KeyError as exc:
            compact = "".join(ch for ch in token if ch.isalnum())
            for candidate in MechanicalLayerKind:
                if candidate.name.replace("_", "") == compact:
                    return candidate
            raise ValueError(f"Unsupported mechanical layer kind: {kind!r}") from exc
    return MechanicalLayerKind(int(kind))


def mechanical_layer_kind_to_data_token(
    kind: int | str | MechanicalLayerKind,
) -> str:
    """Return the Board6/Library `MECHKIND` token used by Altium."""

    return _MECHANICAL_LAYER_KIND_TOKENS[coerce_mechanical_layer_kind(kind)]


@dataclass(frozen=True)
class PcbLayerKindMapping:
    """Decoded `LayerKindMapping/Data` stream."""

    format_version: str = "1.0"
    unknown: int = LAYER_KIND_MAPPING_DEFAULT_UNKNOWN
    entries: tuple[tuple[int, MechanicalLayerKind], ...] = ()

    @classmethod
    def make_default(cls) -> "PcbLayerKindMapping":
        return cls()

    @classmethod
    def from_bytes(cls, data: bytes) -> "PcbLayerKindMapping":
        if len(data) < 16:
            raise ValueError("Invalid LayerKindMapping stream")
        text_len = struct.unpack_from("<I", data, 0)[0]
        offset = 4
        if len(data) < offset + text_len + 8:
            raise ValueError("Invalid LayerKindMapping stream")
        version = data[offset : offset + text_len].decode("utf-16le").rstrip("\x00")
        offset += text_len
        unknown, entry_count = struct.unpack_from("<II", data, offset)
        offset += 8
        expected_len = offset + (entry_count * 8)
        if len(data) != expected_len:
            raise ValueError("Invalid LayerKindMapping stream length")

        entries: list[tuple[int, MechanicalLayerKind]] = []
        for _ in range(entry_count):
            layer_id, kind_value = struct.unpack_from("<II", data, offset)
            offset += 8
            entries.append((layer_id, MechanicalLayerKind(kind_value)))
        return cls(format_version=version, unknown=unknown, entries=tuple(entries))

    @property
    def mapping(self) -> Mapping[int, MechanicalLayerKind]:
        return MappingProxyType(dict(self.entries))

    @property
    def reserved_tail(self) -> bytes:
        """Compatibility view of the former opaque tail bytes."""

        return struct.pack("<II", self.unknown, len(self.entries)) + b"".join(
            struct.pack("<II", layer_id, int(kind)) for layer_id, kind in self.entries
        )

    def to_bytes(self) -> bytes:
        version_bytes = (self.format_version + "\x00").encode("utf-16le")
        buf = bytearray()
        buf.extend(struct.pack("<I", len(version_bytes)))
        buf.extend(version_bytes)
        buf.extend(struct.pack("<II", int(self.unknown), len(self.entries)))
        for layer_id, kind in self.entries:
            buf.extend(struct.pack("<II", int(layer_id), int(kind)))
        return bytes(buf)

    def with_layer_kind(
        self,
        layer: int | str | PcbLayer,
        kind: int | str | MechanicalLayerKind,
    ) -> "PcbLayerKindMapping":
        layer_id = coerce_layer_kind_mapping_layer_id(layer)
        kind_value = coerce_mechanical_layer_kind(kind)

        entries: list[tuple[int, MechanicalLayerKind]] = []
        updated = False
        for existing_layer_id, existing_kind in self.entries:
            if existing_layer_id == layer_id:
                entries.append((layer_id, kind_value))
                updated = True
            else:
                entries.append((existing_layer_id, existing_kind))
        if not updated:
            entries.append((layer_id, kind_value))
        return type(self)(
            format_version=self.format_version,
            unknown=self.unknown,
            entries=tuple(entries),
        )
