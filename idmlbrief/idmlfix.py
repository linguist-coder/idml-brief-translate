"""Spike fix: Tikal 1.48 moves <CrossReferenceSource> (and similar) inside the
preceding <CharacterStyleRange>. InDesign then carries the formatting of the
range before it (observed: Tracking="-40" leaked onto ". For cleaning steps, see ").

This lifts CrossReferenceSource / HyperlinkTextSource out of an enclosing
CharacterStyleRange: the enclosing range is split into
  CSR(children before) + lifted element + CSR(children after),
with both halves keeping the enclosing range's attributes.

Usage: python unnest_csr.py in.idml out.idml
"""
import copy
import sys
import zipfile

from lxml import etree

CSR = "CharacterStyleRange"


# Only text-source elements that InDesign writes as siblings of CSR.
# Footnote / Table / TextFrame legitimately live inside a CSR (lifting them
# broke the footnote reference in the spike).
LIFT = {"CrossReferenceSource", "HyperlinkTextSource"}


def needs_lift(child):
    return child.tag in LIFT


def fix_tree(root):
    changed = 0
    while True:
        target = None
        for csr in root.iter(CSR):
            for child in csr:
                if needs_lift(child):
                    target = (csr, child)
                    break
            if target:
                break
        if not target:
            return changed
        csr, child = target
        parent = csr.getparent()
        idx = parent.index(csr)
        kids = list(csr)
        k = kids.index(child)
        before, after = kids[:k], kids[k + 1:]
        new = []
        if before:
            a = etree.Element(CSR, dict(csr.attrib))
            a.extend(before)
            new.append(a)
        new.append(child)
        if after:
            b = etree.Element(CSR, dict(csr.attrib))
            b.extend(after)
            new.append(b)
        parent.remove(csr)
        for j, n in enumerate(new):
            parent.insert(idx + j, n)
        changed += 1


def fix_package(src, dst):
    total = 0
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.startswith("Stories/"):
                root = etree.fromstring(data)
                n = fix_tree(root)
                if n:
                    total += n
                    data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            comp = zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED
            zout.writestr(info, data, compress_type=comp)
    return total


if __name__ == "__main__":
    print("lifted", fix_package(sys.argv[1], sys.argv[2]))
