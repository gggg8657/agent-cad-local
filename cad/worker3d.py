"""3D 빌드 워커 (서브프로세스 전용): python -m cad.worker3d job.json outdir
Feature IR → build123d 솔리드 → STEP/STL/GLB + 3면도(제3각법) DXF + 측정·검사 → outdir/_result.json
LLM 생성 코드는 실행하지 않는다 — IR 해석만."""
import json
import math
import os
import resource as _res
import sys
import tempfile
import time
import traceback

from build123d import (Align, Axis, Box, Circle, Cone, Cylinder, GeomType, Part, Plane, Pos, Rectangle, RectangleRounded, Rot, SlotOverall, Sphere,
                       Vector, chamfer, export_brep, export_gltf, export_step, export_stl, extrude, fillet, import_brep, offset, revolve, Polygon)

C, MIN, MAX = Align.CENTER, Align.MIN, Align.MAX
NORMAL = {"top": (0, 0, 1), "bottom": (0, 0, -1), "front": (0, -1, 0), "back": (0, 1, 0), "left": (-1, 0, 0), "right": (1, 0, 0)}
UV = {"top": ((1, 0, 0), (0, 1, 0)), "bottom": ((1, 0, 0), (0, 1, 0)), "front": ((1, 0, 0), (0, 0, 1)), "back": ((1, 0, 0), (0, 0, 1)),
      "left": ((0, 1, 0), (0, 0, 1)), "right": ((0, 1, 0), (0, 0, 1))}
EPS = 0.01


class BuildError(Exception):
    pass


def base_solid(b):
    t = b["type"]
    if t == "box":
        return Box(*b["size"], align=(C, C, MIN))
    if t == "cylinder":
        return Cylinder(b["d"] / 2, b["h"], align=(C, C, MIN))
    if t == "tube":
        return Cylinder(b["d"] / 2, b["h"], align=(C, C, MIN)) - Cylinder(b["id"] / 2, b["h"], align=(C, C, MIN))
    pl = {"XY": Plane.XY, "XZ": Plane.XZ, "YZ": Plane.YZ}[b["plane"]]
    face = _rounded(pl, b["points"], b.get("radii"))
    if b.get("wall"):  # 속 빈 단면: 외곽을 안쪽으로 wall 만큼 띄워 뺀다
        inner = offset(face, amount=-b["wall"])
        if not inner.faces() or inner.area <= 0:
            raise BuildError(f"wall {b['wall']:g} 이 단면에 비해 너무 두꺼움 (안쪽 윤곽이 사라짐)")
        face = face - inner
    for h in b.get("holes") or []:
        if h["type"] == "circle":
            face = face - pl * Pos(*h["at"]) * Circle(h["d"] / 2)
        else:
            face = face - _rounded(pl, h["points"], h.get("radii"))
    if not face.faces() or face.area <= 0:
        raise BuildError("단면이 비었음 (holes/wall 이 외곽을 다 지움)")
    if t == "extrude":
        if b["plane"] == "XY":
            return extrude(face, amount=b["h"])
        return extrude(face, amount=b["h"] / 2, both=True)
    return revolve(face, Axis.Z, b.get("angle", 360))


def _rounded(pl, pts, radii=None):
    """평면 위 다각형. radii[k] > 0 인 꼭짓점은 그 반경으로 라운드 (2D fillet)."""
    face = pl * Polygon(*[tuple(p) for p in pts], align=None)
    for k, r in enumerate(radii or []):
        if r and r > 0:
            target = pl.from_local_coords(Vector(pts[k][0], pts[k][1], 0))
            v = min(face.vertices(), key=lambda v: (Vector(v) - target).length)
            try:
                face = fillet(v, r)
            except Exception as e:
                raise BuildError(f"points[{k}] 라운드 r={r:g} 실패 (인접 변보다 큼?): {type(e).__name__}")
    return face


