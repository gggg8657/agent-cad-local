"""PART_2D — 판재/가스켓/패널 Profile IR → DXF(가공도 + 레이저용 cut) . 좌표 원점 = 외곽 중심(rect/circle) 또는 다각형 원래 좌표."""
import math

from shapely.geometry import Point, Polygon, box
from shapely import affinity

from . import dxfkit
from .util import num, resolve_params

DENSITY = {"SS400": 7.85, "STEEL": 7.85, "S45C": 7.85, "SPCC": 7.85, "SUS304": 7.93, "SUS316": 7.98, "AL6061": 2.70, "AL5052": 2.68,
           "AL7075": 2.81, "AL": 2.70, "BRASS": 8.50, "C3604": 8.50, "CU": 8.96, "COPPER": 8.96, "ABS": 1.04, "PLA": 1.24, "POM": 1.41,
           "ACRYLIC": 1.19, "PMMA": 1.19, "PC": 1.20, "PEEK": 1.32, "RUBBER": 1.20, "EPDM": 1.20, "FR4": 1.85, "TI": 4.43}
CLEARANCE = {"M2": 2.4, "M2.5": 2.9, "M3": 3.4, "M4": 4.5, "M5": 5.5, "M6": 6.6, "M8": 9.0, "M10": 11.0, "M12": 13.5, "M14": 15.5,
             "M16": 17.5, "M20": 22.0, "M24": 26.0}  # ISO 273 medium


def density(material):
    m = (material or "").upper().replace("-", "").replace(" ", "")
    for k, v in DENSITY.items():
        if m.startswith(k):
            return v
    return None


# ── IR 정규화 ─────────────────────────────────────────────────────────────
def normalize(ir):
    """LLM이 쓴 IR을 엔진 입력으로. 반환 (ir, errors[]) — errors는 수리 루프로 돌려보낼 메시지."""
    errs = []
    ir = dict(ir or {})
    ir, _ = resolve_params(ir, ir.get("params"))
    ir["kind"] = "part2d"
    ir.setdefault("name", "plate")
    ir["thickness"] = num(ir.get("thickness"), 0) or 0
    o = dict(ir.get("outline") or {})
    t = (o.get("type") or "rect").lower()
    o["type"] = t
    if t == "rect":
        o["w"], o["h"] = num(o.get("w") or o.get("width")), num(o.get("h") or o.get("height"))
        o["r"] = num(o.get("r") or o.get("radius"), 0) or 0
        if not o["w"] or not o["h"] or o["w"] <= 0 or o["h"] <= 0:
            errs.append("outline rect 에 양수 w, h 가 필요")
        elif o["r"] * 2 >= min(o["w"], o["h"]):
            errs.append(f"outline 모서리 R{o['r']:g} 가 너무 큼 (최대 {min(o['w'], o['h']) / 2 - 0.1:g})")
    elif t == "circle":
        o["d"] = num(o.get("d") or o.get("diameter"))
        if not o["d"] or o["d"] <= 0:
            errs.append("outline circle 에 양수 d 가 필요")
    elif t == "polygon":
        pts = o.get("points") or []
        if len(pts) < 3:
            errs.append("outline polygon 에 3개 이상 points 필요")
        o["points"] = [[num(p[0], 0), num(p[1], 0)] for p in pts]
        o["r"] = num(o.get("r"), 0) or 0
    else:
        errs.append(f"outline.type {t!r} 미지원 (rect|circle|polygon)")
    ir["outline"] = o
    holes = []
    for h in ir.get("holes") or []:
        h = dict(h)
        if h.get("thread") and not h.get("d"):
            h["d"] = CLEARANCE.get(str(h["thread"]).upper())
            if not h["d"]:
                errs.append(f"알 수 없는 나사 규격 {h['thread']!r}")
                continue
        h["d"] = num(h.get("d"))
        if not h["d"] or h["d"] <= 0:
            errs.append(f"hole 지름 d 가 없음: {h}")
            continue
        try:
            h["points"] = expand_pattern(h, o)
        except ValueError as e:
            errs.append(str(e))
            continue
        holes.append(h)
    ir["holes"] = holes
    cuts = []
    for c in ir.get("cutouts") or []:
        c = dict(c)
        c["type"] = (c.get("type") or "rect").lower()
        c["at"] = [num(v, 0) for v in (c.get("at") or [0, 0])][:2]
        for k in ("w", "h", "r", "l", "d", "angle"):
            if k in c:
                c[k] = num(c[k], 0)
        if c["type"] not in ("rect", "slot", "circle"):
            errs.append(f"cutout.type {c['type']!r} 미지원 (rect|slot|circle)")
            continue
        cuts.append(c)
    ir["cutouts"] = cuts
    return ir, errs


