"""PCB drawing-geometry IR shared by visual output backends."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypedDict

from .altium_pcb_drill_rendering import should_render_via_drill_hole
from .altium_pcb_enums import PadShape, PcbNetClassKind
from .altium_pcb_mask_paste_rules import should_force_pad_copper_render
from .altium_record_pcb__pad import AltiumPcbPad
from .altium_record_pcb__via import AltiumPcbVia
from .altium_record_types import PcbLayer
from .altium_resolved_layer_stack import resolved_layer_stack_from_pcbdoc

if TYPE_CHECKING:
    from .altium_pcbdoc import AltiumPcbDoc


PcbDrawingPrimitiveRole = Literal["copper", "boardhole", "coppercutout"]
PcbDrawingClassKind = Literal["net_class", "differential_pair_class"]


class _PcbPrimitiveMetadataKwargs(TypedDict, total=False):
    net_name: str | None
    net_index: int | None
    net_classes: tuple[str, ...]
    differential_pair_names: tuple[str, ...]
    differential_pair_classes: tuple[str, ...]
    component_designator: str | None


@dataclass(frozen=True)
class PcbDrawingPoint:
    """PCB board-space point in mils."""

    x_mils: float
    y_mils: float


@dataclass(frozen=True)
class PcbDrawingVector:
    """Normalized 2D vector."""

    x: float
    y: float


@dataclass(frozen=True)
class PcbDrawingSize:
    """PCB board-space size in mils."""

    width_mils: float
    height_mils: float


@dataclass(frozen=True)
class PcbDrawingUni:
    """Draftsman-compatible rounded rectangle/capsule geometry in board space."""

    location: PcbDrawingPoint
    direction: PcbDrawingVector
    size: PcbDrawingSize
    radius_mils: float


@dataclass(frozen=True)
class PcbDrawingArc:
    """Draftsman-compatible circular arc geometry in board space."""

    center: PcbDrawingPoint
    radius_mils: float
    start_angle_degrees: float
    end_angle_degrees: float
    thickness_mils: float


@dataclass(frozen=True)
class PcbDrawingPolygon:
    """Polygon contours in PCB board-space mils."""

    contours: tuple[tuple[PcbDrawingPoint, ...], ...]


@dataclass(frozen=True)
class PcbDrawingGeometry:
    """Geometry payload for one PCB primitive."""

    unis: tuple[PcbDrawingUni, ...] = ()
    arcs: tuple[PcbDrawingArc, ...] = ()
    polygons: tuple[PcbDrawingPolygon, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Return true when no drawable geometry is present."""

        return not self.unis and not self.arcs and not self.polygons


@dataclass(frozen=True)
class PcbDrawingPrimitive:
    """One PCB primitive normalized for drawing backends."""

    primitive_kind: str
    role: PcbDrawingPrimitiveRole
    layer: PcbLayer
    geometry: PcbDrawingGeometry
    net_name: str | None = None
    net_index: int | None = None
    net_classes: tuple[str, ...] = ()
    differential_pair_names: tuple[str, ...] = ()
    differential_pair_classes: tuple[str, ...] = ()
    component_designator: str | None = None
    pad_designator: str | None = None
    is_plated: bool | None = None
    source_identity: int | None = None

    def matches_class(self, kind: PcbDrawingClassKind, name: str) -> bool:
        """Return true when this primitive belongs to the requested class."""

        normalized = name.casefold()
        if kind == "net_class":
            return any(item.casefold() == normalized for item in self.net_classes)
        return any(
            item.casefold() == normalized for item in self.differential_pair_classes
        )


@dataclass(frozen=True)
class PcbDrawingLayer:
    """A rendered PCB layer worth of normalized drawing primitives."""

    layer: PcbLayer
    primitives: tuple[PcbDrawingPrimitive, ...]


@dataclass(frozen=True)
class PcbDrawingDocument:
    """Normalized PCB drawing primitives grouped by layer."""

    layers: tuple[PcbDrawingLayer, ...]

    @property
    def primitives(self) -> tuple[PcbDrawingPrimitive, ...]:
        """Return all primitives in layer order."""

        return tuple(
            primitive for layer in self.layers for primitive in layer.primitives
        )

    def layer(self, layer: int | PcbLayer) -> PcbDrawingLayer | None:
        """Return a drawing layer by legacy/TV6 layer id."""

        layer_id = int(layer)
        for drawing_layer in self.layers:
            if int(drawing_layer.layer) == layer_id:
                return drawing_layer
        return None

    def primitives_for_layer(
        self,
        layer: int | PcbLayer,
    ) -> tuple[PcbDrawingPrimitive, ...]:
        """Return primitives for a legacy/TV6 layer id."""

        drawing_layer = self.layer(layer)
        if drawing_layer is None:
            return ()
        return drawing_layer.primitives


