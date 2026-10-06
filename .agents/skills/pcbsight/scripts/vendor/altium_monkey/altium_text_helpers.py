"""Shared text-normalization helpers."""

from __future__ import annotations


def normalized_object_text(value: object) -> str:
    return str(value or "").strip()
