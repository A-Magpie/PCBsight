"""
PCB SVG renderer scaffold.

Starts with a shared context/options model and stable SVG document
composition so record-level to_svg() implementations can plug in incrementally.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
import hashlib
import html
import inspect
import json
import logging
import math
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from .altium_board import resolve_outline_arc_segment
from .altium_pcb_drill_rendering import should_render_via_drill_hole
from .altium_embedded_font_helpers import safe_embedded_font_filename_component
from .altium_resolved_layer_stack import resolved_layer_stack_from_pcbdoc
from .altium_pcb_special_strings import (
    normalize_project_parameters,
    substitute_pcb_special_strings,
)
from .altium_record_types import PcbLayer
from .altium_pcb_layer_ref import (
    PcbLayerFamily,
    PcbLayerLike,
    PcbLayerRef,
    PcbLayerResolutionError,
    PcbSignalLayerKind,
    coerce_pcb_layer_ref,
)

if TYPE_CHECKING:
    from .altium_pcbdoc import AltiumPcbDoc
    from .altium_board import AltiumBoardOutline, BoardOutlineVertex
    from .altium_resolved_layer_stack import ResolvedLayerStack


_MIL_TO_MM = 0.0254
_PX_TO_MM = 25.4 / 96.0
log = logging.getLogger(__name__)
SVG_ENRICHMENT_SCHEMA_ID = "altium_monkey.pcb.svg.enrichment.a0"
SVG_ENRICHMENT_METADATA_ID = "pcb-enrichment-a0"
_UNLINKED_INDEX_SENTINELS = {-1, 0xFFFF, 65535}
PCB_SVG_DRILLS_LAYER_ID = 9001
PCB_SVG_DRILLS_LAYER_KEY = "DRILLS"
PCB_SVG_DRILLS_LAYER_NAME = "DRILLS"
PCB_SVG_DRILLS_LAYER_DISPLAY_NAME = "Drill Holes"
_SIGNAL_KIND_DISPLAY_NAMES = {
    PcbSignalLayerKind.TOP: "Top Layer",
    PcbSignalLayerKind.BOTTOM: "Bottom Layer",
}
_SIGNAL_KIND_SORT_INDEXES = {
    PcbSignalLayerKind.TOP: 1,
    PcbSignalLayerKind.BOTTOM: 129,
}


@dataclass(frozen=True)
class _PcbSvgLayerMetadata:
    token: str
    layer_key: str
    name: str
    display_name: str
    role: str
    family: str
    legacy_layer_id: int | None = None
    v7_saved_layer_id: int | None = None
    runtime_v7_layer_id: int | None = None
    kind_id: int | None = None
    custom_name: str | None = None
    semantic_key: str | None = None


@dataclass(frozen=True, eq=False)
class _PcbSvgRenderLayer:
    ref: PcbLayerRef

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _PcbSvgRenderLayer):
            return self.ref == other.ref
        if isinstance(other, PcbLayer):
            return self.legacy_layer == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.ref)

    @property
    def token(self) -> str:
        return self.ref.token

    @property
    def legacy_layer(self) -> PcbLayer | None:
        return self.ref.legacy_layer

    @property
    def legacy_layer_id(self) -> int | None:
        return self.ref.legacy_layer_id

    @property
    def value(self) -> int:
        if self.legacy_layer_id is not None:
            return self.legacy_layer_id
        if self.ref.v7_saved_layer_id is not None:
            return self.ref.v7_saved_layer_id
        if self.ref.runtime_v7_layer_id is not None:
            return self.ref.runtime_v7_layer_id
        raise PcbLayerResolutionError(f"Layer {self.token} has no numeric identity")

    def to_json_name(self) -> str:
        return self.token

    def is_copper(self) -> bool:
        return self.ref.family in {
            PcbLayerFamily.SIGNAL,
            PcbLayerFamily.INTERNAL_PLANE,
        }

    def is_legacy_copper(self) -> bool:
        return self.legacy_layer is not None and self.legacy_layer.is_copper()

    @property
    def cache_key(self) -> str:
        return self.token


@dataclass
class PcbSvgRenderOptions:
    """
    Top-level rendering options for PCB SVG output.
    """

    # Select renderable native layers. When this is an ordered sequence and
    # layer_render_order is unset, composed board output preserves that order.
    visible_layers: Collection[PcbLayerLike] | None = None
    # Optional explicit native-layer render order for composed board output.
    # Items outside the selected/discovered visible layer set are ignored, and
    # unmentioned visible layers append by deterministic layer-id order. Derived
    # groups such as DRILLS render after native layer groups.
    layer_render_order: Sequence[PcbLayerLike] | None = None
    layer_colors: dict[PcbLayerLike, str] = field(default_factory=dict)
    # If set, all PCB layers render in this single color for board + per-layer outputs.
    all_layers_color_override: str | None = None
    # PCB text is emitted as geometry for 3D/viz compatibility.
    # TrueType/barcode text may emit filled outlines when enabled.
    text_as_polygons: bool = True
    # Hard-fail on text geometry/rendering errors by default.
    # Set True only for exploratory/debug runs that explicitly allow degraded
    # geometry-box fallback behavior.
    allow_text_geometry_fallback: bool = False
    # Honor component NAMEON/COMMENTON visibility flags when rendering linked
    # designator/value text primitives.
    respect_component_text_visibility: bool = True
    show_board_outline: bool = True
    show_empty_layers: bool = False
    include_metadata: bool = True
    # Add the newer consumer-facing render-layer registry to the enrichment
    # payload. Default off so existing SVG metadata payloads remain stable.
    include_render_layer_metadata: bool = False
    stage: str = "viz"  # validation | viz | export
    group_mode: str = "layer"  # layer | net | component | object
    precision: int = 4
    board_outline_color: str = "#C0A000"
    # Board-profile voids (cutouts) render with their own stroke color.
    # If None, cutouts inherit board_outline_color.
    board_cutout_color: str | None = "#FF0000"
    # If set, one-layer SVG exports use this color for all geometry.
    layer_svg_color_override: str | None = None
    # Controls output display size. Geometry/viewBox remains in mm coordinates.
    svg_display_scale: float = 1.0
    # Optional unit suffix for width/height attrs (e.g. "mm", "px"). Empty keeps unitless attrs.
    svg_size_unit: str = ""
    # Include a root SVG viewBox in PCB millimeter coordinates.
    include_view_box: bool = True
    # Polygon definitions from Polygons6/Data are debug/reference geometry, not
    # rendered copper. Keep disabled by default to avoid non-copper overlays in
    # layer outputs (especially imported designs with many polygon definitions).
    include_polygon_definition_overlays: bool = False
    # Style for polygon-definition overlays (Polygons6).
    polygon_overlay_color: str = "#000000"
    # Clip copper-layer rendering to the board profile (supports castellated
    # half-hole style edge pads when enabled).
    clip_copper_to_board_outline: bool = False
    # Clip all rendered layers (including silkscreen/mechanical) to board outline.
    clip_all_layers_to_board_outline: bool = False
    # Clip copper geometry to remove drill-hole interiors.
    clip_holes_from_copper: bool = False
    # Mirror scene around X axis in SVG space (used for bottom-view exports).
    mirror_x: bool = False
    # Drill hole render mode:
    # - "knockout": legacy opaque white knockouts (default)
    # - "overlay": colored/transparent review overlay
    # - "none": no explicit drill-hole primitives
    drill_hole_mode: str = "knockout"
    # Legacy/fallback overlay color used when plating-specific colors are not set.
    drill_hole_overlay_color: str = "#FF0000"
    # Overlay drill colors by plating class.
    drill_hole_overlay_plated_color: str | None = None
    drill_hole_overlay_non_plated_color: str | None = None
    drill_hole_overlay_opacity: float = 0.25
    # When true in overlay mode, render drill holes as outline-only strokes
    # so underlying copper/tracks remain visible inside hole interiors.
    drill_hole_overlay_outline: bool = False
    # PcbLib footprint preview has no board rule context. Altium's PcbLib editor
    # resolves rule-driven solder/paste expansion as zero in that preview.
    footprint_rule_mask_expansion_zero: bool = False
    # Stroke width used for overlay-outline holes (mm).
    drill_hole_overlay_outline_width_mm: float = 0.10
    # Emit drill holes in a dedicated synthetic layer group (`layer-DRILLS`)
    # instead of interleaving them inside copper layer groups.
    drill_holes_as_layer_group: bool = True
    # Optional renderer-owned pad designator overlay. Default off so existing
    # PCB/PcbLib SVG output remains unchanged.
    show_pad_designators: bool = False
    pad_designator_font_family: str = "monospace"
    pad_designator_fill: str = "#111111"
    pad_designator_max_font_size_mm: float | None = None
    pad_designator_padding_ratio: float = 0.20
    # "top" emits composed-SVG labels after all rendered layers so mask/paste
    # layers do not obscure them; "layer" keeps labels inside the owning layer.
    pad_designator_overlay_z_order: str = "top"
    # Optional renderer-owned 0,0 origin/datum overlay. Default off so existing
    # PCB/PcbLib SVG output remains unchanged.
    show_origin_datum: bool = False
    origin_datum_color: str = "#00AEEF"
    origin_datum_stroke_width_mm: float = 0.08
    origin_datum_marker_size_mm: float = 1.2
    origin_datum_marker_style: str = "circle_cross"


@dataclass
class PcbSvgRenderContext:
    """
    Runtime context shared by renderer and record-level emitters.
    """

    options: PcbSvgRenderOptions
    min_x_mils: float
    min_y_mils: float
    max_x_mils: float
    max_y_mils: float
    project_parameters: dict[str, str] = field(default_factory=dict)
    net_count: int = 0
    component_count: int = 0
    net_name_by_index: dict[int, str] = field(default_factory=dict)
    net_uid_by_index: dict[int, str] = field(default_factory=dict)
    net_classes_by_name: dict[str, tuple[str, ...]] = field(default_factory=dict)
    component_designator_by_index: dict[int, str] = field(default_factory=dict)
    component_uid_by_index: dict[int, str] = field(default_factory=dict)
    component_data_by_index: dict[int, dict[str, object]] = field(default_factory=dict)
    layer_key_by_id: dict[int, str] = field(default_factory=dict)
    layer_name_by_id: dict[int, str] = field(default_factory=dict)
    layer_display_name_by_id: dict[int, str] = field(default_factory=dict)
    layer_token_by_id: dict[int, str] = field(default_factory=dict)
    layer_metadata_by_token: dict[str, _PcbSvgLayerMetadata] = field(
        default_factory=dict
    )
    all_layer_ids: tuple[int, ...] = field(default_factory=tuple)
    all_layer_tokens: tuple[str, ...] = field(default_factory=tuple)
    board_centroid_mils: tuple[float, float] | None = None
    board_centroid_relative_to_origin_mils: tuple[float, float] | None = None
    primitive_index_by_identity: dict[tuple[str, int], int] = field(
        default_factory=dict,
        repr=False,
    )
    _project_parameters_ci: dict[str, str] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self._project_parameters_ci = normalize_project_parameters(
            self.project_parameters
        )

    @property
    def width_mils(self) -> float:
        return max(self.max_x_mils - self.min_x_mils, 1.0)

    @property
    def height_mils(self) -> float:
        return max(self.max_y_mils - self.min_y_mils, 1.0)

    @property
    def width_mm(self) -> float:
        return self.width_mils * _MIL_TO_MM

    @property
    def height_mm(self) -> float:
        return self.height_mils * _MIL_TO_MM

    def x_to_svg(self, x_mils: float) -> float:
        return (x_mils - self.min_x_mils) * _MIL_TO_MM

    def y_to_svg(self, y_mils: float) -> float:
        # PCB coordinates are Y-up; SVG is Y-down.
        return (self.max_y_mils - y_mils) * _MIL_TO_MM

    def fmt(self, value: float) -> str:
        return f"{value:.{self.options.precision}f}".rstrip("0").rstrip(".")

    def layer_color(self, layer: PcbLayer) -> str:
        if self.options.all_layers_color_override is not None:
            return self.options.all_layers_color_override
        return self.options.layer_colors.get(layer, layer.default_color)

    def layer_ref_color(self, ref: PcbLayerRef) -> str:
        if self.options.all_layers_color_override is not None:
            return self.options.all_layers_color_override
        direct_color = self._layer_color_override_for_ref(ref)
        if direct_color is not None:
            return direct_color
        if ref.legacy_layer is not None:
            return ref.legacy_layer.default_color
        if ref.family == PcbLayerFamily.MECHANICAL:
            return PcbLayer.MECHANICAL_1.default_color
        if ref.family in {PcbLayerFamily.SIGNAL, PcbLayerFamily.INTERNAL_PLANE}:
            return PcbLayer.MID1.default_color
        return "#808080"

    def _layer_color_override_for_ref(self, ref: PcbLayerRef) -> str | None:
        for raw_layer, color in self.options.layer_colors.items():
            try:
                candidate = coerce_pcb_layer_ref(raw_layer)
            except PcbLayerResolutionError:
                continue
            if candidate == ref:
                return str(color)
        return None

    def substitute_special_strings(self, text: str) -> str:
        """
        Resolve PCB special strings (for example `.PCB_MIXDOWN`) from project context.

        Unresolved tokens are preserved literally.
        """
        return substitute_pcb_special_strings(text, self._project_parameters_ci)

    def _normalize_link_index(self, index: int | None, *, limit: int) -> int | None:
        """
        Normalize primitive linkage index (net/component) and reject sentinels.
        """
        if index is None:
            return None
        try:
            value = int(index)
        except (TypeError, ValueError):
            return None
        if value in _UNLINKED_INDEX_SENTINELS:
            return None
        if value < 0 or value >= limit:
            return None
        return value

    def layer_key(self, layer_id: int) -> str:
        """
        Resolve stable layer key for a legacy layer ID.
        """
        return self.layer_key_by_id.get(int(layer_id), f"L{int(layer_id)}")

    def layer_token(self, layer_id: int) -> str:
        """
        Resolve stable V7-aware layer token for a legacy layer ID.
        """
        layer_id_i = int(layer_id)
        return self.layer_token_by_id.get(layer_id_i, self.layer_name(layer_id_i))

    def layer_name(self, layer_id: int) -> str:
        """
        Resolve JSON-friendly layer name for a legacy layer ID.
        """
        return self.layer_name_by_id.get(int(layer_id), f"LAYER_{int(layer_id)}")

    def layer_display_name(self, layer_id: int) -> str:
        """
        Resolve friendly display name for a legacy layer ID.
        """
        return self.layer_display_name_by_id.get(
            int(layer_id), self.layer_name(layer_id)
        )

    def layer_role(self, layer_id: int) -> str:
        """
        Resolve tool-agnostic layer role classification.
        """
        if int(layer_id) == PCB_SVG_DRILLS_LAYER_ID:
            return "drill"
        try:
            layer = PcbLayer(int(layer_id))
        except ValueError:
            return "other"

        if layer.is_copper() or layer.is_internal_plane():
            return "copper"
        if layer.is_overlay():
            return "silkscreen"
        if layer.is_solder_mask():
            return "soldermask"
        if layer.is_paste_mask():
            return "paste"
        if layer.value in {PcbLayer.DRILL_GUIDE.value, PcbLayer.DRILL_DRAWING.value}:
            return "drill"
        if layer.is_mechanical():
            return "mechanical"
        return "other"

    def layer_metadata_attrs(self, layer_id: int) -> list[str]:
        """
        Build shared per-element layer metadata attributes.
        """
        layer_id_i = int(layer_id)
        token = self.layer_token(layer_id_i)
        metadata = self.layer_metadata_by_token.get(token)
        if metadata is not None:
            return self._layer_metadata_attrs_from_metadata(metadata)
        return [
            f'data-layer-id="{layer_id_i}"',
            f'data-layer-key="{html.escape(self.layer_key(layer_id_i))}"',
            f'data-layer-name="{html.escape(self.layer_name(layer_id_i))}"',
            f'data-layer-display-name="{html.escape(self.layer_display_name(layer_id_i))}"',
            f'data-layer-token="{html.escape(token)}"',
            'data-layer-family="unknown"',
            f'data-layer-role="{self.layer_role(layer_id_i)}"',
        ]

    def layer_metadata_attrs_for_ref(self, ref: PcbLayerRef) -> list[str]:
        """
        Build per-element metadata for a V7-aware layer reference.
        """
        metadata = self.layer_metadata_by_token.get(ref.token)
        if metadata is None:
            metadata = _metadata_for_layer_ref(ref)
        return self._layer_metadata_attrs_from_metadata(metadata)

    def layer_identifier_for_ref(self, ref: PcbLayerRef) -> int | None:
        """
        Return an integer identifier for deterministic element ids only.
        """
        if ref.legacy_layer_id is not None:
            return ref.legacy_layer_id
        if ref.v7_saved_layer_id is not None:
            return ref.v7_saved_layer_id
        return ref.runtime_v7_layer_id

    def _layer_metadata_attrs_from_metadata(
        self,
        metadata: _PcbSvgLayerMetadata,
    ) -> list[str]:
        attrs: list[str] = []
        if metadata.legacy_layer_id is not None:
            attrs.append(f'data-layer-id="{int(metadata.legacy_layer_id)}"')
        attrs.extend(
            [
                f'data-layer-key="{html.escape(metadata.layer_key)}"',
                f'data-layer-name="{html.escape(metadata.name)}"',
                f'data-layer-display-name="{html.escape(metadata.display_name)}"',
                f'data-layer-token="{html.escape(metadata.token)}"',
                f'data-layer-family="{html.escape(metadata.family)}"',
                f'data-layer-role="{html.escape(metadata.role)}"',
            ]
        )
        if metadata.v7_saved_layer_id is not None:
            attrs.append(f'data-layer-v7-saved-id="{int(metadata.v7_saved_layer_id)}"')
        if metadata.runtime_v7_layer_id is not None:
            attrs.append(
                f'data-layer-runtime-v7-id="{int(metadata.runtime_v7_layer_id)}"'
            )
        if metadata.kind_id is not None:
            attrs.append(f'data-layer-kind-id="{int(metadata.kind_id)}"')
        if metadata.custom_name:
            attrs.append(
                f'data-layer-custom-name="{html.escape(metadata.custom_name)}"'
            )
        if metadata.semantic_key:
            attrs.append(
                f'data-layer-semantic-key="{html.escape(metadata.semantic_key)}"'
            )
        return attrs

    def render_layer_metadata_entries(self) -> list[dict[str, object]]:
        """
        Build the additive consumer-facing render-layer registry.
        """
        entries: list[dict[str, object]] = []
        for token in self.all_layer_tokens:
            metadata = self.layer_metadata_by_token.get(token)
            if metadata is None:
                continue
            entry: dict[str, object] = {
                "key": metadata.layer_key,
                "name": metadata.name,
                "display_name": metadata.display_name,
                "token": metadata.token,
                "family": metadata.family,
                "role": metadata.role,
            }
            if metadata.legacy_layer_id is not None:
                entry["id"] = metadata.legacy_layer_id
                entry["legacy_layer_id"] = metadata.legacy_layer_id
            if metadata.v7_saved_layer_id is not None:
                entry["v7_saved_layer_id"] = metadata.v7_saved_layer_id
            if metadata.runtime_v7_layer_id is not None:
                entry["runtime_v7_layer_id"] = metadata.runtime_v7_layer_id
            if metadata.kind_id is not None:
                entry["kind_id"] = metadata.kind_id
            if metadata.custom_name:
                entry["custom_name"] = metadata.custom_name
            if metadata.semantic_key:
                entry["semantic_key"] = metadata.semantic_key
            if metadata.legacy_layer_id == PCB_SVG_DRILLS_LAYER_ID:
                entry.update(
                    {
                        "kind": "derived",
                        "source": "renderer",
                        "derived_from": ["pad-hole", "via-hole"],
                    }
                )
            else:
                entry.update({"kind": "native", "source": "altium"})
            entries.append(entry)
        return entries

    def relationship_metadata_attrs(
        self,
        *,
        net_index: int | None = None,
        component_index: int | None = None,
        include_net_classes: bool = True,
    ) -> list[str]:
        """
        Build normalized net/component relationship metadata attributes.
        """
        attrs: list[str] = []

        normalized_net = self._normalize_link_index(net_index, limit=self.net_count)
        if normalized_net is not None:
            attrs.append(f'data-net-index="{normalized_net}"')
            net_name = self.net_name_by_index.get(normalized_net)
            if net_name:
                attrs.append(f'data-net="{html.escape(net_name)}"')
                net_uid = self.net_uid_by_index.get(normalized_net, "")
                if net_uid:
                    attrs.append(f'data-net-uid="{html.escape(net_uid)}"')
                if include_net_classes:
                    classes = tuple(self.net_classes_by_name.get(net_name, ()))
                    if classes:
                        joined = ";".join(classes)
                        attrs.append(f'data-net-classes="{html.escape(joined)}"')
                        # Single-class convenience alias.
                        attrs.append(f'data-net-class="{html.escape(classes[0])}"')

        normalized_comp = self._normalize_link_index(
            component_index, limit=self.component_count
        )
        if normalized_comp is not None:
            attrs.append(f'data-component-index="{normalized_comp}"')
            designator = self.component_designator_by_index.get(normalized_comp)
            if designator:
                attrs.append(f'data-component="{html.escape(designator)}"')
            component_uid = self.component_uid_by_index.get(normalized_comp, "")
            if component_uid:
                attrs.append(f'data-component-uid="{html.escape(component_uid)}"')

        return attrs

    def primitive_svg_id(
        self,
        primitive_kind: str,
        primitive_obj: object,
        *,
        layer_id: int | None = None,
        role: str | None = None,
    ) -> str | None:
        """
        Return deterministic SVG element ID for a primitive emission.
        """
        index = self.primitive_index_by_identity.get(
            (primitive_kind, id(primitive_obj))
        )
        if index is None:
            return None

        parts = [f"pcb-{primitive_kind}-{index}"]
        if layer_id is not None:
            parts.append(f"L{int(layer_id)}")
        if role:
            safe_role = re.sub(r"[^A-Za-z0-9_-]+", "-", role).strip("-").lower()
            if safe_role:
                parts.append(safe_role)
        return "-".join(parts)

    def primitive_id_attr(
        self,
        primitive_kind: str,
        primitive_obj: object,
        *,
        layer_id: int | None = None,
        role: str | None = None,
    ) -> str | None:
        """
        Return deterministic identity attributes for a primitive emission.
        """
        svg_id = self.primitive_svg_id(
            primitive_kind,
            primitive_obj,
            layer_id=layer_id,
            role=role,
        )
        if not svg_id:
            return None
        escaped_svg_id = html.escape(svg_id)
        return f'id="{escaped_svg_id}" data-element-key="{escaped_svg_id}"'

    def enrichment_metadata_payload(
        self,
        *,
        view_kind: str,
        included_layer_ids: list[int],
        included_layer_tokens: list[str],
        includes_board_outline: bool,
        pcbdoc_filename: str | None,
    ) -> dict[str, object]:
        """
        Build document-level enrichment payload embedded in SVG metadata.
        """
        included_ids_sorted = sorted({int(layer_id) for layer_id in included_layer_ids})
        included_tokens_sorted = sorted(
            {str(token) for token in included_layer_tokens if str(token)}
        )

        layers_payload: dict[str, object] = {
            "all_layer_ids": [int(layer_id) for layer_id in self.all_layer_ids],
            "all_layer_tokens": [str(token) for token in self.all_layer_tokens],
            "layer_id_to_key": {
                str(layer_id): self.layer_key(layer_id)
                for layer_id in self.all_layer_ids
            },
            "layer_id_to_name": {
                str(layer_id): self.layer_name(layer_id)
                for layer_id in self.all_layer_ids
            },
            "layer_token_to_key": {
                token: metadata.layer_key
                for token, metadata in sorted(self.layer_metadata_by_token.items())
            },
            "layer_token_to_name": {
                token: metadata.name
                for token, metadata in sorted(self.layer_metadata_by_token.items())
            },
        }
        if self.options.include_render_layer_metadata:
            layers_payload["render_layers"] = self.render_layer_metadata_entries()

        payload: dict[str, object] = {
            "schema": SVG_ENRICHMENT_SCHEMA_ID,
            "source": {"pcbdoc_file": pcbdoc_filename or ""},
            "board": {
                "centroid_mils": (
                    [self.board_centroid_mils[0], self.board_centroid_mils[1]]
                    if self.board_centroid_mils is not None
                    else None
                ),
                "centroid_relative_to_origin_mils": (
                    [
                        self.board_centroid_relative_to_origin_mils[0],
                        self.board_centroid_relative_to_origin_mils[1],
                    ]
                    if self.board_centroid_relative_to_origin_mils is not None
                    else None
                ),
            },
            "view": {
                "kind": view_kind,
                "included_layer_ids": included_ids_sorted,
                "included_layer_tokens": included_tokens_sorted,
                "includes_board_outline": bool(includes_board_outline),
            },
            "layers": layers_payload,
            "lookup": {
                "net_index_to_name": {
                    str(idx): name
                    for idx, name in sorted(self.net_name_by_index.items())
                },
                "net_name_to_classes": {
                    name: list(classes)
                    for name, classes in sorted(self.net_classes_by_name.items())
                },
                "component_index_to_designator": {
                    str(idx): designator
                    for idx, designator in sorted(
                        self.component_designator_by_index.items()
                    )
                },
                "component_index_to_uid": {
                    str(idx): uid
                    for idx, uid in sorted(self.component_uid_by_index.items())
                },
            },
            "components": [
                self.component_data_by_index[idx]
                for idx in sorted(self.component_data_by_index.keys())
            ],
        }
        return payload


def _metadata_for_layer_ref(ref: PcbLayerRef) -> _PcbSvgLayerMetadata:
    return _PcbSvgLayerMetadata(
        token=ref.token,
        layer_key=_default_layer_key_for_ref(ref),
        name=ref.token,
        display_name=_default_display_name_for_ref(ref),
        role=_layer_role_for_ref(ref),
        family=str(ref.family.value),
        legacy_layer_id=ref.legacy_layer_id,
        v7_saved_layer_id=ref.v7_saved_layer_id,
        runtime_v7_layer_id=ref.runtime_v7_layer_id,
    )


def _default_layer_key_for_ref(ref: PcbLayerRef) -> str:
    if ref.legacy_layer_id is not None:
        return f"L{ref.legacy_layer_id}"
    if ref.v7_saved_layer_id is not None:
        return f"V7_{ref.v7_saved_layer_id}"
    if ref.runtime_v7_layer_id is not None:
        return f"TV7_{ref.runtime_v7_layer_id}"
    return ref.token


def _default_display_name_for_ref(ref: PcbLayerRef) -> str:
    if ref.legacy_layer is not None:
        return ref.legacy_layer.to_display_name()
    if ref.family != PcbLayerFamily.SIGNAL:
        return _non_signal_display_name_for_ref(ref)
    if ref.signal_kind is not None:
        signal_display_name = _SIGNAL_KIND_DISPLAY_NAMES.get(ref.signal_kind)
        if signal_display_name is not None:
            return signal_display_name
    if ref.number is not None:
        return f"Mid-Layer {ref.number}"
    return ref.token


def _non_signal_display_name_for_ref(ref: PcbLayerRef) -> str:
    if ref.family == PcbLayerFamily.INTERNAL_PLANE and ref.number is not None:
        return f"Internal Plane {ref.number}"
    if ref.family == PcbLayerFamily.MECHANICAL and ref.number is not None:
        return f"Mechanical {ref.number}"
    return ref.token


def _signal_layer_sort_index(ref: PcbLayerRef) -> int:
    if ref.signal_kind is not None:
        signal_sort_index = _SIGNAL_KIND_SORT_INDEXES.get(ref.signal_kind)
        if signal_sort_index is not None:
            return signal_sort_index
    if ref.number is not None:
        return 1 + ref.number
    return 0


def _native_layer_sort_index(ref: PcbLayerRef) -> int:
    return ref.v7_saved_layer_id or ref.runtime_v7_layer_id or 0


def _render_layer_sort_key(layer: _PcbSvgRenderLayer) -> tuple[int, int, str]:
    ref = layer.ref
    if ref.family == PcbLayerFamily.SIGNAL:
        return (0, _signal_layer_sort_index(ref), ref.token)
    if ref.legacy_layer is not None:
        return (1, int(ref.legacy_layer), ref.token)
    if ref.family == PcbLayerFamily.INTERNAL_PLANE and ref.number is not None:
        return (2, ref.number, ref.token)
    if ref.family == PcbLayerFamily.MECHANICAL and ref.number is not None:
        return (3, ref.number, ref.token)
    return (4, _native_layer_sort_index(ref), ref.token)


def _layer_role_for_ref(ref: PcbLayerRef) -> str:
    if ref.family in {PcbLayerFamily.SIGNAL, PcbLayerFamily.INTERNAL_PLANE}:
        return "copper"
    if ref.family == PcbLayerFamily.MECHANICAL:
        return "mechanical"
    if ref.legacy_layer is None:
        return "other"
    layer = ref.legacy_layer
    if layer.is_overlay():
        return "silkscreen"
    if layer.is_solder_mask():
        return "soldermask"
    if layer.is_paste_mask():
        return "paste"
    if layer.value in {PcbLayer.DRILL_GUIDE.value, PcbLayer.DRILL_DRAWING.value}:
        return "drill"
    return "other"


def _ref_for_resolved_layer(layer: object) -> PcbLayerRef | None:
    legacy_id = getattr(layer, "legacy_id", None)
    v7_id = getattr(layer, "v7_id", None)
    try:
        if legacy_id is not None:
            return PcbLayerRef.from_legacy(int(legacy_id))
        if v7_id is not None:
            return PcbLayerRef.from_v7_saved_layer_id(int(v7_id))
    except PcbLayerResolutionError:
        return None
    return None


@dataclass(frozen=True)
class _PcbSvgLayerCacheEntry:
    """
    Pre-rendered primitives for one layer under a fixed color context.
    """

    layer_color: str
    base_primitives: tuple[str, ...]
    drill_primitives: tuple[str, ...]
    overlay_primitives: tuple[str, ...]


@dataclass(frozen=True)
class _PcbSvgRenderCache:
    """
    Reusable render cache for one PCB/options/color context.
    """

    ctx: PcbSvgRenderContext
    layer_color_override: str | None
    layer_entries: dict[str, _PcbSvgLayerCacheEntry]
    board_clip_path_d: str
    hole_mask_elements: dict[int, tuple[str, ...]]


class PcbSvgRenderer:
    """
    PCB SVG renderer entry point.

    This class owns document/layer composition and context setup.
    Primitive and record rendering hooks are added incrementally.
    """

    def __init__(self, options: PcbSvgRenderOptions | None = None) -> None:
        self.options = options or PcbSvgRenderOptions()
        self._tt_renderer = None
        self._stroke_renderer = None
        self._barcode_renderer = None
        self._embedded_font_resolver_cache: dict[str, object | None] = {}

    @staticmethod
    def _resolved_layer_stack_safe(
        pcbdoc: "AltiumPcbDoc",
    ) -> "ResolvedLayerStack | None":
        """
        Best-effort resolved stack lookup for behavior-preserving consumers.
        """
        try:
            return resolved_layer_stack_from_pcbdoc(pcbdoc)
        except Exception:
            return None

    def _text_renderers(self) -> tuple[object | None, object | None, object | None]:
        """
        Get shared text renderers, creating them lazily.
        """
        if (
            self._tt_renderer is None
            or self._stroke_renderer is None
            or self._barcode_renderer is None
        ):
            try:
                from .altium_text_to_polygon import (
                    BarcodeRenderer,
                    StrokeTextRenderer,
                    TrueTypeTextRenderer,
                )

                if self._tt_renderer is None:
                    self._tt_renderer = TrueTypeTextRenderer()
                if self._stroke_renderer is None:
                    self._stroke_renderer = StrokeTextRenderer()
                if self._barcode_renderer is None:
                    self._barcode_renderer = BarcodeRenderer()
            except ImportError as exc:
                if self.options.allow_text_geometry_fallback:
                    log.warning(
                        "Text renderer modules unavailable; falling back to geometry boxes: %s",
                        exc,
                    )
                    return (None, None, None)
                raise RuntimeError(
                    "Text renderer modules unavailable and fallback is disabled. "
                    "Use the uv/rack environment with text dependencies installed."
                ) from exc
            except Exception as exc:
                if self.options.allow_text_geometry_fallback:
                    log.exception(
                        "Failed to initialize text renderers; falling back to geometry boxes"
                    )
                    return (None, None, None)
                raise RuntimeError(
                    "Failed to initialize PCB text renderers and fallback is disabled."
                ) from exc
        return (self._tt_renderer, self._stroke_renderer, self._barcode_renderer)

    @staticmethod
    def _normalize_font_alias(name: str) -> str:
        """
        Normalize font alias for resilient lookups.
        """
        return " ".join(name.replace("-", " ").replace("_", " ").lower().split())

    def _embedded_font_resolver(self, pcbdoc: "AltiumPcbDoc") -> object | None:
        """
        Build a font resolver backed by PCB embedded TTF fonts.
        """
        if pcbdoc.filepath:
            cache_key = str(pcbdoc.filepath.resolve())
        else:
            cache_key = f"inmemory:{id(pcbdoc)}"

        if cache_key in self._embedded_font_resolver_cache:
            return self._embedded_font_resolver_cache[cache_key]

        embedded_fonts = list(getattr(pcbdoc, "embedded_fonts", []) or [])
        if not embedded_fonts:
            self._embedded_font_resolver_cache[cache_key] = None
            return None

        cache_dir = Path(tempfile.gettempdir()) / "altium_embedded_fonts"
        cache_dir.mkdir(parents=True, exist_ok=True)

        alias_to_path: dict[str, str] = {}

        for embedded in embedded_fonts:
            try:
                ttf_data = embedded.decompress()
            except Exception:
                continue
            if not ttf_data:
                continue

            digest = hashlib.sha1(ttf_data).hexdigest()[:16]
            name = (embedded.name or "EmbeddedFont").strip()
            style = (embedded.style or "").strip()
            filename = (
                f"{safe_embedded_font_filename_component(name)}__"
                f"{safe_embedded_font_filename_component(style or 'regular')}__{digest}.ttf"
            )
            font_path = cache_dir / filename
            if not font_path.exists():
                font_path.write_bytes(ttf_data)

            aliases = {name}
            if style:
                aliases.add(f"{name} {style}".strip())
            base_name = re.sub(
                r"\b(bold|italic|oblique|regular)\b", "", name, flags=re.IGNORECASE
            ).strip()
            if base_name:
                aliases.add(base_name)
                if style:
                    aliases.add(f"{base_name} {style}".strip())

            for alias in aliases:
                alias_to_path[self._normalize_font_alias(alias)] = str(font_path)

        def _resolver(font_name: str, bold: bool, italic: bool) -> str | None:
            style_name = "Regular"
            if bold and italic:
                style_name = "Bold Italic"
            elif bold:
                style_name = "Bold"
            elif italic:
                style_name = "Italic"

            candidates = [
                f"{font_name} {style_name}",
                f"{font_name} {'Italic Bold' if style_name == 'Bold Italic' else ''}".strip(),
                font_name,
            ]
            for cand in candidates:
                key = self._normalize_font_alias(cand)
                if key in alias_to_path:
                    return alias_to_path[key]

            normalized_name = self._normalize_font_alias(font_name)
            # Last fallback: first embedded font whose alias starts with requested family.
            for alias, path in alias_to_path.items():
                if alias.startswith(normalized_name):
                    return path
            return None

        self._embedded_font_resolver_cache[cache_key] = _resolver
        return _resolver

    def render_board(
        self,
        pcbdoc: "AltiumPcbDoc",
        project_parameters: dict[str, str] | None = None,
    ) -> str:
        layers = self._collect_visible_layers(pcbdoc)
        return self._render_svg_document(
            pcbdoc, layers, project_parameters=project_parameters
        )

    def render_board_outline_only(
        self,
        pcbdoc: "AltiumPcbDoc",
        project_parameters: dict[str, str] | None = None,
    ) -> str:
        """
        Render only board-outline geometry in an SVG scene.
        """
        return self._render_svg_document(
            pcbdoc, [], project_parameters=project_parameters
        )

    def render_layers(
        self,
        pcbdoc: "AltiumPcbDoc",
        project_parameters: dict[str, str] | None = None,
    ) -> dict[str, str]:
        svgs: dict[str, str] = {}
        layer_color_override = self.options.layer_svg_color_override
        visible_layers = self._collect_visible_layers(pcbdoc)
        for layer in visible_layers:
            layer_name = layer.token
            svgs[layer_name] = self._render_svg_document(
                pcbdoc,
                [layer],
                layer_color_override=layer_color_override,
                project_parameters=project_parameters,
            )
        if self.options.drill_holes_as_layer_group and self._board_has_drill_holes(
            pcbdoc
        ):
            drill_source_layers = [
                layer.legacy_layer
                for layer in visible_layers
                if layer.legacy_layer is not None and layer.is_legacy_copper()
            ]
            if drill_source_layers:
                svgs[PCB_SVG_DRILLS_LAYER_NAME] = self._render_svg_document(
                    pcbdoc,
                    [],
                    layer_color_override=layer_color_override,
                    project_parameters=project_parameters,
                    drill_source_layers=drill_source_layers,
                )
        return svgs

    def render_board_and_layers(
        self,
        pcbdoc: "AltiumPcbDoc",
        project_parameters: dict[str, str] | None = None,
        *,
        include_board: bool = True,
        include_board_outline_in_layers: bool = True,
    ) -> tuple[str | None, dict[str, str]]:
        """
        Render composed board SVG plus per-layer SVGs with shared primitive cache.

        This avoids rescanning all primitives for board and layer passes separately.
        """
        layers = self._collect_visible_layers(pcbdoc)
        board_cache = self._build_render_cache(
            pcbdoc,
            layers,
            layer_color_override=None,
            project_parameters=project_parameters,
        )
        board_svg: str | None = None
        if include_board:
            board_svg = self._render_svg_document(
                pcbdoc,
                layers,
                project_parameters=project_parameters,
                render_cache=board_cache,
                include_board_outline=True,
            )

        layer_color_override = self.options.layer_svg_color_override
        layer_cache = board_cache
        if not self._cache_matches_layer_color_override(
            board_cache,
            layers,
            layer_color_override,
        ):
            layer_cache = self._build_render_cache(
                pcbdoc,
                layers,
                layer_color_override=layer_color_override,
                project_parameters=project_parameters,
            )

        layer_svgs: dict[str, str] = {}
        for layer in layers:
            layer_name = layer.token
            layer_svgs[layer_name] = self._render_svg_document(
                pcbdoc,
                [layer],
                layer_color_override=layer_color_override,
                project_parameters=project_parameters,
                render_cache=layer_cache,
                include_board_outline=include_board_outline_in_layers,
            )
        if self.options.drill_holes_as_layer_group and self._board_has_drill_holes(
            pcbdoc
        ):
            drill_source_layers = [
                layer.legacy_layer
                for layer in layers
                if layer.legacy_layer is not None and layer.is_legacy_copper()
            ]
            if drill_source_layers:
                layer_svgs[PCB_SVG_DRILLS_LAYER_NAME] = self._render_svg_document(
                    pcbdoc,
                    [],
                    layer_color_override=layer_color_override,
                    project_parameters=project_parameters,
                    render_cache=layer_cache,
                    include_board_outline=include_board_outline_in_layers,
                    drill_source_layers=drill_source_layers,
                )
        return board_svg, layer_svgs

    def _render_svg_document(
        self,
        pcbdoc: "AltiumPcbDoc",
        layers: list[_PcbSvgRenderLayer],
        layer_color_override: str | None = None,
        project_parameters: dict[str, str] | None = None,
        render_cache: _PcbSvgRenderCache | None = None,
        include_board_outline: bool = True,
        drill_source_layers: list[PcbLayer] | None = None,
    ) -> str:
        ctx = self._resolve_render_context(
            pcbdoc,
            render_cache,
            project_parameters=project_parameters,
        )
        drill_group_primitives = self._collect_drill_group_primitives(
            ctx,
            pcbdoc,
            layers,
            render_cache,
            drill_source_layers=drill_source_layers,
        )
        board_clip_id, board_clip_path_d = self._resolve_board_clip_definition(
            ctx,
            pcbdoc,
            layers,
            render_cache,
        )
        layer_hole_masks = self._collect_layer_hole_masks(
            ctx,
            pcbdoc,
            layers,
            render_cache,
        )
        extra_defs, extra_scene = self._render_overlay_defs_scene(
            ctx,
            pcbdoc,
        )

        outline_group: list[str] = []
        if include_board_outline and self.options.show_board_outline:
            outline_group = self._render_board_outline(
                ctx,
                pcbdoc.board.outline if pcbdoc.board else None,
                stroke_color=layer_color_override or self.options.board_outline_color,
            )

        rendered_layer_lines = self._render_requested_layers(
            ctx,
            pcbdoc,
            layers,
            layer_color_override,
            render_cache,
            board_clip_id=board_clip_id,
            layer_hole_masks=layer_hole_masks,
        )
        active_layer_ids, active_layer_tokens = (
            self._rendered_layer_identity_from_layer_lines(
                layers,
                rendered_layer_lines,
            )
        )
        if drill_group_primitives:
            active_layer_ids.append(PCB_SVG_DRILLS_LAYER_ID)
            active_layer_tokens.append(PCB_SVG_DRILLS_LAYER_NAME)
        active_layer_ids, active_layer_tokens, view_kind = self._resolve_render_view(
            ctx,
            active_layer_ids,
            active_layer_tokens,
        )
        svg_attrs = self._build_svg_document_attrs(ctx, pcbdoc, view_kind)

        lines = [f"<svg {' '.join(svg_attrs)}>"]
        self._append_svg_metadata(
            lines,
            ctx,
            view_kind,
            active_layer_ids,
            active_layer_tokens,
            includes_board_outline=bool(outline_group),
            pcbdoc=pcbdoc,
        )
        self._append_svg_defs(
            lines,
            ctx,
            board_clip_id,
            board_clip_path_d,
            layer_hole_masks,
            extra_defs,
        )
        lines.append(f"  <g {' '.join(self._build_scene_attrs(ctx))}>")

        if outline_group:
            lines.extend(outline_group)

        lines.extend(rendered_layer_lines)

        if drill_group_primitives:
            drill_layer_clip = (
                board_clip_id
                if board_clip_id
                and (
                    self.options.clip_all_layers_to_board_outline
                    or self.options.clip_copper_to_board_outline
                )
                else None
            )
            lines.extend(
                self._render_drill_layer_group(
                    ctx,
                    drill_group_primitives,
                    clip_path_id=drill_layer_clip,
                )
            )

        if extra_scene:
            lines.extend(extra_scene)

        lines.extend(
            self._render_top_pad_designator_overlay_group(
                ctx,
                pcbdoc,
                layers,
                render_cache,
                board_clip_id=board_clip_id,
            )
        )
        lines.extend(self._render_top_origin_datum_overlay_group(ctx))

        lines.append("  </g>")
        lines.append("</svg>")
        return "\n".join(lines)

    def _resolve_render_context(
        self,
        pcbdoc: "AltiumPcbDoc",
        render_cache: _PcbSvgRenderCache | None,
        *,
        project_parameters: dict[str, str] | None,
    ) -> PcbSvgRenderContext:
        if render_cache is not None:
            return render_cache.ctx
        return self._build_context(
            pcbdoc,
            project_parameters=project_parameters,
        )

    def _collect_drill_group_primitives(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layers: list[_PcbSvgRenderLayer],
        render_cache: _PcbSvgRenderCache | None,
        *,
        drill_source_layers: list[PcbLayer] | None,
    ) -> list[str]:
        if not self.options.drill_holes_as_layer_group:
            return []
        if (self.options.drill_hole_mode or "").strip().lower() == "none":
            return []

        drill_layers = drill_source_layers
        if drill_layers is None:
            drill_layers = [
                layer.legacy_layer
                for layer in layers
                if layer.legacy_layer is not None and layer.is_legacy_copper()
            ]
        drill_group_primitives: list[str] = []
        for drill_layer in drill_layers:
            if not drill_layer.is_copper():
                continue
            drill_group_primitives.extend(
                self._drill_primitives_for_layer(
                    ctx,
                    pcbdoc,
                    drill_layer,
                    render_cache,
                )
            )
        return drill_group_primitives

    def _drill_primitives_for_layer(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layer: PcbLayer,
        render_cache: _PcbSvgRenderCache | None,
    ) -> list[str]:
        cache_key = PcbLayerRef.from_legacy(layer).token
        cache_entry = (
            render_cache.layer_entries.get(cache_key)
            if render_cache is not None
            else None
        )
        if cache_entry is not None:
            return list(cache_entry.drill_primitives)
        return self._render_drill_holes_for_layer(
            ctx,
            pcbdoc,
            layer,
        )

    @staticmethod
    def _rendered_layer_identity_from_layer_lines(
        layers: list[_PcbSvgRenderLayer],
        layer_lines: list[str],
    ) -> tuple[list[int], list[str]]:
        rendered_text = "\n".join(layer_lines)
        rendered_ids: list[int] = []
        rendered_tokens: list[str] = []
        for layer in layers:
            if f'id="layer-{layer.token}"' not in rendered_text:
                continue
            rendered_tokens.append(layer.token)
            if layer.legacy_layer_id is not None:
                rendered_ids.append(layer.legacy_layer_id)
        return rendered_ids, rendered_tokens

    def _resolve_render_view(
        self,
        ctx: PcbSvgRenderContext,
        active_layer_ids: list[int],
        active_layer_tokens: list[str],
    ) -> tuple[list[int], list[str], str]:
        active_layer_ids = sorted({int(layer_id) for layer_id in active_layer_ids})
        active_layer_tokens = sorted(
            {str(token) for token in active_layer_tokens if str(token)}
        )

        if not active_layer_tokens and not active_layer_ids:
            return active_layer_ids, active_layer_tokens, "board_outline_only"
        if set(active_layer_tokens) == set(ctx.all_layer_tokens):
            return active_layer_ids, active_layer_tokens, "board"
        return active_layer_ids, active_layer_tokens, "layer_set"

    def _build_svg_document_attrs(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        view_kind: str,
    ) -> list[str]:
        display_scale = (
            self.options.svg_display_scale
            if self.options.svg_display_scale > 0
            else 1.0
        )
        size_unit = self.options.svg_size_unit.strip()
        width_display = ctx.width_mm * display_scale
        height_display = ctx.height_mm * display_scale
        width_attr = (
            f"{ctx.fmt(width_display)}{size_unit}"
            if size_unit
            else ctx.fmt(width_display)
        )
        height_attr = (
            f"{ctx.fmt(height_display)}{size_unit}"
            if size_unit
            else ctx.fmt(height_display)
        )
        svg_attrs = [
            'xmlns="http://www.w3.org/2000/svg"',
            'version="1.1"',
            f'width="{width_attr}"',
            f'height="{height_attr}"',
        ]
        if self.options.include_view_box:
            svg_attrs.append(
                f'viewBox="0 0 {ctx.fmt(ctx.width_mm)} {ctx.fmt(ctx.height_mm)}"'
            )
        if not self.options.include_metadata:
            return svg_attrs

        svg_attrs.extend(
            [
                f'data-stage="{html.escape(self.options.stage)}"',
                f'data-group-mode="{html.escape(self.options.group_mode)}"',
                f'data-enrichment-schema="{SVG_ENRICHMENT_SCHEMA_ID}"',
                f'data-view-kind="{view_kind}"',
                f'data-mirror-x="{str(bool(self.options.mirror_x)).lower()}"',
            ]
        )
        if pcbdoc.filepath:
            svg_attrs.append(f'data-source="{html.escape(pcbdoc.filepath.name)}"')
        if ctx.board_centroid_mils is not None:
            svg_attrs.append(
                f'data-board-centroid-x-mils="{ctx.fmt(ctx.board_centroid_mils[0])}"'
            )
            svg_attrs.append(
                f'data-board-centroid-y-mils="{ctx.fmt(ctx.board_centroid_mils[1])}"'
            )
        if ctx.board_centroid_relative_to_origin_mils is not None:
            svg_attrs.append(
                f'data-board-centroid-origin-x-mils="{ctx.fmt(ctx.board_centroid_relative_to_origin_mils[0])}"'
            )
            svg_attrs.append(
                f'data-board-centroid-origin-y-mils="{ctx.fmt(ctx.board_centroid_relative_to_origin_mils[1])}"'
            )
        return svg_attrs

    def _resolve_board_clip_definition(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layers: list[_PcbSvgRenderLayer],
        render_cache: _PcbSvgRenderCache | None,
    ) -> tuple[str, str]:
        if not (
            self.options.clip_copper_to_board_outline
            or self.options.clip_all_layers_to_board_outline
        ):
            return "", ""
        if not layers:
            return "", ""

        outline = pcbdoc.board.outline if pcbdoc.board else None
        board_clip_path_d = (
            render_cache.board_clip_path_d
            if render_cache is not None
            else self._board_outline_clip_path(ctx, outline)
        )
        if not board_clip_path_d:
            return "", ""
        return "clip-board-outline", board_clip_path_d

    def _collect_layer_hole_masks(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layers: list[_PcbSvgRenderLayer],
        render_cache: _PcbSvgRenderCache | None,
    ) -> dict[int, tuple[str, list[str]]]:
        if not self.options.clip_holes_from_copper or not layers:
            return {}

        layer_hole_masks: dict[int, tuple[str, list[str]]] = {}
        for layer in layers:
            legacy_layer = layer.legacy_layer
            if legacy_layer is None or not legacy_layer.is_copper():
                continue
            hole_elements = self._layer_hole_mask_elements(
                ctx,
                pcbdoc,
                legacy_layer,
                render_cache,
            )
            if not hole_elements:
                continue
            layer_hole_masks[legacy_layer.value] = (
                f"mask-drills-{legacy_layer.value}",
                hole_elements,
            )
        return layer_hole_masks

    def _layer_hole_mask_elements(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layer: PcbLayer,
        render_cache: _PcbSvgRenderCache | None,
    ) -> list[str]:
        if render_cache is not None and layer.value in render_cache.hole_mask_elements:
            return list(render_cache.hole_mask_elements[layer.value])
        return self._collect_drill_hole_elements(
            ctx,
            pcbdoc,
            layer,
            plated_hole_color="#000000",
            non_plated_hole_color="#000000",
            hole_opacity=1.0,
            hole_outline=False,
            hole_outline_width_mm=0.10,
            include_metadata=False,
        )

    def _append_svg_metadata(
        self,
        lines: list[str],
        ctx: PcbSvgRenderContext,
        view_kind: str,
        active_layer_ids: list[int],
        active_layer_tokens: list[str],
        *,
        includes_board_outline: bool,
        pcbdoc: "AltiumPcbDoc",
    ) -> None:
        if not self.options.include_metadata:
            return

        enrichment_payload = ctx.enrichment_metadata_payload(
            view_kind=view_kind,
            included_layer_ids=active_layer_ids,
            included_layer_tokens=active_layer_tokens,
            includes_board_outline=includes_board_outline,
            pcbdoc_filename=pcbdoc.filepath.name if pcbdoc.filepath else None,
        )
        payload_json = json.dumps(
            enrichment_payload,
            sort_keys=True,
            separators=(",", ":"),
        )
        lines.append(
            f'  <metadata id="{SVG_ENRICHMENT_METADATA_ID}" '
            f'data-schema="{SVG_ENRICHMENT_SCHEMA_ID}">'
        )
        lines.append(f"    {html.escape(payload_json, quote=False)}")
        lines.append("  </metadata>")

    def _append_svg_defs(
        self,
        lines: list[str],
        ctx: PcbSvgRenderContext,
        board_clip_id: str,
        board_clip_path_d: str,
        layer_hole_masks: dict[int, tuple[str, list[str]]],
        extra_defs: list[str],
    ) -> None:
        if not (board_clip_path_d or layer_hole_masks or extra_defs):
            return

        lines.append("  <defs>")
        if board_clip_path_d:
            lines.append(f'    <clipPath id="{board_clip_id}">')
            lines.append(
                "      "
                + (
                    f'<path d="{board_clip_path_d}" fill-rule="evenodd" '
                    'clip-rule="evenodd"/>'
                )
            )
            lines.append("    </clipPath>")

        if layer_hole_masks:
            view_w = ctx.fmt(ctx.width_mm)
            view_h = ctx.fmt(ctx.height_mm)
            for mask_id, hole_elements in layer_hole_masks.values():
                lines.append(
                    f'    <mask id="{mask_id}" maskUnits="userSpaceOnUse" '
                    f'x="0" y="0" width="{view_w}" height="{view_h}">'
                )
                lines.append(
                    f'      <rect x="0" y="0" width="{view_w}" height="{view_h}" fill="#FFFFFF"/>'
                )
                for elem in hole_elements:
                    lines.append(f"      {elem}")
                lines.append("    </mask>")
        lines.extend(extra_defs)
        lines.append("  </defs>")

    def _build_scene_attrs(self, ctx: PcbSvgRenderContext) -> list[str]:
        scene_attrs = ['id="scene"']
        if self.options.mirror_x:
            scene_attrs.append(
                f'transform="translate({ctx.fmt(ctx.width_mm)} 0) scale(-1 1)"'
            )
        return scene_attrs

    def _render_requested_layers(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layers: list[_PcbSvgRenderLayer],
        layer_color_override: str | None,
        render_cache: _PcbSvgRenderCache | None,
        *,
        board_clip_id: str,
        layer_hole_masks: dict[int, tuple[str, list[str]]],
    ) -> list[str]:
        lines: list[str] = []
        for layer in layers:
            lines.extend(
                self._render_single_requested_layer(
                    ctx,
                    pcbdoc,
                    layer,
                    layer_color_override,
                    render_cache,
                    board_clip_id=board_clip_id,
                    layer_hole_masks=layer_hole_masks,
                )
            )
        return lines

    def _render_single_requested_layer(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layer: _PcbSvgRenderLayer,
        layer_color_override: str | None,
        render_cache: _PcbSvgRenderCache | None,
        *,
        board_clip_id: str,
        layer_hole_masks: dict[int, tuple[str, list[str]]],
    ) -> list[str]:
        layer_board_clip = (
            board_clip_id
            if board_clip_id
            and (self.options.clip_all_layers_to_board_outline or layer.is_copper())
            else None
        )
        layer_hole_mask_id = (
            layer_hole_masks.get(layer.legacy_layer_id, (None, []))[0]
            if layer.legacy_layer_id is not None
            else None
        )
        cache_entry = (
            render_cache.layer_entries.get(layer.cache_key)
            if render_cache is not None
            else None
        )
        if cache_entry is not None:
            return self._render_cached_layer_group(
                ctx,
                layer,
                cache_entry,
                clip_path_id=layer_board_clip,
                mask_id=layer_hole_mask_id,
            )
        return self._render_live_layer_group(
            ctx,
            pcbdoc,
            layer,
            layer_color_override,
            clip_path_id=layer_board_clip,
            mask_id=layer_hole_mask_id,
        )

    def _render_cached_layer_group(
        self,
        ctx: PcbSvgRenderContext,
        layer: _PcbSvgRenderLayer,
        cache_entry: _PcbSvgLayerCacheEntry,
        *,
        clip_path_id: str | None,
        mask_id: str | None,
    ) -> list[str]:
        layer_drill_primitives = list(cache_entry.drill_primitives)
        return self._render_layer_group_from_primitives(
            ctx,
            layer,
            cache_entry.layer_color,
            cache_entry.base_primitives,
            [] if self.options.drill_holes_as_layer_group else layer_drill_primitives,
            (
                []
                if self._pad_designator_overlays_render_top()
                else cache_entry.overlay_primitives
            ),
            clip_path_id=clip_path_id,
            mask_id=mask_id,
        )

    def _render_live_layer_group(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layer: _PcbSvgRenderLayer,
        layer_color_override: str | None,
        *,
        clip_path_id: str | None,
        mask_id: str | None,
    ) -> list[str]:
        if self.options.drill_holes_as_layer_group:
            layer_color, base_primitives, _, overlay_primitives = (
                self._collect_render_layer_primitives(
                    ctx,
                    pcbdoc,
                    layer,
                    layer_color_override,
                )
            )
            return self._render_layer_group_from_primitives(
                ctx,
                layer,
                layer_color,
                base_primitives,
                [],
                []
                if self._pad_designator_overlays_render_top()
                else overlay_primitives,
                clip_path_id=clip_path_id,
                mask_id=mask_id,
            )
        return self._render_layer_group(
            ctx,
            pcbdoc,
            layer,
            layer_color_override,
            clip_path_id=clip_path_id,
            mask_id=mask_id,
        )

    def _render_top_pad_designator_overlay_group(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layers: list[_PcbSvgRenderLayer],
        render_cache: _PcbSvgRenderCache | None,
        *,
        board_clip_id: str | None,
    ) -> list[str]:
        if not self._pad_designator_overlays_render_top():
            return []

        overlay_primitives: list[str] = []
        for layer in layers:
            legacy_layer = layer.legacy_layer
            if legacy_layer is None or not legacy_layer.is_copper():
                continue
            cache_entry = (
                render_cache.layer_entries.get(layer.cache_key)
                if render_cache is not None
                else None
            )
            if cache_entry is not None:
                overlay_primitives.extend(cache_entry.overlay_primitives)
            else:
                overlay_primitives.extend(
                    self._render_pad_designator_overlay_for_layer(
                        ctx,
                        pcbdoc,
                        legacy_layer,
                    )
                )
        if not overlay_primitives:
            return []

        attrs = [
            'id="pcb-pad-designator-overlays"',
            'class="pcb-pad-designator-overlays"',
        ]
        if board_clip_id:
            attrs.append(f'clip-path="url(#{html.escape(board_clip_id)})"')
        if self.options.include_metadata:
            attrs.extend(
                [
                    'data-overlay-container="pad-designator"',
                    'data-overlay-z-order="top"',
                ]
            )

        lines = [f"    <g {' '.join(attrs)}>"]
        for primitive in overlay_primitives:
            lines.append(f"      {primitive}")
        lines.append("    </g>")
        return lines

    def _pad_designator_overlays_render_top(self) -> bool:
        if not self.options.show_pad_designators:
            return False
        return str(self.options.pad_designator_overlay_z_order).strip().lower() in {
            "top",
            "above_layers",
            "above-layers",
            "front",
        }

    def _render_top_origin_datum_overlay_group(
        self,
        ctx: PcbSvgRenderContext,
    ) -> list[str]:
        if not self.options.show_origin_datum:
            return []

        cx = ctx.x_to_svg(0.0)
        cy = ctx.y_to_svg(0.0)
        marker_size = max(float(self.options.origin_datum_marker_size_mm), 0.05)
        radius = marker_size * 0.5
        cross_radius = radius * 1.35
        stroke_width = max(float(self.options.origin_datum_stroke_width_mm), 0.0)
        color = html.escape(str(self.options.origin_datum_color))
        marker_style = self._normalized_origin_datum_marker_style(
            self.options.origin_datum_marker_style
        )
        show_circle = marker_style in {"circle", "circle_cross"}
        show_cross = marker_style in {"cross", "circle_cross"}

        attrs = [
            'id="pcb-origin-datum-overlay"',
            'class="pcb-origin-datum-overlay"',
            'pointer-events="none"',
        ]
        if self.options.include_metadata:
            attrs.extend(
                [
                    'data-overlay-kind="origin-datum"',
                    'data-overlay-role="reference-datum"',
                    'data-overlay-z-order="top"',
                    'data-origin-x-mils="0"',
                    'data-origin-y-mils="0"',
                    f'data-origin-svg-x-mm="{ctx.fmt(cx)}"',
                    f'data-origin-svg-y-mm="{ctx.fmt(cy)}"',
                    f'data-marker-style="{marker_style}"',
                    f'data-marker-size-mm="{ctx.fmt(marker_size)}"',
                ]
            )

        common_attrs = [
            'fill="none"',
            f'stroke="{color}"',
            f'stroke-width="{ctx.fmt(stroke_width)}"',
            'stroke-linecap="round"',
            'vector-effect="non-scaling-stroke"',
        ]
        circle_attrs = [
            'class="pcb-origin-datum-circle"',
            f'cx="{ctx.fmt(cx)}"',
            f'cy="{ctx.fmt(cy)}"',
            f'r="{ctx.fmt(radius)}"',
            *common_attrs,
        ]
        horizontal_attrs = [
            'class="pcb-origin-datum-cross pcb-origin-datum-cross-horizontal"',
            f'x1="{ctx.fmt(cx - cross_radius)}"',
            f'y1="{ctx.fmt(cy)}"',
            f'x2="{ctx.fmt(cx + cross_radius)}"',
            f'y2="{ctx.fmt(cy)}"',
            *common_attrs,
        ]
        vertical_attrs = [
            'class="pcb-origin-datum-cross pcb-origin-datum-cross-vertical"',
            f'x1="{ctx.fmt(cx)}"',
            f'y1="{ctx.fmt(cy - cross_radius)}"',
            f'x2="{ctx.fmt(cx)}"',
            f'y2="{ctx.fmt(cy + cross_radius)}"',
            *common_attrs,
        ]
        if not show_circle:
            circle_attrs.append('style="display:none"')
        if not show_cross:
            horizontal_attrs.append('style="display:none"')
            vertical_attrs.append('style="display:none"')
        if self.options.include_metadata:
            circle_attrs.extend(
                [
                    'data-primitive="origin-datum-circle"',
                    'data-overlay-kind="origin-datum"',
                    'data-marker-part="circle"',
                ]
            )
            horizontal_attrs.extend(
                [
                    'data-primitive="origin-datum-cross"',
                    'data-overlay-kind="origin-datum"',
                    'data-marker-part="cross-horizontal"',
                ]
            )
            vertical_attrs.extend(
                [
                    'data-primitive="origin-datum-cross"',
                    'data-overlay-kind="origin-datum"',
                    'data-marker-part="cross-vertical"',
                ]
            )

        return [
            f"    <g {' '.join(attrs)}>",
            f"      <circle {' '.join(circle_attrs)}/>",
            f"      <line {' '.join(horizontal_attrs)}/>",
            f"      <line {' '.join(vertical_attrs)}/>",
            "    </g>",
        ]

    @staticmethod
    def _normalized_origin_datum_marker_style(marker_style: str) -> str:
        normalized = str(marker_style or "").strip().lower().replace("-", "_")
        if normalized in {"circle", "cross", "circle_cross"}:
            return normalized
        return "circle_cross"

    def _render_drill_layer_group(
        self,
        ctx: PcbSvgRenderContext,
        drill_primitives: list[str] | tuple[str, ...],
        *,
        clip_path_id: str | None = None,
    ) -> list[str]:
        if not drill_primitives:
            return []

        attrs = [f'id="layer-{PCB_SVG_DRILLS_LAYER_NAME}"']
        if clip_path_id:
            attrs.append(f'clip-path="url(#{html.escape(clip_path_id)})"')
        if self.options.include_metadata:
            attrs.extend(ctx.layer_metadata_attrs(PCB_SVG_DRILLS_LAYER_ID))
            attrs.extend(
                [
                    'data-layer-origin="synthetic-drill-aggregate"',
                    f'data-primitive-count="{len(drill_primitives)}"',
                ]
            )

        lines = [f"    <g {' '.join(attrs)}>"]
        for primitive in drill_primitives:
            lines.append(f"      {primitive}")
        lines.append("    </g>")
        return lines

    def _build_render_cache(
        self,
        pcbdoc: "AltiumPcbDoc",
        layers: list[_PcbSvgRenderLayer],
        *,
        layer_color_override: str | None,
        project_parameters: dict[str, str] | None = None,
    ) -> _PcbSvgRenderCache:
        ctx = self._build_context(pcbdoc, project_parameters=project_parameters)
        outline = pcbdoc.board.outline if pcbdoc.board else None
        board_clip_path_d = ""
        if (
            self.options.clip_copper_to_board_outline
            or self.options.clip_all_layers_to_board_outline
        ) and layers:
            board_clip_path_d = self._board_outline_clip_path(ctx, outline)

        layer_entries: dict[str, _PcbSvgLayerCacheEntry] = {}
        hole_mask_elements: dict[int, tuple[str, ...]] = {}
        for layer in layers:
            layer_color, base_primitives, drill_primitives, overlay_primitives = (
                self._collect_render_layer_primitives(
                    ctx,
                    pcbdoc,
                    layer,
                    layer_color_override,
                )
            )
            layer_entries[layer.cache_key] = _PcbSvgLayerCacheEntry(
                layer_color=layer_color,
                base_primitives=tuple(base_primitives),
                drill_primitives=tuple(drill_primitives),
                overlay_primitives=tuple(overlay_primitives),
            )

            legacy_layer = layer.legacy_layer
            if (
                self.options.clip_holes_from_copper
                and legacy_layer is not None
                and legacy_layer.is_copper()
            ):
                hole_elements = self._collect_drill_hole_elements(
                    ctx,
                    pcbdoc,
                    legacy_layer,
                    plated_hole_color="#000000",
                    non_plated_hole_color="#000000",
                    hole_opacity=1.0,
                    hole_outline=False,
                    hole_outline_width_mm=0.10,
                    include_metadata=False,
                )
                if hole_elements:
                    hole_mask_elements[legacy_layer.value] = tuple(hole_elements)

        return _PcbSvgRenderCache(
            ctx=ctx,
            layer_color_override=layer_color_override,
            layer_entries=layer_entries,
            board_clip_path_d=board_clip_path_d,
            hole_mask_elements=hole_mask_elements,
        )

    def _cache_matches_layer_color_override(
        self,
        cache: _PcbSvgRenderCache,
        layers: list[_PcbSvgRenderLayer],
        layer_color_override: str | None,
    ) -> bool:
        if cache.layer_color_override == layer_color_override:
            return True

        for layer in layers:
            expected_color = (
                layer_color_override
                if layer_color_override is not None
                else cache.ctx.layer_ref_color(layer.ref)
            )
            entry = cache.layer_entries.get(layer.cache_key)
            if entry is None or entry.layer_color != expected_color:
                return False
        return True

    def _build_context(
        self,
        pcbdoc: "AltiumPcbDoc",
        project_parameters: dict[str, str] | None = None,
    ) -> PcbSvgRenderContext:
        min_x, min_y, max_x, max_y = self._compute_bounds_mils(pcbdoc)
        resolved = self._resolved_layer_stack_safe(pcbdoc)
        net_name_by_index, net_uid_by_index = self._build_net_metadata(pcbdoc)
        net_classes_by_name = self._build_net_classes_by_name(pcbdoc)
        (
            component_designator_by_index,
            component_uid_by_index,
            component_data_by_index,
        ) = self._build_component_metadata(pcbdoc)
        (
            all_layer_ids,
            all_layer_tokens,
            layer_key_by_id,
            layer_name_by_id,
            layer_display_name_by_id,
            layer_token_by_id,
            layer_metadata_by_token,
        ) = self._build_layer_metadata(pcbdoc, resolved)
        primitive_index_by_identity = self._build_primitive_index_by_identity(pcbdoc)
        board_centroid_mils, board_centroid_relative = (
            self._compute_board_centroids_safe(pcbdoc)
        )

        return PcbSvgRenderContext(
            options=self.options,
            min_x_mils=min_x,
            min_y_mils=min_y,
            max_x_mils=max_x,
            max_y_mils=max_y,
            project_parameters=project_parameters or {},
            net_count=len(getattr(pcbdoc, "nets", []) or []),
            component_count=len(getattr(pcbdoc, "components", []) or []),
            net_name_by_index=net_name_by_index,
            net_uid_by_index=net_uid_by_index,
            net_classes_by_name=net_classes_by_name,
            component_designator_by_index=component_designator_by_index,
            component_uid_by_index=component_uid_by_index,
            component_data_by_index=component_data_by_index,
            layer_key_by_id=layer_key_by_id,
            layer_name_by_id=layer_name_by_id,
            layer_display_name_by_id=layer_display_name_by_id,
            layer_token_by_id=layer_token_by_id,
            layer_metadata_by_token=layer_metadata_by_token,
            all_layer_ids=all_layer_ids,
            all_layer_tokens=all_layer_tokens,
            board_centroid_mils=board_centroid_mils,
            board_centroid_relative_to_origin_mils=board_centroid_relative,
            primitive_index_by_identity=primitive_index_by_identity,
        )

    def _build_net_metadata(
        self,
        pcbdoc: "AltiumPcbDoc",
    ) -> tuple[dict[int, str], dict[int, str]]:
        net_name_by_index: dict[int, str] = {}
        net_uid_by_index: dict[int, str] = {}
        for idx, net in enumerate(getattr(pcbdoc, "nets", []) or []):
            name = (getattr(net, "name", "") or "").strip()
            if name:
                net_name_by_index[idx] = name
            uid = (getattr(net, "unique_id", "") or "").strip()
            if uid:
                net_uid_by_index[idx] = uid
        return net_name_by_index, net_uid_by_index

    def _build_net_classes_by_name(
        self,
        pcbdoc: "AltiumPcbDoc",
    ) -> dict[str, tuple[str, ...]]:
        net_classes_acc: dict[str, set[str]] = {}
        for net_class in getattr(pcbdoc, "net_classes", []) or []:
            class_kind = self._safe_int(getattr(net_class, "kind", 0), default=0)
            if class_kind != 0:
                continue
            class_name = (getattr(net_class, "name", "") or "").strip()
            if not class_name:
                continue
            for member_name in getattr(net_class, "members", []) or []:
                net_name = str(member_name or "").strip()
                if not net_name:
                    continue
                net_classes_acc.setdefault(net_name, set()).add(class_name)
        return {
            net_name: tuple(sorted(classes))
            for net_name, classes in net_classes_acc.items()
        }

    def _build_component_metadata(
        self,
        pcbdoc: "AltiumPcbDoc",
    ) -> tuple[dict[int, str], dict[int, str], dict[int, dict[str, object]]]:
        component_designator_by_index: dict[int, str] = {}
        component_uid_by_index: dict[int, str] = {}
        component_data_by_index: dict[int, dict[str, object]] = {}
        for idx, comp in enumerate(getattr(pcbdoc, "components", []) or []):
            designator = (getattr(comp, "designator", "") or "").strip()
            if designator:
                component_designator_by_index[idx] = designator
            uid = (getattr(comp, "unique_id", "") or "").strip()
            if uid:
                component_uid_by_index[idx] = uid
            component_data_by_index[idx] = self._build_component_metadata_entry(
                idx, comp, designator, uid
            )
        return (
            component_designator_by_index,
            component_uid_by_index,
            component_data_by_index,
        )

    def _build_component_metadata_entry(
        self,
        index: int,
        component: object,
        designator: str,
        unique_id: str,
    ) -> dict[str, object]:
        parameters_raw = dict(getattr(component, "parameters", {}) or {})
        parameters = {
            str(key): str(value)
            for key, value in parameters_raw.items()
            if value is not None
        }
        return {
            "index": index,
            "designator": designator,
            "unique_id": unique_id,
            "footprint": (getattr(component, "footprint", "") or "").strip(),
            "description": (getattr(component, "description", "") or "").strip(),
            "layer": (getattr(component, "layer", "") or "").strip(),
            "x_mils": self._safe_component_metric(component, "get_x_mils"),
            "y_mils": self._safe_component_metric(component, "get_y_mils"),
            "rotation_deg": self._safe_component_metric(
                component, "get_rotation_degrees"
            ),
            "parameters": parameters,
        }

    def _safe_component_metric(
        self,
        component: object,
        method_name: str,
    ) -> float | None:
        try:
            metric_getter = getattr(component, method_name, None)
            if callable(metric_getter):
                value = metric_getter()
                if isinstance(value, int | float | str):
                    return float(value)
        except Exception:
            return None
        return None

    def _build_layer_metadata(
        self,
        pcbdoc: "AltiumPcbDoc",
        resolved: Any,
    ) -> tuple[
        tuple[int, ...],
        tuple[str, ...],
        dict[int, str],
        dict[int, str],
        dict[int, str],
        dict[int, str],
        dict[str, _PcbSvgLayerMetadata],
    ]:
        all_layers_for_metadata = self._collect_visible_layers(pcbdoc, force_all=True)
        metadata_by_token = {
            layer.token: _metadata_for_layer_ref(layer.ref)
            for layer in all_layers_for_metadata
        }
        has_drill_holes = self._board_has_drill_holes(pcbdoc)
        if has_drill_holes:
            metadata_by_token[PCB_SVG_DRILLS_LAYER_NAME] = _PcbSvgLayerMetadata(
                token=PCB_SVG_DRILLS_LAYER_NAME,
                layer_key=PCB_SVG_DRILLS_LAYER_KEY,
                name=PCB_SVG_DRILLS_LAYER_NAME,
                display_name=PCB_SVG_DRILLS_LAYER_DISPLAY_NAME,
                role="drill",
                family="derived",
                legacy_layer_id=PCB_SVG_DRILLS_LAYER_ID,
            )

        metadata_by_token = self._apply_resolved_layer_metadata(
            resolved,
            metadata_by_token,
        )
        all_layer_ids = tuple(
            sorted(
                metadata.legacy_layer_id
                for metadata in metadata_by_token.values()
                if metadata.legacy_layer_id is not None
            )
        )
        all_layer_tokens = tuple(sorted(metadata_by_token))
        layer_key_by_id = {
            metadata.legacy_layer_id: metadata.layer_key
            for metadata in metadata_by_token.values()
            if metadata.legacy_layer_id is not None
        }
        layer_name_by_id = {
            metadata.legacy_layer_id: metadata.name
            for metadata in metadata_by_token.values()
            if metadata.legacy_layer_id is not None
        }
        layer_display_name_by_id = {
            metadata.legacy_layer_id: metadata.display_name
            for metadata in metadata_by_token.values()
            if metadata.legacy_layer_id is not None
        }
        layer_token_by_id = {
            metadata.legacy_layer_id: metadata.token
            for metadata in metadata_by_token.values()
            if metadata.legacy_layer_id is not None
        }
        return (
            all_layer_ids,
            all_layer_tokens,
            layer_key_by_id,
            layer_name_by_id,
            layer_display_name_by_id,
            layer_token_by_id,
            metadata_by_token,
        )

    def _apply_resolved_layer_metadata(
        self,
        resolved: Any,
        metadata_by_token: dict[str, _PcbSvgLayerMetadata],
    ) -> dict[str, _PcbSvgLayerMetadata]:
        if resolved is None:
            return metadata_by_token
        for layer in resolved.layers:
            ref = _ref_for_resolved_layer(layer)
            if ref is None:
                continue
            metadata = metadata_by_token.get(ref.token)
            if metadata is None:
                continue
            display_name = str(getattr(layer, "display_name", "") or "").strip()
            layer_key = str(getattr(layer, "layer_key", "") or "").strip()
            default_display = _default_display_name_for_ref(ref)
            metadata_by_token[ref.token] = replace(
                metadata,
                layer_key=layer_key or metadata.layer_key,
                display_name=display_name or metadata.display_name,
                custom_name=(
                    display_name
                    if display_name and display_name != default_display
                    else metadata.custom_name
                ),
            )
        return metadata_by_token

    def _build_primitive_index_by_identity(
        self,
        pcbdoc: "AltiumPcbDoc",
    ) -> dict[tuple[str, int], int]:
        primitive_index_by_identity: dict[tuple[str, int], int] = {}
        primitive_collections = {
            "pad": getattr(pcbdoc, "pads", []) or [],
            "via": getattr(pcbdoc, "vias", []) or [],
            "track": getattr(pcbdoc, "tracks", []) or [],
            "arc": getattr(pcbdoc, "arcs", []) or [],
            "fill": getattr(pcbdoc, "fills", []) or [],
            "region": getattr(pcbdoc, "regions", []) or [],
            "shapebased-region": getattr(pcbdoc, "shapebased_regions", []) or [],
            "text": getattr(pcbdoc, "texts", []) or [],
            "polygon": getattr(pcbdoc, "polygons", []) or [],
        }
        for kind, primitives in primitive_collections.items():
            for idx, primitive in enumerate(primitives):
                primitive_index_by_identity[(kind, id(primitive))] = idx
        return primitive_index_by_identity

    def _compute_board_centroids_safe(
        self,
        pcbdoc: "AltiumPcbDoc",
    ) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
        try:
            return (
                pcbdoc.compute_board_centroid_mils(),
                pcbdoc.compute_board_centroid_relative_to_origin_mils(),
            )
        except Exception:
            return None, None

    def _safe_int(self, value: object, *, default: int) -> int:
        if not isinstance(value, int | float | str):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _render_overlay_defs_scene(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
    ) -> tuple[list[str], list[str]]:
        """
        Generic renderer extension hook for downstream-owned overlays.
        """
        del ctx, pcbdoc
        return [], []

    @staticmethod
    def _update_bounds_point(
        bounds: dict[str, float | None],
        x: float,
        y: float,
    ) -> None:
        bounds["min_x"] = x if bounds["min_x"] is None else min(bounds["min_x"], x)
        bounds["min_y"] = y if bounds["min_y"] is None else min(bounds["min_y"], y)
        bounds["max_x"] = x if bounds["max_x"] is None else max(bounds["max_x"], x)
        bounds["max_y"] = y if bounds["max_y"] is None else max(bounds["max_y"], y)

    def _update_bounds_box(
        self,
        bounds: dict[str, float | None],
        x0: float,
        y0: float,
        x1: float,
        y1: float,
    ) -> None:
        self._update_bounds_point(bounds, x0, y0)
        self._update_bounds_point(bounds, x1, y1)

    def _update_outline_bounds(
        self,
        bounds: dict[str, float | None],
        pcbdoc: "AltiumPcbDoc",
    ) -> None:
        if pcbdoc.board and pcbdoc.board.outline and pcbdoc.board.outline.vertices:
            bx0, by0, bx1, by1 = pcbdoc.board.outline.bounding_box
            self._update_bounds_box(bounds, bx0, by0, bx1, by1)

    def _update_track_bounds(
        self,
        bounds: dict[str, float | None],
        pcbdoc: "AltiumPcbDoc",
    ) -> None:
        for track in pcbdoc.tracks:
            if self._should_skip_primitive_for_svg(track):
                continue
            half_width = max(track.width_mils * 0.5, 0.1)
            self._update_bounds_box(
                bounds,
                track.start_x_mils - half_width,
                track.start_y_mils - half_width,
                track.start_x_mils + half_width,
                track.start_y_mils + half_width,
            )
            self._update_bounds_box(
                bounds,
                track.end_x_mils - half_width,
                track.end_y_mils - half_width,
                track.end_x_mils + half_width,
                track.end_y_mils + half_width,
            )

    def _update_arc_bounds(
        self,
        bounds: dict[str, float | None],
        pcbdoc: "AltiumPcbDoc",
    ) -> None:
        for arc in pcbdoc.arcs:
            if self._should_skip_primitive_for_svg(arc):
                continue
            radius = max(arc.radius_mils + max(arc.width_mils * 0.5, 0.1), 0.1)
            self._update_bounds_box(
                bounds,
                arc.center_x_mils - radius,
                arc.center_y_mils - radius,
                arc.center_x_mils + radius,
                arc.center_y_mils + radius,
            )

    def _update_pad_bounds(
        self,
        bounds: dict[str, float | None],
        pcbdoc: "AltiumPcbDoc",
    ) -> None:
        for pad in pcbdoc.pads:
            if self._should_skip_primitive_for_svg(pad):
                continue
            pad_w_mils = max(getattr(pad, "top_width", 0) / 10000.0, 0.1)
            pad_h_mils = max(getattr(pad, "top_height", 0) / 10000.0, 0.1)
            half_w = pad_w_mils * 0.5
            half_h = pad_h_mils * 0.5
            self._update_bounds_box(
                bounds,
                pad.x_mils - half_w,
                pad.y_mils - half_h,
                pad.x_mils + half_w,
                pad.y_mils + half_h,
            )

    def _update_via_bounds(
        self,
        bounds: dict[str, float | None],
        pcbdoc: "AltiumPcbDoc",
    ) -> None:
        for via in pcbdoc.vias:
            if self._should_skip_primitive_for_svg(via):
                continue
            radius = max(via.diameter_mils * 0.5, via.hole_size_mils * 0.5, 0.1)
            self._update_bounds_box(
                bounds,
                via.x_mils - radius,
                via.y_mils - radius,
                via.x_mils + radius,
                via.y_mils + radius,
            )

    def _update_fill_bounds(
        self,
        bounds: dict[str, float | None],
        pcbdoc: "AltiumPcbDoc",
    ) -> None:
        for fill in pcbdoc.fills:
            if self._should_skip_primitive_for_svg(fill):
                continue
            self._update_bounds_box(
                bounds,
                min(fill.pos1_x_mils, fill.pos2_x_mils),
                min(fill.pos1_y_mils, fill.pos2_y_mils),
                max(fill.pos1_x_mils, fill.pos2_x_mils),
                max(fill.pos1_y_mils, fill.pos2_y_mils),
            )

    def _update_region_bounds(
        self,
        bounds: dict[str, float | None],
        pcbdoc: "AltiumPcbDoc",
    ) -> None:
        for region in pcbdoc.regions:
            if self._should_skip_primitive_for_svg(region):
                continue
            for vertex in getattr(region, "outline_vertices", []):
                self._update_bounds_point(bounds, vertex.x_mils, vertex.y_mils)
            for hole in getattr(region, "hole_vertices", []):
                for vertex in hole:
                    self._update_bounds_point(bounds, vertex.x_mils, vertex.y_mils)

    def _update_shapebased_region_bounds(
        self,
        bounds: dict[str, float | None],
        pcbdoc: "AltiumPcbDoc",
    ) -> None:
        for region in pcbdoc.shapebased_regions:
            if self._should_skip_primitive_for_svg(region):
                continue
            for vertex in getattr(region, "outline", []):
                self._update_bounds_point(bounds, vertex.x_mils, vertex.y_mils)
            for hole in getattr(region, "holes", []):
                for vertex in hole:
                    self._update_bounds_point(bounds, vertex.x_mils, vertex.y_mils)

    def _update_text_bounds(
        self,
        bounds: dict[str, float | None],
        pcbdoc: "AltiumPcbDoc",
    ) -> None:
        for text in pcbdoc.texts:
            if self._should_skip_primitive_for_svg(text):
                continue
            w = max(text.height_mils, 0.1)
            h = max(text.height_mils, 0.1)
            self._update_bounds_box(
                bounds,
                text.x_mils - w * 0.5,
                text.y_mils - h * 0.5,
                text.x_mils + w * 0.5,
                text.y_mils + h * 0.5,
            )

    def _update_polygon_overlay_bounds(
        self,
        bounds: dict[str, float | None],
        pcbdoc: "AltiumPcbDoc",
    ) -> None:
        if not self.options.include_polygon_definition_overlays:
            return

        for polygon in pcbdoc.polygons:
            for vertex in getattr(polygon, "outline", []):
                self._update_bounds_point(bounds, vertex.x_mils, vertex.y_mils)
            for cutout in getattr(polygon, "cutouts", []):
                for vertex in cutout:
                    self._update_bounds_point(bounds, vertex.x_mils, vertex.y_mils)

    def _update_origin_datum_bounds(self, bounds: dict[str, float | None]) -> None:
        if not self.options.show_origin_datum:
            return

        marker_size_mils = (
            max(float(self.options.origin_datum_marker_size_mm), 0.05) / _MIL_TO_MM
        )
        stroke_width_mils = (
            max(float(self.options.origin_datum_stroke_width_mm), 0.0) / _MIL_TO_MM
        )
        radius_mils = marker_size_mils * 0.5 * 1.35 + stroke_width_mils
        self._update_bounds_box(
            bounds,
            -radius_mils,
            -radius_mils,
            radius_mils,
            radius_mils,
        )

    def _compute_bounds_mils(
        self, pcbdoc: "AltiumPcbDoc"
    ) -> tuple[float, float, float, float]:
        bounds: dict[str, float | None] = {
            "min_x": None,
            "min_y": None,
            "max_x": None,
            "max_y": None,
        }

        self._update_outline_bounds(bounds, pcbdoc)
        self._update_track_bounds(bounds, pcbdoc)
        self._update_arc_bounds(bounds, pcbdoc)
        self._update_pad_bounds(bounds, pcbdoc)
        self._update_via_bounds(bounds, pcbdoc)
        self._update_fill_bounds(bounds, pcbdoc)
        self._update_region_bounds(bounds, pcbdoc)
        self._update_shapebased_region_bounds(bounds, pcbdoc)
        self._update_text_bounds(bounds, pcbdoc)
        self._update_polygon_overlay_bounds(bounds, pcbdoc)
        self._update_origin_datum_bounds(bounds)

        min_x = bounds["min_x"]
        min_y = bounds["min_y"]
        max_x = bounds["max_x"]
        max_y = bounds["max_y"]
        if min_x is None or min_y is None or max_x is None or max_y is None:
            # Fallback when geometry data is unavailable.
            return (0.0, 0.0, 1000.0, 1000.0)

        # Add a small border to avoid clipping stroke caps.
        margin = max((max_x - min_x) * 0.01, (max_y - min_y) * 0.01, 1.0)
        return (min_x - margin, min_y - margin, max_x + margin, max_y + margin)

    def _board_has_drill_holes(self, pcbdoc: "AltiumPcbDoc") -> bool:
        for pad in getattr(pcbdoc, "pads", []) or []:
            if self._should_skip_primitive_for_svg(pad):
                continue
            if getattr(pad, "hole_size", 0) > 0:
                return True
        for via in getattr(pcbdoc, "vias", []) or []:
            if self._should_skip_primitive_for_svg(via):
                continue
            if getattr(via, "hole_size_mils", 0.0) > 0.0:
                return True
        return False

    def _collect_visible_layers(
        self,
        pcbdoc: "AltiumPcbDoc",
        *,
        force_all: bool = False,
    ) -> list[_PcbSvgRenderLayer]:
        requested_visible_layers = None if force_all else self.options.visible_layers
        visible_layer_order = self._visible_layer_order_hint(requested_visible_layers)
        visible = (
            {}
            if requested_visible_layers is None
            else {
                layer.ref: layer
                for layer in self._coerced_layers(requested_visible_layers)
            }
        )
        if not visible:
            resolved = self._resolved_layer_stack_safe(pcbdoc)
            stackup_copper_layers = self._stackup_copper_layer_refs(pcbdoc)
            self._collect_visible_primitive_layers(
                pcbdoc,
                visible,
                stackup_copper_layers,
            )
            self._collect_visible_via_layers(
                pcbdoc,
                visible,
                stackup_copper_layers,
            )
            self._collect_visible_derived_pad_layers(pcbdoc, visible)
            self._collect_visible_derived_via_layers(pcbdoc, visible)
            self._collect_visible_polygon_layers(
                pcbdoc,
                visible,
                resolved,
                stackup_copper_layers,
            )

        if not visible:
            visible = {
                PcbLayerRef.top(): _PcbSvgRenderLayer(PcbLayerRef.top()),
                PcbLayerRef.bottom(): _PcbSvgRenderLayer(PcbLayerRef.bottom()),
            }
        return self._order_visible_layers(
            visible,
            force_all=force_all,
            visible_layer_order=visible_layer_order,
        )

    @staticmethod
    def _coerced_layers(layers: Collection[PcbLayerLike]) -> list[_PcbSvgRenderLayer]:
        return [_PcbSvgRenderLayer(coerce_pcb_layer_ref(layer)) for layer in layers]

    def _visible_layer_order_hint(
        self,
        visible_layers: Collection[PcbLayerLike] | None,
    ) -> list[_PcbSvgRenderLayer] | None:
        if self.options.layer_render_order or not isinstance(visible_layers, Sequence):
            return None
        ordered: list[_PcbSvgRenderLayer] = []
        for layer in self._coerced_layers(visible_layers):
            if all(layer.ref != existing.ref for existing in ordered):
                ordered.append(layer)
        return ordered

    def _collect_visible_primitive_layers(
        self,
        pcbdoc: "AltiumPcbDoc",
        visible: dict[PcbLayerRef, _PcbSvgRenderLayer],
        stackup_copper_layers: set[PcbLayerRef] | None,
    ) -> None:
        collection_names = (
            "tracks",
            "arcs",
            "pads",
            "fills",
            "regions",
            "texts",
            "shapebased_regions",
        )
        for collection_name in collection_names:
            for primitive in getattr(pcbdoc, collection_name, []):
                if self._should_skip_primitive_for_svg(primitive):
                    continue
                layer_ref = self._primitive_layer_ref(primitive)
                if layer_ref is None:
                    continue
                self._add_visible_layer(
                    visible,
                    layer_ref,
                    stackup_copper_layers,
                )

    def _primitive_layer_ref(self, primitive: object) -> PcbLayerRef | None:
        layer_ref_method = getattr(primitive, "layer_ref", None)
        if callable(layer_ref_method):
            try:
                ref = layer_ref_method()
            except PcbLayerResolutionError:
                return None
            return ref if isinstance(ref, PcbLayerRef) else None
        layer = self._primitive_layer_enum(primitive)
        return PcbLayerRef.from_legacy(layer) if layer is not None else None

    def _primitive_layer_enum(self, primitive: object) -> PcbLayer | None:
        layer_value = getattr(primitive, "layer", None)
        if layer_value is None:
            return None
        try:
            return PcbLayer(int(layer_value))
        except ValueError:
            return None

    def _collect_visible_via_layers(
        self,
        pcbdoc: "AltiumPcbDoc",
        visible: dict[PcbLayerRef, _PcbSvgRenderLayer],
        stackup_copper_layers: set[PcbLayerRef] | None,
    ) -> None:
        for via in getattr(pcbdoc, "vias", []):
            if self._should_skip_primitive_for_svg(via):
                continue
            for layer in self._iter_via_span_layers(via):
                self._add_visible_layer(
                    visible,
                    PcbLayerRef.from_legacy(layer),
                    stackup_copper_layers,
                )

    def _iter_via_span_layers(self, via: object) -> list[PcbLayer]:
        start = self._safe_int(getattr(via, "layer_start", 1), default=1)
        end = self._safe_int(getattr(via, "layer_end", 32), default=32)
        layers: list[PcbLayer] = []
        for layer_id in range(min(start, end), max(start, end) + 1):
            try:
                layers.append(PcbLayer(layer_id))
            except ValueError:
                continue
        return layers

    def _collect_visible_derived_pad_layers(
        self,
        pcbdoc: "AltiumPcbDoc",
        visible: dict[PcbLayerRef, _PcbSvgRenderLayer],
    ) -> None:
        derived_pad_layers = (
            PcbLayer.TOP,
            PcbLayer.BOTTOM,
            PcbLayer.TOP_SOLDER,
            PcbLayer.BOTTOM_SOLDER,
            PcbLayer.TOP_PASTE,
            PcbLayer.BOTTOM_PASTE,
        )
        for pad in getattr(pcbdoc, "pads", []):
            if self._should_skip_primitive_for_svg(pad):
                continue
            for derived_layer in derived_pad_layers:
                if pad._should_render_on_layer(derived_layer):  # noqa: SLF001
                    self._add_visible_layer(
                        visible,
                        PcbLayerRef.from_legacy(derived_layer),
                        None,
                    )

    def _collect_visible_derived_via_layers(
        self,
        pcbdoc: "AltiumPcbDoc",
        visible: dict[PcbLayerRef, _PcbSvgRenderLayer],
    ) -> None:
        derived_via_layers = (PcbLayer.TOP_SOLDER, PcbLayer.BOTTOM_SOLDER)
        for via in getattr(pcbdoc, "vias", []):
            if self._should_skip_primitive_for_svg(via):
                continue
            for derived_layer in derived_via_layers:
                if via._should_render_on_layer(derived_layer):  # noqa: SLF001
                    self._add_visible_layer(
                        visible,
                        PcbLayerRef.from_legacy(derived_layer),
                        None,
                    )

    def _collect_visible_polygon_layers(
        self,
        pcbdoc: "AltiumPcbDoc",
        visible: dict[PcbLayerRef, _PcbSvgRenderLayer],
        resolved: Any,
        stackup_copper_layers: set[PcbLayerRef] | None,
    ) -> None:
        for polygon in getattr(pcbdoc, "polygons", []):
            layer = self._resolve_polygon_layer(
                polygon,
                resolved,
            )
            if layer is None:
                continue
            self._add_visible_layer(
                visible,
                PcbLayerRef.from_legacy(layer),
                stackup_copper_layers,
            )

    def _resolve_polygon_layer(
        self,
        polygon: object,
        resolved: Any,
    ) -> PcbLayer | None:
        layer_name = str(getattr(polygon, "layer", "")).replace(" ", "").upper()
        if not layer_name:
            return None
        direct_layer = self._resolve_polygon_layer_name(layer_name)
        if direct_layer is not None:
            return direct_layer
        resolved_layer = self._resolve_polygon_layer_from_stack(layer_name, resolved)
        if resolved_layer is not None:
            return resolved_layer
        if layer_name.startswith("INTERNALPLANE"):
            return self._resolve_polygon_layer_name(
                layer_name.replace("INTERNALPLANE", "PLANE")
            )
        return None

    def _resolve_polygon_layer_name(self, layer_name: str) -> PcbLayer | None:
        try:
            return PcbLayer.from_json_name(layer_name)
        except ValueError:
            return None

    def _resolve_polygon_layer_from_stack(
        self,
        layer_name: str,
        resolved: Any,
    ) -> PcbLayer | None:
        if resolved is None:
            return None
        resolved_layer = resolved.layer_by_token(layer_name)
        if resolved_layer is None or resolved_layer.legacy_id is None:
            return None
        try:
            return PcbLayer(int(resolved_layer.legacy_id))
        except ValueError:
            return None

    def _add_visible_layer(
        self,
        visible: dict[PcbLayerRef, _PcbSvgRenderLayer],
        layer: PcbLayerRef,
        stackup_copper_layers: set[PcbLayerRef] | None,
    ) -> None:
        if (
            layer.family in {PcbLayerFamily.SIGNAL, PcbLayerFamily.INTERNAL_PLANE}
            and stackup_copper_layers is not None
            and layer not in stackup_copper_layers
        ):
            return
        visible.setdefault(layer, _PcbSvgRenderLayer(layer))

    def _order_visible_layers(
        self,
        visible: dict[PcbLayerRef, _PcbSvgRenderLayer],
        *,
        force_all: bool,
        visible_layer_order: list[_PcbSvgRenderLayer] | None,
    ) -> list[_PcbSvgRenderLayer]:
        if not force_all and self.options.layer_render_order:
            ordered_layers: list[_PcbSvgRenderLayer] = []
            for requested in self._coerced_layers(self.options.layer_render_order):
                layer = visible.get(requested.ref)
                if layer is not None and layer not in ordered_layers:
                    ordered_layers.append(layer)
            for layer in sorted(visible.values(), key=_render_layer_sort_key):
                if layer not in ordered_layers:
                    ordered_layers.append(layer)
            return ordered_layers
        if not force_all and visible_layer_order:
            ordered_layers = [
                visible[layer.ref]
                for layer in visible_layer_order
                if layer.ref in visible
            ]
            for layer in sorted(visible.values(), key=_render_layer_sort_key):
                if layer not in ordered_layers:
                    ordered_layers.append(layer)
            return ordered_layers
        return sorted(visible.values(), key=_render_layer_sort_key)

    def _stackup_copper_layer_refs(
        self,
        pcbdoc: "AltiumPcbDoc",
    ) -> set[PcbLayerRef] | None:
        """
        Resolve the real copper layer set from board stackup when available.
        """
        resolved = self._resolved_layer_stack_safe(pcbdoc)
        if resolved is None:
            return None

        stackup: set[PcbLayerRef] = set()
        for layer in resolved.layers:
            legacy_id = getattr(layer, "legacy_id", None)
            v7_id = getattr(layer, "v7_id", None)
            ref: PcbLayerRef | None = None
            try:
                if legacy_id is not None:
                    ref = PcbLayerRef.from_legacy(int(legacy_id))
                elif v7_id is not None:
                    ref = PcbLayerRef.from_v7_saved_layer_id(int(v7_id))
            except PcbLayerResolutionError:
                continue
            if ref is None or ref.family not in {
                PcbLayerFamily.SIGNAL,
                PcbLayerFamily.INTERNAL_PLANE,
            }:
                continue
            stackup.add(ref)

        if len(stackup) < 2:
            return None
        return stackup

    def _render_layer_group(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layer: _PcbSvgRenderLayer,
        layer_color_override: str | None = None,
        clip_path_id: str | None = None,
        mask_id: str | None = None,
    ) -> list[str]:
        layer_color, base_primitives, drill_primitives, overlay_primitives = (
            self._collect_render_layer_primitives(
                ctx,
                pcbdoc,
                layer,
                layer_color_override,
            )
        )
        return self._render_layer_group_from_primitives(
            ctx,
            layer,
            layer_color,
            base_primitives,
            drill_primitives,
            overlay_primitives,
            clip_path_id=clip_path_id,
            mask_id=mask_id,
        )

    def _collect_layer_primitives(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layer: PcbLayer,
        layer_color_override: str | None = None,
    ) -> tuple[str, list[str], list[str], list[str]]:
        """
        Collect non-hole, drill-hole, and overlay primitives for a layer.
        """
        layer_color = (
            layer_color_override
            if layer_color_override is not None
            else ctx.layer_color(layer)
        )

        base_primitives: list[str] = []
        base_primitives.extend(
            self._render_regions_for_layer(ctx, pcbdoc, layer, layer_color)
        )
        base_primitives.extend(
            self._render_fills_for_layer(ctx, pcbdoc, layer, layer_color)
        )
        base_primitives.extend(
            self._render_pads_for_layer(ctx, pcbdoc, layer, layer_color)
        )
        base_primitives.extend(
            self._render_vias_for_layer(ctx, pcbdoc, layer, layer_color)
        )
        base_primitives.extend(
            self._render_tracks_for_layer(ctx, pcbdoc, layer, layer_color)
        )
        base_primitives.extend(
            self._render_arcs_for_layer(ctx, pcbdoc, layer, layer_color)
        )
        base_primitives.extend(
            self._render_texts_for_layer(ctx, pcbdoc, layer, layer_color)
        )
        if self.options.include_polygon_definition_overlays:
            base_primitives.extend(
                self._render_polygons_for_layer(ctx, pcbdoc, layer, layer_color)
            )

        drill_primitives = self._render_drill_holes_for_layer(ctx, pcbdoc, layer)
        overlay_primitives = self._render_pad_designator_overlay_for_layer(
            ctx,
            pcbdoc,
            layer,
        )
        return layer_color, base_primitives, drill_primitives, overlay_primitives

    def _collect_render_layer_primitives(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layer: _PcbSvgRenderLayer,
        layer_color_override: str | None = None,
    ) -> tuple[str, list[str], list[str], list[str]]:
        layer_color = layer_color_override or ctx.layer_ref_color(layer.ref)
        if layer.legacy_layer is not None:
            return self._collect_layer_primitives(
                ctx,
                pcbdoc,
                layer.legacy_layer,
                layer_color,
            )

        base_primitives: list[str] = []
        region_collection = (
            pcbdoc.shapebased_regions if pcbdoc.shapebased_regions else pcbdoc.regions
        )
        base_primitives.extend(
            self._render_v7_ref_primitive_collection(
                ctx,
                region_collection,
                layer,
                layer_color,
            )
        )
        base_primitives.extend(
            self._render_v7_ref_primitive_collection(
                ctx,
                pcbdoc.fills,
                layer,
                layer_color,
            )
        )
        base_primitives.extend(
            self._render_v7_ref_primitive_collection(
                ctx,
                pcbdoc.tracks,
                layer,
                layer_color,
            )
        )
        base_primitives.extend(
            self._render_v7_ref_primitive_collection(
                ctx,
                pcbdoc.arcs,
                layer,
                layer_color,
            )
        )
        base_primitives.extend(
            self._render_v7_ref_primitive_collection(
                ctx,
                pcbdoc.texts,
                layer,
                layer_color,
            )
        )
        return layer_color, base_primitives, [], []

    def _layer_group_attrs_from_primitives(
        self,
        ctx: PcbSvgRenderContext,
        layer: _PcbSvgRenderLayer,
        *,
        layer_color: str,
        total_primitive_count: int,
        clip_path_id: str | None,
        mask_id: str | None,
        split_drill_overlay: bool,
    ) -> list[str]:
        attrs = [
            f'id="layer-{html.escape(layer.to_json_name())}"',
        ]
        if clip_path_id and not split_drill_overlay:
            attrs.append(f'clip-path="url(#{html.escape(clip_path_id)})"')
        if mask_id and not split_drill_overlay:
            attrs.append(f'mask="url(#{html.escape(mask_id)})"')
        if self.options.include_metadata:
            attrs.extend(ctx.layer_metadata_attrs_for_ref(layer.ref))
            attrs.extend(
                [
                    f'data-color="{html.escape(layer_color)}"',
                    f'data-primitive-count="{total_primitive_count}"',
                ]
            )
        return attrs

    @staticmethod
    def _clip_mask_group_attrs(
        *,
        clip_path_id: str | None,
        mask_id: str | None = None,
    ) -> list[str]:
        attrs: list[str] = []
        if clip_path_id:
            attrs.append(f'clip-path="url(#{html.escape(clip_path_id)})"')
        if mask_id:
            attrs.append(f'mask="url(#{html.escape(mask_id)})"')
        return attrs

    @staticmethod
    def _append_nested_layer_primitives(
        lines: list[str],
        primitives: list[str] | tuple[str, ...],
        group_attrs: list[str],
        *,
        emit_empty_group: bool = False,
    ) -> None:
        if not primitives and not (group_attrs and emit_empty_group):
            return
        if group_attrs:
            lines.append(f"      <g {' '.join(group_attrs)}>")
        prefix = "        " if group_attrs else "      "
        for primitive in primitives:
            lines.append(f"{prefix}{primitive}")
        if group_attrs:
            lines.append("      </g>")

    def _append_split_drill_overlay_primitives(
        self,
        lines: list[str],
        *,
        base_primitives: list[str] | tuple[str, ...],
        drill_primitives: list[str] | tuple[str, ...],
        overlay_primitives: list[str] | tuple[str, ...],
        clip_path_id: str | None,
        mask_id: str | None,
    ) -> None:
        base_group_attrs = self._clip_mask_group_attrs(
            clip_path_id=clip_path_id,
            mask_id=mask_id,
        )
        self._append_nested_layer_primitives(
            lines,
            base_primitives,
            base_group_attrs,
            emit_empty_group=True,
        )
        overlay_group_attrs = self._clip_mask_group_attrs(clip_path_id=clip_path_id)
        self._append_nested_layer_primitives(
            lines,
            drill_primitives,
            overlay_group_attrs,
            emit_empty_group=True,
        )
        self._append_nested_layer_primitives(
            lines,
            overlay_primitives,
            overlay_group_attrs,
        )

    @staticmethod
    def _append_unsplit_layer_primitives(
        lines: list[str],
        *primitive_groups: list[str] | tuple[str, ...],
    ) -> None:
        for primitives in primitive_groups:
            for primitive in primitives:
                lines.append(f"      {primitive}")

    def _render_layer_group_from_primitives(
        self,
        ctx: PcbSvgRenderContext,
        layer: _PcbSvgRenderLayer,
        layer_color: str,
        base_primitives: list[str] | tuple[str, ...],
        drill_primitives: list[str] | tuple[str, ...],
        overlay_primitives: list[str] | tuple[str, ...],
        *,
        clip_path_id: str | None = None,
        mask_id: str | None = None,
    ) -> list[str]:
        drill_mode = (self.options.drill_hole_mode or "knockout").strip().lower()
        split_drill_overlay = (
            bool(mask_id)
            and layer.is_copper()
            and drill_mode == "overlay"
            and bool(drill_primitives)
        )
        total_primitive_count = (
            len(base_primitives) + len(drill_primitives) + len(overlay_primitives)
        )
        if total_primitive_count == 0 and not self.options.show_empty_layers:
            return []

        attrs = self._layer_group_attrs_from_primitives(
            ctx,
            layer,
            layer_color=layer_color,
            total_primitive_count=total_primitive_count,
            clip_path_id=clip_path_id,
            mask_id=mask_id,
            split_drill_overlay=split_drill_overlay,
        )
        lines = [f"    <g {' '.join(attrs)}>"]
        if split_drill_overlay:
            self._append_split_drill_overlay_primitives(
                lines,
                base_primitives=base_primitives,
                drill_primitives=drill_primitives,
                overlay_primitives=overlay_primitives,
                clip_path_id=clip_path_id,
                mask_id=mask_id,
            )
        else:
            self._append_unsplit_layer_primitives(
                lines,
                base_primitives,
                drill_primitives,
                overlay_primitives,
            )
        lines.append("    </g>")
        return lines

    def _render_primitive_collection(
        self,
        ctx: PcbSvgRenderContext,
        collection: Sequence[object],
        layer: PcbLayer,
        layer_color: str,
        **kwargs: Any,
    ) -> list[str]:
        """
        Render a primitive collection with standardized kwargs.
        """
        elements: list[str] = []
        for primitive in collection:
            if self._should_skip_primitive_for_svg(primitive):
                continue
            to_svg = getattr(primitive, "to_svg", None)
            if to_svg is None:
                continue
            call_kwargs = {
                "stroke": layer_color,
                "include_metadata": self.options.include_metadata,
                **kwargs,
            }
            if self._to_svg_accepts_for_layer(to_svg):
                call_kwargs["for_layer"] = layer
            rendered = to_svg(ctx, **call_kwargs)
            if rendered:
                elements.extend(rendered)
        return elements

    def _render_v7_ref_primitive_collection(
        self,
        ctx: PcbSvgRenderContext,
        collection: Sequence[object],
        layer: _PcbSvgRenderLayer,
        layer_color: str,
    ) -> list[str]:
        """
        Render a migrated primitive collection for a V7-only layer reference.
        """
        elements: list[str] = []
        for primitive in collection:
            if self._should_skip_primitive_for_svg(primitive):
                continue
            primitive_ref = self._primitive_layer_ref(primitive)
            if primitive_ref != layer.ref:
                continue
            to_svg = getattr(primitive, "to_svg", None)
            if to_svg is None:
                continue
            call_kwargs: dict[str, object] = {
                "stroke": layer_color,
                "include_metadata": self.options.include_metadata,
            }
            if self._to_svg_accepts_for_layer(to_svg):
                call_kwargs["for_layer"] = layer.ref
            rendered = to_svg(ctx, **call_kwargs)
            if rendered:
                elements.extend(rendered)
        return elements

    def _render_region_collection(
        self,
        ctx: PcbSvgRenderContext,
        collection: Sequence[object],
        layer: PcbLayer,
        layer_color: str,
    ) -> list[str]:
        """
        Render region primitives with per-object copper-region color semantics.
        """
        elements: list[str] = []
        for primitive in collection:
            if self._should_skip_primitive_for_svg(primitive):
                continue
            to_svg = getattr(primitive, "to_svg", None)
            if to_svg is None:
                continue
            color = self._region_color_for_layer(layer, layer_color, primitive)
            call_kwargs = {
                "stroke": color,
                "include_metadata": self.options.include_metadata,
            }
            if self._to_svg_accepts_for_layer(to_svg):
                call_kwargs["for_layer"] = layer
            rendered = to_svg(ctx, **call_kwargs)
            if rendered:
                elements.extend(rendered)
        return elements

    def _region_color_for_layer(
        self,
        layer: PcbLayer,
        layer_color: str,
        primitive: object,
    ) -> str:
        if not layer.is_copper():
            return layer_color
        polygon_index = self._coerce_optional_int(
            getattr(primitive, "polygon_index", None)
        )
        # Vendor custom pad shapes often arrive as unlinked copper regions.
        # Keep those in the layer color; only linked polygon pours use overlay color.
        if polygon_index is None or polygon_index in _UNLINKED_INDEX_SENTINELS:
            return layer_color
        return str(self.options.polygon_overlay_color or layer_color)

    @staticmethod
    def _coerce_optional_int(value: object) -> int | None:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_svg_accepts_for_layer(to_svg: Any) -> bool:
        """
        Check whether a primitive to_svg callable accepts a for_layer kwarg.
        """
        try:
            signature = inspect.signature(to_svg)
        except (TypeError, ValueError):
            # Builtins or wrapped callables may not expose signatures.
            return True

        if "for_layer" in signature.parameters:
            return True

        for parameter in signature.parameters.values():
            if parameter.kind == inspect.Parameter.VAR_KEYWORD:
                return True

        return False

    @staticmethod
    def _should_skip_primitive_for_svg(primitive: object) -> bool:
        """
        Return True for primitives that should never emit SVG geometry.
        """
        layer_value = getattr(primitive, "layer", None)
        is_on_keepout_layer = False
        try:
            is_on_keepout_layer = (
                layer_value is not None and int(layer_value) == PcbLayer.KEEPOUT.value
            )
        except (TypeError, ValueError):
            pass

        if getattr(primitive, "is_keepout", False) and not is_on_keepout_layer:
            return True

        kind = getattr(primitive, "kind", None)
        if kind is None:
            return False

        kind_name = str(getattr(kind, "name", kind)).upper()
        if kind_name in {
            "BOARD_CUTOUT",
            "POLYGON_CUTOUT",
            "DASHED_OUTLINE",
            "UNKNOWN_3",
            "CAVITY_DEFINITION",
        }:
            return True

        try:
            kind_value = int(kind)
        except (TypeError, ValueError):
            return False

        # Regions6 integer kinds: 1=cutout, 3=board cutout.
        return kind_value in {1, 3}

    def _render_pads_for_layer(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layer: PcbLayer,
        layer_color: str,
    ) -> list[str]:
        return self._render_primitive_collection(
            ctx,
            pcbdoc.pads,
            layer,
            layer_color,
            render_holes=False,
        )

    def _render_pad_designator_overlay_for_layer(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layer: PcbLayer,
    ) -> list[str]:
        if not self.options.show_pad_designators:
            return []
        if not layer.is_copper():
            return []

        labels: list[str] = []
        for pad in getattr(pcbdoc, "pads", []) or []:
            if self._should_skip_primitive_for_svg(pad):
                continue
            label = str(getattr(pad, "designator", "") or "").strip()
            if not label:
                continue
            label_element = self._pad_designator_label_element(ctx, pad, layer, label)
            if label_element:
                labels.append(label_element)
        if not labels:
            return []

        attrs = ['class="pcb-pad-designator-overlay"']
        if self.options.include_metadata:
            attrs.extend(
                [
                    'data-overlay-kind="pad-designator"',
                    'data-overlay-role="review-annotation"',
                ]
            )
            attrs.extend(ctx.layer_metadata_attrs(layer.value))
        lines = [f"<g {' '.join(attrs)}>"]
        lines.extend(f"  {label}" for label in labels)
        lines.append("</g>")
        return ["\n".join(lines)]

    def _pad_designator_label_element(
        self,
        ctx: PcbSvgRenderContext,
        pad: object,
        layer: PcbLayer,
        label: str,
    ) -> str | None:
        if not self._pad_designator_should_render_on_layer(pad, layer):
            return None

        source_layer_fn = getattr(pad, "_source_layer", None)
        geometry_fn = getattr(pad, "_pad_svg_geometry_state", None)
        if not callable(source_layer_fn) or not callable(geometry_fn):
            return None
        source_layer = source_layer_fn()
        geometry = geometry_fn(
            ctx,
            layer=layer,
            source_layer=source_layer,
        )
        if geometry is None:
            return None
        geometry_map = cast(Mapping[str, object], geometry)

        font_size = self._pad_designator_font_size_mm(
            label,
            width_mm=float(cast(int | float | str, geometry_map["width_mm"])),
            height_mm=float(cast(int | float | str, geometry_map["height_mm"])),
        )
        if font_size is None:
            return None

        attrs = [
            'class="pcb-pad-designator-label"',
            f'x="{ctx.fmt(float(cast(int | float | str, geometry_map["cx"])))}"',
            f'y="{ctx.fmt(float(cast(int | float | str, geometry_map["cy"])))}"',
            f'font-family="{html.escape(self.options.pad_designator_font_family)}"',
            f'font-size="{ctx.fmt(font_size)}"',
            'text-anchor="middle"',
            'dominant-baseline="central"',
            'pointer-events="none"',
            f'fill="{html.escape(str(self.options.pad_designator_fill))}"',
        ]
        if self.options.include_metadata:
            escaped_label = html.escape(label)
            attrs.extend(
                [
                    'data-primitive="pad-designator-label"',
                    'data-overlay-kind="pad-designator"',
                    f'data-pad-designator="{escaped_label}"',
                    f'data-pad-number="{escaped_label}"',
                ]
            )
            attrs.extend(ctx.layer_metadata_attrs(layer.value))
            attrs.extend(
                ctx.relationship_metadata_attrs(
                    net_index=getattr(pad, "net_index", None),
                    component_index=getattr(pad, "component_index", None),
                )
            )
            element_id_attr = ctx.primitive_id_attr(
                "pad",
                pad,
                layer_id=layer.value,
                role="designator-label",
            )
            if element_id_attr:
                attrs.append(element_id_attr)

        return f"<text {' '.join(attrs)}>{html.escape(label)}</text>"

    def _pad_designator_should_render_on_layer(
        self,
        pad: object,
        layer: PcbLayer,
    ) -> bool:
        if not layer.is_copper():
            return False
        try:
            should_render = getattr(pad, "_should_render_on_layer", None)
            should_force = getattr(pad, "_should_force_svg_copper_render", None)
            layer_size = getattr(pad, "_layer_size", None)
            if (
                not callable(should_render)
                or not callable(should_force)
                or not callable(layer_size)
            ):
                return False
            if not should_render(layer) and not should_force(layer):
                return False
            size = layer_size(layer)
            if not isinstance(size, (list, tuple)) or len(size) < 2:
                return False
            width_iu, height_iu = size[0], size[1]
        except Exception:
            return False
        return int(width_iu) > 0 and int(height_iu) > 0

    def _pad_designator_font_size_mm(
        self,
        label: str,
        *,
        width_mm: float,
        height_mm: float,
    ) -> float | None:
        padding_ratio = max(
            0.0, min(float(self.options.pad_designator_padding_ratio), 0.45)
        )
        available_w = max(float(width_mm) * (1.0 - padding_ratio), 0.0)
        available_h = max(float(height_mm) * (1.0 - padding_ratio), 0.0)
        if available_w <= 0.0 or available_h <= 0.0:
            return None

        glyph_count = max(len(label), 1)
        width_limited = available_w / max(glyph_count * 0.62, 1.0)
        height_limited = available_h * 0.72
        font_size = min(width_limited, height_limited)
        max_size = self.options.pad_designator_max_font_size_mm
        if max_size is not None:
            font_size = min(font_size, max(float(max_size), 0.0))

        return font_size

    def _render_vias_for_layer(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layer: PcbLayer,
        layer_color: str,
    ) -> list[str]:
        return self._render_primitive_collection(
            ctx,
            pcbdoc.vias,
            layer,
            layer_color,
            render_holes=False,
        )

    def _render_tracks_for_layer(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layer: PcbLayer,
        layer_color: str,
    ) -> list[str]:
        return self._render_primitive_collection(ctx, pcbdoc.tracks, layer, layer_color)

    def _render_arcs_for_layer(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layer: PcbLayer,
        layer_color: str,
    ) -> list[str]:
        return self._render_primitive_collection(ctx, pcbdoc.arcs, layer, layer_color)

    def _render_fills_for_layer(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layer: PcbLayer,
        layer_color: str,
    ) -> list[str]:
        return self._render_primitive_collection(ctx, pcbdoc.fills, layer, layer_color)

    def _render_regions_for_layer(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layer: PcbLayer,
        layer_color: str,
    ) -> list[str]:
        # ShapeBasedRegions are rendered copper geometry. Prefer them when present
        # to avoid duplicate filled regions from parallel region streams.
        if pcbdoc.shapebased_regions:
            rendered = self._render_region_collection(
                ctx,
                pcbdoc.shapebased_regions,
                layer,
                layer_color,
            )
            if rendered:
                return rendered
        return self._render_region_collection(ctx, pcbdoc.regions, layer, layer_color)

    def _render_texts_for_layer(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layer: PcbLayer,
        layer_color: str,
    ) -> list[str]:
        tt_renderer, stroke_renderer, barcode_renderer = self._text_renderers()
        font_resolver = self._embedded_font_resolver(pcbdoc)
        texts = [
            text
            for text in pcbdoc.texts
            if self._should_render_component_linked_text(pcbdoc, text)
        ]
        return self._render_primitive_collection(
            ctx,
            texts,
            layer,
            layer_color,
            text_as_polygons=self.options.text_as_polygons,
            truetype_renderer=tt_renderer,
            stroke_renderer=stroke_renderer,
            barcode_renderer=barcode_renderer,
            font_resolver=font_resolver,
        )

    def _render_drill_holes_for_layer(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layer: PcbLayer,
    ) -> list[str]:
        """
        Render drill holes after copper geometry for this layer.
        """
        if not layer.is_copper():
            return []

        mode = (self.options.drill_hole_mode or "knockout").strip().lower()
        if mode == "none":
            return []

        if mode == "overlay":
            base_color = self.options.drill_hole_overlay_color or "#FF0000"
            plated_color = self.options.drill_hole_overlay_plated_color or base_color
            non_plated_color = (
                self.options.drill_hole_overlay_non_plated_color or base_color
            )
            opacity = self.options.drill_hole_overlay_opacity
            outline = bool(self.options.drill_hole_overlay_outline)
            outline_w = max(
                float(self.options.drill_hole_overlay_outline_width_mm), 0.01
            )
        else:
            # Legacy default output: opaque white knockouts.
            plated_color = "#FFFFFF"
            non_plated_color = "#FFFFFF"
            opacity = 1.0
            outline = False
            outline_w = 0.10

        return self._collect_drill_hole_elements(
            ctx,
            pcbdoc,
            layer,
            plated_hole_color=plated_color,
            non_plated_hole_color=non_plated_color,
            hole_opacity=opacity,
            hole_outline=outline,
            hole_outline_width_mm=outline_w,
            include_metadata=self.options.include_metadata,
        )

    def _collect_drill_hole_elements(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layer: PcbLayer,
        *,
        plated_hole_color: str,
        non_plated_hole_color: str,
        hole_opacity: float,
        hole_outline: bool,
        hole_outline_width_mm: float,
        include_metadata: bool,
    ) -> list[str]:
        """
        Collect pad/via hole primitives for one copper layer.
        """
        if not layer.is_copper():
            return []

        opacity = max(0.0, min(1.0, float(hole_opacity)))
        fill_opacity_attr = (
            f' fill-opacity="{ctx.fmt(opacity)}"' if opacity < 1.0 else ""
        )
        plated_color = str(plated_hole_color)
        non_plated_color = str(non_plated_hole_color)

        elements: list[str] = []
        for pad in pcbdoc.pads:
            if self._should_skip_primitive_for_svg(pad):
                continue
            if getattr(pad, "hole_size", 0) <= 0:
                continue
            if not pad._should_render_on_layer(layer):  # noqa: SLF001
                continue
            # Match Draftsman/oracle behavior: only emit hole geometry where
            # the pad contributes copper on this layer.
            width_iu, height_iu = pad._layer_size(layer)  # noqa: SLF001
            if width_iu <= 0 or height_iu <= 0:
                continue
            pad_plating = bool(getattr(pad, "is_plated", True))
            color = plated_color if pad_plating else non_plated_color
            elements.extend(
                pad._hole_knockout_svg_elements(  # noqa: SLF001
                    ctx,
                    layer,
                    include_metadata=include_metadata,
                    hole_color=color,
                    hole_opacity=opacity,
                    hole_outline=hole_outline,
                    hole_outline_width_mm=hole_outline_width_mm,
                )
            )

        for via in pcbdoc.vias:
            if self._should_skip_primitive_for_svg(via):
                continue
            if not via._spans_layer(layer):  # noqa: SLF001
                continue
            if not should_render_via_drill_hole(via):
                continue

            hole_radius_mm = max(via.hole_size_mils * _MIL_TO_MM / 2.0, 0.0)
            if hole_radius_mm <= 0:
                continue
            via_plating = bool(getattr(via, "is_plated", True))
            color = plated_color if via_plating else non_plated_color

            attrs = [
                f'cx="{ctx.fmt(ctx.x_to_svg(via.x_mils))}"',
                f'cy="{ctx.fmt(ctx.y_to_svg(via.y_mils))}"',
                f'r="{ctx.fmt(hole_radius_mm)}"',
            ]
            if hole_outline:
                outline_w = max(
                    0.01, min(float(hole_outline_width_mm), hole_radius_mm * 2.0)
                )
                attrs.extend(
                    [
                        'fill="none"',
                        f'stroke="{html.escape(color)}"',
                        f'stroke-width="{ctx.fmt(outline_w)}"',
                    ]
                )
                if opacity < 1.0:
                    attrs.append(f'stroke-opacity="{ctx.fmt(opacity)}"')
            else:
                attrs.append(f'fill="{html.escape(color)}"')
                if fill_opacity_attr:
                    attrs.append(fill_opacity_attr.strip())
            if include_metadata:
                via_hole_plating = "plated" if via_plating else "non-plated"
                via_hole_render = "stroke" if hole_outline else "fill"
                attrs.extend(
                    [
                        'data-primitive="via-hole"',
                        'data-hole-owner="via"',
                        'data-hole-kind="round"',
                        f'data-hole-plating="{via_hole_plating}"',
                        f'data-hole-render="{via_hole_render}"',
                    ]
                )
                attrs.extend(ctx.layer_metadata_attrs(layer.value))
                attrs.extend(
                    ctx.relationship_metadata_attrs(
                        net_index=getattr(via, "net_index", None),
                    )
                )
                hole_id_attr = ctx.primitive_id_attr(
                    "via",
                    via,
                    layer_id=layer.value,
                    role="hole",
                )
                if hole_id_attr:
                    attrs.append(hole_id_attr)
            elements.append(f"<circle {' '.join(attrs)}/>")

        return elements

    def _should_render_component_linked_text(
        self,
        pcbdoc: "AltiumPcbDoc",
        text: object,
    ) -> bool:
        if not self.options.respect_component_text_visibility:
            return True

        comp_idx = getattr(text, "component_index", None)
        if comp_idx is None:
            return True
        if comp_idx < 0 or comp_idx >= len(pcbdoc.components):
            return True

        comp = pcbdoc.components[comp_idx]

        if getattr(text, "is_comment", False):
            # Comment text is the component "value" string in PCB.
            if not (
                bool(getattr(comp, "comment_on", True))
                or bool(getattr(comp, "value_on", False))
            ):
                return False

        if getattr(text, "is_designator", False):
            if not (
                bool(getattr(comp, "name_on", True))
                or bool(getattr(comp, "designator_on", False))
            ):
                return False

        return True

    def _render_polygons_for_layer(
        self,
        ctx: PcbSvgRenderContext,
        pcbdoc: "AltiumPcbDoc",
        layer: PcbLayer,
        layer_color: str,
    ) -> list[str]:
        polygon_color = str(self.options.polygon_overlay_color or layer_color)
        return self._render_primitive_collection(
            ctx,
            pcbdoc.polygons,
            layer,
            polygon_color,
        )

    def _render_board_outline(
        self,
        ctx: PcbSvgRenderContext,
        outline: "AltiumBoardOutline | None",
        stroke_color: str,
    ) -> list[str]:
        if outline is None or not outline.vertices:
            return []

        main_path = self._path_from_vertices(ctx, outline.vertices)
        if not main_path:
            return []

        cutout_stroke_color = str(self.options.board_cutout_color or stroke_color)
        lines = ['    <g id="board-outline">']
        profile_attrs = [
            f'd="{main_path}"',
            'fill="none"',
            f'stroke="{stroke_color}"',
            'stroke-width="0.1"',
            'stroke-linejoin="round"',
        ]
        if self.options.include_metadata:
            profile_attrs.append('data-feature="board-outline"')
            profile_attrs.append('data-element-key="board-outline"')
        lines.append("      " + f"<path {' '.join(profile_attrs)}/>")

        for cutout_index, cutout_vertices in enumerate(outline.cutouts):
            cutout_path = self._path_from_vertices(ctx, cutout_vertices)
            if cutout_path:
                cutout_attrs = [
                    f'd="{cutout_path}"',
                    'fill="none"',
                    f'stroke="{cutout_stroke_color}"',
                    'stroke-width="0.1"',
                    'stroke-linejoin="round"',
                ]
                if self.options.include_metadata:
                    cutout_attrs.append('data-feature="board-cutout"')
                    cutout_attrs.append(f'data-feature-index="{cutout_index}"')
                    cutout_attrs.append(
                        f'data-element-key="board-cutout-{cutout_index}"'
                    )
                lines.append("      " + f"<path {' '.join(cutout_attrs)}/>")

        lines.append("    </g>")
        return lines

    def _board_outline_clip_path(
        self,
        ctx: PcbSvgRenderContext,
        outline: "AltiumBoardOutline | None",
    ) -> str:
        """
        Build board-profile clip path (outline minus cutouts).
        """
        if outline is None or not outline.vertices:
            return ""

        main_path = self._path_from_vertices(ctx, outline.vertices)
        if not main_path:
            return ""

        parts = [main_path]
        for cutout_vertices in outline.cutouts:
            cutout_path = self._path_from_vertices(ctx, cutout_vertices)
            if cutout_path:
                parts.append(cutout_path)
        return " ".join(parts)

    def _path_from_vertices(
        self, ctx: PcbSvgRenderContext, vertices: Sequence["BoardOutlineVertex"]
    ) -> str:
        if not vertices:
            return ""

        parts = []
        first = vertices[0]
        parts.append(
            f"M {ctx.fmt(ctx.x_to_svg(first.x_mils))} {ctx.fmt(ctx.y_to_svg(first.y_mils))}"
        )

        n = len(vertices)
        for i in range(n):
            current = vertices[i]
            nxt = vertices[(i + 1) % n]
            if getattr(current, "is_arc", False):
                r_start = math.hypot(
                    current.x_mils - current.center_x_mils,
                    current.y_mils - current.center_y_mils,
                )
                r_end = math.hypot(
                    nxt.x_mils - current.center_x_mils,
                    nxt.y_mils - current.center_y_mils,
                )
                radius_mils = (
                    current.radius_mils
                    if current.radius_mils > 0
                    else (r_start + r_end) * 0.5
                )
                if radius_mils <= 0:
                    radius_mils = max(r_start, r_end, 0.1)
                radius_mm = radius_mils * _MIL_TO_MM

                clockwise, sweep_deg = resolve_outline_arc_segment(current, nxt)
                large_arc_flag = "1" if sweep_deg > 180.0 else "0"
                # SVG sweep-flag follows increasing angles in the current user
                # coordinate system. In our Y-down canvas that is clockwise.
                sweep_flag = "1" if clockwise else "0"

                parts.append(
                    " ".join(
                        [
                            "A",
                            ctx.fmt(radius_mm),
                            ctx.fmt(radius_mm),
                            "0",
                            large_arc_flag,
                            sweep_flag,
                            ctx.fmt(ctx.x_to_svg(nxt.x_mils)),
                            ctx.fmt(ctx.y_to_svg(nxt.y_mils)),
                        ]
                    )
                )
            else:
                parts.append(
                    f"L {ctx.fmt(ctx.x_to_svg(nxt.x_mils))} {ctx.fmt(ctx.y_to_svg(nxt.y_mils))}"
                )
        parts.append("Z")
        return " ".join(parts)