def face_frame(part, name):
    """해당 방향을 향하는 평면 중 가장 넓은 면 → (원점, n, U, V, (umin,umax,vmin,vmax))."""
    n = Vector(*NORMAL[name])
    best = None
    for f in part.faces():
        if f.geom_type != GeomType.PLANE:
            continue
        if f.normal_at().dot(n) > 0.999 and (best is None or f.area > best.area + 1e-6):
            best = f
    if best is None:
        raise BuildError(f"'{name}' 방향을 향하는 평면이 없음")
    bb = best.bounding_box()
    o = (bb.min + bb.max) * 0.5
    U, V = Vector(*UV[name][0]), Vector(*UV[name][1])
    us = [(Vector(p) - o).dot(U) for p in (bb.min, bb.max)]
    vs = [(Vector(p) - o).dot(V) for p in (bb.min, bb.max)]
    return o, n, U, V, (min(us), max(us), min(vs), max(vs)), best


def plane_at(o, n, U):
    return Plane(origin=o, x_dir=U, z_dir=n)


def sketch2d(f):
    s = f["shape"]
    if s == "circle":
        return Circle(f.get("d", 0) / 2)
    if s == "slot":
        return SlotOverall(f.get("l", 0), f.get("w", 0), rotation=f.get("angle", 0))
    if f.get("r"):
        return RectangleRounded(f.get("w", 0), f.get("h", 0), f["r"])
    return Rectangle(f.get("w", 0), f.get("h", 0))


def edgeset(part, which):
    E = part.edges()
    if which == "vertical":
        return E.filter_by(Axis.Z)
    if which == "x":
        return E.filter_by(Axis.X)
    if which == "y":
        return E.filter_by(Axis.Y)
    if which == "top":
        return E.group_by(Axis.Z)[-1]
    if which == "bottom":
        return E.group_by(Axis.Z)[0]
    return E


def apply(part, f, checks, rules, diag):
    op = f["op"]
    if op in ("hole", "pocket", "boss"):
        o, n, U, V, (u0, u1, v0, v1), face = face_frame(part, f["face"])
        ou, ov = o.dot(U), o.dot(V)  # IR 의 (u,v)는 절대좌표 → 면 중심 기준으로
        if op == "hole":
            pts = f["points"]
            if isinstance(pts, dict) and "corners" in pts:  # corners: 면 가장자리 기준
                ix, iy = pts["corners"]
                pts = [[u0 + ix, v0 + iy], [u1 - ix, v0 + iy], [u1 - ix, v1 - iy], [u0 + ix, v1 - iy]]
                f["points"] = [[u + ou, v + ov] for u, v in pts]
            elif isinstance(pts, dict):  # center 없는 패턴: 면 중심 기준
                pts = pts["rel"]
                f["points"] = [[u + ou, v + ov] for u, v in pts]
            else:
                pts = [[u - ou, v - ov] for u, v in pts]
            r = f["d"] / 2
            depth = f["depth"] or diag * 2
            web = rules.get("min_web_mm", 1.0)
            for u, v in pts:
                p = o + U * u + V * v
                edge = min(u - u0, u1 - u, v - v0, v1 - v) - r
                tag = f"{f['face']} Ø{f['d']:g} @({u + ou:.1f},{v + ov:.1f})"
                if edge < 0:
                    checks.append({"id": "P3D-004", "severity": "error", "msg": f"{tag}: 구멍이 {f['face']} 면 밖으로 나감 (면 범위 u {u0 + ou:.1f}~{u1 + ou:.1f}, v {v0 + ov:.1f}~{v1 + ov:.1f})"})
                elif edge < web:
                    checks.append({"id": "P3D-004", "severity": "warning", "msg": f"{tag}: 가장자리까지 살 두께 {edge:.2f} mm < {web} mm"})
                pl = plane_at(p + n * EPS, n, U)
                cut = pl * Cylinder(r, depth + EPS, align=(C, C, MAX))
                if f.get("cbore"):
                    cut = cut + pl * Cylinder(f["cbore"]["d"] / 2, f["cbore"]["depth"] + EPS, align=(C, C, MAX))
                if f.get("csk"):
                    D = f["csk"]["d"]
                    ang = f["csk"].get("angle") or 90
                    h = (D - f["d"]) / 2 / math.tan(math.radians(ang / 2))
                    cut = cut + pl * Cone(D / 2 + EPS, f["d"] / 2, h + EPS, align=(C, C, MAX))
                part = part - cut
            return part
        p = o + U * (f["at"][0] - ou) + V * (f["at"][1] - ov)
        sk = plane_at(p, n, U) * sketch2d(f)
        if op == "pocket":
            return part - extrude(sk, amount=f["depth"], dir=-n)
        return part + extrude(sk, amount=f["height"], dir=n)
    if op == "cut" and f["shape"] == "plane":  # 경사 절단: point 를 지나는 평면의 normal 쪽 반공간 제거
        big = diag * 4 + 10
        half = Plane(origin=f["point"], z_dir=f["normal"]) * Box(big, big, big, align=(C, C, MIN))
        out = part - half
        if not out.solids():
            raise BuildError("절단 평면이 부품 전체를 지움 (normal 방향 반대?)")
        return out
    if op in ("add", "cut"):
        s = f["shape"]
        if s == "box":
            sh = Box(*f["size"])
        elif s == "cylinder":
            sh = Cylinder(f["d"] / 2, f.get("h") or 1)
        elif s == "cone":
            sh = Cone(f["d"] / 2, (f.get("d2") or 0) / 2, f.get("h") or 1)
        else:
            sh = Sphere(f["d"] / 2)
        sh = Pos(*f["at"]) * Rot(*f["rot"]) * sh
        return part + sh if op == "add" else part - sh
    if op == "shell":
        _, _, _, _, _, face = face_frame(part, f["face"])
        if f["t"] < rules.get("min_wall_mm", 1.0):
            checks.append({"id": "P3D-006", "severity": "warning", "msg": f"shell 벽 두께 {f['t']} mm < 최소 {rules.get('min_wall_mm', 1.0)} mm"})
        return offset(part, amount=-f["t"], openings=face)
    if op in ("fillet", "chamfer"):
        return round_edges(part, f, checks)
    raise BuildError(f"op {op} 미지원")


