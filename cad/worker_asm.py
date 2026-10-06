"""조립 빌드 워커 (서브프로세스 전용): python -m cad.worker_asm job.json outdir
model(컴포넌트별 형상 기술자, 재질 mat) → build123d 솔리드(재질별 그룹) → STEP · GLB(전체/절개) · 정밀 간섭
→ GA 도면 DXF: 정면 단면(재질별 해칭·굵은 절단선·숨은 뒤쪽 윤곽) + 평면 + 부품 풍선·부품표 + 표고 + 냉각재 흐름 화살표."""
import json
import math
import os
import re
import sys
import traceback

from build123d import (Align, Axis, Box, Color, Compound, Cylinder, Plane, Polygon, Pos, RegularPolygon, Sphere, Vector, export_gltf, export_step,
                       extrude, revolve, Rot, section)

C, MIN, MAX = Align.CENTER, Align.MIN, Align.MAX
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cad.detail import MAT_ALPHA, MAT_RGB  # noqa: E402

HATCH = {"steel": ("ANSI31", 8, 0.9), "concrete": ("AR-CONC", 252, 0.08), "graphite": ("ANSI37", 250, 0.9), "insulation": ("ANSI37", 41, 0.6),
         "fuel": ("SOLID", 6, 0), "control": ("SOLID", 250, 0), "water": ("SOLID", 151, 0), "sodium": ("SOLID", 51, 0), "salt": ("SOLID", 211, 0),
         "tubes": ("ANSI32", 5, 0.5), "glass": ("SOLID", 131, 0), "motor": ("ANSI31", 5, 0.9)}
MAT_KO = {"steel": "강재", "concrete": "콘크리트", "graphite": "흑연", "fuel": "핵연료", "control": "제어봉", "water": "물", "sodium": "소듐", "salt": "용융염",
          "tubes": "전열관", "glass": "차폐창", "motor": "모터", "insulation": "전열기"}


def _rev(prof, x, y, z):
    pts = []
    for q in [(max(px, 0.0), pz) for px, pz in prof]:
        if not pts or abs(pts[-1][0] - q[0]) > 1e-6 or abs(pts[-1][1] - q[1]) > 1e-6:
            pts.append(q)
    return Pos(x, y, z) * revolve(Plane.XZ * Polygon(*pts, align=None), Axis.Z, 360)


