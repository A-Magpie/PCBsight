"""Shared V7-aware SVG layer helpers for PCB record primitives."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .altium_pcb_layer_ref import PcbLayerRef, PcbLayerResolutionError
from .altium_record_types import PcbLayer

if TYPE_CHECKING:
    from .altium_pcb_layer_ref import PcbPrimitiveLayerState
    from .altium_pcb_svg_renderer import PcbSvgRenderContext


class SvgLayeredPrimitive(Protocol):
    """Primitive that stores a legacy layer byte and resolves V7 layer state."""

    layer: int

    def layer_state(self) -> "PcbPrimitiveLayerState": ...


def svg_layer_ref(primitive: SvgLayeredPrimitive) -> PcbLayerRef | None:
    """Return the primitive's V7-aware layer ref, or None when unresolvable."""

    try:
        return primitive.layer_state().ref
    except PcbLayerResolutionError:
        return None


def svg_requested_layer_ref(
    for_layer: PcbLayer | PcbLayerRef,
) -> PcbLayerRef | None:
    """Coerce an SVG layer filter to a ref, or None for unmapped legacy ids."""

    if isinstance(for_layer, PcbLayerRef):
        return for_layer
    try:
        return PcbLayerRef.from_legacy(for_layer)
    except PcbLayerResolutionError:
        return None


def svg_matches_requested_layer(
    primitive: SvgLayeredPrimitive,
    for_layer: PcbLayer | PcbLayerRef | None,
    layer_ref: PcbLayerRef | None,
) -> bool:
    """Return True when the primitive should render for the requested layer."""

    if for_layer is None:
        return True
    if isinstance(for_layer, PcbLayerRef):
        return layer_ref == for_layer
    requested_ref = svg_requested_layer_ref(for_layer)
    if requested_ref is not None:
        return layer_ref == requested_ref
    return int(primitive.layer) == int(for_layer)


def legacy_svg_color(
    primitive: SvgLayeredPrimitive,
    ctx: "PcbSvgRenderContext",
) -> str:
    """Return the legacy layer color, or neutral gray for unknown layers."""

    try:
        return ctx.layer_color(PcbLayer(int(primitive.layer)))
    except ValueError:
        return "#808080"


def svg_fill_color(
    primitive: SvgLayeredPrimitive,
    ctx: "PcbSvgRenderContext",
    layer_ref: PcbLayerRef | None,
) -> str:
    """Return the fill color from the V7 ref when known, else legacy color."""

    if layer_ref is not None:
        return ctx.layer_ref_color(layer_ref)
    return legacy_svg_color(primitive, ctx)
