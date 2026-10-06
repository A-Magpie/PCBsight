"""
Parse PCB region primitive records.
"""

import html
import logging
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .altium_pcb_enums import (
    PcbRegionKind,
    pcb_region_kind_from_native_kind,
    pcb_region_kind_to_native_kind,
)
from .altium_pcb_layer_ref import PcbLayerRef, resolve_pcb_primitive_layer_state
from .altium_record_pcb__svg_support import (
    svg_fill_color,
    svg_layer_ref,
    svg_matches_requested_layer,
)
from .altium_record_types import (
    PcbGraphicalObject,
    PcbLayer,
    PcbRecordType,
    PcbV7LayerTokenMixin,
)

if TYPE_CHECKING:
    from .altium_pcb_layer_ref import PcbLayerRegistry, PcbPrimitiveLayerState
    from .altium_pcb_svg_renderer import PcbSvgRenderContext

log = logging.getLogger(__name__)


@dataclass
class RegionVertex:
    """
    Single vertex in a region outline or hole contour.

        Stores coordinates in raw internal units (1 mil = 10000 units) to avoid
        floating-point round-trip precision loss when dividing/multiplying by 10000.
    """

    x_raw: float  # Internal units (10000 per mil)
    y_raw: float  # Internal units (10000 per mil)

    @property
    def x_mils(self) -> float:
        return self.x_raw / 10000.0

    @property
    def y_mils(self) -> float:
        return self.y_raw / 10000.0


