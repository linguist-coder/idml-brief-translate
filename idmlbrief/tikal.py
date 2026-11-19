"""Run Okapi Tikal 1.48 (okf_idml) with a fixed JRE and a fixed filter config.

The java child is started directly (no tikal.bat) so it can be killed on
cancel / shutdown. Paths come from IDMLBRIEF_JRE / IDMLBRIEF_OKAPI, or the
bundled `vendor/` directory next to the executable, or the dev tools folder.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TIKAL_VERSION = "1.48.0"
CONFIG_NAME = "okf_idml@idmlbrief"
HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "resources" / f"{CONFIG_NAME}.fprm"


class TikalError(RuntimeError):
    pass


def _base() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return HERE.parent


def find_jre() -> Path:
    # dev: <repo>/vendor/ (git-ignored); packaged: <exe dir>/vendor/
    for cand in (os.environ.get("IDMLBRIEF_JRE"), _base() / "vendor" / "jre"):
        if cand and (Path(cand) / "bin" / "java.exe").exists():
            return Path(cand)
    raise TikalError("Java runtime not found")


def find_okapi() -> Path:
    for cand in (os.environ.get("IDMLBRIEF_OKAPI"), _base() / "vendor" / "okapi"):
        if cand and (Path(cand) / "lib" / f"okapi-lib-{TIKAL_VERSION}.jar").exists():
            return Path(cand)
    raise TikalError(f"Okapi {TIKAL_VERSION} not found")


_current: subprocess.Popen | None = None


def _run(args: list[str], cwd: Path, timeout: int = 300) -> str:
    global _current
    jre, okapi = find_jre(), find_okapi()
    cmd = [str(jre / "bin" / "java.exe"), "-cp", str(okapi / "lib" / "*"),
           "net.sf.okapi.applications.tikal.Main", *args]
    env = dict(os.environ, JAVA_HOME=str(jre))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    _current = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env=env, creationflags=flags)
    try:
        out, _ = _current.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _current.kill()
        _current.communicate()
        raise TikalError("Tikal timed out")
    finally:
        proc, _current = _current, None
    text = out.decode("utf-8", "replace")
    if proc.returncode != 0 or re.search(r"\b(ERROR|Exception)\b", text):
        raise TikalError(f"Tikal failed (exit {proc.returncode}): {text[-800:]}")
    return text


def kill_running():
    if _current and _current.poll() is None:
        _current.kill()


def _config_dir(work: Path) -> Path:
    d = work / "_cfg"
    d.mkdir(exist_ok=True)
    shutil.copy(CONFIG_FILE, d / CONFIG_FILE.name)
    return d


def extract(idml: Path, work: Path) -> Path:
    """Copy idml into work/ and extract. Returns the XLIFF path."""
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    local = work / "input.idml"
    if idml.resolve() != local.resolve():
        shutil.copy(idml, local)
    cfg = _config_dir(work)
    _run(["-x", local.name, "-sl", "en", "-tl", "ja", "-fc", CONFIG_NAME, "-pd", str(cfg)], work)
    xlf = work / "input.idml.xlf"
    if not xlf.exists():
        raise TikalError("Tikal produced no XLIFF")
    strip_config_path(xlf)
    return xlf


def merge(xlf: Path, work: Path) -> Path:
    """Merge xlf (must sit next to work/input.idml). Returns work/input.out.idml."""
    work = work.resolve()
    target = work / "input.idml.xlf"
    if xlf.resolve() != target.resolve():
        shutil.copy(xlf, target)
    cfg = _config_dir(work)
    out = work / "input.out.idml"
    if out.exists():
        out.unlink()
    _run(["-m", target.name, "-sl", "en", "-tl", "ja", "-fc", CONFIG_NAME, "-pd", str(cfg)], work)
    if not out.exists():
        raise TikalError("Tikal produced no IDML")
    return out


def strip_config_path(xlf: Path):
    """Tikal writes the absolute config path (with the user name) into okp:configId."""
    s = xlf.read_text(encoding="utf-8")
    s2 = re.sub(r' okp:configId="[^"]*"', "", s)
    if s2 != s:
        xlf.write_text(s2, encoding="utf-8")
