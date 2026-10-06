"""Typed public inventory objects for selectable Altium assets."""

from __future__ import annotations

import os
import uuid
from dataclasses import asdict, dataclass, field
from typing import Literal, Mapping, TypeAlias, cast

from .altium_api_markers import public_api
from .altium_embedded_assets import EmbeddedAssetInventory, EmbeddedAssetReference
from .altium_json_helpers import json_ready as _json_ready

_EXTRACTABLE_ASSET_SCHEMA = "altium_monkey.extractable_assets.a0"

AssetKind = Literal[
    "embedded_model",
    "embedded_font",
    "opaque_embedded",
    "pcb_footprint",
    "sch_symbol",
    "sch_image",
]
SourceKind = Literal["pcbdoc", "pcblib", "schdoc", "schlib", "intlib", "project"]


@public_api
@dataclass(frozen=True)
class AltiumAssetRef:
    """Source-local reference for one extractable asset.

    File-backed refs are durable through normalized source_path identity. Refs
    from unsaved/live containers carry a process-local source_instance_id and
    are only valid for that live container instance in the current process.
    """

    source_kind: SourceKind
    source_path: str | None
    kind: AssetKind
    key: str
    index: int | None = None
    name: str | None = None
    source_instance_id: str | None = None


@public_api
@dataclass(frozen=True)
class EmbeddedModelAssetDetails:
    """Typed details for an embedded model extractable asset."""

    model_format: str = ""
    id: str = ""
    compressed_size: int = 0
    decompressed_size: int = 0
    is_embedded: bool = False
    references: tuple[EmbeddedAssetReference, ...] = ()


@public_api
@dataclass(frozen=True)
class EmbeddedFontAssetDetails:
    """Typed details for an embedded font extractable asset."""

    style: str = ""
    compressed_size: int = 0
    decompressed_size: int = 0
    raw_size: int = 0
    support_status: str = ""


@public_api
@dataclass(frozen=True)
class OpaqueEmbeddedAssetDetails:
    """Typed details for an opaque embedded extractable asset."""

    raw_size: int = 0
    support_status: str = ""
    reason: str = ""


@public_api
@dataclass(frozen=True)
class PcbFootprintAssetDetails:
    """Typed details for a PCB footprint extractable asset."""

    pattern: str = ""
    occurrence: int = 0
    component_indices: tuple[int, ...] = ()
    component_designators: tuple[str, ...] = ()
    component_count: int = 0
    source_footprint_library: str = ""
    pad_count: int = 0
    primitive_count: int = 0


@public_api
@dataclass(frozen=True)
class SchSymbolAssetDetails:
    """Typed details for a schematic symbol extractable asset."""

    display_name: str = ""
    safe_name: str = ""
    component_count: int = 0
    component_designators: tuple[str, ...] = ()
    selected_designator: str = ""
    lib_reference: str = ""
    design_item_id: str = ""
    original_name: str = ""
    description: str = ""
    pin_count: int = 0
    object_count: int = 0
    part_count: int | None = None


AltiumAssetDetails: TypeAlias = (
    EmbeddedModelAssetDetails
    | EmbeddedFontAssetDetails
    | OpaqueEmbeddedAssetDetails
    | PcbFootprintAssetDetails
    | SchSymbolAssetDetails
)


@public_api
@dataclass(frozen=True)
class AltiumAssetSummary:
    """Read-only summary for a user-selectable extractable asset."""

    ref: AltiumAssetRef
    kind: AssetKind
    name: str
    extraction_filename: str | None
    native_extension: str | None
    can_extract: bool
    payload_available: bool
    payload_sha256: str | None = None
    details: AltiumAssetDetails = field(default_factory=EmbeddedModelAssetDetails)
    extras: Mapping[str, object] = field(default_factory=dict)


@public_api
@dataclass(frozen=True)
class AltiumAssetInventory:
    """Aggregate read-only inventory for user-selectable assets."""

    source_kind: SourceKind
    source_path: str | None
    assets: tuple[AltiumAssetSummary, ...]

    def by_kind(self, kind: AssetKind) -> tuple[AltiumAssetSummary, ...]:
        """Return inventory entries for one asset kind."""
        return tuple(asset for asset in self.assets if asset.kind == kind)

    def to_dict(self, *, include_schema: bool = True) -> dict[str, object]:
        """Return the stable downstream JSON-ready inventory shape."""
        data = cast(dict[str, object], _json_ready(asdict(self)))
        if include_schema:
            return {"schema": _EXTRACTABLE_ASSET_SCHEMA, **data}
        return data


