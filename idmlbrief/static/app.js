"use strict";
// All data is inserted with textContent / value. No innerHTML with data.
const $ = (id) => document.getElementById(id);
function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null) e.append(k instanceof Node ? k : String(k));
  return e;
}
async function api(method, path, body, headers) {
  const opt = { method, headers: headers || {} };
  if (body instanceof Blob || body instanceof ArrayBuffer) opt.body = body;
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, opt);
  let data = null;
  try { data = await r.json(); } catch (_) { /* not json */ }
  if (!r.ok) {
    const msg = data && (data.detail || (data.refused ? "Refused" : null)) || r.statusText;
    const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    err.data = data;
    throw err;
  }
  return data;
}
function say(id, text, cls) { const e = $(id); e.textContent = text || ""; e.className = cls || ""; }

// ------------------------------------------------------------- tabs
for (const b of document.querySelectorAll("#nav button")) {
  b.addEventListener("click", () => {
    document.querySelectorAll("#nav button").forEach((x) => x.classList.toggle("on", x === b));
    document.querySelectorAll("section").forEach((s) => s.classList.toggle("on", s.id === b.dataset.s));
    refresh();
  });
}

// ------------------------------------------------------------- brief
const TYPES = ["term", "keep", "digits", "styled", "free"];
let brief = { name: "", rules: [] };
function ruleRow(r) {
  const inp = (k, size) => el("input", { value: r[k] || "", size: size || 14, oninput: (e) => { r[k] = e.target.value; } });
  const sel = (k, opts) => {
    const s = el("select", { onchange: (e) => { r[k] = e.target.value; } }, opts.map((o) => el("option", { value: o }, o)));
    s.value = r[k]; return s;
  };
  return el("tr", {}, el("td", {}, inp("id", 5)), el("td", {}, sel("type", TYPES)), el("td", {}, sel("level", ["required", "recommended"])),
    el("td", {}, inp("text", 40)), el("td", {}, inp("source")), el("td", {}, inp("target")), el("td", {}, inp("style", 10)),
    el("td", {}, el("button", { class: "b", onclick: () => { brief.rules = brief.rules.filter((x) => x !== r); drawBrief(); } }, "Remove")));
}
function drawBrief() {
  $("bname").value = brief.name;
  const tb = document.querySelector("#rules tbody");
  tb.replaceChildren(...brief.rules.map(ruleRow));
}
async function loadBrief() { brief = await api("GET", "/api/brief"); drawBrief(); }
$("bname").addEventListener("input", (e) => { brief.name = e.target.value; });
$("addrule").onclick = () => {
  const n = brief.rules.length + 1;
  brief.rules.push({ id: "B-" + String(n).padStart(2, "0"), type: "free", level: "required", text: "", source: "", target: "", style: "", scope: "all", lang: "ja" });
  drawBrief();
};
$("resetbrief").onclick = async () => { brief = await api("POST", "/api/brief/reset"); drawBrief(); say("briefmsg", "Template loaded (not saved yet).", "mut"); };
$("savebrief").onclick = async () => {
  try { await api("PUT", "/api/brief", brief); say("briefmsg", "Saved.", "ok"); refresh(); }
  catch (e) { say("briefmsg", e.message, "err"); }
};

// ------------------------------------------------------------- document
$("upload").onclick = async () => {
  const f = $("file").files[0];
  if (!f) return say("upmsg", "Choose a file.", "err");
  say("upmsg", "Reading…", "mut");
  try {
    await api("POST", "/api/upload", f, { "X-Filename": f.name });
    say("upmsg", "Opened.", "ok");
  } catch (e) {
    if (e.data && e.data.refused) {
      say("upmsg", "Not supported in v1:", "err");
      $("scope").replaceChildren(el("ul", {}, e.data.refused.map(([w, d]) => el("li", {}, w + ": " + d))));
      return;
    }
    say("upmsg", e.message, "err");
  }
  refresh();
};
async function loadSamples() {
  const list = await api("GET", "/api/samples");
  $("samples").replaceChildren(...list.map((n) => el("option", { value: n }, n)));
  $("opensample").disabled = !list.length;
}
$("opensample").onclick = async () => { await api("POST", "/api/samples/" + encodeURIComponent($("samples").value)); refresh(); };

