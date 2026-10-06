"""High-level Draftsman document wrapper."""

from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum, IntEnum, IntFlag
from importlib import resources
from pathlib import Path
from collections.abc import Sequence
from typing import Self, TypeVar
from uuid import uuid4

import lxml.etree as etree

from .altium_object_collection import ObjectCollection
from .altium_draftsman_container import (
    DraftsmanContainerPayload,
    DraftsmanSourceCompression,
    DraftsmanWriteCompression,
    decode_draftsman_payload,
    read_draftsman_payload,
    write_draftsman_payload,
)
from .altium_draftsman_xml import (
    XML_SCHEMA_INSTANCE_NAMESPACE,
    child_text,
    children_by_local_name,
    element_local_name,
    element_type,
    first_child_by_local_name,
    is_nil_element,
    qualified_name,
)


def _format_float(value: float) -> str:
    return f"{float(value):.15g}"


DRAFTSMAN_POINTS_PER_INCH = 96.0
DRAFTSMAN_MM_PER_INCH = 25.4
DRAFTSMAN_POINTS_PER_MM = DRAFTSMAN_POINTS_PER_INCH / DRAFTSMAN_MM_PER_INCH
_DraftsmanEnumT = TypeVar("_DraftsmanEnumT", bound=Enum)


def draftsman_points_from_mm(value_mm: float) -> float:
    """Convert millimeters to Draftsman serialized drawing points."""

    return float(value_mm) * DRAFTSMAN_POINTS_PER_MM


def draftsman_mm_from_points(value_points: float) -> float:
    """Convert Draftsman serialized drawing points to millimeters."""

    return float(value_points) / DRAFTSMAN_POINTS_PER_MM


def _read_int_child(element: etree._Element, child_local_name: str) -> int | None:
    value = child_text(element, child_local_name)
    if value is None or not value.strip():
        return None
    return int(value)


def _read_float_child(element: etree._Element, child_local_name: str) -> float | None:
    value = child_text(element, child_local_name)
    if value is None or not value.strip():
        return None
    return float(value)


def _read_bool_child(element: etree._Element, child_local_name: str) -> bool | None:
    value = child_text(element, child_local_name)
    if value is None or not value.strip():
        return None
    return value.strip().lower() == "true"


def _write_child_text(
    element: etree._Element,
    child_local_name: str,
    value: str | None,
) -> None:
    child = first_child_by_local_name(element, child_local_name)
    if child is None:
        child = etree.SubElement(element, qualified_name(child_local_name))
    child.text = value


def _write_int_child(
    element: etree._Element,
    child_local_name: str,
    value: int | None,
) -> None:
    _write_child_text(element, child_local_name, None if value is None else str(value))


def _write_float_child(
    element: etree._Element,
    child_local_name: str,
    value: float | None,
) -> None:
    _write_child_text(
        element,
        child_local_name,
        None if value is None else _format_float(value),
    )


def _write_bool_child(
    element: etree._Element,
    child_local_name: str,
    value: bool | None,
) -> None:
    _write_child_text(
        element,
        child_local_name,
        None if value is None else ("true" if value else "false"),
    )


def _new_note_element_xml(
    text: str,
    element_id: str,
    border_style: "DraftsmanNoteBorderStyle | None" = None,
) -> etree._Element:
    effective_border_style = border_style or DraftsmanNoteBorderStyle.NONE
    element = etree.Element(qualified_name("NoteElement"))
    for child_local_name, child_text_value in (
        ("BorderStyle", effective_border_style.legacy_name),
        ("BorderStyleV2", str(int(effective_border_style))),
        ("FirstLineIndent", "0"),
        ("Id", element_id),
        ("Postfix", "."),
        ("Text", text),
    ):
        child = etree.SubElement(element, qualified_name(child_local_name))
        child.text = child_text_value
    return element


def _read_only_collection(items: Sequence[object]) -> ObjectCollection:
    return ObjectCollection(list(items)).where()


_BLANK_PROFILE_RESOURCES = {
    "25": "data/draftsman/blank_ad25.PCBDwf.xml",
    "25.8.1": "data/draftsman/blank_ad25.PCBDwf.xml",
    "ad25": "data/draftsman/blank_ad25.PCBDwf.xml",
}

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_TINY_TRANSPARENT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class DraftsmanFontDecoration(IntFlag):
    """Draftsman font decoration flags used by `FontStyleData.DecorationsV2`."""

    NONE = 0
    ITALIC = 1
    BOLD = 2
    UNDERLINE = 4
    STRIKEOUT = 8


class DraftsmanNoteBorderStyle(IntEnum):
    """Draftsman note/callout reference border style."""

    NONE = 0
    SQUARE = 1
    CIRCLE = 2
    TRIANGLE = 3
    UNDERLINE = 4
    BOX = 5
    OBLONG = 6
    CIRCLE_GOST = 7
    TRIANGLE_GOST = 8
    HEXAGON = 9
    FLAG = 10

    @property
    def legacy_name(self) -> str:
        """Return the older `BorderStyle` enum text Altium writes beside V2."""

        return _NOTE_BORDER_LEGACY_NAMES.get(self, "None")

    @property
    def display_name(self) -> str:
        """Return a human-facing style label."""

        return _NOTE_BORDER_DISPLAY_NAMES[self]

    @classmethod
    def from_xml_fields(
        cls,
        border_style: str | None,
        border_style_v2: int | None,
    ) -> "DraftsmanNoteBorderStyle":
        """Resolve a style from current V2 data with legacy fallback."""

        if border_style_v2 is not None:
            try:
                return cls(border_style_v2)
            except ValueError:
                return cls.NONE
        return _NOTE_BORDER_LEGACY_LOOKUP.get(
            (border_style or "None").casefold(), cls.NONE
        )


class DraftsmanHorizontalAlignment(Enum):
    """Horizontal alignment labels used by Draftsman text items."""

    LEFT = "Left"
    CENTER = "Center"
    RIGHT = "Right"
    STRETCH = "Stretch"

    @classmethod
    def from_xml_text(cls, value: str | None) -> "DraftsmanHorizontalAlignment | None":
        """Resolve a horizontal alignment from an XML label."""

        return _enum_from_xml_text(cls, value)


class DraftsmanVerticalAlignment(Enum):
    """Vertical alignment labels used by Draftsman text items."""

    TOP = "Top"
    CENTER = "Center"
    BOTTOM = "Bottom"
    STRETCH = "Stretch"

    @classmethod
    def from_xml_text(cls, value: str | None) -> "DraftsmanVerticalAlignment | None":
        """Resolve a vertical alignment from an XML label."""

        return _enum_from_xml_text(cls, value)


_NOTE_BORDER_LEGACY_NAMES = {
    DraftsmanNoteBorderStyle.NONE: "None",
    DraftsmanNoteBorderStyle.SQUARE: "Square",
    DraftsmanNoteBorderStyle.CIRCLE: "Circle",
    DraftsmanNoteBorderStyle.TRIANGLE: "Triangle",
    DraftsmanNoteBorderStyle.UNDERLINE: "Underline",
    DraftsmanNoteBorderStyle.BOX: "Box",
    DraftsmanNoteBorderStyle.OBLONG: "Oblong",
}

_NOTE_BORDER_DISPLAY_NAMES = {
    DraftsmanNoteBorderStyle.NONE: "None",
    DraftsmanNoteBorderStyle.SQUARE: "Square",
    DraftsmanNoteBorderStyle.CIRCLE: "Circle",
    DraftsmanNoteBorderStyle.TRIANGLE: "Triangle",
    DraftsmanNoteBorderStyle.UNDERLINE: "Underline",
    DraftsmanNoteBorderStyle.BOX: "Box",
    DraftsmanNoteBorderStyle.OBLONG: "Oblong",
    DraftsmanNoteBorderStyle.CIRCLE_GOST: "Circle (GOST)",
    DraftsmanNoteBorderStyle.TRIANGLE_GOST: "Triangle (GOST)",
    DraftsmanNoteBorderStyle.HEXAGON: "Hexagon",
    DraftsmanNoteBorderStyle.FLAG: "Flag",
}

_NOTE_BORDER_LEGACY_LOOKUP = {
    legacy_name.casefold(): style
    for style, legacy_name in _NOTE_BORDER_LEGACY_NAMES.items()
}
_NOTE_BORDER_LEGACY_LOOKUP.update(
    {
        "circlegost": DraftsmanNoteBorderStyle.CIRCLE_GOST,
        "circle (gost)": DraftsmanNoteBorderStyle.CIRCLE_GOST,
        "trianglegost": DraftsmanNoteBorderStyle.TRIANGLE_GOST,
        "triangle (gost)": DraftsmanNoteBorderStyle.TRIANGLE_GOST,
        "hexagon": DraftsmanNoteBorderStyle.HEXAGON,
        "flag": DraftsmanNoteBorderStyle.FLAG,
    }
)


