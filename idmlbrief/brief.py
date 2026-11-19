"""Translation brief (F1) with bilingual terms (F2).

Every rule has an ID. Only a few rule types are machine-checked:
  term   - source term -> required target term
  keep   - text that must appear unchanged (do not translate)
  digits - numbers in the source must appear as digits in the target
  styled - a character style must sit on a given target text
           (needs the code -> style map from codemap.py; otherwise "cannot judge")
  free   - written instruction; checked by the review model only
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

RULE_TYPES = ("term", "keep", "digits", "styled", "free")
MACHINE_TYPES = {"term", "keep", "digits", "styled"}
ID_RE = re.compile(r"^B-\d{2}$")


@dataclass
class Rule:
    id: str
    type: str
    text: str  # what the rule says, in plain words (shown to models and people)
    source: str = ""  # term / keep / styled: the source-side text
    target: str = ""  # term: required target term / styled: target text that carries the style
    style: str = ""  # styled: character style name
    level: str = "required"  # required | recommended
    scope: str = "all"  # v1: whole document only
    lang: str = "ja"

    def validate(self):
        if not ID_RE.match(self.id):
            raise ValueError(f"rule id must look like B-01: {self.id!r}")
        if self.type not in RULE_TYPES:
            raise ValueError(f"{self.id}: unknown type {self.type}")
        if self.level not in ("required", "recommended"):
            raise ValueError(f"{self.id}: level must be required or recommended")
        if self.scope != "all":
            raise ValueError(f"{self.id}: v1 supports scope 'all' only")
        if self.lang != "ja":
            raise ValueError(f"{self.id}: v1 supports Japanese only")
        if not self.text.strip():
            raise ValueError(f"{self.id}: describe the rule")
        if self.type in ("term", "keep", "styled") and not self.source.strip():
            raise ValueError(f"{self.id}: source text is required")
        if self.type in ("term", "styled") and not self.target.strip():
            raise ValueError(f"{self.id}: target text is required")
        if self.type == "styled" and not self.style.strip():
            raise ValueError(f"{self.id}: character style name is required")

    @property
    def machine(self) -> bool:
        return self.type in MACHINE_TYPES


@dataclass
class Brief:
    name: str = "Untitled brief"
    rules: list[Rule] = field(default_factory=list)

    def validate(self):
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule ids must be unique")
        if len(self.rules) > 50:
            raise ValueError("v1 keeps a brief to 50 rules")
        for r in self.rules:
            r.validate()

    def to_dict(self):
        return {"name": self.name, "rules": [asdict(r) for r in self.rules]}

    @classmethod
    def from_dict(cls, d):
        b = cls(name=str(d.get("name", "Untitled brief"))[:200],
                rules=[Rule(**{k: str(v) for k, v in r.items() if k in Rule.__dataclass_fields__})
                       for r in d.get("rules", [])])
        b.validate()
        return b

    def hash(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def rule(self, rid):
        return next((r for r in self.rules if r.id == rid), None)

    def for_prompt(self) -> str:
        lines = []
        for r in self.rules:
            extra = ""
            if r.type == "term":
                extra = f' (translate "{r.source}" as "{r.target}")'
            elif r.type == "keep":
                extra = f' (keep "{r.source}" exactly as written)'
            elif r.type == "styled":
                extra = f' (the styled source word "{r.source}" becomes "{r.target}"; keep the style tag around it)'
            lines.append(f"{r.id} [{r.level}] {r.text}{extra}")
        return "\n".join(lines)


def default_brief() -> Brief:
    """The template shipped with the app, written for the TX-40 sample."""
    return Brief(name="TX-40 quick guide (EN -> JA)", rules=[
        Rule("B-01", "keep", "Do not translate UI button names. POWER stays in English.", source="POWER"),
        Rule("B-02", "styled", "The UI Label style goes on the Japanese colour word.",
             source="green", target="緑色", style="UI Label"),
        Rule("B-03", "term", "Use the agreed term for label roll.", source="label roll", target="ラベルロール"),
        Rule("B-04", "term", "Use the agreed term for print head.", source="print head", target="プリントヘッド"),
        Rule("B-05", "keep", "Do not translate the product name.", source="TX-40"),
        Rule("B-06", "digits", "Write numbers as digits (two seconds -> 2 秒).", level="recommended"),
        Rule("B-07", "free", "Use the polite form (です・ます) in instructions."),
        Rule("B-08", "free", "Safety-related wording must not be softened or shortened."),
    ])
