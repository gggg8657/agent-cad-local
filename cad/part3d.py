"""PART_3D — Feature IR 정규화 + 빌드 워커 실행. OCC(build123d)는 worker3d 서브프로세스에서만 import (타임아웃·메모리 제한, 서버 보호).

좌표 규약: 모든 좌표는 절대좌표(mm). box/cylinder base 는 XY 중심·Z=0 바닥. 면(face) 피처의 (u,v)는 그 면 축의 절대값.
  top/bottom: u=X, v=Y   front/back: u=X, v=Z   left/right: u=Y, v=Z   (corners 패턴만 면 가장자리 기준 inset)
"""
import ast
import json
import math
import os
import subprocess
import sys
import threading

from . import util
from .part2d import CLEARANCE, density
from .util import ROOT, TEXT_KEYS, resolve_params

_CTX = threading.local()  # normalize 중인 IR 의 params·오류 목록·현재 위치 (숫자 필드 엄격 해석용)


def num(v, default=None):
    """숫자 필드 해석. 숫자·숫자 문자열·params 수식은 값으로. 정의 안 된 이름·계산 실패·inf/nan 은 조용히 0 으로 바꾸지 않고
    오류로 남긴다 (수리 루프가 원인을 알도록)."""
    if v is None or v == "":
        return default
    ctx = getattr(_CTX, "c", None)

    def bad(msg):
        if ctx is not None and msg not in ctx["errs"]:
            ctx["errs"].append(msg)
        return default
    where = (ctx or {}).get("w", "")
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else bad(f"{where}: 값 {v} 이 유한한 숫자가 아님")
    if isinstance(v, str):
        x = util.num(v)
        if x is None:
            try:
                x = util.safe_eval(v, (ctx or {}).get("params") or {})
            except Exception as e:
                try:
                    names = sorted({n.id for n in ast.walk(ast.parse(v.strip(), mode="eval")) if isinstance(n, ast.Name)}
                                   - set((ctx or {}).get("params") or {}) - {"pi"} - set(util._FUNCS))
                except SyntaxError:
                    names = []
                if names:
                    return bad(f"{where}: 치수 이름 {', '.join(repr(n) for n in names)} 이 정의되지 않음 — params 에 추가하거나 숫자로")
                return bad(f"{where}: 수식 {v!r} 계산 실패 ({type(e).__name__}: {e})")
        return float(x) if math.isfinite(x) else bad(f"{where}: {v!r} 이 유한한 숫자가 아님")
    return bad(f"{where}: 숫자 자리에 {type(v).__name__} {str(v)[:40]}")

TAP_DRILL = {"M2": 1.6, "M2.5": 2.05, "M3": 2.5, "M4": 3.3, "M5": 4.2, "M6": 5.0, "M8": 6.8, "M10": 8.5, "M12": 10.2, "M16": 14.0, "M20": 17.5}
FACES = ("top", "bottom", "front", "back", "left", "right")
FACE_ALIAS = {"+z": "top", "-z": "bottom", "-y": "front", "+y": "back", "-x": "left", "+x": "right", "위": "top", "아래": "bottom",
              "앞": "front", "뒤": "back", "왼쪽": "left", "오른쪽": "right", ">z": "top", "<z": "bottom", "<y": "front", ">y": "back",
              "<x": "left", ">x": "right"}
BASES = ("box", "cylinder", "tube", "extrude", "revolve")
AXES = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}
TEXT_KEYS_3D = TEXT_KEYS - {"id"}  # part3d 에서 id 는 tube 내경(숫자) — ICT 장비 ID 와 다르다
OPS = ("hole", "pocket", "boss", "add", "cut", "shell", "fillet", "chamfer")
EDGESETS = ("vertical", "top", "bottom", "all", "x", "y")


def _face(f, errs, where):
    f = FACE_ALIAS.get(str(f or "top").lower(), str(f or "top").lower())
    if f not in FACES:
        errs.append(f"{where}: face {f!r} 미지원 ({'|'.join(FACES)})")
    return f


def _vec(v, n=3, default=0.0):
    v = list(v or [])
    return [num(v[i], default) if i < len(v) else default for i in range(n)]


def normalize(ir):
    errs = []
    try:
        _CTX.c = {"errs": errs, "params": {}, "w": "base"}
        return _normalize(ir, errs)
    finally:
        _CTX.c = None


