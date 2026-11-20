"""Japanese line-breaking warning (read only).

The app swaps text and leaves Styles.xml as it is, so English paragraph styles
keep their Latin composer and kinsoku set "None". This module reads the
effective composer and kinsoku set of every paragraph that will hold Japanese
text and lists the ones that are not set up for Japanese. Nothing is changed.

Effective value = paragraph override in the story > the style > its BasedOn
chain > [No paragraph style].
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from lxml import etree

ROOT_STYLE = "ParagraphStyle/$ID/[No paragraph style]"

COMPOSERS = {
    "HL Composer": "Adobe Paragraph Composer",
    "HL Single": "Adobe Single-line Composer",
    "HL Composer J": "Adobe Japanese Paragraph Composer",
    "HL Single J": "Adobe Japanese Single-line Composer",
    "HL Composer Optyca": "Adobe World-Ready Paragraph Composer",
    "HL Single Optyca": "Adobe World-Ready Single-line Composer",
}
KINSOKU = {"Nothing": "None", "HardKinsoku": "Hard", "SoftKinsoku": "Soft"}


def _local(e):
    return etree.QName(e).localname if isinstance(e.tag, str) else None


def _parser():
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)


def _props(e) -> dict:
    """Composer / KinsokuSet / BasedOn set directly on a style or a paragraph range."""
    out = {}
    if e.get("Composer"):
        out["composer"] = e.get("Composer")
    for p in e:
        if _local(p) != "Properties":
            continue
        for c in p:
            t = _local(c)
            if t == "KinsokuSet":
                out["kinsoku"] = (c.text or "").strip()
            elif t == "BasedOn":
                out["based_on"] = (c.text or "").strip()
    return out


def composer_name(v: str) -> str:
    v = (v or "").removeprefix("$ID/")
    return COMPOSERS.get(v, v or "unknown")


def kinsoku_name(v: str) -> str:
    if not v:
        return "unknown"
    if v.startswith("KinsokuTable/"):
        return v.split("/", 1)[1]
    return KINSOKU.get(v, v)


def is_japanese_composer(v: str) -> bool:
    return (v or "").removeprefix("$ID/").endswith(" J")


def _paragraphs(psr) -> int:
    """Non-empty paragraphs in this range. <Br/> ends a paragraph; text in nested
    ranges (table cells, footnotes, anchored frames) belongs to those ranges."""
    count, buf = 0, ""

    def walk(e):
        nonlocal count, buf
        for c in e:
            t = _local(c)
            if t == "ParagraphStyleRange" or t in ("Table", "Footnote", "TextFrame", "Properties"):
                continue
            if t == "Content":
                # text after a processing instruction (<?ACE 4?>) is in the PI's tail
                buf += (c.text or "") + "".join(ch.tail or "" for ch in c)
            elif t == "Br":
                if buf.strip():
                    count += 1
                buf = ""
            else:
                walk(c)

    walk(psr)
    if buf.strip():
        count += 1
    return count


def _style_name(self_id: str, styles: dict) -> str:
    s = styles.get(self_id)
    name = (s or {}).get("name") or self_id.split("/", 1)[-1]
    return name.removeprefix("$ID/")


def scan(idml: Path, story_ids) -> list[dict]:
    """Return one entry per (style, composer, kinsoku) that is not set up for Japanese.

    Each entry: style, composer, kinsoku (display names), problems (list), paragraphs (count).
    """
    want = set(story_ids or [])
    with zipfile.ZipFile(idml) as z:
        names = z.namelist()
        styles: dict[str, dict] = {}
        by_name: dict[str, str] = {}
        if "Resources/Styles.xml" in names:
            root = etree.fromstring(z.read("Resources/Styles.xml"), _parser())
            for e in root.iter():
                if _local(e) == "ParagraphStyle":
                    sid = e.get("Self")
                    styles[sid] = {"name": e.get("Name", ""), **_props(e)}
                    by_name.setdefault(e.get("Name", ""), sid)

        def resolve(sid: str, key: str, seen=None):
            seen = seen or set()
            if not sid or sid in seen:
                return None
            seen.add(sid)
            s = styles.get(sid)
            if s is None:
                return None
            if key in s and s[key]:
                return s[key]
            parent = s.get("based_on") or ""
            if parent:
                psid = parent if parent in styles else by_name.get(parent) or by_name.get(parent.removeprefix("$ID/"))
                v = resolve(psid, key, seen)
                if v is not None:
                    return v
            return None if sid == ROOT_STYLE else resolve(ROOT_STYLE, key, seen)

        groups: dict[tuple, int] = {}
        for n in names:
            if not n.startswith("Stories/"):
                continue
            root = etree.fromstring(z.read(n), _parser())
            story = next((e for e in root if _local(e) == "Story"), None)
            if story is None or (want and story.get("Self") not in want):
                continue
            for psr in story.iter():
                if _local(psr) != "ParagraphStyleRange":
                    continue
                n_par = _paragraphs(psr)
                if not n_par:
                    continue
                sid = psr.get("AppliedParagraphStyle") or ROOT_STYLE
                own = _props(psr)
                comp = own.get("composer") or resolve(sid, "composer") or ""
                kin = own.get("kinsoku") or resolve(sid, "kinsoku") or ""
                key = (_style_name(sid, styles), comp, kin)
                groups[key] = groups.get(key, 0) + n_par

    out = []
    for (style, comp, kin), count in groups.items():
        problems = []
        if kin == "Nothing":
            problems.append("kinsoku set is None")
        if comp and not is_japanese_composer(comp):
            problems.append("not a Japanese composer")
        if problems:
            out.append({"style": style, "composer": composer_name(comp), "kinsoku": kinsoku_name(kin),
                        "problems": problems, "paragraphs": count})
    out.sort(key=lambda d: (-d["paragraphs"], d["style"]))
    return out


def summary_line(entries: list[dict]) -> str:
    if not entries:
        return ""
    return (f"{len(entries)} paragraph style(s) that will hold Japanese text are not set up for Japanese line "
            "breaking. With the kinsoku set at None, nothing stops a 。 or 、 from starting a line. "
            "The app does not change styles. Set them in InDesign.")