@dataclass(frozen=True)
class PcbRoutedClassView:
    """A routed net-class or differential-pair-class view discovered from PcbDoc."""

    kind: PcbDrawingClassKind
    name: str
    members: tuple[str, ...]
    nets: tuple[str, ...]
    layers: tuple[PcbLayer, ...]


def build_pcb_drawing_geometry(
    pcbdoc: "AltiumPcbDoc",
    *,
    layers: "Iterable[PcbLayer | int] | None" = None,
) -> PcbDrawingDocument:
    """Build normalized PCB drawing primitives without invoking an output backend."""

    metadata = _PcbSourceMetadata.from_pcbdoc(pcbdoc)
    requested_layers = _coerce_layer_filter(layers)
    actual_copper_layers = _actual_copper_layers(pcbdoc)
    primitives: list[PcbDrawingPrimitive] = []

    primitives.extend(_track_primitives(pcbdoc, metadata, requested_layers))
    primitives.extend(_arc_primitives(pcbdoc, metadata, requested_layers))
    primitives.extend(_fill_primitives(pcbdoc, metadata, requested_layers))
    primitives.extend(
        _pad_primitives(pcbdoc, metadata, actual_copper_layers, requested_layers)
    )
    primitives.extend(
        _via_primitives(pcbdoc, metadata, actual_copper_layers, requested_layers)
    )
    primitives.extend(_region_primitives(pcbdoc, metadata, requested_layers))

    layers_by_id: dict[int, list[PcbDrawingPrimitive]] = {}
    for primitive in primitives:
        if primitive.geometry.is_empty:
            continue
        layers_by_id.setdefault(int(primitive.layer), []).append(primitive)

    return PcbDrawingDocument(
        layers=tuple(
            PcbDrawingLayer(PcbLayer(layer_id), tuple(layer_primitives))
            for layer_id, layer_primitives in sorted(layers_by_id.items())
        )
    )


def discover_pcb_routed_class_views(
    pcbdoc: "AltiumPcbDoc",
    *,
    min_routing_length_mils: float = 10.0,
) -> tuple[PcbRoutedClassView, ...]:
    """Discover routed net-class and differential-pair-class views."""

    views: list[PcbRoutedClassView] = []
    net_name_by_index = _net_name_by_index(pcbdoc)

    for pcb_class in getattr(pcbdoc, "net_classes", []) or []:
        class_name = str(getattr(pcb_class, "name", "") or "").strip()
        if not class_name:
            continue

        try:
            class_kind = PcbNetClassKind(int(getattr(pcb_class, "kind", 0)))
        except (TypeError, ValueError):
            continue

        members = tuple(
            member
            for member in (
                str(raw_member or "").strip()
                for raw_member in getattr(pcb_class, "members", []) or []
            )
            if member
        )
        if class_kind == PcbNetClassKind.NET:
            nets = members
            kind: PcbDrawingClassKind = "net_class"
        elif class_kind == PcbNetClassKind.DIFF_PAIR:
            nets = _nets_for_differential_pair_class(pcbdoc, class_name, members)
            kind = "differential_pair_class"
        else:
            continue

        if not nets:
            continue

        routed_layers = _routed_layers_for_nets(
            pcbdoc,
            set(nets),
            net_name_by_index,
            min_routing_length_mils=min_routing_length_mils,
        )
        if not routed_layers:
            continue

        views.append(
            PcbRoutedClassView(
                kind=kind,
                name=class_name,
                members=members,
                nets=tuple(sorted(set(nets), key=str.casefold)),
                layers=tuple(routed_layers),
            )
        )

    return tuple(
        sorted(
            views,
            key=lambda view: (
                0 if view.kind == "net_class" else 1,
                view.name.casefold(),
            ),
        )
    )