@dataclass(frozen=True)
class DraftsmanColor:
    """ARGB color value used by Draftsman XML color fields."""

    a: int = 255
    r: int = 0
    g: int = 0
    b: int = 0

    def __post_init__(self) -> None:
        for channel_name in ("a", "r", "g", "b"):
            channel_value = getattr(self, channel_name)
            if not 0 <= channel_value <= 255:
                raise ValueError(f"{channel_name} must be between 0 and 255")

    @classmethod
    def rgb(cls, r: int, g: int, b: int) -> Self:
        """Create an opaque color from RGB channels."""

        return cls(a=255, r=r, g=g, b=b)

    @classmethod
    def from_element(cls, element: etree._Element) -> Self | None:
        """Read a Draftsman color from an XML element."""

        values: dict[str, int] = {}
        for channel_name in ("A", "R", "G", "B"):
            channel_text = child_text(element, channel_name)
            if channel_text is None or not channel_text.strip():
                return None
            values[channel_name.lower()] = int(channel_text)
        return cls(**values)

    @property
    def hex_rgb(self) -> str:
        """Return the color as `#RRGGBB`, ignoring alpha."""

        return f"#{self.r:02X}{self.g:02X}{self.b:02X}"

    def write_to_element(self, element: etree._Element) -> None:
        """Write this color into an existing Draftsman color XML element."""

        for child_local_name, value in (
            ("A", self.a),
            ("B", self.b),
            ("G", self.g),
            ("R", self.r),
        ):
            _write_int_child(element, child_local_name, value)

        scale_values = {
            "ScA": self.a / 255.0,
            "ScB": self.b / 255.0,
            "ScG": self.g / 255.0,
            "ScR": self.r / 255.0,
        }
        for child_local_name, value in scale_values.items():
            if first_child_by_local_name(element, child_local_name) is not None:
                _write_float_child(element, child_local_name, value)


@dataclass(frozen=True)
class DraftsmanPoint:
    """Point in Draftsman page coordinates, in millimeters.

    Draftsman page coordinates use a lower-left origin: X increases to the
    right and Y increases toward the top of the sheet. Use
    `AltiumDraftsmanPage.point_from_top_left(...)` when placing objects by
    visual top-left offsets.
    """

    x_mm: float
    y_mm: float


@dataclass(frozen=True)
class DraftsmanSize:
    """Two-dimensional Draftsman size in millimeters."""

    width_mm: float
    height_mm: float


@dataclass(frozen=True)
class DraftsmanRect:
    """Rectangle in Draftsman page coordinates, in millimeters.

    `x_mm` and `y_mm` use Draftsman's lower-left page origin. Width and height
    are positive dimensions in millimeters.
    """

    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float


@dataclass(frozen=True)
class DraftsmanMargin:
    """Draftsman page margins in millimeters."""

    left_mm: float
    top_mm: float
    right_mm: float
    bottom_mm: float


class DraftsmanStandardSheetSize(Enum):
    """Standard Draftsman sheet sizes, in millimeters."""

    A0 = ("A0", 1189.0, 841.0)
    A1 = ("A1", 841.0, 594.0)
    A2 = ("A2", 594.0, 420.0)
    A3 = ("A3", 420.0, 297.0)
    A4 = ("A4", 297.0, 210.0)
    ANSI_A = ("ANSI A", 279.0, 216.0)
    ANSI_B = ("ANSI B", 432.0, 279.0)
    ANSI_C = ("ANSI C", 559.0, 432.0)
    ANSI_D = ("ANSI D", 864.0, 559.0)
    ANSI_E = ("ANSI E", 1118.0, 864.0)
    LETTER = ("Letter", 279.0, 216.0)
    LEGAL = ("Legal", 356.0, 216.0)
    TABLOID = ("Tabloid", 432.0, 279.0)

    @property
    def display_name(self) -> str:
        """Return the Altium standard sheet-size label."""

        return self.value[0]

    @property
    def size(self) -> DraftsmanSize:
        """Return the nominal sheet size in millimeters."""

        return DraftsmanSize(width_mm=self.value[1], height_mm=self.value[2])

    @classmethod
    def from_display_name(cls, value: str | None) -> Self | None:
        """Resolve a standard sheet size from Altium's display label."""

        if value is None:
            return None
        for sheet_size in cls:
            if sheet_size.display_name.casefold() == value.casefold():
                return sheet_size
        return None


@dataclass(frozen=True)
class DraftsmanFontStyle:
    """Document-level Draftsman font style.

    Draftsman stores font family names, sizes, and decoration flags in the
    `.PCBDwf` document. The document format does not embed custom font payloads
    in this style pool; callers should ensure custom families are installed on
    machines that open or render the document.
    """

    id: int
    family_name: str
    size: float
    decorations: DraftsmanFontDecoration = DraftsmanFontDecoration.NONE

    @property
    def bold(self) -> bool:
        """Return true when the style has the bold decoration flag."""

        return bool(self.decorations & DraftsmanFontDecoration.BOLD)

    @property
    def italic(self) -> bool:
        """Return true when the style has the italic decoration flag."""

        return bool(self.decorations & DraftsmanFontDecoration.ITALIC)

    @property
    def underline(self) -> bool:
        """Return true when the style has the underline decoration flag."""

        return bool(self.decorations & DraftsmanFontDecoration.UNDERLINE)

    @property
    def strikeout(self) -> bool:
        """Return true when the style has the strikeout decoration flag."""

        return bool(self.decorations & DraftsmanFontDecoration.STRIKEOUT)


class AltiumDraftsmanDocumentOptions:
    """XML-backed wrapper for Draftsman document-level drawing options."""

    def __init__(
        self,
        element: etree._Element,
        document: "AltiumDraftsmanDocument",
    ) -> None:
        self._element = element
        self._document = document

    @property
    def element(self) -> etree._Element:
        """Return the live XML element for the options block."""

        return self._element

    @property
    def font_style_id(self) -> int | None:
        """Return the document default font style id."""

        return _read_int_child(self._element, "FontStyleId")

    @font_style_id.setter
    def font_style_id(self, value: int | None) -> None:
        _write_int_child(self._element, "FontStyleId", value)

    @property
    def font_style(self) -> DraftsmanFontStyle | None:
        """Return the document default font style when it is in the style pool."""

        style_id = self.font_style_id
        if style_id is None:
            return None
        return self._document.font_style_by_id(style_id)

    def set_font_style(self, font_style: DraftsmanFontStyle | int) -> None:
        """Set the document default font style by style object or id."""

        self.font_style_id = (
            font_style if isinstance(font_style, int) else font_style.id
        )

    @property
    def non_fitted_font_style_id(self) -> int | None:
        """Return the non-fitted component font style id."""

        return _read_int_child(self._element, "NonFittedFontStyleId")

    @non_fitted_font_style_id.setter
    def non_fitted_font_style_id(self, value: int | None) -> None:
        _write_int_child(self._element, "NonFittedFontStyleId", value)

    @property
    def sheet_color(self) -> DraftsmanColor | None:
        """Return the page background color."""

        return _read_color_child(self._element, "SheetColor")

    @sheet_color.setter
    def sheet_color(self, value: DraftsmanColor) -> None:
        _write_color_child(self._element, "SheetColor", value)

    @property
    def border_color(self) -> DraftsmanColor | None:
        """Return the sheet border color."""

        return _read_color_child(self._element, "BorderColor")

    @border_color.setter
    def border_color(self, value: DraftsmanColor) -> None:
        _write_color_child(self._element, "BorderColor", value)

    @property
    def grid_visible(self) -> bool | None:
        """Return whether the Draftsman grid is visible."""

        return _read_bool_child(self._element, "GridVisible")

    @grid_visible.setter
    def grid_visible(self, value: bool | None) -> None:
        _write_bool_child(self._element, "GridVisible", value)

    @property
    def grid_size_mm(self) -> float | None:
        """Return the Draftsman grid size in millimeters."""

        value = _read_float_child(self._element, "GridSize")
        if value is None:
            return None
        return draftsman_mm_from_points(value)

    @grid_size_mm.setter
    def grid_size_mm(self, value: float | None) -> None:
        _write_float_child(
            self._element,
            "GridSize",
            None if value is None else draftsman_points_from_mm(value),
        )

    @property
    def grid_color(self) -> DraftsmanColor | None:
        """Return the Draftsman grid color."""

        return _read_color_child(self._element, "GridColor")

    @grid_color.setter
    def grid_color(self, value: DraftsmanColor) -> None:
        _write_color_child(self._element, "GridColor", value)


