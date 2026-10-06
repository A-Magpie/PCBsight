"""Schematic record model for SchRecordType.BUS."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_record_sch__wire import AltiumSchWire
from .altium_record_types import SchRecordType


class AltiumSchBus(AltiumSchWire):
    """
    BUS record.

    Represents a bus (multi-signal) connection.
    Inherits all behavior from WIRE.
    """

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.BUS

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
        kind: str = "bus",
        object_id: str = "eBus",
        default_color_raw: int = 0x000000,
        stroke_width_mils_override: float | None = 3.0,
        junction_color_raw: int = 0x800000,
        junction_size_px: float = 6.0,
    ) -> "SchGeometryRecord | None":
        """
        Build an oracle-aligned geometry record for a bus path.
        """
        return super().to_geometry(
            ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            kind=kind,
            object_id=object_id,
            default_color_raw=default_color_raw,
            stroke_width_mils_override=stroke_width_mils_override,
            junction_color_raw=junction_color_raw,
            junction_size_px=junction_size_px,
        )

    def __repr__(self) -> str:
        legacy_vertices = getattr(self, "vertices", None)
        vertex_count = (
            len(legacy_vertices) if legacy_vertices is not None else len(self.points)
        )
        return f"<AltiumSchBus vertices={vertex_count}>"