@dataclass(frozen=True)
class _PcbSourceMetadata:
    net_name_by_index: dict[int, str]
    net_classes_by_net_name: dict[str, tuple[str, ...]]
    differential_pair_names_by_net_name: dict[str, tuple[str, ...]]
    differential_pair_classes_by_net_name: dict[str, tuple[str, ...]]
    component_designator_by_index: dict[int, str]

    @classmethod
    def from_pcbdoc(cls, pcbdoc: "AltiumPcbDoc") -> "_PcbSourceMetadata":
        net_name_by_index = _net_name_by_index(pcbdoc)
        return cls(
            net_name_by_index=net_name_by_index,
            net_classes_by_net_name=_net_classes_by_net_name(pcbdoc),
            differential_pair_names_by_net_name=_differential_pair_names_by_net_name(
                pcbdoc
            ),
            differential_pair_classes_by_net_name=(
                _differential_pair_classes_by_net_name(pcbdoc)
            ),
            component_designator_by_index=_component_designator_by_index(pcbdoc),
        )

    def primitive_kwargs(self, primitive: object) -> _PcbPrimitiveMetadataKwargs:
        """Return relationship metadata for one source primitive."""

        net_index = _normalized_net_index(getattr(primitive, "net_index", None))
        net_name = None if net_index is None else self.net_name_by_index.get(net_index)
        component_index = _normalized_int(getattr(primitive, "component_index", None))
        return _PcbPrimitiveMetadataKwargs(
            net_index=net_index,
            net_name=net_name,
            net_classes=tuple(self.net_classes_by_net_name.get(net_name or "", ())),
            differential_pair_names=tuple(
                self.differential_pair_names_by_net_name.get(net_name or "", ())
            ),
            differential_pair_classes=tuple(
                self.differential_pair_classes_by_net_name.get(net_name or "", ())
            ),
            component_designator=(
                None
                if component_index is None
                else self.component_designator_by_index.get(component_index)
            ),
        )


def _track_primitives(
    pcbdoc: "AltiumPcbDoc",
    metadata: _PcbSourceMetadata,
    requested_layers: set[PcbLayer] | None,
) -> list[PcbDrawingPrimitive]:
    result: list[PcbDrawingPrimitive] = []
    for track in getattr(pcbdoc, "tracks", []) or []:
        if _should_skip_drawing_primitive(track):
            continue
        layer = _primitive_layer(track)
        if layer is None or not _include_layer(layer, requested_layers):
            continue

        start_x = float(getattr(track, "start_x_mils", 0.0) or 0.0)
        start_y = float(getattr(track, "start_y_mils", 0.0) or 0.0)
        end_x = float(getattr(track, "end_x_mils", 0.0) or 0.0)
        end_y = float(getattr(track, "end_y_mils", 0.0) or 0.0)
        width = max(float(getattr(track, "width_mils", 0.0) or 0.0), 0.0)
        dx = end_x - start_x
        dy = end_y - start_y
        length = math.hypot(dx, dy)
        if length <= 1e-9 or width <= 0.0:
            continue

        result.append(
            PcbDrawingPrimitive(
                primitive_kind="track",
                role="copper",
                layer=layer,
                geometry=PcbDrawingGeometry(
                    unis=(
                        PcbDrawingUni(
                            location=PcbDrawingPoint(
                                (start_x + end_x) * 0.5,
                                (start_y + end_y) * 0.5,
                            ),
                            direction=PcbDrawingVector(dx / length, dy / length),
                            size=PcbDrawingSize(length + width, width),
                            radius_mils=width * 0.5,
                        ),
                    )
                ),
                **metadata.primitive_kwargs(track),
            )
        )
    return result


def _arc_primitives(
    pcbdoc: "AltiumPcbDoc",
    metadata: _PcbSourceMetadata,
    requested_layers: set[PcbLayer] | None,
) -> list[PcbDrawingPrimitive]:
    result: list[PcbDrawingPrimitive] = []
    for arc in getattr(pcbdoc, "arcs", []) or []:
        if _should_skip_drawing_primitive(arc):
            continue
        layer = _primitive_layer(arc)
        if layer is None or not _include_layer(layer, requested_layers):
            continue

        radius = max(float(getattr(arc, "radius_mils", 0.0) or 0.0), 0.0)
        thickness = max(float(getattr(arc, "width_mils", 0.0) or 0.0), 0.0)
        if radius <= 0.0 or thickness <= 0.0:
            continue

        result.append(
            PcbDrawingPrimitive(
                primitive_kind="arc",
                role="copper",
                layer=layer,
                geometry=PcbDrawingGeometry(
                    arcs=(
                        PcbDrawingArc(
                            center=PcbDrawingPoint(
                                float(getattr(arc, "center_x_mils", 0.0) or 0.0),
                                float(getattr(arc, "center_y_mils", 0.0) or 0.0),
                            ),
                            radius_mils=radius,
                            start_angle_degrees=float(
                                getattr(arc, "start_angle", 0.0) or 0.0
                            ),
                            end_angle_degrees=float(
                                getattr(arc, "end_angle", 0.0) or 0.0
                            ),
                            thickness_mils=thickness,
                        ),
                    )
                ),
                **metadata.primitive_kwargs(arc),
            )
        )
    return result


