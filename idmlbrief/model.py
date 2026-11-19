"""Run state: segments, findings and per-rule evaluation status, bound to versions."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from .xliff import Code

# app-defined check IDs (not brief rules). The review model may use M-* only.
APP_CHECKS = {
    "M-meaning": ("meaning", "Meaning differs from the source"),
    "M-omission": ("meaning", "Source content missing in the translation"),
    "M-addition": ("meaning", "Translation adds content that is not in the source"),
    "C-code": ("code-structure", "Inline codes could not be placed (needs an external fix)"),
    "C-empty": ("code-structure", "Empty translation for a paragraph with text"),
    "C-untranslated": ("instruction", "Possibly untranslated (same as the source)"),
    "C-number": ("instruction", "A number in the source is not in the translation"),
    "C-linebreak": ("code-structure", "Number of forced line breaks changed"),
}
AI_APP_CHECKS = ("M-meaning", "M-omission", "M-addition")


def parts_to_json(parts):
    if parts is None:
        return None
    return [p if isinstance(p, str) else {"k": p.kind, "i": p.id, "r": p.raw} for p in parts]


def parts_from_json(js):
    if js is None:
        return None
    return [p if isinstance(p, str) else Code(p["k"], p["i"], p["r"]) for p in js]


def text_of(parts) -> str:
    return "".join(p for p in (parts or []) if isinstance(p, str))


def h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


@dataclass
class Seg:
    id: str
    file: str
    source: list
    target: list | None = None
    translatable: bool = True
    status: str = "pending"  # pending | translated | kept_source | failed
    note: str = ""
    edited: bool = False
    attempts: int = 0

    def version(self) -> str:
        return h(parts_to_json(self.target))

    def to_dict(self):
        d = asdict(self)
        d["source"] = parts_to_json(self.source)
        d["target"] = parts_to_json(self.target)
        return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["source"] = parts_from_json(d["source"])
        d["target"] = parts_from_json(d["target"])
        return cls(**d)


@dataclass
class Finding:
    seg_id: str
    rule_id: str  # B-xx or an APP_CHECKS key
    kind: str  # term | instruction | meaning | code-structure
    method: str  # machine | ai
    severity: str  # error | warning | needs-check
    evidence: str
    suggestion: str = ""
    src_quote: str = ""
    tgt_quote: str = ""
    seg_version: str = ""
    status: str = "open"  # open | stale


# per (rule, segment) result
RESULTS = ("violation", "clean", "not_applicable", "cannot_judge", "unevaluated")


@dataclass
class Eval:
    rule_id: str
    seg_id: str
    result: str
    method: str
    reason: str = ""  # for unevaluated / cannot_judge
    seg_version: str = ""


@dataclass
class Usage:
    stage: str  # translate | review
    model: str
    input_tokens: int | None
    output_tokens: int | None


@dataclass
class RunState:
    run_id: str
    input_name: str
    input_sha256: str
    xlf_sha256: str
    brief: dict
    brief_hash: str
    settings: dict
    segs: list[Seg] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    evals: list[Eval] = field(default_factory=list)
    usage: list[Usage] = field(default_factory=list)
    ai_review: str = "not_run"  # not_run | current | stale | running | failed
    ai_review_reason: str = ""
    doc_version: int = 0
    codemap: dict = field(default_factory=dict)  # seg id -> {pair id: {"style":..,"overrides":{..}}}
    scope: dict = field(default_factory=dict)
    log: list[str] = field(default_factory=list)
    sample_label: str = ""  # set for saved example runs

    def seg(self, sid) -> Seg:
        return next(s for s in self.segs if s.id == sid)

    def to_dict(self):
        d = asdict(self)
        d["segs"] = [s.to_dict() for s in self.segs]
        return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["segs"] = [Seg.from_dict(s) for s in d["segs"]]
        d["findings"] = [Finding(**f) for f in d["findings"]]
        d["evals"] = [Eval(**e) for e in d["evals"]]
        d["usage"] = [Usage(**u) for u in d["usage"]]
        return cls(**d)

    def targets_version(self) -> str:
        return h([s.version() for s in self.segs])