@public_api
@dataclass(frozen=True)
class AltiumExtractedAsset:
    """Result of extracting exactly one selected asset."""

    ref: AltiumAssetRef
    filename: str
    payload: bytes | None = None
    pcblib: object | None = None
    schlib: object | None = None


def extractable_asset_schema_id() -> str:
    """Return the current extractable asset inventory JSON schema id."""
    return _EXTRACTABLE_ASSET_SCHEMA


def asset_key(kind: AssetKind, index: int) -> str:
    """Return the source-local stable key for one inventory entry."""
    return f"{kind}:{index}"


def semantic_asset_key(kind: AssetKind, *parts: object) -> str:
    """Return a stable key from semantic source-local identity fields."""
    encoded = [kind]
    for part in parts:
        text = "" if part is None else str(part)
        encoded.append(f"{len(text.encode('utf-8'))}:{text}")
    return "|".join(encoded)


def _source_path_identity(source_path: str | None) -> str | None:
    if source_path in (None, ""):
        return None
    return os.path.normcase(os.path.abspath(os.path.normpath(source_path)))


def source_instance_id_for(owner: object, source_path: str | None) -> str | None:
    """Return a stable identity token for one unsaved/live source container."""
    if source_path not in (None, ""):
        return None
    attribute_name = "_altium_asset_source_instance_id"
    source_instance_id = getattr(owner, attribute_name, None)
    if not source_instance_id:
        source_instance_id = f"py:{uuid.uuid4().hex}"
        setattr(owner, attribute_name, source_instance_id)
    return str(source_instance_id)


def _extractable_size(value: int | None) -> int:
    """Return the generic extractable JSON size convention for optional sizes."""
    return 0 if value is None else int(value)


def selected_asset_index(
    ref_or_name_or_index: object,
    *,
    summaries: tuple[AltiumAssetSummary, ...],
    expected_source_kind: SourceKind,
    expected_kind: AssetKind,
) -> int:
    """Resolve a public selection input to an inventory index."""
    if isinstance(ref_or_name_or_index, AltiumAssetSummary):
        ref_or_name_or_index = ref_or_name_or_index.ref

    if isinstance(ref_or_name_or_index, AltiumAssetRef):
        ref = ref_or_name_or_index
        if ref.source_kind != expected_source_kind:
            raise ValueError(
                f"asset reference source mismatch: expected {expected_source_kind}, "
                f"got {ref.source_kind}"
            )
        if ref.kind != expected_kind:
            raise ValueError(
                f"asset reference kind mismatch: expected {expected_kind}, got {ref.kind}"
            )
        if ref.index is None:
            raise ValueError("asset reference is missing an index")
        index = ref.index
    elif isinstance(ref_or_name_or_index, int):
        index = ref_or_name_or_index
    elif isinstance(ref_or_name_or_index, str):
        matches = [
            int(summary.ref.index)
            for summary in summaries
            if summary.name == ref_or_name_or_index and summary.ref.index is not None
        ]
        if not matches:
            raise KeyError(f"unknown {expected_kind} asset: {ref_or_name_or_index!r}")
        if len(matches) > 1:
            raise ValueError(
                f"ambiguous {expected_kind} asset name: {ref_or_name_or_index!r}"
            )
        index = matches[0]
    else:
        raise TypeError(
            "asset selector must be an AltiumAssetRef, AltiumAssetSummary, "
            "integer index, or exact asset name"
        )

    if index < 0 or index >= len(summaries):
        raise IndexError(f"{expected_kind} asset index out of range: {index}")
    if isinstance(ref_or_name_or_index, AltiumAssetRef):
        summary_ref = summaries[index].ref
        expected_source_path = _source_path_identity(summary_ref.source_path)
        actual_source_path = _source_path_identity(ref_or_name_or_index.source_path)
        if actual_source_path != expected_source_path:
            raise ValueError(
                f"asset reference source path mismatch: expected "
                f"{summary_ref.source_path!r}, got {ref_or_name_or_index.source_path!r}"
            )
        expected_source_instance = summary_ref.source_instance_id or None
        actual_source_instance = ref_or_name_or_index.source_instance_id or None
        if actual_source_instance != expected_source_instance:
            raise ValueError("asset reference source instance mismatch")
        if not ref_or_name_or_index.key:
            raise KeyError(f"{expected_kind} asset reference is missing a key")
        expected_key = summary_ref.key
        if ref_or_name_or_index.key != expected_key:
            raise KeyError(
                f"stale {expected_kind} asset reference key: "
                f"expected {expected_key!r}, got {ref_or_name_or_index.key!r}"
            )
    return index