def _fill_primitives(
    pcbdoc: "AltiumPcbDoc",
    metadata: _PcbSourceMetadata,
    requested_layers: set[PcbLayer] | None,
) -> list[PcbDrawingPrimitive]:
    result: list[PcbDrawingPrimitive] = []
    for fill in getattr(pcbdoc, "fills", []) or []:
        if _should_skip_drawing_primitive(fill):
            continue
        layer = _primitive_layer(fill)
        if layer is None or not _include_layer(layer, requested_layers):
            continue

        x1 = float(getattr(fill, "pos1_x_mils", 0.0) or 0.0)
        y1 = float(getattr(fill, "pos1_y_mils", 0.0) or 0.0)
        x2 = float(getattr(fill, "pos2_x_mils", 0.0) or 0.0)
        y2 = float(getattr(fill, "pos2_y_mils", 0.0) or 0.0)
        width = abs(x2 - x1)
        height = abs(y2 - y1)
        if width <= 0.0 or height <= 0.0:
            continue

        angle = math.radians(float(getattr(fill, "rotation", 0.0) or 0.0))
        result.append(
            PcbDrawingPrimitive(
                primitive_kind="fill",
                role="copper",
                layer=layer,
                geometry=PcbDrawingGeometry(
                    unis=(
                        PcbDrawingUni(
                            location=PcbDrawingPoint((x1 + x2) * 0.5, (y1 + y2) * 0.5),
                            direction=PcbDrawingVector(
                                math.cos(angle), math.sin(angle)
                            ),
                            size=PcbDrawingSize(width, height),
                            radius_mils=0.0,
                        ),
                    )
                ),
                **metadata.primitive_kwargs(fill),
            )
        )
    return result


def _pad_primitives(
    pcbdoc: "AltiumPcbDoc",
    metadata: _PcbSourceMetadata,
    actual_copper_layers: tuple[PcbLayer, ...],
    requested_layers: set[PcbLayer] | None,
) -> list[PcbDrawingPrimitive]:
    result: list[PcbDrawingPrimitive] = []
    for pad in getattr(pcbdoc, "pads", []) or []:
        if not isinstance(pad, AltiumPcbPad):
            continue
        if _should_skip_drawing_primitive(pad):
            continue
        for layer in actual_copper_layers:
            if not _include_layer(layer, requested_layers):
                continue
            if not pad._should_render_on_layer(  # noqa: SLF001
                layer
            ) and not should_force_pad_copper_render(pad, layer):
                continue
            primitive = _pad_copper_primitive(pad, layer, metadata)
            if primitive is not None:
                result.append(primitive)
            hole_primitive = _pad_hole_primitive(pad, layer, metadata)
            if hole_primitive is not None:
                result.append(hole_primitive)
    return result


def _pad_copper_primitive(
    pad: AltiumPcbPad,
    layer: PcbLayer,
    metadata: _PcbSourceMetadata,
) -> PcbDrawingPrimitive | None:
    width_iu, height_iu = pad._layer_size(layer)  # noqa: SLF001
    if width_iu <= 0 or height_iu <= 0:
        return None

    width = float(width_iu) / 10000.0
    height = float(height_iu) / 10000.0
    shape = pad._layer_shape(layer)  # noqa: SLF001
    rotation = float(getattr(pad, "rotation", 0.0) or 0.0)

    if shape == PadShape.OCTAGONAL:
        points = pad._octagon_points(  # noqa: SLF001
            float(getattr(pad, "x_mils", 0.0) or 0.0),
            float(getattr(pad, "y_mils", 0.0) or 0.0),
            width * 0.5,
            height * 0.5,
            rotation,
        )
        geometry = PcbDrawingGeometry(
            polygons=(
                PcbDrawingPolygon(
                    contours=(
                        tuple(PcbDrawingPoint(float(x), float(y)) for x, y in points),
                    )
                ),
            )
        )
    else:
        if shape == PadShape.RECTANGLE:
            radius = 0.0
        elif shape == PadShape.ROUNDED_RECTANGLE:
            radius = float(
                pad._layer_corner_radius_mils(layer, width, height)  # noqa: SLF001
            )
        else:
            radius = min(width, height) * 0.5
        geometry = PcbDrawingGeometry(
            unis=(
                _uni_from_center_size_rotation(
                    x_mils=float(getattr(pad, "x_mils", 0.0) or 0.0),
                    y_mils=float(getattr(pad, "y_mils", 0.0) or 0.0),
                    width_mils=width,
                    height_mils=height,
                    radius_mils=radius,
                    rotation_degrees=rotation,
                ),
            )
        )

    return PcbDrawingPrimitive(
        primitive_kind="pad",
        role="copper",
        layer=layer,
        geometry=geometry,
        pad_designator=_pad_designator(pad),
        is_plated=_pad_is_plated(pad),
        source_identity=id(pad),
        **metadata.primitive_kwargs(pad),
    )


