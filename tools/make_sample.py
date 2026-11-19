"""Build the saved example shipped with the app (F10) from the hand-written reference.

It is labelled as not being a model run. Real recorded runs replace or join it
after spike 5 is run with Sho's keys."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from idmlbrief import checks, pipeline, xliff  # noqa: E402
from idmlbrief.brief import default_brief  # noqa: E402

REF = {k: v for k, v in json.load(open(ROOT / "fixtures/ja_reference.json", encoding="utf-8")).items() if not k.startswith("_")}
CASES = json.load(open(ROOT / "fixtures/planted_cases.json", encoding="utf-8"))["A"]

brief = default_brief()
state, work = pipeline.open_run(ROOT / "fixtures/tx40_ja_base.idml", brief, Path(tempfile.mkdtemp()), {})
over = {c["seg"]: c["text"] for c in CASES if c["expect"][0] in ("B-01", "B-03")}
for s in state.segs:
    if s.status == "kept_source":
        continue
    s.target = xliff.from_tagged(over.get(s.id, REF[s.id]), s.source)
    s.status = "translated"
checks.run_machine(state, brief)
state.sample_label = ("Example. The translation was written by hand with two planted brief violations (B-01, B-03). "
                      "It is not output from an AI model, and the AI review has not run.")
state.log = []
out = ROOT / "idmlbrief/samples/tx40-handwritten-example.json"
out.write_text(json.dumps({"state": state.to_dict(), "export": None}, ensure_ascii=False), encoding="utf-8")
print(out, len(state.findings), "findings")
