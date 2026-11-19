"""Machine checks (F5). Re-run in full every time; they cost nothing."""
from __future__ import annotations

import re

from .brief import Brief
from .model import APP_CHECKS, Eval, Finding, RunState, text_of
from .xliff import LB, Code

NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
NUM_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
             "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
NUM_WORD_RE = re.compile(r"\b(" + "|".join(NUM_WORDS) + r")\b", re.I)
LATIN_WORD = re.compile(r"[A-Za-z]{2,}")


def has_translatable_text(parts) -> bool:
    return bool(re.search(r"[A-Za-z]", text_of(parts)))


def _contains(text, needle, case=False):
    if case:
        return needle in text
    return needle.lower() in text.lower()


def _pair_text(parts, pid):
    """Text inside pair pid (including nested text)."""
    out, depth, inside = [], 0, False
    for p in parts:
        if isinstance(p, Code) and p.kind == "bpt" and p.id == pid:
            inside = True
            continue
        if isinstance(p, Code) and p.kind == "ept" and p.id == pid:
            inside = False
            continue
        if inside and isinstance(p, str):
            out.append(p)
    return "".join(out)


def run_machine(state: RunState, brief: Brief):
    """Replace all machine findings and machine evals."""
    state.findings = [f for f in state.findings if f.method != "machine"]
    state.evals = [e for e in state.evals if e.method != "machine"]

    def add(seg, rid, sev, evidence, src="", tgt="", suggestion=""):
        kind = APP_CHECKS[rid][0] if rid in APP_CHECKS else (
            "term" if brief.rule(rid).type in ("term", "keep") else "instruction")
        state.findings.append(Finding(seg.id, rid, kind, "machine", sev, evidence, suggestion, src, tgt, seg.version()))

    for seg in state.segs:
        src = text_of(seg.source)
        tgt = text_of(seg.target) if seg.target is not None else None
        v = seg.version()
        if seg.status == "failed":
            add(seg, "C-code", "error", seg.note or "The model output did not keep the inline codes.")
        if tgt is None:
            for r in brief.rules:
                if r.machine:
                    state.evals.append(Eval(r.id, seg.id, "unevaluated", "machine", "not translated", v))
            continue
        if seg.translatable and has_translatable_text(seg.source) and not tgt.strip():
            add(seg, "C-empty", "error", "The translation is empty.")
        if src.count(LB) != tgt.count(LB):
            add(seg, "C-linebreak", "needs-check", f"Forced line breaks: source {src.count(LB)}, translation {tgt.count(LB)}.")
        for n in NUM_RE.findall(src):
            if n not in tgt:
                add(seg, "C-number", "needs-check", f"{n} is in the source but not in the translation.", n)
        # possibly untranslated: same as the source, has words, not only keep-terms
        if seg.translatable and seg.status != "failed" and tgt.strip() == src.strip():
            rest = src
            for r in brief.rules:
                if r.type == "keep":
                    rest = rest.replace(r.source, "")
            if len(LATIN_WORD.findall(rest)) >= 2:
                add(seg, "C-untranslated", "needs-check", "The translation is identical to the source.")

        for r in brief.rules:
            if not r.machine:
                continue
            sev = "error" if r.level == "required" else "warning"
            res = "not_applicable"
            if r.type == "term" and _contains(src, r.source):
                res = "clean" if r.target in tgt else "violation"
                if res == "violation":
                    add(seg, r.id, sev, f'Source has "{r.source}" but the translation does not use "{r.target}".', r.source)
            elif r.type == "keep" and _contains(src, r.source, case=True):
                res = "clean" if r.source in tgt else "violation"
                if res == "violation":
                    add(seg, r.id, sev, f'"{r.source}" must stay as written.', r.source)
            elif r.type == "digits":
                words = NUM_WORD_RE.findall(src)
                if words:
                    missing = [w for w in words if NUM_WORDS[w.lower()] not in tgt]
                    res = "violation" if missing else "clean"
                    if missing:
                        add(seg, r.id, sev, f'Number word(s) {", ".join(missing)} not written as digits.', ", ".join(missing))
            elif r.type == "styled":
                cm = state.codemap.get(seg.id)
                pairs = [pid for pid, info in (cm or {}).items()
                         if (info.get("style") or "").endswith("/" + r.style) or info.get("style") == r.style]
                styled_src = [pid for pid in pairs if _contains(_pair_text(seg.source, pid), r.source)]
                if cm is None and _contains(src, r.source):
                    res = "cannot_judge"
                elif styled_src:
                    ok = all(r.target in _pair_text(seg.target, pid) for pid in styled_src)
                    res = "clean" if ok else "violation"
                    if not ok:
                        got = " / ".join(_pair_text(seg.target, pid) for pid in styled_src)
                        add(seg, r.id, sev, f'Style "{r.style}" is on "{got}", expected on "{r.target}".', r.source, got)
            state.evals.append(Eval(r.id, seg.id, res, "machine", "", v))


def summarize(state: RunState, brief: Brief):
    """Per rule: counts by result, plus unevaluated reasons. Never one pass/fail."""
    out = []
    ids = [r.id for r in brief.rules] + ["M-meaning", "M-omission", "M-addition"]
    for rid in ids:
        ev = [e for e in state.evals if e.rule_id == rid]
        counts = {k: 0 for k in ("violation", "clean", "not_applicable", "cannot_judge", "unevaluated")}
        reasons = {}
        for e in ev:
            counts[e.result] += 1
            if e.result in ("unevaluated", "cannot_judge"):
                reasons[e.reason or "unknown"] = reasons.get(e.reason or "unknown", 0) + 1
        if not ev:  # never evaluated (e.g. the AI review has not run): count every paragraph
            counts["unevaluated"] = len(state.segs)
            reasons = {"not run yet": len(state.segs)}
        open_f = [f for f in state.findings if f.rule_id == rid and f.status == "open"]
        out.append({"rule_id": rid, "counts": counts, "reasons": reasons, "findings": len(open_f)})
    return out