def embedded_inventory_asset_summaries(
    inventory: EmbeddedAssetInventory, *, source_instance_id: str | None = None
) -> tuple[AltiumAssetSummary, ...]:
    """Convert embedded-binary inventory entries into extractable summaries."""
    assets: list[AltiumAssetSummary] = []

    for model in inventory.models:
        kind: AssetKind = "embedded_model"
        index = int(model.index)
        assets.append(
            AltiumAssetSummary(
                ref=AltiumAssetRef(
                    source_kind=inventory.source_kind,
                    source_path=inventory.source_path,
                    kind=kind,
                    key=semantic_asset_key(kind, index, model.name, model.id),
                    index=index,
                    name=model.name,
                    source_instance_id=source_instance_id,
                ),
                kind=kind,
                name=model.name,
                extraction_filename=model.extraction_filename,
                native_extension=model.extraction_filename.rsplit(".", 1)[-1]
                if "." in model.extraction_filename
                else None,
                can_extract=model.payload_available,
                payload_available=model.payload_available,
                payload_sha256=model.payload_sha256,
                details=EmbeddedModelAssetDetails(
                    model_format=model.model_format,
                    id=model.id,
                    compressed_size=model.compressed_size,
                    decompressed_size=_extractable_size(model.decompressed_size),
                    is_embedded=model.is_embedded,
                    references=model.references,
                ),
            )
        )

    for font in inventory.fonts:
        kind = "embedded_font"
        index = int(font.index)
        assets.append(
            AltiumAssetSummary(
                ref=AltiumAssetRef(
                    source_kind=inventory.source_kind,
                    source_path=inventory.source_path,
                    kind=kind,
                    key=semantic_asset_key(
                        kind,
                        index,
                        font.family_name,
                        font.style,
                        font.extraction_filename,
                    ),
                    index=index,
                    name=font.family_name,
                    source_instance_id=source_instance_id,
                ),
                kind=kind,
                name=font.family_name,
                extraction_filename=font.extraction_filename,
                native_extension="ttf",
                can_extract=font.payload_available,
                payload_available=font.payload_available,
                payload_sha256=font.payload_sha256,
                details=EmbeddedFontAssetDetails(
                    style=font.style,
                    compressed_size=_extractable_size(font.compressed_size),
                    decompressed_size=_extractable_size(font.decompressed_size),
                    raw_size=_extractable_size(font.raw_size),
                    support_status=font.support_status,
                ),
            )
        )

    for opaque in inventory.opaque_assets:
        kind = "opaque_embedded"
        index = int(opaque.index)
        assets.append(
            AltiumAssetSummary(
                ref=AltiumAssetRef(
                    source_kind=inventory.source_kind,
                    source_path=inventory.source_path,
                    kind=kind,
                    key=semantic_asset_key(kind, index, opaque.stream_name),
                    index=index,
                    name=opaque.stream_name,
                    source_instance_id=source_instance_id,
                ),
                kind=kind,
                name=opaque.stream_name,
                extraction_filename=opaque.extraction_filename,
                native_extension=None,
                can_extract=opaque.payload_available,
                payload_available=opaque.payload_available,
                payload_sha256=opaque.payload_sha256,
                details=OpaqueEmbeddedAssetDetails(
                    raw_size=opaque.raw_size or 0,
                    support_status=opaque.support_status,
                    reason=opaque.reason or "",
                ),
            )
        )

    return tuple(assets)
