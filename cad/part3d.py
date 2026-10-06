"""PART_3D — Feature IR 정규화 + 빌드 워커 실행. OCC(build123d)는 worker3d 서브프로세스에서만 import (타임아웃·메모리 제한, 서버 보호).

좌표 규약: 모든 좌표는 절대좌표(mm). box/cylinder base 는 XY 중심·Z=0 바닥. 면(face) 피처의 (u,v)는 그 면 축의 절대값.
  top/bottom: u=X, v=Y   front/back: u=X, v=Z   left/right: u=Y, v=Z   (corners 패턴만 면 가장자리 기준 inset)
"""
import json
import os
import subprocess
import sys

from .part2d import CLEARANCE, density
from .util import ROOT, num, resolve_params

TAP_DRILL = {"M2": 1.6, "M2.5": 2.05, "M3": 2.5, "M4": 3.3, "M5": 4.2, "M6": 5.0, "M8": 6.8, "M10": 8.5, "M12": 10.2, "M16": 14.0, "M20": 17.5}
FACES = ("top", "bottom", "front", "back", "left", "right")
FACE_ALIAS = {"+z": "top", "-z": "bottom", "-y": "front", "+y": "back", "-x": "left", "+x": "right", "위": "top", "아래": "bottom",
              "앞": "front", "뒤": "back", "왼쪽": "left", "오른쪽": "right", ">z": "top", "<z": "bottom", "<y": "front", ">y": "back",
              "<x": "left", ">x": "right"}
BASES = ("box", "cylinder", "tube", "extrude", "revolve")
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
    ir = dict(ir or {})
    ir, params = resolve_params(ir, ir.get("params"))
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
            b["id"] = num(b.get("id") or b.get("inner_d"))
            if not b["id"] or b["id"] >= (b["d"] or 0):
                errs.append("tube 는 0 < id < d")
        if not b["d"] or not b["h"]:
            errs.append(f"base {t} 에 d, h 필요")
    elif t in ("extrude", "revolve"):
        pts = b.get("points") or []
        if len(pts) < 3:
            errs.append(f"base {t} 에 3개 이상 points(2D 프로파일) 필요")
        b["points"] = [[num(p[0], 0), num(p[1], 0)] for p in pts]
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
    feats = []
    for i, f in enumerate(ir.get("features") or []):
        f = dict(f)
        op = (f.get("op") or "").lower()
        f["op"] = op
        w = f"features[{i}]({op})"
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
        elif op in ("add", "cut"):
            f["shape"] = (f.get("shape") or "box").lower()
            if f["shape"] not in ("box", "cylinder", "sphere", "cone"):
                errs.append(f"{w}: shape 는 box|cylinder|sphere|cone")
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


def build(ir, outdir, rules, rev="R00", timeout=120):
    """worker3d 를 서브프로세스로 실행. 반환 dict(files, metrics, checks, error)."""
    os.makedirs(outdir, exist_ok=True)
    job = os.path.join(outdir, "_job.json")
    with open(job, "w", encoding="utf-8") as f:
        json.dump({"ir": ir, "rules": rules, "rev": rev, "density": density(ir.get("material"))}, f, ensure_ascii=False)
    try:
        r = subprocess.run([worker_python(), "-m", "cad.worker3d", job, outdir], cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "OMP_NUM_THREADS": "4"})
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
        return {"error": "3D 워커 비정상 종료: " + (r.stderr or r.stdout)[-800:]}
    with open(res, encoding="utf-8") as f:
        out = json.load(f)
    os.remove(res)
    return out