class AltiumDraftsmanItem:
    """Generic XML-backed wrapper for one Draftsman page item."""

    def __init__(self, element: etree._Element, page: "AltiumDraftsmanPage") -> None:
        self._element = element
        self._page = page

    @property
    def element(self) -> etree._Element:
        """Return the live XML element for this item."""

        return self._element

    @property
    def page(self) -> "AltiumDraftsmanPage":
        """Return the page that owns this item."""

        return self._page

    @property
    def id(self) -> int | None:
        """Return the item id when present."""

        return _read_int_child(self._element, "Id")

    @property
    def item_type(self) -> str:
        """Return the Draftsman item type label."""

        return element_type(self._element) or element_local_name(self._element)

    @property
    def rect(self) -> DraftsmanRect | None:
        """Return this item's rectangle in page millimeters when present."""

        return _read_rect_child(self._element, "Rect")

    @rect.setter
    def rect(self, value: DraftsmanRect) -> None:
        _write_rect_child(self._element, "Rect", value)

    @property
    def rotation_degrees(self) -> float | None:
        """Return this item's current rotation in degrees when present."""

        return _read_float_child(self._element, "Rotation2")

    @rotation_degrees.setter
    def rotation_degrees(self, value: float | None) -> None:
        _write_float_child(self._element, "Rotation2", value)

    @property
    def title(self) -> str | None:
        """Return the item title field when present."""

        return child_text(self._element, "Title")

    @title.setter
    def title(self, value: str | None) -> None:
        _write_child_text(self._element, "Title", value)


class AltiumDraftsmanNoteElement:
    """XML-backed wrapper for one Draftsman note element."""

    def __init__(
        self,
        element: etree._Element,
        note: "AltiumDraftsmanNote",
    ) -> None:
        self._element = element
        self._note = note

    @property
    def element(self) -> etree._Element:
        """Return the live XML element for this note element."""

        return self._element

    @property
    def note(self) -> "AltiumDraftsmanNote":
        """Return the note that owns this element."""

        return self._note

    @property
    def id(self) -> str | None:
        """Return the durable note-element id when present."""

        return child_text(self._element, "Id")

    @property
    def text(self) -> str | None:
        """Return the displayed note text."""

        return child_text(self._element, "Text")

    @text.setter
    def text(self, value: str | None) -> None:
        _write_child_text(self._element, "Text", value)

    @property
    def postfix(self) -> str | None:
        """Return the note numbering postfix field when present."""

        return child_text(self._element, "Postfix")

    @postfix.setter
    def postfix(self, value: str | None) -> None:
        _write_child_text(self._element, "Postfix", value)

    @property
    def border_style(self) -> str | None:
        """Return the legacy border style label when present."""

        return child_text(self._element, "BorderStyle")

    @property
    def border_style_v2(self) -> int | None:
        """Return the current numeric border style value when present."""

        return _read_int_child(self._element, "BorderStyleV2")

    @property
    def border_style_kind(self) -> DraftsmanNoteBorderStyle:
        """Return the effective note border style enum."""

        return DraftsmanNoteBorderStyle.from_xml_fields(
            self.border_style,
            self.border_style_v2,
        )

    def set_border_style(self, style: DraftsmanNoteBorderStyle) -> None:
        """Set the note row border style using Altium's current V2 enum."""

        _write_note_border_style(self._element, style)

    @property
    def first_line_indent_mm(self) -> float | None:
        """Return the first-line indent in millimeters when present."""

        value = child_text(self._element, "FirstLineIndent")
        if value is None or not value.strip():
            return None
        return draftsman_mm_from_points(float(value))


class AltiumDraftsmanNote(AltiumDraftsmanItem):
    """Typed XML-backed wrapper for one Draftsman note item."""

    @property
    def rect(self) -> DraftsmanRect | None:
        """Return the note placement as a Draftsman rectangle.

        Draftsman note XML stores a start point and width, with height driven by
        note contents. The returned rectangle therefore uses `height_mm=0`.
        """

        start_point = self.start_point
        width_mm = self.width_mm
        if start_point is None or width_mm is None:
            return None
        return DraftsmanRect(
            x_mm=start_point.x_mm,
            y_mm=start_point.y_mm,
            width_mm=width_mm,
            height_mm=0.0,
        )

    @rect.setter
    def rect(self, value: DraftsmanRect) -> None:
        self.start_point = DraftsmanPoint(value.x_mm, value.y_mm)
        self.width_mm = value.width_mm

    @property
    def elements(self) -> ObjectCollection:
        """Return a read-only collection of note elements."""

        elements_element = first_child_by_local_name(self.element, "Elements")
        if elements_element is None:
            return _read_only_collection([])
        wrappers = [
            AltiumDraftsmanNoteElement(element, self)
            for element in children_by_local_name(elements_element, "NoteElement")
        ]
        return _read_only_collection(wrappers)

    @property
    def element_count(self) -> int:
        """Return the number of note elements."""

        return len(self.elements)

    def element_by_id(self, element_id: str) -> AltiumDraftsmanNoteElement | None:
        """Return the first note element with the requested durable id."""

        return self.elements.first(AltiumDraftsmanNoteElement, id=element_id)

    def add_element(
        self,
        text: str,
        *,
        element_id: str | None = None,
        template_element_id: str | None = None,
        border_style: DraftsmanNoteBorderStyle | None = None,
    ) -> AltiumDraftsmanNoteElement:
        """Append a note element and return its live wrapper."""

        new_element_id = element_id or uuid4().hex
        elements_element = self._ensure_elements_element()
        template = self._note_element_template(template_element_id)
        effective_border_style = border_style or DraftsmanNoteBorderStyle.NONE
        element = _new_note_element_xml(text, new_element_id, effective_border_style)
        if template is not None:
            element = deepcopy(template)
            _write_child_text(element, "Id", new_element_id)
            _write_child_text(element, "Text", text)
            if border_style is not None:
                _write_note_border_style(element, border_style)
        elements_element.append(element)
        return AltiumDraftsmanNoteElement(element, self)

    def remove_element(self, element_id: str) -> bool:
        """Remove a note element by durable id and report whether it was found."""

        note_element = self.element_by_id(element_id)
        if note_element is None:
            return False
        parent = note_element.element.getparent()
        if parent is None:
            return False
        parent.remove(note_element.element)
        return True

    def move_element(self, element_id: str, index: int) -> None:
        """Move a note element to a new zero-based position."""

        note_element = self.element_by_id(element_id)
        if note_element is None:
            raise KeyError(element_id)
        parent = note_element.element.getparent()
        if parent is None:
            raise ValueError("note element is detached")
        parent.remove(note_element.element)
        parent.insert(index, note_element.element)

    @property
    def title_visible(self) -> bool | None:
        """Return whether the note title is visible when the field is present."""

        return _read_bool_child(self.element, "TitleVisible")

    @title_visible.setter
    def title_visible(self, value: bool | None) -> None:
        _write_bool_child(self.element, "TitleVisible", value)

    @property
    def element_font_style_id(self) -> int | None:
        """Return the note body font style id."""

        return _read_int_child(self.element, "ElementFontStyleId")

    @element_font_style_id.setter
    def element_font_style_id(self, value: int | None) -> None:
        _write_int_child(self.element, "ElementFontStyleId", value)

    @property
    def element_font_style(self) -> DraftsmanFontStyle | None:
        """Return the note body font style when it is in the style pool."""

        style_id = self.element_font_style_id
        if style_id is None:
            return None
        return self.page.document.font_style_by_id(style_id)

    def set_element_font_style(self, font_style: DraftsmanFontStyle | int) -> None:
        """Set the note body font style and make the explicit style active."""

        self.element_font_style_id = (
            font_style if isinstance(font_style, int) else font_style.id
        )
        self.use_document_font_for_elements = False

    @property
    def title_font_style_id(self) -> int | None:
        """Return the note title font style id."""

        return _read_int_child(self.element, "TitleFontStyleId")

    @title_font_style_id.setter
    def title_font_style_id(self, value: int | None) -> None:
        _write_int_child(self.element, "TitleFontStyleId", value)

    @property
    def title_font_style(self) -> DraftsmanFontStyle | None:
        """Return the note title font style when it is in the style pool."""

        style_id = self.title_font_style_id
        if style_id is None:
            return None
        return self.page.document.font_style_by_id(style_id)

    def set_title_font_style(self, font_style: DraftsmanFontStyle | int) -> None:
        """Set the note title font style and make the explicit style active."""

        self.title_font_style_id = (
            font_style if isinstance(font_style, int) else font_style.id
        )
        self.use_document_font_for_title = False

    @property
    def element_text_color(self) -> DraftsmanColor | None:
        """Return the note body text color."""

        return _read_color_child(self.element, "ElementTextColor")

    @element_text_color.setter
    def element_text_color(self, value: DraftsmanColor) -> None:
        _write_color_child(self.element, "ElementTextColor", value)

    @property
    def title_text_color(self) -> DraftsmanColor | None:
        """Return the note title text color."""

        return _read_color_child(self.element, "TitleTextColor")

    @title_text_color.setter
    def title_text_color(self, value: DraftsmanColor) -> None:
        _write_color_child(self.element, "TitleTextColor", value)

    @property
    def use_document_font_for_elements(self) -> bool | None:
        """Return whether note body rows use the document font style."""

        return _read_bool_child(self.element, "UseDocumentFontForElements")

    @use_document_font_for_elements.setter
    def use_document_font_for_elements(self, value: bool | None) -> None:
        _write_bool_child(self.element, "UseDocumentFontForElements", value)

    @property
    def use_document_font_for_title(self) -> bool | None:
        """Return whether the note title uses the document font style."""

        return _read_bool_child(self.element, "UseDocumentFontForTitle")

    @use_document_font_for_title.setter
    def use_document_font_for_title(self, value: bool | None) -> None:
        _write_bool_child(self.element, "UseDocumentFontForTitle", value)

    @property
    def start_point(self) -> DraftsmanPoint | None:
        """Return the note start point in page millimeters."""

        return _read_point_child(self.element, "StartPoint")

    @start_point.setter
    def start_point(self, value: DraftsmanPoint) -> None:
        _write_point_child(self.element, "StartPoint", value)

    @property
    def width_mm(self) -> float | None:
        """Return the note width in millimeters."""

        value = _read_float_child(self.element, "Width")
        if value is None:
            return None
        return draftsman_mm_from_points(value)

    @width_mm.setter
    def width_mm(self, value: float | None) -> None:
        _write_float_child(
            self.element,
            "Width",
            None if value is None else draftsman_points_from_mm(value),
        )

    def _ensure_elements_element(self) -> etree._Element:
        elements_element = first_child_by_local_name(self.element, "Elements")
        if elements_element is None:
            elements_element = etree.SubElement(
                self.element, qualified_name("Elements")
            )
        return elements_element

    def _note_element_template(self, element_id: str | None) -> etree._Element | None:
        if element_id is not None:
            note_element = self.element_by_id(element_id)
            return note_element.element if note_element is not None else None
        if self.element_count == 0:
            return None
        return self.elements[-1].element