def _profile(raw, errs, w):
    """2D 프로파일 점 목록 [[x,y] | [x,y,r], ...] → ([[x,y]...], [r...]). r = 그 꼭짓점 라운드 반경(0 = 각진 모서리)."""
    pts, rs = [], []
    for k, p in enumerate(raw or []):
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            errs.append(f"{w}: points[{k}] 는 [x, y] 또는 [x, y, r] 이어야 함 (지금 {p!r})")
            continue
        pts.append([num(p[0], 0), num(p[1], 0)])
        rs.append(num(p[2], 0) if len(p) > 2 else 0.0)
    if len(pts) > 3 and pts[0] == pts[-1]:  # 닫는 점 중복 제거
        pts, rs = pts[:-1], rs[:-1]
    return pts, rs


def _normalize(ir, errs):
    ir = dict(ir or {})
    try:
        ir, params = resolve_params(ir, ir.get("params"), TEXT_KEYS_3D)
    except Exception as e:
        errs.append(f"{e} — params 는 앞에서 정의한 이름·숫자만 참조할 수 있음")
        return ir, errs
    _CTX.c["params"] = params
    ir["kind"] = "part3d"
    ir.setdefault("name", "part")
    b = dict(ir.get("base") or {})
    t = (b.get("type") or "box").lower()
    b["type"] = t
    if t not in BASES:
        errs.append(f"base.type {t!r} 미지원 ({'|'.join(BASES)})")
    if t == "box":
        b["size"] = _vec(b.get("size") or [b.get("x"), b.get("y"), b.get("z")])
        if min(b["size"]) <= 0:
            errs.append("base box size [x,y,z] 는 모두 양수")
    elif t in ("cylinder", "tube"):
        b["d"] = num(b.get("d") or b.get("od") or (num(b.get("r"), 0) * 2 if b.get("r") else None))
        b["h"] = num(b.get("h") or b.get("height"))
        if t == "tube":
            raw = next((b[k] for k in ("id", "inner_d", "ID", "inner_diameter") if b.get(k) not in (None, "")), None)
            b["id"] = num(raw)
            if raw is None:
                errs.append("tube 에 id(내경) 필요")
            elif b["id"] is not None and not 0 < b["id"] < (b["d"] or 0):
                errs.append(f"tube 는 0 < id < d (지금 id={b['id']:g}, d={b['d'] or 0:g})")
        if not b["d"] or not b["h"]:
            errs.append(f"base {t} 에 d, h 필요")
    elif t in ("extrude", "revolve"):
        b["points"], b["radii"] = _profile(b.get("points"), errs, f"base {t}")
        if len(b["points"]) < 3:
            errs.append(f"base {t} 에 3개 이상 points(2D 프로파일) 필요")
        if b.get("r"):  # 모든 꼭짓점 같은 라운드
            b["radii"] = [r or num(b["r"], 0) for r in b["radii"]]
        holes = []
        for k, h in enumerate(b.get("holes") or []):  # 내부 루프: 점 목록 또는 {"type":"circle","at":[x,y],"d":D}
            if isinstance(h, dict):
                if (h.get("type") or "circle") != "circle":
                    errs.append(f"base {t} holes[{k}]: dict 는 circle 만 ({{'type':'circle','at':[x,y],'d':D}})")
                    continue
                holes.append({"type": "circle", "at": _vec(h.get("at"), 2), "d": num(h.get("d"), 0)})
                if not holes[-1]["d"]:
                    errs.append(f"base {t} holes[{k}]: d 필요")
            else:
                hp, hr = _profile(h, errs, f"base {t} holes[{k}]")
                if len(hp) < 3:
                    errs.append(f"base {t} holes[{k}]: 3개 이상 점 필요")
                holes.append({"type": "poly", "points": hp, "radii": hr})
        b["holes"] = holes
        b["wall"] = num(b.get("wall"))  # 외곽을 안쪽으로 wall 만큼 띄운 속 빈 단면 (프레임·각관·삼각 랙)
        if b["wall"] is not None and b["wall"] <= 0:
            errs.append(f"base {t}: wall 은 양수")
        b["plane"] = str(b.get("plane") or ("XZ" if t == "revolve" else "XY")).upper()
        if b["plane"] not in ("XY", "XZ", "YZ"):
            errs.append("plane 은 XY|XZ|YZ")
        if t == "extrude":
            b["h"] = num(b.get("h") or b.get("depth"))
            if not b["h"]:
                errs.append("extrude 에 h(돌출 길이) 필요")
        else:
            b["angle"] = num(b.get("angle"), 360)
    ir["base"] = b
    _CTX.c["w"] = "features"
    feats = []
    for i, f in enumerate(ir.get("features") or []):
        f = dict(f)
        op = (f.get("op") or "").lower()
        f["op"] = op
        w = f"features[{i}]({op})"
        _CTX.c["w"] = w
        if op not in OPS:
            errs.append(f"{w}: op 미지원 ({'|'.join(OPS)})")
            continue
        if op in ("hole", "pocket", "boss"):
            f["face"] = _face(f.get("face"), errs, w)
        if op == "hole":
            th = str(f.get("thread") or "").upper()
            if th and not f.get("d"):
                table = TAP_DRILL if f.get("tap") else CLEARANCE
                f["d"] = table.get(th)
                if not f["d"]:
                    errs.append(f"{w}: 나사 {th} 모름")
                    continue
            f["d"] = num(f.get("d"))
            if not f["d"] or f["d"] <= 0:
                errs.append(f"{w}: 지름 d 필요")
                continue
            f["depth"] = num(f.get("depth"))  # None = 관통
            for k in ("cbore", "csk"):
                if f.get(k):
                    f[k] = {kk: num(vv) for kk, vv in dict(f[k]).items()}
            f["points"] = _uv_points(f, errs, w)
        elif op in ("pocket", "boss"):
            f["shape"] = (f.get("shape") or "rect").lower()
            for k in ("w", "h", "r", "d", "l", "depth", "height", "angle"):
                if k in f:
                    f[k] = num(f[k], 0)
            f["at"] = _vec(f.get("at"), 2)
            if op == "pocket" and not f.get("depth"):
                errs.append(f"{w}: depth 필요")
            if op == "boss" and not f.get("height"):
                errs.append(f"{w}: height 필요")
        elif op == "cut" and (f.get("shape") or "").lower() == "plane":  # 경사 절단: 평면의 normal 쪽을 잘라낸다
            f["shape"] = "plane"
            f["point"] = _vec(f.get("point") or f.get("at"))
            if f.get("normal") is not None:
                nv = _vec(f.get("normal"))
            else:  # 수평면(normal +Z)을 axis 둘레로 angle 만큼 기울임
                ax = str(f.get("axis") or "x").lower()
                a = math.radians(num(f.get("angle"), 0) or 0)
                nv = {"x": [0, -math.sin(a), math.cos(a)], "y": [math.sin(a), 0, math.cos(a)]}.get(ax)
                if nv is None:
                    errs.append(f"{w}: plane 의 axis 는 x|y (또는 normal 을 직접)")
                    nv = [0, 0, 1]
            if math.hypot(*nv) < 1e-9:
                errs.append(f"{w}: normal 이 0 벡터")
            f["normal"] = nv
        elif op in ("add", "cut"):
            f["shape"] = (f.get("shape") or "box").lower()
            if f["shape"] not in ("box", "cylinder", "sphere", "cone"):
                errs.append(f"{w}: shape 는 box|cylinder|sphere|cone" + ("|plane(cut 만)" if op == "cut" else ""))
            f["at"] = _vec(f.get("at"))
            f["rot"] = _vec(f.get("rot"))
            ax = str(f.get("axis") or "z").lower()
            if ax == "x":
                f["rot"] = [0, 90, 0]
            elif ax == "y":
                f["rot"] = [90, 0, 0]
            if f["shape"] == "box":
                f["size"] = _vec(f.get("size"))
                if min(f["size"]) <= 0:
                    errs.append(f"{w}: box size 양수")
            else:
                for k in ("d", "h", "d2"):
                    if k in f:
                        f[k] = num(f[k])
                if not f.get("d"):
                    errs.append(f"{w}: d 필요")
        elif op == "shell":
            f["t"] = num(f.get("t") or f.get("thickness"))
            f["face"] = _face(f.get("face") or "top", errs, w)
            if not f["t"]:
                errs.append(f"{w}: t(벽 두께) 필요")
        elif op in ("fillet", "chamfer"):
            f["edges"] = str(f.get("edges") or "vertical").lower()
            if f["edges"] not in EDGESETS:
                errs.append(f"{w}: edges 는 {'|'.join(EDGESETS)}")
            key = "r" if op == "fillet" else "d"
            f[key] = num(f.get(key) or f.get("r") or f.get("d"))
            if not f[key]:
                errs.append(f"{w}: {key} 필요")
        feats.append(f)
    # CAD 관례: 외곽 필렛/모따기는 쉘보다 먼저 (쉘 뒤에 하면 안쪽 모서리까지 잡혀 r>t 일 때 커널 실패)
    si = next((i for i, f in enumerate(feats) if f["op"] == "shell"), None)
    if si is not None:
        late = [f for f in feats[si + 1:] if f["op"] in ("fillet", "chamfer") and f.get("edges") in ("vertical", "bottom")]
        if late:
            feats = feats[:si] + late + [f for f in feats[si:] if f not in late]
            ir.setdefault("notes", []).append("엔진: 외곽 필렛/모따기를 쉘 앞으로 이동 (안쪽 R = 바깥 R − 벽두께)")
    ir["features"] = feats
    return ir, errs


