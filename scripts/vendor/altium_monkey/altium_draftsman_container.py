"""Container helpers for Draftsman document payloads."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from lz4.block import decompress

DraftsmanSourceCompression = Literal["raw", "lz4"]
DraftsmanWriteCompression = Literal["preserve", "raw", "lz4"]

_RAW_XML_PREFIX = b"<Doc"
_COMPRESSED_FLAG = 1
_VARINT_CONTINUATION_BIT = 0x80
_VARINT_VALUE_MASK = 0x7F
_MAX_VARINT_BYTES = 10


class DraftsmanContainerError(ValueError):
    """Raised when a Draftsman container payload cannot be decoded."""


@dataclass(frozen=True)
class DraftsmanContainerPayload:
    """Decoded Draftsman XML bytes plus the source container encoding."""

    xml_bytes: bytes
    source_compression: DraftsmanSourceCompression


def is_raw_draftsman_xml(data: bytes) -> bool:
    """Return true when a Draftsman payload starts with raw XML."""

    return data.startswith(_RAW_XML_PREFIX)


def decode_draftsman_payload(data: bytes) -> DraftsmanContainerPayload:
    """Decode raw XML or K4os LZ4 Draftsman bytes into XML bytes."""

    if is_raw_draftsman_xml(data):
        return DraftsmanContainerPayload(data, "raw")
    return DraftsmanContainerPayload(_decode_lz4_payload(data), "lz4")


def read_draftsman_payload(path: str | Path) -> DraftsmanContainerPayload:
    """Read and decode a Draftsman file from disk."""

    return decode_draftsman_payload(Path(path).read_bytes())


def encode_draftsman_payload(
    xml_bytes: bytes,
    compression: DraftsmanWriteCompression = "raw",
    source_compression: DraftsmanSourceCompression | None = None,
) -> bytes:
    """Encode Draftsman XML bytes for writing to disk."""

    resolved_compression = _resolve_write_compression(compression, source_compression)
    if resolved_compression == "lz4":
        raise NotImplementedError("Draftsman LZ4 write support is not implemented yet")
    if not is_raw_draftsman_xml(xml_bytes):
        raise DraftsmanContainerError("Draftsman XML must start with <Doc")
    return xml_bytes


def write_draftsman_payload(
    path: str | Path,
    xml_bytes: bytes,
    compression: DraftsmanWriteCompression = "raw",
    source_compression: DraftsmanSourceCompression | None = None,
) -> None:
    """Write Draftsman XML bytes to disk using the requested container mode."""

    encoded = encode_draftsman_payload(xml_bytes, compression, source_compression)
    Path(path).write_bytes(encoded)


def _decode_lz4_payload(data: bytes) -> bytes:
    offset = 0
    chunks: list[bytes] = []

    while offset < len(data):
        flags, offset = _read_varint(data, offset)
        uncompressed_size, offset = _read_varint(data, offset)

        if flags & _COMPRESSED_FLAG:
            compressed_size, offset = _read_varint(data, offset)
            payload_end = offset + compressed_size
            _require_available(data, offset, compressed_size)
            payload = data[offset:payload_end]
            offset = payload_end
            chunks.append(decompress(payload, uncompressed_size=uncompressed_size))
            continue

        payload_end = offset + uncompressed_size
        _require_available(data, offset, uncompressed_size)
        chunks.append(data[offset:payload_end])
        offset = payload_end

    xml_bytes = b"".join(chunks)
    if not is_raw_draftsman_xml(xml_bytes):
        raise DraftsmanContainerError("Decoded Draftsman payload is not XML")
    return xml_bytes


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    shift = 0

    for _index in range(_MAX_VARINT_BYTES):
        _require_available(data, offset, 1)
        byte_value = data[offset]
        offset += 1
        result |= (byte_value & _VARINT_VALUE_MASK) << shift
        if not byte_value & _VARINT_CONTINUATION_BIT:
            return result, offset
        shift += 7

    raise DraftsmanContainerError("Draftsman varint is too long")


def _require_available(data: bytes, offset: int, size: int) -> None:
    if size < 0:
        raise DraftsmanContainerError("Draftsman payload size is negative")
    if offset < 0 or offset + size > len(data):
        raise DraftsmanContainerError("Draftsman payload ends unexpectedly")


def _resolve_write_compression(
    compression: DraftsmanWriteCompression,
    source_compression: DraftsmanSourceCompression | None,
) -> DraftsmanSourceCompression:
    if compression == "raw":
        return "raw"
    if compression == "lz4":
        return "lz4"
    if source_compression == "raw":
        return "raw"
    return "raw"
