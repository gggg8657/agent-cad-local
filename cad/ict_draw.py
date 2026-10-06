"""ICT 설계 모델 → 배치도 DXF · 개황도(계통도) DXF · 랙 실장도 DXF · 3D 목업 GLB. 모두 같은 model 에서 파생 (수량·ID 불일치 없음)."""
import json
import math
import struct

from ezdxf import bbox as ezb

from . import dxfkit
from .ict import LIB, RD, RW, SYM, STYPES, RACK_U, space_of

MED_LAYER = {"CAT6": "ICT-UTP", "CAT5E": "ICT-UTP", "SMF": "ICT-FIBER", "MMF": "ICT-FIBER"}


# ── 배치도 ────────────────────────────────────────────────────────────────
def floor_extent(f):
    xs = [s["rect"][0] for s in f["spaces"]] + [s["rect"][0] + s["rect"][2] for s in f["spaces"]] + [f["riser"][0]]
    ys = [s["rect"][1] for s in f["spaces"]] + [s["rect"][1] + s["rect"][3] for s in f["spaces"]] + [f["riser"][1]]
    return min(xs), min(ys), max(xs), max(ys)


def layout_dxf(model, path, rev="R00", base_doc=None):
    """base_doc 가 있으면 (기존 건축 DXF) 그 위에 ICT 레이어만 추가 — 공간 좌표는 DXF 좌표계."""
    doc = base_doc or dxfkit.new_doc()
    if base_doc is not None:
        for name, d in dxfkit.STD["layers"].items():
            dxfkit.ensure_layer(doc, name)
        if dxfkit.STYLE not in doc.styles:
            doc.styles.add(dxfkit.STYLE, font=dxfkit.STD.get("font", "malgun.ttf"))
    msp = doc.modelspace()
    # 층별 세로 배치 오프셋 (기존 DXF 위에 그릴 때는 0)
    exts = [floor_extent(f) for f in model["floors"]]
    W = max(e[2] - e[0] for e in exts)
    H = sum(e[3] - e[1] for e in exts)
    S = dxfkit.pick_scale(W * 1.25, H * 1.2 + 30000, "A1")
    sym = 5 * S  # 심볼 폭 = 지면 5 mm
    th = 2.5 * S
    offs, y = {}, 0
    for f, e in zip(model["floors"], exts):
        if base_doc is not None or model.get("dxf_coords"):
            offs[f["id"]] = (0, 0)
        else:
            offs[f["id"]] = (-e[0], y - e[3])
            y -= (e[3] - e[1]) + 16 * S
    model["_draw_offset"] = offs

    def P(fid, p):
        o = offs[fid]
        return (p[0] + o[0], p[1] + o[1])
    for f, e in zip(model["floors"], exts):
        fid = f["id"]
        if base_doc is None:
            ox, oy = offs[fid]
            dxfkit.text(msp, f"{fid}  {f.get('name', '')}  평면", (e[0] + ox, e[3] + oy + 5 * S), 5 * S, "ICT-TEXT", "BOTTOM_LEFT")
            for s in f["spaces"]:
                x, yy, w, h = s["rect"]
                dxfkit.rect(msp, *P(fid, (x, yy)), w, h, "A-WALL")
                if w > 40 * S and h > 15 * S:  # 큰 공간: 안쪽 좌상단
                    dxfkit.text(msp, f"{s['name']}", P(fid, (x + 2 * S, yy + h - 3 * S)), 3.2 * S, "A-ROOM", "TOP_LEFT")
                    dxfkit.text(msp, f"{s['id']}  {w / 1000:g}×{h / 1000:g} m", P(fid, (x + 2 * S, yy + h - 7.5 * S)), 2 * S, "A-ROOM", "TOP_LEFT")
                else:  # 작은 실: 바깥 위
                    dxfkit.text(msp, f"{s['name']}  {w / 1000:g}×{h / 1000:g} m", P(fid, (x, yy + h + 1 * S)), 2.4 * S, "A-ROOM", "BOTTOM_LEFT")
        else:  # 기존 도면 위: 엔진이 신설한 공간(존이 없는 것)만 표시
            for s in f["spaces"]:
                if s.get("zone") is None:
                    x, yy, w, h = s["rect"]
                    dxfkit.rect(msp, x, yy, w, h, "ICT-ANNOTATION")
                    dxfkit.text(msp, f"(신설) {s['name']} {w / 1000:g}×{h / 1000:g} m", (x, yy + h + 1 * S), 2.4 * S, "ICT-ANNOTATION", "BOTTOM_LEFT")
        # 수직 간선
        rx, ry = P(fid, f["riser"])
        r = LIB["riser"]["size"] / 2 + 1 * S
        dxfkit.rect(msp, rx - r, ry - r, 2 * r, 2 * r, "ICT-RISER")
        msp.add_line((rx - r, ry - r), (rx + r, ry + r), dxfattribs={"layer": "ICT-RISER"})
        msp.add_line((rx - r, ry + r), (rx + r, ry - r), dxfattribs={"layer": "ICT-RISER"})
        dxfkit.text(msp, "TPS", (rx, ry + r + 1 * S), 2.2 * S, "ICT-RISER", "BOTTOM_CENTER")
    for t in model["trays"]:
        a, b = P(t["floor"], t["line"][0]), P(t["floor"], t["line"][1])
        msp.add_line(a, b, dxfattribs={"layer": "ICT-TRAY", "lineweight": 50})
        dxfkit.text(msp, "CABLE TRAY 300×100", ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2 + 1 * S), 1.8 * S, "ICT-TRAY", "BOTTOM_CENTER")
    # 결선: 말단 낙하선 + 광 간선
    for c in model["connections"]:
        lay = MED_LAYER.get(c["medium"], "ICT-UTP")
        for fid, pts in c["path"].items():
            if c["kind"] == "access":
                pts = pts[:2]  # 장비 → 트레이 낙하만 (트레이 위는 묶음)
            elif lay != "ICT-FIBER":
                continue
            msp.add_lwpolyline([P(fid, p) for p in pts], dxfattribs={"layer": lay})
    E = {e["id"]: e for e in model["equipment"]}
    for e in model["equipment"]:
        if e.get("rack") or not e.get("pos"):
            continue
        p = P(e["floor"], e["pos"])
        if e["type"] in ("cctv_fixed", "cctv_ptz"):
            rot = e.get("rot", 0)
            if e["type"] == "cctv_fixed":
                dxfkit.wedge(msp, p, 10000, rot - 30, rot + 30, "ICT-CCTV-FOV")
        if e["type"] == "ap":
            msp.add_circle(p, SYM["ap"]["coverage_r"], dxfattribs={"layer": "ICT-WIFI-COV"})
        dxfkit.add_symbol(msp, e["type"], p, sym, e["id"], rot=e.get("rot", 0) if e["type"] == "cctv_fixed" else 0)
    for r in model["racks"]:
        x, y = P(r["floor"], r["pos"])
        w, d = (RW, RD) if r["rot"] == 0 else (RD, RW)
        dxfkit.rect(msp, x - w / 2, y - d / 2, w, d, "ICT-RACK")
        msp.add_line((x - w / 2, y - d / 2), (x + w / 2, y + d / 2), dxfattribs={"layer": "ICT-RACK"})
        names = ", ".join(sl["id"].split("-")[0] for sl in r["slots"])
        dxfkit.text(msp, r["id"], (x, y - d / 2 - 1 * S), 2 * S, "ICT-RACK", "TOP_CENTER")
        dxfkit.text(msp, f"{r['top_u']}U/{RACK_U}U", (x, y - d / 2 - 4 * S), 1.6 * S, "ICT-RACK", "TOP_CENTER")
    if base_doc is not None:
        return doc
    e = ezb.extents(msp)
    lx, ly = e.extmax.x + 10 * S, e.extmax.y
    types = sorted({x["type"] for x in model["equipment"] if not x.get("rack")}, key=list(SYM).index)
    yy = dxfkit.legend(msp, (lx, ly), types + [("line", "ICT-TRAY", "케이블 트레이"), ("line", "ICT-UTP", "UTP(CAT6) 인입"),
                                              ("line", "ICT-FIBER", "광(SMF) 간선"), ("line", "ICT-RISER", "수직 간선(TPS)")], S)
    notes = ["주기", "1. 장비 위치는 배치 규칙에 따른 초안이며 현장 실측 후 조정한다."] + \
            [f"{i + 2}. {a}" for i, a in enumerate(model.get("assumptions", [])[:12])]
    for i, n in enumerate(notes):
        dxfkit.text(msp, n, (lx, yy - 6 * S - i * 4.2 * S), (3.2 if i == 0 else 2.2) * S, "ICT-TEXT", "TOP_LEFT")
    e = ezb.extents(msp)
    dxfkit.frame(msp, ((e.extmin.x, e.extmin.y), (e.extmax.x, e.extmax.y)), model["name"], "ICT 장비 배치도", scale=S, paper="A1", rev=rev,
                 extra=[("장비 / 케이블", f"{len(model['equipment'])} EA / {sum(c['length_m'] for c in model['connections']):,.0f} m")])
    doc.saveas(path)
    return doc


