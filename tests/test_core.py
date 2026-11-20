"""Core tests with mock providers. Run: python -m unittest discover -s tests -v
Needs the dev JRE + Okapi (tikal.find_jre / find_okapi)."""
import copy
import io
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from idmlbrief import checks, gate, jastyle, pipeline, report, review, scope, translate, xliff  # noqa: E402
from idmlbrief.brief import Brief, Rule, default_brief  # noqa: E402
from idmlbrief.llm import CallResult, MockProvider  # noqa: E402
from idmlbrief.xliff import CodeError  # noqa: E402

FIX = ROOT / "fixtures" / "tx40_ja_base.idml"
REF = {k: v for k, v in json.load(open(ROOT / "fixtures/ja_reference.json", encoding="utf-8")).items() if not k.startswith("_")}
TU4 = "NFFD13CDE-tu4"
TU2 = "NFFD13CDE-tu2"


def tr_with(over=None, calls=None):
    def fn(system, user, schema):
        req = json.loads(user)
        if calls is not None:
            calls.append([s["id"] for s in req["segments"]])
        return {"translations": [{"id": s["id"], "text": (over or {}).get(s["id"], REF[s["id"]])} for s in req["segments"]]}
    return MockProvider(fn)


def rv_with(fn_per_seg=None, extra=None, skip=()):
    def fn(system, user, schema):
        req = json.loads(user)
        segs = [{"id": s["id"], "findings": (fn_per_seg(s) if fn_per_seg else [])} for s in req["segments"] if s["id"] not in skip]
        if extra:
            segs += extra
        return {"segments": segs}
    return MockProvider(fn)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="bl-test-"))
        cls.brief = default_brief()
        cls.state0, cls.work = pipeline.open_run(FIX, cls.brief, cls.tmp, {})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def fresh(self):
        return copy.deepcopy(self.state0)


class TranslateTests(Base):
    def test_happy_path_and_export(self):
        st = self.fresh()
        translate.translate(st, self.brief, tr_with())
        checks.run_machine(st, self.brief)
        self.assertEqual({s.status for s in st.segs}, {"translated", "kept_source"})
        res = pipeline.export_with_report(st, self.brief, self.work, self.tmp / "out")
        self.assertEqual(res["gate_problems"], [])
        self.assertTrue(Path(res["idml"]).exists())
        html = Path(res["report"]).read_text(encoding="utf-8")
        self.assertIn("Not checked", html)
        for bad in ("verified", "ready to deliver", "Verified"):
            self.assertNotIn(bad, html)

    def test_kept_source_not_sent(self):
        st = self.fresh()
        calls = []
        translate.translate(st, self.brief, tr_with(calls=calls))
        sent = {i for c in calls for i in c}
        kept = {s.id for s in st.segs if s.status == "kept_source"}
        self.assertTrue(kept)
        self.assertFalse(sent & kept)

    def test_broken_codes_retry_then_fail(self):
        st = self.fresh()
        bad = "<g1>POWER</g1> ボタン"  # drops g2/g3
        translate.translate(st, self.brief, tr_with({TU4: bad}))
        s = st.seg(TU4)
        self.assertEqual(s.status, "failed")
        self.assertEqual(s.attempts, 2)
        self.assertEqual(s.target, s.source)  # source kept in the working file
        checks.run_machine(st, self.brief)
        self.assertTrue(any(f.rule_id == "C-code" and f.seg_id == TU4 for f in st.findings))
        res = pipeline.export(st, self.work, self.tmp / "out2")
        self.assertEqual(res["gate_problems"], [])  # English paragraph is still a valid file

    def test_retry_fixes(self):
        st = self.fresh()
        n = {"c": 0}

        def fn(system, user, schema):
            req = json.loads(user)
            n["c"] += 1
            out = []
            for s in req["segments"]:
                t = REF[s["id"]]
                if s["id"] == TU4 and n["c"] == 1:
                    t = "<g1>POWER</g1>"
                out.append({"id": s["id"], "text": t})
            return {"translations": out}
        translate.translate(st, self.brief, MockProvider(fn))
        self.assertEqual(st.seg(TU4).status, "translated")

    def test_missing_and_unknown_ids(self):
        st = self.fresh()

        def fn(system, user, schema):
            req = json.loads(user)
            out = [{"id": s["id"], "text": REF[s["id"]]} for s in req["segments"] if s["id"] != TU2]
            out.append({"id": "nope", "text": "x"})
            return {"translations": out}
        translate.translate(st, self.brief, MockProvider(fn))
        self.assertEqual(st.seg(TU2).status, "failed")
        self.assertTrue(any("not asked" in l for l in st.log))

    def test_auth_error_stops(self):
        st = self.fresh()
        p = MockProvider(lambda *a: CallResult(None, "mock", error="auth"))
        translate.translate(st, self.brief, p)
        self.assertFalse(any(s.status == "translated" for s in st.segs))
        self.assertLessEqual(len(st.usage), 1 + 20)

    def test_rate_limit_leaves_pending(self):
        st = self.fresh()
        translate.translate(st, self.brief, MockProvider(lambda *a: CallResult(None, "mock", error="rate_limit")))
        self.assertTrue(all(s.status in ("pending", "kept_source") for s in st.segs))

    def test_cancel(self):
        st = self.fresh()
        translate.translate(st, self.brief, tr_with(), cancel=lambda: True)
        self.assertFalse(any(s.status == "translated" for s in st.segs))