def _uv_points(f, errs, w):
    """절대 (u,v) 목록. 패턴에 center 가 없으면 면 중심 기준 → {"rel": [...]} 로 표시해 워커가 면 중심에 붙인다."""
    pts = _uv_points_raw(f, errs, w)
    if isinstance(pts, list) and (f.get("pattern") or "points").lower() in ("grid", "polar", "pcd", "circle", "line") and f.get("center") is None:
        return {"rel": pts}
    return pts


def _uv_points_raw(f, errs, w):
    pat = (f.get("pattern") or "points").lower()
    c = _vec(f.get("center"), 2)
    if pat in ("points", "at", "list"):
        pts = f.get("at") or f.get("points") or [[0, 0]]
        if pts and not isinstance(pts[0], (list, tuple)):
            pts = [pts]
        return [_vec(p, 2) for p in pts]
    if pat == "corners":
        if f.get("inset") is None and f.get("inset_x") is None:
            errs.append(f"{w}: pattern corners 에 inset 필요")
        return {"corners": [num(f.get("inset_x"), num(f.get("inset"), 0)), num(f.get("inset_y"), num(f.get("inset"), 0))]}
    if pat == "grid":
        nx, ny, dx, dy = int(num(f.get("nx"), 1)), int(num(f.get("ny"), 1)), num(f.get("dx"), 0), num(f.get("dy"), 0)
        return [[c[0] + (i - (nx - 1) / 2) * dx, c[1] + (j - (ny - 1) / 2) * dy] for j in range(ny) for i in range(nx)]
    if pat in ("polar", "pcd", "circle"):
        import math
        n = int(num(f.get("n"), 0))
        r = num(f.get("pcd"), 0) / 2 if f.get("pcd") else num(f.get("r"), 0)
        a0 = num(f.get("start"), 0)
        if n < 1 or r <= 0:
            errs.append(f"{w}: polar 에 n, pcd 필요")
        return [[c[0] + r * math.cos(math.radians(a0 + 360 * k / max(n, 1))), c[1] + r * math.sin(math.radians(a0 + 360 * k / max(n, 1)))] for k in range(n)]
    if pat == "line":
        n, dx, dy = int(num(f.get("n"), 1)), num(f.get("dx"), 0), num(f.get("dy"), 0)
        return [[c[0] + (k - (n - 1) / 2) * dx, c[1] + (k - (n - 1) / 2) * dy] for k in range(n)]
    errs.append(f"{w}: pattern {pat!r} 미지원")
    return []


