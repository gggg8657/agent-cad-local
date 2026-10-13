"""로컬 LLM 클라이언트 — Ollama /api/chat 또는 OpenAI 호환(vLLM·llama.cpp·LM Studio). 외부 API 없음."""
import json
import os
import urllib.error
import urllib.request

LLM_API = os.environ.get("LLM_API", "ollama")
LLM_BASE = os.environ.get("LLM_BASE_URL", "http://localhost:8000/v1" if LLM_API == "openai" else "http://localhost:11434").rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "qwen3:8b")
LLM_KEY = os.environ.get("LLM_API_KEY", "")
NUM_CTX = int(os.environ.get("NUM_CTX", "32768"))


def _openai(system, messages, model, on_token, json_mode):
    body = {"model": model, "stream": True, "temperature": 0.1,
            "messages": [{"role": "system", "content": system}, *messages]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    hdr = {"Content-Type": "application/json", **({"Authorization": f"Bearer {LLM_KEY}"} if LLM_KEY else {})}
    buf = []
    try:
        req = urllib.request.Request(LLM_BASE + "/chat/completions", json.dumps(body).encode(), hdr)
        with urllib.request.urlopen(req, timeout=3600) as r:
            for line in r:
                line = line.decode().strip()
                if not line.startswith("data:") or line == "data: [DONE]":
                    continue
                tok = (json.loads(line[5:])["choices"][0].get("delta") or {}).get("content") or ""
                if tok:
                    buf.append(tok)
                    on_token and on_token(tok)
    except urllib.error.HTTPError as e:
        if json_mode and e.code == 400:  # response_format 미지원 서버
            return _openai(system, messages, model, on_token, False)
        raise RuntimeError(f"LLM HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    return "".join(buf)


VISION_MODEL = os.environ.get("VISION_MODEL") or MODEL


def _with_images(messages, images):
    """마지막 user 메시지에 이미지(bytes 목록) 첨부 — Ollama: images[b64], OpenAI: content parts."""
    if not images:
        return messages
    import base64
    b64 = [base64.b64encode(b).decode() for b in images]
    msgs = [dict(m) for m in messages]
    if LLM_API == "openai":
        msgs[-1]["content"] = [{"type": "text", "text": msgs[-1]["content"]}] + \
                              [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + x}} for x in b64]
    else:
        msgs[-1]["images"] = b64
    return msgs


def chat(system, messages, model=None, on_token=None, json_mode=False, images=None):
    model = model or MODEL
    messages = _with_images(messages, images)
    if LLM_API == "openai":
        return _openai(system, messages, model, on_token, json_mode)
    body = {"model": model, "stream": True, "think": False, "options": {"temperature": 0.1, "num_ctx": NUM_CTX},
            "messages": [{"role": "system", "content": system}, *messages]}
    if json_mode:
        body["format"] = "json"
    for attempt in (0, 1):
        try:
            req = urllib.request.Request(LLM_BASE + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
            buf = []
            with urllib.request.urlopen(req, timeout=3600) as r:
                for line in r:
                    if not line.strip():
                        continue
                    j = json.loads(line)
                    if "error" in j:
                        raise RuntimeError(j["error"])
                    tok = j.get("message", {}).get("content", "")
                    if tok:
                        buf.append(tok)
                        on_token and on_token(tok)
                    if j.get("done"):
                        break
            return "".join(buf)
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            if attempt == 0 and "think" in msg:
                body.pop("think")
                continue
            raise RuntimeError(f"Ollama HTTP {e.code}: {msg[:300]}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"LLM 서버 연결 실패: {e.reason}")


def models():
    if LLM_API == "openai":
        req = urllib.request.Request(LLM_BASE + "/models", headers={"Authorization": f"Bearer {LLM_KEY}"} if LLM_KEY else {})
        with urllib.request.urlopen(req, timeout=10) as r:
            return [m["id"] for m in json.load(r)["data"]]
    with urllib.request.urlopen(LLM_BASE + "/api/tags", timeout=10) as r:
        return [m["name"] for m in json.load(r)["models"]]