def _pad_hole_primitive(
    pad: AltiumPcbPad,
    layer: PcbLayer,
    metadata: _PcbSourceMetadata,
) -> PcbDrawingPrimitive | None:
    hole_size = float(getattr(pad, "hole_size_mils", 0.0) or 0.0)
    if hole_size <= 0.0:
        return None
    hole_center_x, hole_center_y = _pad_hole_center_mils(pad, layer)
    slot_length = _slot_length_mils(pad, hole_size)
    is_slot = _is_slot_hole(pad, hole_size, slot_length)
    width = slot_length if is_slot else hole_size
    rotation = (
        float(getattr(pad, "slot_rotation", 0.0) or 0.0)
        + float(getattr(pad, "rotation", 0.0) or 0.0)
        if is_slot
        else 0.0
    )
    return PcbDrawingPrimitive(
        primitive_kind="pad",
        role="boardhole",
        layer=layer,
        geometry=PcbDrawingGeometry(
            unis=(
                _uni_from_center_size_rotation(
                    x_mils=hole_center_x,
                    y_mils=hole_center_y,
                    width_mils=width,
                    height_mils=hole_size,
                    radius_mils=hole_size * 0.5,
                    rotation_degrees=rotation,
                ),
            )
        ),
        pad_designator=_pad_designator(pad),
        is_plated=_pad_is_plated(pad),
        source_identity=id(pad),
        **metadata.primitive_kwargs(pad),
    )


def _via_primitives(
    pcbdoc: "AltiumPcbDoc",
    metadata: _PcbSourceMetadata,
    actual_copper_layers: tuple[PcbLayer, ...],
    requested_layers: set[PcbLayer] | None,
) -> list[PcbDrawingPrimitive]:
    result: list[PcbDrawingPrimitive] = []
    for via in getattr(pcbdoc, "vias", []) or []:
        if not isinstance(via, AltiumPcbVia):
            continue
        if _should_skip_drawing_primitive(via):
            continue
        for layer in actual_copper_layers:
            if not _include_layer(layer, requested_layers):
                continue
            if not via._spans_layer(layer):  # noqa: SLF001
                continue
            layer_index = layer.value - 1
            if (
                0 <= layer_index < len(getattr(via, "is_pad_removed", []))
                and via.is_pad_removed[layer_index]
            ):
                continue
            diameter_iu = via._diameter_for_layer(layer)  # noqa: SLF001
            if diameter_iu <= 0:
                continue

            diameter = float(diameter_iu) / 10000.0
            result.append(
                PcbDrawingPrimitive(
                    primitive_kind="via",
                    role="copper",
                    layer=layer,
                    geometry=PcbDrawingGeometry(
                        unis=(
                            _uni_from_center_size_rotation(
                                x_mils=float(getattr(via, "x_mils", 0.0) or 0.0),
                                y_mils=float(getattr(via, "y_mils", 0.0) or 0.0),
                                width_mils=diameter,
                                height_mils=diameter,
                                radius_mils=diameter * 0.5,
                                rotation_degrees=0.0,
                            ),
                        )
                    ),
                    is_plated=True,
                    source_identity=id(via),
                    **metadata.primitive_kwargs(via),
                )
            )
            if should_render_via_drill_hole(via):
                hole_size = float(getattr(via, "hole_size_mils", 0.0) or 0.0)
                result.append(
                    PcbDrawingPrimitive(
                        primitive_kind="via",
                        role="boardhole",
                        layer=layer,
                        geometry=PcbDrawingGeometry(
                            unis=(
                                _uni_from_center_size_rotation(
                                    x_mils=float(getattr(via, "x_mils", 0.0) or 0.0),
                                    y_mils=float(getattr(via, "y_mils", 0.0) or 0.0),
                                    width_mils=hole_size,
                                    height_mils=hole_size,
                                    radius_mils=hole_size * 0.5,
                                    rotation_degrees=0.0,
                                ),
                            )
                        ),
                        is_plated=True,
                        source_identity=id(via),
                        **metadata.primitive_kwargs(via),
                    )
                )
    return result


def _region_primitives(
    pcbdoc: "AltiumPcbDoc",
    metadata: _PcbSourceMetadata,
    requested_layers: set[PcbLayer] | None,
) -> list[PcbDrawingPrimitive]:
    result: list[PcbDrawingPrimitive] = []
    regions = getattr(pcbdoc, "shapebased_regions", []) or getattr(
        pcbdoc, "regions", []
    )
    for region in regions:
        if _should_skip_drawing_primitive(region):
            continue
        layer = _primitive_layer(region)
        if layer is None or not _include_layer(layer, requested_layers):
            continue
        contours = _region_contours(region)
        if not contours:
            continue
        result.append(
            PcbDrawingPrimitive(
                primitive_kind="region",
                role="copper",
                layer=layer,
                geometry=PcbDrawingGeometry(
                    polygons=(PcbDrawingPolygon(contours=contours),)
                ),
                **metadata.primitive_kwargs(region),
            )
        )
    return result