def worker_python():
    venv = os.path.join(ROOT, "venv", "bin", "python")
    return venv if os.path.exists(venv) else sys.executable


def build(ir, outdir, rules, rev="R00", timeout=120, stated=None):
    """worker3d 를 서브프로세스로 실행. 반환 dict(files, metrics, checks, error)."""
    os.makedirs(outdir, exist_ok=True)
    job = os.path.join(outdir, "_job.json")
    with open(job, "w", encoding="utf-8") as f:
        json.dump({"ir": ir, "rules": rules, "rev": rev, "density": density(ir.get("material")), "stated": stated}, f, ensure_ascii=False)
    where = ""
    try:
        r = subprocess.run([worker_python(), "-m", "cad.worker3d", job, outdir], cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "OMP_NUM_THREADS": "4"})
        if os.path.exists(os.path.join(outdir, "_progress")):
            with open(os.path.join(outdir, "_progress")) as pf:
                where = pf.read()
    except subprocess.TimeoutExpired:
        where = ""
        try:
            with open(os.path.join(outdir, "_progress")) as pf:
                where = pf.read()
        except OSError:
            pass
        return {"error": f"3D 빌드가 {timeout}s 안에 끝나지 않음 — {where or '?'} 단계에서 멈춤 (해당 피처 단순화: 필렛 반경/모서리 집합 축소)"}
    finally:
        os.path.exists(job) and os.remove(job)
        os.path.exists(os.path.join(outdir, "_progress")) and os.remove(os.path.join(outdir, "_progress"))
    res = os.path.join(outdir, "_result.json")
    if not os.path.exists(res):
        sig = f"신호 {-r.returncode}" if r.returncode < 0 else f"종료 코드 {r.returncode}"
        return {"error": f"3D 워커 비정상 종료 ({sig}, {where or '?'} 단계 — 해당 피처를 바꾸거나 빼라): " + (r.stderr or r.stdout)[-600:]}
    with open(res, encoding="utf-8") as f:
        out = json.load(f)
    os.remove(res)
    return out