class KernelFail(Exception):
    pass


def isolated(fn, part, timeout=90):
    """OCC 연산을 fork 한 자식에서 실행 → BREP 로 받는다. 커널이 segfault 해도 워커는 살아남는다
    (예: 삼각 프리즘에 단면 밖까지 나간 포켓 → 필렛에서 SIGSEGV)."""
    fd, path = tempfile.mkstemp(suffix=".brep")
    os.close(fd)
    pid = os.fork()
    if pid == 0:
        code = 3
        try:
            out = fn(part)
            if not out.is_valid or not out.solids():
                raise KernelFail("결과 솔리드가 유효하지 않음")
            export_brep(out, path)
            code = 0
        except BaseException as e:
            with open(path + ".err", "w") as ef:
                ef.write(f"{type(e).__name__}: {str(e)[:160]}")
        os._exit(code)
    try:
        t0 = time.time()
        while True:
            done, st = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            if time.time() - t0 > timeout:
                os.kill(pid, 9)
                os.waitpid(pid, 0)
                raise KernelFail(f"{timeout}s 초과")
            time.sleep(0.02)
        if os.WIFSIGNALED(st):
            raise KernelFail(f"커널 비정상 종료 (신호 {os.WTERMSIG(st)})")
        if os.WEXITSTATUS(st) != 0:
            raise KernelFail(open(path + ".err").read() if os.path.exists(path + ".err") else f"종료 코드 {os.WEXITSTATUS(st)}")
        return Part(import_brep(path).wrapped)
    finally:
        for q in (path, path + ".err"):
            os.path.exists(q) and os.remove(q)


def round_edges(part, f, checks):
    """필렛·모따기: 원래 크기 → ×0.5 → ×0.25 순으로 시도, 모두 실패하거나 대상 모서리가 없으면 건너뛰고 경고 (빌드는 계속)."""
    op, key = f["op"], ("r" if f["op"] == "fillet" else "d")
    r0, tag = f[key], f"{f['op']} {key}={f[key]:g} ({f['edges']} 모서리)"
    if not edgeset(part, f["edges"]):
        checks.append({"id": "P3D-008", "severity": "warning", "repairable": False, "msg": f"{tag}: 해당 모서리가 없어 건너뜀"})
        return part
    last = None
    for k in (1.0, 0.5, 0.25):
        def run(p, r=r0 * k):
            return (fillet if op == "fillet" else chamfer)(edgeset(p, f["edges"]), r)
        try:
            out = isolated(run, part)
            if k < 1:
                checks.append({"id": "P3D-008", "severity": "warning", "repairable": False,
                               "msg": f"{tag}: 커널 실패로 {key}={r0 * k:g} 로 줄여 적용 ({last})"})
            return out
        except KernelFail as e:
            last = str(e)
    checks.append({"id": "P3D-008", "severity": "warning", "repairable": False, "msg": f"{tag}: ×1·×0.5·×0.25 모두 실패 → 건너뜀 ({last})"})
    return part


