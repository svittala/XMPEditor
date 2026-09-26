"""
Shared helpers for reading and writing Adobe XMP sidecar files
(the .xmp files that sit next to raw photos like OV7A2905.CR3).

Used by xmpread.py (viewer) and xmpwrite.py (editor). Stdlib only —
runs on macOS and Windows with any Python 3.8+.
"""

import io
import os
import re
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape
import xml.etree.ElementTree as ET

RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

_XPACKET_RE = re.compile(r"<\?xpacket[^?]*\?>")


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def resolve_sidecars(path_str):
    """Given an image path, a .xmp path, or a directory, return a list of
    .xmp Paths to process. Returns [] if nothing could be resolved.

    The image itself need not exist on disk (e.g. a folder of .xmp sidecars
    with no local .jpg/.CR3 copies) — only the sidecar's presence matters."""
    p = Path(path_str)
    if p.is_dir():
        found = set(p.glob("*.xmp")) | set(p.glob("*.XMP"))
        return sorted(found, key=lambda f: f.name.lower())
    if p.suffix.lower() == ".xmp":
        return [p] if p.exists() else []
    for candidate in (p.with_suffix(".xmp"), p.with_suffix(".XMP")):
        if candidate.exists():
            return [candidate]
    return []


# ---------------------------------------------------------------------------
# Reading / parsing
# ---------------------------------------------------------------------------

def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def strip_xpacket(text):
    if text.startswith("﻿"):
        text = text[1:]
    return _XPACKET_RE.sub("", text).strip()


def _split_tag(tag):
    if tag.startswith("{"):
        uri, local = tag[1:].split("}", 1)
        return uri, local
    return None, tag


def parse_xmp(path):
    """Parse a .xmp sidecar into a plain dict:
      path, attrs (flat "prefix:local" -> value), title, description,
      creator (list), rights, subject (list), hierarchical_subject (list),
      iso (list), flash (dict or None)
    """
    text = strip_xpacket(read_text(path))

    ns_map = {}
    for _, (prefix, uri) in ET.iterparse(io.StringIO(text), events=("start-ns",)):
        if prefix:
            ns_map[uri] = prefix

    root = ET.fromstring(text)

    def qname(uri, local):
        prefix = ns_map.get(uri)
        return f"{prefix}:{local}" if prefix else local

    data = {
        "path": str(path),
        "attrs": {},
        "title": None,
        "description": None,
        "creator": [],
        "rights": None,
        "subject": [],
        "hierarchical_subject": [],
        "iso": [],
        "flash": None,
    }

    desc = root.find(f".//{{{RDF_NS}}}Description")
    if desc is None:
        return data

    for k, v in desc.attrib.items():
        uri, local = _split_tag(k)
        data["attrs"][qname(uri, local) if uri else local] = v

    def li_texts(elem):
        return [li.text or "" for li in elem.iter(f"{{{RDF_NS}}}li")]

    def alt_text(elem):
        best = None
        for li in elem.iter(f"{{{RDF_NS}}}li"):
            if best is None:
                best = li.text or ""
            if li.get(XML_LANG) == "x-default":
                return li.text or ""
        return best

    for child in list(desc):
        uri, local = _split_tag(child.tag)
        name = qname(uri, local) if uri else local
        if name == "dc:title":
            data["title"] = alt_text(child)
        elif name == "dc:description":
            data["description"] = alt_text(child)
        elif name == "dc:rights":
            data["rights"] = alt_text(child)
        elif name == "dc:creator":
            data["creator"] = li_texts(child)
        elif name == "dc:subject":
            data["subject"] = li_texts(child)
        elif name == "lr:hierarchicalSubject":
            data["hierarchical_subject"] = li_texts(child)
        elif name == "exif:ISOSpeedRatings":
            data["iso"] = li_texts(child)
        elif name == "exif:Flash":
            flash = {}
            for k, v in child.attrib.items():
                fu, fl = _split_tag(k)
                flash[qname(fu, fl) if fu else fl] = v
            data["flash"] = flash

    return data


# ---------------------------------------------------------------------------
# Value formatting (rationals -> human readable)
# ---------------------------------------------------------------------------

def parse_rational(s):
    if s is None:
        return None
    s = s.strip()
    try:
        if "/" in s:
            num, den = s.split("/", 1)
            num, den = float(num), float(den)
            return num / den if den else None
        return float(s)
    except ValueError:
        return None


def fmt_num(val, max_decimals=2):
    if val is None:
        return None
    r = round(val, max_decimals)
    if r == int(r):
        return str(int(r))
    return f"{r:.{max_decimals}f}".rstrip("0").rstrip(".")


