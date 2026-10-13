#!/usr/bin/env python3
"""agent-cad-local — 폐쇄망 로컬 CAD Agent. 자연어 → Project IR(LLM) → 결정론 CAD 엔진 → STEP/DXF/SVG/GLB + 검증.

  bash setup.sh                                        # venv + selftest + http://localhost:8778
  venv/bin/python app.py                               # 수동 실행
  venv/bin/python app.py --cli "100x60x10 블록에 M5 4개" [part3d]   # 프로젝트 생성, 결과 경로 출력
  LLM_API=openai LLM_BASE_URL=http://gpu:8000/v1 LLM_MODEL=Qwen3-32B venv/bin/python app.py
"""
import json
import mimetypes
import os
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cad import llm, pipeline
from cad.util import ROOT, read, resource, scrub

PORT = int(os.environ.get("PORT", "8778"))
WS = pipeline.WS
STATIC = {"three.min.js", "GLTFLoader.js", "OrbitControls.js"}  # fonts/ 는 서버 내부(래스터)용
CTYPE = {".svg": "image/svg+xml", ".dxf": "application/dxf", ".step": "application/step", ".stl": "model/stl", ".glb": "model/gltf-binary",
         ".csv": "text/csv; charset=utf-8", ".md": "text/markdown; charset=utf-8", ".json": "application/json; charset=utf-8",
         ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def agent_user(h):
    """포털이 넘긴 사용자(로그인 확인됨). 관리자·서버 안 접속·헤더 없음(단독 실행) = owner"""
    return (h.headers.get("X-Agent-User") or "owner").strip() or "owner"


def user_ws(h):
    """포털이 넘긴 사용자별 작업 폴더. owner(서버 주인)·헤더 없음(단독 실행) = 예전 WORKSPACE 그대로.
    프로젝트·업로드는 이 폴더 안에서만 찾으므로 다른 사용자의 것은 id 를 알아도 열거나 숨길 수 없다."""
    u = agent_user(h)
    if u == "owner":
        return WS
    d = os.path.join(WS, "u", re.sub(r"[^A-Za-z0-9_.-]", "_", u)[:64] or "_")
    os.makedirs(d, exist_ok=True)
    return d


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        if a and isinstance(a[0], str) and ("/api/run" in a[0] or "/api/rebuild" in a[0]):
            super().log_message(fmt, *a)

    def _send(self, body, ctype="application/json; charset=utf-8", code=200, extra=None):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        pipeline.use_ws(user_ws(self))  # 프로젝트·업로드는 사용자별 (같은 연결의 다음 요청도 여기서 다시 정함)
        u = urllib.parse.urlparse(self.path)
        path = u.path
        try:
            if path in ("/", "/index.html"):
                html = read(os.path.join(ROOT, "ui.html")).replace("%MODEL%", json.dumps(llm.MODEL))
                return self._send(html.encode(), "text/html; charset=utf-8")
            if path.startswith("/static/") and path[8:] in STATIC:
                with open(os.path.join(ROOT, "static", path[8:]), "rb") as f:
                    return self._send(f.read(), "application/javascript", extra={"Cache-Control": "max-age=86400"})
            if path == "/api/models":
                try:
                    return self._send({"models": llm.models(), "default": llm.MODEL, "api": llm.LLM_API})
                except Exception as e:
                    return self._send({"models": [llm.MODEL], "default": llm.MODEL, "error": scrub(f"LLM 서버 응답 없음 ({type(e).__name__})")})
            if path == "/api/projects":
                q = urllib.parse.parse_qs(u.query)
                return self._send(pipeline.list_projects(owner=(q.get("owner") or [None])[0], show_all="all" in q))
            if path == "/api/kb":
                from cad import kb
                return self._send([{"id": a["id"], "name": a["name"], "kind": a.get("kind"), "summary": a.get("summary", ""), "edited": a.get("edited", False),
                                    "user_added": a.get("user_added", False), "n_principles": len(a.get("principles", []))} for a in kb.load_all().values()])
            m = re.fullmatch(r"/api/kb/([a-z0-9_]+)", path)
            if m:
                from cad import kb
                return self._send(kb.public(kb.get(m.group(1)), agent_user(self)))
            if path == "/api/gt":
                return self._send(pipeline.list_gt())
            m = re.fullmatch(r"/api/gt/([\w\-]+)\.(png|jpg|json)", path)
            if m:
                g = pipeline.load_gt(m.group(1))
                fp = os.path.join(pipeline.GT_DIR, g["image"] if m.group(2) != "json" else m.group(1) + ".json")
                with open(fp, "rb") as f:
                    return self._send(f.read(), "image/png" if m.group(2) != "json" else "application/json; charset=utf-8")
            if path == "/api/library":
                return self._send({"symbols": resource("library", "ict.json")["symbols"], "rules": resource("rules", "ict.json"),
                                   "part_rules": resource("rules", "part.json")})
            m = re.fullmatch(r"/api/projects/([0-9]{8}-[0-9a-f]{6})", path)
            if m:
                p = pipeline.load(m.group(1))
                for r in p.get("revisions") or []:
                    if r.get("error"):
                        r["error"] = scrub(r["error"])
                return self._send(p)
            m = re.fullmatch(r"/api/projects/([0-9]{8}-[0-9a-f]{6})/(R\d{2}|input)/([^/]+)", path)
            if m:
                name = urllib.parse.unquote(m.group(3))
                if "/" in name or name.startswith("."):
                    raise FileNotFoundError(name)
                fp = os.path.join(pipeline.pdir(m.group(1)), m.group(2), name)
                with open(fp, "rb") as f:
                    data = f.read()
                ext = os.path.splitext(name)[1].lower()
                q = urllib.parse.parse_qs(u.query)
                hdr = {"Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(name)} if "dl" in q else {}
                return self._send(data, CTYPE.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream", extra=hdr)
            self._send({"error": "없음"}, code=404)
        except (FileNotFoundError, KeyError):
            self._send({"error": "없음"}, code=404)
        except Exception:
            import traceback
            traceback.print_exc()
            self._send({"error": "서버 내부 오류"}, code=500)

    def _sse(self, fn):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def emit(ev):
            for k in ("error", "log", "msg"):
                if isinstance(ev.get(k), str):
                    ev = {**ev, k: scrub(ev[k])}
            try:
                self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        try:
            fn(emit)
        except Exception as e:
            import traceback
            traceback.print_exc()
            emit({"error": scrub(f"{type(e).__name__}: {e}")})

    def do_POST(self):
        pipeline.use_ws(user_ws(self))
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        try:
            if self.path == "/api/upload":
                name = urllib.parse.unquote(self.headers.get("X-Filename") or "upload.dxf")
                return self._send({"upload": pipeline.save_upload(name, body), "name": name})
            try:
                req = json.loads(body or b"{}")
            except ValueError:
                return self._send({"error": "JSON 본문이 아님"}, code=400)
            if not isinstance(req, dict):
                return self._send({"error": "JSON 객체여야 함"}, code=400)
            if self.path == "/api/run":
                text = (req.get("prompt") or "").strip()
                if not text:
                    return self._send({"error": "빈 요청"}, code=400)
                return self._sse(lambda emit: pipeline.run(text, req.get("task") or "auto", req.get("project"), req.get("upload"),
                                                           req.get("model"), emit, req.get("gt") or None, req.get("owner")))
            m = re.fullmatch(r"/api/kb/([a-z0-9_]+)(/reset)?", self.path)
            if m:
                from cad import kb
                user = agent_user(self)
                if m.group(2):
                    a = kb.reset_override(m.group(1), req.get("author", ""), user=user)
                    return self._send(kb.public(a, user) if a else {"ok": True})
                return self._send(kb.public(kb.save_override(m.group(1), req.get("patch") or {}, req.get("author", ""), req.get("note", ""), user=user), user))
            m = re.fullmatch(r"/api/projects/([0-9]{8}-[0-9a-f]{6})/hide", self.path)
            if m:
                return self._send(pipeline.set_hidden(m.group(1), req.get("hidden", True)))
            if self.path == "/api/rebuild":
                return self._sse(lambda emit: pipeline.rebuild(req["project"], req["ir"], emit))
            self._send({"error": "없음"}, code=404)
        except PermissionError as e:  # 공유 지식 DB: 만든 사람이나 관리자만
            self._send({"error": str(e)}, code=403)
        except (FileNotFoundError, KeyError):
            self._send({"error": "없음"}, code=404)
        except (ValueError, TypeError) as e:  # 입력 오류
            self._send({"error": scrub(f"{type(e).__name__}: {e}")}, code=400)
        except Exception:
            import traceback
            traceback.print_exc()
            self._send({"error": "서버 내부 오류"}, code=500)


def cli(text, task="auto"):
    def emit(ev):
        if "token" in ev:
            return
        print(json.dumps(ev, ensure_ascii=False), file=sys.stderr)
    p = pipeline.run(text, task, emit=emit)
    r = p["revisions"][-1]
    print(os.path.join(pipeline.pdir(p["id"]), r["rev"]))
    return 0 if r["status"] in ("pass", "warnings") else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        sys.exit(cli(sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read(), sys.argv[3] if len(sys.argv) > 3 else "auto"))
    print(f"agent-cad-local → http://localhost:{PORT}  (model={llm.MODEL}, llm={llm.LLM_API} {llm.LLM_BASE}, data={pipeline.WS})")
    ThreadingHTTPServer((os.environ.get("BIND", ""), PORT), H).serve_forever()
