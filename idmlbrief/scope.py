"""Input gate for IDML (spike 2).

Every input is classified before anything is sent anywhere:

  translated  - text Tikal extracts with the fixed config (body, master pages,
                footnotes, tables, anchored / grouped frames, pasteboard)
  excluded    - not translated, verified to come back unchanged
                (hidden layers, index topics, cross-reference format text)
  unsupported - not verified to survive the round trip -> the input is refused

The story allowlist is exactly the set of elements present in the spike
fixture whose round trip was checked in InDesign (spike 1/2). Anything else
refuses the file; nothing is dropped silently.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

MAX_ENTRIES = 3000
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_RATIO = 200  # uncompressed / compressed per entry

STORY_ALLOW = {
    "Story", "StoryPreference", "InCopyExportOption", "ParagraphStyleRange", "CharacterStyleRange",
    "Content", "Br", "Properties", "Footnote", "Table", "Row", "Column", "Cell", "TextFrame",
    "TextFramePreference", "AnchoredObjectSetting", "TextWrapPreference", "TextWrapOffset",
    "ObjectExportOption", "AltMetadataProperty", "ActualMetadataProperty", "PathGeometry",
    "GeometryPathType", "PathPointArray", "PathPointType", "InsetSpacing", "CrossReferenceSource",
    "TextVariableInstance", "PageReference", "ParagraphDestination", "ListItem",
    # property children seen inside <Properties> of styles ranges
    "Leading", "AppliedFont", "BasedOn", "PreviewColor", "Label", "KeyValuePair",
}

REFUSE_REASONS = {
    "Note": "InDesign notes",
    "Change": "tracked changes",
    "XMLElement": "XML-tagged content",
    "HyperlinkTextSource": "hyperlinks (not verified yet)",
    "Rectangle": "graphics inside text (not verified yet)",
    "Group": "groups inside text (not verified yet)",
    "EPSText": "EPS text",
    "MathZone": "math zones",
}


@dataclass
class ScopeReport:
    ok: bool = True
    translated_stories: list = field(default_factory=list)
    excluded: list = field(default_factory=list)  # (what, detail)
    unsupported: list = field(default_factory=list)  # (what, detail)
    warnings: list = field(default_factory=list)

    def refuse(self, what, detail):
        self.ok = False
        self.unsupported.append((what, detail))


def _parse(data: bytes, name: str, rep: ScopeReport):
    head = data[:4096]
    if b"<!DOCTYPE" in head or b"<!ENTITY" in data:
        rep.refuse("DOCTYPE / entity declaration", name)
        return None
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    try:
        return etree.fromstring(data, parser)
    except etree.XMLSyntaxError as e:
        rep.refuse("broken XML", f"{name}: {e}")
        return None


def _local(e):
    return etree.QName(e).localname if isinstance(e.tag, str) else None


def scan(path: Path) -> ScopeReport:
    rep = ScopeReport()
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        rep.refuse("not an IDML (zip) file", Path(path).name)
        return rep
    with z:
        infos = z.infolist()
        if len(infos) > MAX_ENTRIES:
            rep.refuse("too many entries", str(len(infos)))
            return rep
        total = 0
        for i in infos:
            n = i.filename
            if n.startswith(("/", "\\")) or ".." in n.split("/") or ":" in n:
                rep.refuse("unsafe path in package", n)
            total += i.file_size
            if i.compress_size and i.file_size / max(i.compress_size, 1) > MAX_RATIO and i.file_size > 1_000_000:
                rep.refuse("suspicious compression ratio", n)
        if total > MAX_TOTAL_BYTES:
            rep.refuse("package too large when unpacked", f"{total} bytes")
        if not rep.ok:
            return rep
        names = z.namelist()
        if not names or names[0] != "mimetype" or z.read("mimetype").strip() != b"application/vnd.adobe.indesign-idml-package":
            rep.refuse("not an IDML package", "mimetype")
            return rep
        if "designmap.xml" not in names:
            rep.refuse("not an IDML package", "designmap.xml missing")
            return rep

        dm = _parse(z.read("designmap.xml"), "designmap.xml", rep)
        if dm is None:
            return rep
        xref_formats = {}
        used_formats = set()
        hidden_layers = {e.get("Self"): e.get("Name") for e in dm.iter() if _local(e) == "Layer" and e.get("Visible") == "false"}
        for e in dm.iter():
            t = _local(e)
            if t == "Condition":
                rep.refuse("conditional text", e.get("Name", ""))
            if t == "Topic":
                rep.excluded.append(("index topic (stays in the source language)", e.get("Name", "")))
            if t == "CrossReferenceFormat":
                lit = [b.get("CustomText", "") for b in e.iter() if _local(b) == "BuildingBlock"
                       and not (b.get("CustomText", "") or "").startswith("$ID/")
                       and re.search(r"[A-Za-z]", b.get("CustomText", "") or "")]
                if lit:
                    xref_formats[e.get("Self")] = f"{e.get('Name')}: {' | '.join(x.strip() for x in lit)}"

        # stories on hidden layers
        hidden_stories = set()
        for n in names:
            if n.startswith(("Spreads/", "MasterSpreads/")):
                root = _parse(z.read(n), n, rep)
                if root is None:
                    return rep
                for tf in root.iter():
                    if _local(tf) == "TextFrame" and tf.get("ItemLayer") in hidden_layers:
                        hidden_stories.add(tf.get("ParentStory"))
        for s in sorted(hidden_stories):
            rep.excluded.append(("text on a hidden layer (left unchanged)", f"story {s}"))

        for n in names:
            if not n.startswith("Stories/"):
                continue
            root = _parse(z.read(n), n, rep)
            if root is None:
                return rep
            story = next((e for e in root if _local(e) == "Story"), None)
            if story is None:
                rep.refuse("story without <Story>", n)
                continue
            if story.get("IsEndnoteStory") == "true":
                rep.refuse("endnotes", n)
            if story.get("TrackChanges") == "true":
                rep.refuse("track changes is on", n)
            for e in root.iter():
                t = _local(e)
                if t is None or e is root:
                    continue
                if (e.get("AppliedConditions") or "").strip():
                    rep.refuse("conditional text", n)
                if e.getparent() is not None and _local(e.getparent()) == "Properties":
                    continue
                if t == "TextVariableInstance":
                    par = e.getparent()
                    inside_xref = any(_local(a) == "CrossReferenceSource" for a in e.iterancestors())
                    if not inside_xref:
                        rep.refuse("text variable outside a cross-reference", f"{n}: {e.get('Name')}")
                    continue
                if t not in STORY_ALLOW:
                    rep.refuse(REFUSE_REASONS.get(t, f"<{t}> (not verified)"), n)
            for e in root.iter():
                if _local(e) == "CrossReferenceSource":
                    used_formats.add(e.get("AppliedFormat"))
            sid = story.get("Self")
            if sid not in hidden_stories:
                rep.translated_stories.append(sid)
    for f in sorted(used_formats):
        if f in xref_formats:
            rep.excluded.append(("cross-reference format text stays as is (edit the format in InDesign)", xref_formats[f]))
    # unique, ordered
    rep.unsupported = list(dict.fromkeys(rep.unsupported))
    return rep