class AltiumDraftsmanText(AltiumDraftsmanItem):
    """Typed XML-backed wrapper for one Draftsman text item."""

    @property
    def text(self) -> str | None:
        """Return the displayed text expression."""

        return child_text(self.element, "Text")

    @text.setter
    def text(self, value: str | None) -> None:
        _write_child_text(self.element, "Text", value)

    @property
    def font_style_id(self) -> int | None:
        """Return the text font-style id."""

        return _read_int_child(self.element, "FontStyleId")

    @font_style_id.setter
    def font_style_id(self, value: int | None) -> None:
        _write_int_child(self.element, "FontStyleId", value)

    @property
    def fill_style_id(self) -> int | None:
        """Return the text fill-style id.

        Altium falls back to transparent text fill when this id does not
        resolve to a document fill style.
        """

        return _read_int_child(self.element, "FillStyleId")

    @fill_style_id.setter
    def fill_style_id(self, value: int | None) -> None:
        _write_int_child(self.element, "FillStyleId", value)

    @property
    def font_style(self) -> DraftsmanFontStyle | None:
        """Return the text font style when it is in the style pool."""

        style_id = self.font_style_id
        if style_id is None:
            return None
        return self.page.document.font_style_by_id(style_id)

    def set_font_style(self, font_style: DraftsmanFontStyle | int) -> None:
        """Set the text font style and make the explicit style active."""

        self.font_style_id = (
            font_style if isinstance(font_style, int) else font_style.id
        )
        self.use_document_font = False

    @property
    def use_document_font(self) -> bool | None:
        """Return whether this text follows the document default font."""

        return _read_bool_child(self.element, "UseDocumentFont")

    @use_document_font.setter
    def use_document_font(self, value: bool | None) -> None:
        _write_bool_child(self.element, "UseDocumentFont", value)

    @property
    def color(self) -> DraftsmanColor | None:
        """Return the text color."""

        return _read_color_child(self.element, "Color")

    @color.setter
    def color(self, value: DraftsmanColor) -> None:
        _write_color_child(self.element, "Color", value)

    @property
    def horizontal_alignment(self) -> DraftsmanHorizontalAlignment | None:
        """Return the horizontal alignment enum when recognized."""

        return DraftsmanHorizontalAlignment.from_xml_text(
            child_text(self.element, "HorizontalAlignment")
        )

    @horizontal_alignment.setter
    def horizontal_alignment(
        self,
        value: DraftsmanHorizontalAlignment | str | None,
    ) -> None:
        _write_child_text(
            self.element,
            "HorizontalAlignment",
            _enum_value_or_text(value),
        )

    @property
    def vertical_alignment(self) -> DraftsmanVerticalAlignment | None:
        """Return the vertical alignment enum when recognized."""

        return DraftsmanVerticalAlignment.from_xml_text(
            child_text(self.element, "VerticalAlignment")
        )

    @vertical_alignment.setter
    def vertical_alignment(
        self,
        value: DraftsmanVerticalAlignment | str | None,
    ) -> None:
        _write_child_text(self.element, "VerticalAlignment", _enum_value_or_text(value))

    @property
    def clip_to_bounds(self) -> bool | None:
        """Return whether rendering clips text to `rect`."""

        return _read_bool_child(self.element, "ClipToBounds")

    @clip_to_bounds.setter
    def clip_to_bounds(self, value: bool | None) -> None:
        _write_bool_child(self.element, "ClipToBounds", value)


class AltiumDraftsmanPicture(AltiumDraftsmanItem):
    """Typed XML-backed wrapper for one Draftsman picture item."""

    @property
    def maintain_aspect_ratio(self) -> bool | None:
        """Return whether the picture keeps the source image aspect ratio."""

        return _read_bool_child(self.element, "MaintainAspectRatio")

    @maintain_aspect_ratio.setter
    def maintain_aspect_ratio(self, value: bool | None) -> None:
        _write_bool_child(self.element, "MaintainAspectRatio", value)

    @property
    def image_bytes(self) -> bytes:
        """Return the embedded image bytes.

        Altium reads `Bitmap2` when present and falls back to the legacy
        `Bitmap` payload. The public wrapper follows that same precedence.
        """

        return _read_bytes_child(self.element, "Bitmap2") or _read_bytes_child(
            self.element,
            "Bitmap",
        )

    def set_image_bytes(self, data: bytes) -> None:
        """Replace the embedded image bytes."""

        _write_picture_bitmap_fields(self.element, data)