def f_number(attrs):
    val = parse_rational(attrs.get("exif:FNumber"))
    return f"f/{fmt_num(val)}" if val is not None else None


def shutter(attrs):
    raw = attrs.get("exif:ExposureTime")
    if raw is None:
        return None
    if "/" in raw:
        try:
            num, den = (float(x) for x in raw.split("/", 1))
        except ValueError:
            return raw
        if den and num < den:
            return f"{fmt_num(num)}/{fmt_num(den)} s"
        return f"{fmt_num(num / den) if den else fmt_num(num)} s"
    return f"{raw} s"

def focal_length(attrs):
    val = parse_rational(attrs.get("exif:FocalLength"))
    return f"{fmt_num(val)} mm" if val is not None else None


def exposure_bias(attrs):
    val = parse_rational(attrs.get("exif:ExposureBiasValue"))
    if val is None:
        return None
    if val == 0:
        return "0 EV"
    sign = "+" if val > 0 else ""
    return f"{sign}{fmt_num(val)} EV"


def iso_value(data):
    if data.get("iso"):
        return ", ".join(data["iso"])
    return data["attrs"].get("exif:RecommendedExposureIndex")


_METERING = {
    "0": "Unknown", "1": "Average", "2": "Center-Weighted Average",
    "3": "Spot", "4": "Multi-Spot", "5": "Pattern (Evaluative)",
    "6": "Partial", "255": "Other",
}


def metering_mode(attrs):
    raw = attrs.get("exif:MeteringMode")
    return _METERING.get(raw, raw) if raw is not None else None


_EXIF_WB = {"0": "Auto", "1": "Manual"}


def white_balance_exif(attrs):
    raw = attrs.get("exif:WhiteBalance")
    return _EXIF_WB.get(raw, raw) if raw is not None else None


def stars(attrs):
    raw = attrs.get("xmp:Rating")
    try:
        n = max(0, min(5, int(float(raw))))
    except (TypeError, ValueError):
        return "— (not rated)"
    return f"{'★' * n}{'☆' * (5 - n)} ({n})"


def pick_label(attrs):
    raw = attrs.get("xmpDM:pick")
    if raw == "1":
        return "✓ Picked"
    if raw == "-1":
        return "✗ Rejected"
    return "Unflagged"


def flash_label(data):
    flash = data.get("flash")
    if not flash:
        return None
    fired = (flash.get("exif:Fired") or "").lower()
    if fired == "true":
        return "On"
    if fired == "false":
        return "Off"
    return "Unknown"


# ---------------------------------------------------------------------------
# Writing helpers (used by xmpwrite.py)
# ---------------------------------------------------------------------------

def esc_attr(s):
    return _xml_escape(s, {'"': "&quot;"})


def esc_text(s):
    return _xml_escape(s)


def find_open_tag_span(text, tag_start, start=0):
    """Return (start, end) of a <tag ...> open tag, end being just past the
    closing '>', scanning past '>' characters inside quoted attribute values.
    Searches from `start` onward."""
    m = re.search(re.escape(tag_start), text[start:])
    if not m:
        return None
    m_start = m.start() + start
    i = m.end() + start
    in_quote = None
    while i < len(text):
        c = text[i]
        if in_quote:
            if c == in_quote:
                in_quote = None
        elif c in ('"', "'"):
            in_quote = c
        elif c == ">":
            return m_start, i + 1
        i += 1
    return None


def find_root_description_span(text):
    """Return (open_start, open_end, close_start, close_end) for the
    top-level <rdf:Description>...</rdf:Description> under <rdf:RDF> — the
    one holding the image's own attributes and dc:/xmp:/crs: children.

    XMP files commonly contain many OTHER nested <rdf:Description> elements
    (e.g. inside crs:CorrectionMasks / crs:MapPolynomial develop-setting
    structs). The outer one is the first <rdf:Description ...> in the file
    and — because it must be the last to close of all of them — its closing
    tag is the last "</rdf:Description>" that appears before "</rdf:RDF>"."""
    rdf_open_idx = text.index("<rdf:RDF")
    rdf_close_idx = text.index("</rdf:RDF>", rdf_open_idx)

    span = find_open_tag_span(text, "<rdf:Description", start=rdf_open_idx)
    if span is None or span[0] > rdf_close_idx:
        raise ValueError("could not find top-level <rdf:Description> in file")
    open_start, open_end = span

    close_start = text.rindex("</rdf:Description>", open_end, rdf_close_idx)
    close_end = close_start + len("</rdf:Description>")
    return open_start, open_end, close_start, close_end


def write_text_atomic(path, text):
    """Write text to path via a temp file + atomic replace, preserving the
    file's LF-only line endings on every platform (no CRLF translation)."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)