class CheckTests(Base):
    def test_styled_rule_violation(self):
        st = self.fresh()
        # UI Label (g2) on the wrong word
        wrong = REF[TU4].replace("<g2>緑色</g2>", "緑色").replace("ステータスランプ", "<g2>ステータスランプ</g2>")
        translate.translate(st, self.brief, tr_with({TU4: wrong}))
        checks.run_machine(st, self.brief)
        self.assertTrue(any(f.rule_id == "B-02" and f.seg_id == TU4 for f in st.findings))

    def test_term_and_keep(self):
        st = self.fresh()
        t = REF[TU4].replace("POWER", "電源")
        translate.translate(st, self.brief, tr_with({TU4: t}))
        checks.run_machine(st, self.brief)
        self.assertTrue(any(f.rule_id == "B-01" for f in st.findings))
        ev = [e for e in st.evals if e.rule_id == "B-01" and e.seg_id == TU4]
        self.assertEqual(ev[0].result, "violation")

    def test_untranslated_flag(self):
        st = self.fresh()
        src = xliff.to_tagged(st.seg(TU2).source)
        translate.translate(st, self.brief, tr_with({TU2: src}))
        checks.run_machine(st, self.brief)
        self.assertTrue(any(f.rule_id == "C-untranslated" and f.seg_id == TU2 for f in st.findings))


class ReviewTests(Base):
    def translated(self):
        st = self.fresh()
        translate.translate(st, self.brief, tr_with())
        checks.run_machine(st, self.brief)
        return st

    def test_unknown_rule_dropped_missing_unevaluated(self):
        st = self.translated()

        def per(s):
            return [{"rule_id": "B-99", "severity": "error", "evidence": "x", "suggestion": "", "src_quote": "", "tgt_quote": ""}]
        review.review(st, self.brief, rv_with(per, skip=(TU2,)))
        self.assertFalse(any(f.rule_id == "B-99" for f in st.findings))
        ev = {e.result for e in st.evals if e.method == "ai" and e.seg_id == TU2}
        self.assertEqual(ev, {"unevaluated"})
        self.assertEqual(st.ai_review, "current")

    def test_finding_kept(self):
        st = self.translated()

        def per(s):
            if s["id"] == TU2:
                return [{"rule_id": "B-08", "severity": "error", "evidence": "softened", "suggestion": "s",
                         "src_quote": "safety", "tgt_quote": "安全"}]
            return []
        review.review(st, self.brief, rv_with(per))
        self.assertTrue(any(f.rule_id == "B-08" and f.method == "ai" for f in st.findings))

    def test_edit_during_review_discards(self):
        st = self.translated()

        def fn(system, user, schema):
            st.doc_version += 1
            return {"segments": [{"id": s["id"], "findings": []} for s in json.loads(user)["segments"]]}
        ok = review.review(st, self.brief, MockProvider(fn))
        self.assertFalse(ok)
        self.assertEqual(st.ai_review, "stale")

    def test_edit_marks_stale_and_validates(self):
        st = self.translated()
        review.review(st, self.brief, rv_with())
        self.assertEqual(st.ai_review, "current")
        with self.assertRaises(CodeError):
            pipeline.edit_segment(st, self.brief, TU4, "POWER だけ")
        self.assertEqual(st.ai_review, "current")
        self.assertEqual(st.edit_count, 0)
        pipeline.edit_segment(st, self.brief, TU4, REF[TU4].replace("押し続けます", "長押しします"))
        self.assertEqual(st.ai_review, "stale")
        self.assertEqual(st.edit_count, 1)
        self.assertTrue(all(f.status == "stale" for f in st.findings if f.method == "ai"))

    def test_all_failed(self):
        st = self.translated()
        review.review(st, self.brief, MockProvider(lambda *a: CallResult(None, "mock", error="timeout")))
        self.assertEqual(st.ai_review, "failed")
        self.assertFalse(any(e.result == "clean" for e in st.evals if e.method == "ai"))