class AltiumDraftsmanPage:
    """XML-backed wrapper for one Draftsman page.

    Geometry helpers expose millimeters. Draftsman page coordinates use a
    lower-left origin: X increases rightward and Y increases upward.
    """

    def __init__(
        self,
        element: etree._Element,
        document: "AltiumDraftsmanDocument",
    ) -> None:
        self._element = element
        self._document = document

    @property
    def element(self) -> etree._Element:
        """Return the live XML element for this page."""

        return self._element

    @property
    def document(self) -> "AltiumDraftsmanDocument":
        """Return the document that owns this page."""

        return self._document

    @property
    def id(self) -> int | None:
        """Return the page id when present."""

        return _read_int_child(self._element, "Id")

    @property
    def items(self) -> ObjectCollection:
        """Return a read-only collection of page item wrappers."""

        items_element = first_child_by_local_name(self._element, "Items")
        if items_element is None:
            return _read_only_collection([])
        wrappers = [
            _wrap_page_item(item_element, self)
            for item_element in children_by_local_name(items_element, "Item")
        ]
        return _read_only_collection(wrappers)

    @property
    def item_count(self) -> int:
        """Return the number of page items."""

        return len(self.items)

    @property
    def size(self) -> DraftsmanSize | None:
        """Return the page size in millimeters."""

        return _read_size_child(self._element, "Size")

    @size.setter
    def size(self, value: DraftsmanSize) -> None:
        _write_size_child(self._element, "Size", value)

    @property
    def margin(self) -> DraftsmanMargin | None:
        """Return the page margins in millimeters."""

        return _read_margin_child(self._element, "Margin")

    @margin.setter
    def margin(self, value: DraftsmanMargin) -> None:
        _write_margin_child(self._element, "Margin", value)

    @property
    def sheet_sizing_mode(self) -> str | None:
        """Return the raw Draftsman sheet sizing mode label."""

        return child_text(self._element, "SheetSizingMode")

    @sheet_sizing_mode.setter
    def sheet_sizing_mode(self, value: str | None) -> None:
        _write_child_text(self._element, "SheetSizingMode", value)

    @property
    def standard_size_name(self) -> str | None:
        """Return the standard sheet size label, such as `A4`."""

        return child_text(self._element, "StandardSizeName")

    @standard_size_name.setter
    def standard_size_name(self, value: str | None) -> None:
        _write_child_text(self._element, "StandardSizeName", value)

    @property
    def standard_sheet_size(self) -> DraftsmanStandardSheetSize | None:
        """Return the standard sheet-size enum when the name is recognized."""

        return DraftsmanStandardSheetSize.from_display_name(self.standard_size_name)

    @standard_sheet_size.setter
    def standard_sheet_size(self, value: DraftsmanStandardSheetSize) -> None:
        self.apply_standard_sheet_size(value)

    def apply_standard_sheet_size(self, value: DraftsmanStandardSheetSize) -> None:
        """Set this page to one of Altium's standard sheet sizes."""

        self.sheet_sizing_mode = "StandardSize"
        self.standard_size_name = value.display_name
        self.size = value.size

    @property
    def show_zones(self) -> bool | None:
        """Return whether page zones are visible."""

        return _read_bool_child(self._element, "ShowZones")

    @show_zones.setter
    def show_zones(self, value: bool | None) -> None:
        _write_bool_child(self._element, "ShowZones", value)

    @property
    def notes(self) -> ObjectCollection:
        """Return a read-only collection of note items on this page."""

        return self.items.of_type(AltiumDraftsmanNote)

    def item_by_id(self, item_id: int) -> AltiumDraftsmanItem | None:
        """Return the first page item with the requested id."""

        return self.items.first(AltiumDraftsmanItem, id=item_id)

    def items_by_type(self, item_type: str) -> ObjectCollection:
        """Return a read-only collection of page items with a raw item type."""

        return self.items.where(item_type=item_type)

    def note_by_title(
        self,
        title: str,
        *,
        case_sensitive: bool = False,
    ) -> AltiumDraftsmanNote | None:
        """Return the first page note with the requested title."""

        requested_title = title if case_sensitive else title.casefold()
        for note in self.notes:
            note_title = note.title
            if note_title is None:
                continue
            effective_title = note_title if case_sensitive else note_title.casefold()
            if effective_title == requested_title:
                return note
        return None

    def point_from_top_left(self, *, left_mm: float, top_mm: float) -> DraftsmanPoint:
        """Return a page point offset from the visual top-left corner.

        The returned `DraftsmanPoint` is still in Draftsman page coordinates,
        whose serialized origin is the lower-left corner. This helper converts
        a top-edge offset into the corresponding Y coordinate.
        """

        size = self.size
        if size is None:
            raise ValueError("Draftsman page size is not available")
        return DraftsmanPoint(x_mm=float(left_mm), y_mm=size.height_mm - float(top_mm))

    def rect_centered(self, *, width_mm: float, height_mm: float) -> DraftsmanRect:
        """Return a rectangle centered on the page."""

        size = self.size
        if size is None:
            raise ValueError("Draftsman page size is not available")
        width = float(width_mm)
        height = float(height_mm)
        return DraftsmanRect(
            x_mm=(size.width_mm - width) / 2.0,
            y_mm=(size.height_mm - height) / 2.0,
            width_mm=width,
            height_mm=height,
        )

    def add_text(
        self,
        *,
        text: str,
        rect: DraftsmanRect,
        font_style: DraftsmanFontStyle | int | None = None,
        color: DraftsmanColor | None = None,
        horizontal_alignment: DraftsmanHorizontalAlignment | str = (
            DraftsmanHorizontalAlignment.LEFT
        ),
        vertical_alignment: DraftsmanVerticalAlignment | str = (
            DraftsmanVerticalAlignment.TOP
        ),
        clip_to_bounds: bool = True,
        use_document_font: bool | None = None,
        rotation_degrees: float = 0.0,
        fill_style_id: int = 0,
    ) -> AltiumDraftsmanText:
        """Append a Draftsman text item to this page.

        The default `fill_style_id=0` intentionally leaves the fill style
        unresolved so Altium loads the text with a transparent fill. Some blank
        templates use fill style id 1 for a visible hatch fill.
        """

        document_font_id = self.document.document_options.font_style_id
        font_style_id = _style_id_or_default(font_style, document_font_id)
        effective_use_document_font = (
            font_style is None if use_document_font is None else use_document_font
        )
        item = etree.Element(qualified_name("Item"))
        item.set(qualified_name("type", XML_SCHEMA_INSTANCE_NAMESPACE), "Text")
        _write_int_child(item, "Id", self.document.next_page_or_item_id)
        _write_child_text(item, "Anchor", "Top Left")
        _write_bool_child(item, "OriginatedFromTemplate", False)
        _write_bool_child(item, "ClipToBounds", clip_to_bounds)
        _write_color_child(item, "Color", color or DraftsmanColor.rgb(0, 0, 0))
        _write_int_child(item, "FillStyleId", fill_style_id)
        _write_int_child(item, "FontStyleId", font_style_id)
        _write_child_text(
            item,
            "HorizontalAlignment",
            _enum_value_or_text(horizontal_alignment),
        )
        _write_rect_child(item, "Rect", rect)
        _write_child_text(item, "Rotation", _orthogonal_rotation_name(rotation_degrees))
        _write_float_child(item, "Rotation2", rotation_degrees)
        _write_child_text(item, "Text", text)
        _write_bool_child(item, "UseDocumentFont", effective_use_document_font)
        _write_int_child(item, "Version", 1)
        _write_child_text(
            item, "VerticalAlignment", _enum_value_or_text(vertical_alignment)
        )

        self._ensure_items_element().append(item)
        return AltiumDraftsmanText(item, self)

    def add_picture(
        self,
        *,
        source_path: str | Path,
        rect: DraftsmanRect,
        maintain_aspect_ratio: bool = True,
        rotation_degrees: float = 0.0,
    ) -> AltiumDraftsmanPicture:
        """Append a Draftsman picture item with embedded image bytes."""

        image_bytes = Path(source_path).read_bytes()
        item = etree.Element(qualified_name("Item"))
        item.set(qualified_name("type", XML_SCHEMA_INSTANCE_NAMESPACE), "Picture")
        _write_int_child(item, "Id", self.document.next_page_or_item_id)
        _write_child_text(item, "Anchor", "Top Left")
        _write_bool_child(item, "OriginatedFromTemplate", False)
        _write_picture_bitmap_fields(item, image_bytes)
        _write_bool_child(item, "MaintainAspectRatio", maintain_aspect_ratio)
        _write_rect_child(item, "Rect", rect)
        _write_child_text(item, "Rotation", _orthogonal_rotation_name(rotation_degrees))
        _write_float_child(item, "Rotation2", rotation_degrees)

        self._ensure_items_element().append(item)
        return AltiumDraftsmanPicture(item, self)

    def add_note(
        self,
        *,
        title: str | None = None,
        rect: DraftsmanRect | None = None,
        x_mm: float | None = None,
        y_mm: float | None = None,
        width_mm: float | None = None,
        bullets: list[str] | tuple[str, ...] = (),
        element_font_style: DraftsmanFontStyle | int | None = None,
        title_font_style: DraftsmanFontStyle | int | None = None,
        text_color: DraftsmanColor | None = None,
        title_visible: bool = True,
        use_document_font_for_elements: bool | None = None,
        use_document_font_for_title: bool | None = None,
        bullet_border_style: DraftsmanNoteBorderStyle = DraftsmanNoteBorderStyle.NONE,
    ) -> AltiumDraftsmanNote:
        """Append a simple Draftsman note item to this page.

        `rect` is in Draftsman page coordinates with a lower-left origin. Use
        `point_from_top_left(...)` to build a rect from visual top-left offsets.
        Draftsman serializes notes as a start point plus width; `rect.height_mm`
        is accepted for layout consistency with text and picture creation but
        is not serialized. Legacy `x_mm`/`y_mm`/`width_mm` keyword arguments are
        still accepted when `rect` is not provided.

        The generated note uses the document style pool by id and preserves the
        XML-backed model. More specialized note title/link element data can be
        added once additional Altium-open/save fixtures prove those contracts.
        """

        note_rect = rect or DraftsmanRect(
            x_mm=20.0 if x_mm is None else float(x_mm),
            y_mm=20.0 if y_mm is None else float(y_mm),
            width_mm=120.0 if width_mm is None else float(width_mm),
            height_mm=0.0,
        )
        document_font_id = self.document.document_options.font_style_id
        body_font_id = _style_id_or_default(element_font_style, document_font_id)
        heading_font_id = _style_id_or_default(title_font_style, body_font_id)
        effective_use_document_font_for_elements = (
            element_font_style is None
            if use_document_font_for_elements is None
            else use_document_font_for_elements
        )
        effective_use_document_font_for_title = (
            title_font_style is None and element_font_style is None
            if use_document_font_for_title is None
            else use_document_font_for_title
        )
        effective_text_color = text_color or DraftsmanColor.rgb(0, 0, 0)

        item = etree.Element(qualified_name("Item"))
        item.set(qualified_name("type", XML_SCHEMA_INSTANCE_NAMESPACE), "Note")
        _write_int_child(item, "Id", self.document.next_page_or_item_id)
        _write_child_text(item, "ElementItems", None)
        _write_int_child(item, "ElementFontStyleId", body_font_id)
        _write_color_child(item, "ElementTextColor", effective_text_color)
        elements_element = etree.SubElement(item, qualified_name("Elements"))
        for bullet in bullets:
            elements_element.append(
                _new_note_element_xml(bullet, uuid4().hex, bullet_border_style)
            )
        _write_float_child(
            item,
            "HorizontalSpacing",
            draftsman_points_from_mm(2.0),
        )
        _write_point_child(
            item, "StartPoint", DraftsmanPoint(note_rect.x_mm, note_rect.y_mm)
        )
        _write_child_text(item, "Title", title)
        _write_int_child(item, "TitleFontStyleId", heading_font_id)
        _write_color_child(item, "TitleTextColor", effective_text_color)
        _write_bool_child(item, "TitleVisible", title_visible)
        _write_bool_child(
            item,
            "UseDocumentFontForElements",
            effective_use_document_font_for_elements,
        )
        _write_bool_child(
            item,
            "UseDocumentFontForTitle",
            effective_use_document_font_for_title,
        )
        _write_float_child(item, "VerticalSpacing", draftsman_points_from_mm(2.0))
        _write_float_child(item, "Width", draftsman_points_from_mm(note_rect.width_mm))

        self._ensure_items_element().append(item)
        return AltiumDraftsmanNote(item, self)

    def _ensure_items_element(self) -> etree._Element:
        items_element = first_child_by_local_name(self._element, "Items")
        if items_element is None:
            items_element = etree.SubElement(self._element, qualified_name("Items"))
        return items_element