def _region_contours(region: object) -> tuple[tuple[PcbDrawingPoint, ...], ...]:
    outline = getattr(region, "outline_vertices", None)
    holes = getattr(region, "hole_vertices", None)
    if outline is None:
        outline = getattr(region, "outline", None)
        holes = getattr(region, "holes", None)
    if not outline:
        return ()

    contours: list[tuple[PcbDrawingPoint, ...]] = []
    outline_points = _points_from_vertices(_iterable_or_empty(outline))
    if len(outline_points) >= 3:
        contours.append(outline_points)
    for hole in _iterable_or_empty(holes):
        hole_points = _points_from_vertices(_iterable_or_empty(hole))
        if len(hole_points) >= 3:
            contours.append(hole_points)
    return tuple(contours)


def _points_from_vertices(vertices: Iterable[object]) -> tuple[PcbDrawingPoint, ...]:
    points: list[PcbDrawingPoint] = []
    for vertex in vertices:
        points.append(
            PcbDrawingPoint(
                float(getattr(vertex, "x_mils", 0.0) or 0.0),
                float(getattr(vertex, "y_mils", 0.0) or 0.0),
            )
        )
    return _dedupe_points(tuple(points))


def _iterable_or_empty(value: object) -> Iterable[object]:
    if value is None or isinstance(value, str | bytes):
        return ()
    if isinstance(value, Iterable):
        return value
    return ()


def _uni_from_center_size_rotation(
    *,
    x_mils: float,
    y_mils: float,
    width_mils: float,
    height_mils: float,
    radius_mils: float,
    rotation_degrees: float,
) -> PcbDrawingUni:
    angle = math.radians(rotation_degrees)
    return PcbDrawingUni(
        location=PcbDrawingPoint(x_mils, y_mils),
        direction=PcbDrawingVector(math.cos(angle), math.sin(angle)),
        size=PcbDrawingSize(width_mils, height_mils),
        radius_mils=max(radius_mils, 0.0),
    )


def _pad_designator(pad: AltiumPcbPad) -> str | None:
    designator = str(getattr(pad, "designator", "") or "").strip()
    return designator or None


def _pad_is_plated(pad: AltiumPcbPad) -> bool:
    return bool(getattr(pad, "is_plated", False))


def _pad_hole_center_mils(
    pad: AltiumPcbPad,
    layer: PcbLayer,
) -> tuple[float, float]:
    hole_center_mils = getattr(pad, "hole_center_mils", None)
    if callable(hole_center_mils):
        try:
            result = hole_center_mils(layer)
            if isinstance(result, tuple | list) and len(result) >= 2:
                return float(result[0]), float(result[1])
        except (TypeError, ValueError):
            pass
    return (
        float(getattr(pad, "x_mils", 0.0) or 0.0),
        float(getattr(pad, "y_mils", 0.0) or 0.0),
    )


def _slot_length_mils(pad: AltiumPcbPad, hole_size_mils: float) -> float:
    slot_size = getattr(pad, "slot_size_mils", None)
    if slot_size is not None:
        try:
            return max(float(slot_size or 0.0), hole_size_mils)
        except (TypeError, ValueError):
            return hole_size_mils

    slot_size_iu = _normalized_int(getattr(pad, "slot_size", None))
    if slot_size_iu is None or slot_size_iu <= 0:
        return hole_size_mils
    from_internal_units = getattr(pad, "_from_internal_units", None)
    if not callable(from_internal_units):
        return hole_size_mils
    try:
        resolved_slot_size = from_internal_units(slot_size_iu)
        if not isinstance(resolved_slot_size, int | float | str):
            return hole_size_mils
        return max(float(resolved_slot_size), hole_size_mils)
    except (TypeError, ValueError):
        return hole_size_mils


def _is_slot_hole(
    pad: AltiumPcbPad,
    hole_size_mils: float,
    slot_length_mils: float,
) -> bool:
    hole_shape = _normalized_int(getattr(pad, "hole_shape", None))
    return hole_shape == 2 and slot_length_mils > hole_size_mils + 1e-9


