"""End-to-end with mock providers: reference JA (spike 4) as model A output."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from idmlbrief import pipeline, translate, review, checks  # noqa: E402
from idmlbrief.brief import default_brief  # noqa: E402
from idmlbrief.llm import MockProvider  # noqa: E402

REF = {k: v for k, v in json.load(open(ROOT / "fixtures/ja_reference.json", encoding="utf-8")).items() if not k.startswith("_")}


def mock_translate(system, user, schema):
    req = json.loads(user)
    return {"translations": [{"id": s["id"], "text": REF[s["id"]]} for s in req["segments"]]}


def mock_review(system, user, schema):
    req = json.loads(user)
    return {"segments": [{"id": s["id"], "findings": []} for s in req["segments"]]}


def main(work_root):
    brief = default_brief()
    state, work = pipeline.open_run(ROOT / "fixtures/tx40_ja_base.idml", brief, Path(work_root), {})
    translate.translate(state, brief, MockProvider(mock_translate))
    checks.run_machine(state, brief)
    review.review(state, brief, MockProvider(mock_review))
    pipeline.save(state, work)
    summ = pipeline.run_summary(state, brief)
    res = pipeline.export(state, work, Path(work_root) / "out")
    return state, work, summ, res


if __name__ == "__main__":
    state, work, summ, res = main(sys.argv[1])
    print(json.dumps(summ, ensure_ascii=False, indent=1))
    for f in state.findings:
        print("F", f.seg_id, f.rule_id, f.severity, f.evidence)
    print(json.dumps(res, ensure_ascii=False, indent=1))
