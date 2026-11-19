"""Transcript export: plain text, WebVTT, SRT, JSON."""
from __future__ import annotations


def _stamp(t: float, sep: str) -> str:
    t = max(0.0, t)
    h, rem = divmod(int(t), 3600)
    m, s = divmod(rem, 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        s, ms = s + 1, 0
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_txt(items: list[dict]) -> str:
    return "\n".join(i["text"] for i in items) + ("\n" if items else "")


def to_vtt(items: list[dict]) -> str:
    out = ["WEBVTT", ""]
    for i in items:
        out += [f"{_stamp(i['start'], '.')} --> {_stamp(max(i['end'], i['start'] + 0.5), '.')}", i["text"], ""]
    return "\n".join(out)


def to_srt(items: list[dict]) -> str:
    out = []
    for n, i in enumerate(items, 1):
        out += [str(n), f"{_stamp(i['start'], ',')} --> {_stamp(max(i['end'], i['start'] + 0.5), ',')}", i["text"], ""]
    return "\n".join(out)
