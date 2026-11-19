"""Write THIRD_PARTY_NOTICES.txt for the packaged app.

The Python package list is not hand-written: it is read from what PyInstaller
actually bundled (the PYZ archive inside the exe + top-level folders in
_internal), mapped to installed distributions of the build Python. Run it with
the same Python that ran PyInstaller.
Java parts are referenced in place (vendor/jre/legal, vendor/okapi/lib/licenses),
which ship inside the zip unchanged.
Usage: python tools/notices.py DIST_DIR OUT_FILE
"""
import sys
from importlib import metadata
from pathlib import Path

HEAD = """IDML Brief Translate - third-party notices
==========================================

The code of IDML Brief Translate itself is released under the MIT License
(LICENSE.txt). The distribution as a whole is NOT under one license: it bundles
the components below, each under its own terms.

1. Java runtime: Eclipse Temurin 21.0.12.1+1 JRE (OpenJDK build; see vendor/jre/release)
   License: GNU GPL v2 with the Classpath Exception, plus third-party notices.
   Full texts: vendor/jre/legal/ and vendor/jre/NOTICE (shipped unchanged).
   Source code: the complete corresponding source archive
   (OpenJDK21U-jdk-sources_21.0.12.1_1.tar.gz, as published by Eclipse Adoptium)
   is attached to the same GitHub release as this zip:
   https://github.com/linguist-coder/idml-brief-translate/releases
   Upstream: https://github.com/adoptium/temurin21-binaries/releases/tag/jdk-21.0.12.1%2B1
   Build scripts: https://github.com/adoptium/temurin-build

2. Okapi Framework 1.48.0 (Tikal), redistributed unchanged from okapiframework.org
   License: Apache License 2.0. Bundled libraries and their licenses:
   vendor/okapi/lib/licenses/ and META-INF/ inside each jar (shipped unchanged).
   Libraries in vendor/okapi/lib without a license file in that folder:
   - htmlparser 1.4: MIT / BSD (per its jar manifest)
   - java-diff 1.1.2: as distributed with Okapi 1.48.0

3. Python runtime: CPython {pyver} (Python Software Foundation License), with
   OpenSSL 3 (Apache License 2.0), libffi (MIT) and the Microsoft Visual C++
   runtime (VCRUNTIME140*.dll, redistributable files).
   The executable is built with PyInstaller {pyiver} (GPL v2 with the bootloader
   exception, which allows distributing the built program under any terms).

4. C libraries inside the lxml {lxmlver} binary module (lxml/etree*.pyd), as built
   and published by the lxml project on PyPI, used unchanged:
   libxml2 {libxml2} (MIT), libxslt {libxslt} (MIT), zlib (zlib License) and
   libiconv (GNU LGPL v2.1). Source: https://github.com/lxml/lxml (tag lxml-{lxmlver})
   and the upstream projects. The lxml module is a separate file in _internal/lxml/
   and can be replaced with your own build of the same lxml version.

5. Python packages bundled in this build:
"""

SKIP = {"pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile", "pywin32-ctypes", "pip"}


def bundled_toplevel(dist_dir: Path) -> set[str]:
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    import tempfile
    exe = next(dist_dir.glob("*.exe"))
    c = CArchiveReader(str(exe))
    names = set()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "pyz"
        p.write_bytes(c.extract("PYZ.pyz"))
        z = ZlibArchiveReader(str(p))
        names |= {m.split(".")[0] for m in z.toc}
        del z
    internal = dist_dir / "_internal"
    for q in internal.iterdir():
        names.add(q.name.split(".")[0].split("-")[0])
    return names


def main(dist_dir, out):
    dist_dir = Path(dist_dir)
    top = bundled_toplevel(dist_dir)
    pkg2dist = metadata.packages_distributions()
    dists = sorted({d for t in top for d in pkg2dist.get(t, [])} - SKIP, key=str.lower)
    import PyInstaller
    from lxml import etree
    lines = [HEAD.format(pyver=sys.version.split()[0], pyiver=PyInstaller.__version__,
                         lxmlver=metadata.version("lxml"),
                         libxml2=".".join(map(str, etree.LIBXML_VERSION)),
                         libxslt=".".join(map(str, etree.LIBXSLT_VERSION)))]
    for name in dists:
        d = metadata.distribution(name)
        lic = d.metadata.get("License-Expression") or d.metadata.get("License") or ""
        lic = lic.splitlines()[0][:80] if lic else ""
        if not lic:
            lic = "; ".join(c.split("::")[-1].strip() for c in d.metadata.get_all("Classifier") or [] if c.startswith("License"))
        lines.append(f"   - {d.metadata['Name']} {d.version}: {lic or 'see text below'}\n")
    lines.append("\n\nLicense texts of the Python packages\n====================================\n")
    for name in dists:
        d = metadata.distribution(name)
        files = [f for f in (d.files or []) if any(k in f.name.upper() for k in ("LICEN", "COPYING", "NOTICE"))]
        if not files:
            lines.append(f"\n----- {d.metadata['Name']} {d.version}: no license file shipped; license field above -----\n")
        for f in files:
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if text:
                lines.append(f"\n----- {d.metadata['Name']} {d.version}: {f.name} -----\n{text}\n")
    Path(out).write_text("".join(lines), encoding="utf-8", newline="\r\n")
    print("bundled distributions:", ", ".join(dists))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
