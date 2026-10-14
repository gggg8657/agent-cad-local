"""공통 유틸 — JSON 입출력, LLM 출력에서 JSON 뽑기, 파라미터 수식 안전 평가."""
import ast
import json
import math
import operator
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 응답·보고서에 서버 내부 정보(절대 경로, 내부 주소)가 새지 않게 지운다
_ABS = re.compile(r"(?<![\w.:/])/(?:home|tmp|opt|usr|var|root|mnt|srv|data|etc|proc|run)/[^\s'\"`,;)\]}>]*")
_URL = re.compile(r"\b(?:https?|wss?)://[^\s'\"`,;)\]}>]+")
_HOST = re.compile(r"\b(?:localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)(?::\d+)?\b")


def scrub(x):
    """문자열(또는 그 안의 dict·list 문자열)에서 절대 경로·URL·내부 호스트 주소를 가린다."""
    if isinstance(x, str):
        x = _URL.sub("(내부 주소)", x)
        x = _ABS.sub(lambda m: "…/" + os.path.basename(m.group(0).rstrip("/")), x)
        return _HOST.sub("(내부 주소)", x)
    if isinstance(x, dict):
        return {k: scrub(v) for k, v in x.items()}
    if isinstance(x, list):
        return [scrub(v) for v in x]
    return x


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def write(p, s):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


def load_json(p):
    return json.loads(read(p))


def dump_json(p, obj):
    write(p, json.dumps(obj, ensure_ascii=False, indent=1))


def resource(*parts):
    return load_json(os.path.join(ROOT, *parts))


def extract_json(text):
    """LLM 출력에서 첫 JSON 객체를 꺼낸다 (think 블록·코드펜스·앞뒤 잡담 허용)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, flags=re.S)
    if m:
        text = m.group(1)
    start = text.find("{")
    if start < 0:
        raise ValueError("JSON 객체가 없음")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                frag = text[start:i + 1]
                try:
                    return json.loads(frag)
                except json.JSONDecodeError:
                    return json.loads(re.sub(r",\s*([}\]])", r"\1", frag))  # 끝 쉼표 허용
    raise ValueError("JSON 괄호가 닫히지 않음")


# ── 파라미터 수식 ("W/2-10", "sqrt(D)") 안전 평가 ─────────────────────────
# 크기 상한 — 공유 KB 검증식·IR 에 `9**9**9**9` 같은 식을 넣어 서버(GIL)를 멈추게 하지 못하게
MAX_EXPR_LEN = 1000      # 식 글자 수
MAX_CONST = 1e100        # 식에 쓴 숫자 상수의 절댓값
MAX_POW_EXP = 64         # 거듭제곱 지수의 절댓값
MAX_POW_BASE = 1e100     # 거듭제곱 밑의 절댓값
MAX_INT_BITS = 4096      # 중간 결과 정수 크기(비트)


def _pow(a, b):
    if abs(b) > MAX_POW_EXP:
        raise ValueError(f"거듭제곱 지수가 너무 큼(|지수| ≤ {MAX_POW_EXP})")
    if abs(a) > MAX_POW_BASE:
        raise ValueError("거듭제곱 밑이 너무 큼")
    return operator.pow(a, b)


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: _pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos}
_CMP = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Eq: operator.eq, ast.NotEq: operator.ne}
_FUNCS = {"sqrt": math.sqrt, "sin": lambda d: math.sin(math.radians(d)), "cos": lambda d: math.cos(math.radians(d)),
          "tan": lambda d: math.tan(math.radians(d)), "min": min, "max": max, "abs": abs, "round": round, "ceil": math.ceil, "floor": math.floor}


def safe_eval(expr, names):
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            if abs(n.value) > MAX_CONST:
                raise ValueError("수식의 숫자가 너무 큼")
            return n.value
        if isinstance(n, ast.Name):
            if n.id in names:
                return names[n.id]
            if n.id == "pi":
                return math.pi
            raise ValueError(f"알 수 없는 파라미터 {n.id!r}")
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            r = _OPS[type(n.op)](ev(n.left), ev(n.right))
            if isinstance(r, int) and r.bit_length() > MAX_INT_BITS:
                raise ValueError("수식 중간 결과가 너무 큼")
            return r
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS:
            return _FUNCS[n.func.id](*[ev(a) for a in n.args])
        if isinstance(n, ast.Compare):  # 검증식용: a < b <= c, ==, !=
            left = ev(n.left)
            for op, rc in zip(n.ops, n.comparators):
                right = ev(rc)
                if not _CMP[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(n, ast.BoolOp):
            vals = [ev(v) for v in n.values]
            return all(vals) if isinstance(n.op, ast.And) else any(vals)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            return not ev(n.operand)
        if isinstance(n, ast.IfExp):
            return ev(n.body) if ev(n.test) else ev(n.orelse)
        raise ValueError(f"허용되지 않는 수식: {ast.dump(n)[:60]}")
    src = str(expr).strip()
    if len(src) > MAX_EXPR_LEN:
        raise ValueError(f"수식이 너무 김(최대 {MAX_EXPR_LEN}자)")
    return float(ev(ast.parse(src, mode="eval")))


TEXT_KEYS = {"name", "id", "type", "op", "face", "edges", "material", "text", "kind", "unit", "plane", "pattern", "mode", "note",
             "label", "axis", "prefix", "space", "medium", "from", "to", "floor", "block", "layer", "tag", "value", "zone", "title", "shape", "near"}


def resolve_params(obj, params, text_keys=TEXT_KEYS):
    """IR 안의 문자열 수식("W/2")을 숫자로. params 자체도 앞에서부터 순서대로 평가.
    text_keys: 값을 글자 그대로 두는 키. 작업마다 다르다 (ICT 의 id 는 장비 ID, part3d tube 의 id 는 내경)."""
    names = {}
    for k, v in (params or {}).items():
        try:
            names[k] = v if isinstance(v, (int, float)) else safe_eval(v, names)
        except Exception as e:
            raise ValueError(f"params.{k} = {v!r} 계산 실패: {e}") from e

    def walk(o, key=None):
        if isinstance(o, dict):
            return {k: walk(v, k) for k, v in o.items() if k != "params"} | ({"params": o["params"]} if "params" in o else {})
        if isinstance(o, list):
            return [walk(v, key) for v in o]
        if isinstance(o, str) and key not in text_keys:
            try:
                return safe_eval(o, names)
            except Exception:
                return o
        return o
    return walk(obj), names


def num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

