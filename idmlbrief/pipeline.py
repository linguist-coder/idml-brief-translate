"""Run lifecycle: open an IDML, edit, export a working IDML + report (F3, F7, F8)."""
from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import checks, codemap, gate, jastyle, scope, tikal, xliff
from .brief import Brief
from .idmlfix import fix_package
from .model import RunState, Seg, text_of
from .review import mark_stale
from .translate import prepare
from .xliff import Code

APP_VERSION = "0.1.1"


class ScopeRefused(ValueError):
    def __init__(self, report):
        super().__init__("This IDML contains content IDML Brief Translate 0.1 does not handle.")
        self.report = report


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def open_run(idml: Path, brief: Brief, workroot: Path, settings: dict) -> tuple[RunState, Path]:
    rep = scope.scan(idml)
    if not rep.ok:
        raise ScopeRefused(rep)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    work = (workroot / run_id).resolve()
    (work / "x").mkdir(parents=True)
    xlf = tikal.extract(idml, work / "x")
    _, segs = xliff.load(xlf)
    cm = codemap.probe(work / "x" / "input.idml", work / "probe")
    state = RunState(
        run_id=run_id, input_name=Path(idml).name, input_sha256=sha256(idml), xlf_sha256=sha256(xlf),
        brief=brief.to_dict(), brief_hash=brief.hash(), settings=dict(settings),
        segs=[Seg(s.id, s.file, s.source) for s in segs],
        codemap={sid: {pid: {"style": p.char_style or "", "overrides": p.overrides, "label": p.label()}
                       for pid, p in sc.pairs.items()} | {pid: {"element": k} for pid, k in sc.placeholders.items()}
                 for sid, sc in cm.items()},
        scope={"translated_stories": rep.translated_stories, "excluded": rep.excluded, "warnings": rep.warnings,
               "ja_line_breaking": jastyle.scan(idml, rep.translated_stories)},
    )
    prepare(state)
    checks.run_machine(state, brief)
    save(state, work)
    return state, work


def save(state: RunState, work: Path):
    tmp = work / "state.json.tmp"
    tmp.write_text(json.dumps(state.to_dict(), ensure_ascii=False), encoding="utf-8")
    tmp.replace(work / "state.json")


def load(work: Path) -> RunState:
    return RunState.from_dict(json.loads((work / "state.json").read_text(encoding="utf-8")))


def edit_segment(state: RunState, brief: Brief, sid: str, tagged: str):
    """Manual edit (F8). Same code validation as the model output."""
    seg = state.seg(sid)
    parts = xliff.from_tagged(tagged, seg.source)
    if not text_of(parts).strip() and checks.has_translatable_text(seg.source):
        raise xliff.CodeError("empty translation")
    seg.target = parts
    seg.status = "translated"
    seg.edited = True
    seg.note = "edited by hand"
    state.doc_version += 1
    state.edit_count += 1
    mark_stale(state, "The translation was edited after the AI review.")
    checks.run_machine(state, brief)


def export(state: RunState, work: Path, out_dir: Path) -> dict:
    """Write targets, merge, fix, gate. Returns a result dict; the IDML only exists if the gate passed."""
    x = work / "x"
    tree, segs = xliff.load(x / "input.idml.xlf")
    targets = {}
    for s in state.segs:
        targets[s.id] = s.target if s.target is not None else list(s.source)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    ex = work / f"export-{stamp}"
    ex.mkdir()
    shutil.copy(x / "input.idml", ex / "input.idml")
    xliff.write_targets(tree, targets, ex / "ja.xlf")
    tikal.strip_config_path(ex / "ja.xlf")
    merged = tikal.merge(ex / "ja.xlf", ex)
    fixed = ex / "fixed.idml"
    fix_package(merged, fixed)
    pieces = []
    for s in state.segs:
        if s.status == "translated":
            buf = ""
            for p in s.target:
                if isinstance(p, Code):
                    pieces.append(buf)
                    buf = ""
                else:
                    buf += p
            pieces.append(buf)
    problems = gate.check(x / "input.idml", fixed, [p for p in pieces if p.strip()])
    result = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app_version": APP_VERSION, "tikal": tikal.TIKAL_VERSION,
        "input_sha256": state.input_sha256, "brief_hash": state.brief_hash,
        "targets_version": state.targets_version(), "doc_version": state.doc_version,
        "gate_problems": problems, "idml": None, "idml_sha256": None,
    }
    if problems:
        return result
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(state.input_name).stem
    out = out_dir / f"{stem}.ja.work-{stamp}.idml"
    shutil.copy(fixed, out)
    result["idml"] = str(out)
    result["idml_sha256"] = sha256(out)
    return result


def run_summary(state: RunState, brief: Brief) -> dict:
    by_status = {}
    for s in state.segs:
        by_status[s.status] = by_status.get(s.status, 0) + 1
    return {"segments": len(state.segs), "by_status": by_status,
            "rules": checks.summarize(state, brief), "ai_review": state.ai_review,
            "ai_review_reason": state.ai_review_reason,
            "findings_open": sum(1 for f in state.findings if f.status == "open")}


def usage_dicts(state):
    return [asdict(u) for u in state.usage]


def export_with_report(state: RunState, brief: Brief, work: Path, out_dir: Path) -> dict:
    from . import report
    res = export(state, work, out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = Path(res["idml"]).stem.rsplit("work-", 1)[-1] if res["idml"] else datetime.now().strftime("%Y%m%d-%H%M%S")
    rp = out_dir / f"{Path(state.input_name).stem}.ja.report-{stamp}.html"
    rp.write_text(report.build(state, brief, res), encoding="utf-8")
    res["report"] = str(rp)
    (work / "last_export.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return res