# ── 3면도 ─────────────────────────────────────────────────────────────────
def edges2d(edges):
    out = []
    for e in edges:
        try:
            if e.geom_type == GeomType.LINE:
                a, b = e.start_point(), e.end_point()
                out.append(("l", [(a.X, a.Y), (b.X, b.Y)]))
            elif e.geom_type == GeomType.CIRCLE and e.is_closed:
                c = e.arc_center
                out.append(("c", (c.X, c.Y, e.radius)))
            else:
                n = max(8, min(96, int(e.length / 1.5)))
                out.append(("l", [(p.X, p.Y) for p in e.positions([i / n for i in range(n + 1)])]))
        except Exception:
            continue
    return out


def drawing(part, ir, metrics, outdir, rev):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from cad import dxfkit
    bb = part.bounding_box()
    cx, cy, cz = (bb.min.X + bb.max.X) / 2, (bb.min.Y + bb.max.Y) / 2, (bb.min.Z + bb.max.Z) / 2
    X, Y, Z = bb.size.X, bb.size.Y, bb.size.Z
    D = max(X, Y, Z) * 10
    views = {  # name: (camera, up, 2D 크기)
        "front": ((cx, cy - D, cz), (0, 0, 1), (X, Z)),
        "top": ((cx, cy, cz + D), (0, 1, 0), (X, Y)),
        "right": ((cx + D, cy, cz), (0, 0, 1), (Y, Z)),
        "iso": ((cx + D, cy - D, cz + D), (0, 0, 1), None),
    }
    gap = max(X, Y, Z) * 0.45 + 10
    place = {"front": (0, 0), "top": (0, Z / 2 + gap + Y / 2), "right": (X / 2 + gap + Y / 2, 0)}
    doc = dxfkit.new_doc()
    msp = doc.modelspace()
    proj = {}
    for name, (cam, up, _) in views.items():
        vis, hid = part.project_to_viewport(cam, up, (cx, cy, cz))
        proj[name] = (edges2d(vis), edges2d(hid) if name != "iso" else [])
    # iso 는 오른쪽 위 빈 칸
    iso_pts = [p for k, g in proj["iso"][0] if k == "l" for p in g] or [(0, 0)]
    ix0, ix1 = min(p[0] for p in iso_pts), max(p[0] for p in iso_pts)
    iy0, iy1 = min(p[1] for p in iso_pts), max(p[1] for p in iso_pts)
    place["iso"] = (place["right"][0] - (ix0 + ix1) / 2, place["top"][1] - (iy0 + iy1) / 2)
    for name, (vis, hid) in proj.items():
        ox, oy = place[name]
        for layer, items in (("M-HIDDEN", hid), ("M-PART", vis)):
            for k, g in items:
                if k == "l":
                    msp.add_lwpolyline([(x + ox, y + oy) for x, y in g], dxfattribs={"layer": layer})
                else:
                    msp.add_circle((g[0] + ox, g[1] + oy), g[2], dxfattribs={"layer": layer})
    s = dxfkit.pick_scale((X + Y) * 1.7 + 2 * gap, (Z + Y) * 1.7 + 2 * gap, "A3")
    s = s if s >= 1 else 1
    dim = {"dimtxt": 3 * s, "dimasz": 2.5 * s, "dimexe": 1.5 * s, "dimexo": 1 * s, "dimgap": 1 * s, "dimdec": 1, "dimlfac": 1,
           "dimtxsty": dxfkit.STYLE, "dimclrd": 3, "dimclre": 3}

    def ld(p1, p2, base, angle=0):
        msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle, dimstyle="EZDXF", override=dim, dxfattribs={"layer": "M-DIM"}).render()
    ld((-X / 2, -Z / 2), (X / 2, -Z / 2), (0, -Z / 2 - 10 * s))                      # 정면 폭
    ld((-X / 2, -Z / 2), (-X / 2, Z / 2), (-X / 2 - 10 * s, 0), 90)                  # 정면 높이
    ty = place["top"][1]
    ld((-X / 2, ty - Y / 2), (-X / 2, ty + Y / 2), (-X / 2 - 10 * s, ty), 90)        # 평면 깊이
    for name in ("front", "top", "right"):
        ox, oy = place[name]
        h = {"front": Z, "top": Y, "right": Z}[name]
        dxfkit.text(msp, {"front": "정면도", "top": "평면도", "right": "우측면도"}[name], (ox, oy - h / 2 - (22 if name == "front" else 7) * s), 3.5 * s, "M-TEXT")
    dxfkit.text(msp, "등각도", (place["iso"][0] + (ix0 + ix1) / 2, place["iso"][1] + iy0 - 8 * s), 3.5 * s, "M-TEXT")
    notes = [f"재질: {ir.get('material') or '-'}" + (f"   질량: {metrics['mass_g']:g} g" if metrics.get("mass_g") else ""),
             f"외형: {X:.1f} × {Y:.1f} × {Z:.1f} mm   부피: {metrics['volume_mm3']:,.0f} mm³"]
    for f in ir["features"]:
        if f["op"] == "hole":
            n = len(f["points"]) if isinstance(f["points"], list) else 4
            th = str(f.get("thread") or "").upper()
            lab = f"{n}× " + (f"{th} 탭" if th and f.get("tap") else f"Ø{f['d']:g}")
            lab += f" 깊이 {f['depth']:g}" if f.get("depth") else " 관통"
            if f.get("cbore"):
                lab += f", 카운터보어 Ø{f['cbore']['d']:g} 깊이 {f['cbore']['depth']:g}"
            if f.get("csk"):
                lab += f", 접시 Ø{f['csk']['d']:g}×{f['csk'].get('angle') or 90:g}°"
            notes.append(f"[{f['face']}] {lab}" + (f" ({th} 볼트 여유)" if th and not f.get("tap") else ""))
        elif f["op"] in ("fillet", "chamfer"):
            notes.append(f"{'R' if f['op'] == 'fillet' else 'C'}{f.get('r') or f.get('d'):g} ({f['edges']} 모서리)")
        elif f["op"] == "shell":
            notes.append(f"쉘 두께 {f['t']:g} ({f['face']} 개방)")
    notes += list(ir.get("notes") or []) + ["지시 없는 모서리 C0.5, 일반 공차 KS B ISO 2768-m", "투상법: 제3각법"]
    ny = -Z / 2 - 32 * s
    for i, n in enumerate(notes):
        dxfkit.text(msp, f"{i + 1}. {n}", (-X / 2 - 10 * s, ny - i * 5 * s), 3 * s, "M-TEXT", "TOP_LEFT")
    from ezdxf import bbox as ezb
    e = ezb.extents(msp)
    dxfkit.frame(msp, ((e.extmin.x, e.extmin.y), (e.extmax.x, e.extmax.y)), ir.get("name", "part"), f"부품도  {ir.get('material') or ''}",
                 scale=s, paper="A3", rev=rev)
    p = os.path.join(outdir, f"{ir.get('name', 'part')}_drawing.dxf")
    doc.saveas(p)
    return os.path.basename(p)