def prim(s):
    p = s["p"]
    if p == "cyl":
        x, y, z = s["c"]
        ax = s.get("axis", "z")
        if ax == "x":
            return Pos(x, y, z) * Rot(0, 90, 0) * Cylinder(s["d"] / 2, s["h"], align=(C, C, MIN))
        if ax == "y":
            return Pos(x, y, z) * Rot(-90, 0, 0) * Cylinder(s["d"] / 2, s["h"], align=(C, C, MIN))
        return Pos(x, y, z) * Cylinder(s["d"] / 2, s["h"], align=(C, C, MIN))
    if p == "shell":  # 속 빈 용기 + 타원 경판 (벽 단면 회전)
        x, y, z = s["c"]
        D, H, t = s["d"], s["h"], s["t"]
        R, hh = D / 2, D * 0.22
        Ri, hi = R - t, max(hh - t, t)
        arc = lambda r, h, z0, up, n=12: [(r * math.cos(math.pi / 2 * k / n), z0 + (1 if up else -1) * h * math.sin(math.pi / 2 * k / n)) for k in range(n + 1)]
        return _rev(arc(R, hh, hh, False)[::-1] + arc(R, hh, H - hh, True) + arc(Ri, hi, H - hh, True)[::-1] + arc(Ri, hi, hh, False), x, y, z)
    if p == "rev":  # 임의 회전체 윤곽 [(r, z)…] (c 기준)
        x, y, z = s["c"]
        return _rev(s["prof"], x, y, z)
    if p == "dome":
        x, y, z = s["c"]
        D, H, t = s["d"], s["h"], s["t"]
        outer = Cylinder(D / 2, H, align=(C, C, MIN)) + Pos(0, 0, H) * (Sphere(D / 2) & Box(D * 1.1, D * 1.1, D, align=(C, C, MIN)))
        inner = Pos(0, 0, t) * Cylinder(D / 2 - t, H - t, align=(C, C, MIN)) + Pos(0, 0, H) * (Sphere(D / 2 - t) & Box(D, D, D, align=(C, C, MIN)))
        return Pos(x, y, z) * (outer - inner)
    if p == "pool_cyl":
        x, y, z = s["c"]
        D, H, t = s["d"], s["h"], s["t"]
        return Pos(x, y, z) * (Cylinder(D / 2, H, align=(C, C, MIN)) - Pos(0, 0, t) * Cylinder(D / 2 - t, H, align=(C, C, MIN)))
    if p == "pool_box":
        a, b, t = s["min"], s["max"], s["t"]
        W, L, H = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        o = Box(W, L, H, align=(C, C, MIN)) - Pos(0, 0, t) * Box(max(W - 2 * t, 1), max(L - 2 * t, 1), H, align=(C, C, MIN))
        return Pos((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, a[2]) * o
    if p == "tube":
        x, y, z = s["c"]
        return Pos(x, y, z) * (Cylinder(s["d"] / 2, s["h"], align=(C, C, MIN)) - Cylinder(s["id"] / 2, s["h"], align=(C, C, MIN)))
    if p == "head":
        x, y, z = s["c"]
        r, h, up = s["d"] / 2, max(s["h"], 1), s.get("up", True)
        n = 12
        return _rev([(0.0, 0.0)] + [(r * math.cos(math.pi / 2 * k / n), (1 if up else -1) * h * math.sin(math.pi / 2 * k / n)) for k in range(n + 1)], x, y, z)
    if p == "hex":
        x, y, z = s["c"]
        return Pos(x, y, z) * extrude(RegularPolygon(s["af"] / math.sqrt(3), 6), s["h"])
    if p == "box":
        a, b = s["min"], s["max"]
        return Pos((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2) * Box(b[0] - a[0], b[1] - a[1], b[2] - a[2])
    if p == "seg":
        a, b = Vector(*s["a"]), Vector(*s["b"])
        v = b - a
        if v.length < 1e-6:
            return None
        return Plane(origin=a, z_dir=v.normalized()) * Cylinder(s["d"] / 2, v.length, align=(C, C, MIN))
    if p == "bottle":
        R, Rn, t = s["R"], s["Rn"], s["t"]
        zb, zs, zt = s["zb"], s["zs"], s["zt"]
        zn = zs + (R - Rn) * 0.8
        mk = lambda pts: revolve(Plane.XZ * Polygon(*pts, align=None), Axis.Z, 360)
        return mk([(0, zb), (R, zb), (R, zs), (Rn, zn), (Rn, zt), (0, zt)]) - mk([(0, zb + t), (R - t, zb + t), (R - t, zs), (Rn - t, zn), (Rn - t, zt - t), (0, zt - t)])
    raise ValueError(f"형상 {p}")


def _matof(s, c):
    return s.get("mat") or c.get("mat") or "_comp"


def _rgba(mat, c):
    if mat in MAT_RGB:
        r, g, b = MAT_RGB[mat]
        return r, g, b, MAT_ALPHA.get(mat, 1.0)
    r, g, b = c["rgb"]
    return r, g, b, c.get("alpha", 1.0)


def groups_of(c):
    """컴포넌트 → {mat: {"solid": 합친 솔리드|None, "lat": [격자 솔리드…], "lat_s": [기술자…]}}"""
    G = {}
    for s in c["shapes"]:
        m = _matof(s, c)
        g = G.setdefault(m, {"solid": None, "lat": [], "lat_s": []})
        q = prim(s)
        if q is None:
            continue
        if s.get("lattice"):
            g["lat"].append(q)
            g["lat_s"].append(s)
        else:
            g["solid"] = q if g["solid"] is None else g["solid"] + q
    return G


def paint(sh, mat, c, label):
    r, g, b, a = _rgba(mat, c)
    sh.color = Color(r / 255, g / 255, b / 255, a)
    sh.label = label
    return sh


def _bbox_of(s):
    p = s["p"]
    if p in ("box", "pool_box"):
        return s["min"][1], s["max"][1]
    if p == "seg":
        r = s["d"] / 2
        return min(s["a"][1], s["b"][1]) - r, max(s["a"][1], s["b"][1]) + r
    if p == "bottle":
        return -s["R"], s["R"]
    if p == "hex":
        return s["c"][1] - s["af"] / 1.7, s["c"][1] + s["af"] / 1.7
    if p == "rev":
        r = max(q[0] for q in s["prof"])
        return s["c"][1] - r, s["c"][1] + r
    if s.get("axis") == "y":
        return s["c"][1], s["c"][1] + s["h"]
    return s["c"][1] - s["d"] / 2, s["c"][1] + s["d"] / 2


def main(job_path, outdir):
    job = json.load(open(job_path, encoding="utf-8"))
    model = job["model"]
    res = {"files": {}, "checks": [], "metrics": {}, "error": None}
    name = job.get("name", "assembly")
    prog = os.path.join(outdir, "_progress")
    say = lambda m: open(prog, "w").write(m)
    try:
        comps = {c["id"]: c for c in model["components"]}
        G = {}
        for c in model["components"]:
            say(f"{c['id']} 솔리드")
            G[c["id"]] = groups_of(c)
        # 간섭: 기기끼리, 물·소듐·전열관 다발 같은 '내용물'은 제외
        say("간섭 검사")
        tol = job.get("interference_tol_mm3", 1000)
        eq = [c for c in model["components"] if c["role"] == "equipment"]
        allow = [set(x) for x in model.get("allow_overlap", [])] + [{"core", "reflector"}, {"riser", "core"}, {"core", "chimney"}]
        solid_eq = {}
        for c in eq:
            parts = [g["solid"] for m, g in G[c["id"]].items() if g["solid"] is not None and m not in ("water", "sodium", "tubes")]
            if parts:
                s0 = parts[0]
                for q in parts[1:]:
                    s0 = s0 + q
                solid_eq[c["id"]] = s0
        n_pairs = 0
        for i in range(len(eq)):
            for j in range(i + 1, len(eq)):
                a, b = eq[i], eq[j]
                A, B = a["bbox"], b["bbox"]
                if not all(A[k] < B[k + 3] and B[k] < A[k + 3] for k in range(3)) or {a["type"], b["type"]} in allow:
                    continue
                if a["id"] not in solid_eq or b["id"] not in solid_eq:
                    continue
                n_pairs += 1
                try:
                    v = (solid_eq[a["id"]] & solid_eq[b["id"]]).volume
                except Exception:
                    v = 0
                if v > tol:
                    res["checks"].append({"id": "MSR-003" if model.get("domain") == "msr" else "GEO-001", "name": "기기 간섭", "severity": "error", "repairable": True,
                                          "msg": f"{a['label']}({a['id']}) ∩ {b['label']}({b['id']}) = {v / 1e6:.3f} L"})
        res["metrics"]["interference_pairs_checked"] = n_pairs
        dens = job.get("densities", {})
        mass = 0.0
        for c in model["components"]:
            rho = next((v for k, v in dens.items() if c["material"].startswith(k)), None)
            if rho and c["type"] == "reactor_vessel":
                mass += sum(g["solid"].volume for g in G[c["id"]].values() if g["solid"] is not None) / 1e9 * rho
        if mass:
            res["metrics"]["vessel_mass_t"] = round(mass / 1000, 2)
        # 내보내기: 컴포넌트 = 재질별 자식들의 묶음
        say("STEP/GLB 내보내기")
        e = model["extent"]
        big = 4 * max(e[3] - e[0], e[4] - e[1], e[5] - e[2], 1)
        keep = Pos(0, big / 2, (e[2] + e[5]) / 2) * Box(big, big, big)  # y ≥ 0 반쪽 → 정면(-Y)에서 내부가 보임
        full, cut = [], []
        for c in model["components"]:
            kids, ckids = [], []
            for m, g in G[c["id"]].items():
                if g["solid"] is not None:
                    kids.append(paint(g["solid"], m, c, f"{c['id']}:{m}"))
                    try:
                        h = g["solid"] & keep if c["type"] != "shield_cell" else g["solid"]
                        if h is not None and h.volume > 1:
                            ckids.append(paint(h, m, {**c, "alpha": 1.0 if c["role"] == "container" and m != "water" else c.get("alpha", 1.0)},
                                               f"{c['id']}:{m}"))
                    except Exception:
                        pass
                if g["lat"]:
                    kids.append(paint(Compound(children=g["lat"]), m, c, f"{c['id']}:{m}:lattice"))
                    ycen = lambda s: s["c"][1] if "c" in s else (s["min"][1] + s["max"][1]) / 2
                    back = [q for q, s in zip(g["lat"], g["lat_s"]) if ycen(s) >= -1]
                    if back:
                        ckids.append(paint(Compound(children=back), m, c, f"{c['id']}:{m}:lattice"))
            if kids:
                cc = Compound(children=kids)
                cc.label = c["id"]
                full.append(cc)
            if ckids:
                cc = Compound(children=ckids)
                cc.label = c["id"]
                cut.append(cc)
        asm = Compound(children=full)
        asm.label = name
        export_step(asm, os.path.join(outdir, f"{name}.step"))
        res["files"][f"{name}.step"] = "STEP 조립체 (기기·재질별 이름·색)"
        export_gltf(asm, os.path.join(outdir, f"{name}.glb"), binary=True, linear_deflection=3, angular_deflection=0.35)
        res["files"][f"{name}.glb"] = "GLB 전체"
        casm = Compound(children=cut)
        casm.label = name + "_cutaway"
        export_gltf(casm, os.path.join(outdir, f"{name}_cutaway.glb"), binary=True, linear_deflection=3, angular_deflection=0.35)
        res["files"][f"{name}_cutaway.glb"] = "GLB 절개 (내부 보기)"
        say("GA 도면")
        try:
            res["files"][ga_drawing(model, comps, G, cut, outdir, name, job.get("rev", "R00"))] = "GA 도면 DXF (정면 단면·해칭 + 평면 + 부품표)"
        except Exception as ex:
            res["checks"].append({"id": "GA-900", "severity": "warning", "msg": f"GA 도면 실패: {type(ex).__name__}: {ex}"})
            res["trace"] = traceback.format_exc()[-1500:]
    except Exception as ex:
        res["error"] = f"{type(ex).__name__}: {str(ex)[:300]}"
        res["trace"] = traceback.format_exc()[-1500:]
    json.dump(res, open(os.path.join(outdir, "_result.json"), "w", encoding="utf-8"), ensure_ascii=False)


# ── GA 도면 ───────────────────────────────────────────────────────────────
def _layer(c):
    if c.get("layer"):
        return c["layer"], c.get("color", 7)
    from cad.assembly import COMP
    v = COMP.get(c["type"]) or {"layer": "MSR-PIPE", "color": 9}
    return v["layer"], v["color"]


def msr_notes(model):
    calc = model["calc"]
    notes = [f"열출력 {model['design']['power_mwth']:g} MWth · {calc['salt']['name']} · ΔT {model['design']['core_dT']:g} K",
             f"노심 Ø{calc['core']['D_mm']} × H{calc['core']['H_mm']} mm, 출력밀도 {calc['core']['q_MW_m3']} MW/m³",
             f"연료염 {calc['salt']['inventory_m3']} m³ ({calc['salt']['mass_t']} t), 유량 {calc['flow']['m_kg_s']} kg/s"]
    if "hx" in calc:
        notes.append(f"열교환기 {calc['hx']['A_each_m2']} m² × {len([c for c in model['components'] if c['type'] == 'heat_exchanger'])}기, 관 {calc['hx']['tubes_each']}개/기")
    if "drain" in calc:
        notes.append(f"덤프탱크 {calc['drain']['tanks']}기 × {calc['drain']['tank_V_m3']} m³ = {calc['drain']['capacity_m3']} m³")
    notes.append("개념설계 초안 — 교육·검토용 일반값, 실제 설계값 아님")
    return notes


def _wire_pts(w, tol):
    pts = []
    for ed in w.edges():
        n = max(2, min(80, int(ed.length / tol) + 1))
        ps = [ed.position_at(i / (n - 1)) for i in range(n)]
        if pts and (ps[0] - Vector(pts[-1][0], 0, pts[-1][1])).length > (ps[-1] - Vector(pts[-1][0], 0, pts[-1][1])).length:
            ps = ps[::-1]
        pts += [(p.X, p.Z) for p in ps]
    return pts


def ga_drawing(model, comps, G, cut, outdir, name, rev):
    from ezdxf import bbox as ezb
    from cad import dxfkit
    from cad.worker3d import edges2d
    doc = dxfkit.new_doc()
    msp = doc.modelspace()
    for c in model["components"]:
        lay, col = _layer(c)
        if lay not in doc.layers:
            doc.layers.add(lay, color=col)
    for lay, col in (("GA-CUT", 7), ("GA-HATCH", 8), ("GA-FLOW", 1), ("GA-EL", 3), ("GA-BALLOON", 7)):
        if lay not in doc.layers:
            doc.layers.add(lay, color=col)
    e = model["extent"]
    H = e[5] - e[2]
    W = e[3] - e[0]
    Dcam = 10 * max(H, W)
    cz = (e[2] + e[5]) / 2
    S = dxfkit.pick_scale(W * 2.9 + 3000, H * 1.25 + 2000, "A1")
    tol = max(W, H) / 400

    # 1) 뒤쪽 윤곽 (절개 반쪽을 −Y 에서 투영, 얇은 선)
    for sh in cut:
        c = comps[sh.label]
        if c["type"] == "shield_cell":
            continue
        lay = _layer(c)[0]
        for kid in sh.children:
            if ":lattice" in (kid.label or ""):
                continue
            try:
                vis, _ = kid.project_to_viewport((0, -Dcam, cz), (0, 0, 1), (0, 0, cz))
            except Exception:
                continue
            for k, g in edges2d(vis):
                if k == "l":
                    msp.add_lwpolyline(g, dxfattribs={"layer": lay})
                else:
                    msp.add_circle((g[0], g[1]), g[2], dxfattribs={"layer": lay})
    # 2) 절단면 해칭 (기술자 단위로 y=0 단면) + 굵은 절단선
    def hatch(paths, mat):
        pat, col, sc = HATCH.get(mat, ("ANSI31", 8, 0.9))
        h = msp.add_hatch(color=col, dxfattribs={"layer": "GA-HATCH"})
        if pat == "SOLID":
            h.set_solid_fill(color=col)
        else:
            h.set_pattern_fill(pat, color=col, scale=max(sc * S, 0.01))
        for i, pts in enumerate(paths):
            if len(pts) >= 3:
                h.paths.add_polyline_path(pts, is_closed=True, flags=1 if i == 0 else 16)
        for pts in paths:
            if len(pts) >= 3:
                msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "GA-CUT", "lineweight": 50})
    for c in model["components"]:
        if c["type"] == "shield_cell":
            continue
        for s in c["shapes"]:
            m = _matof(s, c)
            y0, y1 = _bbox_of(s)
            if not (y0 < 0 < y1):
                continue
            if s.get("lattice"):  # 집합체: 해석적 단면 (사각형)
                if s["p"] == "hex":
                    x, _, z = s["c"]
                    hw = s["af"] / 2
                else:
                    x = (s["min"][0] + s["max"][0]) / 2
                    z = s["min"][2]
                    hw = (s["max"][0] - s["min"][0]) / 2
                hz = s["h"] if s["p"] == "hex" else s["max"][2] - s["min"][2]
                hatch([[(x - hw, z - cz), (x + hw, z - cz), (x + hw, z + hz - cz), (x - hw, z + hz - cz)]], m)
                continue
            try:
                q = prim(s)
                sec = section(q, section_by=Plane.XZ)
            except Exception:
                continue
            for f in sec.faces():
                paths = [[(px, pz - cz) for px, pz in _wire_pts(f.outer_wire(), tol)]] + [[(px, pz - cz) for px, pz in _wire_pts(w, tol)] for w in f.inner_wires()]
                hatch(paths, m)
    # 3) 평면 (위에서, 오른쪽)
    px, py = W * 1.25 + 2500, H * 0.18
    for c in model["components"]:
        if c["type"] == "shield_cell":
            continue
        lay = _layer(c)[0]
        for m, g in G[c["id"]].items():
            if m == "water":
                continue
            for s in g["lat_s"]:  # 격자는 해석적으로
                if s["p"] == "hex":
                    r = s["af"] / math.sqrt(3)
                    x, y = s["c"][0], s["c"][1]
                    msp.add_lwpolyline([(px + x + r * math.cos(math.radians(60 * k)), py + y + r * math.sin(math.radians(60 * k))) for k in range(6)], close=True,
                                       dxfattribs={"layer": lay, "color": 1 if m == "control" else 256})
                else:
                    a, b = s["min"], s["max"]
                    msp.add_lwpolyline([(px + a[0], py + a[1]), (px + b[0], py + a[1]), (px + b[0], py + b[1]), (px + a[0], py + b[1])], close=True,
                                       dxfattribs={"layer": lay, "color": 1 if m == "control" else 256})
            if g["solid"] is None:
                continue
            try:
                vis, _ = g["solid"].project_to_viewport((0, 0, Dcam), (0, 1, 0), (0, 0, 0))
            except Exception:
                continue
            for k, gg in edges2d(vis):
                if k == "l":
                    msp.add_lwpolyline([(x + px, y + py) for x, y in gg], dxfattribs={"layer": lay})
                else:
                    msp.add_circle((gg[0] + px, gg[1] + py), gg[2], dxfattribs={"layer": lay})
    msp.add_line((px - W * 0.6, py), (px + W * 0.6, py), dxfattribs={"layer": "M-CENTER"})
    msp.add_line((px, py - (e[4] - e[1]) * 0.6), (px, py + (e[4] - e[1]) * 0.6), dxfattribs={"layer": "M-CENTER"})
    dxfkit.text(msp, "A", (px - W * 0.62, py + 3 * S), 5 * S, "GA-CUT")
    dxfkit.text(msp, "A", (px + W * 0.62, py + 3 * S), 5 * S, "GA-CUT")
    # 4) 냉각재 흐름 화살표 (노심 상향, 하강관 하향)
    core = next((c for c in model["components"] if c["type"] in ("core",)), None)
    vessel = next((c for c in model["components"] if c["type"] in ("rpv", "reactor_vessel", "pool")), None)
    if core:
        b = core["bbox"]
        ax_ = (b[0] + b[3]) / 2
        _flow_arrow(msp, (ax_, b[2] - cz + (b[5] - b[2]) * 0.15), (ax_, b[5] - cz + (b[5] - b[2]) * 0.25), S, 1)
        if vessel:
            v = vessel["bbox"]
            for sgn in (-1, 1):
                xx = ax_ + sgn * ((b[3] - b[0]) / 2 + ((v[3] - v[0]) / 2 - (b[3] - b[0]) / 2) * 0.55)
                _flow_arrow(msp, (xx, b[5] - cz + (b[5] - b[2]) * 0.2), (xx, b[2] - cz + (b[5] - b[2]) * 0.1), S, 5)
    # 5) 표고 (EL., 노심 바닥 = ±0)
    z0 = core["bbox"][2] if core else e[2]
    marks = {}
    for c in model["components"]:
        if c["role"] in ("container",) or c["type"] in ("core",):
            marks[round(c["bbox"][2])] = re.sub(r" \d+$", "", c["label"]) + " 하단"
            marks[round(c["bbox"][5])] = re.sub(r" \d+$", "", c["label"]) + " 상단"
    xl = e[0] - 2200
    last = None
    for zz in sorted(marks):
        if last is not None and abs(zz - last) < 7 * S:
            continue
        last = zz
        y = zz - cz
        msp.add_lwpolyline([(xl - 1.8 * S, y + 2.4 * S), (xl + 1.8 * S, y + 2.4 * S), (xl, y)], close=True, dxfattribs={"layer": "GA-EL"})
        msp.add_line((xl, y), (e[0] - 300, y), dxfattribs={"layer": "GA-EL", "linetype": "DOT"})
        dxfkit.text(msp, f"EL. {(zz - z0) / 1000:+.3f}", (xl - 2.5 * S, y + 1 * S), 2.8 * S, "GA-EL", "BOTTOM_RIGHT")
        dxfkit.text(msp, marks[zz], (xl - 2.5 * S, y - 1 * S), 2.2 * S, "GA-EL", "TOP_RIGHT")
    # 6) 부품 풍선 + 부품표
    keys = []
    for c in sorted(model["components"], key=lambda c: -c["bbox"][5]):
        k = c.get("key") or c["type"]
        if c["role"] == "pipe" or c["type"] == "shield_cell" or k in [x[0] for x in keys]:
            continue
        keys.append((k, c))
    right = e[3] + 1500
    used = []
    rows = []
    for n, (k, c) in enumerate(keys, 1):
        insts = [x for x in model["components"] if (x.get("key") or x["type"]) == k]
        vis = max(insts, key=lambda x: ((x["bbox"][1] + x["bbox"][4]) / 2 >= -1, (x["bbox"][0] + x["bbox"][3]) / 2))
        b = vis["bbox"]
        ax_ = (b[0] + b[3]) / 2 if c["role"] != "container" else b[3] - (b[3] - b[0]) * 0.03
        az = (b[2] + b[5]) / 2 - cz if c["role"] != "container" else b[2] + (b[5] - b[2]) * 0.3 - cz
        ly = az
        while any(abs(ly - u) < 9 * S for u in used):
            ly -= 9 * S
        used.append(ly)
        msp.add_lwpolyline([(ax_, az), (right - 600, ly), (right, ly)], dxfattribs={"layer": "GA-BALLOON"})
        msp.add_circle((ax_, az), 0.8 * S, dxfattribs={"layer": "GA-BALLOON"})
        msp.add_circle((right + 3.5 * S, ly), 3.5 * S, dxfattribs={"layer": "GA-BALLOON"})
        dxfkit.text(msp, str(n), (right + 3.5 * S, ly), 3.2 * S, "GA-BALLOON")
        mats = sorted({MAT_KO.get(_matof(s, c), "") for x in insts for s in x["shapes"]} - {""})
        rows.append((n, re.sub(r" \d+$", "", c["label"]), len(insts), ", ".join(mats) or c.get("material", "")))
    # 부품표 (평면도 아래)
    tx, ty = px - W * 0.55, py - (e[4] - e[1]) * 0.7 - 6 * S
    cw = [10 * S, 46 * S, 12 * S, 44 * S]
    rh = 6 * S
    head = ("번호", "품명", "수량", "재질")
    for r_i, row in enumerate([head] + [tuple(map(str, r)) for r in rows]):
        x = tx
        y = ty - r_i * rh
        for j, cell in enumerate(row):
            dxfkit.rect(msp, x, y - rh, cw[j], rh, "ICT-TITLE")
            dxfkit.text(msp, cell, (x + cw[j] / 2, y - rh / 2), 2.8 * S if r_i else 3.2 * S, "ICT-TEXT")
            x += cw[j]
    # 해칭 범례
    used_m = sorted({_matof(s, c) for c in model["components"] for s in c["shapes"]} & set(HATCH))
    ly = ty - (len(rows) + 2) * rh
    dxfkit.text(msp, "단면 재질", (tx, ly), 3.4 * S, "ICT-TEXT", "TOP_LEFT")
    for i, m in enumerate(used_m):
        yy = ly - 6 * S - i * 6 * S
        pat, col, sc = HATCH[m]
        h = msp.add_hatch(color=col, dxfattribs={"layer": "GA-HATCH"})
        if pat == "SOLID":
            h.set_solid_fill(color=col)
        else:
            h.set_pattern_fill(pat, color=col, scale=max(sc * S, 0.01))
        h.paths.add_polyline_path([(tx, yy), (tx + 10 * S, yy), (tx + 10 * S, yy - 4 * S), (tx, yy - 4 * S)], is_closed=True)
        msp.add_lwpolyline([(tx, yy), (tx + 10 * S, yy), (tx + 10 * S, yy - 4 * S), (tx, yy - 4 * S)], close=True, dxfattribs={"layer": "GA-CUT"})
        dxfkit.text(msp, MAT_KO.get(m, m), (tx + 13 * S, yy - 2 * S), 2.8 * S, "ICT-TEXT", "MIDDLE_LEFT")
    # 치수
    dim = {"dimtxt": 3.2 * S, "dimasz": 2.5 * S, "dimexe": 1.5 * S, "dimexo": 1 * S, "dimgap": 1 * S, "dimdec": 0, "dimlfac": 1, "dimtxsty": dxfkit.STYLE}
    msp.add_linear_dim(base=(0, e[2] - cz - 10 * S), p1=(e[0], e[2] - cz), p2=(e[3], e[2] - cz), dimstyle="EZDXF", override=dim, dxfattribs={"layer": "M-DIM"}).render()
    if core:
        b = core["bbox"]
        msp.add_linear_dim(base=(0, b[5] - cz + 5 * S), p1=(b[0], b[5] - cz), p2=(b[3], b[5] - cz), dimstyle="EZDXF", override=dim, dxfattribs={"layer": "M-DIM"}).render()
        msp.add_linear_dim(base=(b[3] + 4 * S, 0), p1=(b[3], b[2] - cz), p2=(b[3], b[5] - cz), angle=90, dimstyle="EZDXF", override=dim,
                           dxfattribs={"layer": "M-DIM"}).render()
    dxfkit.text(msp, "단면 A-A", (0, e[2] - cz - 22 * S), 5 * S, "ICT-TEXT")
    dxfkit.text(msp, "평면", (px, py + (e[4] - e[1]) * 0.62 + 4 * S), 5 * S, "ICT-TEXT")
    notes = model.get("ga_notes") or msr_notes(model)
    ny = ly - (len(used_m) + 2) * 6 * S
    for i, n in enumerate(notes):
        dxfkit.text(msp, f"{i + 1}. {n}", (tx, ny - i * 5 * S), 2.8 * S, "ICT-TEXT", "TOP_LEFT")
    x = ezb.extents(msp)
    dxfkit.frame(msp, ((x.extmin.x, x.extmin.y), (x.extmax.x, x.extmax.y)), model["name"], "개념설계 GA (단면 A-A · 평면 · 부품표)", scale=S, paper="A1", rev=rev)
    p = os.path.join(outdir, f"{name}_GA.dxf")
    doc.saveas(p)
    return os.path.basename(p)


def _flow_arrow(msp, a, b, S, color):
    msp.add_line(a, b, dxfattribs={"layer": "GA-FLOW", "color": color, "lineweight": 50})
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy) or 1
    ux, uy = dx / L, dy / L
    s = 3 * S
    msp.add_solid([b, (b[0] - ux * s - uy * s * 0.45, b[1] - uy * s + ux * s * 0.45), (b[0] - ux * s + uy * s * 0.45, b[1] - uy * s - ux * s * 0.45)],
                  dxfattribs={"layer": "GA-FLOW", "color": color})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