// ------------------------------------------------------------- keys / run
$("savekeys").onclick = async () => {
  const body = {};
  if ($("kA").value) body.anthropic = $("kA").value;
  if ($("kB").value) body.openai = $("kB").value;
  const r = await api("POST", "/api/keys", body);
  $("kA").value = ""; $("kB").value = "";
  say("keymsg", `Anthropic: ${r.anthropic ? "set" : "not set"} / OpenAI: ${r.openai ? "set" : "not set"}`, "mut");
};
async function job(path) {
  try { await api("POST", path); say("jobmsg", "Started.", "mut"); } catch (e) { say("jobmsg", e.message, "err"); }
  refresh();
}
$("go-tr").onclick = () => job("/api/translate");
$("go-rv").onclick = () => job("/api/review");
$("cancel").onclick = () => job("/api/cancel");

// ------------------------------------------------------------- findings / edit
$("recheck").onclick = async () => { await api("POST", "/api/recheck"); refresh(); };
$("onlyf").onchange = () => drawSegs(last);
function drawSegs(S) {
  if (!S || !S.run) { $("segs").replaceChildren(el("p", { class: "mut" }, "No document.")); return; }
  const fby = {};
  for (const f of S.run.findings) (fby[f.seg_id] = fby[f.seg_id] || []).push(f);
  const only = $("onlyf").checked;
  const rows = [];
  for (const s of S.run.segs) {
    const fs = fby[s.id] || [];
    if (only && !fs.length) continue;
    const ta = el("textarea", { rows: 3 }); ta.value = s.target ?? "";
    const msg = el("span");
    const save = el("button", { class: "b", onclick: async () => {
      try { await api("PUT", "/api/segment/" + encodeURIComponent(s.id), { text: ta.value }); msg.textContent = "Saved. AI review is now out of date."; msg.className = "ok"; refresh(); }
      catch (e) { msg.textContent = e.message; msg.className = "err"; }
    } }, "Save");
    const codes = Object.entries(s.codes || {}).map(([k, v]) => `${k}: ${v.label || v.element || ""}`).join("  ");
    rows.push(el("div", { class: "box" },
      el("div", { class: "mut mono" }, `${s.id}  ${s.status}${s.edited ? " (edited)" : ""}  ${s.note || ""}`),
      el("div", { class: "mono" }, s.source),
      codes ? el("div", { class: "mut mono" }, codes) : null,
      ta, el("div", { class: "row" }, save, msg),
      fs.length ? el("ul", {}, fs.map((f) => el("li", { class: f.severity === "error" ? "err" : "warn" },
        `[${f.rule_id} / ${f.method}${f.status === "stale" ? " / out of date" : ""}] ${f.evidence}` +
        (f.suggestion ? `  Suggestion: ${f.suggestion}` : "")))) : null));
  }
  $("segs").replaceChildren(...(rows.length ? rows : [el("p", { class: "mut" }, "Nothing to show.")]));
}
function ruleTable(S) {
  const rows = S.run.summary.rules.map((r) => {
    const c = r.counts;
    return el("tr", {}, el("td", {}, r.rule_id), el("td", { class: c.violation ? "err" : "" }, c.violation), el("td", {}, c.clean),
      el("td", {}, c.not_applicable), el("td", {}, c.cannot_judge + c.unevaluated + " " + Object.entries(r.reasons).map(([k, v]) => `${k}: ${v}`).join("; ")));
  });
  return el("table", {}, el("tr", {}, ["Rule", "Violations", "Clean", "Not applicable", "Could not judge / not evaluated"].map((h) => el("th", {}, h))), rows);
}