def rewrite_zip(src, dst, fn):
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, "w") as zo:
        for i in zi.infolist():
            data = zi.read(i.filename)
            data = fn(i.filename, data)
            if data is None:
                continue
            zo.writestr(i, data)


class GateTests(Base):
    def test_negative_controls(self):
        st = self.fresh()
        translate.translate(st, self.brief, tr_with())
        res = pipeline.export(st, self.work, self.tmp / "g")
        good = Path(res["idml"])
        src = self.work / "x" / "input.idml"
        cases = {
            "drop footnote": lambda n, d: d.replace(b"<Footnote", b"<FootnoteX").replace(b"</Footnote>", b"</FootnoteX>") if n.startswith("Stories/") else d,
            "drop tracking": lambda n, d: d.replace(b' Tracking="-40"', b"") if n.startswith("Stories/") else d,
            "drop xref": lambda n, d: d.replace(b"CrossReferenceSource", b"CrossReferenceSourceX") if n.startswith("Stories/") else d,
            "style edit": lambda n, d: d.replace(b"BIZ UDPGothic", b"Arial", 1) if n == "Resources/Styles.xml" else d,
        }
        for name, fn in cases.items():
            bad = self.tmp / f"bad-{name.replace(' ', '_')}.idml"
            rewrite_zip(good, bad, fn)
            with self.subTest(name):
                self.assertNotEqual(gate.check(src, bad), [], name)
        self.assertEqual(gate.check(src, good), [])


class ScopeTests(unittest.TestCase):
    def make(self, fn, extra=None):
        d = Path(tempfile.mkdtemp(prefix="bl-scope-"))
        p = d / "t.idml"
        rewrite_zip(FIX, p, fn)
        if extra:
            with zipfile.ZipFile(p, "a") as z:
                for n, data in extra.items():
                    z.writestr(n, data)
        return p

    def test_base_ok(self):
        self.assertTrue(scope.scan(FIX).ok)

    def test_refusals(self):
        cases = {
            "doctype": dict(fn=lambda n, d: d.replace(b"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>",
                                                     b"<?xml version=\"1.0\" encoding=\"UTF-8\"?><!DOCTYPE x [<!ENTITY a \"b\">]>", 1)
                            if n.startswith("Stories/") else d),
            "zip slip": dict(fn=lambda n, d: d, extra={"../evil.xml": b"<a/>"}),
            "condition": dict(fn=lambda n, d: d.replace(b"<CharacterStyleRange ", b"<CharacterStyleRange AppliedConditions=\"Condition/x\" ", 1)
                              if n.startswith("Stories/") else d),
            "not idml": dict(fn=lambda n, d: b"application/zip" if n == "mimetype" else d),
        }
        for name, kw in cases.items():
            with self.subTest(name):
                self.assertFalse(scope.scan(self.make(**kw)).ok, name)


class ReportTests(Base):
    def test_escaping(self):
        st = self.fresh()
        b = Brief("x<script>", [Rule("B-01", "free", "<img src=x onerror=alert(1)>")])
        translate.translate(st, b, tr_with())
        st.log.append("<b>")
        html = report.build(st, b, None)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)


