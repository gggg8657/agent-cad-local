"""공통 유틸 — JSON 입출력, LLM 출력에서 JSON 뽑기, 파라미터 수식 안전 평가."""
import ast
import json
import math
import operator
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos}
_CMP = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Eq: operator.eq, ast.NotEq: operator.ne}
_FUNCS = {"sqrt": math.sqrt, "sin": lambda d: math.sin(math.radians(d)), "cos": lambda d: math.cos(math.radians(d)),
          "tan": lambda d: math.tan(math.radians(d)), "min": min, "max": max, "abs": abs, "round": round, "ceil": math.ceil, "floor": math.floor}


def safe_eval(expr, names):
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.Name):
            if n.id in names:
                return names[n.id]
            if n.id == "pi":
                return math.pi
            raise ValueError(f"알 수 없는 파라미터 {n.id!r}")
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
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
    return float(ev(ast.parse(str(expr).strip(), mode="eval")))


TEXT_KEYS = {"name", "id", "type", "op", "face", "edges", "material", "text", "kind", "unit", "plane", "pattern", "mode", "note",
             "label", "axis", "prefix", "space", "medium", "from", "to", "floor", "block", "layer", "tag", "value", "zone", "title", "shape", "near"}


def resolve_params(obj, params):
    """IR 안의 문자열 수식("W/2")을 숫자로. params 자체도 앞에서부터 순서대로 평가."""
    names = {}
    for k, v in (params or {}).items():
        names[k] = v if isinstance(v, (int, float)) else safe_eval(v, names)

    def walk(o, key=None):
        if isinstance(o, dict):
            return {k: walk(v, k) for k, v in o.items() if k != "params"} | ({"params": o["params"]} if "params" in o else {})
        if isinstance(o, list):
            return [walk(v, key) for v in o]
        if isinstance(o, str) and key not in TEXT_KEYS:
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