# ── 개황도 (계통도) ──────────────────────────────────────────────────────
def overview_dxf(model, path, rev="R00"):
    doc = dxfkit.new_doc()
    msp = doc.modelspace()
    E = {e["id"]: e for e in model["equipment"]}
    kids = {}
    for c in model["connections"]:
        if c["kind"] == "access":
            kids.setdefault(c["to"], []).append(E[c["from"]])
    heads = [e for e in model["equipment"] if SYM[e["type"]]["role"] == "head"]
    cores = [e for e in model["equipment"] if e["type"] == "core_sw"]
    access = [e for e in model["equipment"] if e["type"] == "access_sw"]
    bw, bh = 34, 12          # 박스 (mm, 1:1 지면)
    pos = {}

    def box(e, x, y, w=bw, h=bh, sub=None):
        dxfkit.rect(msp, x - w / 2, y - h / 2, w, h, SYM[e["type"]]["layer"] if e["type"] in SYM else "ICT-NETWORK")
        dxfkit.text(msp, e["id"], (x, y + 1.6), 2.6, "ICT-TEXT")
        dxfkit.text(msp, sub or SYM[e["type"]]["label"], (x, y - 2.8), 1.9, "ICT-TEXT")
        pos[e["id"]] = (x, y, w, h)
    # 말단 묶음 열: 접속 스위치별
    col_w = 46
    groups = []
    for a in access:
        ch = kids.get(a["id"], [])
        bytype = {}
        for k in ch:
            bytype.setdefault(k["type"], []).append(k)
        groups.append((a, bytype))
    total_w = max(len(access), 1) * col_w
    y_head, y_core, y_acc, y_end = 0, -36, -80, -112
    xs = [(i - (len(access) - 1) / 2) * col_w for i in range(len(access))]
    for i, e in enumerate(heads):
        box(e, (i - (len(heads) - 1) / 2) * (bw + 14), y_head)
    for i, e in enumerate(cores):
        box(e, (i - (len(cores) - 1) / 2) * (bw + 30), y_core)
    # 층 묶음 점선 박스
    floors_seen = {}
    for x, (a, _) in zip(xs, groups):
        box(a, x, y_acc, sub=f"{space_of(model, a['space'])[1]['name']}")
        floors_seen.setdefault(a["floor"], []).append(x)
    for fid, fx in floors_seen.items():
        x0, x1 = min(fx) - col_w / 2 + 2, max(fx) + col_w / 2 - 2
        msp.add_lwpolyline([(x0, y_acc + 12), (x1, y_acc + 12), (x1, y_end - 60), (x0, y_end - 60)], close=True,
                           dxfattribs={"layer": "ICT-ANNOTATION", "linetype": "DASHED"})
        f = next(f for f in model["floors"] if f["id"] == fid)
        dxfkit.text(msp, f"{fid} {f.get('name', '')}", (x0 + 2, y_acc + 10), 2.6, "ICT-ANNOTATION", "TOP_LEFT")
    # 말단: 종류별 요약 박스 — 스위치 아래 좌측 버스에서 가지
    for x, (a, bytype) in zip(xs, groups):
        if not bytype:
            continue
        xb = x - 21
        yy = y_end
        for t, es in bytype.items():
            ids = [k["id"] for k in es]
            lab = ids[0] if len(ids) == 1 else f"{ids[0]} ~ {ids[-1].split('-')[-1]}"
            dxfkit.rect(msp, x - 17, yy - 6, 36, 12, SYM[t]["layer"])
            dxfkit.add_symbol(msp, t, (x - 11, yy), 7, "", layer=SYM[t]["layer"])
            dxfkit.text(msp, f"{SYM[t]['abbr']} ×{len(es)}", (x - 5, yy + 1.8), 2.6, "ICT-TEXT", "MIDDLE_LEFT")
            dxfkit.text(msp, lab, (x - 5, yy - 2.6), 1.7, "ICT-TEXT", "MIDDLE_LEFT")
            msp.add_line((xb, yy), (x - 17, yy), dxfattribs={"layer": "ICT-UTP"})
            last = yy
            yy -= 16
        msp.add_lwpolyline([(x, y_acc - bh / 2), (x, y_acc - bh / 2 - 6), (xb, y_acc - bh / 2 - 6), (xb, last)], dxfattribs={"layer": "ICT-UTP"})
        dxfkit.text(msp, "CAT6", (xb - 1, y_acc - bh / 2 - 9), 1.6, "ICT-TEXT", "TOP_RIGHT")
    # 업링크·코어간·헤드 연결 (직교 버스)
    for c in model["connections"]:
        if c["kind"] == "access" or c["from"] not in pos or c["to"] not in pos:
            continue
        (x1, y1, _, h1), (x2, y2, _, h2) = pos[c["from"]], pos[c["to"]]
        lay = MED_LAYER.get(c["medium"], "ICT-UTP")
        if abs(y1 - y2) < 1:  # 코어 간
            msp.add_line((x1 + bw / 2, y1), (x2 - bw / 2, y2), dxfattribs={"layer": lay})
            dxfkit.text(msp, f"{c['medium']} ISL", ((x1 + x2) / 2, y1 + 1.5), 1.8, "ICT-TEXT", "BOTTOM_CENTER")
            continue
        top, bot = ((x1, y1, h1), (x2, y2, h2)) if y1 > y2 else ((x2, y2, h2), (x1, y1, h1))
        ci = [k for k, e in enumerate(cores) if e["id"] in (c["from"], c["to"])]
        jog = (ci[0] - (len(cores) - 1) / 2) * 6 if ci else 0
        ym = (top[1] - top[2] / 2 + bot[1] + bot[2] / 2) / 2 + jog * 0.6
        msp.add_lwpolyline([(top[0] + jog, top[1] - top[2] / 2), (top[0] + jog, ym), (bot[0] + jog * 0.3, ym), (bot[0] + jog * 0.3, bot[1] + bot[2] / 2)],
                           dxfattribs={"layer": lay})
    dxfkit.legend(msp, (total_w / 2 + 30, y_head + 6), [("line", "ICT-FIBER", "광(SMF/MMF)"), ("line", "ICT-UTP", "UTP(CAT6)"),
                                                         ("line", "ICT-ANNOTATION", "층 구분")], 1)
    cnt = {}
    for e in model["equipment"]:
        cnt[SYM[e["type"]]["abbr"]] = cnt.get(SYM[e["type"]]["abbr"], 0) + 1
    dxfkit.text(msp, "구성: " + ", ".join(f"{k} {v}" for k, v in cnt.items()), (total_w / 2 + 30, y_head - 30), 2.2, "ICT-TEXT", "TOP_LEFT")
    e = ezb.extents(msp)
    dxfkit.frame(msp, ((e.extmin.x, e.extmin.y), (e.extmax.x, e.extmax.y)), model["name"], "ICT 시스템 개황도 (계통도)", scale=1,
                 paper="A3" if total_w < 300 else "A2", rev=rev)
    doc.saveas(path)
    return doc


