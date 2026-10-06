"""Read, write, and extract Altium integrated libraries."""

from __future__ import annotations

import hashlib
import os
import struct
import sys
import tempfile
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ._safe_artifact_name import dedupe_artifact_basename, safe_artifact_basename
from ._intlib_collation import intlib_name_sort_key
from .altium_ole import AltiumOleFile, AltiumOleWriter

if TYPE_CHECKING:
    from .altium_pcblib import AltiumPcbFootprint
    from .altium_schlib import AltiumSymbol

_COMPRESSED_PREFIX = 0x02
_RAW_PREFIX = 0x00
_ZLIB_PREFIX = b"\x78"
_DEFAULT_MAX_DECOMPRESSED_STREAM_BYTES = 268_435_456
_SOURCE_STREAM_ROOTS = frozenset({"SchLib", "PCBLib", "PCB3DLib"})
_METADATA_STREAMS = frozenset({"Version.Txt", "LibCrossRef.Txt", "Parameters   .bin"})
_ENVELOPED_METADATA_STREAMS = frozenset({"LibCrossRef.Txt", "Parameters   .bin"})
_VERSION_DATA = b"\x00\x02\x00\x00\x00"
_ZERO_CLSID = b"\x00" * 16
_WRITER_METADATA_STREAMS = frozenset(
    {"Version.Txt", "LibCrossRef.Txt", "Parameters   .bin"}
)
_RESERVED_COMPONENT_FIELDS = frozenset(
    {
        "comment",
        "component kind",
        "component type",
        "description",
        "designator",
        "footprint",
        "footprint doc",
        "library reference",
        "pad count",
        "pin count",
    }
)


@dataclass(frozen=True)
class IntLibModel:
    """
    Model entry referenced by one component in an integrated library.

    Attributes:
        name: Model name stored in `LibCrossRef.Txt`.
        model_type: Native model type label such as `PCBLIB` or `SI`.
        virtual_path: Integrated-library stream path, or `None` for models
            stored only as component metadata.
        source_path: Original source path captured by Altium at compile time,
            or `None` when the model has no embedded source stream.
    """

    name: str
    model_type: str
    virtual_path: str | None = None
    source_path: str | None = None


@dataclass(frozen=True)
class IntLibComponent:
    """
    Component entry referenced by an integrated library.

    Attributes:
        name: Component library reference.
        virtual_path: Integrated-library schematic source stream path.
        description: Component description from `LibCrossRef.Txt`.
        source_path: Original source path captured by Altium at compile time.
        models: Model entries associated with this component.
    """

    name: str
    virtual_path: str
    description: str
    source_path: str
    models: tuple[IntLibModel, ...]


@dataclass(frozen=True)
class IntLibSource:
    """
    One extractable source stream inside an integrated library.

    Attributes:
        kind: Top-level source family, for example `SchLib`, `PCBLib`, or
            `PCB3DLib`.
        stream_path: OLE stream path using `/` separators.
        original_path: Original path recorded by Altium, if present.
        suggested_filename: Safe output filename inferred from the original
            path or stream name.
        output_path: File path written by `AltiumIntLib.extract_sources`, or
            `None` before extraction.
    """

    kind: str
    stream_path: str
    original_path: str | None
    suggested_filename: str
    output_path: Path | None = None


@dataclass(frozen=True)
class IntLibExtractionResult:
    """
    Result returned by `AltiumIntLib.extract_sources`.

    Attributes:
        output_dir: Directory that received the extracted source files.
        sources: Extracted source files with their output paths populated.
        libpkg_path: Generated LibPkg path when requested, otherwise `None`.
    """

    output_dir: Path
    sources: tuple[IntLibSource, ...]
    libpkg_path: Path | None


class IntLibBuildWarningCode(StrEnum):
    """Stable warning codes returned by the IntLib writer."""

    UNUSED_FOOTPRINT = "unused_footprint"


@dataclass(frozen=True, slots=True)
class IntLibBuildWarning:
    """One non-fatal condition reported by an IntLib build."""

    code: IntLibBuildWarningCode
    subject: str
    message: str


@dataclass(frozen=True, slots=True)
class IntLibBuildSource:
    """One immutable source packaged by an IntLib build."""

    kind: Literal["SchLib", "PCBLib"]
    logical_name: str
    stream_path: str
    sha256: str
    entity_count: int


@dataclass(frozen=True, slots=True)
class IntLibBuildResult:
    """Immutable summary returned after a validated IntLib is published."""

    output_path: Path
    output_sha256: str
    sources: tuple[IntLibBuildSource, ...]
    component_count: int
    footprint_count: int
    model_count: int
    parameter_record_count: int
    source_count: int
    warnings: tuple[IntLibBuildWarning, ...]


@dataclass(frozen=True, slots=True)
class _SourceSnapshot:
    kind: Literal["SchLib", "PCBLib"]
    index: int
    logical_name: str
    data: bytes
    sha256: str

    @property
    def stream_path(self) -> str:
        extension = "schlib" if self.kind == "SchLib" else "pcblib"
        return f"{self.kind}/{self.index}.{extension}"

    @property
    def virtual_path(self) -> str:
        return ":\\" + self.stream_path.replace("/", "\\")


