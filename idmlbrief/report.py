"""HTML report (F7). Every value goes through html.escape.

Never says "verified" or "ready to deliver". Three separate sections:
  1. Language and brief  2. Codes and structure  3. InDesign layout = not checked
"""
from __future__ import annotations

from html import escape
from pathlib import Path

from . import checks, jastyle
from .brief import Brief
from .llm import DESTINATIONS, estimate_cost
from .model import APP_CHECKS, RunState
from .xliff import to_tagged

CSS = """
body{font:14px/1.5 system-ui,"Segoe UI","Yu Gothic UI",sans-serif;margin:24px;color:#1d1d1f;background:#fff;max-width:1100px}
h1{font-size:20px}h2{font-size:16px;margin-top:28px;border-bottom:1px solid #ddd;padding-bottom:4px}
table{border-collapse:collapse;width:100%;margin:8px 0}td,th{border:1px solid #ddd;padding:4px 6px;text-align:left;vertical-align:top}
th{background:#f4f4f6}.err{color:#b00020}.warn{color:#8a5a00}.muted{color:#666}.box{background:#f7f7f9;padding:8px 12px;border-radius:6px}
code{font:12px Consolas,monospace;background:#f1f1f3;padding:0 3px}
"""


def _e(x) -> str:
    return escape(str(x if x is not None else ""), quote=True)


