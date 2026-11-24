"""Transcript export: plain text, one sentence per line."""
from __future__ import annotations


def to_txt(items: list[dict]) -> str:
    return "\n".join(i["text"] for i in items) + ("\n" if items else "")

