"""Tikal 1.48 XLIFF 1.2 (okf_idml) <-> segment model.

Only the shape Tikal 1.48 writes for okf_idml is accepted: <ph>, <bpt>, <ept>
inline codes with numeric ids, one <source>/<target> per trans-unit, no <seg-source>.
Anything else raises UnsupportedXliff (the input is refused, never repaired).

Model
-----
Segment.parts = list of str | Code
Code(kind="ph"|"bpt"|"ept", id="1", raw="<content-1/>")

LLM view
--------
ph            -> <x1/>
bpt id=n ...  -> <g1> ... </g1>
Tags are short and unambiguous so the validator can compare them exactly.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from lxml import etree

NS = "urn:oasis:names:tc:xliff:document:1.2"
X = "{%s}" % NS
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
ALLOWED_INLINE = {"ph", "bpt", "ept"}


class UnsupportedXliff(ValueError):
    pass


class CodeError(ValueError):
    """Model output whose inline codes do not match the source segment."""


@dataclass(frozen=True)
class Code:
    kind: str  # ph | bpt | ept
    id: str
    raw: str  # text content of the element (Tikal's opaque reference)


@dataclass
class Segment:
    file: str  # Stories/Story_uXXX.xml
    id: str  # trans-unit id
    source: list = field(default_factory=list)
    target: list | None = None

    def source_text(self) -> str:
        return "".join(p for p in self.source if isinstance(p, str))

    def code_ids(self):
        return [(c.kind, c.id) for c in self.source if isinstance(c, Code)]


def _parse_inline(el) -> list:
    parts: list = []
    if el.text:
        parts.append(el.text)
    for child in el:
        tag = etree.QName(child).localname
        if tag not in ALLOWED_INLINE:
            raise UnsupportedXliff(f"inline element <{tag}> is not supported")
        if len(child):
            raise UnsupportedXliff(f"nested markup inside <{tag}>")
        cid = child.get("id")
        if not cid or not cid.isdigit():
            raise UnsupportedXliff(f"<{tag}> without numeric id")
        parts.append(Code(tag, cid, child.text or ""))
        if child.tail:
            parts.append(child.tail)
    return _merge_text(parts)


def _merge_text(parts):
    out = []
    for p in parts:
        if isinstance(p, str):
            if not p:
                continue
            if out and isinstance(out[-1], str):
                out[-1] += p
                continue
        out.append(p)
    return out


def load(path) -> tuple[etree._ElementTree, list[Segment]]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    tree = etree.parse(str(path), parser)
    root = tree.getroot()
    if root.tag != X + "xliff" or root.get("version") != "1.2":
        raise UnsupportedXliff("not XLIFF 1.2")
    segs = []
    for f in root.iter(X + "file"):
        if f.get("source-language") != "en" or f.get("target-language") != "ja":
            raise UnsupportedXliff("only en -> ja is supported")
        for tu in f.iter(X + "trans-unit"):
            if tu.find(X + "seg-source") is not None:
                raise UnsupportedXliff("segmented XLIFF (seg-source) is not supported")
            src = tu.find(X + "source")
            if src is None:
                raise UnsupportedXliff("trans-unit without source")
            seg = Segment(file=f.get("original"), id=tu.get("id"), source=_parse_inline(src))
            tgt = tu.find(X + "target")
            if tgt is not None:
                seg.target = _parse_inline(tgt)
            segs.append(seg)
    ids = [s.id for s in segs]
    if len(ids) != len(set(ids)):
        raise UnsupportedXliff("duplicate trans-unit id")
    return tree, segs


def write_targets(tree, targets: dict[str, list], out_path):
    """targets: trans-unit id -> parts. Every trans-unit must be given."""
    tree = copy.deepcopy(tree)
    root = tree.getroot()
    seen = set()
    for tu in root.iter(X + "trans-unit"):
        tid = tu.get("id")
        if tid not in targets:
            raise ValueError(f"no target for {tid}")
        seen.add(tid)
        tgt = tu.find(X + "target")
        if tgt is None:
            tgt = etree.SubElement(tu, X + "target")
        for c in list(tgt):
            tgt.remove(c)
        tgt.text = None
        tgt.set(XML_LANG, "ja")
        last = None
        for p in targets[tid]:
            if isinstance(p, str):
                if last is None:
                    tgt.text = (tgt.text or "") + p
                else:
                    last.tail = (last.tail or "") + p
            else:
                last = etree.SubElement(tgt, X + p.kind, id=p.id)
                last.text = p.raw
    extra = set(targets) - seen
    if extra:
        raise ValueError(f"targets for unknown trans-units: {sorted(extra)}")
    tree.write(str(out_path), xml_declaration=True, encoding="UTF-8")


# ---------------------------------------------------------------- LLM view

LB = " "  # forced line break, shown to the model as <lb/>
SHY = "­"  # discretionary hyphen, not shown to the model (spike 3)


def to_tagged(parts) -> str:
    out = []
    for p in parts:
        if isinstance(p, str):
            t = p.replace(SHY, "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            out.append(t.replace(LB, "<lb/>"))
        elif p.kind == "ph":
            out.append(f"<x{p.id}/>")
        elif p.kind == "bpt":
            out.append(f"<g{p.id}>")
        else:
            out.append(f"</g{p.id}>")
    return "".join(out)


TAG_RE = re.compile(r"<(/?)([a-zA-Z]+)(\d*)(/?)>")


def from_tagged(text: str, source_parts) -> list:
    """Parse model output back into parts, validating codes against the source.

    Raises CodeError on: unknown tag, unknown id, missing code, duplicated code,
    kind mismatch (x vs g), unbalanced or crossing pairs, stray '<' / '>'.
    """
    by_id = {}
    for p in source_parts:
        if isinstance(p, Code):
            by_id.setdefault(p.id, {})[p.kind] = p
    parts: list = []
    pos = 0
    stack: list[str] = []
    seen: list[tuple[str, str]] = []
    for m in TAG_RE.finditer(text):
        parts.append(text[pos:m.start()])
        pos = m.end()
        close, name, num, selfclose = m.groups()
        if name == "lb" and not num and not close and selfclose:
            parts.append(LB)
            continue
        if name not in ("x", "g") or not num:
            raise CodeError(f"unknown tag {m.group(0)!r}")
        if num not in by_id:
            raise CodeError(f"unknown code id {num}")
        if name == "x":
            if close or not selfclose:
                raise CodeError(f"malformed placeholder {m.group(0)!r}")
            if "ph" not in by_id[num]:
                raise CodeError(f"id {num} is a pair, not a placeholder")
            parts.append(by_id[num]["ph"])
            seen.append(("ph", num))
        else:
            if selfclose:
                raise CodeError(f"malformed pair tag {m.group(0)!r}")
            if "bpt" not in by_id[num]:
                raise CodeError(f"id {num} is a placeholder, not a pair")
            if not close:
                stack.append(num)
                parts.append(by_id[num]["bpt"])
                seen.append(("bpt", num))
            else:
                if not stack or stack[-1] != num:
                    raise CodeError(f"pair {num} closed out of order (crossing or unopened)")
                stack.pop()
                parts.append(by_id[num]["ept"])
                seen.append(("ept", num))
    parts.append(text[pos:])
    if stack:
        raise CodeError(f"pair(s) not closed: {stack}")
    text_only = "".join(p for p in parts if isinstance(p, str))
    if "<" in text_only or ">" in text_only:
        raise CodeError("stray '<' or '>' outside a known tag")
    expected = sorted((p.kind, p.id) for p in source_parts if isinstance(p, Code))
    got = sorted(seen)
    if got != expected:
        missing = sorted(set(expected) - set(got))
        dup = sorted({x for x in got if got.count(x) > 1})
        raise CodeError(f"code set differs: missing={missing} duplicated={dup}")
    unescaped = [
        p.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&") if isinstance(p, str) else p
        for p in parts
    ]
    return _merge_text(unescaped)