class AltiumPcbRegion(PcbV7LayerTokenMixin, PcbGraphicalObject):
    """
    PCB region primitive record.

    Represents a filled polygon region on PCB (copper pour, cutout, keepout).

    Attributes:
        layer: Layer number (1=TOP, 32=BOTTOM, etc.)
        polygon_index: Links to APOLYGON6 record (uint16)
        component_index: Links to component (uint16, 0xFFFF=unlinked)
        net_index: Links to net (uint16, 0xFFFF=unlinked)
        hole_count: Number of holes in the polygon
        is_locked: Locked for editing (inverted bit logic)
        is_keepout: Keepout region
        is_polygon_outline: Part of polygon outline
        outline_vertex_count: Number of outline vertices
        outline_vertices: List of RegionVertex for outline contour
        hole_vertices: List of lists of RegionVertex for each hole
        properties: Dict of region properties (KIND, ISBOARDCUTOUT, etc.)
        kind: Region kind (0=copper, 1=cutout, 2=named, 3=board_cutout)
        cavity_height: Cavity definition height in internal units.
    """

    def __init__(self) -> None:
        super().__init__()
        self.polygon_index: int = 0
        self.hole_count: int = 0
        self.outline_vertex_count: int = 0
        self.outline_vertices: list[RegionVertex] = []
        self.hole_vertices: list[list[RegionVertex]] = []
        self.properties: dict[str, str] = {}
        self.kind: int = 0
        self.union_index: int = 0
        self.is_board_cutout: bool = False
        self.is_shapebased: bool = False
        self.keepout_restrictions: int = 0
        self.subpoly_index: int = 0
        self.cavity_height: int | str = 0

        # Raw byte preservation for byte-identical round-trip
        self._flags1_raw: int = 0x04  # Full flags1 byte (not just known bits)
        self._skip_bytes_9: bytes = (
            b"\x00" * 5
        )  # Bytes at offset 9-13 (union_index + pad)
        self._skip_bytes_16: bytes = b"\x00" * 2  # Bytes at offset 16-17
        self._properties_raw: bytes | None = None  # Original properties string bytes
        self._properties_raw_signature: tuple | None = (
            None  # Snapshot of property fields at parse time
        )
        self._properties_emit_trailing_null: bool = False
        self._properties_include_keepout_restrictions: bool = True
        self._trailing_data: bytes = b""
        self._trailing_data_outline_signature: tuple | None = None
        self._emit_extended_outline_tail: bool = False
        self._raw_binary: bytes | None = None
        self._raw_binary_signature: tuple | None = None

    @property
    def record_type(self) -> PcbRecordType:
        """
        Return the PCB region record discriminator.
        """
        return PcbRecordType.REGION

    def layer_state(
        self,
        registry: "PcbLayerRegistry | None" = None,
    ) -> "PcbPrimitiveLayerState":
        """Return the V7-aware layer identity plus stored layer diagnostics."""

        return resolve_pcb_primitive_layer_state(
            self,
            registry=registry,
            v7_saved_layer_id_field=None,
            v7_layer_token_field="v7_layer",
        )

    def layer_ref(
        self,
        registry: "PcbLayerRegistry | None" = None,
    ) -> "PcbLayerRef":
        """Return the V7-aware layer identity for this region."""

        return self.layer_state(registry=registry).ref

    def parse_from_binary(self, data: bytes, offset: int = 0) -> int:
        """
        Parse REGION record from binary data.

        Args:
            data: Binary data containing the record
            offset: Starting offset in data (default 0)

        Returns:
            Number of bytes consumed

        Raises:
            ValueError: If data is invalid or too short
        """
        if len(data) < offset + 1:
            raise ValueError("Data too short for REGION record")

        cursor = offset

        # Verify type byte
        type_byte = data[cursor]
        if type_byte != PcbRecordType.REGION:
            raise ValueError(
                f"Invalid REGION type byte: 0x{type_byte:02X} "
                f"(expected 0x{int(PcbRecordType.REGION):02X})"
            )
        cursor += 1

        # Parse SubRecord 1: Main data
        if len(data) < cursor + 4:
            raise ValueError("Data too short for SubRecord length")

        subrecord_len = struct.unpack("<I", data[cursor : cursor + 4])[0]
        cursor += 4

        if len(data) < cursor + subrecord_len:
            raise ValueError(
                f"SubRecord truncated: expected {subrecord_len} bytes, got {len(data) - cursor}"
            )

        content = data[cursor : cursor + subrecord_len]
        cursor += subrecord_len

        # Parse fixed header (first 18 bytes)
        if len(content) < 18:
            log.warning(
                f"REGION SubRecord shorter than expected: {len(content)} bytes (expected >=18)"
            )

        pos = 0

        # Layer (offset 0)
        self.layer = content[pos]
        pos += 1

        # Flags1 (offset 1)
        flags1 = content[pos]
        self._flags1_raw = flags1  # Preserve full byte for round-trip
        self.is_locked = (flags1 & 0x04) == 0  # Inverted logic
        self.is_polygon_outline = (flags1 & 0x02) != 0
        pos += 1

        # Flags2 (offset 2)
        flags2 = content[pos]
        self.is_keepout = flags2 == 0x02
        pos += 1

        # Net index (offset 3-4)
        self.net_index = struct.unpack("<H", content[pos : pos + 2])[0]
        if self.net_index == 0xFFFF:
            self.net_index = None
        pos += 2

        # Polygon index (offset 5-6)
        self.polygon_index = struct.unpack("<H", content[pos : pos + 2])[0]
        pos += 2

        # Component index (offset 7-8)
        self.component_index = struct.unpack("<H", content[pos : pos + 2])[0]
        if self.component_index == 0xFFFF:
            self.component_index = None
        pos += 2

        # Bytes at offset 9-13 (union_index + padding, typically 0xFFFFFFFF 0x00)
        self._skip_bytes_9 = bytes(content[pos : pos + 5])
        pos += 5

        # Hole count (offset 14-15)
        self.hole_count = struct.unpack("<H", content[pos : pos + 2])[0]
        pos += 2

        # Bytes at offset 16-17
        self._skip_bytes_16 = bytes(content[pos : pos + 2])
        pos += 2

        # Properties section (offset 18+)
        # Format: uint32 length + ASCII pipe-separated key=value string
        if pos + 4 <= len(content):
            prop_len = struct.unpack("<I", content[pos : pos + 4])[0]
            pos += 4

            if pos + prop_len <= len(content):
                self._properties_raw = bytes(content[pos : pos + prop_len])
                self._properties_emit_trailing_null = self._properties_raw.endswith(
                    b"\x00"
                )
                props_str = self._properties_raw.decode("ascii", errors="replace")
                pos += prop_len
                self.properties = self._parse_properties(props_str)
                self.kind = int(self.properties.get("KIND", "0"))
                self.is_board_cutout = (
                    self.properties.get("ISBOARDCUTOUT", "FALSE").upper() == "TRUE"
                )
                self.is_shapebased = (
                    self.properties.get("ISSHAPEBASED", "FALSE").upper() == "TRUE"
                )
                self.keepout_restrictions = int(
                    self.properties.get("KEEPOUTRESTRIC", "0")
                )
                self.subpoly_index = int(self.properties.get("SUBPOLYINDEX", "0"))
                self.union_index = int(
                    self.properties.get("UNIONINDEX", "0").strip().strip("\x00") or "0"
                )
                self.cavity_height = self._parse_mil_value(
                    self.properties.get("CAVITYHEIGHT", "0mil")
                )
            else:
                log.warning(
                    f"REGION properties truncated: need {prop_len}, have {len(content) - pos}"
                )

        # Outline vertex count (uint32)
        if pos + 4 <= len(content):
            self.outline_vertex_count = struct.unpack("<I", content[pos : pos + 4])[0]
            pos += 4

            # Parse outline vertices (16 bytes each: double x, double y)
            self.outline_vertices = []
            for _ in range(self.outline_vertex_count):
                if pos + 16 > len(content):
                    log.warning("REGION: outline vertex data truncated")
                    break
                vx, vy = struct.unpack("<dd", content[pos : pos + 16])
                self.outline_vertices.append(RegionVertex(x_raw=vx, y_raw=vy))
                pos += 16

        # Parse hole vertices
        self.hole_vertices = []
        for _ in range(self.hole_count):
            if pos + 4 > len(content):
                log.warning("REGION: hole vertex count truncated")
                break
            hole_vert_count = struct.unpack("<I", content[pos : pos + 4])[0]
            pos += 4

            hole_verts = []
            for _ in range(hole_vert_count):
                if pos + 16 > len(content):
                    log.warning("REGION: hole vertex data truncated")
                    break
                vx, vy = struct.unpack("<dd", content[pos : pos + 16])
                hole_verts.append(RegionVertex(x_raw=vx, y_raw=vy))
                pos += 16
            self.hole_vertices.append(hole_verts)

        self._trailing_data = bytes(content[pos:]) if pos < len(content) else b""
        self._trailing_data_outline_signature = (
            self._outline_signature_for_trailing_data()
        )

        log.debug(
            f"REGION: layer={self.layer}, kind={self.kind}, "
            f"verts={self.outline_vertex_count}, holes={self.hole_count}, "
            f"net={self.net_index}, poly={self.polygon_index}"
        )

        # Store raw binary for round-trip
        self._raw_binary = data[offset:cursor]
        self._raw_binary_signature = self._state_signature()
        self._properties_raw_signature = self._properties_field_signature()

        return cursor - offset

    @staticmethod
    def _parse_properties(props_str: str) -> dict:
        """
        Parse pipe-separated key=value properties string.
        """
        result = {}
        for part in props_str.split("|"):
            part = part.strip().strip("\x00")
            if "=" in part:
                key, _, value = part.partition("=")
                result[key.strip().strip("\x00")] = value.strip().strip("\x00")
        return result

    @staticmethod
    def _parse_mil_value(value_str: object) -> int:
        """
        Parse a native mil-unit text value to internal units.
        """
        text = str(value_str or "").strip().strip("\x00")
        if text.lower().endswith("mil"):
            try:
                return int(round(float(text[:-3].strip()) * 10000.0))
            except ValueError:
                return 0
        if text:
            try:
                return int(round(float(text)))
            except ValueError:
                return 0
        return 0

    def _cavity_height_internal_units(self) -> int:
        """
        Return cavity height as internal units for both primitive and board regions.
        """
        value = getattr(self, "cavity_height", 0)
        if isinstance(value, int):
            return int(value)
        if isinstance(value, float):
            return int(round(value))
        return self._parse_mil_value(value)

    @staticmethod
    def _format_mil_value(internal_units: int) -> str:
        """
        Format an internal-unit value as '<mils>mil'.
        """
        return f"{format(float(internal_units) / 10000.0, '.12g')}mil"

    @property
    def region_kind(self) -> PcbRegionKind:
        """
        Return the semantic public region kind for the native record fields.
        """
        return pcb_region_kind_from_native_kind(
            int(self.kind),
            is_board_cutout=self.is_board_cutout,
        )

    @property
    def cavity_height_mils(self) -> float:
        """
        Cavity definition height in mils.
        """
        return self._cavity_height_internal_units() / 10000.0

    def _properties_field_signature(self) -> tuple:
        """
        Snapshot of typed fields that contribute to the properties string.
        """
        return (
            int(self.kind),
            bool(self.is_board_cutout),
            bool(self.is_shapebased),
            int(self.keepout_restrictions),
            int(self.subpoly_index),
            int(self.union_index),
            self._cavity_height_internal_units(),
            tuple(sorted((str(k), str(v)) for k, v in self.properties.items())),
        )

    def _state_signature(self) -> tuple:
        """
        Return a stable signature of semantically known REGION fields.
        """
        return (
            int(self.layer),
            int(self.net_index) if self.net_index is not None else 0xFFFF,
            int(self.polygon_index),
            int(self.component_index) if self.component_index is not None else 0xFFFF,
            bool(self.is_locked),
            bool(self.is_keepout),
            bool(self.is_polygon_outline),
            int(self.kind),
            bool(self.is_board_cutout),
            bool(self.is_shapebased),
            int(self.keepout_restrictions),
            int(self.subpoly_index),
            int(self.union_index),
            self._cavity_height_internal_units(),
            tuple((v.x_raw, v.y_raw) for v in self.outline_vertices),
            tuple(
                tuple((v.x_raw, v.y_raw) for v in hole) for hole in self.hole_vertices
            ),
            tuple(sorted((str(k), str(v)) for k, v in self.properties.items())),
            int(self._flags1_raw),
            self._skip_bytes_9,
            self._skip_bytes_16,
        )

    def _properties_string(self) -> str:
        """
        Build pipe-separated region properties from object state.
        """
        props: dict[str, str] = {str(k): str(v) for k, v in self.properties.items()}
        props["KIND"] = str(pcb_region_kind_to_native_kind(self.kind))
        props["ISBOARDCUTOUT"] = "TRUE" if self.is_board_cutout else "FALSE"
        props["ISSHAPEBASED"] = "TRUE" if self.is_shapebased else "FALSE"
        if self._properties_include_keepout_restrictions or "KEEPOUTRESTRIC" in props:
            props["KEEPOUTRESTRIC"] = str(int(self.keepout_restrictions))
        props["SUBPOLYINDEX"] = str(int(self.subpoly_index))
        props["UNIONINDEX"] = str(int(self.union_index))
        cavity_height = getattr(self, "cavity_height", 0)
        if cavity_height or "CAVITYHEIGHT" in props or int(self.kind) == 4:
            if isinstance(cavity_height, str):
                props["CAVITYHEIGHT"] = cavity_height
            else:
                props["CAVITYHEIGHT"] = self._format_mil_value(
                    self._cavity_height_internal_units()
                )
        return "|".join(f"{k}={v}" for k, v in props.items())

    def serialize_to_binary(self) -> bytes:
        """
        Serialize REGION record to binary format.

        Returns:
            Binary data ready to write to stream

        Strategy:
            Reuse raw binary only when semantic fields are unchanged.
            Otherwise emit deterministic property + vertex arrays.
        """
        state_sig = self._state_signature()
        cached_sig = getattr(self, "_raw_binary_signature", None)
        if self._raw_binary is not None and cached_sig == state_sig:
            return self._raw_binary

        subrecord = bytearray()
        subrecord.append(max(0, min(255, int(self.layer))))

        # Reconstruct flags1 from raw value, updating only known semantic bits
        flags1 = self._flags1_raw
        # Clear known bits, then set from fields
        flags1 = (
            flags1 & ~0x06
        )  # Clear bits 1 (polygon_outline) and 2 (locked-inverted)
        if not self.is_locked:
            flags1 |= 0x04
        if self.is_polygon_outline:
            flags1 |= 0x02
        subrecord.append(flags1)
        subrecord.append(0x02 if self.is_keepout else 0x00)

        net_index = (
            0xFFFF
            if self.net_index is None
            else max(0, min(0xFFFF, int(self.net_index)))
        )
        comp_index = (
            0xFFFF
            if self.component_index is None
            else max(0, min(0xFFFF, int(self.component_index)))
        )
        subrecord.extend(struct.pack("<H", net_index))
        subrecord.extend(
            struct.pack("<H", max(0, min(0xFFFF, int(self.polygon_index))))
        )
        subrecord.extend(struct.pack("<H", comp_index))
        subrecord.extend(self._skip_bytes_9)

        holes = self.hole_vertices or []
        subrecord.extend(struct.pack("<H", max(0, min(0xFFFF, len(holes)))))
        subrecord.extend(self._skip_bytes_16)

        # Use original properties bytes if available and property fields are unchanged
        props_unchanged = (
            self._properties_raw is not None
            and self._properties_raw_signature is not None
            and self._properties_field_signature() == self._properties_raw_signature
        )
        if props_unchanged:
            properties_raw = self._properties_raw
            if properties_raw is None:
                raise RuntimeError("Missing raw region properties")
            subrecord.extend(struct.pack("<I", len(properties_raw)))
            subrecord.extend(properties_raw)
        else:
            props_bytes = self._properties_string().encode("ascii", errors="replace")
            if self._properties_emit_trailing_null and not props_bytes.endswith(
                b"\x00"
            ):
                props_bytes += b"\x00"
            subrecord.extend(struct.pack("<I", len(props_bytes)))
            subrecord.extend(props_bytes)

        outline = self.outline_vertices or []
        subrecord.extend(struct.pack("<I", len(outline)))
        for vertex in outline:
            subrecord.extend(struct.pack("<dd", vertex.x_raw, vertex.y_raw))

        for hole in holes:
            subrecord.extend(struct.pack("<I", len(hole)))
            for vertex in hole:
                subrecord.extend(struct.pack("<dd", vertex.x_raw, vertex.y_raw))

        subrecord.extend(self._trailing_bytes_for_write())

        record = bytearray()
        record.append(0x0B)  # REGION type
        record.extend(struct.pack("<I", len(subrecord)))
        record.extend(subrecord)

        result = bytes(record)
        self._raw_binary = result
        self._raw_binary_signature = state_sig
        return result

    def _trailing_bytes_for_write(self) -> bytes:
        """
        Return any native contour tail after the simple REGION hole list.

        BoardRegions/Data stores an additional extended-vertex contour after
        the simple double-coordinate outline. Ordinary Regions6 records do not
        need this tail unless one was parsed from native source.
        """
        outline_signature = self._outline_signature_for_trailing_data()
        if (
            self._trailing_data
            and self._trailing_data_outline_signature == outline_signature
        ):
            return self._trailing_data
        if self._emit_extended_outline_tail:
            return self._build_extended_outline_tail()
        return b""

    def _outline_signature_for_trailing_data(self) -> tuple:
        return tuple(
            (float(vertex.x_raw), float(vertex.y_raw))
            for vertex in tuple(self.outline_vertices or ())
        )

    def _build_extended_outline_tail(self) -> bytes:
        vertices = list(self.outline_vertices or [])
        if not vertices:
            return b""
        output_vertices = list(vertices)
        first = vertices[0]
        last = vertices[-1]
        if (float(first.x_raw), float(first.y_raw)) != (
            float(last.x_raw),
            float(last.y_raw),
        ):
            output_vertices.append(first)

        result = bytearray()
        result.extend(struct.pack("<I", max(0, len(output_vertices) - 1)))
        for vertex in output_vertices:
            result.append(0)
            result.extend(struct.pack("<i", int(round(float(vertex.x_raw)))))
            result.extend(struct.pack("<i", int(round(float(vertex.y_raw)))))
            result.extend(struct.pack("<i", 0))
            result.extend(struct.pack("<i", 0))
            result.extend(struct.pack("<i", 0))
            result.extend(struct.pack("<d", 0.0))
            result.extend(struct.pack("<d", 0.0))
        return bytes(result)

    def _path_from_vertices(
        self, ctx: "PcbSvgRenderContext", vertices: list[RegionVertex]
    ) -> str:
        """
        Build an SVG subpath from region vertices.
        """
        if len(vertices) < 3:
            return ""

        parts = []
        first = vertices[0]
        parts.append(
            f"M {ctx.fmt(ctx.x_to_svg(first.x_mils))} {ctx.fmt(ctx.y_to_svg(first.y_mils))}"
        )
        for vertex in vertices[1:]:
            parts.append(
                f"L {ctx.fmt(ctx.x_to_svg(vertex.x_mils))} {ctx.fmt(ctx.y_to_svg(vertex.y_mils))}"
            )
        parts.append("Z")
        return " ".join(parts)

    def to_svg(
        self,
        ctx: "PcbSvgRenderContext | None" = None,
        *,
        stroke: str | None = None,
        include_metadata: bool = True,
        for_layer: PcbLayer | PcbLayerRef | None = None,
    ) -> list[str]:
        """
        Render region as filled contour with optional hole cutouts.
        """
        if ctx is None:
            return []
        layer_ref = svg_layer_ref(self)
        if not svg_matches_requested_layer(self, for_layer, layer_ref):
            return []
        if not self.outline_vertices or len(self.outline_vertices) < 3:
            return []

        color = stroke if stroke is not None else svg_fill_color(self, ctx, layer_ref)

        subpaths: list[str] = []
        main_path = self._path_from_vertices(ctx, self.outline_vertices)
        if not main_path:
            return []
        subpaths.append(main_path)

        for hole in self.hole_vertices:
            hole_path = self._path_from_vertices(ctx, hole)
            if hole_path:
                subpaths.append(hole_path)

        attrs = [
            f'd="{" ".join(subpaths)}"',
            f'fill="{html.escape(color)}"',
            'fill-rule="evenodd"',
            'stroke="none"',
        ]

        if include_metadata:
            attrs.extend(self._svg_metadata_attrs(ctx, layer_ref))

        return [f"<path {' '.join(attrs)}/>"]

    def _svg_metadata_attrs(
        self,
        ctx: "PcbSvgRenderContext",
        layer_ref: PcbLayerRef | None,
    ) -> list[str]:
        attrs = ['data-primitive="region"']
        layer_identifier: int | None
        if layer_ref is not None:
            attrs.extend(ctx.layer_metadata_attrs_for_ref(layer_ref))
            layer_identifier = ctx.layer_identifier_for_ref(layer_ref)
        else:
            attrs.extend(ctx.layer_metadata_attrs(int(self.layer)))
            layer_identifier = int(self.layer)
        attrs.append(f'data-kind="{self.kind}"')
        attrs.extend(
            ctx.relationship_metadata_attrs(
                net_index=self.net_index,
                component_index=self.component_index,
            )
        )
        element_id_attr = ctx.primitive_id_attr(
            "region",
            self,
            layer_id=layer_identifier,
            role="main",
        )
        if element_id_attr:
            attrs.append(element_id_attr)
        return attrs

    def __repr__(self) -> str:
        return (
            f"<AltiumPcbRegion layer={self.layer} "
            f"poly_idx={self.polygon_index} holes={self.hole_count}>"
        )