def bbox_checks(exp, got, tol, stated=None):
    """P3D-005 기대 외형. stated=None(IR 직접 편집·재빌드)이면 전부 사용자 값 → error.
    stated=[사용자 문장의 숫자들] 이면 그 숫자와 같은 축만 error, 나머지는 LLM 이 추정한 값이므로 warning (수리 루프를 돌리지 않음)."""
    bad = [(i, float(a), b) for i, (a, b) in enumerate(zip(exp, got)) if abs(float(a) - b) > tol]
    if not bad:
        return []
    user = [x for x in bad if stated is None or any(abs(x[1] - s) <= max(tol, 1e-3 * s) for s in stated)]
    out = []
    if user:
        out.append({"id": "P3D-005", "severity": "error", "msg": f"외형 {got} 가 요구 {exp} 와 다름 (사용자 지정 축: {'XYZ'[user[0][0]]}…)"})
    guess = [x for x in bad if x not in user]
    if guess:
        out.append({"id": "P3D-005", "severity": "warning", "repairable": False,
                    "msg": f"외형 {got} 가 LLM 추정 외형 {exp} 와 다름 (사용자가 말하지 않은 축 {''.join('XYZ'[x[0]] for x in guess)} — 참고)"})
    return out


def main(job_path, outdir):
    try:
        pass  # RLIMIT_AS 는 쓰지 않음: OCC 멀티스레드 가상메모리 예약과 충돌해 무한 대기. 타임아웃으로만 보호
    except Exception:
        pass
    job = json.load(open(job_path, encoding="utf-8"))
    ir, rules, rev = job["ir"], job.get("rules") or {}, job.get("rev", "R00")
    res = {"files": {}, "metrics": {}, "checks": [], "error": None}
    checks = res["checks"]
    name = ir.get("name", "part")
    try:
        part = base_solid(ir["base"])
        bb = part.bounding_box()
        diag = (bb.size.X ** 2 + bb.size.Y ** 2 + bb.size.Z ** 2) ** 0.5
        prog = os.path.join(outdir, "_progress")
        for i, f in enumerate(ir["features"]):
            with open(prog, "w") as pf:
                pf.write(f"features[{i}]({f['op']})")
            try:
                v0 = part.volume
                part = apply(part, f, checks, rules, diag)
                if f["op"] in ("hole", "pocket", "cut", "boss", "add") and abs(part.volume - v0) < 1e-6:
                    checks.append({"id": "P3D-007", "severity": "warning", "msg": f"features[{i}]({f['op']}) 가 형상을 바꾸지 않음 (위치가 재료 밖이거나 겹침)"})
            except BuildError as e:
                raise BuildError(f"features[{i}]({f['op']}): {e}")
            except Exception as e:
                raise BuildError(f"features[{i}]({f['op']}) 커널 실패: {type(e).__name__}: {str(e)[:160]}"
                                 + (" — 반경/두께가 너무 크거나 모서리가 짧음" if f["op"] in ("fillet", "chamfer", "shell") else ""))
        with open(prog, "w") as pf:
            pf.write("내보내기/3면도")
        solids = part.solids()
        vol = part.volume
        bb = part.bounding_box()
        m = {"volume_mm3": round(vol, 1), "area_mm2": round(part.area, 1), "bbox_mm": [round(bb.size.X, 2), round(bb.size.Y, 2), round(bb.size.Z, 2)],
             "solids": len(solids), "faces": len(part.faces()), "valid": bool(part.is_valid)}
        if job.get("density"):
            m["mass_g"] = round(vol * job["density"] / 1000, 1)
        if solids:
            c = part.center()
            m["center_of_mass"] = [round(c.X, 2), round(c.Y, 2), round(c.Z, 2)]
        res["metrics"] = m
        if not m["valid"]:
            checks.append({"id": "P3D-001", "severity": "error", "msg": "솔리드가 유효하지 않음 (BRep check 실패)"})
        if len(solids) != 1:
            checks.append({"id": "P3D-002", "severity": "error", "msg": f"바디가 {len(solids)}개 (단일 부품은 1개여야 함 — 떨어진 덩어리 확인)"})
        if vol <= 0:
            checks.append({"id": "P3D-003", "severity": "error", "msg": "부피 0"})
        exp = (ir.get("expect") or {}).get("bbox")
        if exp:
            checks += bbox_checks(exp, m["bbox_mm"], rules.get("bbox_tol_mm", 0.05), job.get("stated"))
        export_step(part, os.path.join(outdir, f"{name}.step"))
        res["files"][f"{name}.step"] = "STEP (교환 표준)"
        export_stl(part, os.path.join(outdir, f"{name}.stl"), tolerance=0.02, angular_tolerance=0.15)
        res["files"][f"{name}.stl"] = "STL (메시·3D프린팅)"
        export_gltf(part, os.path.join(outdir, f"{name}.glb"), binary=True, linear_deflection=0.05, angular_deflection=0.2)
        res["files"][f"{name}.glb"] = "GLB (웹 뷰어)"
        try:
            res["files"][drawing(part, ir, m, outdir, rev)] = "3면도 DXF (제3각법)"
        except Exception as e:
            checks.append({"id": "P3D-900", "severity": "warning", "msg": f"도면 생성 실패: {type(e).__name__}: {str(e)[:200]}"})
    except BuildError as e:
        res["error"] = str(e)
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        res["trace"] = traceback.format_exc()[-1500:]
    json.dump(res, open(os.path.join(outdir, "_result.json"), "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