def build(state: RunState, brief: Brief, export: dict | None) -> str:
    rows = checks.summarize(state, brief)
    out = [f"<!doctype html><html lang='en'><meta charset='utf-8'><title>IDML Brief Translate report</title><style>{CSS}</style><body>"]
    out.append("<h1>IDML Brief Translate report</h1>")
    if state.sample_label:
        out.append(f"<p class='box warn'><b>{_e(state.sample_label)}</b></p>")
    out.append("<p class='box'>This report lists what the checks found. It does not say the translation is correct, "
               "and it does not check the layout in InDesign. Open the working IDML in InDesign and check it before delivery.</p>")
    ex = export or {}
    meta = [
        ("Input", f"{state.input_name} (sha256 {state.input_sha256[:16]}…)"),
        ("Brief", f"{brief.name} (hash {state.brief_hash[:16]}…)"),
        ("Translation version", f"{state.targets_version()} / hand edits {state.edit_count}"),
        ("Working IDML", f"{Path(ex['idml']).name if ex.get('idml') else 'not written'}" + (f" (sha256 {ex['idml_sha256'][:16]}…)" if ex.get("idml_sha256") else "")),
        ("Tools", f"IDML Brief Translate {ex.get('app_version', '')}, Okapi Tikal {ex.get('tikal', '')}"),
        ("Sent to", "; ".join(f"{k}: {v}" for k, v in DESTINATIONS.items())),
    ]
    cost = estimate_cost(state.usage)
    models = sorted({f"{u.stage}: {u.model}" for u in state.usage})
    meta.append(("Models", ", ".join(models) or "none"))
    meta.append(("Cost (reported tokens only)", f"${cost['usd_known']:.4f}" + (f" + {cost['calls_unknown']} call(s) with unknown cost" if cost["calls_unknown"] else "")))
    out.append("<table>" + "".join(f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>" for k, v in meta) + "</table>")

    # 1. language and brief
    out.append("<h2>1. Language and brief</h2>")
    ai = {"current": "done for this version", "stale": "out of date (the translation changed after it ran)",
          "not_run": "not run", "failed": "failed", "running": "running"}.get(state.ai_review, state.ai_review)
    out.append(f"<p>AI review: <b>{_e(ai)}</b> {_e(state.ai_review_reason)}</p>")
    out.append("<table><tr><th>Rule</th><th>What it says</th><th>Checked by</th><th>Violations</th><th>Clean</th>"
               "<th>Not applicable</th><th>Could not judge / not evaluated</th></tr>")
    for r in rows:
        rule = brief.rule(r["rule_id"])
        text = rule.text if rule else APP_CHECKS[r["rule_id"]][1]
        by = ("machine" if rule and rule.machine else "AI review")
        c = r["counts"]
        why = "; ".join(f"{k}: {v}" for k, v in r["reasons"].items())
        cls = " class='err'" if c["violation"] else ""
        out.append(f"<tr><td>{_e(r['rule_id'])}</td><td>{_e(text)}</td><td>{by}</td><td{cls}>{c['violation']}</td>"
                   f"<td>{c['clean']}</td><td>{c['not_applicable']}</td><td>{c['cannot_judge'] + c['unevaluated']} {_e(why)}</td></tr>")
    out.append("</table>")
    out.append("<p class='muted'>An AI review result is a reading by a second model, not proof. A clean count means the "
               "check found nothing, not that nothing is wrong.</p>")

    out.append("<h3>Findings</h3>")
    fs = [f for f in state.findings]
    if not fs:
        out.append("<p>None.</p>")
    else:
        out.append("<table><tr><th>Paragraph</th><th>Rule</th><th>By</th><th>Severity</th><th>Evidence</th><th>Status</th></tr>")
        for f in fs:
            cls = "err" if f.severity == "error" else "warn"
            q = ""
            if f.src_quote or f.tgt_quote:
                q = f"<br><span class='muted'>source: {_e(f.src_quote)} / translation: {_e(f.tgt_quote)}</span>"
            sug = f"<br>Suggestion: {_e(f.suggestion)}" if f.suggestion else ""
            out.append(f"<tr><td>{_e(f.seg_id)}</td><td>{_e(f.rule_id)}</td><td>{_e(f.method)}</td><td class='{cls}'>{_e(f.severity)}</td>"
                       f"<td>{_e(f.evidence)}{q}{sug}</td><td>{_e(f.status)}</td></tr>")
        out.append("</table>")

    # 2. codes and structure
    out.append("<h2>2. Codes and structure</h2>")
    gp = ex.get("gate_problems")
    if gp is None:
        out.append("<p>Not exported yet.</p>")
    elif gp:
        out.append("<p class='err'><b>The working IDML was not written.</b> The structure check found:</p><ul>"
                   + "".join(f"<li>{_e(p)}</li>" for p in gp) + "</ul>")
    else:
        out.append("<p>Structure check: footnotes, cross-references, index markers, anchored objects, tables and "
                   "character formatting are the same as in the input (order inside a paragraph may change).</p>")
    failed = [s for s in state.segs if s.status in ("failed", "pending")]
    if failed:
        out.append(f"<p class='err'>{len(failed)} paragraph(s) are still in English in the working IDML:</p><ul>"
                   + "".join(f"<li>{_e(s.id)}: {_e(s.note)}</li>" for s in failed) + "</ul>")
    sc = state.scope or {}
    if sc.get("excluded"):
        out.append("<p>Not translated by design:</p><ul>" + "".join(f"<li>{_e(a)}: {_e(b)}</li>" for a, b in sc["excluded"]) + "</ul>")

    # 3. layout
    out.append("<h2>3. InDesign layout</h2><p><b>Not checked.</b> Overset text, line breaks, fonts and page flow are only "
               "visible in InDesign. Update cross-references and the index there.</p>")
    jl = (state.scope or {}).get("ja_line_breaking")
    out.append("<h3>Japanese line breaking (read from the styles, nothing changed)</h3>")
    if jl is None:
        out.append("<p class='muted'>Not read for this run.</p>")
    elif not jl:
        out.append("<p>Every paragraph style that holds Japanese text uses a Japanese composer, a kinsoku set and a mojikumi set.</p>")
    else:
        out.append(f"<p class='warn'>{_e(jastyle.summary_line(jl))}</p>")
        out.append("<table><tr><th>Paragraph style</th><th>Composer</th><th>Kinsoku set</th><th>Mojikumi set</th><th>Paragraphs</th></tr>"
                   + "".join(f"<tr><td>{_e(d['style'])}</td><td>{_e(d['composer'])}</td><td>{_e(d['kinsoku'])}</td><td>{_e(d.get('mojikumi', ''))}</td>"
                             f"<td>{d['paragraphs']}</td></tr>" for d in jl) + "</table>")

    # paragraphs
    out.append("<h2>Paragraphs</h2><table><tr><th>ID</th><th>Source</th><th>Translation</th><th>Status</th></tr>")
    for s in state.segs:
        out.append(f"<tr><td>{_e(s.id)}</td><td>{_e(to_tagged(s.source))}</td><td>{_e(to_tagged(s.target) if s.target is not None else '')}</td>"
                   f"<td>{_e(s.status)}{' (edited)' if s.edited else ''}</td></tr>")
    out.append("</table></body></html>")
    return "\n".join(out)
