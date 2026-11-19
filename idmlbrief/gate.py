"""Stop gate before a working IDML is handed out (F7).

Order-insensitive structure check between the input IDML and the merged
output. It ignores order on purpose: legitimate reordering of styled words
would trip an order-sensitive comparison (spike 4).

Checks:
  1. same zip entries; mimetype first
  2. non-story XML parts: canonically equal (Properties children as a multiset,
     xml:space ignored)
  3. per story: the multiset of non-text leaf elements (with their ancestor
     chain and attributes) is unchanged  -> footnotes, xrefs, index markers,
     anchors, tables, page numbers survived
  4. per story: the set of character-run signatures (innermost CSR attributes)
     used by text is unchanged -> no style lost, none invented
  5. every translated paragraph's text appears in the output stories
"""
from __future__ import annotations

import zipfile
from collections import Counter
from xml.etree import ElementTree as ET

XS = "{http://www.w3.org/XML/1998/namespace}space"


def _attrs(e):
    return tuple(sorted((k, v) for k, v in e.attrib.items() if k != XS))


def _canon(e):
    kids = [_canon(k) for k in e]
    if e.tag == "Properties":
        kids = sorted(kids, key=repr)
    return (e.tag, _attrs(e), (e.text or "").strip() or None, tuple(kids))


def _parse(data: bytes):
    return ET.fromstring(data, parser=ET.XMLParser(target=ET.TreeBuilder(insert_pis=True, insert_comments=False)))


def _story_profile(root):
    """Count every element except the run wrappers (CharacterStyleRange, Content),
    which Tikal may split or merge. Processing instructions (page numbers,
    footnote markers) are counted too."""
    elems, sigs, text = Counter(), set(), []

    def rec(e, chain, csr):
        if e.tag is ET.ProcessingInstruction:
            elems[(chain, "PI", (e.text or "").strip())] += 1
            return
        if e.tag == "CharacterStyleRange":
            csr = _attrs(e)
            for k in e:
                rec(k, chain, csr)
            return
        if e.tag == "Content":
            if e.text:
                sigs.add(csr)
                text.append(e.text)
            for k in e:
                rec(k, chain, csr)
                if k.tail:
                    sigs.add(csr)
                    text.append(k.tail)
            return
        if e.tag == "Properties":
            elems[(chain, "Properties", repr(sorted(repr(_canon(k)) for k in e)))] += 1
            return
        elems[(chain, e.tag, _attrs(e))] += 1
        for k in e:
            rec(k, chain + (e.tag,), csr)

    rec(root, (), None)
    return elems, sigs, "".join(text)


def check(src_idml, out_idml, translated_texts=()) -> list[str]:
    problems = []
    za, zb = zipfile.ZipFile(src_idml), zipfile.ZipFile(out_idml)
    na, nb = za.namelist(), zb.namelist()
    if set(na) != set(nb):
        problems.append(f"Package parts differ: missing {sorted(set(na) - set(nb))}, added {sorted(set(nb) - set(na))}")
    if not nb or nb[0] != "mimetype":
        problems.append("The output package does not start with mimetype.")
    out_text = []
    for n in na:
        if n not in nb:
            continue
        a_raw, b_raw = za.read(n), zb.read(n)
        if not n.endswith(".xml"):
            if a_raw != b_raw:
                problems.append(f"{n}: changed")
            continue
        if n.startswith("Stories/"):
            ea, sa, _ = _story_profile(_parse(a_raw))
            eb, sb, tb = _story_profile(_parse(b_raw))
            out_text.append(tb)
            if ea != eb:
                lost = ea - eb
                added = eb - ea
                problems.append(f"{n}: objects in the text changed (lost {sum(lost.values())}: "
                                f"{sorted({k[1] for k in lost})}, added {sum(added.values())}: {sorted({k[1] for k in added})})")
            if sa != sb:
                problems.append(f"{n}: character formatting changed (lost {len(sa - sb)}, new {len(sb - sa)})")
        elif a_raw != b_raw and _canon(ET.fromstring(a_raw)) != _canon(ET.fromstring(b_raw)):
            problems.append(f"{n}: changed outside the stories")
    joined = "".join(out_text)
    missing = [t for t in translated_texts if t.strip() and t.replace(" ", "") not in joined.replace(" ", "")]
    if missing:
        problems.append(f"{len(missing)} translated paragraph(s) not found in the output, e.g. {missing[0][:60]!r}")
    return problems
