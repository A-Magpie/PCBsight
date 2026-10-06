"""Shared PCB pad authoring helpers."""

from __future__ import annotations

from .altium_pcb_enums import PadHoleShape, PadShape
from .altium_record_pcb__pad import AltiumPcbPad
from .altium_record_types import PcbLayer

ROUNDED_RECTANGLE_ALT_SHAPE = 9
ROUNDED_RECTANGLE_FULL_STACK_LAYER_CODE = 4
ROUNDED_RECTANGLE_FULL_STACK_MODE_FLAGS = 0x0180
ROUNDED_RECTANGLE_FULL_STACK_SHAPE_VALUE = 9
DEFAULT_ROUNDED_RECTANGLE_CORNER_RADIUS_PERCENT = 50
ROUND_HOLE_SHAPE = int(PadHoleShape.ROUND)
SQUARE_HOLE_SHAPE = int(PadHoleShape.SQUARE)
SLOT_HOLE_SHAPE = 2
PadHoleShapeInput = PadHoleShape | int | str
PadShapeInput = PadShape | int | str
OptionalPadShapeInput = PadShapeInput | None


def validate_non_negative(value: float, name: str) -> None:
    if float(value) < 0.0:
        raise ValueError(f"{name} must be non-negative")


def normalize_pad_hole_shape(value: PadHoleShapeInput) -> PadHoleShape:
    """
    Normalize public pad hole-shape input.
    """
    if isinstance(value, PadHoleShape):
        return value
    if isinstance(value, int):
        try:
            return PadHoleShape(value)
        except ValueError as exc:
            raise ValueError("hole_shape must be round, square, or slot") from exc

    normalized = str(value).strip().lower().replace("_", "-")
    aliases = {
        "0": PadHoleShape.ROUND,
        "round": PadHoleShape.ROUND,
        "circular": PadHoleShape.ROUND,
        "circle": PadHoleShape.ROUND,
        "1": PadHoleShape.SQUARE,
        "square": PadHoleShape.SQUARE,
        "2": PadHoleShape.SLOT,
        "slot": PadHoleShape.SLOT,
        "slotted": PadHoleShape.SLOT,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError("hole_shape must be round, square, or slot") from exc


def normalize_pad_shape(value: PadShapeInput) -> PadShape:
    """
    Normalize public pad shape input.
    """
    if isinstance(value, PadShape):
        return value
    if isinstance(value, int):
        try:
            return PadShape(value)
        except ValueError as exc:
            raise ValueError(
                "shape must be circle, rectangle, octagonal, rounded_rectangle, or custom"
            ) from exc

    normalized = str(value).strip().lower().replace("_", "-").replace(" ", "-")
    aliases = {
        "1": PadShape.CIRCLE,
        "circle": PadShape.CIRCLE,
        "round": PadShape.CIRCLE,
        "circular": PadShape.CIRCLE,
        "2": PadShape.RECTANGLE,
        "rect": PadShape.RECTANGLE,
        "rectangle": PadShape.RECTANGLE,
        "3": PadShape.OCTAGONAL,
        "octagon": PadShape.OCTAGONAL,
        "octagonal": PadShape.OCTAGONAL,
        "4": PadShape.ROUNDED_RECTANGLE,
        "rounded-rect": PadShape.ROUNDED_RECTANGLE,
        "rounded-rectangle": PadShape.ROUNDED_RECTANGLE,
        "10": PadShape.CUSTOM,
        "custom": PadShape.CUSTOM,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(
            "shape must be circle, rectangle, octagonal, rounded_rectangle, or custom"
        ) from exc


def apply_authored_pad_shape(
    pad: AltiumPcbPad,
    *,
    shape: PadShapeInput,
    width_iu: int,
    height_iu: int,
    corner_radius_percent: int | float | None,
) -> None:
    """
    Apply public semantic pad shape to native fields.

    Altium does not persist authored rounded rectangles as base shape `4`.
    It uses a round base shape plus SubRecord 6 alternate-shape and radius
    data. Keeping that encoding here prevents public API callers from writing
    files that reopen with invalid pad-shape state.
    """
    shape_id = int(normalize_pad_shape(shape))
    if shape_id != int(PadShape.ROUNDED_RECTANGLE):
        pad.shape = shape_id
        pad.top_shape = shape_id
        pad.mid_shape = shape_id
        pad.bot_shape = shape_id
        return

    corner_pct, exact_pct = _normalize_corner_radius_percent(corner_radius_percent)
    if exact_pct is not None:
        pad.exact_corner_radius_percent_by_layer = {
            _exact_corner_layer_token(pad): exact_pct
        }
    base_shape = int(PadShape.CIRCLE)
    pad.shape = base_shape
    pad.top_shape = base_shape
    pad.mid_shape = base_shape
    pad.bot_shape = base_shape
    pad.inner_size_x = [width_iu] * 29
    pad.inner_size_y = [height_iu] * 29
    pad.inner_shape = [base_shape] * 29
    pad.alt_shape = [ROUNDED_RECTANGLE_ALT_SHAPE] * 32
    pad.corner_radius = [corner_pct] * 32
    pad.full_stack_layer_entries = [
        (
            ROUNDED_RECTANGLE_FULL_STACK_LAYER_CODE,
            ROUNDED_RECTANGLE_FULL_STACK_MODE_FLAGS,
            ROUNDED_RECTANGLE_FULL_STACK_SHAPE_VALUE,
            width_iu,
            height_iu,
            corner_pct,
        )
    ]


def apply_authored_pad_local_stack(
    pad: AltiumPcbPad,
    *,
    base_shape: PadShapeInput,
    base_width_iu: int,
    base_height_iu: int,
    top_shape: OptionalPadShapeInput = None,
    top_width_iu: int | None = None,
    top_height_iu: int | None = None,
    mid_shape: OptionalPadShapeInput = None,
    mid_width_iu: int | None = None,
    mid_height_iu: int | None = None,
    bottom_shape: OptionalPadShapeInput = None,
    bottom_width_iu: int | None = None,
    bottom_height_iu: int | None = None,
    pad_mode: int | None = None,
    corner_radius_percent: int | float | None = None,
) -> None:
    """
    Apply public top/mid/bottom pad-body options to native local-stack fields.

    Altium's local-stack mode stores the top, middle, and bottom pad body
    geometry in SubRecord 5, with inner-layer body arrays in SubRecord 6. The
    public API keeps the simpler single-body pad path untouched and only enters
    this mode when a caller supplies a layer-specific override or explicitly
    requests `pad_mode=1`.
    """
    local_stack_values = (
        top_shape,
        top_width_iu,
        top_height_iu,
        mid_shape,
        mid_width_iu,
        mid_height_iu,
        bottom_shape,
        bottom_width_iu,
        bottom_height_iu,
    )
    has_local_stack_override = any(value is not None for value in local_stack_values)
    if pad_mode is None and not has_local_stack_override:
        return

    resolved_mode = 1 if pad_mode is None else int(pad_mode)
    if resolved_mode not in (0, 1):
        raise ValueError("pad_mode must be 0 or 1")
    if resolved_mode == 0:
        if has_local_stack_override:
            raise ValueError("top/mid/bottom pad body overrides require pad_mode=1")
        return

    base = normalize_pad_shape(base_shape)
    if base == PadShape.CUSTOM:
        raise ValueError("local-stack pad body overrides do not support custom shape")

    corner_pct, exact_pct = _normalize_corner_radius_percent(corner_radius_percent)
    if exact_pct is not None:
        # Native CornerRadiusChamfer evidence covers simple single-body pads
        # only; local-stack fractional storage is unverified.
        raise ValueError(
            "fractional corner_radius_percent is not supported with "
            "top/mid/bottom pad body overrides"
        )
    pad.pad_mode = 1

    top_body = _resolve_layer_body(
        shape=top_shape,
        width_iu=top_width_iu,
        height_iu=top_height_iu,
        fallback_shape=base,
        fallback_width_iu=base_width_iu,
        fallback_height_iu=base_height_iu,
        label="top",
    )
    mid_body = _resolve_layer_body(
        shape=mid_shape,
        width_iu=mid_width_iu,
        height_iu=mid_height_iu,
        fallback_shape=base,
        fallback_width_iu=base_width_iu,
        fallback_height_iu=base_height_iu,
        label="mid",
    )
    bottom_body = _resolve_layer_body(
        shape=bottom_shape,
        width_iu=bottom_width_iu,
        height_iu=bottom_height_iu,
        fallback_shape=base,
        fallback_width_iu=base_width_iu,
        fallback_height_iu=base_height_iu,
        label="bottom",
    )

    pad.top_width = top_body.width_iu
    pad.top_height = top_body.height_iu
    pad.mid_width = mid_body.width_iu
    pad.mid_height = mid_body.height_iu
    pad.bot_width = bottom_body.width_iu
    pad.bot_height = bottom_body.height_iu

    pad.top_shape = _native_layer_shape(top_body.shape)
    pad.mid_shape = _native_layer_shape(mid_body.shape)
    pad.bot_shape = _native_layer_shape(bottom_body.shape)
    pad.inner_size_x = [mid_body.width_iu] * 29
    pad.inner_size_y = [mid_body.height_iu] * 29
    pad.inner_shape = [pad.mid_shape] * 29

    if has_local_stack_override:
        pad.alt_shape = [0] * 32
        pad.corner_radius = [0] * 32
        pad.full_stack_layer_entries = []

    _apply_rounded_layer_markers(pad, top_body.shape, (0,), corner_pct)
    _apply_rounded_layer_markers(pad, mid_body.shape, range(1, 30), corner_pct)
    _apply_rounded_layer_markers(pad, bottom_body.shape, (31,), corner_pct)


class _PadLayerBody:
    def __init__(self, shape: PadShape, width_iu: int, height_iu: int) -> None:
        self.shape = shape
        self.width_iu = width_iu
        self.height_iu = height_iu


def _resolve_layer_body(
    *,
    shape: OptionalPadShapeInput,
    width_iu: int | None,
    height_iu: int | None,
    fallback_shape: PadShape,
    fallback_width_iu: int,
    fallback_height_iu: int,
    label: str,
) -> _PadLayerBody:
    resolved_shape = fallback_shape if shape is None else normalize_pad_shape(shape)
    if resolved_shape == PadShape.CUSTOM:
        raise ValueError(f"{label}_shape does not support custom shape")
    resolved_width = fallback_width_iu if width_iu is None else int(width_iu)
    resolved_height = fallback_height_iu if height_iu is None else int(height_iu)
    if resolved_width <= 0:
        raise ValueError(f"{label}_width_mils must be positive")
    if resolved_height <= 0:
        raise ValueError(f"{label}_height_mils must be positive")
    return _PadLayerBody(resolved_shape, resolved_width, resolved_height)


def _native_layer_shape(shape: PadShape) -> int:
    if shape == PadShape.ROUNDED_RECTANGLE:
        return int(PadShape.CIRCLE)
    return int(shape)


def _apply_rounded_layer_markers(
    pad: AltiumPcbPad,
    shape: PadShape,
    layer_indices: range | tuple[int, ...],
    corner_pct: int,
) -> None:
    if shape != PadShape.ROUNDED_RECTANGLE:
        return
    if len(pad.alt_shape) < 32:
        pad.alt_shape = [0] * 32
    if len(pad.corner_radius) < 32:
        pad.corner_radius = [0] * 32
    for index in layer_indices:
        pad.alt_shape[index] = ROUNDED_RECTANGLE_ALT_SHAPE
        pad.corner_radius[index] = corner_pct


def _normalize_corner_radius_percent(
    value: int | float | None,
) -> tuple[int, float | None]:
    """
    Return `(legacy_rounded_percent, exact_fractional_percent)`.

    The exact value is None for integral inputs so whole-number percents keep
    producing byte-identical output (no CornerRadiusChamfer lane). Fractional
    inputs keep full precision in the exact lane while the legacy SubRecord 6
    byte carries the rounded integer, matching native AD dual-lane storage.
    """
    if value is None:
        return DEFAULT_ROUNDED_RECTANGLE_CORNER_RADIUS_PERCENT, None
    percent = float(value)
    if not 0 <= percent <= 100:
        raise ValueError("corner_radius_percent must be between 0 and 100")
    legacy = int(round(percent))
    if float(legacy) == percent:
        return legacy, None
    return legacy, percent


def _exact_corner_layer_token(pad: AltiumPcbPad) -> str:
    """
    Layer token for a fractional corner percent on the simple pad path.

    Native evidence covers single-layer SMD pads only, so fractional
    authoring is restricted to TOP/BOTTOM pads rather than inventing
    unproven multilayer CornerRadiusChamfer storage.
    """
    if pad.layer in (int(PcbLayer.TOP), int(PcbLayer.BOTTOM)):
        return PcbLayer(pad.layer).name
    raise ValueError(
        "fractional corner_radius_percent is only supported for pads on the "
        "top or bottom layer; use a whole-number percent for other pads"
    )