class AltiumDraftsmanDocument:
    """XML-backed Draftsman document with conservative load and save support."""

    def __init__(
        self,
        root: etree._Element,
        *,
        source_path: str | Path | None = None,
        source_compression: DraftsmanSourceCompression = "raw",
    ) -> None:
        self._root: etree._Element = root
        self._source_path: Path | None = (
            Path(source_path) if source_path is not None else None
        )
        self._source_compression: DraftsmanSourceCompression = source_compression

    @classmethod
    def from_file(cls, path: str | Path) -> Self:
        """Load a Draftsman file from disk."""

        source_path = Path(path)
        payload = read_draftsman_payload(source_path)
        return cls.from_payload(payload, source_path=source_path)

    @classmethod
    def from_template(
        cls,
        path: str | Path,
        *,
        source_document_name: str | Path | None = None,
    ) -> Self:
        """Load a Draftsman template file as an editable document."""

        document = cls.from_file(path)
        if source_document_name is not None:
            document.set_source_document_name(source_document_name)
        return document

    @classmethod
    def blank(
        cls,
        profile: str = "ad25",
        *,
        source_document_name: str | Path | None = None,
    ) -> Self:
        """Create a blank Draftsman document from packaged defaults."""

        document = cls.from_xml_bytes(_blank_profile_xml(profile))
        if source_document_name is not None:
            document.set_source_document_name(source_document_name)
        return document

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        *,
        source_path: str | Path | None = None,
    ) -> Self:
        """Load a Draftsman document from raw file bytes."""

        return cls.from_payload(
            decode_draftsman_payload(data),
            source_path=source_path,
        )

    @classmethod
    def from_xml_bytes(
        cls,
        xml_bytes: bytes,
        *,
        source_path: str | Path | None = None,
        source_compression: DraftsmanSourceCompression = "raw",
    ) -> Self:
        """Load a Draftsman document from decoded XML bytes."""

        root = _parse_xml_root(xml_bytes)
        return cls(
            root,
            source_path=source_path,
            source_compression=source_compression,
        )

    @classmethod
    def from_payload(
        cls,
        payload: DraftsmanContainerPayload,
        *,
        source_path: str | Path | None = None,
    ) -> Self:
        """Load a Draftsman document from a decoded container payload."""

        return cls.from_xml_bytes(
            payload.xml_bytes,
            source_path=source_path,
            source_compression=payload.source_compression,
        )

    @property
    def root(self) -> etree._Element:
        """Return the live XML root element."""

        return self._root

    @property
    def source_path(self) -> Path | None:
        """Return the path this document was loaded from, if known."""

        return self._source_path

    @property
    def source_compression(self) -> DraftsmanSourceCompression:
        """Return the original container encoding for this document."""

        return self._source_compression

    @property
    def root_tag(self) -> str:
        """Return the root XML element local name."""

        return element_local_name(self._root)

    @property
    def format_version(self) -> int | None:
        """Return the Draftsman format version when the root declares one."""

        return _read_int_child(self._root, "FormatVersion")

    @property
    def source_document_name(self) -> str:
        """Return the linked board or project filename stored in the document."""

        return child_text(self._root, "SourceDocumentName", "") or ""

    @property
    def document_options(self) -> AltiumDraftsmanDocumentOptions:
        """Return the XML-backed document options wrapper."""

        options_element = first_child_by_local_name(self._root, "DocumentOptions")
        if options_element is None:
            options_element = etree.SubElement(
                self._root,
                qualified_name("DocumentOptions"),
            )
        return AltiumDraftsmanDocumentOptions(options_element, self)

    @property
    def font_styles(self) -> ObjectCollection:
        """Return document-level font styles.

        These styles reference font family names installed on the host. The
        `.PCBDwf` style pool does not contain embedded font bytes.
        """

        styles_element = first_child_by_local_name(self._root, "FontStyles")
        if styles_element is None:
            return _read_only_collection([])
        styles = [
            style
            for element in children_by_local_name(styles_element, "FontStyleData")
            if (style := _font_style_from_element(element)) is not None
        ]
        return _read_only_collection(styles)

    def font_style_by_id(self, style_id: int) -> DraftsmanFontStyle | None:
        """Return the font style with the requested id, if present."""

        return self.font_styles.first(DraftsmanFontStyle, id=style_id)

    def find_font_style(
        self,
        family_name: str,
        size: float,
        *,
        decorations: DraftsmanFontDecoration = DraftsmanFontDecoration.NONE,
    ) -> DraftsmanFontStyle | None:
        """Return the first matching font style from the document pool."""

        requested_family = family_name.casefold()
        for style in self.font_styles:
            if style.family_name.casefold() != requested_family:
                continue
            if abs(style.size - float(size)) > 1e-9:
                continue
            if style.decorations != decorations:
                continue
            return style
        return None

    def get_or_create_font_style(
        self,
        family_name: str,
        size: float,
        *,
        bold: bool = False,
        italic: bool = False,
        underline: bool = False,
        strikeout: bool = False,
        decorations: DraftsmanFontDecoration | None = None,
    ) -> DraftsmanFontStyle:
        """Return a matching font style, adding one when necessary."""

        effective_decorations = decorations
        if effective_decorations is None:
            effective_decorations = _font_decorations_from_flags(
                bold=bold,
                italic=italic,
                underline=underline,
                strikeout=strikeout,
            )

        existing = self.find_font_style(
            family_name,
            size,
            decorations=effective_decorations,
        )
        if existing is not None:
            return existing

        new_style = DraftsmanFontStyle(
            id=self._next_font_style_id(),
            family_name=family_name,
            size=float(size),
            decorations=effective_decorations,
        )
        self._ensure_font_styles_element().append(_font_style_to_element(new_style))
        return new_style

    def set_source_document_name(self, source_document_name: str | Path) -> None:
        """Set the linked board or project filename stored in the document."""

        source_document_text = str(source_document_name)
        _write_child_text(self._root, "SourceDocumentName", source_document_text)
        _write_parameter_value(self._root, "Pcb_File_Name", source_document_text)

    @property
    def pages(self) -> ObjectCollection:
        """Return a read-only collection of page wrappers."""

        pages_element = first_child_by_local_name(self._root, "Pages")
        if pages_element is None:
            return _read_only_collection([])
        wrappers = [
            AltiumDraftsmanPage(page_element, self)
            for page_element in children_by_local_name(pages_element, "Page")
        ]
        return _read_only_collection(wrappers)

    def page_by_id(self, page_id: int) -> AltiumDraftsmanPage | None:
        """Return the first page with the requested id."""

        return self.pages.first(AltiumDraftsmanPage, id=page_id)

    @property
    def items(self) -> ObjectCollection:
        """Return a read-only collection of all page item wrappers."""

        wrappers: list[object] = []
        for page in self.pages:
            wrappers.extend(page.items.to_list())
        return _read_only_collection(wrappers)

    @property
    def max_page_or_item_id(self) -> int:
        """Return the highest id found on pages and page items."""

        ids: list[int] = []
        for page in self.pages:
            if page.id is not None:
                ids.append(page.id)
            for item in page.items:
                if item.id is not None:
                    ids.append(item.id)
        if not ids:
            return 0
        return max(ids)

    @property
    def next_page_or_item_id(self) -> int:
        """Return the next id after all known page and item ids."""

        return self.max_page_or_item_id + 1

    def item_by_id(self, item_id: int) -> AltiumDraftsmanItem | None:
        """Return the first page item with the requested id."""

        return self.items.first(AltiumDraftsmanItem, id=item_id)

    @property
    def notes(self) -> ObjectCollection:
        """Return a read-only collection of note items in the document."""

        return self.items.of_type(AltiumDraftsmanNote)

    def note_by_id(self, note_id: int) -> AltiumDraftsmanNote | None:
        """Return the first note item with the requested id."""

        return self.notes.first(AltiumDraftsmanNote, id=note_id)

    @property
    def texts(self) -> ObjectCollection:
        """Return a read-only collection of text items in the document."""

        return self.items.of_type(AltiumDraftsmanText)

    @property
    def pictures(self) -> ObjectCollection:
        """Return a read-only collection of picture items in the document."""

        return self.items.of_type(AltiumDraftsmanPicture)

    def add_page(
        self,
        *,
        copy_from: AltiumDraftsmanPage | None = None,
        clear_items: bool = True,
    ) -> AltiumDraftsmanPage:
        """Append a page cloned from existing sheet setup and return it.

        The first supported mode clears cloned page items. Preserving page
        items needs id and reference remapping and is intentionally deferred.
        """

        if not clear_items:
            raise NotImplementedError(
                "add_page(clear_items=False) requires page-item id remapping"
            )

        source_page = copy_from or self.pages.first(AltiumDraftsmanPage)
        if source_page is None:
            blank_document = type(self).blank()
            source_page = blank_document.pages[0]

        page_element = deepcopy(source_page.element)
        _write_int_child(page_element, "Id", self.next_page_or_item_id)
        _clear_page_items(page_element)
        self._ensure_pages_element().append(page_element)
        return AltiumDraftsmanPage(page_element, self)

    def remove_page(self, id_or_page: int | AltiumDraftsmanPage) -> bool:
        """Remove a page by id or wrapper and report whether it was found."""

        page_element = self._page_element_for(id_or_page)
        if page_element is None:
            return False
        parent = page_element.getparent()
        if parent is None:
            return False
        parent.remove(page_element)
        return True

    def move_page(self, id_or_page: int | AltiumDraftsmanPage, index: int) -> None:
        """Move a page to a new zero-based position."""

        page_element = self._page_element_for(id_or_page)
        if page_element is None:
            page_id = (
                id_or_page.id
                if isinstance(id_or_page, AltiumDraftsmanPage)
                else id_or_page
            )
            raise KeyError(page_id)
        parent = page_element.getparent()
        if parent is None:
            raise ValueError("page is detached")
        parent.remove(page_element)
        parent.insert(index, page_element)

    def to_xml_bytes(self, *, pretty_print: bool = False) -> bytes:
        """Serialize the live XML tree to UTF-8 bytes."""

        return etree.tostring(
            self._root,
            encoding="utf-8",
            xml_declaration=False,
            pretty_print=pretty_print,
        )

    def save(
        self,
        path: str | Path,
        *,
        compression: DraftsmanWriteCompression = "preserve",
        pretty_print: bool = False,
    ) -> None:
        """Write the document to disk."""

        write_draftsman_payload(
            path,
            self.to_xml_bytes(pretty_print=pretty_print),
            compression=compression,
            source_compression=self._source_compression,
        )

    def _ensure_font_styles_element(self) -> etree._Element:
        styles_element = first_child_by_local_name(self._root, "FontStyles")
        if styles_element is not None:
            return styles_element

        styles_element = etree.Element(qualified_name("FontStyles"))
        line_styles = first_child_by_local_name(self._root, "LineStyles")
        if line_styles is not None:
            self._root.insert(self._root.index(line_styles), styles_element)
        else:
            self._root.append(styles_element)
        return styles_element

    def _next_font_style_id(self) -> int:
        ids = [style.id for style in self.font_styles]
        if not ids:
            return 1
        return max(ids) + 1

    def _ensure_pages_element(self) -> etree._Element:
        pages_element = first_child_by_local_name(self._root, "Pages")
        if pages_element is not None:
            return pages_element

        pages_element = etree.Element(qualified_name("Pages"))
        parameters = first_child_by_local_name(self._root, "Parameters")
        if parameters is not None:
            self._root.insert(self._root.index(parameters), pages_element)
        else:
            self._root.append(pages_element)
        return pages_element

    def _page_element_for(
        self,
        id_or_page: int | AltiumDraftsmanPage,
    ) -> etree._Element | None:
        if isinstance(id_or_page, AltiumDraftsmanPage):
            if (
                id_or_page.document is self
                and id_or_page.element.getparent() is not None
            ):
                return id_or_page.element
            page_id = id_or_page.id
            if page_id is None:
                return None
        else:
            page_id = id_or_page

        page = self.page_by_id(page_id)
        return None if page is None else page.element