def _routed_layers_for_nets(
    pcbdoc: "AltiumPcbDoc",
    nets: set[str],
    net_name_by_index: dict[int, str],
    *,
    min_routing_length_mils: float,
) -> list[PcbLayer]:
    layers: set[PcbLayer] = set()
    for track in getattr(pcbdoc, "tracks", []) or []:
        layer = _primitive_layer(track)
        if layer is None or not layer.is_copper():
            continue
        if _primitive_net_name(track, net_name_by_index) not in nets:
            continue
        if _track_length_mils(track) >= min_routing_length_mils:
            layers.add(layer)

    for arc in getattr(pcbdoc, "arcs", []) or []:
        layer = _primitive_layer(arc)
        if layer is None or not layer.is_copper():
            continue
        if _primitive_net_name(arc, net_name_by_index) not in nets:
            continue
        if _arc_length_mils(arc) >= min_routing_length_mils:
            layers.add(layer)

    return sorted(layers, key=lambda layer: layer.value)


def _nets_for_differential_pair_class(
    pcbdoc: "AltiumPcbDoc",
    class_name: str,
    members: tuple[str, ...],
) -> tuple[str, ...]:
    pairs_by_name = {
        str(getattr(pair, "name", "") or "").casefold(): pair
        for pair in getattr(pcbdoc, "differential_pairs", []) or []
        if str(getattr(pair, "name", "") or "").strip()
    }

    pair_names = members
    if not pair_names and class_name.strip().casefold() == "all differential pairs":
        pair_names = tuple(
            str(getattr(pair, "name", "") or "") for pair in pairs_by_name.values()
        )

    nets: list[str] = []
    for pair_name in pair_names:
        pair = pairs_by_name.get(pair_name.casefold())
        if pair is None:
            continue
        for net_name in getattr(pair, "net_names", ()):
            clean = str(net_name or "").strip()
            if clean:
                nets.append(clean)
    return tuple(nets)


def _net_name_by_index(pcbdoc: "AltiumPcbDoc") -> dict[int, str]:
    result: dict[int, str] = {}
    for index, net in enumerate(getattr(pcbdoc, "nets", []) or []):
        name = str(getattr(net, "name", "") or "").strip()
        if name:
            result[index] = name
    return result


def _net_classes_by_net_name(pcbdoc: "AltiumPcbDoc") -> dict[str, tuple[str, ...]]:
    classes: dict[str, set[str]] = {}
    for pcb_class in getattr(pcbdoc, "net_classes", []) or []:
        try:
            class_kind = PcbNetClassKind(int(getattr(pcb_class, "kind", 0)))
        except (TypeError, ValueError):
            continue
        if class_kind != PcbNetClassKind.NET:
            continue
        class_name = str(getattr(pcb_class, "name", "") or "").strip()
        if not class_name:
            continue
        for member in getattr(pcb_class, "members", []) or []:
            net_name = str(member or "").strip()
            if net_name:
                classes.setdefault(net_name, set()).add(class_name)
    return _freeze_string_sets(classes)


def _differential_pair_names_by_net_name(
    pcbdoc: "AltiumPcbDoc",
) -> dict[str, tuple[str, ...]]:
    pairs: dict[str, set[str]] = {}
    for pair in getattr(pcbdoc, "differential_pairs", []) or []:
        pair_name = str(getattr(pair, "name", "") or "").strip()
        if not pair_name:
            continue
        for net_name in getattr(pair, "net_names", ()):
            clean = str(net_name or "").strip()
            if clean:
                pairs.setdefault(clean, set()).add(pair_name)
    return _freeze_string_sets(pairs)


def _differential_pair_classes_by_net_name(
    pcbdoc: "AltiumPcbDoc",
) -> dict[str, tuple[str, ...]]:
    classes: dict[str, set[str]] = {}
    for pcb_class in getattr(pcbdoc, "net_classes", []) or []:
        try:
            class_kind = PcbNetClassKind(int(getattr(pcb_class, "kind", 0)))
        except (TypeError, ValueError):
            continue
        if class_kind != PcbNetClassKind.DIFF_PAIR:
            continue
        class_name = str(getattr(pcb_class, "name", "") or "").strip()
        if not class_name:
            continue
        members = tuple(
            str(item or "").strip() for item in getattr(pcb_class, "members", []) or []
        )
        for net_name in _nets_for_differential_pair_class(pcbdoc, class_name, members):
            classes.setdefault(net_name, set()).add(class_name)
    return _freeze_string_sets(classes)


def _component_designator_by_index(pcbdoc: "AltiumPcbDoc") -> dict[int, str]:
    result: dict[int, str] = {}
    for index, component in enumerate(getattr(pcbdoc, "components", []) or []):
        designator = str(getattr(component, "designator", "") or "").strip()
        if designator:
            result[index] = designator
    return result


def _freeze_string_sets(source: dict[str, set[str]]) -> dict[str, tuple[str, ...]]:
    return {
        key: tuple(sorted(values, key=str.casefold)) for key, values in source.items()
    }


