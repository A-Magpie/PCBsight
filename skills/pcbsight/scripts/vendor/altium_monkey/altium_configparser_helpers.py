"""Shared ConfigParser helpers."""

from __future__ import annotations


def preserve_option_case(optionstr: str) -> str:
    """ConfigParser callback that preserves original key casing."""
    return optionstr