def _parse_xml_root(xml_bytes: bytes) -> etree._Element:
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        huge_tree=True,
        remove_blank_text=False,
        recover=False,
    )
    root = etree.fromstring(xml_bytes, parser=parser)
    if not isinstance(root.tag, str):
        raise ValueError("Draftsman XML root must be an element")
    return root


def _clear_page_items(page_element: etree._Element) -> None:
    items_element = first_child_by_local_name(page_element, "Items")
    if items_element is None:
        etree.SubElement(page_element, qualified_name("Items"))
        return
    for child in list(items_element):
        items_element.remove(child)
    items_element.text = None


def _font_decorations_from_flags(
    *,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
    strikeout: bool = False,
) -> DraftsmanFontDecoration:
    decorations = DraftsmanFontDecoration.NONE
    if italic:
        decorations |= DraftsmanFontDecoration.ITALIC
    if bold:
        decorations |= DraftsmanFontDecoration.BOLD
    if underline:
        decorations |= DraftsmanFontDecoration.UNDERLINE
    if strikeout:
        decorations |= DraftsmanFontDecoration.STRIKEOUT
    return decorations


def _font_style_from_element(element: etree._Element) -> DraftsmanFontStyle | None:
    style_id = _read_int_child(element, "Id")
    family_name = child_text(element, "FamilyName")
    size = _read_float_child(element, "Size")
    if style_id is None or family_name is None or size is None:
        return None
    decorations = _read_int_child(element, "DecorationsV2")
    if decorations is None:
        decorations = _decoration_text_to_int(child_text(element, "Decorations"))
    return DraftsmanFontStyle(
        id=style_id,
        family_name=family_name,
        size=size,
        decorations=DraftsmanFontDecoration(decorations),
    )


def _font_style_to_element(style: DraftsmanFontStyle) -> etree._Element:
    element = etree.Element(qualified_name("FontStyleData"))
    _write_child_text(
        element,
        "Decorations",
        _decoration_flags_to_text(style.decorations),
    )
    _write_int_child(element, "DecorationsV2", int(style.decorations))
    _write_child_text(element, "FamilyName", style.family_name)
    _write_int_child(element, "Id", style.id)
    _write_float_child(element, "Size", style.size)
    return element


def _decoration_text_to_int(value: str | None) -> int:
    if value is None:
        return 0
    decorations = DraftsmanFontDecoration.NONE
    for part in value.replace("|", ",").split(","):
        token = part.strip().casefold()
        if token == "italic":
            decorations |= DraftsmanFontDecoration.ITALIC
        elif token == "bold":
            decorations |= DraftsmanFontDecoration.BOLD
        elif token == "underline":
            decorations |= DraftsmanFontDecoration.UNDERLINE
        elif token in {"strikeout", "strike out"}:
            decorations |= DraftsmanFontDecoration.STRIKEOUT
    return int(decorations)


def _decoration_flags_to_text(decorations: DraftsmanFontDecoration) -> str:
    if decorations == DraftsmanFontDecoration.NONE:
        return "None"
    names: list[str] = []
    for flag, name in (
        (DraftsmanFontDecoration.ITALIC, "Italic"),
        (DraftsmanFontDecoration.BOLD, "Bold"),
        (DraftsmanFontDecoration.UNDERLINE, "Underline"),
        (DraftsmanFontDecoration.STRIKEOUT, "StrikeOut"),
    ):
        if decorations & flag:
            names.append(name)
    return ", ".join(names)


