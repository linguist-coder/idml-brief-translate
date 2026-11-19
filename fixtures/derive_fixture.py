"""Derive the IDML Brief Translate test document (Japanese-capable fonts) from the
English test document used in the earlier IDML translation test (tx40.idml).

The original file is read only. Every change is a plain string edit in
Resources/Styles.xml and is listed in CHANGES below, so the derived file
can be rebuilt and diffed at any time.

Usage: python derive_fixture.py <source.idml> <out.idml>
"""
import hashlib
import sys
import zipfile

JP_FONT = "BIZ UDPGothic"  # ships with Windows 10/11 (Regular, Bold)

# (old, new, expected count) applied to Resources/Styles.xml only
CHANGES = [
    ('<AppliedFont type="string">Arial</AppliedFont>',
     f'<AppliedFont type="string">{JP_FONT}</AppliedFont>', 6),
    ('<AppliedFont type="string">Minion Pro</AppliedFont>',
     f'<AppliedFont type="string">{JP_FONT}</AppliedFont>', 1),
    # BIZ UDPGothic has no Italic. Caption -> Regular.
    ('Name="Caption" Imported="false"', 'Name="Caption" Imported="false"', 1),
    # UI Label keeps a visible difference from Strong: Regular + blue fill.
    ('Name="UI Label" FontStyle="Italic">',
     'Name="UI Label" FontStyle="Regular" FillColor="Color/C=100 M=90 Y=10 K=0">', 1),
]
ITALIC_PARA = ('FontStyle="Italic"', 'FontStyle="Regular"')  # remaining Italic (Caption)


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def derive(src, dst):
    with zipfile.ZipFile(src) as zin:
        names = zin.namelist()
        assert names[0] == "mimetype"
        with zipfile.ZipFile(dst, "w") as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == "Resources/Styles.xml":
                    s = data.decode("utf-8")
                    for old, new, n in CHANGES:
                        got = s.count(old)
                        if got != n:
                            raise SystemExit(f"expected {n} x {old!r}, found {got}")
                        s = s.replace(old, new)
                    got = s.count(ITALIC_PARA[0])
                    if got != 1:
                        raise SystemExit(f"expected 1 remaining Italic, found {got}")
                    s = s.replace(*ITALIC_PARA)
                    data = s.encode("utf-8")
                comp = zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED
                zout.writestr(zipfile.ZipInfo(info.filename, date_time=(2026, 11, 17, 0, 0, 0)), data,
                              compress_type=comp)
    print("source", sha256(src))
    print("derived", sha256(dst))


if __name__ == "__main__":
    derive(sys.argv[1], sys.argv[2])