def outline_bounds(o):
    if o["type"] == "rect":
        return -o["w"] / 2, -o["h"] / 2, o["w"] / 2, o["h"] / 2
    if o["type"] == "circle":
        return -o["d"] / 2, -o["d"] / 2, o["d"] / 2, o["d"] / 2
    xs, ys = [p[0] for p in o["points"]], [p[1] for p in o["points"]]
    return min(xs), min(ys), max(xs), max(ys)


def expand_pattern(h, o):
    pat = (h.get("pattern") or "points").lower()
    cx, cy = [num(v, 0) for v in (h.get("center") or [0, 0])][:2]
    if pat in ("points", "list", "at"):
        pts = h.get("at") or h.get("points") or []
        if pts and not isinstance(pts[0], (list, tuple)):
            pts = [pts]
        if not pts:
            raise ValueError(f"hole 에 at 좌표가 없음 (d={h['d']})")
        return [[num(p[0], 0), num(p[1], 0)] for p in pts]
    if pat == "corners":
        x0, y0, x1, y1 = outline_bounds(o)
        i = num(h.get("inset"), None)
        ix, iy = num(h.get("inset_x"), i), num(h.get("inset_y"), i)
        if ix is None or iy is None:
            raise ValueError("pattern corners 에 inset(모서리에서 구멍 중심까지 거리) 필요")
        return [[x0 + ix, y0 + iy], [x1 - ix, y0 + iy], [x1 - ix, y1 - iy], [x0 + ix, y1 - iy]]
    if pat == "grid":
        nx, ny = int(num(h.get("nx"), 1)), int(num(h.get("ny"), 1))
        dx, dy = num(h.get("dx"), 0), num(h.get("dy"), 0)
        return [[cx + (i - (nx - 1) / 2) * dx, cy + (j - (ny - 1) / 2) * dy] for j in range(ny) for i in range(nx)]
    if pat in ("polar", "circle", "pcd"):
        n = int(num(h.get("n"), 0))
        r = num(h.get("pcd"), 0) / 2 if h.get("pcd") else num(h.get("r"), 0)
        a0 = num(h.get("start"), 0)
        if n < 1 or r <= 0:
            raise ValueError("pattern polar 에 n, pcd 필요")
        return [[cx + r * math.cos(math.radians(a0 + 360 * k / n)), cy + r * math.sin(math.radians(a0 + 360 * k / n))] for k in range(n)]
    if pat == "line":
        n, dx, dy = int(num(h.get("n"), 1)), num(h.get("dx"), 0), num(h.get("dy"), 0)
        return [[cx + (k - (n - 1) / 2) * dx, cy + (k - (n - 1) / 2) * dy] for k in range(n)]
    raise ValueError(f"hole pattern {pat!r} 미지원 (points|corners|grid|polar|line)")


# ── 기하 (shapely) ────────────────────────────────────────────────────────
def outline_geom(o):
    if o["type"] == "rect":
        g = box(-o["w"] / 2, -o["h"] / 2, o["w"] / 2, o["h"] / 2)
        if o["r"]:
            g = g.buffer(-o["r"], join_style=2).buffer(o["r"], quad_segs=16)
        return g
    if o["type"] == "circle":
        return Point(0, 0).buffer(o["d"] / 2, quad_segs=32)
    g = Polygon(o["points"])
    if o.get("r") and g.is_valid:
        g = g.buffer(-o["r"], join_style=2).buffer(o["r"], quad_segs=16)
    return g


