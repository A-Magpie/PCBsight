"""XML helpers for Draftsman document trees."""

from __future__ import annotations

from collections.abc import Iterator

import lxml.etree as etree

DRAFTSMAN_V1_NAMESPACE = (
    "http://schemas.datacontract.org/2004/07/"
    "Altium.Designer.PcbDrawing.DataSerialization.V1"
)
XML_SCHEMA_INSTANCE_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"

DRAFTSMAN_NAMESPACE_MAP = {
    "d": DRAFTSMAN_V1_NAMESPACE,
    "i": XML_SCHEMA_INSTANCE_NAMESPACE,
}


def qualified_name(
    local: str,
    namespace: str = DRAFTSMAN_V1_NAMESPACE,
) -> str:
    """Return an expanded XML name for a Draftsman element or attribute."""

    return f"{{{namespace}}}{local}"


def local_name(tag: str) -> str:
    """Return the local part of an expanded XML tag name."""

    return etree.QName(tag).localname


def element_local_name(element: etree._Element) -> str:
    """Return an element tag's local name."""

    return local_name(element.tag)


def element_type(element: etree._Element) -> str | None:
    """Return the schema-instance type assigned to a polymorphic element."""

    return element.get(qualified_name("type", XML_SCHEMA_INSTANCE_NAMESPACE))


def is_nil_element(element: etree._Element) -> bool:
    """Return true when an element carries schema-instance nil=true."""

    return element.get(qualified_name("nil", XML_SCHEMA_INSTANCE_NAMESPACE)) == "true"


def iter_child_elements(element: etree._Element) -> Iterator[etree._Element]:
    """Yield only element children, skipping comments and processing nodes."""

    for child in element:
        if isinstance(child.tag, str):
            yield child


def children_by_local_name(
    element: etree._Element,
    child_local_name: str,
) -> list[etree._Element]:
    """Return direct child elements matching a local name."""

    return [
        child
        for child in iter_child_elements(element)
        if element_local_name(child) == child_local_name
    ]


def first_child_by_local_name(
    element: etree._Element,
    child_local_name: str,
) -> etree._Element | None:
    """Return the first direct child element matching a local name."""

    for child in iter_child_elements(element):
        if element_local_name(child) == child_local_name:
            return child
    return None


def child_text(
    element: etree._Element,
    child_local_name: str,
    default: str | None = None,
) -> str | None:
    """Return text from the first matching direct child element."""

    child = first_child_by_local_name(element, child_local_name)
    if child is None or child.text is None:
        return default
    return child.text
