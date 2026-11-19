"""Local web app (F9). Binds 127.0.0.1 on a free port. One user, one run at a time.

Safety:
  * per-launch random token; the browser gets it once via /start?t=... and keeps
    it in an HttpOnly SameSite=Strict cookie; every /api call needs it
  * Host must be 127.0.0.1:<port> or localhost:<port>; Origin, when sent, must match
  * API keys stay in this process's memory only (never written, never returned)
  * single instance: a lock file in the work folder
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from . import checks, pipeline, review, tikal, translate
from .brief import Brief, default_brief
from .llm import DESTINATIONS, PRICES, REVIEW_MODEL, TRANSLATE_MODEL, ClaudeProvider, OpenAIProvider
from .xliff import CodeError, to_tagged

MAX_UPLOAD = 200 * 1024 * 1024
STATIC = Path(__file__).resolve().parent / "static"


def app_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "IDMLBriefTranslate"
    base.mkdir(parents=True, exist_ok=True)
    return base


class App:
    def __init__(self, root: Path | None = None):
        self.root = root or app_dir()
        self.token = secrets.token_urlsafe(32)
        self.port = 0
        self.keys = {"anthropic": "", "openai": ""}
        self.brief: Brief = default_brief()
        self.state = None
        self.work: Path | None = None
        self.export = None
        self.lock = threading.Lock()
        self.job = None  # {"kind", "thread", "cancel", "progress", "error"}
        self.out_dir = Path.home() / "Documents" / "IDMLBriefTranslate"
        self.models = {"translate": TRANSLATE_MODEL, "review": REVIEW_MODEL}

    # ----------------------------------------------------------- jobs
    def busy(self):
        return self.job is not None and self.job["thread"].is_alive()

    def start_job(self, kind, fn):
        if self.busy():
            raise HTTPException(409, "Another step is running.")
        job = {"kind": kind, "cancel": False, "progress": "", "error": ""}

        def run():
            try:
                fn(job)
            except Exception as e:  # shown to the user; no key material is in these messages
                job["error"] = f"{type(e).__name__}: {e}"[:500]
            finally:
                if self.state is not None and self.work is not None:
                    with self.lock:
                        pipeline.save(self.state, self.work)
        job["thread"] = threading.Thread(target=run, daemon=True)
        self.job = job
        job["thread"].start()


def estimate(state, brief) -> dict:
    """Rough send preview. Tokens ~ characters / 3.5 for English, JA output ~ 1.0 token/char."""
    segs = [s for s in state.segs if s.translatable and s.status in ("pending", "failed")]
    chars = sum(len(to_tagged(s.source)) for s in segs)
    batches = max(1, -(-len(segs) // translate.BATCH)) if segs else 0
    bchars = len(brief.for_prompt()) + len(translate.SYSTEM)
    tin = int((chars + batches * bchars) / 3.5)
    tout = int(chars * 0.9)
    p = PRICES.get(TRANSLATE_MODEL)
    usd = (tin / 1e6 * p[0] + tout / 1e6 * p[1]) if p else None
    return {"paragraphs": len(segs), "characters": chars, "requests": batches,
            "est_input_tokens": tin, "est_output_tokens": tout, "est_usd_translate": round(usd, 3) if usd else None,
            "review_cost": "unknown (price of the review model not recorded)" if PRICES.get(REVIEW_MODEL) is None else None,
            "destinations": DESTINATIONS,
            "not_sent": "the IDML file itself, images, file paths, your name; only paragraph text, inline tags and the brief"}


def create_app(A: App) -> FastAPI:
    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @api.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        if host not in (f"127.0.0.1:{A.port}", f"localhost:{A.port}"):
            return Response("bad host", 403)
        origin = request.headers.get("origin")
        if origin and origin not in (f"http://127.0.0.1:{A.port}", f"http://localhost:{A.port}"):
            return Response("bad origin", 403)
        p = request.url.path
        if p != "/start" and request.cookies.get("bl") != A.token:
            return Response("Open IDML Brief Translate from the app window.", 403)
        resp = await call_next(request)
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
        return resp

    @api.get("/start")
    def start(t: str = ""):
        if not secrets.compare_digest(t, A.token):
            return Response("bad token", 403)
        r = RedirectResponse("/", 303)
        r.set_cookie("bl", A.token, httponly=True, samesite="strict")
        return r

    @api.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @api.get("/app.js")
    def js():
        return FileResponse(STATIC / "app.js", media_type="text/javascript")

    # ----------------------------------------------------------- brief
    @api.get("/api/brief")
    def get_brief():
        return A.brief.to_dict()

    @api.put("/api/brief")
    async def put_brief(request: Request):
        if A.busy():
            raise HTTPException(409, "Wait for the running step to finish.")
        try:
            b = Brief.from_dict(await request.json())
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e))
        A.brief = b
        if A.state is not None:
            with A.lock:
                A.state.brief, A.state.brief_hash = b.to_dict(), b.hash()
                A.state.doc_version += 1
                review.mark_stale(A.state, "The brief changed after the AI review.")
                checks.run_machine(A.state, b)
        return {"ok": True, "hash": b.hash()}

    @api.post("/api/brief/reset")
    def reset_brief():
        A.brief = default_brief()
        return A.brief.to_dict()

    # ----------------------------------------------------------- keys
    @api.post("/api/keys")
    async def keys(request: Request):
        d = await request.json()
        for k in ("anthropic", "openai"):
            if k in d:
                A.keys[k] = str(d[k]).strip()
        return {k: bool(v) for k, v in A.keys.items()}

    # ----------------------------------------------------------- document
    @api.post("/api/upload")
    async def upload(request: Request):
        if A.busy():
            raise HTTPException(409, "Wait for the running step to finish.")
        name = Path(request.headers.get("x-filename", "input.idml")).name
        if not name.lower().endswith(".idml"):
            raise HTTPException(400, "Choose an .idml file (File > Save As > InDesign Markup in InDesign).")
        body = await request.body()
        if len(body) > MAX_UPLOAD:
            raise HTTPException(413, "File too large.")
        inbox = A.root / "inbox"
        inbox.mkdir(exist_ok=True)
        p = inbox / ("upload-" + secrets.token_hex(4) + ".idml")
        p.write_bytes(body)
        try:
            st, work = pipeline.open_run(p, A.brief, A.root / "runs", {"models": A.models})
        except pipeline.ScopeRefused as e:
            return JSONResponse({"ok": False, "refused": e.report.unsupported, "excluded": e.report.excluded}, 422)
        except tikal.TikalError as e:
            raise HTTPException(500, f"Could not read the IDML: {e}")
        finally:
            p.unlink(missing_ok=True)
        st.input_name = name
        A.state, A.work, A.export = st, work, None
        pipeline.save(st, work)
        return {"ok": True, "run_id": st.run_id}

    @api.get("/api/state")
    def state():
        job = None
        if A.job:
            job = {k: A.job[k] for k in ("kind", "progress", "error")} | {"running": A.busy()}
        out = {"keys": {k: bool(v) for k, v in A.keys.items()}, "models": A.models, "job": job,
               "brief_hash": A.brief.hash(), "out_dir": str(A.out_dir), "export": A.export}
        st = A.state
        if st is None:
            return out
        with A.lock:
            out["run"] = {
                "run_id": st.run_id, "input": st.input_name, "scope": st.scope, "doc_version": st.doc_version,
                "summary": pipeline.run_summary(st, A.brief), "preview": estimate(st, A.brief),
                "sample_label": st.sample_label,
                "segs": [{"id": s.id, "source": to_tagged(s.source), "target": to_tagged(s.target) if s.target is not None else None,
                          "status": s.status, "note": s.note, "edited": s.edited,
                          "codes": st.codemap.get(s.id, {})} for s in st.segs],
                "findings": [f.__dict__ for f in st.findings],
                "log": st.log[-50:],
            }
        return out

    def provider(kind):
        if A.work is None:
            raise HTTPException(400, "This is a saved example. Open your own IDML to translate or review.")
        if kind == "translate":
            if not A.keys["anthropic"]:
                raise HTTPException(400, "Enter the Anthropic API key first.")
            return ClaudeProvider(A.keys["anthropic"], A.models["translate"])
        if not A.keys["openai"]:
            raise HTTPException(400, "Enter the OpenAI API key first.")
        return OpenAIProvider(A.keys["openai"], A.models["review"])

    def need_run():
        if A.state is None:
            raise HTTPException(400, "Open an IDML first.")

    @api.post("/api/translate")
    def do_translate():
        need_run()
        p = provider("translate")

        def fn(job):
            translate.translate(A.state, A.brief, p, cancel=lambda: job["cancel"],
                                progress=lambda m: job.__setitem__("progress", m))
            with A.lock:
                A.state.doc_version += 1
                review.mark_stale(A.state, "The translation changed after the AI review.")
                checks.run_machine(A.state, A.brief)
        A.start_job("translate", fn)
        return {"ok": True}

    @api.post("/api/review")
    def do_review():
        need_run()
        p = provider("review")

        def fn(job):
            review.review(A.state, A.brief, p, cancel=lambda: job["cancel"],
                          progress=lambda m: job.__setitem__("progress", m))
        A.start_job("review", fn)
        return {"ok": True}

    @api.post("/api/cancel")
    def cancel():
        if A.job:
            A.job["cancel"] = True
        return {"ok": True}

    @api.put("/api/segment/{sid}")
    async def edit(sid: str, request: Request):
        need_run()
        if A.busy():
            raise HTTPException(409, "Wait for the running step to finish.")
        d = await request.json()
        try:
            with A.lock:
                pipeline.edit_segment(A.state, A.brief, sid, str(d.get("text", "")))
                if A.work is not None:
                    pipeline.save(A.state, A.work)
        except StopIteration:
            raise HTTPException(404, "No such paragraph.")
        except CodeError as e:
            raise HTTPException(400, f"Tags do not match the source: {e}")
        return {"ok": True}

    @api.post("/api/recheck")
    def recheck():
        need_run()
        with A.lock:
            checks.run_machine(A.state, A.brief)
        return {"ok": True}

    @api.post("/api/export")
    def export():
        need_run()
        if A.work is None:
            raise HTTPException(400, "Examples cannot be exported. Open your own IDML.")
        if A.busy():
            raise HTTPException(409, "Wait for the running step to finish.")
        with A.lock:
            A.export = pipeline.export_with_report(A.state, A.brief, A.work, A.out_dir)
        return A.export

    @api.get("/api/report")
    def report_html():
        if not A.export or not A.export.get("report"):
            raise HTTPException(404, "Export first.")
        return HTMLResponse(Path(A.export["report"]).read_text(encoding="utf-8"),
                            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

    @api.post("/api/open-folder")
    def open_folder():
        A.out_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(A.out_dir)  # noqa: S606 - local folder chosen by the app
        return {"ok": True}

    @api.get("/api/samples")
    def samples():
        d = Path(__file__).resolve().parent / "samples"
        return [p.stem for p in sorted(d.glob("*.json"))] if d.exists() else []

    @api.post("/api/samples/{name}")
    def open_sample(name: str):
        d = Path(__file__).resolve().parent / "samples"
        p = d / (Path(name).name + ".json")
        if not p.exists():
            raise HTTPException(404, "No such sample.")
        from .model import RunState
        data = json.loads(p.read_text(encoding="utf-8"))
        A.state = RunState.from_dict(data["state"])
        A.brief = Brief.from_dict(A.state.brief)
        A.work, A.export = None, data.get("export")
        return {"ok": True}

    @api.post("/api/shutdown")
    def shutdown():
        threading.Timer(0.3, lambda: os._exit(0)).start()
        tikal.kill_running()
        return {"ok": True}

    return api


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main(open_browser=True):
    A = App()
    lockf = A.root / "idmlbrief.lock"
    try:
        import msvcrt
        fh = open(lockf, "a+")
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except (ImportError, OSError):
        print("IDML Brief Translate is already running. Use the window that is open.", file=sys.stderr)
        return 1
    A.port = _free_port()
    url = f"http://127.0.0.1:{A.port}/start?t={A.token}"
    if open_browser and not os.environ.get("IDMLBRIEF_NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"IDML Brief Translate is running. If no browser opened, open:\n{url}\nClose this window to quit.", flush=True)
    uvicorn.run(create_app(A), host="127.0.0.1", port=A.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