def cutout_geom(c):
    x, y = c["at"]
    if c["type"] == "circle":
        return Point(x, y).buffer(c.get("d", 0) / 2, quad_segs=32)
    if c["type"] == "slot":
        L, W = c.get("l", 0), c.get("w", 0)
        g = box(-(L - W) / 2, 0, (L - W) / 2, 0).buffer(W / 2, quad_segs=16) if L > W else Point(0, 0).buffer(W / 2)
    else:
        w, h, r = c.get("w", 0), c.get("h", 0), c.get("r", 0)
        g = box(-w / 2, -h / 2, w / 2, h / 2)
        if r:
            g = g.buffer(-r, join_style=2).buffer(r, quad_segs=16)
    return affinity.translate(affinity.rotate(g, c.get("angle", 0) or 0, origin=(0, 0)), x, y)


def validate(ir, rules):
    """규칙 결과 [{id, severity, msg}] + 측정값."""
    out, R = [], rules
    o = ir["outline"]
    plate = outline_geom(o)
    if not plate.is_valid or plate.is_empty:
        out.append({"id": "P2D-001", "severity": "error", "msg": "외곽선이 닫힌 단순 다각형이 아님 (자기교차/점 부족)"})
        return out, {}
    feats = []
    for h in ir["holes"]:
        for p in h["points"]:
            feats.append((f"Ø{h['d']:g} @({p[0]:.1f},{p[1]:.1f})", Point(*p).buffer(h["d"] / 2, quad_segs=24), h["d"]))
    for c in ir["cutouts"]:
        feats.append((f"{c['type']} @({c['at'][0]:.1f},{c['at'][1]:.1f})", cutout_geom(c), min(v for v in (c.get("w"), c.get("h"), c.get("d"), c.get("l")) if v) or 0))
    web = R.get("min_web_mm", 1.0)
    t = ir.get("thickness") or 0
    for name, g, size in feats:
        if not plate.contains(g):
            out.append({"id": "P2D-002", "severity": "error", "msg": f"{name} 가 외곽 밖으로 나감"})
        elif plate.exterior.distance(g) < web:
            out.append({"id": "P2D-003", "severity": "warning", "msg": f"{name} 와 외곽 사이 웹 {plate.exterior.distance(g):.2f} mm < {web} mm"})
        if t and size and size < t * R.get("min_hole_to_thickness", 1.0):
            out.append({"id": "P2D-005", "severity": "warning", "msg": f"{name} 크기 {size:g} mm 가 판두께 {t:g} mm 보다 작음 (레이저/펀칭 가공 곤란)"})
    for i in range(len(feats)):
        for j in range(i + 1, len(feats)):
            d = feats[i][1].distance(feats[j][1])
            if feats[i][1].intersects(feats[j][1]):
                out.append({"id": "P2D-004", "severity": "error", "msg": f"{feats[i][0]} 와 {feats[j][0]} 겹침"})
            elif d < web:
                out.append({"id": "P2D-004", "severity": "warning", "msg": f"{feats[i][0]} 와 {feats[j][0]} 사이 웹 {d:.2f} mm < {web} mm"})
    net = plate
    for _, g, _ in feats:
        net = net.difference(g)
    x0, y0, x1, y1 = plate.bounds
    m = {"bbox_mm": [round(x1 - x0, 2), round(y1 - y0, 2)], "area_mm2": round(net.area, 1),
         "cut_length_mm": round(plate.exterior.length + sum(g.exterior.length for _, g, _ in feats), 1),
         "holes": sum(len(h["points"]) for h in ir["holes"]), "thickness_mm": t}
    rho = density(ir.get("material"))
    if rho and t:
        m["mass_g"] = round(net.area * t * rho / 1000, 1)
    exp = ir.get("expect") or {}
    if exp.get("bbox"):
        for a, b in zip(exp["bbox"], m["bbox_mm"]):
            if abs(num(a, b) - b) > 0.01:
                out.append({"id": "P2D-006", "severity": "error", "msg": f"외형 {m['bbox_mm']} 가 요구 {exp['bbox']} 와 다름"})
                break
    return out, m