// ------------------------------------------------------------- export
$("export").onclick = async () => {
  say("expmsg", "Exporting…", "mut");
  try {
    const r = await api("POST", "/api/export");
    say("expmsg", r.idml ? "Done." : "Stopped: the working IDML was not written.", r.idml ? "ok" : "err");
    drawExport(r);
  } catch (e) { say("expmsg", e.message, "err"); }
};
$("openfolder").onclick = () => api("POST", "/api/open-folder");
function drawExport(r) {
  $("replink").style.display = r && r.report ? "inline" : "none";
  if (!r) { $("expres").replaceChildren(); return; }
  $("expres").replaceChildren(el("ul", {},
    el("li", {}, "Working IDML: " + (r.idml || "not written")),
    el("li", {}, "Report: " + (r.report || "")),
    (r.gate_problems || []).map((p) => el("li", { class: "err" }, p))));
}

// plain-language hints for error codes coming from the model calls
function explain(reason) {
  if (/auth/.test(reason)) return reason + ". The API key was rejected. Re-enter it in 3 Translate & review (check it is in the right field).";
  if (/rate_limit/.test(reason)) return reason + ". The provider limited the requests. Wait a minute and run again.";
  if (/timeout|network/.test(reason)) return reason + ". No answer from the provider. Check the internet connection and run again.";
  return reason;
}

// ------------------------------------------------------------- refresh
let last = null;
let timer = null;
async function refresh() {
  const S = await api("GET", "/api/state");
  last = S;
  const run = S.run;
  const j = S.job;
  $("status").textContent = (run ? run.input + (run.sample_label ? " (example)" : "") : "No document") +
    (j && j.running ? " · " + j.kind + ": " + (j.progress || "…") : "");
  if (run && run.sample_label) say("jobmsg", "This is a saved example. Open your own IDML in 2 Document to translate.", "mut");
  else if (j && !j.running && j.error) say("jobmsg", j.error, "err");
  else if (j && !j.running) say("jobmsg", j.kind + " finished.", "ok");
  $("go-tr").disabled = $("go-rv").disabled = !run || !!run.sample_label || (j && j.running);
  $("cancel").disabled = !(j && j.running);
  $("export").disabled = !run || !!run.sample_label || (j && j.running);
  if (run) {
    const sc = run.scope || {};
    $("scope").replaceChildren(el("p", {}, `${run.segs.length} paragraphs in ${(sc.translated_stories || []).length} stories.`),
      (sc.excluded || []).length ? el("div", {}, el("b", {}, "Not translated by design:"), el("ul", {}, sc.excluded.map(([w, d]) => el("li", {}, w + ": " + d)))) : null);
    const p = run.preview;
    $("preview").replaceChildren(
      el("div", {}, `Translation: ${p.paragraphs} paragraphs, ${p.characters} characters, ${p.requests} request(s) to ${p.destinations.translate}, model ${S.models.translate}.`),
      el("div", {}, `Estimated: about ${p.est_input_tokens} input / ${p.est_output_tokens} output tokens` + (p.est_usd_translate != null ? `, about $${p.est_usd_translate}` : "") + " (rough)."),
      el("div", {}, `AI review: all translated paragraphs to ${p.destinations.review}, model ${S.models.review}. Cost: ${p.review_cost || "see report"}.`),
      el("div", { class: "mut" }, "Not sent: " + p.not_sent + "."));
    const st = run.summary.by_status;
    $("runsum").replaceChildren(el("p", {}, Object.entries(st).map(([k, v]) => `${k}: ${v}`).join(" · ")));
    const ai = { current: "AI review is up to date.", stale: "AI review is out of date.", not_run: "AI review has not run.", failed: "AI review failed.", running: "AI review is running." }[run.summary.ai_review];
    say("aistate", ai + " " + explain(run.summary.ai_review_reason || ""), run.summary.ai_review === "current" ? "ok" : "warn");
    $("rulesum").replaceChildren(ruleTable(S));
  }
  if (document.querySelector("section.on").id === "find") drawSegs(S);
  if (S.export && !$("expres").childNodes.length) drawExport(S.export);
  clearTimeout(timer);
  if (j && j.running) timer = setTimeout(refresh, 1000);
}
loadBrief(); loadSamples(); refresh();