def _enum_from_xml_text(
    enum_cls: type[_DraftsmanEnumT],
    value: str | None,
) -> _DraftsmanEnumT | None:
    if value is None:
        return None
    for member in enum_cls:
        if str(member.value).casefold() == value.casefold():
            return member
    return None


def _enum_value_or_text(value: Enum | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _style_id_or_default(
    style: DraftsmanFontStyle | int | None,
    default_style_id: int | None,
) -> int | None:
    if isinstance(style, DraftsmanFontStyle):
        return style.id
    if isinstance(style, int):
        return style
    return default_style_id


def _write_note_border_style(
    element: etree._Element,
    style: DraftsmanNoteBorderStyle,
) -> None:
    _write_child_text(element, "BorderStyle", style.legacy_name)
    _write_int_child(element, "BorderStyleV2", int(style))


def _read_color_child(
    element: etree._Element,
    child_local_name: str,
) -> DraftsmanColor | None:
    child = first_child_by_local_name(element, child_local_name)
    if child is None:
        return None
    return DraftsmanColor.from_element(child)


def _write_color_child(
    element: etree._Element,
    child_local_name: str,
    value: DraftsmanColor,
) -> None:
    child = first_child_by_local_name(element, child_local_name)
    if child is None:
        child = etree.SubElement(element, qualified_name(child_local_name))
    value.write_to_element(child)


def _read_point_child(
    element: etree._Element,
    child_local_name: str,
) -> DraftsmanPoint | None:
    child = first_child_by_local_name(element, child_local_name)
    if child is None:
        return None
    x_mm = _read_float_child(child, "X")
    y_mm = _read_float_child(child, "Y")
    if x_mm is None or y_mm is None:
        return None
    return DraftsmanPoint(
        x_mm=draftsman_mm_from_points(x_mm),
        y_mm=draftsman_mm_from_points(y_mm),
    )


def _write_point_child(
    element: etree._Element,
    child_local_name: str,
    value: DraftsmanPoint,
) -> None:
    child = first_child_by_local_name(element, child_local_name)
    if child is None:
        child = etree.SubElement(element, qualified_name(child_local_name))
    _write_float_child(child, "X", draftsman_points_from_mm(value.x_mm))
    _write_float_child(child, "Y", draftsman_points_from_mm(value.y_mm))


def _read_size_child(
    element: etree._Element,
    child_local_name: str,
) -> DraftsmanSize | None:
    child = first_child_by_local_name(element, child_local_name)
    if child is None:
        return None
    width_mm = _read_float_child(child, "Width")
    height_mm = _read_float_child(child, "Height")
    if width_mm is None or height_mm is None:
        return None
    return DraftsmanSize(
        width_mm=draftsman_mm_from_points(width_mm),
        height_mm=draftsman_mm_from_points(height_mm),
    )


def _write_size_child(
    element: etree._Element,
    child_local_name: str,
    value: DraftsmanSize,
) -> None:
    child = first_child_by_local_name(element, child_local_name)
    if child is None:
        child = etree.SubElement(element, qualified_name(child_local_name))
    _write_float_child(child, "Width", draftsman_points_from_mm(value.width_mm))
    _write_float_child(child, "Height", draftsman_points_from_mm(value.height_mm))


def _read_rect_child(
    element: etree._Element,
    child_local_name: str,
) -> DraftsmanRect | None:
    child = first_child_by_local_name(element, child_local_name)
    if child is None:
        return None
    x_mm = _read_float_child(child, "X")
    y_mm = _read_float_child(child, "Y")
    width_mm = _read_float_child(child, "Width")
    height_mm = _read_float_child(child, "Height")
    if x_mm is None or y_mm is None or width_mm is None or height_mm is None:
        return None
    return DraftsmanRect(
        x_mm=draftsman_mm_from_points(x_mm),
        y_mm=draftsman_mm_from_points(y_mm),
        width_mm=draftsman_mm_from_points(width_mm),
        height_mm=draftsman_mm_from_points(height_mm),
    )


def _write_rect_child(
    element: etree._Element,
    child_local_name: str,
    value: DraftsmanRect,
) -> None:
    child = first_child_by_local_name(element, child_local_name)
    if child is None:
        child = etree.SubElement(element, qualified_name(child_local_name))
    _write_float_child(child, "Height", draftsman_points_from_mm(value.height_mm))
    _write_float_child(child, "Width", draftsman_points_from_mm(value.width_mm))
    _write_float_child(child, "X", draftsman_points_from_mm(value.x_mm))
    _write_float_child(child, "Y", draftsman_points_from_mm(value.y_mm))


def _read_margin_child(
    element: etree._Element,
    child_local_name: str,
) -> DraftsmanMargin | None:
    child = first_child_by_local_name(element, child_local_name)
    if child is None:
        return None
    left_mm = _read_float_child(child, "Left")
    top_mm = _read_float_child(child, "Top")
    right_mm = _read_float_child(child, "Right")
    bottom_mm = _read_float_child(child, "Bottom")
    if left_mm is None or top_mm is None or right_mm is None or bottom_mm is None:
        return None
    return DraftsmanMargin(
        left_mm=draftsman_mm_from_points(left_mm),
        top_mm=draftsman_mm_from_points(top_mm),
        right_mm=draftsman_mm_from_points(right_mm),
        bottom_mm=draftsman_mm_from_points(bottom_mm),
    )


def _write_margin_child(
    element: etree._Element,
    child_local_name: str,
    value: DraftsmanMargin,
) -> None:
    child = first_child_by_local_name(element, child_local_name)
    if child is None:
        child = etree.SubElement(element, qualified_name(child_local_name))
    _write_float_child(child, "Bottom", draftsman_points_from_mm(value.bottom_mm))
    _write_float_child(child, "Left", draftsman_points_from_mm(value.left_mm))
    _write_float_child(child, "Right", draftsman_points_from_mm(value.right_mm))
    _write_float_child(child, "Top", draftsman_points_from_mm(value.top_mm))


def _read_bytes_child(element: etree._Element, child_local_name: str) -> bytes:
    child = first_child_by_local_name(element, child_local_name)
    if child is None or is_nil_element(child) or child.text is None:
        return b""
    text = child.text.strip()
    if not text:
        return b""
    return base64.b64decode(text)


def _write_bytes_child(
    element: etree._Element,
    child_local_name: str,
    value: bytes | None,
) -> None:
    child = first_child_by_local_name(element, child_local_name)
    if child is None:
        child = etree.SubElement(element, qualified_name(child_local_name))
    nil_attribute = qualified_name("nil", XML_SCHEMA_INSTANCE_NAMESPACE)
    if value is None:
        child.text = None
        child.set(nil_attribute, "true")
        return
    child.attrib.pop(nil_attribute, None)
    child.text = base64.b64encode(value).decode("ascii")


def _write_picture_bitmap_fields(element: etree._Element, data: bytes) -> None:
    if data.startswith(_PNG_SIGNATURE):
        _write_bytes_child(element, "Bitmap", data)
        _write_bytes_child(element, "Bitmap2", None)
    else:
        _write_bytes_child(element, "Bitmap", _TINY_TRANSPARENT_PNG)
        _write_bytes_child(element, "Bitmap2", data)


def _orthogonal_rotation_name(rotation_degrees: float) -> str:
    normalized = float(rotation_degrees) % 360.0
    if normalized == 90.0:
        return "Ccw90"
    if normalized == 180.0:
        return "Ccw180"
    if normalized == 270.0:
        return "Ccw270"
    return "Ccw0"


def _wrap_page_item(
    element: etree._Element,
    page: AltiumDraftsmanPage,
) -> AltiumDraftsmanItem:
    if element_type(element) == "Note":
        return AltiumDraftsmanNote(element, page)
    if element_type(element) == "Text":
        return AltiumDraftsmanText(element, page)
    if element_type(element) == "Picture":
        return AltiumDraftsmanPicture(element, page)
    return AltiumDraftsmanItem(element, page)


def _blank_profile_xml(profile: str) -> bytes:
    profile_key = profile.strip().lower()
    resource_name = _BLANK_PROFILE_RESOURCES.get(profile_key)
    if resource_name is None:
        supported = ", ".join(sorted(_BLANK_PROFILE_RESOURCES))
        raise ValueError(
            f"Unsupported Draftsman blank profile {profile!r}; use {supported}"
        )
    return resources.files(__package__).joinpath(resource_name).read_bytes()


def _write_parameter_value(
    root: etree._Element,
    parameter_name: str,
    value: str,
) -> None:
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        if element_local_name(element) != "DrawingDocumentParameterData":
            continue
        if child_text(element, "Name") == parameter_name:
            _write_child_text(element, "Value", value)
            return