def _primitive_layer(primitive: object) -> PcbLayer | None:
    try:
        return PcbLayer(int(getattr(primitive, "layer")))
    except (TypeError, ValueError):
        return None


def _primitive_net_name(
    primitive: object,
    net_name_by_index: dict[int, str],
) -> str | None:
    net_index = _normalized_net_index(getattr(primitive, "net_index", None))
    if net_index is None:
        return None
    return net_name_by_index.get(net_index)


def _normalized_net_index(value: object) -> int | None:
    index = _normalized_int(value)
    if index is None or index in {-1, 0xFFFF, 65535}:
        return None
    return index


def _normalized_int(value: object) -> int | None:
    if not isinstance(value, int | float | str):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _track_length_mils(track: object) -> float:
    return math.hypot(
        float(getattr(track, "end_x_mils", 0.0) or 0.0)
        - float(getattr(track, "start_x_mils", 0.0) or 0.0),
        float(getattr(track, "end_y_mils", 0.0) or 0.0)
        - float(getattr(track, "start_y_mils", 0.0) or 0.0),
    )


def _arc_length_mils(arc: object) -> float:
    radius = max(float(getattr(arc, "radius_mils", 0.0) or 0.0), 0.0)
    if radius <= 0.0:
        return 0.0
    start = float(getattr(arc, "start_angle", 0.0) or 0.0)
    end = float(getattr(arc, "end_angle", 0.0) or 0.0)
    delta = (end - start) % 360.0
    if math.isclose(delta, 0.0, abs_tol=1e-9):
        delta = 360.0
    return abs(radius * math.radians(delta))


def _should_skip_drawing_primitive(primitive: object) -> bool:
    layer_value = getattr(primitive, "layer", None)
    is_keepout_layer = False
    try:
        is_keepout_layer = (
            layer_value is not None and int(layer_value) == PcbLayer.KEEPOUT.value
        )
    except (TypeError, ValueError):
        pass
    if getattr(primitive, "is_keepout", False) and not is_keepout_layer:
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
    return kind_value in {1, 3}


def _actual_copper_layers(pcbdoc: "AltiumPcbDoc") -> tuple[PcbLayer, ...]:
    stack_layers = _stackup_copper_layers(pcbdoc)
    if stack_layers:
        return stack_layers

    layers: set[PcbLayer] = set()
    for bucket_name in ("tracks", "arcs", "fills", "regions", "shapebased_regions"):
        for primitive in getattr(pcbdoc, bucket_name, []) or []:
            layer = _primitive_layer(primitive)
            if layer is not None and layer.is_copper():
                layers.add(layer)
    if not layers:
        layers.add(PcbLayer.TOP)
        layers.add(PcbLayer.BOTTOM)
    return tuple(sorted(layers, key=lambda layer: layer.value))


def _stackup_copper_layers(pcbdoc: "AltiumPcbDoc") -> tuple[PcbLayer, ...]:
    try:
        stack = resolved_layer_stack_from_pcbdoc(pcbdoc)
    except Exception:
        return ()

    layers: list[PcbLayer] = []
    for resolved_layer in getattr(stack, "layers", []) or []:
        legacy_id = getattr(resolved_layer, "legacy_id", None)
        if legacy_id is None:
            continue
        try:
            layer = PcbLayer(int(legacy_id))
        except (TypeError, ValueError):
            continue
        if layer.is_copper() and layer not in layers:
            layers.append(layer)
    return tuple(layers)


def _coerce_layer_filter(
    layers: "Iterable[PcbLayer | int] | None",
) -> set[PcbLayer] | None:
    if layers is None:
        return None
    result: set[PcbLayer] = set()
    for layer in layers:
        result.add(PcbLayer(int(layer)))
    return result


def _include_layer(layer: PcbLayer, requested_layers: set[PcbLayer] | None) -> bool:
    return requested_layers is None or layer in requested_layers


def _dedupe_points(
    points: tuple[PcbDrawingPoint, ...],
    tol: float = 1e-9,
) -> tuple[PcbDrawingPoint, ...]:
    if not points:
        return ()
    deduped: list[PcbDrawingPoint] = [points[0]]
    for point in points[1:]:
        previous = deduped[-1]
        if (
            abs(point.x_mils - previous.x_mils) <= tol
            and abs(point.y_mils - previous.y_mils) <= tol
        ):
            continue
        deduped.append(point)
    if len(deduped) >= 2:
        first = deduped[0]
        last = deduped[-1]
        if (
            abs(first.x_mils - last.x_mils) <= tol
            and abs(first.y_mils - last.y_mils) <= tol
        ):
            deduped.pop()
    return tuple(deduped)
