"""What each inline code stands for, found by a probe merge (no guessing from
Tikal's opaque <content-N> references).

Every text run of every segment is replaced by a unique ASCII token; the
XLIFF is merged with Tikal; the merged stories are read back and, for each
token, we record the innermost CharacterStyleRange attributes and the
non-CSR ancestors (e.g. CrossReferenceSource, Footnote, Cell).

For a pair <gN>: decoration = attributes of the runs inside it that differ
from the runs right outside it. For a placeholder <xN>: the IDML element that
appears between the token before and the token after it.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from . import tikal, xliff
from .idmlfix import fix_package
from .xliff import Code

TOKEN = "ZQ{:05d}Z"
TOKEN_RE = re.compile(r"ZQ(\d{5})Z")
IGNORE_ATTRS = {"AppliedCharacterStyle"}  # reported separately


@dataclass
class PairInfo:
    char_style: str | None
    overrides: dict = field(default_factory=dict)

    def label(self):
        bits = []
        if self.char_style:
            bits.append("style:" + self.char_style.replace("CharacterStyle/", ""))
        bits += [f"{k}={v}" for k, v in sorted(self.overrides.items())]
        return ", ".join(bits) or "(no visible change)"


@dataclass
class SegmentCodes:
    pairs: dict[str, PairInfo] = field(default_factory=dict)
    placeholders: dict[str, str] = field(default_factory=dict)  # id -> element kind


def _story_leaves(root):
    """Yield (kind, payload, csr_attrs, chain) in document order."""
    def rec(e, csr, chain):
        tag = etree.QName(e).localname if isinstance(e.tag, str) else None
        if tag is None:  # processing instruction / comment
            yield ("pi", e.text if hasattr(e, "text") else "", csr, chain)
            if e.tail:
                yield ("text", e.tail, csr, chain)
            return
        if tag == "CharacterStyleRange":
            csr = dict(e.attrib)
        if tag == "Content":
            if e.text:
                yield ("text", e.text, csr, chain)
            for c in e:
                yield from rec(c, csr, chain)
            return
        kids = list(e)
        if tag not in ("CharacterStyleRange", "ParagraphStyleRange", "Story", "Properties") and tag != "Story":
            yield ("elem", tag, csr, chain)
        if tag == "Properties":
            return
        for c in kids:
            yield from rec(c, csr, chain + (tag,))
            if c.tail and c.tail.strip() and etree.QName(c).localname == "Content":
                pass

    yield from rec(root, None, ())


META = {"StoryPreference", "InCopyExportOption", "TextFramePreference", "AnchoredObjectSetting",
        "TextWrapPreference", "ObjectExportOption", "Column", "Row", "TextVariableInstance",
        "ContourOption", "FrameFittingOption", "Br"}


def _ph_kind(s, i, segs, tok_of, flat, pos_of_tok):
    """Element right after the token that precedes the placeholder."""
    order = [(sg.id, j) for sg in segs if sg.file == s.file for j in range(len(sg.source))]
    k = order.index((s.id, i))
    prev_t = next((tok_of[o] for o in reversed(order[:k]) if o in tok_of), None)
    start = pos_of_tok[prev_t] + 1 if prev_t is not None else 0
    for x in flat[start:]:
        if x[0] == "elem":
            return x[1]
        if x[0] == "tok":
            break
    return "unknown"


def probe(idml: Path, work: Path) -> dict[str, SegmentCodes]:
    work = Path(work)
    xlf = tikal.extract(idml, work)
    tree, segs = xliff.load(xlf)
    targets = {}
    n = 0
    where = {}  # token -> (seg id, part index)
    for s in segs:
        parts = []
        for i, p in enumerate(s.source):
            if isinstance(p, str):
                tok = TOKEN.format(n)
                where[n] = (s.id, i)
                n += 1
                parts.append(tok)
            else:
                parts.append(p)
        targets[s.id] = parts
    probe_xlf = work / "probe.xlf"
    xliff.write_targets(tree, targets, probe_xlf)
    out = tikal.merge(probe_xlf, work)
    fixed = work / "probe.fixed.idml"
    fix_package(out, fixed)

    # token -> csr attrs ; and stream of tokens/elements per story
    tok_attrs: dict[int, dict] = {}
    streams = []
    with zipfile.ZipFile(fixed) as z:
        for name in z.namelist():
            if not name.startswith("Stories/"):
                continue
            root = etree.fromstring(z.read(name))
            stream = []
            for kind, payload, csr, chain in _story_leaves(root):
                if kind == "text":
                    for m in TOKEN_RE.finditer(payload):
                        t = int(m.group(1))
                        tok_attrs[t] = dict(csr or {})
                        stream.append(("tok", t))
                elif kind == "elem":
                    stream.append(("elem", payload))
                elif kind == "pi":
                    stream.append(("elem", "ProcessingInstruction"))
            streams.append(stream)

    flat = [x for st in streams for x in st if not (x[0] == "elem" and x[1] in META)]
    pos_of_tok = {x[1]: k for k, x in enumerate(flat) if x[0] == "tok"}
    result: dict[str, SegmentCodes] = {}
    seg_by_id = {s.id: s for s in segs}
    tok_of = {(sid, i): t for t, (sid, i) in where.items()}
    for s in segs:
        sc = SegmentCodes()
        # pairs
        depth_stack = []
        inside: dict[str, list[int]] = {}
        outside: list[int] = []
        for i, p in enumerate(s.source):
            if isinstance(p, Code) and p.kind == "bpt":
                depth_stack.append(p.id)
            elif isinstance(p, Code) and p.kind == "ept":
                depth_stack.pop()
            elif isinstance(p, str):
                t = tok_of[(s.id, i)]
                if depth_stack:
                    inside.setdefault(depth_stack[-1], []).append(t)
                else:
                    outside.append(t)
        base = tok_attrs.get(outside[0], {}) if outside else {}
        for cid, toks in inside.items():
            a = tok_attrs.get(toks[0], {})
            style = a.get("AppliedCharacterStyle")
            base_style = base.get("AppliedCharacterStyle")
            ov = {k: v for k, v in a.items() if k not in IGNORE_ATTRS and base.get(k) != v}
            sc.pairs[cid] = PairInfo(style if style != base_style and "[No character style]" not in (style or "") else None, ov)
        # placeholders: first inline element between the neighbouring tokens
        for i, p in enumerate(s.source):
            if isinstance(p, Code) and p.kind == "ph":
                sc.placeholders[p.id] = _ph_kind(s, i, segs, tok_of, flat, pos_of_tok)
        result[s.id] = sc
    return result