@dataclass(frozen=True, slots=True)
class _ComponentFacts:
    source: _SourceSnapshot
    name: str
    description: str
    designator: str
    component_kind: str
    component_type: str
    pin_count: int
    parameters: tuple[tuple[str, str], ...]
    models: tuple[_FootprintFacts, ...]
    current_model: str | None


@dataclass(frozen=True, slots=True)
class _FootprintFacts:
    source: _SourceSnapshot
    name: str
    description: str
    height: str
    pad_count: int


@dataclass(frozen=True, slots=True)
class _PreparedFacts:
    components: tuple[_ComponentFacts, ...]
    footprints: tuple[_FootprintFacts, ...]
    unused_footprints: tuple[str, ...]
    entity_counts: dict[str, int]


class _BinaryCursor:
    """Small bounds-checked cursor for IntLib metadata streams."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self.position = 0

    def read_u32(self) -> int:
        if self.position + 4 > len(self._data):
            raise ValueError("Unexpected end of IntLib cross-reference stream")
        value = int.from_bytes(self._data[self.position : self.position + 4], "little")
        self.position += 4
        return value

    def read_altium_string(self) -> str:
        byte_count = self.read_u32()
        payload = self._read_bytes(byte_count)
        if not payload:
            return ""
        text_length = payload[0]
        text_bytes = payload[1 : 1 + text_length]
        return _decode_metadata_string(text_bytes)

    def _read_bytes(self, size: int) -> bytes:
        if size < 0 or self.position + size > len(self._data):
            raise ValueError("Unexpected end of IntLib cross-reference stream")
        value = self._data[self.position : self.position + size]
        self.position += size
        return value


class AltiumIntLib:
    """
    Read, create, and extract Altium `.IntLib` files.

    Creation packages ordered, already-prepared SchLib and PcbLib sources.
    Source-library merging and application catalog policy remain separate
    operations.
    """

    def __init__(
        self,
        filepath: str | Path,
        *,
        max_decompressed_stream_bytes: int = _DEFAULT_MAX_DECOMPRESSED_STREAM_BYTES,
    ) -> None:
        """
        Open and parse an integrated library file.

        Args:
            filepath: Path to the `.IntLib` file.
            max_decompressed_stream_bytes: Maximum decoded bytes allowed for
                each compressed stream. Must be positive and less than
                `sys.maxsize`.
        """
        self._max_decompressed_stream_bytes = _validate_max_decompressed_stream_bytes(
            max_decompressed_stream_bytes
        )
        self.filepath = Path(filepath)
        self._ole = AltiumOleFile(str(self.filepath))
        self._component_parse_error: str | None = None
        try:
            self._components = self._parse_components()
        except ValueError as exc:
            self._component_parse_error = str(exc)
            self._components = ()

    @classmethod
    def from_file(
        cls,
        filepath: str | Path,
        *,
        max_decompressed_stream_bytes: int = _DEFAULT_MAX_DECOMPRESSED_STREAM_BYTES,
    ) -> "AltiumIntLib":
        """
        Open an integrated library from disk.

        Args:
            filepath: Path to the `.IntLib` file.
            max_decompressed_stream_bytes: Maximum decoded bytes allowed for
                each compressed stream. Must be positive and less than
                `sys.maxsize`.

        Returns:
            Parsed `AltiumIntLib` instance.
        """
        return cls(
            filepath,
            max_decompressed_stream_bytes=max_decompressed_stream_bytes,
        )

    @classmethod
    def create_from_libraries(
        cls,
        output_path: str | Path,
        *,
        schlib_paths: Sequence[str | Path],
        pcblib_paths: Sequence[str | Path] = (),
        overwrite: bool = False,
    ) -> IntLibBuildResult:
        """Create an IntLib from ordered prepared source libraries.

        Each source path is read exactly once before parsing. The captured
        bytes are preserved exactly inside the IntLib. The result is staged,
        reopened, validated, and atomically published. Existing output is not
        replaced unless ``overwrite`` is true.

        Args:
            output_path: Destination `.IntLib` path.
            schlib_paths: One or more prepared `.SchLib` paths in stream order.
            pcblib_paths: Prepared `.PcbLib` paths in stream order.
            overwrite: Replace an existing destination when true.

        Returns:
            Build hashes, counts, and typed non-fatal warnings.
        """
        destination = Path(output_path)
        schlib_sources = _coerce_source_paths(schlib_paths, "schlib_paths")
        pcblib_sources = _coerce_source_paths(pcblib_paths, "pcblib_paths")
        if not schlib_sources:
            raise ValueError("schlib_paths must contain at least one source")
        _validate_build_paths(
            destination,
            source_paths=(*schlib_sources, *pcblib_sources),
            overwrite=overwrite,
        )
        schlibs = tuple(
            _capture_source(path, kind="SchLib", index=index)
            for index, path in enumerate(schlib_sources)
        )
        pcblibs = tuple(
            _capture_source(path, kind="PCBLib", index=index)
            for index, path in enumerate(pcblib_sources)
        )
        sources = (*schlibs, *pcblibs)
        facts = _inspect_prepared_sources(schlibs, pcblibs)
        writer = _build_intlib_writer(sources, facts)
        staged_path = _allocate_staged_path(destination)
        try:
            writer.write(staged_path)
            _sync_staged_file(staged_path)
            _validate_staged_intlib(staged_path, sources, facts)
            output_sha256 = _sha256(staged_path.read_bytes())
            _publish_staged_intlib(staged_path, destination, overwrite=overwrite)
        finally:
            staged_path.unlink(missing_ok=True)
        return _build_result(
            destination,
            output_sha256=output_sha256,
            sources=sources,
            facts=facts,
        )

    @property
    def components(self) -> tuple[IntLibComponent, ...]:
        """
        Component records parsed from `LibCrossRef.Txt`.
        """
        return self._components

    @property
    def component_parse_error(self) -> str | None:
        """
        Cross-reference parse error, if component metadata could not be read.

        Some vendor-generated IntLibs contain extractable source streams but
        malformed cross-reference metadata. In that case source extraction can
        still proceed by scanning OLE stream paths directly.
        """
        return self._component_parse_error

    @property
    def stream_paths(self) -> tuple[str, ...]:
        """
        OLE stream paths in the integrated library using `/` separators.
        """
        return tuple("/".join(parts) for parts in self._ole.listdir(streams=True))

    def get_source_entries(self) -> tuple[IntLibSource, ...]:
        """
        Return unique embedded source streams that can be extracted.

        The returned entries are deduplicated by stream path. Original Altium
        source paths are used only to infer friendly basenames; source
        directories are never reused.
        """
        sources: dict[str, IntLibSource] = {}
        for component in self.components:
            self._add_source_entry(
                sources,
                component.virtual_path,
                component.source_path,
            )
            for model in component.models:
                self._add_source_entry(sources, model.virtual_path, model.source_path)

        for stream_path in self.stream_paths:
            if _is_source_stream_path(stream_path):
                self._add_source_entry(sources, stream_path, None)

        return tuple(sources.values())

    def read_stream(self, stream_path: str, *, decompress: bool = True) -> bytes:
        """
        Read one OLE stream from the integrated library.

        Args:
            stream_path: OLE stream path. Both native IntLib virtual paths such
                as `:\\SchLib\\0.schlib` and normalized OLE paths such as
                `SchLib/0.schlib` are accepted.
            decompress: When true, remove Altium's IntLib compression wrapper
                from compressed streams.

        Returns:
            Stream bytes. Source streams are returned as normal SchLib/PcbLib
            file bytes when `decompress` is true.
        """
        ole_path = _normalize_virtual_path(stream_path)
        data = self._ole.openstream(ole_path)
        if not decompress:
            return data
        return _decompress_intlib_stream(
            data,
            stream_path=ole_path,
            max_decompressed_bytes=self._max_decompressed_stream_bytes,
        )

    def extract_sources(
        self,
        output_dir: str | Path,
        *,
        overwrite: bool = True,
        use_original_filenames: bool = True,
        write_libpkg: bool = True,
    ) -> IntLibExtractionResult:
        """
        Extract embedded source streams from the integrated library.

        Args:
            output_dir: Destination directory.
            overwrite: Replace existing files when true.
            use_original_filenames: Use original source basenames recorded by
                Altium when available. When false, stream basenames such as
                `0.schlib` are used.
            write_libpkg: Also write a simple `.LibPkg` that references the
                extracted source files with relative paths.

        Returns:
            `IntLibExtractionResult` describing the extracted files.
        """
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)

        extracted = self._extract_source_files(
            destination,
            overwrite=overwrite,
            use_original_filenames=use_original_filenames,
        )
        libpkg_path = None
        if write_libpkg:
            libpkg_path = self._write_libpkg(destination, extracted)

        return IntLibExtractionResult(
            output_dir=destination,
            sources=tuple(extracted),
            libpkg_path=libpkg_path,
        )

    def close(self) -> None:
        """Release the parsed OLE container state."""
        self._ole.close()

    def __enter__(self) -> "AltiumIntLib":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def _parse_components(self) -> tuple[IntLibComponent, ...]:
        if not self._ole.exists("LibCrossRef.Txt"):
            return ()

        data = self.read_stream("LibCrossRef.Txt")
        cursor = _BinaryCursor(data)
        component_count = cursor.read_u32()
        components = [self._parse_component(cursor) for _ in range(component_count)]
        if cursor.position != len(data):
            raise ValueError("IntLib cross-reference stream has trailing data")
        return tuple(components)

    def _parse_component(self, cursor: _BinaryCursor) -> IntLibComponent:
        name = cursor.read_altium_string()
        virtual_path = cursor.read_altium_string()
        cursor.read_u32()
        description = cursor.read_altium_string()
        source_path = cursor.read_altium_string()
        models = tuple(self._parse_model(cursor) for _ in range(cursor.read_u32()))
        return IntLibComponent(
            name=name,
            virtual_path=virtual_path,
            description=description,
            source_path=source_path,
            models=models,
        )

    def _parse_model(self, cursor: _BinaryCursor) -> IntLibModel:
        name = cursor.read_altium_string()
        model_type = cursor.read_altium_string()
        has_source_stream = cursor.read_u32() != 0
        if not has_source_stream:
            return IntLibModel(name=name, model_type=model_type)

        return IntLibModel(
            name=name,
            model_type=model_type,
            virtual_path=cursor.read_altium_string(),
            source_path=cursor.read_altium_string(),
        )

    def _add_source_entry(
        self,
        sources: dict[str, IntLibSource],
        virtual_path: str | None,
        original_path: str | None,
    ) -> None:
        if not virtual_path:
            return
        stream_path = _normalize_virtual_path(virtual_path)
        if stream_path in sources or not _is_source_stream_path(stream_path):
            return
        kind = stream_path.split("/", 1)[0]
        sources[stream_path] = IntLibSource(
            kind=kind,
            stream_path=stream_path,
            original_path=original_path,
            suggested_filename=_suggest_filename(stream_path, original_path),
        )

    def _extract_source_files(
        self,
        output_dir: Path,
        *,
        overwrite: bool,
        use_original_filenames: bool,
    ) -> list[IntLibSource]:
        extracted: list[IntLibSource] = []
        used_by_kind: dict[str, dict[str, int]] = {}
        for source in self.get_source_entries():
            kind_dir = output_dir / source.kind
            kind_dir.mkdir(parents=True, exist_ok=True)
            filename = _choose_output_filename(source, use_original_filenames)
            filename = _dedupe_filename(
                filename, used_by_kind.setdefault(source.kind, {})
            )
            output_path = kind_dir / filename
            if output_path.exists() and not overwrite:
                raise FileExistsError(output_path)
            output_path.write_bytes(self.read_stream(source.stream_path))
            extracted.append(
                replace(source, suggested_filename=filename, output_path=output_path)
            )
        return extracted

    def _write_libpkg(self, output_dir: Path, sources: list[IntLibSource]) -> Path:
        libpkg_path = output_dir / f"{self.filepath.stem}.LibPkg"
        lines = [
            "[Design]",
            "Version=1.0",
            "HierarchyMode=0",
            "OutputPath=Project Outputs",
            "",
        ]
        for index, source in enumerate(sources, start=1):
            if source.output_path is None:
                continue
            relative_path = source.output_path.relative_to(output_dir)
            document_path = "\\".join(relative_path.parts)
            lines.extend(
                [
                    f"[Document{index}]",
                    f"DocumentPath={document_path}",
                    "DocumentUniqueId=",
                    "",
                ]
            )
        libpkg_path.write_text("\n".join(lines), encoding="utf-8")
        return libpkg_path


def _coerce_source_paths(paths: Sequence[str | Path], label: str) -> tuple[Path, ...]:
    if isinstance(paths, (str, Path)):
        raise TypeError(f"{label} must be an ordered sequence of paths")
    return tuple(Path(path) for path in paths)


def _validate_build_paths(
    output_path: Path,
    *,
    source_paths: Sequence[Path],
    overwrite: bool,
) -> None:
    if output_path.exists() and output_path.is_dir():
        raise IsADirectoryError(output_path)
    if output_path.exists() and not overwrite:
        raise FileExistsError(output_path)
    output_resolved = output_path.resolve(strict=False)
    for source in source_paths:
        if source.resolve(strict=False) == output_resolved:
            raise ValueError("IntLib output path must differ from every source path")
    output_path.parent.mkdir(parents=True, exist_ok=True)


def _capture_source(
    path: Path,
    *,
    kind: Literal["SchLib", "PCBLib"],
    index: int,
) -> _SourceSnapshot:
    if not path.is_file():
        if path.exists():
            raise IsADirectoryError(path)
        raise FileNotFoundError(path)
    data = path.read_bytes()
    logical_name = path.name
    _encode_altium_string(logical_name)
    return _SourceSnapshot(
        kind=kind,
        index=index,
        logical_name=logical_name,
        data=data,
        sha256=_sha256(data),
    )


def _inspect_prepared_sources(
    schlibs: Sequence[_SourceSnapshot],
    pcblibs: Sequence[_SourceSnapshot],
) -> _PreparedFacts:
    entity_counts: dict[str, int] = {}
    footprints = _inspect_footprint_sources(pcblibs, entity_counts)
    _require_unique_names((item.name for item in footprints), "footprint")
    footprints_by_fold = {item.name.casefold(): item for item in footprints}
    components = _inspect_component_sources(
        schlibs,
        footprints_by_fold,
        entity_counts,
    )
    _require_unique_names((item.name for item in components), "component")
    linked = {
        model.name.casefold() for component in components for model in component.models
    }
    unused = tuple(
        sorted(
            (item.name for item in footprints if item.name.casefold() not in linked),
            key=intlib_name_sort_key,
        )
    )
    return _PreparedFacts(
        components=components,
        footprints=footprints,
        unused_footprints=unused,
        entity_counts=entity_counts,
    )


def _inspect_footprint_sources(
    pcblibs: Sequence[_SourceSnapshot],
    entity_counts: dict[str, int],
) -> tuple[_FootprintFacts, ...]:
    from .altium_pcblib import AltiumPcbLib

    footprint_rows: list[_FootprintFacts] = []
    for source in pcblibs:
        pcb_library = AltiumPcbLib.from_bytes(
            source.data,
            filename=source.logical_name,
        )
        source_footprints = tuple(
            _footprint_facts(item, source) for item in pcb_library.footprints
        )
        entity_counts[source.stream_path] = len(source_footprints)
        footprint_rows.extend(source_footprints)
    return tuple(footprint_rows)


def _inspect_component_sources(
    schlibs: Sequence[_SourceSnapshot],
    footprints_by_fold: dict[str, _FootprintFacts],
    entity_counts: dict[str, int],
) -> tuple[_ComponentFacts, ...]:
    from .altium_schlib import AltiumSchLib

    component_rows: list[_ComponentFacts] = []
    with tempfile.TemporaryDirectory(prefix="altium-monkey-intlib-") as temp_dir:
        for source in schlibs:
            snapshot_path = Path(temp_dir) / f"{source.index}.SchLib"
            snapshot_path.write_bytes(source.data)
            schematic_library = AltiumSchLib(snapshot_path)
            source_components = tuple(
                _component_facts(symbol, source, footprints_by_fold)
                for symbol in schematic_library.symbols
            )
            entity_counts[source.stream_path] = len(source_components)
            component_rows.extend(source_components)
    return tuple(
        sorted(component_rows, key=lambda item: intlib_name_sort_key(item.name))
    )


def _footprint_facts(
    footprint: AltiumPcbFootprint, source: _SourceSnapshot
) -> _FootprintFacts:
    return _FootprintFacts(
        source=source,
        name=str(footprint.name),
        description=str(footprint.parameters.get("DESCRIPTION", "")),
        height=_normalize_height(footprint.parameters.get("HEIGHT", "0")),
        pad_count=len(footprint.pads),
    )


def _component_facts(
    symbol: AltiumSymbol,
    source: _SourceSnapshot,
    footprints_by_fold: dict[str, _FootprintFacts],
) -> _ComponentFacts:
    name = str(symbol.original_name or symbol.name)
    models, current_model = _implementation_facts(
        name,
        tuple(symbol.implementations),
        footprints_by_fold,
    )
    designators = tuple(symbol.designators)
    designator = str(designators[0].text) if designators else name
    component_kind = _component_kind_name(symbol)
    return _ComponentFacts(
        source=source,
        name=name,
        description=str(symbol.description),
        designator=designator,
        component_kind=component_kind,
        component_type=component_kind,
        pin_count=_component_pin_count(symbol),
        parameters=_component_parameter_pairs(symbol),
        models=models,
        current_model=current_model,
    )


def _implementation_facts(
    component_name: str,
    implementations: Sequence[object],
    footprints_by_fold: dict[str, _FootprintFacts],
) -> tuple[tuple[_FootprintFacts, ...], str | None]:
    models: list[_FootprintFacts] = []
    current_names: list[str] = []
    for implementation in implementations:
        model_type = str(getattr(implementation, "model_type", "")).upper()
        if model_type != "PCBLIB":
            raise ValueError(
                f"unsupported model type for {component_name!r}: {model_type!r}"
            )
        model_name = str(getattr(implementation, "model_name", ""))
        footprint = footprints_by_fold.get(model_name.casefold())
        if footprint is None:
            raise ValueError(
                f"unresolved footprint for {component_name!r}: {model_name!r}"
            )
        models.append(footprint)
        if bool(getattr(implementation, "is_current", False)):
            current_names.append(footprint.name)
    _require_unique_names(
        (model.name for model in models), f"implementation on {component_name!r}"
    )
    if models and len(current_names) != 1:
        raise ValueError(
            f"component {component_name!r} must have exactly one current footprint"
        )
    ordered = tuple(sorted(models, key=lambda item: intlib_name_sort_key(item.name)))
    return ordered, current_names[0] if current_names else None


def _component_kind_name(symbol: AltiumSymbol) -> str:
    from .altium_common_enums import COMPONENT_KIND_NAMES, ComponentKind

    component = symbol.component_record
    kind = getattr(component, "component_kind", ComponentKind.STANDARD)
    try:
        normalized = ComponentKind(kind)
    except ValueError as exc:
        raise ValueError(
            f"unsupported component kind for {symbol.name!r}: {kind}"
        ) from exc
    return COMPONENT_KIND_NAMES[normalized]


def _component_pin_count(symbol: AltiumSymbol) -> int:
    component = symbol.component_record
    recorded = getattr(component, "all_pin_count", 0)
    return (
        int(recorded)
        if isinstance(recorded, int) and recorded > 0
        else len(symbol.pins)
    )


def _component_parameter_pairs(
    symbol: AltiumSymbol,
) -> tuple[tuple[str, str], ...]:
    from .altium_record_sch__designator import AltiumSchDesignator

    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for parameter in symbol.parameters:
        if isinstance(parameter, AltiumSchDesignator):
            continue
        name = str(parameter.name)
        value = str(parameter.text)
        folded = name.casefold()
        if folded == "comment" and value == "=Value":
            continue
        if folded == "description" and value == str(symbol.description):
            continue
        if folded in _RESERVED_COMPONENT_FIELDS:
            raise ValueError(f"reserved source parameter on {symbol.name!r}: {name!r}")
        if folded in seen:
            raise ValueError(f"duplicate source parameter on {symbol.name!r}: {name!r}")
        seen.add(folded)
        _validate_parameter_name(name)
        _validate_parameter_value(value)
        pairs.append((name, value))
    return tuple(sorted(pairs, key=lambda item: item[0].casefold()))


def _require_unique_names(names: Iterable[str], label: str) -> None:
    seen: set[str] = set()
    for name in names:
        folded = name.casefold()
        if not name:
            raise ValueError(f"empty {label} name")
        if folded in seen:
            raise ValueError(f"duplicate or case-colliding {label}: {name!r}")
        seen.add(folded)


def _normalize_height(value: object) -> str:
    text = str(value).strip()
    return text[:-3] if text.casefold().endswith("mil") else text


def _build_intlib_writer(
    sources: Sequence[_SourceSnapshot],
    facts: _PreparedFacts,
) -> AltiumOleWriter:
    writer = AltiumOleWriter(clsid=_ZERO_CLSID)
    writer.add_stream("Version.Txt", _VERSION_DATA)
    writer.add_stream(
        "LibCrossRef.Txt",
        _compress_intlib_stream(_build_crossref(facts)),
    )
    writer.add_stream(
        "Parameters   .bin",
        _compress_intlib_stream(_build_parameters(facts)),
    )
    for source in sources:
        writer.add_stream(source.stream_path, _compress_intlib_stream(source.data))
    return writer


def _build_crossref(facts: _PreparedFacts) -> bytes:
    chunks = [struct.pack("<I", len(facts.components))]
    for component in facts.components:
        chunks.extend(
            (
                _encode_altium_string(component.name),
                _encode_altium_string(component.source.virtual_path),
                struct.pack("<I", 1),
                _encode_altium_string(component.description),
                _encode_altium_string(component.source.logical_name),
                struct.pack("<I", len(component.models)),
            )
        )
        for model in component.models:
            chunks.extend(
                (
                    _encode_altium_string(model.name),
                    _encode_altium_string("PCBLIB"),
                    struct.pack("<I", 1),
                    _encode_altium_string(model.source.virtual_path),
                    _encode_altium_string(model.source.logical_name),
                )
            )
    return b"".join(chunks)


def _encode_altium_string(value: str) -> bytes:
    try:
        encoded = value.encode("cp1252")
    except UnicodeEncodeError as exc:
        raise ValueError(f"IntLib metadata is not CP1252 encodable: {value!r}") from exc
    if len(encoded) > 255:
        raise ValueError("IntLib metadata strings are limited to 255 encoded bytes")
    payload = bytes((len(encoded),)) + encoded
    return struct.pack("<I", len(payload)) + payload


def _build_parameters(facts: _PreparedFacts) -> bytes:
    records: list[bytes] = []
    linked: dict[str, _FootprintFacts] = {}
    for component in facts.components:
        records.append(_component_parameter_record(component))
        for footprint in component.models:
            records.append(_footprint_parameter_record(footprint))
            linked[footprint.name.casefold()] = footprint
    for footprint in sorted(
        linked.values(), key=lambda item: intlib_name_sort_key(item.name)
    ):
        records.append(_footprint_parameter_record(footprint))
    return b"".join(records)


def _component_parameter_record(component: _ComponentFacts) -> bytes:
    fields: list[tuple[str, str]] = [
        ("Comment", "=Value"),
        ("Component Kind", component.component_kind),
        ("Component Type", component.component_type),
    ]
    if component.description:
        fields.append(("Description", component.description))
    fields.append(("Designator", component.designator))
    if component.current_model is not None:
        fields.append(("Footprint", component.current_model))
    fields.extend(
        (
            ("Library Reference", component.name),
            ("Pin Count", str(component.pin_count)),
        )
    )
    fields.extend(component.parameters)
    return _parameter_record(fields)


def _footprint_parameter_record(footprint: _FootprintFacts) -> bytes:
    fields: list[tuple[str, str]] = []
    if footprint.description:
        fields.append(("Description", footprint.description))
    fields.extend(
        (
            ("Height", footprint.height),
            ("Pad Count", str(footprint.pad_count)),
        )
    )
    return _parameter_record(fields)


def _parameter_record(fields: Sequence[tuple[str, str]]) -> bytes:
    text = "|".join(
        f"{_validate_parameter_name(name)}={_validate_parameter_value(value)}"
        for name, value in fields
    )
    payload = text.encode("cp1252") + b"\x00"
    return struct.pack("<I", len(payload)) + payload


def _validate_parameter_name(name: str) -> str:
    if not name or any(char in name for char in "=|\x00"):
        raise ValueError(f"invalid IntLib parameter name: {name!r}")
    if any(ord(char) < 32 for char in name):
        raise ValueError(f"control character in IntLib parameter name: {name!r}")
    _require_cp1252(name, "parameter name")
    return name


def _validate_parameter_value(value: str) -> str:
    if "|" in value or "\x00" in value:
        raise ValueError(f"invalid IntLib parameter value: {value!r}")
    _require_cp1252(value, "parameter value")
    return value


def _require_cp1252(value: str, label: str) -> None:
    try:
        value.encode("cp1252")
    except UnicodeEncodeError as exc:
        raise ValueError(f"IntLib {label} is not CP1252 encodable: {value!r}") from exc


def _compress_intlib_stream(data: bytes) -> bytes:
    return bytes((_COMPRESSED_PREFIX,)) + zlib.compress(data)


def _expected_parameter_count(facts: _PreparedFacts) -> int:
    links = sum(len(component.models) for component in facts.components)
    unique = len(
        {
            model.name.casefold()
            for component in facts.components
            for model in component.models
        }
    )
    return len(facts.components) + links + unique


def _allocate_staged_path(destination: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    return Path(name)


def _sync_staged_file(path: Path) -> None:
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _validate_staged_intlib(
    path: Path,
    sources: Sequence[_SourceSnapshot],
    facts: _PreparedFacts,
) -> None:
    with AltiumIntLib(
        path,
        max_decompressed_stream_bytes=_staged_validation_stream_limit(
            sources,
            facts,
        ),
    ) as library:
        _validate_staged_streams(library, sources)
        _validate_staged_components(library, facts)
        records = _read_parameter_records(library.read_stream("Parameters   .bin"))
        if len(records) != _expected_parameter_count(facts):
            raise ValueError("staged IntLib parameter record count differs")


def _staged_validation_stream_limit(
    sources: Sequence[_SourceSnapshot],
    facts: _PreparedFacts,
) -> int:
    return max(
        1,
        len(_build_crossref(facts)),
        len(_build_parameters(facts)),
        *(len(source.data) for source in sources),
    )


def _validate_staged_streams(
    library: AltiumIntLib,
    sources: Sequence[_SourceSnapshot],
) -> None:
    expected_streams = _WRITER_METADATA_STREAMS | {
        source.stream_path for source in sources
    }
    if set(library.stream_paths) != expected_streams:
        raise ValueError("staged IntLib stream inventory differs")
    if library.read_stream("Version.Txt", decompress=False) != _VERSION_DATA:
        raise ValueError("staged IntLib Version.Txt differs")
    for stream_path in expected_streams - {"Version.Txt"}:
        if not library.read_stream(stream_path, decompress=False).startswith(
            b"\x02\x78"
        ):
            raise ValueError(f"staged IntLib stream is not zlib wrapped: {stream_path}")
    for source in sources:
        if _sha256(library.read_stream(source.stream_path)) != source.sha256:
            raise ValueError(
                f"staged IntLib {source.kind} snapshot differs: {source.logical_name}"
            )


def _validate_staged_components(
    library: AltiumIntLib,
    facts: _PreparedFacts,
) -> None:
    if library.component_parse_error is not None:
        raise ValueError(
            f"staged IntLib cross-reference failed: {library.component_parse_error}"
        )
    if len(library.components) != len(facts.components):
        raise ValueError("staged IntLib component count differs")
    for actual, expected in zip(library.components, facts.components, strict=True):
        if not _component_metadata_matches(actual, expected):
            raise ValueError(
                f"staged IntLib component metadata differs: {expected.name}"
            )


def _component_metadata_matches(
    actual: IntLibComponent,
    expected: _ComponentFacts,
) -> bool:
    actual_models = tuple(
        (model.name, model.virtual_path, model.source_path) for model in actual.models
    )
    expected_models = tuple(
        (model.name, model.source.virtual_path, model.source.logical_name)
        for model in expected.models
    )
    return (
        actual.name,
        actual.virtual_path,
        actual.source_path,
        actual.description,
        actual_models,
    ) == (
        expected.name,
        expected.source.virtual_path,
        expected.source.logical_name,
        expected.description,
        expected_models,
    )


def _read_parameter_records(data: bytes) -> tuple[str, ...]:
    records: list[str] = []
    cursor = _BinaryCursor(data)
    while cursor.position < len(data):
        size = cursor.read_u32()
        payload = cursor._read_bytes(size)
        if not payload.endswith(b"\x00"):
            raise ValueError("IntLib parameter record is not NUL terminated")
        records.append(payload[:-1].decode("cp1252"))
    return tuple(records)


def _publish_staged_intlib(
    staged_path: Path,
    destination: Path,
    *,
    overwrite: bool,
) -> None:
    if overwrite:
        os.replace(staged_path, destination)
        return
    try:
        os.link(staged_path, destination)
    except FileExistsError:
        raise FileExistsError(destination) from None
    staged_path.unlink()


def _build_result(
    output_path: Path,
    *,
    output_sha256: str,
    sources: Sequence[_SourceSnapshot],
    facts: _PreparedFacts,
) -> IntLibBuildResult:
    warnings = tuple(
        IntLibBuildWarning(
            code=IntLibBuildWarningCode.UNUSED_FOOTPRINT,
            subject=name,
            message="footprint is preserved in the PcbLib but is not linked by a component",
        )
        for name in facts.unused_footprints
    )
    return IntLibBuildResult(
        output_path=output_path,
        output_sha256=output_sha256,
        sources=tuple(
            IntLibBuildSource(
                kind=source.kind,
                logical_name=source.logical_name,
                stream_path=source.stream_path,
                sha256=source.sha256,
                entity_count=facts.entity_counts[source.stream_path],
            )
            for source in sources
        ),
        component_count=len(facts.components),
        footprint_count=len(facts.footprints),
        model_count=sum(len(item.models) for item in facts.components),
        parameter_record_count=_expected_parameter_count(facts),
        source_count=len(sources),
        warnings=warnings,
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_virtual_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    if normalized.startswith(":/"):
        normalized = normalized[2:]
    return normalized.strip("/")


def _is_source_stream_path(path: str) -> bool:
    if path in _METADATA_STREAMS:
        return False
    root, _, name = path.partition("/")
    return bool(name) and root in _SOURCE_STREAM_ROOTS


def _validate_max_decompressed_stream_bytes(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value >= sys.maxsize
    ):
        raise ValueError(
            "max_decompressed_stream_bytes must be a positive integer "
            f"less than {sys.maxsize}"
        )
    return value


def _decompress_intlib_stream(
    data: bytes,
    *,
    stream_path: str,
    max_decompressed_bytes: int,
) -> bytes:
    if stream_path == "Version.Txt":
        return data
    if data.startswith(bytes((_COMPRESSED_PREFIX,)) + _ZLIB_PREFIX):
        return _decompress_intlib_zlib(
            data[1:],
            stream_path=stream_path,
            max_decompressed_bytes=max_decompressed_bytes,
        )
    if data and data[0] == _COMPRESSED_PREFIX:
        if stream_path not in _ENVELOPED_METADATA_STREAMS:
            return data
        raise ValueError(f"IntLib stream {stream_path!r} has an invalid zlib payload")
    if data and data[0] == _RAW_PREFIX and stream_path in _ENVELOPED_METADATA_STREAMS:
        return data[1:]
    if stream_path in _ENVELOPED_METADATA_STREAMS:
        raise ValueError(f"IntLib stream {stream_path!r} has an unknown envelope")
    return data


def _decompress_intlib_zlib(
    compressed: bytes,
    *,
    stream_path: str,
    max_decompressed_bytes: int,
) -> bytes:
    if not compressed.startswith(_ZLIB_PREFIX):
        raise ValueError(f"IntLib stream {stream_path!r} has an invalid zlib payload")
    decompressor = zlib.decompressobj()
    try:
        decoded = decompressor.decompress(compressed, max_decompressed_bytes + 1)
    except zlib.error as exc:
        raise ValueError(
            f"IntLib stream {stream_path!r} has an invalid zlib payload"
        ) from exc
    if len(decoded) > max_decompressed_bytes or decompressor.unconsumed_tail:
        raise ValueError(
            f"IntLib stream {stream_path!r} exceeds decompressed limit "
            f"{max_decompressed_bytes} bytes"
        )
    if not decompressor.eof:
        raise ValueError(f"IntLib stream {stream_path!r} has a truncated zlib payload")
    if decompressor.unused_data:
        raise ValueError(f"IntLib stream {stream_path!r} has trailing zlib data")
    return decoded


def _decode_metadata_string(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _suggest_filename(stream_path: str, original_path: str | None) -> str:
    if original_path:
        basename = _portable_basename(original_path)
        if basename:
            return _safe_filename(basename)
    return _safe_filename(stream_path.rsplit("/", 1)[-1])


def _portable_basename(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _safe_filename(filename: str) -> str:
    return safe_artifact_basename(filename, fallback="source.bin")


def _choose_output_filename(source: IntLibSource, use_original_filenames: bool) -> str:
    if use_original_filenames:
        return source.suggested_filename
    return _safe_filename(source.stream_path.rsplit("/", 1)[-1])


def _dedupe_filename(filename: str, used: dict[str, int]) -> str:
    return dedupe_artifact_basename(filename, used)


__all__ = [
    "AltiumIntLib",
    "IntLibBuildResult",
    "IntLibBuildSource",
    "IntLibBuildWarning",
    "IntLibBuildWarningCode",
    "IntLibComponent",
    "IntLibExtractionResult",
    "IntLibModel",
    "IntLibSource",
]