class JaLineBreakTests(unittest.TestCase):
    """Read-only warning: styles holding Japanese text without a Japanese composer / kinsoku set."""

    def _variant(self, styles_fn=None, story_fn=None):
        tmp = Path(tempfile.mkdtemp(prefix="bl-ja-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        out = tmp / "v.idml"
        with zipfile.ZipFile(FIX) as zi, zipfile.ZipFile(out, "w") as zo:
            for i in zi.infolist():
                data = zi.read(i.filename)
                if i.filename == "Resources/Styles.xml" and styles_fn:
                    data = styles_fn(data.decode("utf-8")).encode("utf-8")
                if i.filename.startswith("Stories/") and story_fn:
                    data = story_fn(data.decode("utf-8")).encode("utf-8")
                zo.writestr(i, data, compress_type=zipfile.ZIP_STORED if i.filename == "mimetype" else zipfile.ZIP_DEFLATED)
        return out

    @staticmethod
    def _japanese_root(s):
        s = s.replace('Composer="HL Composer"', 'Composer="HL Composer J"', 1)
        return s.replace('<KinsokuSet type="enumeration">Nothing</KinsokuSet>', '<KinsokuSet type="enumeration">HardKinsoku</KinsokuSet>', 1)

    def test_fixture_warns_every_style(self):
        rep = scope.scan(FIX)
        got = jastyle.scan(FIX, rep.translated_stories)
        self.assertIn("Body", [d["style"] for d in got])
        self.assertTrue(all(d["kinsoku"] == "None" and d["composer"] == "Adobe Paragraph Composer" for d in got))
        self.assertEqual(sum(d["paragraphs"] for d in got), 32)  # 33 paragraphs minus one empty anchor paragraph

    def test_japanese_root_inherited_no_warning(self):
        v = self._variant(self._japanese_root)
        self.assertEqual(jastyle.scan(v, scope.scan(v).translated_stories), [])

    def test_style_override_beats_based_on(self):
        def fn(s):
            s = self._japanese_root(s)
            return s.replace('Self="ParagraphStyle/Body" Name="Body"', 'Self="ParagraphStyle/Body" Name="Body" Composer="HL Composer"', 1)
        v = self._variant(fn)
        got = jastyle.scan(v, scope.scan(v).translated_stories)
        self.assertEqual([(d["style"], d["problems"]) for d in got], [("Body", ["not a Japanese composer"])])

    def test_paragraph_override_counts(self):
        v = self._variant(self._japanese_root,
                          lambda s: s.replace('AppliedParagraphStyle="ParagraphStyle/Caption"',
                                              'AppliedParagraphStyle="ParagraphStyle/Caption" Composer="HL Single"'))
        got = jastyle.scan(v, scope.scan(v).translated_stories)
        self.assertEqual([(d["style"], d["composer"]) for d in got], [("Caption", "Adobe Single-line Composer")])

    def test_open_run_and_report(self):
        tmp = Path(tempfile.mkdtemp(prefix="bl-ja-run-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        st, _ = pipeline.open_run(FIX, default_brief(), tmp, {})
        self.assertTrue(st.scope["ja_line_breaking"])
        html = report.build(st, default_brief(), None)
        self.assertIn("Japanese line breaking", html)
        self.assertIn("Adobe Paragraph Composer", html)


class BriefTests(unittest.TestCase):
    def test_validation(self):
        with self.assertRaises(ValueError):
            Brief.from_dict({"rules": [{"id": "X1", "type": "free", "text": "a"}]})
        with self.assertRaises(ValueError):
            Brief.from_dict({"rules": [{"id": "B-01", "type": "term", "text": "a", "source": "x"}]})
        with self.assertRaises(ValueError):
            Brief.from_dict({"rules": [{"id": "B-01", "type": "free", "text": "a"}, {"id": "B-01", "type": "free", "text": "b"}]})
        b = Brief.from_dict(default_brief().to_dict())
        self.assertEqual(b.hash(), default_brief().hash())


if __name__ == "__main__":
    unittest.main()