# ── 랙 실장도 ─────────────────────────────────────────────────────────────
def rack_dxf(model, path, rev="R00"):
    doc = dxfkit.new_doc()
    msp = doc.modelspace()
    U = 44.45
    W = 482.6
    x = 0
    for r in model["racks"]:
        H = RACK_U * U
        dxfkit.rect(msp, x - 40, -40, W + 80, H + 80, "ICT-RACK")
        dxfkit.rect(msp, x, 0, W, H, "ICT-RACK")
        for u in range(1, RACK_U + 1, 1):
            if u % 5 == 1 or u == RACK_U:
                dxfkit.text(msp, str(u), (x - 8, (u - 0.5) * U), 18, "ICT-TEXT", "MIDDLE_RIGHT")
        for sl in r["slots"]:
            y0 = (sl["u"] - 1) * U
            dxfkit.rect(msp, x + 4, y0 + 2, W - 8, sl["size"] * U - 4, SYM[sl["type"]]["layer"])
            dxfkit.text(msp, f"{sl['id']}  ({SYM[sl['type']]['abbr']}, {sl['size']}U)", (x + W / 2, y0 + sl["size"] * U / 2), min(26, sl["size"] * U * 0.55), "ICT-TEXT")
        _, s = space_of(model, r["space"])
        dxfkit.text(msp, r["id"], (x + W / 2, H + 90), 32, "ICT-TEXT", "BOTTOM_CENTER")
        dxfkit.text(msp, f"{s['name']} · {r['top_u']}U / {RACK_U}U", (x + W / 2, -70), 22, "ICT-TEXT", "TOP_CENTER")
        x += W + 300
    e = ezb.extents(msp)
    S = dxfkit.pick_scale(e.size.x * 1.3, e.size.y * 1.4, "A3")
    dxfkit.frame(msp, ((e.extmin.x, e.extmin.y), (e.extmax.x, e.extmax.y)), model["name"], "랙 실장도 (정면)", scale=S, paper="A3", rev=rev)
    doc.saveas(path)
    return doc