# ── DXF ───────────────────────────────────────────────────────────────────
def _draw_profile(msp, ir, layer):
    o = ir["outline"]
    g = outline_geom(o)
    if o["type"] == "circle":
        msp.add_circle((0, 0), o["d"] / 2, dxfattribs={"layer": layer})
    elif o["type"] == "rect" and o["r"]:
        w, h, r = o["w"] / 2, o["h"] / 2, o["r"]
        b = math.tan(math.radians(90) / 4)  # 90° 원호 bulge
        pts = [(-w + r, -h, 0), (w - r, -h, b), (w, -h + r, 0), (w, h - r, b), (w - r, h, 0), (-w + r, h, b), (-w, h - r, 0), (-w, -h + r, b)]
        msp.add_lwpolyline(pts, format="xyb", close=True, dxfattribs={"layer": layer})
    else:
        msp.add_lwpolyline(list(g.exterior.coords)[:-1], close=True, dxfattribs={"layer": layer})
    for h in ir["holes"]:
        for p in h["points"]:
            msp.add_circle(p, h["d"] / 2, dxfattribs={"layer": layer})
    for c in ir["cutouts"]:
        if c["type"] == "circle":
            msp.add_circle(c["at"], c["d"] / 2, dxfattribs={"layer": layer})
        else:
            msp.add_lwpolyline(list(cutout_geom(c).exterior.coords)[:-1], close=True, dxfattribs={"layer": layer})


