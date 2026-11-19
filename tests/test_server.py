"""Local web app: access control and one full flow with mock providers."""
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from idmlbrief import server  # noqa: E402
from idmlbrief.llm import MockProvider  # noqa: E402

FIX = ROOT / "fixtures" / "tx40_ja_base.idml"
REF = {k: v for k, v in json.load(open(ROOT / "fixtures/ja_reference.json", encoding="utf-8")).items() if not k.startswith("_")}
PORT = 48765


def mock_tr(system, user, schema):
    return {"translations": [{"id": s["id"], "text": REF[s["id"]]} for s in json.loads(user)["segments"]]}


def mock_rv(system, user, schema):
    return {"segments": [{"id": s["id"], "findings": []} for s in json.loads(user)["segments"]]}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bl-srv-"))
        self.A = server.App(self.tmp)
        self.A.port = PORT
        self.A.out_dir = self.tmp / "out"
        self.app = server.create_app(self.A)
        self.c = TestClient(self.app, base_url=f"http://127.0.0.1:{PORT}")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def login(self):
        r = self.c.get(f"/start?t={self.A.token}", follow_redirects=False)
        self.assertEqual(r.status_code, 303)

    def test_access_control(self):
        self.assertEqual(self.c.get("/api/state").status_code, 403)  # no cookie
        self.assertEqual(self.c.get("/start?t=wrong").status_code, 403)
        self.login()
        self.assertEqual(self.c.get("/api/state").status_code, 200)
        evil = TestClient(self.app, base_url=f"http://evil.example:{PORT}")
        evil.cookies.set("bl", self.A.token)
        self.assertEqual(evil.get("/api/state").status_code, 403)  # DNS rebinding: wrong Host
        r = self.c.post("/api/keys", json={"anthropic": "x"}, headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_keys_never_returned(self):
        self.login()
        r = self.c.post("/api/keys", json={"anthropic": "sk-ant-SECRET", "openai": "sk-SECRET"})
        self.assertEqual(r.json(), {"anthropic": True, "openai": True})
        self.assertNotIn("SECRET", self.c.get("/api/state").text)
        self.assertFalse(any("SECRET" in p.read_text(errors="ignore") for p in self.tmp.rglob("*") if p.is_file() and p.suffix in (".json", ".lock", ".html")))

    def test_flow(self):
        self.login()
        self.assertEqual(self.c.post("/api/translate").status_code, 400)  # no document
        r = self.c.post("/api/upload", content=FIX.read_bytes(), headers={"X-Filename": "tx40.idml"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.post("/api/translate").status_code, 400)  # no key
        orig = server.ClaudeProvider, server.OpenAIProvider
        server.ClaudeProvider = lambda key, model: MockProvider(mock_tr)
        server.OpenAIProvider = lambda key, model: MockProvider(mock_rv)
        try:
            self.c.post("/api/keys", json={"anthropic": "a", "openai": "b"})
            self.assertEqual(self.c.post("/api/translate").status_code, 200)
            self.wait()
            self.assertEqual(self.c.post("/api/review").status_code, 200)
            self.wait()
        finally:
            server.ClaudeProvider, server.OpenAIProvider = orig
        st = self.c.get("/api/state").json()
        self.assertEqual(st["run"]["summary"]["ai_review"], "current")
        self.assertEqual(self.A.state.edit_count, 0)  # translation is not a hand edit
        self.c.put("/api/brief", json=self.A.brief.to_dict())
        self.assertEqual(self.A.state.edit_count, 0)  # nor is a brief change
        # bad edit rejected, good edit makes review stale
        sid = "NFFD13CDE-tu4"
        self.assertEqual(self.c.put(f"/api/segment/{sid}", json={"text": "no tags"}).status_code, 400)
        self.assertEqual(self.c.put(f"/api/segment/{sid}", json={"text": REF[sid]}).status_code, 200)
        self.assertEqual(self.c.get("/api/state").json()["run"]["summary"]["ai_review"], "stale")
        ex = self.c.post("/api/export").json()
        self.assertEqual(ex["gate_problems"], [])
        self.assertTrue(Path(ex["idml"]).exists())
        rep = self.c.get("/api/report")
        self.assertIn("out of date", rep.text)
        self.assertIn("hand edits 1", rep.text)
        # opening another document drops the previous job (its message must not carry over)
        self.assertIsNotNone(self.c.get("/api/state").json()["job"])
        self.assertEqual(self.c.post("/api/upload", content=FIX.read_bytes(), headers={"X-Filename": "tx40.idml"}).status_code, 200)
        self.assertIsNone(self.c.get("/api/state").json()["job"])

    def test_refused_upload(self):
        self.login()
        r = self.c.post("/api/upload", content=b"not a zip", headers={"X-Filename": "x.idml"})
        self.assertEqual(r.status_code, 422)
        r = self.c.post("/api/upload", content=b"x", headers={"X-Filename": "x.indd"})
        self.assertEqual(r.status_code, 400)

    def test_sample(self):
        self.login()
        names = self.c.get("/api/samples").json()
        self.assertIn("tx40-handwritten-example", names)
        self.assertEqual(self.c.post("/api/samples/tx40-handwritten-example").status_code, 200)
        st = self.c.get("/api/state").json()
        self.assertIn("not output from an AI model", st["run"]["sample_label"])
        self.assertEqual(self.c.post("/api/export").status_code, 400)
        self.c.post("/api/keys", json={"anthropic": "a", "openai": "b"})
        self.assertEqual(self.c.post("/api/translate").status_code, 400)
        self.assertEqual(self.c.post("/api/review").status_code, 400)
        self.assertEqual(self.c.post("/api/samples/..%2F..%2Fserver").status_code, 404)

    def wait(self):
        for _ in range(200):
            j = self.c.get("/api/state").json()["job"]
            if j and not j["running"]:
                self.assertEqual(j["error"], "")
                return
            time.sleep(0.05)
        self.fail("job did not finish")


if __name__ == "__main__":
    unittest.main()
