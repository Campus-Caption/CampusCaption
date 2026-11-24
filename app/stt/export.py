"""Transcript export: plain text, one sentence per line."""
from __future__ import annotations


def to_txt(items: list[dict]) -> str:
    """One sentence per line; with two microphones each line starts with the speaker ("Audience: …")."""
    lines = [f"{i['label']}: {i['text']}" if i.get("label") else i["text"] for i in items]
    return "\n".join(lines) + ("\n" if lines else "")