def build(ir, outdir, rev="R00"):
    """가공도(치수·중심선·표제란) DXF + 레이저용 cut DXF(외곽·구멍만, 레이어 0). 반환 {파일명: 역할}."""
    import os
    name = ir.get("name", "plate")
    files = {}
    # 1) cut DXF
    cut = dxfkit.new_doc()
    _draw_profile(cut.modelspace(), ir, "0")
    p = os.path.join(outdir, f"{name}_cut.dxf")
    cut.saveas(p)
    files[os.path.basename(p)] = "레이저/워터젯용 (외곽·구멍만)"
    # 2) 가공도
    doc = dxfkit.new_doc()
    msp = doc.modelspace()
    _draw_profile(msp, ir, "M-PART")
    x0, y0, x1, y1 = outline_bounds(ir["outline"])
    W, H = x1 - x0, y1 - y0
    s = dxfkit.pick_scale(W * 1.6, H * 1.8, "A3")
    dim = {"dimtxt": 3 * s, "dimasz": 2.5 * s, "dimexe": 1.5 * s, "dimexo": 1 * s, "dimgap": 1 * s, "dimdec": 1, "dimlfac": 1, "dimtxsty": dxfkit.STYLE, "dimclrd": 3, "dimclre": 3}
    ce = 2.5 * s
    for h in ir["holes"]:
        for px, py in h["points"]:
            r = h["d"] / 2 + ce
            msp.add_line((px - r, py), (px + r, py), dxfattribs={"layer": "M-CENTER"})
            msp.add_line((px, py - r), (px, py + r), dxfattribs={"layer": "M-CENTER"})
    # 전체 치수
    msp.add_linear_dim(base=(0, y0 - 12 * s), p1=(x0, y0), p2=(x1, y0), dimstyle="EZDXF", override=dim, dxfattribs={"layer": "M-DIM"}).render()
    msp.add_linear_dim(base=(x1 + 12 * s, 0), p1=(x1, y0), p2=(x1, y1), angle=90, dimstyle="EZDXF", override=dim, dxfattribs={"layer": "M-DIM"}).render()
    # 구멍 위치 치수: 각 구멍 그룹의 좌하단 구멍 (외곽 기준)
    lvl = 0
    for h in ir["holes"]:
        hx, hy = min(h["points"], key=lambda p: (p[0] + p[1]))
        lvl += 1
        if abs(hx - x0) > 0.01:
            msp.add_linear_dim(base=(0, y1 + (6 + 8 * lvl) * s), p1=(x0, y1), p2=(hx, hy), dimstyle="EZDXF", override=dim,
                               dxfattribs={"layer": "M-DIM"}).render()
        if abs(hy - y0) > 0.01:
            msp.add_linear_dim(base=(x0 - (6 + 8 * lvl) * s, 0), p1=(x0, y0), p2=(hx, hy), angle=90, dimstyle="EZDXF", override=dim,
                               dxfattribs={"layer": "M-DIM"}).render()
        # 콜아웃
        n = len(h["points"])
        lab = f"{n}× Ø{h['d']:g} THRU" + (f" ({str(h['thread']).upper()} 볼트 여유)" if h.get("thread") else "")
        q = max(h["points"], key=lambda p: (p[0] - p[1]))
        tp = (q[0] + h["d"] * 0.7 + 4 * s, q[1] - h["d"] * 0.7 - 4 * s)
        msp.add_line((q[0] + h["d"] * 0.36, q[1] - h["d"] * 0.36), tp, dxfattribs={"layer": "M-DIM"})
        dxfkit.text(msp, lab, (tp[0] + 1 * s, tp[1]), 3 * s, "M-TEXT", "MIDDLE_LEFT")
    for c in ir["cutouts"]:
        g = cutout_geom(c)
        bx0, by0, bx1, by1 = g.bounds
        lab = {"slot": f"장공 {c.get('l', 0):g}×{c.get('w', 0):g}", "circle": f"Ø{c.get('d', 0):g}"}.get(c["type"], f"□{c.get('w', 0):g}×{c.get('h', 0):g}" + (f" R{c['r']:g}" if c.get("r") else ""))
        tp = (bx1 + 4 * s, by1 + 4 * s)
        msp.add_line((bx1, by1 - (by1 - by0) * 0.1), tp, dxfattribs={"layer": "M-DIM"})
        dxfkit.text(msp, lab + (f" @({c['at'][0]:g},{c['at'][1]:g})" if c["at"] != [0, 0] else ""), (tp[0] + 1 * s, tp[1]), 3 * s, "M-TEXT", "MIDDLE_LEFT")
    o = ir["outline"]
    if o["type"] == "rect" and o["r"]:
        dxfkit.text(msp, f"4× R{o['r']:g}", (x1 - o["r"] * 0.3 + 2 * s, y1 + 3 * s), 3 * s, "M-TEXT", "BOTTOM_LEFT")
    if o["type"] == "circle":
        dxfkit.text(msp, f"Ø{o['d']:g}", (x1 * 0.72 + 2 * s, y1 * 0.72 + 2 * s), 3.2 * s, "M-TEXT", "BOTTOM_LEFT")
    notes = [f"재질: {ir.get('material') or '-'}   두께: t{ir.get('thickness') or '-'}"] + list(ir.get("notes") or []) + ["지시 없는 모따기 C0.5, 버 제거"]
    ny = y0 - 26 * s
    for i, n in enumerate(notes):
        dxfkit.text(msp, f"{i + 1}. {n}", (x0, ny - i * 5 * s), 3 * s, "M-TEXT", "TOP_LEFT")
    from ezdxf import bbox as ezb
    e = ezb.extents(msp)
    dxfkit.frame(msp, ((e.extmin.x, e.extmin.y), (e.extmax.x, e.extmax.y)), name, f"판재 가공도  t{ir.get('thickness') or '-'}  {ir.get('material') or ''}",
                 scale=s, paper="A3", rev=rev)
    p = os.path.join(outdir, f"{name}.dxf")
    doc.saveas(p)
    files[os.path.basename(p)] = "가공도 (치수·표제란)"
    return files, doc
