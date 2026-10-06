"""Draftsman board-cache XML emitters for PCB drawing geometry."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, cast

import lxml.etree as etree

from .altium_draftsman import DraftsmanColor
from .altium_draftsman_xml import (
    DRAFTSMAN_V1_NAMESPACE,
    XML_SCHEMA_INSTANCE_NAMESPACE,
    child_text,
    children_by_local_name,
    first_child_by_local_name,
    qualified_name,
)
from .altium_pcb_drawing_geometry import (
    PcbDrawingArc,
    PcbDrawingGeometry,
    PcbDrawingPoint,
    PcbDrawingPolygon,
    PcbDrawingPrimitive,
    PcbDrawingUni,
)
from .altium_record_types import PcbLayer

if TYPE_CHECKING:
    from .altium_board import AltiumBoardOutline


DRAFTSMAN_PCB_CACHE_POINTS_PER_MIL = 0.096
SYSTEM_WINDOWS_MEDIA_NAMESPACE = (
    "http://schemas.datacontract.org/2004/07/System.Windows.Media"
)
SYSTEM_COLLECTIONS_GENERIC_NAMESPACE = (
    "http://schemas.datacontract.org/2004/07/System.Collections.Generic"
)
DRAFTSMAN_ARRAYS_NAMESPACE = "http://schemas.microsoft.com/2003/10/Serialization/Arrays"

_PRIMITIVE_TYPE_NAMES = {
    "arc": "Arc",
    "fill": "Fill",
    "pad": "Pad",
    "region": "Region",
    "track": "Track",
    "via": "Via",
}
_PRIMITIVE_TYPE_V2 = {
    "arc": 0,
    "fill": 1,
    "pad": 2,
    "region": 4,
    "track": 6,
    "via": 7,
}


@dataclass(frozen=True)
class DraftsmanPcbCachePrimitive:
    """A drawing primitive plus optional Draftsman cache style metadata."""

    primitive: PcbDrawingPrimitive
    override_color: DraftsmanColor | None = None


@dataclass(frozen=True)
class DraftsmanPcbCacheLayer:
    """One Draftsman board-cache layer to serialize."""

    layer: PcbLayer
    primitives: tuple[DraftsmanPcbCachePrimitive, ...]
    name: str | None = None
    color: DraftsmanColor | None = None
    thickness_mils: float = 0.0


@dataclass(frozen=True)
class DraftsmanPcbDisplayLayer:
    """One board assembly view displayed-layer entry."""

    layer: PcbLayer
    color: DraftsmanColor
    visible: bool = True


@dataclass(frozen=True)
class DraftsmanBoardAssemblyViewResources:
    """Document-level resource ids needed by a board assembly view."""

    title_font_style_id: int
    component_font_style_id: int
    board_shape_line_style_id: int = 1
    component_line_style_id: int = 2
    component_fill_style_id: int = 1


def draftsman_pcb_cache_points_from_mils(value_mils: float) -> float:
    """Convert PCB board-space mils to Draftsman board-cache drawing points."""

    return float(value_mils) * DRAFTSMAN_PCB_CACHE_POINTS_PER_MIL


def draftsman_color_from_hex_rgb(value: str) -> DraftsmanColor:
    """Parse a `#RRGGBB` color for Draftsman XML fields."""

    clean = value.strip()
    if clean.startswith("#"):
        clean = clean[1:]
    if len(clean) != 6:
        raise ValueError(f"expected #RRGGBB color, got {value!r}")
    return DraftsmanColor.rgb(
        int(clean[0:2], 16),
        int(clean[2:4], 16),
        int(clean[4:6], 16),
    )


def ensure_board_assembly_view_document_resources(
    document: object,
    *,
    title_font_family: str = "Arial",
    title_font_size: float = 14.0,
    component_font_family: str = "Arial",
    component_font_size: float = 8.0,
    board_shape_line_style_id: int = 1,
    component_line_style_id: int = 2,
    component_fill_style_id: int = 1,
) -> DraftsmanBoardAssemblyViewResources:
    """Ensure a blank/starter Draftsman document has board-view resource ids."""

    title_font = document.get_or_create_font_style(  # type: ignore[attr-defined]
        title_font_family,
        title_font_size,
    )
    component_font = document.get_or_create_font_style(  # type: ignore[attr-defined]
        component_font_family,
        component_font_size,
    )
    ensure_board_assembly_view_line_styles(
        document.root,  # type: ignore[attr-defined]
        board_shape_line_style_id=board_shape_line_style_id,
        component_line_style_id=component_line_style_id,
    )
    return DraftsmanBoardAssemblyViewResources(
        title_font_style_id=title_font.id,
        component_font_style_id=component_font.id,
        board_shape_line_style_id=board_shape_line_style_id,
        component_line_style_id=component_line_style_id,
        component_fill_style_id=component_fill_style_id,
    )


def ensure_board_assembly_view_line_styles(
    root: etree._Element,
    *,
    board_shape_line_style_id: int = 1,
    component_line_style_id: int = 2,
) -> None:
    """Ensure the standard board-view outline/component line styles exist."""

    line_styles = _ensure_line_styles_element(root)
    existing_ids = {
        int(style_id)
        for style in children_by_local_name(line_styles, "LineStyleData")
        if (style_id := child_text(style, "Id")) is not None
    }
    if board_shape_line_style_id not in existing_ids:
        line_styles.append(
            board_assembly_view_line_style_xml(
                style_id=board_shape_line_style_id,
                thickness=1.889763779527559,
                thickness_preset_id=2,
            )
        )
    if component_line_style_id not in existing_ids:
        line_styles.append(
            board_assembly_view_line_style_xml(
                style_id=component_line_style_id,
                thickness=0.7559055118110238,
                thickness_preset_id=1,
            )
        )


def board_assembly_view_line_style_xml(
    *,
    style_id: int,
    thickness: float,
    thickness_preset_id: int,
    color: DraftsmanColor | None = None,
) -> etree._Element:
    """Create a `LineStyleData` element for board assembly view references."""

    element = _element("LineStyleData")
    _append_color(element, "Color", color or DraftsmanColor.rgb(0, 0, 0))
    dash_pattern = _append(
        element,
        "DashPattern",
        nsmap={"a": DRAFTSMAN_ARRAYS_NAMESPACE},
    )
    dash_pattern.set(qualified_name("nil", XML_SCHEMA_INSTANCE_NAMESPACE), "true")
    _append_text(element, "DashPatternPresetId", "0")
    _append_text(element, "Id", str(style_id))
    _append_text(element, "Thickness", _format_float(thickness))
    _append_text(element, "ThicknessPresetId", str(thickness_preset_id))
    return element


def board_layers_geometry_information_xml(
    layers: Iterable[DraftsmanPcbCacheLayer],
    *,
    last_update: str = "0001-01-01T00:00:00",
) -> etree._Element:
    """Create a `BoardLayersGeometryInformation` XML subtree."""

    element = _element(
        "BoardLayersGeometryInformation",
        nsmap={None: DRAFTSMAN_V1_NAMESPACE, "i": XML_SCHEMA_INSTANCE_NAMESPACE},
    )
    _append_text(element, "Id", "0")
    _append_text(element, "LastUpdate", last_update)
    layers_geometry = _append(element, "LayersGeometry")
    for layer in layers:
        layers_geometry.append(board_layer_primitives_geometry_xml(layer))
    return element


def board_assembly_information_xml(
    pcbdoc: object,
    *,
    board_thickness_mils: float = 0.0,
    last_update: str = "0001-01-01T00:00:00",
) -> etree._Element:
    """Create a minimal `BoardAssemblyInformation` XML subtree from a PcbDoc."""

    outline = _pcbdoc_board_outline(pcbdoc)
    min_x, min_y, max_x, max_y = outline.bounding_box
    x = draftsman_pcb_cache_points_from_mils(min_x)
    y = draftsman_pcb_cache_points_from_mils(min_y)
    width = draftsman_pcb_cache_points_from_mils(max_x - min_x)
    height = draftsman_pcb_cache_points_from_mils(max_y - min_y)
    board_thickness = draftsman_pcb_cache_points_from_mils(board_thickness_mils)

    element = _element(
        "BoardAssemblyInformation",
        nsmap={None: DRAFTSMAN_V1_NAMESPACE, "i": XML_SCHEMA_INSTANCE_NAMESPACE},
    )
    _append_text(element, "Id", "0")
    _append_rect_values(element, "BoardBoundsBack", x, y, width, height)
    _append_rect_values(element, "BoardBoundsRight", x, y, width, height)
    _append_rect_values(element, "BoardBoundsTop", x, y, width, height)
    _append_board_outline(element, "BoardOutline", outline)
    _append_rect_values(element, "BoardOutlineBounds", x, y, width, height)
    _append_text(element, "BoardThickness", _format_float(board_thickness))
    _append(element, "ComponentTypes")
    _append_cutouts(element, outline)
    _append_text(element, "DefaultUiFilter", "Components")
    _append_text(element, "DefaultUiFilterV2", "0")
    _append(element, "Holes")
    _append_text(element, "LastUpdate", last_update)
    _append_nil(element, "LayersGeometry", None)
    _append_text(element, "RouteToolPathLayerId", "0")
    _append(element, "Variants")
    _append_nil(element, "WireBonding", None)
    return element


def board_projection_information_xml(
    pcbdoc: object,
    *,
    last_update: str = "0001-01-01T00:00:00",
) -> etree._Element:
    """Create a minimal non-null `BoardProjectionInformation` subtree."""

    outline = _pcbdoc_board_outline(pcbdoc)
    min_x, min_y, max_x, _max_y = outline.bounding_box
    origin_x = draftsman_pcb_cache_points_from_mils((min_x + max_x) / 2.0)
    origin_y = draftsman_pcb_cache_points_from_mils(min_y)

    element = _element(
        "BoardProjectionInformation",
        nsmap={None: DRAFTSMAN_V1_NAMESPACE, "i": XML_SCHEMA_INSTANCE_NAMESPACE},
    )
    _append_raw_point(element, "BoardOrigin", origin_x, origin_y)
    _append(element, "ComponentProjections")
    _append_text(element, "LastUpdate", last_update)
    _append_nil(element, "MbaProjections", None)
    _append(
        element,
        "ProjectionData",
        nsmap={"a": SYSTEM_COLLECTIONS_GENERIC_NAMESPACE},
    )
    _append(element, "RealisticBitmaps")
    return element


def board_assembly_view_item_xml(
    *,
    item_id: int,
    board_source_name: str,
    layer: PcbLayer,
    layer_color: DraftsmanColor,
    location_x_points: float,
    location_y_points: float,
    title: str,
    title_font_style_id: int = 1,
    component_font_style_id: int = 1,
    board_shape_line_style_id: int = 1,
    component_line_style_id: int = 2,
    component_fill_style_id: int = 1,
    showed_layer_visible: bool = True,
    extra_showed_layers: Iterable[DraftsmanPcbDisplayLayer] = (),
    mask_color: DraftsmanColor | None = None,
    show_mask: bool = False,
    paste_color: DraftsmanColor | None = None,
    show_paste: bool = False,
    smd_pads_color: DraftsmanColor | None = None,
    show_smd_pads: bool = False,
    through_hole_pads_color: DraftsmanColor | None = None,
    show_through_hole_pads: bool = False,
    show_topology: bool = True,
    scale_numenator: float = 1.0,
    scale_denominator: float = 1.0,
    primary_showed_layer_first: bool = True,
) -> etree._Element:
    """Create a minimal `BoardAssemblyViewData` page item."""

    side_name = "Bottom" if layer == PcbLayer.BOTTOM else "Top"
    side_v2 = 1 if side_name == "Bottom" else 0

    element = _element("Item")
    element.set(
        qualified_name("type", XML_SCHEMA_INSTANCE_NAMESPACE), "BoardAssemblyViewData"
    )
    _append_text(element, "Id", str(item_id))
    _append_text(element, "Anchor", "Top Left")
    _append_text(element, "OriginatedFromTemplate", "false")
    element_items = _append(element, "ElementItems")
    element_items.append(
        linked_title_element_item_xml(title, font_style_id=title_font_style_id)
    )
    _append_text(element, "BoardSourceName", board_source_name)
    _append_text(element, "CustomScale", "1")
    _append_text(element, "HorizontalAlignment", "0")
    _append_raw_point(element, "Location", location_x_points, location_y_points)
    _append_text(element, "Rotation", "Ccw0")
    _append_text(element, "RotationV2", "0")
    _append_scale(element, "Scale", scale_numenator, scale_denominator)
    _append_text(element, "ShowTitle", "true")
    _append_size_values(element, "Size", 0.0, 0.0)
    _append_text(element, "Title", title)
    _append_color(element, "TitleColor", DraftsmanColor.rgb(0, 0, 0))
    _append_text(element, "TitleFontStyleId", str(title_font_style_id))
    _append_text(element, "UseCustomScale", "false")
    _append_text(element, "UseDocumentFontForTitle", "true")
    _append_text(element, "VerticalAlignment", "2")
    _append_text(element, "BoardShapeLineStyleId", str(board_shape_line_style_id))
    _append_text(element, "BondWiresDisplayMode", "1")
    _append(element, "ComponentDisplaySettings")
    _append_text(element, "ComponentLineStyleId", str(component_line_style_id))
    element.append(
        default_component_display_settings_xml(
            font_style_id=component_font_style_id,
            fill_style_id=component_fill_style_id,
        )
    )
    _append_text(element, "HoleDisplayMode", "MinimumDiameter")
    _append_text(element, "MinimumHoleDiameter", "0")
    _append_nil(element, "ProjectionInformation", None)
    _append_text(element, "ShowNoBom", "false")
    _append_text(element, "ShowSilkScreen", "false")
    showed_layers = _append(element, "ShowedLayers")
    primary_layer = DraftsmanPcbDisplayLayer(
        layer=layer,
        color=layer_color,
        visible=showed_layer_visible,
    )
    display_layers = tuple(extra_showed_layers)
    if primary_showed_layer_first:
        display_layers = (primary_layer,) + display_layers
    else:
        display_layers = display_layers + (primary_layer,)
    for display_layer in display_layers:
        _append_layer_display_data(
            showed_layers,
            display_layer.layer,
            display_layer.color,
            visible=display_layer.visible,
        )
    _append_wpf_color(element, "SilkScreenColor", DraftsmanColor.rgb(255, 255, 0))
    _append_text(element, "SilkScreenDisplayMode", "None")
    _append_text(element, "UseProjection", "false")
    element.append(
        view_component_display_settings_xml(
            topology_color=layer_color,
            font_style_id=component_font_style_id,
            mask_color=mask_color,
            show_mask=show_mask,
            paste_color=paste_color,
            show_paste=show_paste,
            smd_pads_color=smd_pads_color,
            show_smd_pads=show_smd_pads,
            through_hole_pads_color=through_hole_pads_color,
            show_through_hole_pads=show_through_hole_pads,
            show_topology=show_topology,
        )
    )
    _append_text(element, "ViewSide", side_name)
    _append_text(element, "ViewSideV2", str(side_v2))
    return element


def linked_title_element_item_xml(
    title: str,
    *,
    font_style_id: int = 1,
) -> etree._Element:
    """Create the linked title element used inside a board assembly view."""

    element = _element("ElementItemData")
    element.set(
        qualified_name("type", XML_SCHEMA_INSTANCE_NAMESPACE),
        "LinkedTextDrawingItemData",
    )
    _append_text(element, "Name", "Title")
    _append_color(element, "Color", DraftsmanColor.rgb(0, 0, 0))
    _append_text(element, "FontStyleId", str(font_style_id))
    _append_text(element, "HorizontalAlignment", "Left")
    _append_text(element, "HorizontalAlignmentV2", "0")
    _append_text(element, "IsShow", "true")
    _append_raw_point(element, "ManualOffset", 0.0, 0.0)
    _append_text(element, "ManualPositioning", "false")
    _append_text(element, "Scaled", "false")
    _append_text(element, "Title", title)
    _append_text(element, "UseDocumentFont", "true")
    _append_text(element, "VerticalAlignment", "Top")
    _append_text(element, "VerticalAlignmentV2", "0")
    return element


def default_component_display_settings_xml(
    *,
    font_style_id: int = 1,
    fill_style_id: int = 1,
) -> etree._Element:
    """Create the default component display settings used by assembly views."""

    element = _element("DefaultComponentDisplaySettings")
    _append_wpf_color(element, "Color", DraftsmanColor.rgb(0, 0, 0))
    _append(element, "ComponentDesignator")
    _append_text(element, "ComponentGeometrySource", "Default")
    _append(element, "Designator")
    _append_wpf_color(element, "DesignatorColor", DraftsmanColor.rgb(0, 0, 0))
    _append_text(element, "DesignatorLocation", "CenterFit")
    _append_text(element, "DissectedFillingStyleId", str(fill_style_id))
    _append_text(element, "FontStyleId", str(font_style_id))
    _append_raw_point(element, "ManualOffset", 0.0, 0.0)
    _append_text(element, "ManualRotation", "0")
    _append_raw_point(element, "ManualScale", 0.0, 0.0)
    _append(element, "ParameterInsertions")
    _append_text(element, "ReferenceMarkerDisplayMode", "None")
    _append_text(element, "ShowComponent", "true")
    _append_text(element, "ShowDesignator", "false")
    _append_text(element, "TextOrientation", "AlongTheComponent")
    _append(element, "UniqueId")
    return element


def view_component_display_settings_xml(
    *,
    topology_color: DraftsmanColor,
    font_style_id: int = 1,
    mask_color: DraftsmanColor | None = None,
    show_mask: bool = False,
    paste_color: DraftsmanColor | None = None,
    show_paste: bool = False,
    smd_pads_color: DraftsmanColor | None = None,
    show_smd_pads: bool = False,
    through_hole_pads_color: DraftsmanColor | None = None,
    show_through_hole_pads: bool = False,
    show_topology: bool = True,
) -> etree._Element:
    """Create the `ViewComponentDisplaySettingsData` XML subtree."""

    element = _element("ViewComponentDisplaySettingsData")
    _append_text(element, "ComponentCaptionSource", "Designator")
    _append_text(element, "FontStyleId", str(font_style_id))
    _append_wpf_color(
        element,
        "MaskaColor",
        mask_color or DraftsmanColor.rgb(128, 0, 128),
    )
    _append_text(element, "MaxAutoFitFontSize", "12")
    _append_text(element, "MinAutoFitFontSize", "5")
    _append_wpf_color(
        element,
        "PastaColor",
        paste_color or DraftsmanColor.rgb(128, 128, 128),
    )
    _append_text(element, "ShowMaska", _bool_text(show_mask))
    _append_text(element, "ShowPasta", _bool_text(show_paste))
    _append_text(element, "ShowSmdPads", _bool_text(show_smd_pads))
    _append_text(element, "ShowThroughHolePads", _bool_text(show_through_hole_pads))
    _append_text(element, "ShowTopology", _bool_text(show_topology))
    _append_wpf_color(
        element,
        "SmdPadsColor",
        smd_pads_color or DraftsmanColor.rgb(255, 0, 0),
    )
    _append_wpf_color(
        element,
        "ThroughHolePadsColor",
        through_hole_pads_color or DraftsmanColor.rgb(146, 208, 80),
    )
    _append_wpf_color(element, "TopologyColor", topology_color)
    _append_text(element, "UseDocumentFontForDesignators", "true")
    _append_text(element, "Variant", "Document")
    return element


def board_layer_primitives_geometry_xml(
    layer: DraftsmanPcbCacheLayer,
) -> etree._Element:
    """Create a `BoardLayerPrimitivesGeometry` XML subtree."""

    element = _element("BoardLayerPrimitivesGeometry")
    element.append(_board_layer_xml(layer))
    primitives_element = _append(element, "Primitives")
    for cache_primitive in layer.primitives:
        primitives_element.append(
            primitive_to_draftsman_cache_xml(
                cache_primitive.primitive,
                override_color=cache_primitive.override_color,
            )
        )
    return element


def primitive_to_draftsman_cache_xml(
    primitive: PcbDrawingPrimitive,
    *,
    override_color: DraftsmanColor | None = None,
) -> etree._Element:
    """Create a Draftsman `Primitive` XML element from one drawing primitive."""

    primitive_type = _primitive_type_name(primitive.primitive_kind)
    element = _element("Primitive")
    board_hole_items = _append(element, "BoardHoleItems")
    _append_nil(element, "ComponentDesignator", primitive.component_designator)
    _append_nil(element, "ComponentFootprint", None)
    copper_cutout_items = _append(element, "CopperCutoutItems")
    copper_items = _append(element, "CopperItems")

    item = _primitive_item_xml(primitive.geometry)
    if primitive.role == "boardhole":
        board_hole_items.append(item)
    elif primitive.role == "coppercutout":
        copper_cutout_items.append(item)
    else:
        copper_items.append(item)

    _append_text(
        element,
        "InComponent",
        "true" if primitive.component_designator else "false",
    )
    if override_color is None:
        _append_nil(element, "OverrideColor", None)
    else:
        color = _append(element, "OverrideColor")
        _append_color_channels(color, override_color)
    _append_text(element, "PrimitiveType", primitive_type)
    _append_text(element, "PrimitiveTypeV2", str(_primitive_type_v2(primitive)))
    return element


def _board_layer_xml(layer: DraftsmanPcbCacheLayer) -> etree._Element:
    element = _element("Layer")
    _append_color(
        element,
        "Color",
        layer.color or draftsman_color_from_hex_rgb(layer.layer.default_color),
    )
    _append_text(element, "IsElectrical", _bool_text(layer.layer.is_copper()))
    _append_text(element, "IsMechanical", _bool_text(layer.layer.is_mechanical()))
    _append_text(element, "IsOverlay", _bool_text(_is_overlay_layer(layer.layer)))
    _append_text(element, "IsPasteMask", _bool_text(_is_paste_mask_layer(layer.layer)))
    _append_text(
        element,
        "IsSolderMask",
        _bool_text(_is_solder_mask_layer(layer.layer)),
    )
    _append_text(element, "MaskInverted", "false")
    _append_text(element, "Name", layer.name or layer.layer.to_display_name())
    _append_text(
        element,
        "Thickness",
        _format_float(draftsman_pcb_cache_points_from_mils(layer.thickness_mils)),
    )
    _append_text(element, "V6LayerId", str(layer.layer.value))
    _append_text(element, "V7LayerId", str(layer.layer.value))
    return element


def _primitive_item_xml(geometry: PcbDrawingGeometry) -> etree._Element:
    element = _element("PrimitiveItem")
    arcs = _append(element, "Arcs")
    for arc in geometry.arcs:
        arcs.append(_arc_xml(arc))
    _append_nil(element, "CounterHoles", None)
    polygons = _append(element, "Polygons")
    for polygon in geometry.polygons:
        polygons.append(_polygon_xml(polygon))
    unis = _append(element, "Unis")
    for uni in geometry.unis:
        unis.append(_uni_xml(uni))
    return element


def _uni_xml(uni: PcbDrawingUni) -> etree._Element:
    element = _element("Uni")
    _append_vector(element, "Direction", uni.direction.x, uni.direction.y)
    _append_point(element, "Location", uni.location)
    _append_text(
        element,
        "Radius",
        _format_float(draftsman_pcb_cache_points_from_mils(uni.radius_mils)),
    )
    size = _append(element, "Size")
    _append_text(
        size,
        "Height",
        _format_float(draftsman_pcb_cache_points_from_mils(uni.size.height_mils)),
    )
    _append_text(
        size,
        "Width",
        _format_float(draftsman_pcb_cache_points_from_mils(uni.size.width_mils)),
    )
    return element


def _arc_xml(arc: PcbDrawingArc) -> etree._Element:
    element = _element("Arc")
    start_angle, end_angle = _draftsman_arc_angles(arc)
    _append_point(element, "Center", arc.center)
    _append_text(element, "EndAngle", _format_float(end_angle))
    _append_text(
        element,
        "Radius",
        _format_float(draftsman_pcb_cache_points_from_mils(arc.radius_mils)),
    )
    _append_text(element, "StartAngle", _format_float(start_angle))
    _append_text(
        element,
        "Thickness",
        _format_float(draftsman_pcb_cache_points_from_mils(arc.thickness_mils)),
    )
    return element


def _draftsman_arc_angles(arc: PcbDrawingArc) -> tuple[float, float]:
    start = _normalize_degrees(arc.start_angle_degrees)
    end = _normalize_degrees(arc.end_angle_degrees)
    raw_delta = arc.end_angle_degrees - arc.start_angle_degrees
    sweep = raw_delta % 360.0

    if math.isclose(sweep, 0.0, abs_tol=1e-9):
        if math.isclose(raw_delta, 0.0, abs_tol=1e-9):
            return (start, end)
        return (start, start + 360.0)

    if end < start:
        end += 360.0
    return (start, end)


def _normalize_degrees(value: float) -> float:
    normalized = float(value) % 360.0
    if math.isclose(normalized, 360.0, abs_tol=1e-9):
        return 0.0
    return 0.0 if math.isclose(normalized, 0.0, abs_tol=1e-9) else normalized


def _polygon_xml(polygon: PcbDrawingPolygon) -> etree._Element:
    element = _element("Poly")
    contours = _append(element, "Contours")
    for contour in polygon.contours:
        contour_element = _append(contours, "ArrayOfPointData")
        for point in contour:
            point_element = _append(contour_element, "PointData")
            _append_text(
                point_element,
                "X",
                _format_float(draftsman_pcb_cache_points_from_mils(point.x_mils)),
            )
            _append_text(
                point_element,
                "Y",
                _format_float(draftsman_pcb_cache_points_from_mils(point.y_mils)),
            )
    return element


def _append_board_outline(
    parent: etree._Element,
    local_name: str,
    outline: object,
) -> etree._Element:
    element = _append(parent, local_name)
    segments = _append(element, "Segments")
    for vertex in getattr(outline, "vertices", ()) or ():
        segments.append(_outline_segment_xml(vertex))
    return element


def _append_cutouts(parent: etree._Element, outline: object) -> etree._Element:
    element = _append(parent, "Cutouts")
    for cutout in getattr(outline, "cutouts", ()) or ():
        _append_board_outline(element, "Contour", cutout)
    return element


def _outline_segment_xml(vertex: object) -> etree._Element:
    element = _element("Segment")
    is_arc = bool(getattr(vertex, "is_arc", False))
    _append_text(
        element, "Angle1", _format_float(getattr(vertex, "start_angle_deg", 0.0) or 0.0)
    )
    _append_text(
        element, "Angle2", _format_float(getattr(vertex, "end_angle_deg", 0.0) or 0.0)
    )
    _append_raw_point(
        element,
        "Center",
        draftsman_pcb_cache_points_from_mils(
            float(getattr(vertex, "center_x_mils", 0.0) or 0.0)
        ),
        draftsman_pcb_cache_points_from_mils(
            float(getattr(vertex, "center_y_mils", 0.0) or 0.0)
        ),
    )
    _append_text(element, "Kind", "Arc" if is_arc else "Line")
    _append_text(element, "KindV2", "1" if is_arc else "0")
    _append_raw_point(
        element,
        "Point",
        draftsman_pcb_cache_points_from_mils(
            float(getattr(vertex, "x_mils", 0.0) or 0.0)
        ),
        draftsman_pcb_cache_points_from_mils(
            float(getattr(vertex, "y_mils", 0.0) or 0.0)
        ),
    )
    _append_text(
        element,
        "Radius",
        _format_float(
            draftsman_pcb_cache_points_from_mils(
                float(getattr(vertex, "radius_mils", 0.0) or 0.0)
            )
        ),
    )
    return element


def _append_point(
    parent: etree._Element,
    local_name: str,
    point: PcbDrawingPoint,
) -> etree._Element:
    element = _append(parent, local_name)
    _append_text(
        element,
        "X",
        _format_float(draftsman_pcb_cache_points_from_mils(point.x_mils)),
    )
    _append_text(
        element,
        "Y",
        _format_float(draftsman_pcb_cache_points_from_mils(point.y_mils)),
    )
    return element


def _append_raw_point(
    parent: etree._Element,
    local_name: str,
    x: float,
    y: float,
) -> etree._Element:
    element = _append(parent, local_name)
    _append_text(element, "X", _format_float(x))
    _append_text(element, "Y", _format_float(y))
    return element


def _append_vector(
    parent: etree._Element,
    local_name: str,
    x: float,
    y: float,
) -> etree._Element:
    return _append_raw_point(parent, local_name, x, y)


def _append_rect_values(
    parent: etree._Element,
    local_name: str,
    x: float,
    y: float,
    width: float,
    height: float,
) -> etree._Element:
    element = _append(parent, local_name)
    _append_text(element, "Height", _format_float(height))
    _append_text(element, "Width", _format_float(width))
    _append_text(element, "X", _format_float(x))
    _append_text(element, "Y", _format_float(y))
    return element


def _append_size_values(
    parent: etree._Element,
    local_name: str,
    width: float,
    height: float,
) -> etree._Element:
    element = _append(parent, local_name)
    _append_text(element, "Height", _format_float(height))
    _append_text(element, "Width", _format_float(width))
    return element


def _append_scale(
    parent: etree._Element,
    local_name: str,
    numenator: float,
    denominator: float,
) -> etree._Element:
    element = _append(parent, local_name)
    _append_text(element, "Denominator", _format_float(denominator))
    _append_text(element, "Numenator", _format_float(numenator))
    return element


def _append_color(
    parent: etree._Element,
    local_name: str,
    color: DraftsmanColor,
) -> etree._Element:
    element = _append(parent, local_name)
    _append_color_channels(element, color)
    return element


def _append_wpf_color(
    parent: etree._Element,
    local_name: str,
    color: DraftsmanColor,
) -> etree._Element:
    element = _append(parent, local_name)
    for child_local_name, value in (
        ("A", color.a),
        ("B", color.b),
        ("G", color.g),
        ("R", color.r),
    ):
        child = etree.SubElement(
            element,
            f"{{{SYSTEM_WINDOWS_MEDIA_NAMESPACE}}}{child_local_name}",
            nsmap={"a": SYSTEM_WINDOWS_MEDIA_NAMESPACE},
        )
        child.text = str(value)
    for child_local_name, value in (
        ("ScA", color.a / 255.0),
        ("ScB", color.b / 255.0),
        ("ScG", color.g / 255.0),
        ("ScR", color.r / 255.0),
    ):
        child = etree.SubElement(
            element,
            f"{{{SYSTEM_WINDOWS_MEDIA_NAMESPACE}}}{child_local_name}",
            nsmap={"a": SYSTEM_WINDOWS_MEDIA_NAMESPACE},
        )
        child.text = _format_float(value)
    return element


def _append_layer_display_data(
    parent: etree._Element,
    layer: PcbLayer,
    color: DraftsmanColor,
    *,
    visible: bool,
) -> etree._Element:
    element = _append(parent, "BoardLayerDisplayData")
    _append_color(element, "Color", color)
    _append_text(element, "V7LayerId", str(layer.value))
    _append_text(element, "Visible", _bool_text(visible))
    return element


def _append_color_channels(element: etree._Element, color: DraftsmanColor) -> None:
    for local_name, value in (
        ("A", color.a),
        ("B", color.b),
        ("G", color.g),
        ("R", color.r),
    ):
        _append_text(element, local_name, str(value))


def _append_nil(
    parent: etree._Element,
    local_name: str,
    value: str | None,
) -> etree._Element:
    element = _append(parent, local_name)
    if value is None:
        element.set(qualified_name("nil", XML_SCHEMA_INSTANCE_NAMESPACE), "true")
    else:
        element.text = value
    return element


def _append_text(
    parent: etree._Element,
    local_name: str,
    value: str,
) -> etree._Element:
    element = _append(parent, local_name)
    element.text = value
    return element


def _append(
    parent: etree._Element,
    local_name: str,
    *,
    nsmap: dict[str | None, str] | None = None,
) -> etree._Element:
    return etree.SubElement(parent, qualified_name(local_name), nsmap=nsmap)


def _element(
    local_name: str,
    *,
    nsmap: dict[str | None, str] | None = None,
) -> etree._Element:
    return etree.Element(qualified_name(local_name), nsmap=nsmap)


def _ensure_line_styles_element(root: etree._Element) -> etree._Element:
    line_styles = first_child_by_local_name(root, "LineStyles")
    if line_styles is not None:
        return line_styles

    line_styles = _element("LineStyles")
    font_styles = first_child_by_local_name(root, "FontStyles")
    if font_styles is not None:
        root.insert(root.index(font_styles) + 1, line_styles)
    else:
        root.append(line_styles)
    return line_styles


def _pcbdoc_board_outline(pcbdoc: object) -> "AltiumBoardOutline":
    board = getattr(pcbdoc, "board", None)
    outline = getattr(board, "outline", None)
    if outline is None or not getattr(outline, "vertices", None):
        raise ValueError("PcbDoc does not contain a board outline")
    return cast("AltiumBoardOutline", outline)


def _primitive_type_name(primitive_kind: str) -> str:
    try:
        return _PRIMITIVE_TYPE_NAMES[primitive_kind]
    except KeyError as exc:
        raise ValueError(
            f"unsupported PCB drawing primitive kind: {primitive_kind}"
        ) from exc


def _primitive_type_v2(primitive: PcbDrawingPrimitive) -> int:
    try:
        return _PRIMITIVE_TYPE_V2[primitive.primitive_kind]
    except KeyError as exc:
        raise ValueError(
            f"unsupported PCB drawing primitive kind: {primitive.primitive_kind}"
        ) from exc


def _is_overlay_layer(layer: PcbLayer) -> bool:
    return layer in {PcbLayer.TOP_OVERLAY, PcbLayer.BOTTOM_OVERLAY}


def _is_paste_mask_layer(layer: PcbLayer) -> bool:
    return layer in {PcbLayer.TOP_PASTE, PcbLayer.BOTTOM_PASTE}


def _is_solder_mask_layer(layer: PcbLayer) -> bool:
    return layer in {PcbLayer.TOP_SOLDER, PcbLayer.BOTTOM_SOLDER}


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _format_float(value: float) -> str:
    numeric = float(value)
    return f"{numeric:.15g}"
