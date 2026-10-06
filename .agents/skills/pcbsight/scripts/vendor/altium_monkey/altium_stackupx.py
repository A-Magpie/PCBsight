"""Read Altium Layer Stack Manager `.stackupx` XML exports."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, cast
import uuid
from xml.etree import ElementTree as ET
from xml.etree.ElementTree import Element

from .altium_pcb_stream_helpers import format_mil_value as _format_mil_value
from .altium_pcb_layer_ref import PcbLayerRef, PcbLayerResolutionError
from .altium_record_types import PcbLayer
from .altium_text_helpers import normalized_object_text as _value_text

if TYPE_CHECKING:
    from .altium_layer_stack_document import (
        AltiumLayerPair,
        AltiumLayerStackDocument,
        AltiumPhysicalStack,
        AltiumStackLayer,
        AltiumTransmissionLine,
    )

STACKUPX_NAMESPACE = "http://altium.com/ns/LayerStackManager"
_EXPORT_NAMESPACE = uuid.UUID("03df8334-9e7d-5b45-a0d4-a2473a777cde")
_COMPONENT_PLACEMENT_TYPE = (
    "Altium.LayerStackManager.LayerSchema.ComponentPlacement, "
    "Altium.LayerStackManager.Abstractions, Version=1.0.0.0, Culture=neutral, "
    "PublicKeyToken=51600b9dd346ed18"
)

_LENGTH_RE = re.compile(
    r"^\s*(?P<number>[-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?P<unit>mil|mm|um|m)?\s*$",
    re.IGNORECASE,
)


class StackupXLayerType(StrEnum):
    """Well-known Altium Layer Stack Manager layer ``TypeId`` GUIDs.

    Use these as ``StackupXLayer.type_id`` values when authoring stacks so
    callers never need to know the raw GUIDs. Source-backed from the AD
    ``Altium.LayerStackManager.LayerSchema`` constants.
    """

    ABSTRACT = "5ee2359d-ad77-4dad-bd77-5a2334695235"
    UNKNOWN = "4af561f4-6919-43d8-942a-d3579b371c26"
    PHYSICAL = "daa9eccc-0064-462d-854e-0b9e829ef563"
    BASE_DIELECTRIC = "56eb1ca5-014a-4387-a43e-1485f44f081e"
    OVERLAY = "c7ef040e-8d00-490b-b00c-a7e7823ff174"
    BIKINI_COVERLAY = "8de65238-89ed-47c5-bbf8-0f6f02d1899c"
    PASTE_MASK = "e6a36257-b90f-45e5-9f8d-a213ecdb687c"
    MECHANICAL = "7a47e6b3-b591-41f7-a27b-8c6b0477e458"
    SOLDER_MASK = "7b384237-13d8-4318-8bcb-accd8d9a51e7"
    COPPER_FOIL = "31e48829-e750-4c28-95e0-1a8313f0158e"
    COPPER_SIGNAL = "f4eccd87-2cfb-4f37-be50-4f3a272b4d01"
    INTERNAL_PLANE = "f59fab94-c5ed-467d-94cd-f60a323c5d5b"
    SURFACE_FINISH = "b0827674-798c-4cf8-807c-8e6c2a11c145"
    DIELECTRIC = "92b02d5e-8d69-48a8-880e-ac4b77db099d"
    PREPREG = "1a79611a-039d-4d40-a204-53c26c50f8b5"
    CORE = "136c62ef-1fa6-4897-ae71-7e797b632b92"
    PLATING = "90b89aa0-a48a-45f4-82f5-b3eca4ec8cce"
    ADHESIVE = "448f9952-79ba-41d8-a8f4-4713ee7a3828"
    STIFFENER = "9fd889fa-c97a-401c-a066-e5f746678381"
    MISC = "786b5f28-f093-4084-bba7-46e8f4f24f55"
    MARKING = "886956f5-b2e9-4114-93f3-f69af872bffb"
    PRINTED_ELECTRONICS_LAYER = "8053a284-8886-43e8-baa0-5aa1d8de97d1"
    PRINTED_ELECTRONICS_CONDUCTIVE = "c558fe3f-d914-4761-9d82-a12591ceb133"
    PRINTED_ELECTRONICS_NONCONDUCTIVE = "18be3c27-bfeb-4bba-8e9a-df4caeee1731"
    PRINTED_ELECTRONICS_ISOLATION = "8590cc63-42d8-48cb-8307-72991910bcc9"
    PRINTED_ELECTRONICS_CORE = "e9a93ad8-17ee-438c-a1b6-92a3995dfda8"
    PRINTED_ELECTRONICS_PREPREG = "743de2f9-2902-4e35-b39c-bacd385f87dd"


class StackupXFeatureId(StrEnum):
    """Known Altium Layer Stack Manager feature GUIDs for ``StackupXFeature.id``."""

    STANDARD_STACKUP = "c8939e8a-fd0e-4d52-8860-b7a98f452016"
    IMPEDANCE_CALCULATOR = "e3df2b86-5f1b-49ca-b266-d1ae57f0ba6f"
    RIGID_FLEX = "5277e6a4-9e5f-4f54-951f-dc18cfeb7530"
    RIGID_FLEX_ADVANCED = "4e697926-85c3-4c60-8b9b-e23ccb226764"
    BACK_DRILLS = "0a82ba33-e4d8-43f3-9c01-412dc26bdd5e"


_LAYER_TYPE_FAMILIES = {
    "5ee2359d-ad77-4dad-bd77-5a2334695235": "abstract",
    "4af561f4-6919-43d8-942a-d3579b371c26": "unknown",
    "daa9eccc-0064-462d-854e-0b9e829ef563": "physical",
    "56eb1ca5-014a-4387-a43e-1485f44f081e": "dielectric",
    "c7ef040e-8d00-490b-b00c-a7e7823ff174": "overlay",
    "8de65238-89ed-47c5-bbf8-0f6f02d1899c": "overlay",
    "e6a36257-b90f-45e5-9f8d-a213ecdb687c": "paste",
    "7a47e6b3-b591-41f7-a27b-8c6b0477e458": "mechanical",
    "7b384237-13d8-4318-8bcb-accd8d9a51e7": "solder_mask",
    "31e48829-e750-4c28-95e0-1a8313f0158e": "copper",
    "f4eccd87-2cfb-4f37-be50-4f3a272b4d01": "copper",
    "f59fab94-c5ed-467d-94cd-f60a323c5d5b": "copper",
    "b0827674-798c-4cf8-807c-8e6c2a11c145": "surface_finish",
    "92b02d5e-8d69-48a8-880e-ac4b77db099d": "dielectric",
    "1a79611a-039d-4d40-a204-53c26c50f8b5": "dielectric",
    "136c62ef-1fa6-4897-ae71-7e797b632b92": "dielectric",
    "90b89aa0-a48a-45f4-82f5-b3eca4ec8cce": "plating",
    "448f9952-79ba-41d8-a8f4-4713ee7a3828": "adhesive",
    "9fd889fa-c97a-401c-a066-e5f746678381": "stiffener",
    "786b5f28-f093-4084-bba7-46e8f4f24f55": "misc",
    "886956f5-b2e9-4114-93f3-f69af872bffb": "marking",
    "8053a284-8886-43e8-baa0-5aa1d8de97d1": "pe_layer",
    "c558fe3f-d914-4761-9d82-a12591ceb133": "pe_conductive",
    "18be3c27-bfeb-4bba-8e9a-df4caeee1731": "pe_nonconductive",
    "8590cc63-42d8-48cb-8307-72991910bcc9": "pe_isolation",
    "e9a93ad8-17ee-438c-a1b6-92a3995dfda8": "pe_core",
    "743de2f9-2902-4e35-b39c-bacd385f87dd": "pe_prepreg",
}
_INTERNAL_PLANE_TYPE_IDS = {
    "f59fab94-c5ed-467d-94cd-f60a323c5d5b",
}
_COPPER_SIGNAL_TYPE_ID = "f4eccd87-2cfb-4f37-be50-4f3a272b4d01"
_COPPER_PLANE_TYPE_ID = "f59fab94-c5ed-467d-94cd-f60a323c5d5b"
_SURFACE_FINISH_TYPE_ID = "b0827674-798c-4cf8-807c-8e6c2a11c145"
_PLATING_TYPE_ID = "90b89aa0-a48a-45f4-82f5-b3eca4ec8cce"
_ADHESIVE_TYPE_ID = "448f9952-79ba-41d8-a8f4-4713ee7a3828"
_STIFFENER_TYPE_ID = "9fd889fa-c97a-401c-a066-e5f746678381"
_MISC_TYPE_ID = "786b5f28-f093-4084-bba7-46e8f4f24f55"
_MARKING_TYPE_ID = "886956f5-b2e9-4114-93f3-f69af872bffb"
_FEATURE_ID_STANDARD = "c8939e8a-fd0e-4d52-8860-b7a98f452016"
_FEATURE_ID_RIGID_FLEX = "5277e6a4-9e5f-4f54-951f-dc18cfeb7530"
_FEATURE_ID_RIGID_FLEX_ADVANCED = "4e697926-85c3-4c60-8b9b-e23ccb226764"
_FEATURE_ID_IMPEDANCE = "e3df2b86-5f1b-49ca-b266-d1ae57f0ba6f"


def _generated_stackupx_id() -> str:
    """Fresh random GUID id for newly authored StackUpX objects.

    Altium's Layer Stack Manager parses stack, layer, and span ids with
    ``Guid.TryParse`` and raises ``Invalid GUID string`` on anything else, so
    authored ids default to generated GUIDs instead of caller-invented text.
    Parse paths keep ids read from existing files verbatim.
    """
    return str(uuid.uuid4())


@dataclass(frozen=True)
class StackupXFeature:
    id: str
    name: str

    def to_debug_json(self) -> dict[str, object]:
        return {"id": self.id, "name": self.name}


@dataclass(frozen=True)
class StackupXProperty:
    name: str
    type_name: str
    value: str

    @property
    def length_mils(self) -> float | None:
        return _parse_length_to_mils(self.value)

    def to_debug_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "type": self.type_name,
            "value": self.value,
            "length_mils": self.length_mils,
        }


@dataclass(frozen=True)
class StackupXLayer:
    id: str = field(default_factory=_generated_stackupx_id, kw_only=True)
    type_id: str
    name: str
    is_shared: bool | None
    is_enabled: bool | None
    is_stiffener: bool | None
    is_adhesive: bool | None
    properties: tuple[StackupXProperty, ...]
    raw_attributes: tuple[tuple[str, str], ...]

    @property
    def family(self) -> str:
        return _LAYER_TYPE_FAMILIES.get(self.type_id.lower(), "unknown")

    @property
    def property_map(self) -> dict[str, StackupXProperty]:
        return {prop.name: prop for prop in self.properties}

    @property
    def thickness_mils(self) -> float | None:
        prop = self.property_map.get("Thickness")
        return prop.length_mils if prop is not None else None

    def property_value(self, name: str) -> str:
        prop = self.property_map.get(name)
        return prop.value if prop is not None else ""

    def to_debug_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "type_id": self.type_id,
            "name": self.name,
            "family": self.family,
            "is_shared": self.is_shared,
            "is_enabled": self.is_enabled,
            "is_stiffener": self.is_stiffener,
            "is_adhesive": self.is_adhesive,
            "thickness_mils": self.thickness_mils,
            "properties": [prop.to_debug_json() for prop in self.properties],
            "raw_attributes": list(self.raw_attributes),
        }


@dataclass(frozen=True)
class StackupXSpan:
    id: str = field(default_factory=_generated_stackupx_id, kw_only=True)
    auto_name: str
    span_type: str
    start_layer_id: str
    stop_layer_id: str
    raw_attributes: tuple[tuple[str, str], ...]

    def to_debug_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "auto_name": self.auto_name,
            "type": self.span_type,
            "start_layer_id": self.start_layer_id,
            "stop_layer_id": self.stop_layer_id,
            "raw_attributes": list(self.raw_attributes),
        }


@dataclass(frozen=True)
class StackupXTransmissionLine:
    layer_id: str
    stack_id: str
    is_differential: bool | None
    tl_type_name: str
    calc_mode: str
    top_ref_layer_id: str
    bottom_ref_layer_id: str
    trace_width: str
    trace_gap: str
    impedance_error: str
    use_solder_mask: bool | None
    coated_height_1: str
    coated_height_2: str
    clearance_to_plane: str
    electric_parameters: tuple[tuple[str, str], ...]
    raw_attributes: tuple[tuple[str, str], ...]

    def to_debug_json(self) -> dict[str, object]:
        return {
            "layer_id": self.layer_id,
            "stack_id": self.stack_id,
            "is_differential": self.is_differential,
            "tl_type_name": self.tl_type_name,
            "calc_mode": self.calc_mode,
            "top_ref_layer_id": self.top_ref_layer_id,
            "bottom_ref_layer_id": self.bottom_ref_layer_id,
            "trace_width": self.trace_width,
            "trace_gap": self.trace_gap,
            "impedance_error": self.impedance_error,
            "use_solder_mask": self.use_solder_mask,
            "coated_height_1": self.coated_height_1,
            "coated_height_2": self.coated_height_2,
            "clearance_to_plane": self.clearance_to_plane,
            "electric_parameters": [list(item) for item in self.electric_parameters],
            "raw_attributes": list(self.raw_attributes),
        }


@dataclass(frozen=True)
class StackupXImpedanceProfile:
    id: str = field(default_factory=_generated_stackupx_id, kw_only=True)
    auto_name: str
    profile_type: str
    impedance: str
    tolerance: str
    transmission_lines: tuple[StackupXTransmissionLine, ...]
    raw_attributes: tuple[tuple[str, str], ...]

    @property
    def transmission_line_count(self) -> int:
        return len(self.transmission_lines)

    def to_debug_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "auto_name": self.auto_name,
            "type": self.profile_type,
            "impedance": self.impedance,
            "tolerance": self.tolerance,
            "transmission_line_count": self.transmission_line_count,
            "transmission_lines": [
                line.to_debug_json() for line in self.transmission_lines
            ],
            "raw_attributes": list(self.raw_attributes),
        }


@dataclass(frozen=True)
class StackupXBranchSectionStack:
    id: str = field(default_factory=_generated_stackupx_id, kw_only=True)
    layer_stack_id: str
    description: str
    material_usage: str
    source: str
    parent_layer_id: str
    parent_layer_stack_id: str
    source_layer_id: str
    source_layer_stack_id: str
    is_left_intrusions_linked: bool | None
    intrusion_left_bottom: str
    intrusion_left_top: str
    is_right_intrusions_linked: bool | None
    intrusion_right_bottom: str
    intrusion_right_top: str
    raw_attributes: tuple[tuple[str, str], ...]

    def to_debug_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "layer_stack_id": self.layer_stack_id,
            "description": self.description,
            "material_usage": self.material_usage,
            "source": self.source,
            "parent_layer_id": self.parent_layer_id,
            "parent_layer_stack_id": self.parent_layer_stack_id,
            "source_layer_id": self.source_layer_id,
            "source_layer_stack_id": self.source_layer_stack_id,
            "is_left_intrusions_linked": self.is_left_intrusions_linked,
            "intrusion_left_bottom": self.intrusion_left_bottom,
            "intrusion_left_top": self.intrusion_left_top,
            "is_right_intrusions_linked": self.is_right_intrusions_linked,
            "intrusion_right_bottom": self.intrusion_right_bottom,
            "intrusion_right_top": self.intrusion_right_top,
            "raw_attributes": list(self.raw_attributes),
        }


@dataclass(frozen=True)
class StackupXBranchSection:
    id: str = field(default_factory=_generated_stackupx_id, kw_only=True)
    name: str
    parent_section_id: str
    stacks: tuple[StackupXBranchSectionStack, ...]
    raw_attributes: tuple[tuple[str, str], ...]

    def to_debug_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "parent_section_id": self.parent_section_id,
            "stacks": [stack.to_debug_json() for stack in self.stacks],
            "raw_attributes": list(self.raw_attributes),
        }


@dataclass(frozen=True)
class StackupXBranch:
    id: str = field(default_factory=_generated_stackupx_id, kw_only=True)
    name: str
    description: str
    parent_branch_id: str
    sections: tuple[StackupXBranchSection, ...]
    raw_attributes: tuple[tuple[str, str], ...]

    def to_debug_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "parent_branch_id": self.parent_branch_id,
            "sections": [section.to_debug_json() for section in self.sections],
            "raw_attributes": list(self.raw_attributes),
        }


@dataclass(frozen=True)
class StackupXStack:
    id: str = field(default_factory=_generated_stackupx_id, kw_only=True)
    name: str
    stack_type: str
    is_flex: bool | None
    is_symmetric: bool | None
    template_id: str
    layers: tuple[StackupXLayer, ...]
    via_spans: tuple[StackupXSpan, ...]
    drill_spans: tuple[StackupXSpan, ...]
    raw_attributes: tuple[tuple[str, str], ...]

    @property
    def layer_names(self) -> tuple[str, ...]:
        return tuple(layer.name for layer in self.layers)

    @property
    def copper_layer_names(self) -> tuple[str, ...]:
        return tuple(layer.name for layer in self.layers if layer.family == "copper")

    def to_debug_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.stack_type,
            "is_flex": self.is_flex,
            "is_symmetric": self.is_symmetric,
            "template_id": self.template_id,
            "layer_names": list(self.layer_names),
            "copper_layer_names": list(self.copper_layer_names),
            "layers": [layer.to_debug_json() for layer in self.layers],
            "via_spans": [span.to_debug_json() for span in self.via_spans],
            "drill_spans": [span.to_debug_json() for span in self.drill_spans],
            "raw_attributes": list(self.raw_attributes),
        }


@dataclass(frozen=True)
class AltiumStackupXDocument:
    serializer_version: str
    version: str
    id: str = field(default_factory=_generated_stackupx_id, kw_only=True)
    revision_id: str = field(default_factory=_generated_stackupx_id, kw_only=True)
    revision_date: str
    features: tuple[StackupXFeature, ...]
    stackup_attributes: tuple[tuple[str, str], ...]
    stacks: tuple[StackupXStack, ...]
    branches: tuple[StackupXBranch, ...]
    impedance_profiles: tuple[StackupXImpedanceProfile, ...] = ()
    source_path: str = ""

    @classmethod
    def from_layer_stack_document(
        cls,
        document: "AltiumLayerStackDocument",
    ) -> "AltiumStackupXDocument":
        """
        Build a rigid `.stackupx` interchange document from the common model.
        """
        stackups = _export_stacks(document)
        primary_stack = stackups[0]
        source_features = tuple(getattr(document, "stackupx_features", ()) or ())
        return cls(
            serializer_version=(
                _value_text(getattr(document, "stackupx_serializer_version", ""))
                or "1.1.0.0"
            ),
            version=_value_text(getattr(document, "stackupx_version", "")) or "2.1.0.0",
            id=_existing_or_stackupx_guid(
                getattr(document, "stackupx_document_id", ""),
                document.active_stack_ref,
                primary_stack.id,
            ),
            revision_id=_existing_or_stackupx_guid(
                getattr(document, "stackupx_revision_id", ""),
                "revision",
                primary_stack.id,
            ),
            revision_date=(
                _value_text(getattr(document, "stackupx_revision_date", ""))
                or "2000-01-01T00:00:00.0000000Z"
            ),
            features=(
                tuple(
                    StackupXFeature(id=str(item[0]), name=str(item[1]))
                    for item in cast(Iterable[tuple[object, object]], source_features)
                )
                if source_features
                else _export_features(
                    rigid_flex_mode=str(getattr(document, "rigid_flex_mode", "") or ""),
                    stackups=stackups,
                )
            ),
            stackup_attributes=tuple(getattr(document, "stackup_attributes", ()) or ())
            or _default_stackup_attributes(),
            stacks=stackups,
            branches=_export_branches(
                stackups=stackups,
                rigid_flex_mode=str(getattr(document, "rigid_flex_mode", "") or ""),
                source_branches=tuple(getattr(document, "branches", ()) or ()),
            ),
            impedance_profiles=_export_impedance_profiles(
                profiles=tuple(document.impedance_profiles or ()),
                transmission_lines=tuple(document.transmission_lines or ()),
                stackups=stackups,
            ),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "AltiumStackupXDocument":
        source = Path(path)
        return cls.from_bytes(source.read_bytes(), source_path=str(source))

    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        source_path: str = "",
    ) -> "AltiumStackupXDocument":
        return cls.from_bytes(text.encode("utf-8"), source_path=source_path)

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        *,
        source_path: str = "",
    ) -> "AltiumStackupXDocument":
        root = ET.fromstring(data)
        if _local_name(root.tag) != "StackupDocument":
            raise ValueError(
                f"Expected StackupDocument root, got {_local_name(root.tag)}"
            )

        stackup = _required_child(root, "Stackup")
        return cls(
            serializer_version=_attribute(root, "SerializerVersion"),
            version=_attribute(root, "Version"),
            id=_attribute(root, "Id"),
            revision_id=_attribute(root, "RevisionId"),
            revision_date=_attribute(root, "RevisionDate"),
            features=_parse_features(root),
            stackup_attributes=_raw_attributes(stackup),
            stacks=_parse_stacks(stackup),
            branches=_parse_branches(stackup),
            impedance_profiles=_parse_impedance_profiles(stackup),
            source_path=source_path,
        )

    @property
    def stack_names(self) -> tuple[str, ...]:
        return tuple(stack.name for stack in self.stacks)

    def stack_by_name(self, name: str) -> StackupXStack | None:
        for stack in self.stacks:
            if stack.name == name:
                return stack
        return None

    def to_text(self) -> str:
        """
        Serialize this document as Altium `.stackupx` XML.
        """
        ET.register_namespace("", STACKUPX_NAMESPACE)
        root = _to_xml_element(self)
        ET.indent(root, space="  ")
        return ET.tostring(root, encoding="unicode", short_empty_elements=True) + "\n"

    def to_bytes(self) -> bytes:
        """
        Serialize this document as UTF-8 `.stackupx` XML bytes.
        """
        return self.to_text().encode("utf-8")

    def write(self, path: str | Path) -> Path:
        """
        Write this document to `path` and return the resolved path object.
        """
        target = Path(path)
        target.write_bytes(self.to_bytes())
        return target

    def to_layer_stack_document(self) -> "AltiumLayerStackDocument":
        """
        Build an `AltiumLayerStackDocument` view from proven StackupX fields.

        This bridge represents the Layer Stack Manager interchange view only.
        Current `.stackupx` exports omit PcbDoc paste rows and do not contain
        `BoardRegions/Data` bend-line geometry, so this method does not try to
        synthesize those PcbDoc-only records.

        Raises ``ValueError`` when stack, layer, or span ids are not GUID
        strings; boards written with such ids crash Altium's Layer Stack
        Manager. Use the generated id defaults or supply GUID strings.
        """
        _guard_board_writable_ids(self)
        from .altium_layer_stack_document import (
            AltiumImpedanceProfile,
            AltiumLayerRegistry,
            AltiumLayerRegistryEntry,
            AltiumLayerStackDocument,
            AltiumLayerStackSourceMap,
            AltiumPhysicalStack,
            AltiumStackBranch,
            AltiumStackBranchSection,
            AltiumStackBranchStack,
            AltiumStackSubstack,
        )

        registry_entries_by_ref: dict[str, AltiumLayerRegistryEntry] = {}
        physical_stacks: list[AltiumPhysicalStack] = []
        substacks: list[AltiumStackSubstack] = []
        layer_pairs: list[AltiumLayerPair] = []
        layer_tokens_by_id: dict[str, str] = {}

        for stack_index, stack in enumerate(self.stacks):
            stack_layer_tokens_by_id = _layer_tokens_by_stack_order(stack.layers)
            layer_tokens_by_id.update(stack_layer_tokens_by_id)
            stack_layers: list[AltiumStackLayer] = []
            for layer_index, layer in enumerate(stack.layers):
                registry_ref = _registry_ref(layer.id)
                standard_token = stack_layer_tokens_by_id.get(
                    layer.id,
                    _standard_token_from_layer_name(layer.name),
                )
                legacy_layer_id = _legacy_layer_id_from_stackupx_token(
                    standard_token or ""
                )
                v7_saved_layer_id = _v7_saved_layer_id_from_stackupx_token(
                    standard_token or ""
                )
                if registry_ref not in registry_entries_by_ref:
                    registry_entries_by_ref[registry_ref] = AltiumLayerRegistryEntry(
                        model_id=registry_ref,
                        display_name=layer.name,
                        family=layer.family,
                        standard_token=standard_token,
                        legacy_layer_id=legacy_layer_id,
                        v7_layer_id=v7_saved_layer_id,
                        source_layer_id=v7_saved_layer_id,
                        source_record_id=layer.id,
                        side=_side_from_layer_name(layer.name),
                        enabled=_stackupx_layer_enabled(layer),
                        used_by_primitives=_stackupx_layer_used_by_primitives(layer),
                        source_aliases=(f"stackupx:{layer.type_id}",),
                    )
                stack_layers.append(
                    _to_stack_layer(
                        layer,
                        layer_index,
                        registry_ref,
                        stack_ref=stack.id,
                        layer_token=standard_token or "",
                    )
                )

            physical_stacks.append(
                AltiumPhysicalStack(
                    stack_ref=stack.id,
                    display_name=stack.name,
                    source_family="stackupx",
                    is_flex=stack.is_flex,
                    stackupx_is_flex=stack.is_flex,
                    stackupx_stack_type=stack.stack_type,
                    layers=tuple(stack_layers),
                )
            )
            substacks.append(
                AltiumStackSubstack(
                    index=stack_index,
                    field_family="stackupx",
                    source_stackup_ref=stack.id,
                    name=stack.name,
                    is_flex=stack.is_flex,
                    raw_stackup_type=stack.stack_type,
                    stackupx_is_flex=stack.is_flex,
                    stackupx_stack_type=stack.stack_type,
                    enabled_layer_refs=tuple(
                        stack_layer.registry_ref
                        for source_layer, stack_layer in zip(stack.layers, stack_layers)
                        if source_layer.is_enabled is not False
                    ),
                )
            )
            layer_pairs.extend(
                _to_layer_pairs(
                    stack,
                    pair_index_start=len(layer_pairs),
                    layer_tokens_by_id=stack_layer_tokens_by_id,
                )
            )

        return AltiumLayerStackDocument(
            layer_registry=AltiumLayerRegistry(
                entries=tuple(
                    registry_entries_by_ref[key]
                    for key in sorted(registry_entries_by_ref)
                )
            ),
            physical_stacks=tuple(physical_stacks),
            active_stack_ref=self.id,
            rigid_flex_mode=_rigid_flex_mode_from_feature_names(
                tuple(feature.name for feature in self.features)
            ),
            substacks=tuple(substacks),
            branches=tuple(
                AltiumStackBranch(
                    source_branch_id=branch.id,
                    name=branch.name,
                    description=branch.description,
                    parent_branch_id=branch.parent_branch_id,
                    sections=tuple(
                        AltiumStackBranchSection(
                            source_section_id=section.id,
                            name=section.name,
                            parent_section_id=section.parent_section_id,
                            stacks=tuple(
                                AltiumStackBranchStack(
                                    source_record_id=stack.id,
                                    source_stack_ref=stack.layer_stack_id,
                                    description=stack.description,
                                    material_usage=stack.material_usage,
                                    source=stack.source,
                                    parent_layer_id=stack.parent_layer_id,
                                    parent_layer_stack_id=stack.parent_layer_stack_id,
                                    source_layer_id=stack.source_layer_id,
                                    source_layer_stack_id=stack.source_layer_stack_id,
                                    is_left_intrusions_linked=(
                                        stack.is_left_intrusions_linked
                                    ),
                                    intrusion_left_bottom=stack.intrusion_left_bottom,
                                    intrusion_left_top=stack.intrusion_left_top,
                                    is_right_intrusions_linked=(
                                        stack.is_right_intrusions_linked
                                    ),
                                    intrusion_right_bottom=stack.intrusion_right_bottom,
                                    intrusion_right_top=stack.intrusion_right_top,
                                )
                                for stack in section.stacks
                            ),
                        )
                        for section in branch.sections
                    ),
                )
                for branch in self.branches
            ),
            layer_pairs=tuple(layer_pairs),
            impedance_profiles=tuple(
                AltiumImpedanceProfile(
                    index=index,
                    source_family="stackupx",
                    source_record_id=profile.id,
                    name=profile.auto_name,
                    profile_type=profile.profile_type,
                    impedance=profile.impedance,
                    tolerance=profile.tolerance,
                    transmission_line_count=profile.transmission_line_count,
                )
                for index, profile in enumerate(self.impedance_profiles)
            ),
            transmission_lines=tuple(
                _to_transmission_lines(
                    self.impedance_profiles,
                    layer_tokens_by_id=layer_tokens_by_id,
                )
            ),
            stackup_attributes=self.stackup_attributes,
            stackupx_serializer_version=self.serializer_version,
            stackupx_version=self.version,
            stackupx_document_id=self.id,
            stackupx_revision_id=self.revision_id,
            stackupx_revision_date=self.revision_date,
            stackupx_features=tuple(
                (feature.id, feature.name) for feature in self.features
            ),
            source=AltiumLayerStackSourceMap(
                origin="stackupx",
                board_record=(
                    ("STACKUPX_DOCUMENT_ID", self.id),
                    ("STACKUPX_REVISION_ID", self.revision_id),
                    ("STACKUPX_REVISION_DATE", self.revision_date),
                    ("STACKUPX_SERIALIZER_VERSION", self.serializer_version),
                    ("STACKUPX_VERSION", self.version),
                ),
                board_region_count=0,
            ),
        )

    def to_debug_json(self) -> dict[str, object]:
        return {
            "source_path": self.source_path,
            "serializer_version": self.serializer_version,
            "version": self.version,
            "id": self.id,
            "revision_id": self.revision_id,
            "revision_date": self.revision_date,
            "features": [feature.to_debug_json() for feature in self.features],
            "stackup_attributes": list(self.stackup_attributes),
            "stack_names": list(self.stack_names),
            "stacks": [stack.to_debug_json() for stack in self.stacks],
            "branches": [branch.to_debug_json() for branch in self.branches],
            "impedance_profiles": [
                profile.to_debug_json() for profile in self.impedance_profiles
            ],
        }


def _parse_features(root: Element) -> tuple[StackupXFeature, ...]:
    feature_set = _optional_child(root, "FeatureSet")
    if feature_set is None:
        return ()
    return tuple(
        StackupXFeature(
            id=_attribute(feature, "Id"),
            name=_text(feature),
        )
        for feature in _children(feature_set, "Feature")
    )


def _export_features(
    *,
    rigid_flex_mode: str,
    stackups: tuple[StackupXStack, ...],
) -> tuple[StackupXFeature, ...]:
    features = [
        StackupXFeature(id=_FEATURE_ID_STANDARD, name="Standard Stackup"),
    ]
    mode = _normalized_rigid_flex_mode(rigid_flex_mode)
    if mode == "none" and any(bool(stack.is_flex) for stack in stackups):
        mode = "standard"
    if mode == "advanced":
        features.append(
            StackupXFeature(
                id=_FEATURE_ID_RIGID_FLEX_ADVANCED,
                name="Rigid/Flex (Advanced)",
            )
        )
        features.append(
            StackupXFeature(id=_FEATURE_ID_IMPEDANCE, name="Impedance Calculator")
        )
        features.append(StackupXFeature(id=_FEATURE_ID_RIGID_FLEX, name="Rigid/Flex"))
    elif mode == "standard":
        features.append(StackupXFeature(id=_FEATURE_ID_RIGID_FLEX, name="Rigid/Flex"))
        features.append(
            StackupXFeature(id=_FEATURE_ID_IMPEDANCE, name="Impedance Calculator")
        )
    else:
        features.append(
            StackupXFeature(id=_FEATURE_ID_IMPEDANCE, name="Impedance Calculator")
        )
    return tuple(features)


def _rigid_flex_mode_from_feature_names(feature_names: tuple[str, ...]) -> str:
    normalized = tuple(str(name or "").strip().upper() for name in feature_names)
    if any("RIGID/FLEX (ADVANCED)" in name for name in normalized):
        return "advanced"
    if any(name == "RIGID/FLEX" for name in normalized):
        return "standard"
    return "none"


def _normalized_rigid_flex_mode(value: str) -> str:
    token = str(value or "").strip().lower()
    allowed_modes = frozenset(("advanced", "standard"))
    return token if token in allowed_modes else "none"


def _parse_stacks(stackup: Element) -> tuple[StackupXStack, ...]:
    stacks_element = _optional_child(stackup, "Stacks")
    if stacks_element is None:
        return ()
    return tuple(_parse_stack(stack) for stack in _children(stacks_element, "Stack"))


def _parse_stack(stack: Element) -> StackupXStack:
    return StackupXStack(
        id=_attribute(stack, "Id"),
        name=_attribute(stack, "Name"),
        stack_type=_attribute(stack, "Type"),
        is_flex=_optional_bool(_attribute(stack, "IsFlex")),
        is_symmetric=_optional_bool(_attribute(stack, "IsSymmetric")),
        template_id=_attribute(stack, "TemplateId"),
        layers=_parse_layers(stack),
        via_spans=_parse_spans(stack, "ViaSpans", "ViaSpan"),
        drill_spans=_parse_spans(stack, "DrillSpans", "DrillSpan"),
        raw_attributes=_raw_attributes(stack),
    )


def _parse_layers(stack: Element) -> tuple[StackupXLayer, ...]:
    layers_element = _optional_child(stack, "Layers")
    if layers_element is None:
        return ()
    return tuple(_parse_layer(layer) for layer in _children(layers_element, "Layer"))


def _parse_layer(layer: Element) -> StackupXLayer:
    return StackupXLayer(
        id=_attribute(layer, "Id"),
        type_id=_attribute(layer, "TypeId"),
        name=_attribute(layer, "Name"),
        is_shared=_optional_bool(_attribute(layer, "IsShared")),
        is_enabled=_optional_bool(_attribute(layer, "IsEnabled")),
        is_stiffener=_optional_bool(_attribute(layer, "IsStiffener")),
        is_adhesive=_optional_bool(_attribute(layer, "IsAdhesive")),
        properties=_parse_properties(layer),
        raw_attributes=_raw_attributes(layer),
    )


def _parse_properties(layer: Element) -> tuple[StackupXProperty, ...]:
    properties_element = _optional_child(layer, "Properties")
    if properties_element is None:
        return ()
    return tuple(
        StackupXProperty(
            name=_attribute(prop, "Name"),
            type_name=_attribute(prop, "Type"),
            value=_text(prop),
        )
        for prop in _children(properties_element, "Property")
    )


def _parse_spans(
    stack: Element, group_name: str, row_name: str
) -> tuple[StackupXSpan, ...]:
    spans_element = _optional_child(stack, group_name)
    if spans_element is None:
        return ()
    return tuple(
        StackupXSpan(
            id=_attribute(span, "Id"),
            auto_name=_attribute(span, "AutoName"),
            span_type=_attribute(span, "Type"),
            start_layer_id=_attribute(span, "StartLayerId"),
            stop_layer_id=_attribute(span, "StopLayerId"),
            raw_attributes=_raw_attributes(span),
        )
        for span in _children(spans_element, row_name)
    )


def _parse_impedance_profiles(
    stackup: Element,
) -> tuple[StackupXImpedanceProfile, ...]:
    profiles_element = _optional_child(stackup, "ImpedanceProfiles")
    if profiles_element is None:
        return ()
    return tuple(
        StackupXImpedanceProfile(
            id=_attribute(profile, "Id"),
            auto_name=_attribute(profile, "AutoName"),
            profile_type=_attribute(profile, "Type"),
            impedance=_attribute(profile, "Impedance"),
            tolerance=_attribute(profile, "Tolerance"),
            transmission_lines=_parse_transmission_lines(profile),
            raw_attributes=_raw_attributes(profile),
        )
        for profile in _children(profiles_element, "ImpedanceProfile")
    )


def _parse_transmission_lines(
    profile: Element,
) -> tuple[StackupXTransmissionLine, ...]:
    lines_element = _optional_child(profile, "TransmissionLines")
    if lines_element is None:
        return ()
    return tuple(
        StackupXTransmissionLine(
            layer_id=_attribute(line, "LayerId"),
            stack_id=_attribute(line, "StackId"),
            is_differential=_optional_bool(_attribute(line, "IsDifferential")),
            tl_type_name=_attribute(line, "TLTypeName"),
            calc_mode=_attribute(line, "CalcMode"),
            top_ref_layer_id=_attribute(line, "TopRefLayerId"),
            bottom_ref_layer_id=_attribute(line, "BottomRefLayerId"),
            trace_width=_attribute(line, "TraceWidth"),
            trace_gap=_attribute(line, "TraceGap"),
            impedance_error=_attribute(line, "ImpedanceError"),
            use_solder_mask=_optional_bool(_attribute(line, "UseSolderMask")),
            coated_height_1=_attribute(line, "CoatedHeight1"),
            coated_height_2=_attribute(line, "CoatedHeight2"),
            clearance_to_plane=_attribute(line, "ClearanceToPlane"),
            electric_parameters=_parse_electric_parameters(line),
            raw_attributes=_raw_attributes(line),
        )
        for line in _children(lines_element, "TransmissionLine")
    )


def _parse_electric_parameters(line: Element) -> tuple[tuple[str, str], ...]:
    electric = _optional_child(line, "ElectricParameters")
    if electric is None:
        return ()
    return _raw_attributes(electric)


def _parse_branches(stackup: Element) -> tuple[StackupXBranch, ...]:
    branches_element = _optional_child(stackup, "Branches")
    if branches_element is None:
        return ()
    return tuple(
        StackupXBranch(
            id=_attribute(branch, "Id"),
            name=_attribute(branch, "Name"),
            description=_attribute(branch, "Description"),
            parent_branch_id=_attribute(branch, "ParentBranchId"),
            sections=_parse_branch_sections(branch),
            raw_attributes=_raw_attributes(branch),
        )
        for branch in _children(branches_element, "Branch")
    )


def _parse_branch_sections(branch: Element) -> tuple[StackupXBranchSection, ...]:
    sections_element = _optional_child(branch, "Sections")
    if sections_element is None:
        return ()
    return tuple(
        StackupXBranchSection(
            id=_attribute(section, "Id"),
            name=_attribute(section, "Name"),
            parent_section_id=_attribute(section, "ParentSectionId"),
            stacks=_parse_branch_section_stacks(section),
            raw_attributes=_raw_attributes(section),
        )
        for section in _children(sections_element, "BranchSection")
    )


def _parse_branch_section_stacks(
    section: Element,
) -> tuple[StackupXBranchSectionStack, ...]:
    stacks_element = _optional_child(section, "Stacks")
    if stacks_element is None:
        return ()
    return tuple(
        StackupXBranchSectionStack(
            id=_attribute(stack, "Id"),
            layer_stack_id=_attribute(stack, "LayerStackId"),
            description=_attribute(stack, "Description"),
            material_usage=_attribute(stack, "MaterialUsage"),
            source=_attribute(stack, "Source"),
            parent_layer_id=_attribute(stack, "ParentLayerId"),
            parent_layer_stack_id=_attribute(stack, "ParentLayerStackId"),
            source_layer_id=_attribute(stack, "SourceLayerId"),
            source_layer_stack_id=_attribute(stack, "SourceLayerStackId"),
            is_left_intrusions_linked=_optional_bool(
                _attribute(stack, "IsLeftIntrusionsLinked")
            ),
            intrusion_left_bottom=_attribute(stack, "IntrusionLeftBottom"),
            intrusion_left_top=_attribute(stack, "IntrusionLeftTop"),
            is_right_intrusions_linked=_optional_bool(
                _attribute(stack, "IsRightIntrusionsLinked")
            ),
            intrusion_right_bottom=_attribute(stack, "IntrusionRightBottom"),
            intrusion_right_top=_attribute(stack, "IntrusionRightTop"),
            raw_attributes=_raw_attributes(stack),
        )
        for stack in _children(stacks_element, "BranchSectionStack")
    )


def _children(element: Element, name: str) -> tuple[Element, ...]:
    return tuple(element.findall(f"./{{*}}{name}"))


def _to_stack_layer(
    layer: StackupXLayer,
    stack_index: int,
    registry_ref: str,
    *,
    stack_ref: str = "",
    layer_token: str = "",
) -> "AltiumStackLayer":
    from .altium_layer_stack_document import AltiumStackLayer

    thickness_mils = layer.thickness_mils
    dielectric_constant = _optional_float(layer.property_value("DielectricConstant"))
    loss_tangent = _optional_float(layer.property_value("LossTangent"))
    material = layer.property_value("Material")
    model_family = _model_family_from_stackupx_layer(layer)
    is_stiffener = layer.is_stiffener
    if layer.family == "stiffener":
        is_stiffener = True
    is_adhesive = layer.is_adhesive
    if layer.family == "adhesive":
        is_adhesive = True
    return AltiumStackLayer(
        stack_index=stack_index,
        registry_ref=registry_ref,
        display_name=layer.name,
        family=model_family,
        source_family="stackupx",
        source_record_id=layer.id,
        layer_id=_v7_saved_layer_id_from_stackupx_token(layer_token),
        legacy_layer_id=_legacy_layer_id_from_stackupx_token(layer_token),
        stackupx_type_id=layer.type_id,
        copper_thickness_mils=thickness_mils if model_family == "copper" else None,
        dielectric_constant=dielectric_constant,
        dielectric_loss_tangent=loss_tangent,
        dielectric_height_mils=(
            thickness_mils
            if model_family
            in {
                "dielectric",
                "solder_mask",
                "surface_finish",
                "plating",
                "misc",
                "marking",
            }
            else None
        ),
        dielectric_material=material,
        dielectric_type=_dielectric_type_from_type_id(layer.type_id),
        component_placement=_component_placement_value(
            layer.property_value("ComponentPlacement")
        ),
        used_by_primitives=_stackupx_layer_used_by_primitives(layer),
        coverlay_expansion=layer.property_value("CoverlayExpansion"),
        is_stiffener=is_stiffener,
        is_adhesive=is_adhesive,
        source_keys=tuple(prop.name for prop in layer.properties),
        stackupx_properties=tuple(
            (prop.name, prop.type_name, prop.value) for prop in layer.properties
        ),
        stackupx_substack_properties=(
            (
                (
                    stack_ref,
                    tuple(
                        (prop.name, prop.type_name, prop.value)
                        for prop in layer.properties
                    ),
                ),
            )
            if stack_ref and layer.properties
            else ()
        ),
        stackupx_is_shared=layer.is_shared,
        stackupx_substack_is_shared=(
            ((stack_ref, bool(layer.is_shared)),)
            if stack_ref and layer.is_shared is not None
            else ()
        ),
        stackupx_substack_is_enabled=(
            ((stack_ref, bool(layer.is_enabled)),)
            if stack_ref and layer.is_enabled is not None
            else ()
        ),
    )


def _model_family_from_stackupx_layer(layer: StackupXLayer) -> str:
    if layer.family in {"adhesive", "stiffener"}:
        return "dielectric"
    if layer.family == "pe_conductive":
        return "copper"
    if layer.family in {"pe_nonconductive", "pe_isolation", "pe_core", "pe_prepreg"}:
        return "dielectric"
    return layer.family


def _stackupx_layer_enabled(layer: StackupXLayer) -> bool:
    return layer.is_enabled is not False


def _stackupx_layer_used_by_primitives(layer: StackupXLayer) -> bool | None:
    if _model_family_from_stackupx_layer(layer) != "copper":
        return None
    return _stackupx_layer_enabled(layer)


def _to_layer_pairs(
    stack: StackupXStack,
    *,
    pair_index_start: int,
    layer_tokens_by_id: dict[str, str],
) -> list["AltiumLayerPair"]:
    from .altium_layer_stack_document import AltiumLayerPair

    layers_by_id = {layer.id: layer for layer in stack.layers}
    pairs: list[AltiumLayerPair] = []
    for span in tuple(stack.via_spans) + tuple(stack.drill_spans):
        start = layers_by_id.get(span.start_layer_id)
        stop = layers_by_id.get(span.stop_layer_id)
        pairs.append(
            AltiumLayerPair(
                pair_index=pair_index_start + len(pairs),
                low_layer_token=layer_tokens_by_id.get(
                    span.start_layer_id, _span_layer_token(start)
                ),
                high_layer_token=layer_tokens_by_id.get(
                    span.stop_layer_id, _span_layer_token(stop)
                ),
                source_substack_refs=(stack.id,),
                drill_pair_type_raw=_span_type_raw(span),
                is_backdrill=(span.span_type == "BackDrill"),
            )
        )
    return pairs


def _to_transmission_lines(
    profiles: tuple[StackupXImpedanceProfile, ...],
    *,
    layer_tokens_by_id: dict[str, str],
) -> list["AltiumTransmissionLine"]:
    from .altium_layer_stack_document import AltiumTransmissionLine

    lines: list[AltiumTransmissionLine] = []
    for profile in profiles:
        for line in profile.transmission_lines:
            lines.append(
                AltiumTransmissionLine(
                    index=len(lines),
                    source_family="stackupx",
                    profile_id=profile.id,
                    substack_id=line.stack_id,
                    layer_id=line.layer_id,
                    layer_token=layer_tokens_by_id.get(line.layer_id, ""),
                    is_differential=line.is_differential,
                    top_ref_id=line.top_ref_layer_id,
                    top_ref_token=layer_tokens_by_id.get(line.top_ref_layer_id, ""),
                    bottom_ref_id=line.bottom_ref_layer_id,
                    bottom_ref_token=layer_tokens_by_id.get(
                        line.bottom_ref_layer_id, ""
                    ),
                    width=line.trace_width,
                    gap=line.trace_gap,
                    calc_mode=line.calc_mode,
                    impedance_error=line.impedance_error,
                    tl_type_name=line.tl_type_name,
                    use_solder_mask=line.use_solder_mask,
                    coated_height_1=line.coated_height_1,
                    coated_height_2=line.coated_height_2,
                    clearance_to_plane=line.clearance_to_plane,
                    electric_parameters=line.electric_parameters,
                )
            )
    return lines


def _optional_child(element: Element, name: str) -> Element | None:
    return element.find(f"./{{*}}{name}")


def _required_child(element: Element, name: str) -> Element:
    child = _optional_child(element, name)
    if child is None:
        raise ValueError(f"Missing required StackupX element: {name}")
    return child


def _attribute(element: Element, name: str) -> str:
    value = element.attrib.get(name, "")
    return str(value).strip()


def _raw_attributes(element: Element) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted((str(key), str(value)) for key, value in element.attrib.items())
    )


def _text(element: Element) -> str:
    return str(element.text or "").strip()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _registry_ref(layer_id: str) -> str:
    token = str(layer_id or "").strip()
    return f"stackupx:{token}" if token else "stackupx:unknown"


def _standard_token_from_layer_name(name: str) -> str | None:
    normalized = _normalized_layer_name(name)
    mapping = {
        "TOPLAYER": "TOP",
        "BOTTOMLAYER": "BOTTOM",
        "TOPSOLDER": "TOPSOLDER",
        "BOTTOMSOLDER": "BOTTOMSOLDER",
        "TOPOVERLAY": "TOPOVERLAY",
        "BOTTOMOVERLAY": "BOTTOMOVERLAY",
    }
    if normalized in mapping:
        return mapping[normalized]
    for prefix in ("MID", "LAYER"):
        if not normalized.startswith(prefix):
            continue
        suffix = normalized[len(prefix) :]
        if suffix.isdigit():
            return f"MID{int(suffix)}"
    return None


def _side_from_layer_name(name: str) -> str | None:
    normalized = _normalized_layer_name(name)
    if normalized.startswith("TOP"):
        return "top"
    if normalized.startswith("BOTTOM"):
        return "bottom"
    return None


def _normalized_layer_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(name or "").upper())


def _component_placement_value(value: str) -> int | None:
    token = str(value or "").strip()
    if not token:
        return None
    mapping = {
        "None": 0,
        "BodyUp": 1,
        "BodyDown": 2,
    }
    if token in mapping:
        return mapping[token]
    try:
        return int(token)
    except ValueError:
        return None


def _span_layer_token(layer: StackupXLayer | None) -> str:
    if layer is None:
        return ""
    return _standard_token_from_layer_name(layer.name) or layer.name


def _layer_tokens_by_stack_order(
    layers: tuple[StackupXLayer, ...],
) -> dict[str, str]:
    copper_layers = [layer for layer in layers if layer.family == "copper"]
    first_copper_id = copper_layers[0].id if copper_layers else ""
    last_copper_id = copper_layers[-1].id if copper_layers else ""
    signal_index = 0
    plane_index = 0
    tokens: dict[str, str] = {}
    for layer in layers:
        if layer.family != "copper":
            tokens[layer.id] = _span_layer_token(layer)
            continue
        normalized = _normalized_layer_name(layer.name)
        if layer.id == first_copper_id or "TOPLAYER" in normalized:
            tokens[layer.id] = "TOP"
            continue
        if layer.id == last_copper_id or "BOTTOMLAYER" in normalized:
            tokens[layer.id] = "BOTTOM"
            continue
        if layer.type_id.lower() in _INTERNAL_PLANE_TYPE_IDS:
            plane_index += 1
            tokens[layer.id] = f"PLANE{plane_index}"
            continue
        signal_index += 1
        tokens[layer.id] = f"MID{signal_index}"
    return tokens


def _optional_float(value: str) -> float | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return float(token)
    except ValueError:
        return None


def _optional_bool(value: str) -> bool | None:
    token = str(value or "").strip().lower()
    if not token:
        return None
    if token == "true":
        return True
    if token == "false":
        return False
    return None


def _parse_length_to_mils(value: str) -> float | None:
    match = _LENGTH_RE.match(str(value or ""))
    if match is None:
        return None
    number = float(match.group("number"))
    unit = (match.group("unit") or "mil").lower()
    if unit == "mil":
        return number
    if unit == "mm":
        return number / 0.0254
    if unit == "um":
        return number / 25.4
    if unit == "m":
        return number / 0.0000254
    return None


def _to_xml_element(document: AltiumStackupXDocument) -> Element:
    root = Element(
        _qname("StackupDocument"),
        {
            "SerializerVersion": document.serializer_version,
            "Version": document.version,
            "Id": document.id,
            "RevisionId": document.revision_id,
            "RevisionDate": document.revision_date,
        },
    )
    _append_feature_set(root, document.features)
    ET.SubElement(root, _qname("TypeExtensions"))
    stackup = ET.SubElement(root, _qname("Stackup"), dict(document.stackup_attributes))
    _append_stacks(stackup, document.stacks)
    _append_impedance_profiles(stackup, document.impedance_profiles)
    _append_branches(stackup, document.branches)
    return root


def _append_feature_set(
    root: Element,
    features: tuple[StackupXFeature, ...],
) -> None:
    feature_set = ET.SubElement(root, _qname("FeatureSet"))
    for feature in features:
        element = ET.SubElement(feature_set, _qname("Feature"), {"Id": feature.id})
        element.text = feature.name


def _append_stacks(stackup: Element, stacks: tuple[StackupXStack, ...]) -> None:
    stacks_element = ET.SubElement(stackup, _qname("Stacks"))
    for stack in stacks:
        attrs = {
            "Id": stack.id,
            "Name": stack.name,
            "IsSymmetric": _xml_bool(stack.is_symmetric),
            "TemplateId": stack.template_id,
        }
        if stack.stack_type:
            attrs["Type"] = stack.stack_type
        if stack.is_flex is not None:
            attrs["IsFlex"] = _xml_bool(stack.is_flex)
        stack_element = ET.SubElement(stacks_element, _qname("Stack"), attrs)
        _append_layers(stack_element, stack.layers)
        _append_spans(stack_element, "ViaSpans", "ViaSpan", stack.via_spans)
        _append_spans(stack_element, "DrillSpans", "DrillSpan", stack.drill_spans)


def _append_layers(
    stack_element: Element,
    layers: tuple[StackupXLayer, ...],
) -> None:
    layers_element = ET.SubElement(stack_element, _qname("Layers"))
    for layer in layers:
        attrs = {"Id": layer.id, "TypeId": layer.type_id, "Name": layer.name}
        if layer.is_shared is not None:
            attrs["IsShared"] = _xml_bool(layer.is_shared)
        if layer.is_enabled is not None:
            attrs["IsEnabled"] = _xml_bool(layer.is_enabled)
        if layer.is_stiffener is not None:
            attrs["IsStiffener"] = _xml_bool(layer.is_stiffener)
        if layer.is_adhesive is not None:
            attrs["IsAdhesive"] = _xml_bool(layer.is_adhesive)
        layer_element = ET.SubElement(layers_element, _qname("Layer"), attrs)
        props_element = ET.SubElement(layer_element, _qname("Properties"))
        for prop in layer.properties:
            prop_element = ET.SubElement(
                props_element,
                _qname("Property"),
                {"Name": prop.name, "Type": prop.type_name},
            )
            prop_element.text = prop.value


def _append_spans(
    stack_element: Element,
    group_name: str,
    row_name: str,
    spans: tuple[StackupXSpan, ...],
) -> None:
    spans_element = ET.SubElement(stack_element, _qname(group_name))
    for span in spans:
        ET.SubElement(
            spans_element,
            _qname(row_name),
            {
                "Id": span.id,
                "AutoName": span.auto_name,
                "Type": span.span_type,
                "StartLayerId": span.start_layer_id,
                "StopLayerId": span.stop_layer_id,
            },
        )


def _append_impedance_profiles(
    stackup: Element,
    profiles: tuple[StackupXImpedanceProfile, ...],
) -> None:
    profiles_element = ET.SubElement(stackup, _qname("ImpedanceProfiles"))
    for profile in profiles:
        profile_element = ET.SubElement(
            profiles_element,
            _qname("ImpedanceProfile"),
            {
                "Id": profile.id,
                "AutoName": profile.auto_name,
                "Type": profile.profile_type,
                "Impedance": profile.impedance,
                "Tolerance": profile.tolerance,
            },
        )
        lines_element = ET.SubElement(profile_element, _qname("TransmissionLines"))
        for line in profile.transmission_lines:
            line_attrs = _transmission_line_attributes(line)
            line_element = ET.SubElement(
                lines_element,
                _qname("TransmissionLine"),
                line_attrs,
            )
            if line.electric_parameters:
                ET.SubElement(
                    line_element,
                    _qname("ElectricParameters"),
                    dict(line.electric_parameters),
                )


def _transmission_line_attributes(line: StackupXTransmissionLine) -> dict[str, str]:
    attrs = {
        "LayerId": line.layer_id,
        "StackId": line.stack_id,
        "TLTypeName": line.tl_type_name,
        "CalcMode": line.calc_mode,
        "TopRefLayerId": line.top_ref_layer_id,
        "BottomRefLayerId": line.bottom_ref_layer_id,
        "TraceWidth": line.trace_width,
        "TraceGap": line.trace_gap,
        "ImpedanceError": line.impedance_error,
        "CoatedHeight1": line.coated_height_1,
        "CoatedHeight2": line.coated_height_2,
        "ClearanceToPlane": line.clearance_to_plane,
    }
    if line.is_differential is not None:
        attrs["IsDifferential"] = _xml_bool(line.is_differential)
    if line.use_solder_mask is not None:
        attrs["UseSolderMask"] = _xml_bool(line.use_solder_mask)
    return {key: value for key, value in attrs.items() if value}


def _append_branches(
    stackup: Element,
    branches: tuple[StackupXBranch, ...],
) -> None:
    branches_element = ET.SubElement(stackup, _qname("Branches"))
    for branch in branches:
        branch_attrs = {
            "Id": branch.id,
            "Name": branch.name,
            "Description": branch.description,
        }
        if branch.parent_branch_id:
            branch_attrs["ParentBranchId"] = branch.parent_branch_id
        branch_element = ET.SubElement(
            branches_element,
            _qname("Branch"),
            branch_attrs,
        )
        sections_element = ET.SubElement(branch_element, _qname("Sections"))
        for section in branch.sections:
            section_attrs = {"Id": section.id, "Name": section.name}
            if section.parent_section_id:
                section_attrs["ParentSectionId"] = section.parent_section_id
            section_element = ET.SubElement(
                sections_element,
                _qname("BranchSection"),
                section_attrs,
            )
            stacks_element = ET.SubElement(section_element, _qname("Stacks"))
            for section_stack in section.stacks:
                ET.SubElement(
                    stacks_element,
                    _qname("BranchSectionStack"),
                    _branch_section_stack_attributes(section_stack),
                )


def _branch_section_stack_attributes(
    stack: StackupXBranchSectionStack,
) -> dict[str, str]:
    attrs = {
        "Id": stack.id,
        "LayerStackId": stack.layer_stack_id,
        "Description": stack.description,
        "MaterialUsage": stack.material_usage,
        "Source": stack.source,
    }
    optional = {
        "ParentLayerId": stack.parent_layer_id,
        "ParentLayerStackId": stack.parent_layer_stack_id,
        "SourceLayerId": stack.source_layer_id,
        "SourceLayerStackId": stack.source_layer_stack_id,
    }
    attrs.update({key: value for key, value in optional.items() if value})
    attrs.update(
        {
            key: value
            for key, value in stack.raw_attributes
            if key not in attrs and key not in _BRANCH_INTRUSION_ATTRIBUTE_NAMES
        }
    )
    attrs.update(
        dict(
            _branch_intrusion_attributes(
                is_left_intrusions_linked=stack.is_left_intrusions_linked,
                intrusion_left_bottom=stack.intrusion_left_bottom,
                intrusion_left_top=stack.intrusion_left_top,
                is_right_intrusions_linked=stack.is_right_intrusions_linked,
                intrusion_right_bottom=stack.intrusion_right_bottom,
                intrusion_right_top=stack.intrusion_right_top,
            )
        )
    )
    return attrs


def _export_stacks(document: "AltiumLayerStackDocument") -> tuple[StackupXStack, ...]:
    stacks: tuple["AltiumPhysicalStack", ...] = tuple(document.physical_stacks or ())
    if not stacks:
        raise ValueError(".stackupx export requires at least one physical stack")
    substack_exports = _export_pcbdoc_substack_stacks(document, stacks)
    if substack_exports:
        return substack_exports
    return tuple(
        _export_stack(document, index, stack, strict_scoped_pairs=len(stacks) > 1)
        for index, stack in enumerate(stacks)
    )


def _export_pcbdoc_substack_stacks(
    document: "AltiumLayerStackDocument",
    stacks: tuple["AltiumPhysicalStack", ...],
) -> tuple[StackupXStack, ...]:
    substacks = tuple(document.substacks or ())
    if len(stacks) != 1 or not _should_export_substacks_as_stackupx_stacks(substacks):
        return ()

    master_stack = stacks[0]
    substack_sequence = substacks + _stackupx_auxiliary_stacks_for_export(
        document,
        substacks,
    )
    substack_physical_stacks = tuple(
        _physical_stack_from_substack(
            master_stack,
            substack,
            include_disabled_layers=index == 0,
        )
        for index, substack in enumerate(substack_sequence)
    )
    return tuple(
        _export_stack(document, index, stack, strict_scoped_pairs=True)
        for index, stack in enumerate(substack_physical_stacks)
    )


def _should_export_substacks_as_stackupx_stacks(substacks: tuple[object, ...]) -> bool:
    if len(substacks) < 2:
        return False
    return any(bool(getattr(substack, "is_flex", False)) for substack in substacks)


def _stackupx_auxiliary_stacks_for_export(
    document: object,
    substacks: tuple[object, ...],
) -> tuple[object, ...]:
    known_refs = {
        _normalized_guid_key(getattr(substack, "source_stackup_ref", ""))
        for substack in substacks
        if _normalized_guid_key(getattr(substack, "source_stackup_ref", ""))
    }
    branch_refs = _branch_stack_refs_for_export(
        tuple(getattr(document, "branches", ()) or ())
    )
    auxiliary: list[object] = []
    auxiliary_refs: set[str] = set()
    for stack in tuple(getattr(document, "stackupx_auxiliary_stacks", ()) or ()):
        key = _normalized_guid_key(getattr(stack, "source_stackup_ref", ""))
        if not key or key in known_refs or key in auxiliary_refs:
            continue
        if branch_refs and key not in branch_refs:
            continue
        auxiliary.append(stack)
        auxiliary_refs.add(key)
    return tuple(auxiliary)


def _branch_stack_refs_for_export(branches: tuple[object, ...]) -> set[str]:
    refs: set[str] = set()
    for branch in branches:
        for section in tuple(getattr(branch, "sections", ()) or ()):
            for stack in tuple(getattr(section, "stacks", ()) or ()):
                for value in (
                    getattr(stack, "source_stack_ref", ""),
                    getattr(stack, "source_layer_stack_id", ""),
                    getattr(stack, "parent_layer_stack_id", ""),
                ):
                    key = _normalized_guid_key(value)
                    if key:
                        refs.add(key)
    return refs


def _physical_stack_from_substack(
    master_stack: "AltiumPhysicalStack",
    substack: object,
    *,
    include_disabled_layers: bool,
) -> "AltiumPhysicalStack":
    stack_ref = str(
        getattr(substack, "source_stackup_ref", "")
        or getattr(master_stack, "stack_ref", "")
    )
    display_name = str(
        getattr(substack, "name", "") or getattr(master_stack, "display_name", "")
    )
    layers = _layers_for_substack(
        master_stack,
        substack,
        include_disabled_layers=include_disabled_layers,
    )
    if not layers:
        raise ValueError(f"Substack {display_name!r} does not reference any layers")
    return replace(
        master_stack,
        stack_ref=stack_ref,
        display_name=display_name,
        is_flex=bool(getattr(substack, "is_flex", False)),
        service_stackup=getattr(substack, "service_stackup", None),
        used_by_primitives=getattr(substack, "used_by_primitives", None),
        stackupx_is_flex=getattr(substack, "stackupx_is_flex", None),
        stackupx_stack_type=getattr(substack, "stackupx_stack_type", None),
        layers=layers,
    )


def _layers_for_substack(
    master_stack: "AltiumPhysicalStack",
    substack: object,
    *,
    include_disabled_layers: bool,
) -> tuple[object, ...]:
    layers = tuple(getattr(master_stack, "layers", ()) or ())
    if include_disabled_layers:
        return layers
    stack_ref = str(getattr(substack, "source_stackup_ref", "") or "")
    if _any_layer_has_stackup_context(layers, stack_ref):
        return tuple(
            layer
            for layer in layers
            if _export_layer_enabled(layer, stack_ref) is not False
        )
    enabled_refs = {
        str(ref) for ref in tuple(getattr(substack, "enabled_layer_refs", ()) or ())
    }
    if not enabled_refs:
        return layers
    return tuple(
        layer
        for layer in layers
        if str(getattr(layer, "registry_ref", "")) in enabled_refs
    )


def _export_stack(
    document: "AltiumLayerStackDocument",
    index: int,
    stack: object,
    *,
    strict_scoped_pairs: bool,
) -> StackupXStack:
    stack_ref = getattr(stack, "stack_ref", "")
    layers = _export_layers(stack)
    stack_id = _existing_or_stackupx_guid(
        stack_ref,
        "stack",
        document.active_stack_ref,
        index,
        getattr(stack, "display_name", ""),
    )
    is_flex = bool(getattr(stack, "is_flex", False))
    stackupx_is_flex = getattr(stack, "stackupx_is_flex", None)
    stack_type_value = getattr(stack, "stackupx_stack_type", None)
    stack_type = "" if stack_type_value is None else str(stack_type_value)
    if stack_type_value is None and is_flex:
        stack_type = "BoardRegionLayerStack"
    emitted_is_flex = stackupx_is_flex if stackupx_is_flex is not None else is_flex
    via_spans, drill_spans = _export_spans(
        stack_id,
        layers,
        _layer_pairs_for_stack(
            document,
            stack,
            strict_scoped_pairs=strict_scoped_pairs,
        ),
        default_pairs=not is_flex and not tuple(document.layer_pairs or ()),
    )
    return StackupXStack(
        id=stack_id,
        name=str(getattr(stack, "display_name", "") or "Board Layer Stack"),
        stack_type=stack_type,
        is_flex=emitted_is_flex,
        is_symmetric=True,
        template_id=_stackupx_guid("template", stack_id),
        layers=layers,
        via_spans=via_spans,
        drill_spans=drill_spans,
        raw_attributes=(),
    )


def _layer_pairs_for_stack(
    document: "AltiumLayerStackDocument",
    stack: object,
    *,
    strict_scoped_pairs: bool,
) -> tuple[object, ...]:
    pairs = tuple(document.layer_pairs or ())
    stack_ref = _normalized_guid_key(getattr(stack, "stack_ref", ""))
    scoped_matches = tuple(
        pair
        for pair in pairs
        if tuple(getattr(pair, "source_substack_refs", ()) or ())
        and stack_ref
        in {
            _normalized_guid_key(ref)
            for ref in tuple(getattr(pair, "source_substack_refs", ()) or ())
        }
    )
    if scoped_matches:
        return scoped_matches
    if strict_scoped_pairs and any(
        tuple(getattr(pair, "source_substack_refs", ()) or ()) for pair in pairs
    ):
        return ()
    if bool(getattr(stack, "is_flex", False)):
        return ()
    unscoped_pairs = tuple(
        pair
        for pair in pairs
        if not tuple(getattr(pair, "source_substack_refs", ()) or ())
    )
    return unscoped_pairs or pairs


def _export_layers(stack: object) -> tuple[StackupXLayer, ...]:
    stack_ref = str(getattr(stack, "stack_ref", "") or "")
    source_layers = tuple(
        layer
        for layer in tuple(getattr(stack, "layers", ()) or ())
        if str(getattr(layer, "family", "")).strip().lower() != "paste"
    )
    unsupported = sorted(
        {
            str(getattr(layer, "family", "")).strip().lower()
            for layer in source_layers
            if str(getattr(layer, "family", "")).strip().lower()
            not in {
                "overlay",
                "solder_mask",
                "copper",
                "dielectric",
                "surface_finish",
                "plating",
                "misc",
                "marking",
            }
        }
    )
    if unsupported:
        raise ValueError(
            "Rigid .stackupx export does not support layer families: "
            + ", ".join(unsupported)
        )
    return tuple(
        _export_layer(index, layer, stack_ref=stack_ref)
        for index, layer in enumerate(source_layers)
    )


def _export_layer(index: int, layer: object, *, stack_ref: str) -> StackupXLayer:
    layer_id = _existing_or_stackupx_guid(
        getattr(layer, "source_record_id", ""),
        index,
        getattr(layer, "display_name", ""),
    )
    enabled = _export_layer_enabled(layer, stack_ref)
    return StackupXLayer(
        id=layer_id,
        type_id=_type_id_for_layer(layer),
        name=str(getattr(layer, "display_name", "") or f"Layer {index}"),
        is_shared=_export_layer_shared(layer, enabled, stack_ref),
        is_enabled=enabled,
        is_stiffener=getattr(layer, "is_stiffener", None),
        is_adhesive=getattr(layer, "is_adhesive", None),
        properties=_properties_for_layer(layer, stack_ref=stack_ref),
        raw_attributes=(),
    )


def _export_layer_enabled(layer: object, stack_ref: str) -> bool | None:
    if not stack_ref:
        return None
    scoped_value = _export_layer_scoped_enabled(layer, stack_ref)
    if scoped_value is not None:
        return scoped_value
    checker = getattr(layer, "is_enabled_for_substack", None)
    if not callable(checker):
        return None
    return None if bool(checker(stack_ref)) else False


def _export_layer_scoped_enabled(layer: object, stack_ref: str) -> bool | None:
    normalized_stack_ref = _normalized_guid_key(stack_ref)
    if not normalized_stack_ref:
        return None
    values = getattr(layer, "stackupx_substack_is_enabled", ())
    if not isinstance(values, tuple | list):
        return None
    for ref, enabled in values:
        if _normalized_guid_key(ref) == normalized_stack_ref:
            return bool(enabled)
    return None


def _export_layer_shared(
    layer: object,
    enabled: bool | None,
    stack_ref: str,
) -> bool:
    scoped_value = _export_layer_scoped_shared(layer, stack_ref)
    if scoped_value is not None:
        return scoped_value
    source_value = getattr(layer, "stackupx_is_shared", None)
    if source_value is not None:
        return bool(source_value)
    family = str(getattr(layer, "family", "") or "").strip().lower()
    if family == "surface_finish":
        return False
    return False if enabled is False else True


def _export_layer_scoped_shared(layer: object, stack_ref: str) -> bool | None:
    normalized_stack_ref = _normalized_guid_key(stack_ref)
    if not normalized_stack_ref:
        return None
    values = getattr(layer, "stackupx_substack_is_shared", ())
    if not isinstance(values, tuple | list):
        return None
    for ref, shared in values:
        if _normalized_guid_key(ref) == normalized_stack_ref:
            return bool(shared)
    return None


def _any_layer_has_stackup_context(
    layers: tuple[object, ...],
    stack_ref: str,
) -> bool:
    normalized_stack_ref = _normalized_guid_key(stack_ref)
    if not normalized_stack_ref:
        return False
    for layer in layers:
        contexts = getattr(layer, "substack_contexts", ())
        if not isinstance(contexts, tuple | list):
            continue
        for context in contexts:
            if not isinstance(context, tuple | list) or not context:
                continue
            context_ref = context[0]
            if _normalized_guid_key(str(context_ref)) == normalized_stack_ref:
                return True
    return False


def _properties_for_layer(
    layer: object,
    *,
    stack_ref: str = "",
) -> tuple[StackupXProperty, ...]:
    family = str(getattr(layer, "family", "")).strip().lower()
    preserved = _preserved_stackupx_properties(layer, stack_ref=stack_ref)
    if preserved:
        return preserved
    if family == "overlay":
        if (
            getattr(layer, "dielectric_type", None) == 4
            or getattr(layer, "dielectric_height_mils", None) is not None
            or str(getattr(layer, "coverlay_expansion", "") or "")
        ):
            return _dielectric_properties(layer)
        return ()
    if family == "copper":
        return _copper_properties(layer)
    if family in {"dielectric", "solder_mask"}:
        return _dielectric_properties(layer)
    if family in {"surface_finish", "plating", "misc", "marking"}:
        return _physical_properties(layer)
    return ()


def _preserved_stackupx_properties(
    layer: object,
    *,
    stack_ref: str = "",
) -> tuple[StackupXProperty, ...]:
    props: list[StackupXProperty] = []
    raw_properties = _stackupx_properties_for_stack(layer, stack_ref)
    if not isinstance(raw_properties, tuple | list):
        return ()
    for item in raw_properties:
        if not isinstance(item, tuple | list):
            continue
        if len(item) < 3:
            continue
        name, type_name, value = item[:3]
        prop_name = str(name or "").strip()
        if not prop_name:
            continue
        props.append(
            StackupXProperty(
                name=prop_name,
                type_name=_property_type_name(prop_name, str(type_name or "")),
                value=str(value or ""),
            )
        )
    return tuple(props)


def _stackupx_properties_for_stack(
    layer: object,
    stack_ref: str,
) -> tuple[tuple[str, str, str], ...]:
    normalized_stack_ref = _normalized_guid_key(stack_ref)
    scoped_values = getattr(layer, "stackupx_substack_properties", ())
    if normalized_stack_ref and isinstance(scoped_values, tuple | list):
        for ref, properties in scoped_values:
            if _normalized_guid_key(ref) == normalized_stack_ref:
                return tuple(properties)
    return tuple(getattr(layer, "stackupx_properties", ()) or ())


def _property_type_name(name: str, type_name: str) -> str:
    if type_name:
        return type_name
    normalized = name.strip().upper()
    if normalized in {
        "THICKNESS",
        "COVERLAYEXPANSION",
        "MATERIAL.DIELECTRICSTRENGTH",
        "MATERIAL.GLASSTRANSTEMP",
    }:
        return "LengthValue"
    if normalized in {"DIELECTRICCONSTANT", "LOSSTANGENT"}:
        return "DimensionlessValue"
    if normalized == "WEIGHT":
        return "MassOunceValue"
    if normalized == "COMPONENTPLACEMENT":
        return _COMPONENT_PLACEMENT_TYPE
    if normalized == "PROCESS":
        return "EntityReference"
    if normalized == "MATERIAL.COLOR":
        return (
            "System.Windows.Media.Color, PresentationCore, Version=4.0.0.0, "
            "Culture=neutral, PublicKeyToken=31bf3856ad364e35"
        )
    return "String"


def _copper_properties(layer: object) -> tuple[StackupXProperty, ...]:
    props: list[StackupXProperty] = []
    _append_length_property(
        props, "Thickness", getattr(layer, "copper_thickness_mils", None)
    )
    component_placement = _component_placement_name(
        getattr(layer, "component_placement", None)
    )
    if component_placement:
        props.append(
            StackupXProperty(
                name="ComponentPlacement",
                type_name=_COMPONENT_PLACEMENT_TYPE,
                value=component_placement,
            )
        )
    return tuple(props)


def _dielectric_properties(layer: object) -> tuple[StackupXProperty, ...]:
    props: list[StackupXProperty] = []
    _append_length_property(
        props,
        "Thickness",
        getattr(layer, "dielectric_height_mils", None),
    )
    material = str(getattr(layer, "dielectric_material", "") or "")
    if material:
        props.append(StackupXProperty("Material", "String", material))
    dielectric_constant = getattr(layer, "dielectric_constant", None)
    if dielectric_constant is not None:
        props.append(
            StackupXProperty(
                "DielectricConstant",
                "DimensionlessValue",
                format(float(dielectric_constant), ".12g"),
            )
        )
    loss_tangent = getattr(layer, "dielectric_loss_tangent", None)
    if loss_tangent is not None:
        props.append(
            StackupXProperty(
                "LossTangent",
                "DimensionlessValue",
                format(float(loss_tangent), ".12g"),
            )
        )
    coverlay_expansion = str(getattr(layer, "coverlay_expansion", "") or "")
    if coverlay_expansion:
        props.append(
            StackupXProperty("CoverlayExpansion", "LengthValue", coverlay_expansion)
        )
    return tuple(props)


def _physical_properties(layer: object) -> tuple[StackupXProperty, ...]:
    props: list[StackupXProperty] = []
    _append_length_property(
        props,
        "Thickness",
        getattr(layer, "dielectric_height_mils", None),
    )
    material = str(getattr(layer, "dielectric_material", "") or "")
    if material:
        props.append(StackupXProperty("Material", "String", material))
    return tuple(props)


def _append_length_property(
    props: list[StackupXProperty],
    name: str,
    value_mils: object,
) -> None:
    value = _float_export_value(value_mils)
    if value is not None:
        props.append(
            StackupXProperty(
                name=name,
                type_name="LengthValue",
                value=_format_mil_value(value),
            )
        )


def _export_spans(
    stack_id: str,
    layers: tuple[StackupXLayer, ...],
    layer_pairs: tuple[object, ...],
    *,
    default_pairs: bool = True,
) -> tuple[tuple[StackupXSpan, ...], tuple[StackupXSpan, ...]]:
    token_layers = _layers_by_pair_token(layers)
    pairs = layer_pairs or (_default_export_pairs(layers) if default_pairs else ())
    via_spans: list[StackupXSpan] = []
    drill_spans: list[StackupXSpan] = []
    for pair in pairs:
        start = token_layers.get(_pair_token(getattr(pair, "low_layer_token", "")))
        stop = token_layers.get(_pair_token(getattr(pair, "high_layer_token", "")))
        if start is None or stop is None:
            continue
        if bool(getattr(pair, "is_backdrill", False)):
            drill_spans.append(
                _export_span(
                    stack_id=stack_id,
                    index=len(drill_spans),
                    kind="drill-span",
                    prefix="BD",
                    span_type="BackDrill",
                    start=start,
                    stop=stop,
                    layers=layers,
                )
            )
        else:
            raw_type = _via_span_type_raw(pair)
            via_spans.append(
                _export_span(
                    stack_id=stack_id,
                    index=len(via_spans),
                    kind="via-span",
                    prefix=_via_span_name_prefix(raw_type),
                    span_type=_stackupx_span_type(raw_type),
                    start=start,
                    stop=stop,
                    layers=layers,
                )
            )
    return tuple(via_spans), tuple(drill_spans)


@dataclass(frozen=True)
class _ExportLayerPair:
    low_layer_token: str
    high_layer_token: str
    drill_pair_type_raw: int | None
    is_backdrill: bool


def _default_export_pairs(layers: tuple[StackupXLayer, ...]) -> tuple[object, ...]:
    copper_layers = tuple(layer for layer in layers if layer.family == "copper")
    if len(copper_layers) < 2:
        return ()
    return (_ExportLayerPair("TOP", "BOTTOM", 0, False),)


def _export_span(
    *,
    stack_id: str,
    index: int,
    kind: str,
    prefix: str,
    span_type: str,
    start: StackupXLayer,
    stop: StackupXLayer,
    layers: tuple[StackupXLayer, ...],
) -> StackupXSpan:
    return StackupXSpan(
        id=_stackupx_guid(kind, stack_id, index),
        auto_name=_span_name(prefix, start, stop, layers),
        span_type=span_type,
        start_layer_id=start.id,
        stop_layer_id=stop.id,
        raw_attributes=(),
    )


def _export_branches(
    *,
    stackups: tuple[StackupXStack, ...],
    rigid_flex_mode: str,
    source_branches: tuple[object, ...] = (),
) -> tuple[StackupXBranch, ...]:
    if not stackups:
        return ()
    if source_branches:
        return _export_source_branches(
            source_branches=source_branches,
            stackups=stackups,
            rigid_flex_mode=rigid_flex_mode,
        )
    first_stack_id = stackups[0].id
    branch_id = _stackupx_guid("branch", first_stack_id)
    sections: list[StackupXBranchSection] = []
    parent_section_id = ""
    parent_stack_id = first_stack_id
    parent_layer_id = _branch_source_layer_id(stackups[0].layers)
    mode = _normalized_rigid_flex_mode(rigid_flex_mode)
    for index, stackup in enumerate(stackups):
        stack_id = stackup.id
        stack_name = stackup.name
        is_flex = bool(stackup.is_flex)
        section_id = _stackupx_guid("branch-section", branch_id, index)
        section_stack = StackupXBranchSectionStack(
            id=_stackupx_guid("branch-section-stack", stack_id),
            layer_stack_id=stack_id,
            description=stack_name,
            material_usage="Individual" if is_flex and mode == "advanced" else "Common",
            source="Design",
            parent_layer_id=parent_layer_id if is_flex else "",
            parent_layer_stack_id=parent_stack_id if is_flex else "",
            source_layer_id=parent_layer_id if is_flex else "",
            source_layer_stack_id=stack_id if is_flex else "",
            is_left_intrusions_linked=True,
            intrusion_left_bottom="0m",
            intrusion_left_top="0m",
            is_right_intrusions_linked=True,
            intrusion_right_bottom="0m",
            intrusion_right_top="0m",
            raw_attributes=(),
        )
        sections.append(
            StackupXBranchSection(
                id=section_id,
                name=f"Branch Section-{index + 1}",
                parent_section_id=parent_section_id,
                stacks=(section_stack,),
                raw_attributes=(),
            )
        )
        parent_section_id = section_id
        if not is_flex:
            parent_stack_id = stack_id
            parent_layer_id = _branch_source_layer_id(stackup.layers)
    return (
        StackupXBranch(
            id=branch_id,
            name="Board",
            description="",
            parent_branch_id="",
            sections=tuple(sections),
            raw_attributes=(),
        ),
    )


def _export_source_branches(
    *,
    source_branches: tuple[object, ...],
    stackups: tuple[StackupXStack, ...],
    rigid_flex_mode: str,
) -> tuple[StackupXBranch, ...]:
    stackups_by_ref = {
        _normalized_guid_key(stackup.id): stackup for stackup in stackups if stackup.id
    }
    mode = _normalized_rigid_flex_mode(rigid_flex_mode)
    result: list[StackupXBranch] = []
    for branch_index, branch in enumerate(source_branches):
        branch_id = _existing_or_stackupx_guid(
            getattr(branch, "source_branch_id", ""),
            "branch",
            branch_index,
            getattr(branch, "name", ""),
        )
        sections: list[StackupXBranchSection] = []
        for section_index, section in enumerate(
            tuple(getattr(branch, "sections", ()) or ())
        ):
            section_id = _existing_or_stackupx_guid(
                getattr(section, "source_section_id", ""),
                "branch-section",
                branch_id,
                section_index,
            )
            section_stacks: list[StackupXBranchSectionStack] = []
            for stack_index, stack in enumerate(
                tuple(getattr(section, "stacks", ()) or ())
            ):
                layer_stack_id = _existing_or_stackupx_guid(
                    getattr(stack, "source_stack_ref", ""),
                    "branch-section-stack-layer-stack",
                    section_id,
                    stack_index,
                )
                linked_stack = stackups_by_ref.get(_normalized_guid_key(layer_stack_id))
                section_stacks.append(
                    StackupXBranchSectionStack(
                        id=_existing_or_stackupx_guid(
                            getattr(stack, "source_record_id", ""),
                            "branch-section-stack",
                            section_id,
                            stack_index,
                        ),
                        layer_stack_id=layer_stack_id,
                        description=str(getattr(stack, "description", "") or ""),
                        material_usage=_stackupx_material_usage(
                            getattr(stack, "material_usage", ""),
                            is_flex=bool(getattr(linked_stack, "is_flex", False)),
                            rigid_flex_mode=mode,
                        ),
                        source=str(getattr(stack, "source", "") or "Design"),
                        parent_layer_id=_existing_or_empty_stackupx_guid(
                            getattr(stack, "parent_layer_id", "")
                        ),
                        parent_layer_stack_id=_existing_or_empty_stackupx_guid(
                            getattr(stack, "parent_layer_stack_id", "")
                        ),
                        source_layer_id=_existing_or_empty_stackupx_guid(
                            getattr(stack, "source_layer_id", "")
                        ),
                        source_layer_stack_id=_existing_or_empty_stackupx_guid(
                            getattr(stack, "source_layer_stack_id", "")
                        ),
                        is_left_intrusions_linked=getattr(
                            stack,
                            "is_left_intrusions_linked",
                            None,
                        ),
                        intrusion_left_bottom=_intrusion_value(
                            getattr(stack, "intrusion_left_bottom", "")
                        ),
                        intrusion_left_top=_intrusion_value(
                            getattr(stack, "intrusion_left_top", "")
                        ),
                        is_right_intrusions_linked=getattr(
                            stack,
                            "is_right_intrusions_linked",
                            None,
                        ),
                        intrusion_right_bottom=_intrusion_value(
                            getattr(stack, "intrusion_right_bottom", "")
                        ),
                        intrusion_right_top=_intrusion_value(
                            getattr(stack, "intrusion_right_top", "")
                        ),
                        raw_attributes=(),
                    )
                )
            sections.append(
                StackupXBranchSection(
                    id=section_id,
                    name=str(
                        getattr(section, "name", "")
                        or f"Branch Section-{section_index + 1}"
                    ),
                    parent_section_id=_existing_or_empty_stackupx_guid(
                        getattr(section, "parent_section_id", "")
                    ),
                    stacks=tuple(section_stacks),
                    raw_attributes=(),
                )
            )
        result.append(
            StackupXBranch(
                id=branch_id,
                name=str(getattr(branch, "name", "") or f"Branch {branch_index + 1}"),
                description=str(getattr(branch, "description", "") or ""),
                parent_branch_id=_existing_or_empty_stackupx_guid(
                    getattr(branch, "parent_branch_id", "")
                ),
                sections=tuple(sections),
                raw_attributes=(),
            )
        )
    return tuple(result)


def _stackupx_material_usage(
    value: object,
    *,
    is_flex: bool,
    rigid_flex_mode: str,
) -> str:
    token = str(value or "").strip()
    if token:
        if token == "1":
            return "Individual"
        if token == "0":
            return "Common"
        return token
    if is_flex and rigid_flex_mode == "advanced":
        return "Individual"
    return "Common"


def _existing_or_empty_stackupx_guid(value: object) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    return _existing_or_stackupx_guid(token)


_BRANCH_INTRUSION_ATTRIBUTE_NAMES = frozenset(
    {
        "IsLeftIntrusionsLinked",
        "IntrusionLeftBottom",
        "IntrusionLeftTop",
        "IsRightIntrusionsLinked",
        "IntrusionRightBottom",
        "IntrusionRightTop",
    }
)


def _branch_source_layer_id(layers: tuple[StackupXLayer, ...]) -> str:
    for layer in layers:
        if layer.family == "copper":
            return layer.id
    return layers[0].id if layers else ""


def _branch_intrusion_attributes(
    *,
    is_left_intrusions_linked: object = True,
    intrusion_left_bottom: object = "0m",
    intrusion_left_top: object = "0m",
    is_right_intrusions_linked: object = True,
    intrusion_right_bottom: object = "0m",
    intrusion_right_top: object = "0m",
) -> tuple[tuple[str, str], ...]:
    return (
        (
            "IsLeftIntrusionsLinked",
            _xml_bool(_intrusion_linked_value(is_left_intrusions_linked)),
        ),
        ("IntrusionLeftBottom", _intrusion_value(intrusion_left_bottom)),
        ("IntrusionLeftTop", _intrusion_value(intrusion_left_top)),
        (
            "IsRightIntrusionsLinked",
            _xml_bool(_intrusion_linked_value(is_right_intrusions_linked)),
        ),
        ("IntrusionRightBottom", _intrusion_value(intrusion_right_bottom)),
        ("IntrusionRightTop", _intrusion_value(intrusion_right_top)),
    )


def _intrusion_value(value: object) -> str:
    token = str(value or "").strip()
    return token if token else "0m"


def _intrusion_linked_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    return True if not token else token not in {"0", "false", "no"}


def _export_impedance_profiles(
    *,
    profiles: tuple[object, ...],
    transmission_lines: tuple[object, ...],
    stackups: tuple[StackupXStack, ...],
) -> tuple[StackupXImpedanceProfile, ...]:
    if not profiles:
        return ()
    profile_ids = _export_profile_ids(profiles)
    profile_ids_by_source = _profile_ids_by_source(profiles, profile_ids)
    lines_by_profile_id = _export_transmission_lines_by_profile(
        transmission_lines=transmission_lines,
        profile_ids_by_source=profile_ids_by_source,
        stackups=stackups,
    )
    return tuple(
        _export_impedance_profile(
            index=index,
            profile=profile,
            profile_id=profile_ids[index],
            transmission_lines=lines_by_profile_id.get(profile_ids[index], ()),
        )
        for index, profile in enumerate(profiles)
    )


def _export_profile_ids(profiles: tuple[object, ...]) -> tuple[str, ...]:
    return tuple(
        _existing_or_stackupx_guid(
            getattr(profile, "source_record_id", ""),
            "impedance-profile",
            index,
            getattr(profile, "name", ""),
        )
        for index, profile in enumerate(profiles)
    )


def _export_impedance_profile(
    *,
    index: int,
    profile: object,
    profile_id: str,
    transmission_lines: tuple[StackupXTransmissionLine, ...],
) -> StackupXImpedanceProfile:
    return StackupXImpedanceProfile(
        id=profile_id,
        auto_name=str(getattr(profile, "name", "") or f"Profile {index}"),
        profile_type=_impedance_profile_type_name(profile),
        impedance=str(getattr(profile, "impedance", "") or ""),
        tolerance=str(getattr(profile, "tolerance", "") or ""),
        transmission_lines=transmission_lines,
        raw_attributes=(),
    )


def _export_transmission_lines_by_profile(
    *,
    transmission_lines: tuple[object, ...],
    profile_ids_by_source: dict[str, str],
    stackups: tuple[StackupXStack, ...],
) -> dict[str, tuple[StackupXTransmissionLine, ...]]:
    default_stack_id = stackups[0].id if stackups else ""
    stack_ids_by_source = {
        _normalized_guid_key(stack.id): stack.id for stack in stackups if stack.id
    }
    layers_by_id = {
        _normalized_guid_key(layer.id): layer
        for stack in stackups
        for layer in stack.layers
        if layer.id
    }
    layers_by_stack_token = {
        (_normalized_guid_key(stack.id), token): layer
        for stack in stackups
        for token, layer in _layers_by_pair_token(stack.layers).items()
    }
    results: dict[str, list[StackupXTransmissionLine]] = {}
    for line in transmission_lines:
        profile_id = _remapped_profile_id(line, profile_ids_by_source)
        stack_id = _remapped_stack_id(line, stack_ids_by_source, default_stack_id)
        layer = _export_transmission_layer(
            source_id=getattr(line, "layer_id", ""),
            token=getattr(line, "layer_token", ""),
            stack_id=stack_id,
            layers_by_id=layers_by_id,
            layers_by_stack_token=layers_by_stack_token,
        )
        if not profile_id or layer is None:
            continue
        results.setdefault(profile_id, []).append(
            _export_transmission_line(
                line=line,
                stack_id=stack_id,
                layer=layer,
                top_ref=_export_transmission_layer(
                    source_id=getattr(line, "top_ref_id", ""),
                    token=getattr(line, "top_ref_token", ""),
                    stack_id=stack_id,
                    layers_by_id=layers_by_id,
                    layers_by_stack_token=layers_by_stack_token,
                ),
                bottom_ref=_export_transmission_layer(
                    source_id=getattr(line, "bottom_ref_id", ""),
                    token=getattr(line, "bottom_ref_token", ""),
                    stack_id=stack_id,
                    layers_by_id=layers_by_id,
                    layers_by_stack_token=layers_by_stack_token,
                ),
            )
        )
    return {key: tuple(value) for key, value in results.items()}


def _remapped_stack_id(
    line: object,
    stack_ids_by_source: dict[str, str],
    default_stack_id: str,
) -> str:
    source_id = _normalized_guid_key(getattr(line, "substack_id", ""))
    if source_id:
        return stack_ids_by_source.get(source_id, default_stack_id)
    return default_stack_id


def _export_transmission_layer(
    *,
    source_id: object,
    token: object,
    stack_id: str,
    layers_by_id: dict[str, StackupXLayer],
    layers_by_stack_token: dict[tuple[str, str], StackupXLayer],
) -> StackupXLayer | None:
    layer = layers_by_id.get(_normalized_guid_key(source_id))
    if layer is not None:
        return layer
    pair_token = _pair_token(token)
    if not pair_token:
        return None
    return layers_by_stack_token.get((_normalized_guid_key(stack_id), pair_token))


def _export_transmission_line(
    *,
    line: object,
    stack_id: str,
    layer: StackupXLayer,
    top_ref: StackupXLayer | None,
    bottom_ref: StackupXLayer | None,
) -> StackupXTransmissionLine:
    return StackupXTransmissionLine(
        layer_id=layer.id,
        stack_id=stack_id,
        is_differential=getattr(line, "is_differential", None),
        tl_type_name=str(getattr(line, "tl_type_name", "") or ""),
        calc_mode=_calc_mode_text(line),
        top_ref_layer_id=top_ref.id if top_ref is not None else "",
        bottom_ref_layer_id=bottom_ref.id if bottom_ref is not None else "",
        trace_width=str(getattr(line, "width", "") or ""),
        trace_gap=str(getattr(line, "gap", "") or ""),
        impedance_error=str(getattr(line, "impedance_error", "") or ""),
        use_solder_mask=getattr(line, "use_solder_mask", None),
        coated_height_1=str(getattr(line, "coated_height_1", "") or ""),
        coated_height_2=str(getattr(line, "coated_height_2", "") or ""),
        clearance_to_plane=str(getattr(line, "clearance_to_plane", "") or ""),
        electric_parameters=_electric_parameters_from_line(line),
        raw_attributes=(),
    )


def _electric_parameters_from_line(line: object) -> tuple[tuple[str, str], ...]:
    raw_parameters = getattr(line, "electric_parameters", ())
    if not isinstance(raw_parameters, tuple | list):
        return ()
    pairs: list[tuple[str, str]] = []
    for item in raw_parameters:
        if not isinstance(item, tuple | list) or len(item) < 2:
            continue
        pairs.append((str(item[0]), str(item[1])))
    return tuple(pairs)


def _profile_ids_by_source(
    profiles: tuple[object, ...],
    profile_ids: tuple[str, ...],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for profile, profile_id in zip(profiles, profile_ids, strict=False):
        source_id = _normalized_guid_key(getattr(profile, "source_record_id", ""))
        if source_id:
            result[source_id] = profile_id
    return result


def _remapped_profile_id(
    line: object,
    profile_ids_by_source: dict[str, str],
) -> str:
    source_id = _normalized_guid_key(getattr(line, "profile_id", ""))
    if not source_id:
        return ""
    return profile_ids_by_source.get(source_id, "")


def _impedance_profile_type_name(profile: object) -> str:
    token = str(getattr(profile, "profile_type", "") or "").strip()
    if token:
        return token
    raw = getattr(profile, "profile_type_raw", None)
    return "Differential" if raw == 1 else "Single"


def _calc_mode_text(line: object) -> str:
    raw = getattr(line, "calc_mode_raw", None)
    if isinstance(raw, int):
        return str(raw)
    return str(getattr(line, "calc_mode", "") or "")


def _normalized_guid_key(value: object) -> str:
    token = str(value or "").strip()
    if token.startswith("{") and token.endswith("}"):
        token = token[1:-1]
    return token.upper()


def _default_stackup_attributes() -> tuple[tuple[str, str], ...]:
    from .altium_layer_stack_document import AltiumStackupSettings

    return AltiumStackupSettings.default().to_stackup_attributes()


def _type_id_for_layer(layer: object) -> str:
    stackupx_type_id = _normalized_guid_key(getattr(layer, "stackupx_type_id", ""))
    if stackupx_type_id:
        return stackupx_type_id
    family = str(getattr(layer, "family", "")).strip().lower()
    if getattr(layer, "is_stiffener", None) is True:
        return _STIFFENER_TYPE_ID
    if getattr(layer, "is_adhesive", None) is True:
        return _ADHESIVE_TYPE_ID
    if family == "overlay":
        if (
            getattr(layer, "dielectric_type", None) == 4
            or getattr(layer, "dielectric_height_mils", None) is not None
            or str(getattr(layer, "coverlay_expansion", "") or "")
        ):
            return "8de65238-89ed-47c5-bbf8-0f6f02d1899c"
        return "c7ef040e-8d00-490b-b00c-a7e7823ff174"
    if family == "solder_mask":
        return "7b384237-13d8-4318-8bcb-accd8d9a51e7"
    if family == "surface_finish":
        return _SURFACE_FINISH_TYPE_ID
    if family == "plating":
        return _PLATING_TYPE_ID
    if family == "misc":
        return _MISC_TYPE_ID
    if family == "marking":
        return _MARKING_TYPE_ID
    if family == "copper":
        if _is_internal_plane_layer(layer):
            return _COPPER_PLANE_TYPE_ID
        return _COPPER_SIGNAL_TYPE_ID
    if family == "dielectric":
        dielectric_type = getattr(layer, "dielectric_type", None)
        if dielectric_type == 2:
            return "1a79611a-039d-4d40-a204-53c26c50f8b5"
        if dielectric_type == 0:
            return "136c62ef-1fa6-4897-ae71-7e797b632b92"
        return "92b02d5e-8d69-48a8-880e-ac4b77db099d"
    raise ValueError(f"Unsupported layer family for .stackupx export: {family}")


def _is_internal_plane_layer(layer: object) -> bool:
    legacy_layer_id = getattr(layer, "legacy_layer_id", None)
    return (
        isinstance(legacy_layer_id, int)
        and PcbLayer.INTERNAL_PLANE_1.value
        <= legacy_layer_id
        <= PcbLayer.INTERNAL_PLANE_16.value
    )


def _legacy_layer_id_from_stackupx_token(token: str) -> int | None:
    value = str(token or "").strip().upper()
    if value == "TOP":
        return PcbLayer.TOP.value
    if value == "BOTTOM":
        return PcbLayer.BOTTOM.value
    if value.startswith("MID") and value[3:].isdigit():
        mid_number = int(value[3:])
        if 1 <= mid_number <= 30:
            return PcbLayer.TOP.value + mid_number
        return None
    if value.startswith("PLANE") and value[5:].isdigit():
        plane_number = int(value[5:])
        if 1 <= plane_number <= 16:
            return PcbLayer.INTERNAL_PLANE_1.value + plane_number - 1
        return None
    return None


def _v7_saved_layer_id_from_stackupx_token(token: str) -> int | None:
    value = str(token or "").strip()
    if not value:
        return None
    try:
        return PcbLayerRef.parse(value).v7_saved_layer_id
    except PcbLayerResolutionError:
        return None


def _layers_by_pair_token(
    layers: tuple[StackupXLayer, ...],
) -> dict[str, StackupXLayer]:
    copper_layers = [layer for layer in layers if layer.family == "copper"]
    if not copper_layers:
        return {}
    result: dict[str, StackupXLayer] = {}
    for layer_id, token in _layer_tokens_by_stack_order(layers).items():
        layer = next((item for item in copper_layers if item.id == layer_id), None)
        if layer is not None:
            result[token] = layer
    result.setdefault("TOP", copper_layers[0])
    result.setdefault("BOTTOM", copper_layers[-1])
    return result


def _pair_token(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().upper()


def _via_span_type_raw(pair: object) -> int:
    return _int_or_default(getattr(pair, "drill_pair_type_raw", None), 0)


def _int_or_default(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    return default


def _stackupx_span_type(span_type_raw: int) -> str:
    mapping = {
        0: "ThruVia",
        1: "BlindVia",
        2: "BuriedVia",
        3: "StackedMicroVia",
    }
    return mapping.get(span_type_raw, "ThruVia")


def _span_type_raw(span: StackupXSpan) -> int | None:
    mapping = {
        "ThruVia": 0,
        "BlindVia": 1,
        "BuriedVia": 2,
        "StackedMicroVia": 3,
    }
    return mapping.get(span.span_type)


def _via_span_name_prefix(span_type_raw: int) -> str:
    if span_type_raw == 2:
        return "Buried"
    if span_type_raw == 3:
        return "MicroVia"
    if span_type_raw == 1:
        return "Blind"
    return "Thru"


def _span_name(
    prefix: str,
    start: StackupXLayer,
    stop: StackupXLayer,
    layers: tuple[StackupXLayer, ...],
) -> str:
    copper_layers = [layer for layer in layers if layer.family == "copper"]
    first = copper_layers.index(start) + 1
    last = copper_layers.index(stop) + 1
    return f"{prefix} {first}:{last}"


def _dielectric_type_from_type_id(type_id: str) -> int | None:
    token = str(type_id or "").strip().lower()
    if token == "1a79611a-039d-4d40-a204-53c26c50f8b5":
        return 2
    if token == "136c62ef-1fa6-4897-ae71-7e797b632b92":
        return 0
    if token == "8de65238-89ed-47c5-bbf8-0f6f02d1899c":
        return 4
    return None


def _component_placement_name(value: object) -> str:
    if value is None:
        return ""
    mapping = {0: "None", 1: "BodyUp", 2: "BodyDown"}
    if isinstance(value, int):
        return mapping.get(value, str(value))
    token = str(value).strip()
    try:
        numeric = int(token)
    except ValueError:
        return token
    return mapping.get(numeric, token)


def _float_export_value(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    token = str(value).strip()
    if not token:
        return None
    try:
        return float(token)
    except ValueError:
        return None


def _stackupx_guid(*parts: object) -> str:
    seed = "|".join(str(part or "") for part in parts)
    return str(uuid.uuid5(_EXPORT_NAMESPACE, seed))


def _is_guid_text(value: object) -> bool:
    token = str(value or "").strip()
    if not token:
        return False
    try:
        uuid.UUID(token.strip("{}"))
    except ValueError:
        return False
    return True


def _non_guid_board_id_problems(document: "AltiumStackupXDocument") -> tuple[str, ...]:
    """List ids that Altium's Layer Stack Manager would reject as non-GUID."""
    problems: list[str] = []

    def _check(kind: str, value: str) -> None:
        if not _is_guid_text(value):
            problems.append(f"{kind} id {value!r} is not a GUID")

    _check("document", document.id)
    _check("document revision", document.revision_id)
    for stack in document.stacks:
        _check("stack", stack.id)
        for layer in stack.layers:
            _check("layer", layer.id)
        for span in (*stack.via_spans, *stack.drill_spans):
            _check("span", span.id)
    return tuple(problems)


def _guard_board_writable_ids(document: "AltiumStackupXDocument") -> None:
    problems = _non_guid_board_id_problems(document)
    if not problems:
        return
    shown = "; ".join(problems[:8])
    extra = len(problems) - 8
    if extra > 0:
        shown = f"{shown}; and {extra} more"
    raise ValueError(
        "StackUpX stack, layer, and span ids must be GUID strings; Altium's "
        f"Layer Stack Manager rejects anything else: {shown}. Omit the id "
        "arguments to use generated GUIDs, or supply valid GUID strings."
    )


def _existing_or_stackupx_guid(*parts: object) -> str:
    for part in parts:
        token = str(part or "").strip()
        if not token:
            continue
        try:
            return str(uuid.UUID(token.strip("{}")))
        except ValueError:
            continue
    return _stackupx_guid(*parts)


def _xml_bool(value: bool | None) -> str:
    return "True" if bool(value) else "False"


def _qname(name: str) -> str:
    return f"{{{STACKUPX_NAMESPACE}}}{name}"