# ── 3D 목업 GLB (자체 writer, OCC 불필요) ────────────────────────────────
def _box_mesh(cx, cy, z0, sx, sy, sz):
    x0, x1, y0, y1, z1 = cx - sx / 2, cx + sx / 2, cy - sy / 2, cy + sy / 2, z0 + sz
    # glTF: Y-up → (x, z, -y)
    P = lambda x, y, z: (x / 1000, z / 1000, -y / 1000)
    faces = [((x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (0, -1, 0)), ((x0, y0, z1), (x0, y1, z1), (x1, y1, z1), (x1, y0, z1), (0, 1, 0)),
             ((x0, y0, z0), (x0, y0, z1), (x1, y0, z1), (x1, y0, z0), (0, 0, 1)), ((x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1), (0, 0, -1)),
             ((x0, y0, z0), (x0, y1, z0), (x0, y1, z1), (x0, y0, z1), (-1, 0, 0)), ((x1, y0, z0), (x1, y0, z1), (x1, y1, z1), (x1, y1, z0), (1, 0, 0))]
    pos, nor, idx = [], [], []
    for f in faces:
        b = len(pos)
        for v in f[:4]:
            pos.append(P(*v))
            nor.append(f[4])
        idx += [b, b + 1, b + 2, b, b + 2, b + 3]
    return pos, nor, idx


def mockup_glb(model, path):
    meshes = {}  # rgba → (pos, nor, idx)

    def add(rgba, m):
        p, n, i = meshes.setdefault(rgba, ([], [], []))
        b = len(p)
        p += m[0]
        n += m[1]
        i += [k + b for k in m[2]]
    z = 0
    zf = {}
    for f in model["floors"]:
        zf[f["id"]] = z
        for s in f["spaces"]:
            x, y, w, h = s["rect"]
            add((0.82, 0.82, 0.80, 1.0), _box_mesh(x + w / 2, y + h / 2, z - 200, w, h, 200))
            col = (0.55, 0.75, 0.95, 0.18) if not STYPES[s["type"]].get("comm") else (0.95, 0.8, 0.4, 0.25)
            add(col, _box_mesh(x + w / 2, y + h / 2, z, w, h, f["height"] * 0.7))
        rx, ry = f["riser"]
        add((0.9, 0.75, 0.1, 1.0), _box_mesh(rx, ry, z, 1200, 1200, f["height"]))
        z -= f["height"] + 1500
    for e in model["equipment"]:
        if e.get("rack") or not e.get("pos"):
            continue
        s = SYM[e["type"]]
        bx, by, bz = s["box"]
        k = 2.5  # 목업에서는 잘 보이게 확대
        add(tuple(c / 255 for c in s["rgb"]) + (1.0,), _box_mesh(e["pos"][0], e["pos"][1], zf[e["floor"]] + s["mount_h"], bx * k, by * k, bz * k))
    for r in model["racks"]:
        w, d = (RW, RD) if r["rot"] == 0 else (RD, RW)
        add((0.16, 0.16, 0.2, 1.0), _box_mesh(r["pos"][0], r["pos"][1], zf[r["floor"]], w, d, LIB["rack"]["height"]))
    for t in model["trays"]:
        (x0, y0), (x1, y1) = t["line"]
        add((0.6, 0.45, 0.2, 1.0), _box_mesh((x0 + x1) / 2, (y0 + y1) / 2, zf[t["floor"]] + 3300, abs(x1 - x0) or 300, abs(y1 - y0) or 300, 100))
    # glTF 2.0 바이너리 조립
    bin_, views, accs, prims, mats = bytearray(), [], [], [], []
    for mi, (rgba, (p, n, i)) in enumerate(meshes.items()):
        def push(data, fmt, target):
            while len(bin_) % 4:
                bin_.append(0)
            off = len(bin_)
            bin_.extend(struct.pack(f"<{len(data)}{fmt}", *data))
            views.append({"buffer": 0, "byteOffset": off, "byteLength": len(bin_) - off, "target": target})
            return len(views) - 1
        flat_p = [c for v in p for c in v]
        flat_n = [float(c) for v in n for c in v]
        vp = push(flat_p, "f", 34962)
        vn = push(flat_n, "f", 34962)
        vi = push(i, "I", 34963)
        mn = [min(v[k] for v in p) for k in range(3)]
        mx = [max(v[k] for v in p) for k in range(3)]
        accs += [{"bufferView": vp, "componentType": 5126, "count": len(p), "type": "VEC3", "min": mn, "max": mx},
                 {"bufferView": vn, "componentType": 5126, "count": len(n), "type": "VEC3"},
                 {"bufferView": vi, "componentType": 5125, "count": len(i), "type": "SCALAR"}]
        b = len(accs) - 3
        mats.append({"pbrMetallicRoughness": {"baseColorFactor": list(rgba), "metallicFactor": 0.0, "roughnessFactor": 0.8},
                     **({"alphaMode": "BLEND", "doubleSided": True} if rgba[3] < 1 else {})})
        prims.append({"attributes": {"POSITION": b, "NORMAL": b + 1}, "indices": b + 2, "material": mi})
    while len(bin_) % 4:
        bin_.append(0)
    gl = {"asset": {"version": "2.0", "generator": "agent-cad-local"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": model["name"]}],
          "meshes": [{"primitives": prims}], "materials": mats, "accessors": accs, "bufferViews": views, "buffers": [{"byteLength": len(bin_)}]}
    js = json.dumps(gl, ensure_ascii=False).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_)))
        f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        f.write(struct.pack("<II", len(bin_), 0x004E4942) + bytes(bin_))
